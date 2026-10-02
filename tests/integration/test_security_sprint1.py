import re
import math
import sqlite3
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
import pytest
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
from app.api.session import create_session_token, verify_session_token
from app.api.rate_limiter import limiter

class SecurityTestEmailSink:
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

    def clear(self):
        self.sent_emails.clear()


@pytest.fixture
def sec_client(tmp_path, monkeypatch):
    limiter.reset()
    db_path = str(tmp_path / "sec_test.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE companies (id TEXT PRIMARY KEY, company TEXT, raw_data_json TEXT)")
        conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)")
        conn.execute("INSERT INTO system_state (key, value) VALUES ('baseline_initialized', 'true')")
        conn.commit()

    run_migrations(db_path)

    user_repo = SQLiteUserRepository(db_path)
    token_repo = SQLiteTokenRepository(db_path)
    delivery_repo = SQLiteDeliveryRepository(db_path)
    email_sink = SecurityTestEmailSink()

    monkeypatch.setattr("app.database.repository.DB_PATH", db_path)
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    app.dependency_overrides[get_db_repos] = lambda: (user_repo, token_repo)
    app.dependency_overrides[get_email_service] = lambda: email_sink

    client = TestClient(app)
    client.db_path = db_path
    client.user_repo = user_repo
    client.token_repo = token_repo
    client.delivery_repo = delivery_repo
    client.email_sink = email_sink
    yield client
    app.dependency_overrides.clear()
    limiter.reset()


