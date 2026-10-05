import pytest
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.api.app import app
from app.config import settings
from app.database.migrations import run_migrations
from app.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteTokenRepository,
    SQLiteDeliveryRepository
)
from app.api.routes_auth import get_db_repos
from app.api.email_service import get_email_service
from app.subscribers.worker import DeliveryWorker
from app.tpo.models import CompanyRecord

class FakeEmailService:
    def __init__(self):
        self.sent_emails = []

    def send_verification_email(self, to_email: str, raw_token: str) -> bool:
        self.sent_emails.append({
            "type": "SIGNUP_VERIFY",
            "to_email": to_email,
            "raw_token": raw_token
        })
        return True

    def send_preference_link_email(self, to_email: str, raw_token: str) -> bool:
        self.sent_emails.append({
            "type": "MANAGE_PREFS",
            "to_email": to_email,
            "raw_token": raw_token
        })
        return True


@pytest.fixture
def action_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_action_tokens.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE companies (id TEXT PRIMARY KEY, company TEXT, raw_data_json TEXT)")
        conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)")
        conn.execute("INSERT INTO system_state (key, value) VALUES ('baseline_initialized', 'true')")
        conn.commit()

    run_migrations(db_path)

    user_repo = SQLiteUserRepository(db_path)
    token_repo = SQLiteTokenRepository(db_path)
    delivery_repo = SQLiteDeliveryRepository(db_path)
    fake_email = FakeEmailService()

    monkeypatch.setattr("app.database.repository.DB_PATH", db_path)
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    app.dependency_overrides[get_db_repos] = lambda: (user_repo, token_repo)
    app.dependency_overrides[get_email_service] = lambda: fake_email

    client = TestClient(app)
    client.db_path = db_path
    client.user_repo = user_repo
    client.token_repo = token_repo
    client.delivery_repo = delivery_repo
    client.fake_email = fake_email
    yield client
    app.dependency_overrides.clear()


# =====================================================================
# 1-5: SIGNUP VERIFY TESTS
# =====================================================================

