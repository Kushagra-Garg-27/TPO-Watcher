import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.api.app import app
from app.config import settings
from app.database.repository import DatabaseRepository
from app.api.session import create_session_token, verify_session_token

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_logout.sqlite")
    db_repo = DatabaseRepository(db_path)
    db_repo.set_baseline_initialized()
    monkeypatch.setattr("app.database.repository.DB_PATH", db_path)
    monkeypatch.setattr("app.health.server.DB_PATH", db_path)
    return TestClient(app)

def test_canonical_logout_clears_cookie_and_revokes_session(client):
    """
    POST /api/v1/auth/logout must:
    1. Revoke the active session token so verify_session_token fails.
    2. Instruct the browser to clear the tpo_session cookie (Max-Age=0).
    """
    token = create_session_token(user_id=42, secret_key=settings.SECRET_KEY)
    assert verify_session_token(token, settings.SECRET_KEY) == 42

    res = client.post("/api/v1/auth/logout", cookies={"tpo_session": token})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["message"] == "Successfully logged out."

    # Verify session revoked
    assert verify_session_token(token, settings.SECRET_KEY) is None

    # Verify cookie clearance header
    set_cookie = res.headers.get("set-cookie", "")
    assert 'tpo_session=""' in set_cookie or "tpo_session=" in set_cookie
    assert "Max-Age=0" in set_cookie

def test_compatibility_preferences_logout_behaves_equivalently(client):
    """
    POST /api/v1/preferences/logout compatibility route must execute the identical
    shared logout behavior as canonical /api/v1/auth/logout without HTTP redirects.
    """
    token = create_session_token(user_id=99, secret_key=settings.SECRET_KEY)
    assert verify_session_token(token, settings.SECRET_KEY) == 99

    res = client.post("/api/v1/preferences/logout", cookies={"tpo_session": token}, follow_redirects=False)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["message"] == "Successfully logged out."

    # Verify session revoked
    assert verify_session_token(token, settings.SECRET_KEY) is None

    # Verify cookie clearance header
    set_cookie = res.headers.get("set-cookie", "")
    assert "Max-Age=0" in set_cookie

def test_logout_missing_cookie_safe(client):
    """
    Calling logout without any session cookie must be safe, idempotent, and return HTTP 200.
    """
    res = client.post("/api/v1/auth/logout")
    assert res.status_code == 200
    assert res.json()["status"] == "success"

def test_logout_expired_or_replayed_safe(client):
    """
    Calling logout with an already-revoked or expired session must be safe and idempotent.
    """
    token = create_session_token(user_id=101, secret_key=settings.SECRET_KEY)
    # First logout
    res1 = client.post("/api/v1/auth/logout", cookies={"tpo_session": token})
    assert res1.status_code == 200

    # Replayed logout
    res2 = client.post("/api/v1/auth/logout", cookies={"tpo_session": token})
    assert res2.status_code == 200
    assert res2.json()["status"] == "success"

def test_static_frontend_api_logout_usage():
    """
    Static validation: ensures frontend/src/lib/api.ts uses canonical /api/v1/auth/logout
    and does NOT use legacy /api/v1/preferences/logout.
    """
    api_ts_path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api.ts"
    assert api_ts_path.exists(), f"Could not find frontend api.ts at {api_ts_path}"

    content = api_ts_path.read_text(encoding="utf-8")
    assert "/api/v1/auth/logout" in content, "frontend/src/lib/api.ts must contain /api/v1/auth/logout"
    assert "/api/v1/preferences/logout" not in content, "frontend/src/lib/api.ts must not contain /api/v1/preferences/logout"
