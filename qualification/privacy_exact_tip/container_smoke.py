"""Offline image probe. Never invokes the application lifecycle or a real poll."""
import importlib.metadata as md
import json
import os
import platform
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def main():
    assert platform.machine() == "aarch64"
    assert os.environ["DB_PATH"] == "/tmp/privacy-smoke.sqlite"
    print(json.dumps({"python": platform.python_version(), "playwright": md.version("playwright"),
                      "architecture": platform.machine()}))
    for line in Path("/app/requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==")
            assert md.version(name) == version
    # Re-run all 21 exact candidate regressions in the image's real interpreter,
    # including shared-console/Uvicorn sinks and fictional overlapping credentials.
    result = subprocess.run([sys.executable, "-I", "-B", "/qualification/run_suite.py", "/app", "privacy"],
                            capture_output=True, text=True, timeout=180)
    receipts = [line.removeprefix("QUALIFICATION_RECEIPT=") for line in result.stdout.splitlines()
                if line.startswith("QUALIFICATION_RECEIPT=")]
    print("CONTAINER_PRIVACY_PROCESS_EXIT=" + str(result.returncode))
    assert len(receipts) == 1
    privacy = json.loads(receipts[0])
    print("CONTAINER_PRIVACY_RECEIPT=" + json.dumps(privacy, sort_keys=True))
    assert result.returncode == 0 and privacy["qualified"] and privacy["passed"] == 21 and privacy["skipped"] == 0

    # Imports initialize safe logging and construct the API; no create_application,
    # app.main entry point, Uvicorn server, watcher, drain or lifespan is run.
    from app.main import get_uvicorn_config
    from app.api.app import create_app
    from app.config import settings
    from app.database.repository import DatabaseRepository
    print("CONTAINER_STAGE=sqlite_migration_and_idempotency")
    assert settings.DELIVERY_DRAIN_INTERVAL_SECONDS == 0
    db = Path(os.environ["DB_PATH"])
    repo = DatabaseRepository(str(db))
    repo.set_baseline_initialized()

    def snapshot():
        with closing(sqlite3.connect(str(db))) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            tables = {row[1] for row in schema if row[0] == "table"}
            required = {"companies", "system_state", "pending_notifications", "users", "user_preferences",
                        "action_tokens", "notification_deliveries", "notification_delivery_outcome_log"}
            assert required <= tables
            # The snapshot contains only disposable, newly-created synthetic state.
            return schema, "\n".join(conn.iterdump())

    before = snapshot()
    DatabaseRepository(str(db))
    assert snapshot() == before
    assert repo.is_baseline_initialized()
    print("CONTAINER_STAGE=api_and_uvicorn_configuration")
    application = create_app()
    config = get_uvicorn_config(application)
    assert config.log_config is None and not config.access_log and not config.server_header
    config.load()  # Initialize protocols/middleware only; no server or socket.
    assert config.loaded
    from fastapi.testclient import TestClient
    client = TestClient(application)  # No context manager: no ASGI lifecycle/scheduler.
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["database"] == "connected"
    assert health.json()["baseline_initialized"] is True
    details = client.get("/health/details")
    assert details.status_code == 200
    body = details.json()
    assert body["database"] == "connected" and body["deliveries"] == {}
    assert body["admin_pending"] == 0 and body["ambiguous_provider_acceptances"] == 0
    client.close()

    print("CONTAINER_STAGE=chromium_launch")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        page = browser.new_page()
        page.goto("about:blank")
        assert page.title() == ""
        print("CHROMIUM_VERSION=" + browser.version)
        page.close()
        browser.close()
    print('CONTAINER_SMOKE_RECEIPT={"qualified":true,"migration_integrity":true,"idempotent":true,'
          '"api_health":true,"uvicorn_config":true,"privacy_passed":21,"chromium_launched":true}')


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('CONTAINER_SMOKE_RECEIPT={"qualified":false,"category":"container_smoke_failed"}')
        raise SystemExit(1)
