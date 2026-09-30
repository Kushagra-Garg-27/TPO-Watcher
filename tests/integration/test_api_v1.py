import pytest
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.api.app import app
from app.database.migrations import run_migrations
from app.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteTokenRepository,
    SQLiteDeliveryRepository
)
from app.api.routes_auth import get_db_repos
from app.api.email_service import get_email_service

class FakeEmailService:
    """In-memory email service sink that captures dispatches without network calls."""
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
def test_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_api.sqlite")
    # Initialize basic companies and system state tables
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE companies (id TEXT PRIMARY KEY, company TEXT, raw_data_json TEXT)")
        conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)")
        conn.execute("INSERT INTO system_state (key, value) VALUES ('baseline_initialized', 'true')")
        conn.commit()

    run_migrations(db_path)

    # Override db repos dependency
    user_repo = SQLiteUserRepository(db_path)
    token_repo = SQLiteTokenRepository(db_path)
    delivery_repo = SQLiteDeliveryRepository(db_path)

    # In-memory fake email service sink
    fake_email_service = FakeEmailService()

    monkeypatch.setattr("app.database.repository.DB_PATH", db_path)
    app.dependency_overrides[get_db_repos] = lambda: (user_repo, token_repo)
    app.dependency_overrides[get_email_service] = lambda: fake_email_service

    client = TestClient(app)
    client.db_path = db_path
    client.user_repo = user_repo
    client.token_repo = token_repo
    client.delivery_repo = delivery_repo
    client.fake_email = fake_email_service
    yield client
    app.dependency_overrides.clear()

def test_health_endpoint(test_client):
    res = test_client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["baseline_initialized"] is True

def test_signup_validation(test_client):
    # 1. Valid 2028 signup
    res = test_client.post("/api/v1/auth/signup", json={
        "email": "student2028@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE",
        "pref_internship": True,
        "pref_placement": True,
        "pref_ppo": True
    })
    assert res.status_code == 201
    assert "Verification link sent" in res.json()["message"]

    # Verify fake email sink captured verification email without touching SMTP
    assert len(test_client.fake_email.sent_emails) == 1
    assert test_client.fake_email.sent_emails[0]["to_email"] == "student2028@vit.edu"
    assert test_client.fake_email.sent_emails[0]["type"] == "SIGNUP_VERIFY"

    # 2. Rejection of graduation year != 2028
    res_bad_year = test_client.post("/api/v1/auth/signup", json={
        "email": "student2027@vit.edu",
        "graduation_year": 2027,
        "branch_canonical": "VIT_CE"
    })
    assert res_bad_year.status_code == 422
    assert "2028" in res_bad_year.text

    # 3. Rejection of non-canonical branch
    res_bad_branch = test_client.post("/api/v1/auth/signup", json={
        "email": "student_cs@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "Computer Science" # Not a canonical enum
    })
    assert res_bad_branch.status_code == 422

def test_verification_flow(test_client):
    # Signup user
    test_client.post("/api/v1/auth/signup", json={
        "email": "verify_me@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_IT"
    })

    # Verify email sink captured the verification link
    assert any(
        e["to_email"] == "verify_me@vit.edu" and e["type"] == "SIGNUP_VERIFY"
        for e in test_client.fake_email.sent_emails
    )

    user = test_client.user_repo.get_by_email("verify_me@vit.edu")
    assert user["is_verified"] == 0

    # Retrieve created token from db
    with sqlite3.connect(test_client.db_path) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM action_tokens WHERE user_id = ? AND token_type = 'SIGNUP_VERIFY'", (user["id"],))
        token_row = dict(c.fetchone())

    # We need to simulate the raw token that hashes to token_row['token_hash']
    # Let's directly test verification with a known raw token
    import secrets
    raw_token = secrets.token_urlsafe(32)
    thash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    test_client.token_repo.create_token(
        user_id=user["id"],
        token_hash=thash,
        token_type="SIGNUP_VERIFY",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
    )

    # Click verify
    res = test_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert res.status_code == 200
    assert "Email Verified" in res.text

    # Verify state in DB
    updated_user = test_client.user_repo.get_by_email("verify_me@vit.edu")
    assert updated_user["is_verified"] == 1

    # Second click must fail (one-time use)
    res_second = test_client.get(f"/api/v1/auth/verify?token={raw_token}")
    assert res_second.status_code == 400
    assert "Invalid or Expired" in res_second.text

