"""Fictional production account acceptance; no source or mail transport."""
import hashlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.database.repository import DatabaseRepository
from app.api.app import create_app
from app.api.session import create_session_token
from app.config import settings


def account_database(tmp_path):
    from app.database.account_schema import install_account_schema
    db = DatabaseRepository(str(tmp_path / "accounts.sqlite"))
    with sqlite3.connect(db.db_path) as connection:
        connection.row_factory = sqlite3.Row
        install_account_schema(connection)
    uid = db.users.create_user("fictional24@vit.edu", 2028, "VIT_CE")
    db.users.set_verified(uid)
    return db, uid


def test_verified_institutional_suffix_does_not_authorize_delivery(tmp_path):
    from app.auth.accounts import production_notification_eligible, AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        assert service.status(uid)["review_state"] == "UNREVIEWED"
        assert not production_notification_eligible(connection, uid)


def approve(service, uid):
    from app.auth.accounts import OperatorAuthorization
    service.review(uid, "APPROVED", authority=OperatorAuthorization("fictional-operator", "approval:fixture"),
                   institution="VIT_PUNE", cohort=2028, branch="VIT_CE", evidence_reference="roster:fictional-1")


def ready(service, uid):
    approve(service, uid)
    raw = service.request_destination(uid, "destination@example.invalid")
    assert service.confirm_destination(raw) == uid
    service.set_consent(uid, True, "student-email-v1")


def test_review_consent_and_independent_destination_are_all_required(tmp_path):
    from app.auth.accounts import production_notification_eligible, AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        approve(service, uid)
        service.set_consent(uid, True, "student-email-v1")
        assert not production_notification_eligible(connection, uid)
        raw = service.request_destination(uid, "destination@example.invalid")
        assert not production_notification_eligible(connection, uid)
        assert service.confirm_destination(raw) == uid
        assert service.confirm_destination(raw) is None
        assert production_notification_eligible(connection, uid, destination="destination@example.invalid")
        assert not production_notification_eligible(connection, uid, destination="elsewhere@example.invalid")
        assert not production_notification_eligible(connection, uid, now_ms=0)


def test_branch_change_revokes_review_without_synthesizing_roster(tmp_path):
    from app.auth.accounts import production_notification_eligible, AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        ready(service, uid)
        service.update_profile(uid, branch="VIT_IT", cohort=2028)
        assert service.status(uid)["review_state"] == "UNREVIEWED"
        assert not production_notification_eligible(connection, uid)


@pytest.mark.parametrize("action", ["withdraw", "suspend", "revoke", "administrator"])
def test_authority_removal_blocks_previously_qualified_recipient(tmp_path, action):
    from app.auth.accounts import production_notification_eligible, AccountService, OperatorAuthorization
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        ready(service, uid)
        if action == "withdraw": service.set_consent(uid, False, "student-email-v1")
        else:
            service.review(uid, {"administrator": "APPROVED", "suspend": "SUSPENDED", "revoke": "REVOKED"}[action],
                authority=OperatorAuthorization("fictional-operator", "approval:fixture"),
                institution="VIT_PUNE", cohort=2028, branch="VIT_CE", evidence_reference="review:fixture",
                role="ADMINISTRATOR" if action == "administrator" else "SUBSCRIBER")
        assert not production_notification_eligible(connection, uid)


def test_replaced_destination_challenge_cannot_verify_previous_address(tmp_path):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        stale = service.request_destination(uid, "stale@example.invalid")
        current = service.request_destination(uid, "current@example.invalid")
        assert service.confirm_destination(stale) is None
        assert service.confirm_destination(current) == uid


def test_durable_sessions_reject_replay_after_logout_all_or_restart(tmp_path):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    token = create_session_token(uid, settings.SECRET_KEY)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        service.register_session(uid, token)
        assert service.session_valid(uid, token)
        service.terminate_sessions(uid)
    with db.users._get_conn() as connection:
        assert not AccountService(connection).session_valid(uid, token)


