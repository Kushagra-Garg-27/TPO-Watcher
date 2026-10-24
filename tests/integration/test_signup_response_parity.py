"""F19: signup responses are state-neutral and never claim delivery.

Fictional accounts in every verification/transport state must produce an
indistinguishable public response carrying no sensitive data.
"""
import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.database.repository import DatabaseRepository

NEUTRAL_HINT = "Request received"


@pytest.fixture
def repo(tmp_path):
    path = (tmp_path / "signup-parity.sqlite").resolve()
    assert path.is_relative_to(isolation.ROOT)
    return DatabaseRepository(str(path))


@pytest.fixture
def client(repo, monkeypatch):
    from app.api import routes_auth
    monkeypatch.setattr(routes_auth, "get_db_repos", lambda: (
        repo.users, repo.tokens))
    sent = {"accepted": True, "raise": False, "calls": 0}

    class FakeService:
        def send_verification_email(self, email, token):
            sent["calls"] += 1
            if sent["raise"]:
                raise RuntimeError("PRIVATE-SMTP-FAILURE")
            return sent["accepted"]

        send_preference_link_email = send_verification_email

    import app.api.routes_auth as mod
    monkeypatch.setattr(mod, "get_email_service", lambda: FakeService())
    return TestClient(create_app()), sent


def signup(client_, email):
    return client_.post("/api/v1/auth/signup", json={
        "email": email, "graduation_year": 2028, "branch_canonical": "VIT_CE",
        "pref_internship": True, "pref_placement": True, "pref_ppo": True})


def test_f19_responses_are_identical_across_account_states(client, repo):
    http, _ = client
    responses = []
    for email in ("new-state@example.invalid", "unverified-state@example.invalid"):
        responses.append(signup(http, email).json())
    # Verified account (existing, is_verified=1) selects the prefs-link branch.
    uid = repo.users.create_user("verified-state@example.invalid", 2028, "VIT_CE")
    repo.users.set_verified(uid)
    responses.append(signup(http, "verified-state@example.invalid").json())
    assert len({r["message"] for r in responses}) == 1
    assert NEUTRAL_HINT in responses[0]["message"]
    assert "sent" not in responses[0]["message"].lower()
    assert all(set(r) == {"status", "message"} for r in responses)
    assert responses[0]["status"] == "success"


def test_f19_transport_failure_keeps_neutral_response(client):
    http, sent = client
    sent["raise"] = True
    response = signup(http, "transport-fail@example.invalid")
    assert response.status_code == 201
    assert NEUTRAL_HINT in response.json()["message"]
    assert "sent" not in response.json()["message"].lower()


def test_f19_transport_rejection_keeps_neutral_response(client):
    http, sent = client
    sent["accepted"] = False
    response = signup(http, "transport-reject@example.invalid")
    assert response.status_code == 201
    assert NEUTRAL_HINT in response.json()["message"]
    assert "sent" not in response.json()["message"].lower()


def test_f19_inactive_account_matches_new_account_response(client, repo):
    http, _ = client
    uid = repo.users.create_user("inactive-state@example.invalid", 2028, "VIT_CE")
    repo.users.set_verified(uid)
    repo.users.set_unsubscribed(uid)
    inactive = signup(http, "inactive-state@example.invalid").json()
    fresh = signup(http, "fresh-state@example.invalid").json()
    assert inactive == fresh