def test_unsubscribe_endpoint(test_client):
    user_id = test_client.user_repo.create_user("unsub_test@vit.edu", 2028, "VIT_CE")
    test_client.user_repo.set_verified(user_id)

    raw_unsub = "unsub_raw_123"
    thash = hashlib.sha256(raw_unsub.encode("utf-8")).hexdigest()
    test_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="UNSUBSCRIBE",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )

    # Unsubscribe
    res = test_client.get(f"/api/v1/unsubscribe?token={raw_unsub}")
    assert res.status_code == 200
    assert "Unsubscribed Successfully" in res.text

    user = test_client.user_repo.get_by_id(user_id)
    assert user["is_active"] == 0

    # Token should now be spent
    res_again = test_client.get(f"/api/v1/unsubscribe?token={raw_unsub}")
    assert res_again.status_code == 400

def test_preference_management_lifecycle(test_client):
    user_id = test_client.user_repo.create_user("pref_test@vit.edu", 2028, "VIT_CE")
    test_client.user_repo.set_verified(user_id)

    raw_pref_token = "pref_action_token_abc"
    thash = hashlib.sha256(raw_pref_token.encode("utf-8")).hexdigest()
    test_client.token_repo.create_token(
        user_id=user_id,
        token_hash=thash,
        token_type="MANAGE_PREFS",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)
    )

    # 1. Unauthenticated request to /api/v1/preferences -> 401
    res_unauth = test_client.get("/api/v1/preferences")
    assert res_unauth.status_code == 401

    # 2. Exchange 15-minute token for 1-hour session
    res_exchange = test_client.get(f"/api/v1/preferences/request?token={raw_pref_token}", follow_redirects=False)
    assert res_exchange.status_code == 303
    assert "tpo_session" in res_exchange.cookies

    # 3. Retrieve preferences using session cookie
    cookies = {"tpo_session": res_exchange.cookies["tpo_session"]}
    res_get = test_client.get("/api/v1/preferences", cookies=cookies)
    assert res_get.status_code == 200
    prefs = res_get.json()
    assert prefs["email"] == "pref_test@vit.edu"
    assert prefs["pref_internship"] is True

    # 4. Update preferences
    res_put = test_client.put("/api/v1/preferences", json={
        "pref_internship": True,
        "pref_placement": False,
        "pref_ppo": False
    }, cookies=cookies)
    assert res_put.status_code == 200

    # Verify update in DB
    updated_prefs = test_client.user_repo.get_preferences(user_id)
    assert updated_prefs["pref_internship"] == 1
    assert updated_prefs["pref_placement"] == 0
    assert updated_prefs["pref_ppo"] == 0

    # 5. Request fresh preference access link (dispatches MANAGE_PREFS email to fake sink)
    res_req = test_client.post("/api/v1/preferences/request-link", json={"email": "pref_test@vit.edu"})
    assert res_req.status_code == 200
    assert any(
        e["to_email"] == "pref_test@vit.edu" and e["type"] == "MANAGE_PREFS"
        for e in test_client.fake_email.sent_emails
    )


def test_smtp_safety_guardrail_prevents_real_email_dispatch(test_client):
    """
    HARD REGRESSION TEST: Proves integration tests never invoke real SMTP.
    Verifies that:
    1. Public endpoints route dispatches through in-memory FakeEmailService sink.
    2. Any direct invocation of real EmailNotificationProvider._send_email_to triggers the safety trap.
    """
    initial_sent_count = len(test_client.fake_email.sent_emails)

    # 1. Signup routes through fake sink
    res_signup = test_client.post("/api/v1/auth/signup", json={
        "email": "safetest2028@vit.edu",
        "graduation_year": 2028,
        "branch_canonical": "VIT_CE"
    })
    assert res_signup.status_code == 201
    assert len(test_client.fake_email.sent_emails) == initial_sent_count + 1
    assert test_client.fake_email.sent_emails[-1]["to_email"] == "safetest2028@vit.edu"
    assert test_client.fake_email.sent_emails[-1]["type"] == "SIGNUP_VERIFY"

    # 2. Hard structural trap: prove that unmocked real smtplib.SMTP raises RuntimeError
    import smtplib
    with pytest.raises(RuntimeError, match="CRITICAL TEST GUARDRAIL TRIGGERED"):
        smtplib.SMTP("smtp.gmail.com", 587)

    # 3. Prove that EmailNotificationProvider._send_email_to fails safely when trap blocks SMTP
    from app.notifications.email import EmailNotificationProvider
    provider = EmailNotificationProvider()
    assert provider._send_email_to("trap_test@vit.edu", "Test Subject", "<p>Test</p>") is False