def test_1_repeated_get_legacy_verify_link_is_non_mutating(action_client):
    user_id = action_client.user_repo.create_user("verify_scan@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token_id = action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    # Scanner / email link preview issues repeated GET requests
    for _ in range(3):
        res = action_client.get(f"/api/v1/auth/verify?token={raw_token}", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == f"/verify#token={raw_token}"

    # Verify state remains completely unmutated
    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_verified"] == 0
    assert user["verified_at"] is None

    token_row = action_client.token_repo.get_valid_token(thash, "SIGNUP_VERIFY")
    assert token_row is not None
    assert token_row["used_at"] is None


def test_2_confirm_post_verifies_correct_user_and_consumes_token(action_client):
    user_id = action_client.user_repo.create_user("verify_user@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_verified"] == 1
    assert user["verified_at"] is not None

    # Token must now be spent
    assert action_client.token_repo.get_valid_token(thash, "SIGNUP_VERIFY") is None


def test_3_replay_confirm_post_rejected(action_client):
    user_id = action_client.user_repo.create_user("verify_replay@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res1 = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
    assert res1.status_code == 200

    # Replay attempt
    res2 = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
    assert res2.status_code == 400
    assert "Invalid or expired" in res2.json()["detail"]


def test_4_wrong_token_type_rejected_for_verify(action_client):
    user_id = action_client.user_repo.create_user("wrong_type_v@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    # Create an UNSUBSCRIBE token instead of SIGNUP_VERIFY
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    res = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
    assert res.status_code == 400
    assert "Invalid or expired" in res.json()["detail"]
    assert action_client.user_repo.get_by_id(user_id)["is_verified"] == 0


def test_5_token_for_user_a_cannot_mutate_user_b(action_client):
    u_a = action_client.user_repo.create_user("user_a@vit.edu", 2028, "VIT_CE")
    u_b = action_client.user_repo.create_user("user_b@vit.edu", 2028, "VIT_IT")

    raw_token_a = secrets.token_urlsafe(32)
    thash_a = hashlib.sha256(raw_token_a.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=u_a,
        token_hash=thash_a,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token_a})
    assert res.status_code == 200

    assert action_client.user_repo.get_by_id(u_a)["is_verified"] == 1
    assert action_client.user_repo.get_by_id(u_b)["is_verified"] == 0


# =====================================================================
# 6-9: UNSUBSCRIBE TESTS
# =====================================================================

def test_6_repeated_get_legacy_unsubscribe_is_non_mutating(action_client):
    user_id = action_client.user_repo.create_user("unsub_scan@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)
    with sqlite3.connect(action_client.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, company, raw_data_json) VALUES ('1001', 'Test Corp', '{}')")
        conn.commit()
    # Enqueue a pending delivery
    action_client.delivery_repo.enqueue_deliveries([{
        "user_id": user_id,
        "company_id": "1001",
        "notification_type": "NEW"
    }])

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Scanner / link prefetcher sends repeated GETs
    for _ in range(3):
        res = action_client.get(f"/api/v1/unsubscribe?token={raw_token}", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == f"/unsubscribe#token={raw_token}"

    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 1
    token_row = action_client.token_repo.get_valid_token(thash, "UNSUBSCRIBE")
    assert token_row is not None
    assert token_row["used_at"] is None

    stats = action_client.delivery_repo.get_delivery_stats()
    assert stats["PENDING"] == 1


def test_7_confirm_post_deactivates_user_and_cancels_deliveries(action_client):
    user_id = action_client.user_repo.create_user("unsub_active@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)
    with sqlite3.connect(action_client.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, company, raw_data_json) VALUES ('1002', 'Test Corp', '{}')")
        conn.commit()
    action_client.delivery_repo.enqueue_deliveries([{
        "user_id": user_id,
        "company_id": "1002",
        "notification_type": "NEW"
    }])

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    res = action_client.post("/api/v1/unsubscribe/confirm", json={"token": raw_token})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0

    stats = action_client.delivery_repo.get_delivery_stats()
    assert stats["PENDING"] == 0
    with sqlite3.connect(action_client.db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM notification_deliveries WHERE user_id = ?", (user_id,))
        assert cursor.fetchone()[0] == 0

    assert action_client.token_repo.get_valid_token(thash, "UNSUBSCRIBE") is None


def test_8_unsubscribe_replay_rejected(action_client):
    user_id = action_client.user_repo.create_user("unsub_rep@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    res1 = action_client.post("/api/v1/unsubscribe/confirm", json={"token": raw_token})
    assert res1.status_code == 200

    res2 = action_client.post("/api/v1/unsubscribe/confirm", json={"token": raw_token})
    assert res2.status_code == 400
    assert "Invalid or expired" in res2.json()["detail"]


def test_9_wrong_token_type_rejected_for_unsubscribe(action_client):
    user_id = action_client.user_repo.create_user("unsub_wrong@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    # Create SIGNUP_VERIFY token instead
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res = action_client.post("/api/v1/unsubscribe/confirm", json={"token": raw_token})
    assert res.status_code == 400
    assert "Invalid or expired" in res.json()["detail"]
    assert action_client.user_repo.get_by_id(user_id)["is_active"] == 1


# =====================================================================
# 10-14: MANAGE PREFS TESTS
# =====================================================================

def test_10_repeated_get_legacy_preferences_link_is_non_mutating(action_client):
    user_id = action_client.user_repo.create_user("prefs_scan@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # Scanner issues repeated GETs
    for _ in range(3):
        res = action_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == f"/preferences/confirm#token={raw_token}"
        assert "tpo_session" not in res.cookies

    token_row = action_client.token_repo.get_valid_token(thash, "MANAGE_PREFS")
    assert token_row is not None
    assert token_row["used_at"] is None


def test_11_confirm_post_consumes_token_and_creates_session(action_client):
    user_id = action_client.user_repo.create_user("prefs_auth@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = action_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
    assert res.status_code == 200
    assert "tpo_session" in res.cookies

    # Verify session authenticates the token owner
    cookies = {"tpo_session": res.cookies["tpo_session"]}
    get_res = action_client.get("/api/v1/preferences", cookies=cookies)
    assert get_res.status_code == 200
    assert get_res.json()["email"] == "prefs_auth@vit.edu"

    # Token must be consumed
    assert action_client.token_repo.get_valid_token(thash, "MANAGE_PREFS") is None


def test_12_preferences_confirm_replay_rejected(action_client):
    user_id = action_client.user_repo.create_user("prefs_rep@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res1 = action_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
    assert res1.status_code == 200

    res2 = action_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
    assert res2.status_code == 400
    assert "Invalid or expired" in res2.json()["detail"]


def test_13_wrong_token_type_rejected_for_preferences(action_client):
    user_id = action_client.user_repo.create_user("prefs_wrong@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res = action_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
    assert res.status_code == 400
    assert "Invalid or expired" in res.json()["detail"]


def test_14_scanner_like_get_followed_by_legitimate_post_succeeds(action_client):
    """
    MANDATORY SPEC REQUIREMENT:
    Proves that an automated link scanner (e.g. anti-phishing mail scanner)
    fetching GET links multiple times does NOT burn the token, and the
    subsequent intentional user POST still succeeds!
    """
    user_id = action_client.user_repo.create_user("scanner_proof@vit.edu", 2028, "VIT_CE")

    # 1. Verification token
    v_raw = secrets.token_urlsafe(32)
    v_hash = hashlib.sha256(v_raw.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=v_hash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    # 2. Preferences token
    p_raw = secrets.token_urlsafe(32)
    p_hash = hashlib.sha256(p_raw.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=p_hash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # 3. Unsubscribe token
    u_raw = secrets.token_urlsafe(32)
    u_hash = hashlib.sha256(u_raw.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=u_hash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Scanner hits all three endpoints via GET
    assert action_client.get(f"/api/v1/auth/verify?token={v_raw}", follow_redirects=False).status_code == 303
    assert action_client.get(f"/api/v1/preferences/request?token={p_raw}", follow_redirects=False).status_code == 303
    assert action_client.get(f"/api/v1/unsubscribe?token={u_raw}", follow_redirects=False).status_code == 303

    # Human user subsequently clicks verify
    res_v = action_client.post("/api/v1/auth/verify/confirm", json={"token": v_raw})
    assert res_v.status_code == 200
    assert action_client.user_repo.get_by_id(user_id)["is_verified"] == 1

    # Human user subsequently clicks preferences
    res_p = action_client.post("/api/v1/preferences/confirm", json={"token": p_raw})
    assert res_p.status_code == 200
    assert "tpo_session" in res_p.cookies

    # Human user subsequently clicks unsubscribe
    res_u = action_client.post("/api/v1/unsubscribe/confirm", json={"token": u_raw})
    assert res_u.status_code == 200
    assert action_client.user_repo.get_by_id(user_id)["is_active"] == 0


# =====================================================================
# QUERY-STRING TOKEN REJECTION ON CONFIRM POST ENDPOINTS
# =====================================================================

def test_query_string_token_rejection_on_new_post_endpoints(action_client):
    user_id = action_client.user_repo.create_user("query_reject@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    # 1. POST with token ONLY in query string -> rejected
    res_query_only = action_client.post(f"/api/v1/auth/verify/confirm?token={raw_token}", json={})
    assert res_query_only.status_code in (400, 422)

    # 2. POST with token in query string even if duplicated in body -> rejected
    res_both = action_client.post(f"/api/v1/auth/verify/confirm?token={raw_token}", json={"token": raw_token})
    assert res_both.status_code == 400
    assert "JSON body, not query string" in res_both.json()["detail"]

    # 3. POST with correct token strictly in JSON body -> accepted
    res_valid = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
    assert res_valid.status_code == 200


# =====================================================================
# PREFERENCE SESSION COOKIE ATTRIBUTES
# =====================================================================

def test_preference_session_cookie_attributes(action_client):
    user_id = action_client.user_repo.create_user("cookie_attrs@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = action_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
    assert res.status_code == 200

    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "max-age=3600" in set_cookie


# =====================================================================
# RFC 8058 ONE-CLICK UNSUBSCRIBE TESTS
# =====================================================================

def test_rfc_8058_valid_form_post_succeeds(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_ok@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Valid form POST as specified in RFC 8058
    res = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data={"List-Unsubscribe": "One-Click"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    # Response must NOT redirect
    assert res.status_code != 303
    assert res.status_code != 302

    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0

    # Token must be consumed
    assert action_client.token_repo.get_valid_token(thash, "UNSUBSCRIBE") is None


def test_rfc_8058_missing_or_invalid_form_rejected(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_bad@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Missing body
    res_empty = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res_empty.status_code == 400

    # Incorrect form value
    res_wrong = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data={"List-Unsubscribe": "Invalid-Value"},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res_wrong.status_code == 400


def test_rfc_8058_get_returns_405_and_does_not_mutate(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_get@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # GET must return 405 Method Not Allowed
    res = action_client.get(f"/api/v1/unsubscribe/one-click?token={raw_token}")
    assert res.status_code == 405

    # Zero mutation
    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 1
    assert action_client.token_repo.get_valid_token(thash, "UNSUBSCRIBE") is not None


def test_rfc_8058_no_session_required(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_nosess@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Ensure no cookies are sent
    action_client.cookies.clear()
    res = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res.status_code == 200


def test_rfc_8058_replay_rejected(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_replay@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    res1 = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res1.status_code == 200

    res2 = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res2.status_code == 400


def test_rfc_8058_wrong_token_type_rejected(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_type@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    res = action_client.post(
        f"/api/v1/unsubscribe/one-click?token={raw_token}",
        data="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert res.status_code == 400


def test_rfc_8058_email_headers_present_in_worker_dispatches(action_client):
    user_id = action_client.user_repo.create_user("rfc8058_worker@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)
    with sqlite3.connect(action_client.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, company, raw_data_json) VALUES ('8058', 'Worker Corp', '{}')")
        conn.commit()

    action_client.delivery_repo.enqueue_deliveries([{
        "user_id": user_id,
        "company_id": "8058",
        "notification_type": "NEW"
    }])

    mock_notifier = MagicMock()
    mock_notifier._send_email_to.return_value = True

    worker = DeliveryWorker(
        delivery_repo=action_client.delivery_repo,
        token_repo=action_client.token_repo,
        email_notifier=mock_notifier,
        batch_size=1,
        lease_seconds=300
    )

    import asyncio
    asyncio.run(worker.process_batch_once())

    assert mock_notifier._send_email_to.call_count == 1
    call_kwargs = mock_notifier._send_email_to.call_args[1]
    extra_headers = call_kwargs.get("extra_headers")
    assert extra_headers is not None
    assert "List-Unsubscribe" in extra_headers
    assert "List-Unsubscribe-Post" in extra_headers
    assert extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    # URL built from settings.BASE_URL
    unsub_header_val = extra_headers["List-Unsubscribe"]
    assert unsub_header_val.startswith(f"<{settings.BASE_URL}/api/v1/unsubscribe/one-click?token=")
    assert unsub_header_val.endswith(">")


# =====================================================================
# 18-21: CONCURRENCY RACE REGRESSION TESTS
# =====================================================================

def test_18_concurrency_verify_confirm(action_client):
    """
    Ensure exactly 1 of 10 concurrent verify/confirm requests claims the token.
    All 9 others fail with HTTP 400.
    Token has exactly 1 used_at timestamp and user is verified.
    """
    import threading

    user_id = action_client.user_repo.create_user("conc_verify@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    responses = []

    def worker():
        barrier.wait()
        res = action_client.post("/api/v1/auth/verify/confirm", json={"token": raw_token})
        responses.append(res)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1
    assert status_codes.count(400) == 9

    # Verify user state is verified
    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_verified"] == 1
    assert user["verified_at"] is not None

    # Verify token row in DB has used_at set
    with sqlite3.connect(action_client.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = ?", (thash,)).fetchone()
        assert row["used_at"] is not None


def test_19_concurrency_unsubscribe_confirm(action_client):
    """
    Ensure exactly 1 of 10 concurrent unsubscribe/confirm requests claims the token.
    All 9 others fail with HTTP 400.
    User is marked inactive and pending deliveries cancelled in one transaction.
    """
    import threading

    user_id = action_client.user_repo.create_user("conc_unsub@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)
    with sqlite3.connect(action_client.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, company, raw_data_json) VALUES ('2001', 'Conc Corp', '{}')")
        conn.commit()

    action_client.delivery_repo.enqueue_deliveries([{
        "user_id": user_id,
        "company_id": "2001",
        "notification_type": "NEW"
    }])

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    responses = []

    def worker():
        barrier.wait()
        res = action_client.post("/api/v1/unsubscribe/confirm", json={"token": raw_token})
        responses.append(res)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1
    assert status_codes.count(400) == 9

    # User must be inactive
    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0

    # Deliveries cancelled
    stats = action_client.delivery_repo.get_delivery_stats()
    assert stats["PENDING"] == 0


def test_20_concurrency_preferences_confirm(action_client):
    """
    Ensure exactly 1 of 10 concurrent preferences/confirm requests claims the token.
    All 9 others fail with HTTP 400.
    Exactly 1 response receives the tpo_session Set-Cookie.
    """
    import threading

    user_id = action_client.user_repo.create_user("conc_prefs@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    responses = []

    def worker():
        # Independent TestClient per thread to ensure cookie headers are captured per request
        thread_client = TestClient(app)
        barrier.wait()
        res = thread_client.post("/api/v1/preferences/confirm", json={"token": raw_token})
        responses.append(res)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1
    assert status_codes.count(400) == 9

    # Exactly 1 response contains tpo_session cookie
    cookies_present = [r for r in responses if "tpo_session" in r.cookies]
    assert len(cookies_present) == 1
    assert cookies_present[0].status_code == 200


def test_21_concurrency_rfc_8058_one_click(action_client):
    """
    Ensure exactly 1 of 10 concurrent RFC 8058 one-click unsubscribe requests claims the token.
    All 9 others fail with HTTP 400.
    User is marked inactive.
    """
    import threading

    user_id = action_client.user_repo.create_user("conc_rfc8058@vit.edu", 2028, "VIT_CE")
    action_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    action_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    num_threads = 10
    barrier = threading.Barrier(num_threads)
    responses = []

    def worker():
        barrier.wait()
        res = action_client.post(
            f"/api/v1/unsubscribe/one-click?token={raw_token}",
            data="List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        responses.append(res)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1
    assert status_codes.count(400) == 9

    user = action_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0
