"""Exercise guards through intercepted calls, never actual dangerous operations."""
import asyncio
import os
from pathlib import Path
import smtplib
import socket
import sqlite3
import subprocess
import urllib.request

import httpx
import pytest
import isolation_bootstrap as isolation


# These checks run during collection, before ordinary fixtures. No application
# modules are imported by this regression module's top level.
assert isolation.disposable_path(os.environ["DB_PATH"])
assert isolation.disposable_path(os.environ["STATE_FILE"])


@pytest.mark.parametrize("transport", [smtplib.SMTP, smtplib.SMTP_SSL])
def test_smtp_is_denied_without_network(transport):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        transport("smtp.invalid", 587)


@pytest.mark.parametrize("url", ["http://external.invalid/", "https://external.invalid/"])
def test_unmocked_httpx_transport_is_denied(url):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        httpx.get(url)


@pytest.mark.asyncio
async def test_unmocked_async_http_transport_is_denied():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        async with httpx.AsyncClient() as client:
            await client.get("https://external.invalid/")


def test_unmocked_urllib_is_denied_before_dns():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        urllib.request.urlopen("https://external.invalid/")


@pytest.mark.parametrize("address", [("192.0.2.1", 443), ("127.0.0.1", 8000)])
def test_external_and_arbitrary_localhost_socket_denied(address):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        with socket.socket() as sock:
            sock.connect(address)


def test_external_dns_is_denied():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        socket.getaddrinfo("external.invalid", 443)


@pytest.mark.parametrize("database", [
    "watcher.sqlite",
    str(isolation.REPOSITORY / "watcher.sqlite"),
    str(isolation.REPOSITORY / "never-open-other.db"),
    "file:" + (isolation.REPOSITORY / "never-open.sqlite").as_posix() + "?mode=ro",
])
def test_persistent_database_is_denied_before_open(database):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        sqlite3.connect(database)


def test_sqlite_attach_cannot_escape_temp_root(tmp_path):
    with sqlite3.connect(str(tmp_path / "synthetic.sqlite")) as conn:
        with isolation.expected_violation(), pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE ? AS forbidden", (str(isolation.REPOSITORY / "never-open.sqlite"),))


@pytest.mark.parametrize("relative", [".env", ".env.production", "playwright_state.json", ".auth/never-read.json"])
def test_sensitive_file_reads_are_denied(relative):
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        (isolation.REPOSITORY / relative).read_bytes()


def test_persistent_file_mutations_are_denied():
    candidate = isolation.REPOSITORY / "must-not-create-or-remove.guard"
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        candidate.write_text("blocked")
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        candidate.unlink()


def test_cli_or_browser_process_is_denied():
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        subprocess.run(["must-not-execute"], check=True)


def test_healthcheck_swallowed_transport_failure_still_records_violation():
    from app.monitoring.checkin import send_healthcheck_ping
    with isolation.expected_violation():
        assert send_healthcheck_ping("https://external.invalid/", success=True) is False
        with pytest.raises(isolation.IsolationViolation, match="intercepted unsafe operation"):
            isolation.assert_no_violations()


@pytest.mark.asyncio
async def test_real_playwright_launch_is_denied_before_browser_start():
    from playwright.async_api import async_playwright
    with isolation.expected_violation(), pytest.raises(isolation.IsolationViolation):
        await async_playwright().start()


def test_synthetic_environment_and_bound_default_paths():
    from app.config import settings
    from app.database.repository import DB_PATH, DatabaseRepository
    from app.auth.manager import STATE_FILE
    assert settings.TPO_USERNAME == ""
    assert settings.TPO_PASSWORD == ""
    assert settings.SMTP_PASSWORD == ""
    assert settings.HEALTHCHECK_WATCHER_URL is None
    assert settings.SECRET_KEY == "synthetic-test-key-not-for-deployment"
    assert isolation.disposable_path(DB_PATH)
    assert isolation.disposable_path(STATE_FILE)
    assert isolation.disposable_path(DatabaseRepository.__init__.__defaults__[0])


def test_import_time_database_path_is_disposable():
    # A compiled fictional module simulates collection-time DB initialization.
    namespace = {}
    exec(compile("import os, sqlite3\nconnection = sqlite3.connect(os.environ['DB_PATH'])\nconnection.close()", "synthetic_collection.py", "exec"), namespace)


def test_synthetic_sqlite_repository_and_fixtures_are_allowed(tmp_path):
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(str(tmp_path / "synthetic.sqlite"))
    user_id = repo.users.create_user("fiction.student24@vit.edu", 2028, "VIT_CE", pref_ppo=False)
    repo.users.set_verified(user_id)
    assert repo.users.get_by_id(user_id)["is_verified"] == 1
    assert repo.users.get_preferences(user_id)["pref_ppo"] == 0


def test_in_memory_sqlite_is_allowed():
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("SELECT 42").fetchone()[0] == 42


def test_in_process_fastapi_testclient_is_allowed():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    application = FastAPI()
    @application.get("/synthetic")
    def synthetic():
        return {"synthetic": True}
    with TestClient(application) as client:
        assert client.get("/synthetic").json() == {"synthetic": True}


def test_explicit_synthetic_email_transport_is_allowed():
    from app.api.email_service import EmailService
    class SyntheticEmailTransport:
        def __init__(self):
            self.messages = []
        def _send_email_to(self, destination, subject, html):
            self.messages.append((destination, subject))
            return True
    transport = SyntheticEmailTransport()
    assert EmailService(transport).send_verification_email("fiction.student24@vit.edu", "synthetic-capability-not-live") is True
    assert transport.messages[0][0] == "fiction.student24@vit.edu"


def test_explicit_httpx_mock_transport_is_allowed():
    def synthetic(request):
        return httpx.Response(200, json={"synthetic": True})
    with httpx.Client(transport=httpx.MockTransport(synthetic)) as client:
        assert client.get("https://external.invalid/").json() == {"synthetic": True}


def test_dotenv_settings_source_does_not_read_files(tmp_path):
    from pydantic_settings import BaseSettings, SettingsConfigDict
    candidate = tmp_path / ".env"
    candidate.write_text("SYNTHETIC_VALUE=should-not-be-loaded\n")
    class SyntheticSettings(BaseSettings):
        SYNTHETIC_VALUE: str = "default"
        model_config = SettingsConfigDict(env_file=candidate)
    assert SyntheticSettings().SYNTHETIC_VALUE == "default"


def test_temp_files_can_be_changed_and_deleted(tmp_path):
    source = tmp_path / "synthetic.txt"
    destination = tmp_path / "renamed.txt"
    source.write_text("fictional")
    source.rename(destination)
    assert destination.read_text() == "fictional"
    destination.unlink()
    assert not destination.exists()