def test_production_status_is_authenticated_and_cannot_self_promote(tmp_path, monkeypatch):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    monkeypatch.setattr("app.database.repository.DB_PATH", db.db_path)
    application = create_app()
    client = TestClient(application, base_url="https://testserver")
    assert client.get("/api/v1/account").status_code == 401
    token = create_session_token(uid, settings.SECRET_KEY)
    with db.users._get_conn() as connection: AccountService(connection).register_session(uid, token)
    client.cookies.set("tpo_session", token)
    result = client.get("/api/v1/account")
    assert result.status_code == 200
    assert result.json()["role"] == "SUBSCRIBER"
    assert result.json()["delivery_eligible"] is False
    assert client.patch("/api/v1/account/profile", json={"role": "ADMINISTRATOR"}).status_code == 422
    assert client.post(f"/api/v1/admin/accounts/{uid}/review", json={"decision": "APPROVED", "evidence_reference": "fake"}).status_code == 403


def test_unsubscribe_withdraws_consent_and_invalidates_pending_destination_challenge(tmp_path):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        raw = service.request_destination(uid, "old@example.invalid")
    db.users.set_unsubscribed(uid)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        assert service.status(uid)["consent_state"] == "WITHDRAWN"
        assert service.confirm_destination(raw) is None


def test_closure_erases_pending_destination_challenges_but_preserves_audit(tmp_path):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        raw = service.request_destination(uid, "private@example.invalid")
        service.request_closure(uid)
        assert connection.execute("SELECT count(*) FROM destination_challenges WHERE user_id=?", (uid,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM account_audit WHERE user_id=?", (uid,)).fetchone()[0] >= 1
        assert service.status(uid)["notification_email"] is None
        assert not service.can_login(uid)


def test_approved_primary_email_replacement_requires_new_ownership_proof(tmp_path):
    from app.auth.accounts import AccountService, production_notification_eligible
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        ready(service, uid)
        token = create_session_token(uid, settings.SECRET_KEY)
        service.register_session(uid, token)
        connection.execute("UPDATE users SET email='replacement@vit.edu' WHERE id=?", (uid,))
        assert connection.execute("SELECT is_verified FROM users WHERE id=?", (uid,)).fetchone()[0] == 0
        assert not service.session_valid(uid, token)
        assert not production_notification_eligible(connection, uid)


def test_existing_token_pruning_job_also_removes_expired_private_account_credentials(tmp_path):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        service = AccountService(connection)
        service.register_session(uid, create_session_token(uid, settings.SECRET_KEY))
        service.request_destination(uid, "expired@example.invalid")
        connection.execute("UPDATE account_sessions SET expires_ms=0")
        connection.execute("UPDATE destination_challenges SET expires_ms=0")
    db.tokens.prune_stale_tokens()
    with db.users._get_conn() as connection:
        assert connection.execute("SELECT count(*) FROM account_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM destination_challenges").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM account_audit").fetchone()[0] >= 1


def test_magic_link_recovery_after_unsubscribe_requires_new_consent_and_durable_session(tmp_path, monkeypatch):
    from app.auth.accounts import AccountService
    from app.api.email_service import get_email_service
    from app.api.session import reset_session_revocations
    db, uid = account_database(tmp_path)
    db.users.set_unsubscribed(uid)
    monkeypatch.setattr("app.database.repository.DB_PATH", db.db_path)
    application = create_app()
    class Sink:
        raw = None
        def send_preference_link_email(self, destination, raw): self.raw = raw
    sink = Sink()
    application.dependency_overrides[get_email_service] = lambda: sink
    client = TestClient(application, base_url="https://testserver")
    assert client.post("/api/v1/preferences/request-link", json={"email": "fictional24@vit.edu"}).status_code == 200
    assert sink.raw
    assert client.post("/api/v1/preferences/confirm", json={"token": sink.raw}).status_code == 200
    status = client.get("/api/v1/account")
    assert status.status_code == 200
    assert status.json()["consent_state"] == "WITHDRAWN"
    assert status.json()["delivery_eligible"] is False
    replay = client.cookies.get("tpo_session")
    assert client.post("/api/v1/auth/logout").status_code == 200
    reset_session_revocations()  # Simulate another server process or restart.
    client.cookies.set("tpo_session", replay)
    assert client.get("/api/v1/preferences").status_code == 401


def test_signup_abuse_logs_do_not_disclose_email_identity(tmp_path, monkeypatch, caplog):
    db, uid = account_database(tmp_path)
    monkeypatch.setattr("app.database.repository.DB_PATH", db.db_path)
    client = TestClient(create_app(), base_url="https://testserver")
    address = "private-identity@example.invalid"
    payload = {"email": address, "branch_canonical": "VIT_CE"}
    for _ in range(5): assert client.post("/api/v1/auth/signup", json=payload).status_code == 201
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 429
    assert address not in caplog.text


def test_account_profile_audit_spam_is_bounded(tmp_path, monkeypatch):
    from app.auth.accounts import AccountService
    db, uid = account_database(tmp_path)
    monkeypatch.setattr("app.database.repository.DB_PATH", db.db_path)
    client = TestClient(create_app(), base_url="https://testserver")
    token = create_session_token(uid, settings.SECRET_KEY)
    with db.users._get_conn() as connection: AccountService(connection).register_session(uid, token)
    client.cookies.set("tpo_session", token)
    for _ in range(30): assert client.patch("/api/v1/account/profile", json={"branch_canonical": "VIT_CE"}).status_code == 200
    assert client.patch("/api/v1/account/profile", json={"branch_canonical": "VIT_CE"}).status_code == 429


@pytest.mark.parametrize("tamper", ["missing_trigger", "changed_trigger", "extra_trigger"])
def test_production_account_schema_verifier_rejects_unaudited_authority_changes(tmp_path, tamper):
    from app.database.account_schema import verify_account_schema
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        assert "account_profile_change" in verify_account_schema(connection)
        if tamper in ("missing_trigger", "changed_trigger"):
            connection.execute("DROP TRIGGER account_profile_change")
        if tamper == "changed_trigger":
            connection.execute("CREATE TRIGGER account_profile_change AFTER UPDATE ON users BEGIN SELECT 1; END")
        if tamper == "extra_trigger":
            connection.execute("CREATE TRIGGER malicious_authority AFTER UPDATE ON student_accounts BEGIN SELECT 1; END")
        with pytest.raises(sqlite3.IntegrityError): verify_account_schema(connection)


def test_open_existing_repository_never_runs_schema_migration(tmp_path, monkeypatch):
    from app.database.sqlite_repository import SQLiteUserRepository
    db, uid = account_database(tmp_path)
    def denied(*args, **kwargs): raise AssertionError("Implicit migration reached")
    monkeypatch.setattr("app.database.sqlite_repository.run_migrations", denied)
    users = SQLiteUserRepository.open_existing(db.db_path)
    assert users.get_by_id(uid)["is_verified"] == 1


def test_open_existing_accepts_exact_admitted_production_connection(tmp_path, monkeypatch):
    from app.database.sqlite_repository import SQLiteUserRepository
    from app.database.notification_migrator import ProductionDatabaseAuthority
    db, uid = account_database(tmp_path)
    authority = ProductionDatabaseAuthority.bind(db.db_path,
        expected_sha256=hashlib.sha256(__import__('pathlib').Path(db.db_path).read_bytes()).hexdigest(), maintenance=True)
    with authority.scope(), authority.connect(require_ready=False) as connection:
        def denied(*args, **kwargs): raise AssertionError("Independent connection reached")
        monkeypatch.setattr(SQLiteUserRepository, "_get_conn", denied)
        users = SQLiteUserRepository.open_existing(db.db_path, connection=connection)
        assert users.db_path == db.db_path


def test_suspended_account_cannot_request_fresh_signup_or_recovery_credentials(tmp_path, monkeypatch):
    from app.auth.accounts import AccountService, OperatorAuthorization
    from app.api.email_service import get_email_service
    db, uid = account_database(tmp_path)
    with db.users._get_conn() as connection:
        AccountService(connection).review(uid, "SUSPENDED", authority=OperatorAuthorization("operator", "approval:fixture"), evidence_reference="suspension:fixture")
    monkeypatch.setattr("app.database.repository.DB_PATH", db.db_path)
    application = create_app()
    class Sink:
        attempted = False
        def send_preference_link_email(self, *args): self.attempted = True
        def send_verification_email(self, *args): self.attempted = True
    sink = Sink()
    application.dependency_overrides[get_email_service] = lambda: sink
    client = TestClient(application, base_url="https://testserver")
    assert client.post("/api/v1/auth/signup", json={"email": "fictional24@vit.edu", "branch_canonical": "VIT_CE"}).status_code == 201
    assert client.post("/api/v1/preferences/request-link", json={"email": "fictional24@vit.edu"}).status_code == 200
    assert not sink.attempted
    with db.users._get_conn() as connection:
        assert connection.execute("SELECT count(*) FROM action_tokens WHERE user_id=? AND used_at IS NULL", (uid,)).fetchone()[0] == 0