# -----------------------------------------------------------------------------
# AUTH-01: Valid signup verification succeeds
# -----------------------------------------------------------------------------
def test_auth_01_valid_signup_verification_succeeds(sec_client):
    res = sec_client.post("/api/v1/auth/signup", json={
        "email": "auth01@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    assert res.status_code == 201
    assert len(sec_client.email_sink.sent_emails) == 1
    raw_token = sec_client.email_sink.sent_emails[0]["raw_token"]

    verify_res = sec_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert verify_res.status_code == 200
    assert "Email Verified" in verify_res.text

    user = sec_client.user_repo.get_by_email("auth01@vit.edu")
    assert user["is_verified"] == 1
    assert user["verified_at"] is not None


# -----------------------------------------------------------------------------
# AUTH-02: Expired signup token fails
# -----------------------------------------------------------------------------
def test_auth_02_expired_signup_token_fails(sec_client):
    user_id = sec_client.user_repo.create_user("auth02@vit.edu", 2028, "VIT_IT")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    # Create expired token (1 hour in the past)
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    res = sec_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert res.status_code == 400
    assert "Invalid or Expired Link" in res.text

    user = sec_client.user_repo.get_by_id(user_id)
    assert user["is_verified"] == 0


# -----------------------------------------------------------------------------
# AUTH-03: Signup token cannot be reused
# -----------------------------------------------------------------------------
def test_auth_03_signup_token_cannot_be_reused(sec_client):
    sec_client.post("/api/v1/auth/signup", json={
        "email": "auth03@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    raw_token = sec_client.email_sink.sent_emails[0]["raw_token"]

    # First attempt: succeeds
    res1 = sec_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert res1.status_code == 200

    # Second attempt: fails
    res2 = sec_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert res2.status_code == 400
    assert "Invalid or Expired Link" in res2.text


# -----------------------------------------------------------------------------
# AUTH-04: Malformed signup token fails safely
# -----------------------------------------------------------------------------
def test_auth_04_malformed_signup_token_fails_safely(sec_client):
    malformed_tokens = [
        "",
        "   ",
        "short",
        "bad/chars/with/slashes",
        "bad#hash?param=1",
        "a" * 1000,
        "<script>alert(1)</script>",
        "' OR '1'='1",
    ]
    for token in malformed_tokens:
        res = sec_client.get(f"/api/v1/auth/verify?token={token}")
        assert res.status_code in (400, 422)
        assert res.status_code != 500
        assert "Internal Server Error" not in res.text
        assert "Traceback" not in res.text


# -----------------------------------------------------------------------------
# AUTH-05: Random token has sufficient entropy
# -----------------------------------------------------------------------------
def test_auth_05_random_token_has_sufficient_entropy():
    # 1. Action token entropy
    action_tokens = [secrets.token_urlsafe(32) for _ in range(200)]
    assert len(set(action_tokens)) == 200, "Collision detected in action tokens!"
    for t in action_tokens:
        assert len(t) >= 42, "Action token too short for 256 bits"

    # 2. Session token entropy
    session_tokens = [create_session_token(user_id=1, secret_key="test-key") for _ in range(200)]
    assert len(set(session_tokens)) == 200, "Collision detected in session tokens!"
    for st in session_tokens:
        parts = st.split(":")
        assert len(parts) == 4
        uid, exp, nonce, sig = parts
        assert len(nonce) == 64, "Session nonce must be 64 hex chars (256 bits)"
        assert len(sig) == 64, "Session signature must be 64 hex chars (SHA-256)"

    # 3. Shannon entropy calculation
    all_bytes = "".join(action_tokens).encode("utf-8")
    freq = {}
    for b in all_bytes:
        freq[b] = freq.get(b, 0) + 1
    total = len(all_bytes)
    entropy = -sum((cnt / total) * math.log2(cnt / total) for cnt in freq.values())
    # Base64url character set has at most log2(64)=6.0 bits/char. Random base64 exceeds 5.8
    assert entropy >= 5.5, f"Entropy {entropy} is lower than expected for CSPRNG"


# -----------------------------------------------------------------------------
# AUTH-06: Raw token is not stored in DB
# -----------------------------------------------------------------------------
def test_auth_06_raw_token_is_not_stored_in_db(sec_client):
    sec_client.post("/api/v1/auth/signup", json={
        "email": "auth06@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    raw_token = sec_client.email_sink.sent_emails[0]["raw_token"]
    expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    with sqlite3.connect(sec_client.db_path) as conn:
        cursor = conn.execute("SELECT * FROM action_tokens WHERE token_type = 'SIGNUP_VERIFY'")
        rows = cursor.fetchall()
        assert len(rows) >= 1
        for row in rows:
            row_dict = dict(zip([col[0] for col in cursor.description], row))
            assert row_dict["token_hash"] == expected_hash
            assert raw_token not in str(row)


# -----------------------------------------------------------------------------
# AUTH-07: Valid magic link succeeds
# -----------------------------------------------------------------------------
def test_auth_07_valid_magic_link_succeeds(sec_client):
    user_id = sec_client.user_repo.create_user("auth07@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 303
    assert "tpo_session" in res.cookies

    # Access preferences with received session
    pref_res = sec_client.get("/api/v1/preferences", cookies={"tpo_session": res.cookies["tpo_session"]})
    assert pref_res.status_code == 200
    data = pref_res.json()
    assert data["email"] == "auth07@vit.edu"


# -----------------------------------------------------------------------------
# AUTH-08: Expired magic link fails
# -----------------------------------------------------------------------------
def test_auth_08_expired_magic_link_fails(sec_client):
    user_id = sec_client.user_repo.create_user("auth08@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )

    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 400
    assert "Invalid or Expired Link" in res.text
    assert "tpo_session" not in res.cookies


# -----------------------------------------------------------------------------
# AUTH-09: Magic link cannot be reused
# -----------------------------------------------------------------------------
def test_auth_09_magic_link_cannot_be_reused(sec_client):
    user_id = sec_client.user_repo.create_user("auth09@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # First exchange succeeds
    res1 = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res1.status_code == 303

    # Replay fails
    res2 = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res2.status_code == 400
    assert "Invalid or Expired Link" in res2.text


# -----------------------------------------------------------------------------
# AUTH-10: Malformed magic link fails
# -----------------------------------------------------------------------------
def test_auth_10_malformed_magic_link_fails(sec_client):
    malformed = ["", "bad/token", "a" * 500, "short", "bad token with spaces"]
    for t in malformed:
        res = sec_client.get(f"/api/v1/preferences/request?token={t}")
        assert res.status_code in (400, 422)


# -----------------------------------------------------------------------------
# AUTH-11: Unsubscribe token is single-use
# -----------------------------------------------------------------------------
def test_auth_11_unsubscribe_token_is_single_use(sec_client):
    user_id = sec_client.user_repo.create_user("auth11@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    res1 = sec_client.get(f"/api/v1/unsubscribe?token={raw_token}")
    assert res1.status_code == 200
    assert "Unsubscribed Successfully" in res1.text
    user = sec_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0

    res2 = sec_client.get(f"/api/v1/unsubscribe?token={raw_token}")
    assert res2.status_code == 400
    assert "Invalid or Expired" in res2.text


# -----------------------------------------------------------------------------
# AUTH-12: Expired unsubscribe token fails
# -----------------------------------------------------------------------------
def test_auth_12_expired_unsubscribe_token_fails(sec_client):
    user_id = sec_client.user_repo.create_user("auth12@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )

    res = sec_client.get(f"/api/v1/unsubscribe?token={raw_token}")
    assert res.status_code == 400
    assert "Invalid or Expired" in res.text

    user = sec_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 1


# -----------------------------------------------------------------------------
# AUTH-13: Session cookie is HttpOnly
# -----------------------------------------------------------------------------
def test_auth_13_session_cookie_is_httponly(sec_client):
    user_id = sec_client.user_repo.create_user("auth13@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 303
    set_cookie = res.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()


# -----------------------------------------------------------------------------
# AUTH-14: Session cookie is Secure
# -----------------------------------------------------------------------------
def test_auth_14_session_cookie_is_secure(sec_client):
    user_id = sec_client.user_repo.create_user("auth14@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 303
    set_cookie = res.headers.get("set-cookie", "")
    assert "secure" in set_cookie.lower()


# -----------------------------------------------------------------------------
# AUTH-15: Session cookie has SameSite=Lax or stricter
# -----------------------------------------------------------------------------
def test_auth_15_session_cookie_has_samesite_lax_or_stricter(sec_client):
    user_id = sec_client.user_repo.create_user("auth15@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 303
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "samesite=lax" in set_cookie or "samesite=strict" in set_cookie
    assert "max-age=3600" in set_cookie
    assert "path=/" in set_cookie


# -----------------------------------------------------------------------------
# AUTH-16: Session expires correctly
# -----------------------------------------------------------------------------
def test_auth_16_session_expires_correctly(sec_client):
    user_id = sec_client.user_repo.create_user("auth16@vit.edu", 2028, "VIT_CE")
    # Expired token (-60s)
    expired_session = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY, max_age_seconds=-60)
    res = sec_client.get("/api/v1/preferences", cookies={"tpo_session": expired_session})
    assert res.status_code == 401
    assert "Session has expired" in res.json()["detail"]


# -----------------------------------------------------------------------------
# AUTH-17: Tampered session fails
# -----------------------------------------------------------------------------
def test_auth_17_tampered_session_fails(sec_client):
    user_id = sec_client.user_repo.create_user("auth17@vit.edu", 2028, "VIT_CE")
    valid_session = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY, max_age_seconds=3600)
    uid, exp, nonce, sig = valid_session.split(":")

    # 1. Tamper user ID (privilege escalation attempt)
    tampered_uid = f"{int(uid)+999}:{exp}:{nonce}:{sig}"
    res1 = sec_client.get("/api/v1/preferences", cookies={"tpo_session": tampered_uid})
    assert res1.status_code == 401

    # 2. Tamper expiration
    tampered_exp = f"{uid}:{int(exp)+10000}:{nonce}:{sig}"
    res2 = sec_client.get("/api/v1/preferences", cookies={"tpo_session": tampered_exp})
    assert res2.status_code == 401

    # 3. Tamper nonce
    tampered_nonce = f"{uid}:{exp}:{'0'*64}:{sig}"
    res3 = sec_client.get("/api/v1/preferences", cookies={"tpo_session": tampered_nonce})
    assert res3.status_code == 401

    # 4. Tamper signature
    tampered_sig = f"{uid}:{exp}:{nonce}:{'f'*64}"
    res4 = sec_client.get("/api/v1/preferences", cookies={"tpo_session": tampered_sig})
    assert res4.status_code == 401


# -----------------------------------------------------------------------------
# AUTH-18: Malformed session fails
# -----------------------------------------------------------------------------
def test_auth_18_malformed_session_fails(sec_client):
    malformed_sessions = [
        "",
        "singletoken",
        "1:2",
        "1:2:3:4:5",
        "notanint:notanint:nonce:sig",
        "1:" + "a" * 1000,
        ":::",
    ]
    for ms in malformed_sessions:
        res = sec_client.get("/api/v1/preferences", cookies={"tpo_session": ms})
        assert res.status_code == 401


# -----------------------------------------------------------------------------
# AUTH-19: Session fixation is prevented
# -----------------------------------------------------------------------------
def test_auth_19_session_fixation_is_prevented(sec_client):
    user_id = sec_client.user_repo.create_user("auth19@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    attacker_session = create_session_token(user_id=9999, secret_key=settings.SECRET_KEY)

    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # Client presents attacker session while exchanging magic link
    sec_client.cookies.set("tpo_session", attacker_session)
    res = sec_client.get(f"/api/v1/preferences/request?token={raw_token}", follow_redirects=False)
    assert res.status_code == 303
    fresh_session = res.cookies["tpo_session"]

    # Fresh session is distinct and has different nonce
    assert fresh_session != attacker_session
    # Previous attacker session is revoked and cannot be used
    old_res = sec_client.get("/api/v1/preferences", cookies={"tpo_session": attacker_session})
    assert old_res.status_code == 401


# -----------------------------------------------------------------------------
# AUTH-20: Repeated magic-link requests are rate-limited
# -----------------------------------------------------------------------------
def test_auth_20_repeated_magic_link_requests_are_rate_limited(sec_client):
    user_id = sec_client.user_repo.create_user("auth20@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    # Max allowed per email is 5 per 15 minutes
    for i in range(5):
        res = sec_client.post("/api/v1/preferences/request-link", json={"email": "auth20@vit.edu"})
        assert res.status_code == 200, f"Attempt {i+1} should succeed"

    # 6th attempt must be blocked by rate limiter
    res_blocked = sec_client.post("/api/v1/preferences/request-link", json={"email": "auth20@vit.edu"})
    assert res_blocked.status_code == 429
    assert "Retry-After" in res_blocked.headers
    assert "Too many" in res_blocked.json()["detail"]


# -----------------------------------------------------------------------------
# AUTH-21: Signup email abuse is rate-limited
# -----------------------------------------------------------------------------
def test_auth_21_signup_email_abuse_is_rate_limited(sec_client):
    target_email = "auth21_abuse@vit.edu"
    for i in range(5):
        res = sec_client.post("/api/v1/auth/signup", json={
            "email": target_email,
            "graduation_year": 2028,
            "branch_canonical": "VIT_CE"
        })
        assert res.status_code == 201

    res_blocked = sec_client.post("/api/v1/auth/signup", json={
        "email": target_email,
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    assert res_blocked.status_code == 429
    assert "Retry-After" in res_blocked.headers


# -----------------------------------------------------------------------------
# AUTH-22: Verification brute force is rate-limited
# -----------------------------------------------------------------------------
def test_auth_22_verification_brute_force_is_rate_limited(sec_client, monkeypatch):
    # Set limit low for fast deterministic testing
    monkeypatch.setattr(settings, "RATE_LIMIT_VERIFY_PER_IP", 10)

    for i in range(10):
        fake_token = secrets.token_urlsafe(32)
        res = sec_client.get(f"/api/v1/auth/verify?token={fake_token}")
        assert res.status_code == 400

    # 11th attempt hits IP rate limit
    blocked_token = secrets.token_urlsafe(32)
    res_blocked = sec_client.get(f"/api/v1/auth/verify?token={blocked_token}")
    assert res_blocked.status_code == 429
    assert "Retry-After" in res_blocked.headers


# -----------------------------------------------------------------------------
# AUTH-23: Authentication errors do not leak secrets
# -----------------------------------------------------------------------------
def test_auth_23_authentication_errors_do_not_leak_secrets(sec_client):
    error_endpoints = [
        ("GET", "/api/v1/auth/verify?token=malformed_token"),
        ("GET", "/api/v1/unsubscribe?token=malformed_token"),
        ("GET", "/api/v1/preferences/request?token=malformed_token"),
        ("POST", "/api/v1/auth/signup", {"email": "invalid@", "graduation_year": 2028, "branch_canonical": "VIT_CE"}),
        ("POST", "/api/v1/preferences/request-link", {"email": "bad_email"}),
        ("PUT", "/api/v1/preferences", {"pref_internship": True, "pref_placement": True, "pref_ppo": True}),
    ]

    secrets_to_check = [
        settings.SECRET_KEY,
        "sqlite://",
        "playwright",
        "Traceback (most recent call last)",
        "password",
    ]

    for item in error_endpoints:
        method = item[0]
        url = item[1]
        body = item[2] if len(item) > 2 else None

        if method == "GET":
            res = sec_client.get(url)
        elif method == "POST":
            res = sec_client.post(url, json=body)
        elif method == "PUT":
            res = sec_client.put(url, json=body)

        for secret in secrets_to_check:
            assert secret not in res.text, f"Secret '{secret}' leaked in endpoint {url}!"


# -----------------------------------------------------------------------------
# AUTH-24: Logs do not contain raw tokens/secrets
# -----------------------------------------------------------------------------
def test_auth_24_logs_do_not_contain_raw_tokens_or_secrets(sec_client, caplog):
    caplog.set_level(logging.DEBUG)

    # 1. Signup flow
    sec_client.post("/api/v1/auth/signup", json={
        "email": "auth24@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    signup_token = sec_client.email_sink.sent_emails[0]["raw_token"]

    # 2. Verify flow
    sec_client.get(f"/api/v1/auth/verify?token={signup_token}")

    # 3. Preference link request
    sec_client.post("/api/v1/preferences/request-link", json={"email": "auth24@vit.edu"})
    pref_token = sec_client.email_sink.sent_emails[1]["raw_token"]

    # 4. Exchange magic link for session
    res_ex = sec_client.get(f"/api/v1/preferences/request?token={pref_token}", follow_redirects=False)
    session_cookie = res_ex.cookies.get("tpo_session")

    # 5. Access and update preferences
    sec_client.put("/api/v1/preferences", json={
        "pref_internship": True,
        "pref_placement": False,
        "pref_ppo": False
    }, cookies={"tpo_session": session_cookie})

    app_log_records = [rec.getMessage() for rec in caplog.records if rec.name.startswith("app.")]
    app_logs = "\n".join(app_log_records)

    # Assert raw action tokens are NEVER logged by application code
    assert signup_token not in app_logs, "Raw signup token found in app logs!"
    assert pref_token not in app_logs, "Raw preference token found in app logs!"
    assert session_cookie not in app_logs, "Raw session cookie found in app logs!"

    # Assert TPO credentials and secret keys are never logged
    assert settings.SECRET_KEY not in app_logs


# -----------------------------------------------------------------------------
# AUTH-25: Public authentication flows cannot access TPO credentials
# -----------------------------------------------------------------------------
def test_auth_25_public_authentication_flows_cannot_access_tpo_credentials(sec_client):
    """
    CRITICAL INVARIANT TEST:
    Proves that public authentication endpoints and modules have ZERO structural or
    functional access to internal watcher credentials or Playwright scrapers.
    """
    import inspect
    from app.api import routes_auth, routes_preferences, session, rate_limiter

    # 1. Verify AST/imports: No public API module imports AuthManager
    for mod in [routes_auth, routes_preferences, session, rate_limiter]:
        source = inspect.getsource(mod)
        assert "AuthManager" not in source
        assert "TPO_USERNAME" not in source
        assert "TPO_PASSWORD" not in source
        assert "async_playwright" not in source
        assert "fetch_companies" not in source

    # 2. Verify endpoints cannot trigger scraping or return credentials
    routes = []
    for r in app.routes:
        p = getattr(r, "path", None)
        if p:
            routes.append(p)
    for r in routes:
        assert "scrape" not in r.lower()
        assert "crawl" not in r.lower()
        assert "credentials" not in r.lower()

    # 3. Check public API responses
    for path in ["/health", "/api/v1/preferences", "/api/v1/auth/signup", "/docs"]:
        res = sec_client.get(path)
        assert "TPO_PASSWORD" not in res.text
        if settings.TPO_PASSWORD:
            assert settings.TPO_PASSWORD not in res.text


# =============================================================================
# FOCUSED REVIEW REGRESSION TESTS (Sprint 1 Finalization)
# =============================================================================

# -----------------------------------------------------------------------------
# REV-01: Legacy 3-part sessions are strictly rejected (no downgrade attack)
# -----------------------------------------------------------------------------
def test_rev_01_legacy_3part_session_is_strictly_rejected(sec_client):
    import hmac
    user_id = sec_client.user_repo.create_user("legacy_user@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    # Construct legacy 3-part session token: user_id:expires_at:signature (no nonce)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + 3600
    legacy_payload = f"{user_id}:{expires_at}"
    legacy_sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        legacy_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    legacy_token = f"{legacy_payload}:{legacy_sig}"

    # 1. Direct verify_session_token check
    assert verify_session_token(legacy_token, settings.SECRET_KEY) is None

    # 2. HTTP endpoint check: passing legacy token must result in 401 Unauthorized
    res = sec_client.get("/api/v1/preferences", cookies={"tpo_session": legacy_token})
    assert res.status_code == 401
    assert "Session has expired or is invalid" in res.json()["detail"]


# -----------------------------------------------------------------------------
# REV-02: Session revocation lifecycle and explicit logout
# -----------------------------------------------------------------------------
def test_rev_02_session_revocation_lifecycle_and_logout(sec_client):
    user_id = sec_client.user_repo.create_user("revocation_user@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    valid_session = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY)

    # 1. Active session succeeds
    res1 = sec_client.get("/api/v1/preferences", cookies={"tpo_session": valid_session})
    assert res1.status_code == 200

    # 2. Perform logout
    res_logout = sec_client.post("/api/v1/preferences/logout", cookies={"tpo_session": valid_session})
    assert res_logout.status_code == 200
    assert "logged out" in res_logout.json()["message"].lower()

    # Cookie removal asserted in response headers
    set_cookie = res_logout.headers.get("set-cookie", "")
    assert 'tpo_session=""' in set_cookie or 'max-age=0' in set_cookie.lower()

    # 3. Subsequent use of the exact same session is rejected (in-process revocation)
    res_revoked = sec_client.get("/api/v1/preferences", cookies={"tpo_session": valid_session})
    assert res_revoked.status_code == 401


# -----------------------------------------------------------------------------
# REV-03: Deactivated user session fails persistently across restart simulation
# -----------------------------------------------------------------------------
def test_rev_03_deactivated_user_session_fails_even_after_restart_simulation(sec_client):
    from app.api.session import reset_session_revocations
    user_id = sec_client.user_repo.create_user("deact_user@vit.edu", 2028, "VIT_CE")
    sec_client.user_repo.set_verified(user_id)

    valid_session = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY)

    # Active session works initially
    res_active = sec_client.get("/api/v1/preferences", cookies={"tpo_session": valid_session})
    assert res_active.status_code == 200

    # User unsubscribes / gets deactivated in database
    sec_client.user_repo.set_unsubscribed(user_id)

    # SIMULATE CONTAINER RESTART: clear in-process revocation memory
    reset_session_revocations()

    # Attempt to access preferences with valid cryptographic signature
    res_deactivated = sec_client.get("/api/v1/preferences", cookies={"tpo_session": valid_session})
    assert res_deactivated.status_code == 401
    assert "deactivated" in res_deactivated.json()["detail"].lower()


# -----------------------------------------------------------------------------
# REV-04: X-Forwarded-For spoofing defense in rate limiter
# -----------------------------------------------------------------------------
def test_rev_04_forwarded_for_spoofing_defense():
    from unittest.mock import Mock
    from app.api.rate_limiter import get_client_ip
    from fastapi import Request

    # Case 1: Single remote IP from trusted proxy
    req1 = Mock(spec=Request)
    req1.headers = {"x-forwarded-for": "198.51.100.5"}
    req1.client = Mock(host="172.18.0.2")
    assert get_client_ip(req1) == "198.51.100.5"

    # Case 2: Attacker injects fake client IP (1.1.1.1) ahead of real IP (203.0.113.88)
    req2 = Mock(spec=Request)
    req2.headers = {"x-forwarded-for": "1.1.1.1, 203.0.113.88"}
    req2.client = Mock(host="172.18.0.2")
    # Must resolve to 203.0.113.88 (the rightmost IP appended by Caddy)
    assert get_client_ip(req2) == "203.0.113.88"

    # Case 3: Multiple spoofed hops + real IP
    req3 = Mock(spec=Request)
    req3.headers = {"x-forwarded-for": "10.0.0.1, 127.0.0.1, 8.8.8.8, 198.51.100.99"}
    req3.client = Mock(host="172.18.0.2")
    assert get_client_ip(req3) == "198.51.100.99"

    # Case 4: Malformed junk in header falls back safely to valid IP or peer
    req4 = Mock(spec=Request)
    req4.headers = {"x-forwarded-for": "<script>alert(1)</script>, not-an-ip"}
    req4.client = Mock(host="172.18.0.2")
    assert get_client_ip(req4) == "172.18.0.2"


# -----------------------------------------------------------------------------
# REV-05: Rate-limiting spoofing attempt blocked by rightmost IP extraction
# -----------------------------------------------------------------------------
def test_rev_05_rate_limiting_spoofing_attempt_blocked_by_rightmost_ip(sec_client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_VERIFY_PER_IP", 5)

    real_ip = "198.51.100.77"

    # Attacker tries rotating the leftmost IP to evade rate limiting
    for i in range(5):
        spoofed_left = f"10.0.0.{i+1}"
        res = sec_client.get(
            "/api/v1/auth/verify?token=fake_token_test",
            headers={"x-forwarded-for": f"{spoofed_left}, {real_ip}"}
        )
        assert res.status_code == 400

    # 6th request with yet another spoofed leftmost IP must STILL be rate-limited
    res_blocked = sec_client.get(
        "/api/v1/auth/verify?token=fake_token_test",
        headers={"x-forwarded-for": f"10.0.0.99, {real_ip}"}
    )
    assert res_blocked.status_code == 429
    assert "Retry-After" in res_blocked.headers


# -----------------------------------------------------------------------------
# REV-06: Cookie Secure behavior behind proxy even if X-Forwarded-Proto: http sent
# -----------------------------------------------------------------------------
def test_rev_06_cookie_secure_behind_proxy_even_if_http_forwarded_proto_sent(sec_client):
    user_id = sec_client.user_repo.create_user("proto_test@vit.edu", 2028, "VIT_CE")
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    sec_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # Client / proxy sends X-Forwarded-Proto: http
    res = sec_client.get(
        f"/api/v1/preferences/request?token={raw_token}",
        headers={"x-forwarded-proto": "http"},
        follow_redirects=False
    )
    assert res.status_code == 303
    set_cookie = res.headers.get("set-cookie", "").lower()
    # Must still be Secure because COOKIE_SECURE is True by default
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
