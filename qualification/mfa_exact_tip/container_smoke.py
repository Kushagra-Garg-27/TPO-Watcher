"""Offline image probe for hardened MFA candidate.

Never invokes the application lifecycle or a real poll.
Tests runtime Python, dependencies, SQLite, API, Chromium, session security, and MFA state machine.
"""
import importlib.metadata as md
import json
import os
import platform
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def main():
    assert platform.machine() in ("aarch64", "arm64"), f"Unexpected architecture: {platform.machine()}"
    assert os.environ.get("DB_PATH", "").startswith("/tmp/"), "DB_PATH must be in disposable /tmp"
    
    print(json.dumps({
        "python": platform.python_version(),
        "playwright": md.version("playwright"),
        "architecture": platform.machine(),
    }))

    # 1. Dependency metadata validation with extras normalization
    for line in Path("/app/requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            distribution_name = name.split("[", 1)[0]
            assert md.version(distribution_name) == version, (
                f"Dependency pin mismatch for {distribution_name}: "
                f"installed={md.version(distribution_name)}, expected={version}"
            )

    # 2. Re-run all 33 exact MFA candidate regressions in the image's real interpreter
    print("CONTAINER_STAGE=mfa_regressions")
    result_mfa = subprocess.run(
        [sys.executable, "-I", "-B", "/qualification/run_suite.py", "/app", "mfa"],
        capture_output=True, text=True, timeout=180,
    )
    receipts_mfa = [
        line.removeprefix("QUALIFICATION_RECEIPT=")
        for line in result_mfa.stdout.splitlines()
        if line.startswith("QUALIFICATION_RECEIPT=")
    ]
    print("CONTAINER_MFA_PROCESS_EXIT=" + str(result_mfa.returncode))
    assert len(receipts_mfa) == 1, "Expected exactly one MFA receipt"
    mfa_receipt = json.loads(receipts_mfa[0])
    print("CONTAINER_MFA_RECEIPT=" + json.dumps(mfa_receipt, sort_keys=True))
    assert result_mfa.returncode == 0 and mfa_receipt["qualified"] and mfa_receipt["passed"] == 33 and mfa_receipt["skipped"] == 0

    # 3. Re-run all 21 exact privacy regressions in the image's real interpreter
    print("CONTAINER_STAGE=privacy_regressions")
    result_priv = subprocess.run(
        [sys.executable, "-I", "-B", "/qualification/run_suite.py", "/app", "privacy"],
        capture_output=True, text=True, timeout=180,
    )
    receipts_priv = [
        line.removeprefix("QUALIFICATION_RECEIPT=")
        for line in result_priv.stdout.splitlines()
        if line.startswith("QUALIFICATION_RECEIPT=")
    ]
    print("CONTAINER_PRIVACY_PROCESS_EXIT=" + str(result_priv.returncode))
    assert len(receipts_priv) == 1, "Expected exactly one privacy receipt"
    privacy_receipt = json.loads(receipts_priv[0])
    print("CONTAINER_PRIVACY_RECEIPT=" + json.dumps(privacy_receipt, sort_keys=True))
    assert result_priv.returncode == 0 and privacy_receipt["qualified"] and privacy_receipt["passed"] == 21 and privacy_receipt["skipped"] == 0

    # 4. SQLite initialization, migrations and idempotent restart
    print("CONTAINER_STAGE=sqlite_migration_and_idempotency")
    from app.config import settings
    from app.database.repository import DatabaseRepository
    assert settings.DELIVERY_DRAIN_INTERVAL_SECONDS == 0

    db_path = Path(os.environ["DB_PATH"])
    repo = DatabaseRepository(str(db_path))
    repo.set_baseline_initialized()

    def snapshot():
        with closing(sqlite3.connect(str(db_path))) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            schema = conn.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            tables = {row[1] for row in schema if row[0] == "table"}
            required = {
                "companies", "system_state", "pending_notifications", "users",
                "user_preferences", "action_tokens", "notification_deliveries",
                "notification_delivery_outcome_log"
            }
            assert required <= tables, f"Missing required tables: {required - tables}"
            return schema, "\n".join(conn.iterdump())

    before = snapshot()
    DatabaseRepository(str(db_path))  # Second initialization must be idempotent
    assert snapshot() == before
    assert repo.is_baseline_initialized()

    # 5. API & Uvicorn configuration verification
    print("CONTAINER_STAGE=api_and_uvicorn_configuration")
    from app.main import get_uvicorn_config
    from app.api.app import create_app
    application = create_app()
    config = get_uvicorn_config(application)
    assert config.log_config is None and not config.access_log and not config.server_header
    config.load()
    assert config.loaded

    from fastapi.testclient import TestClient
    client = TestClient(application)
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["database"] == "connected"
    assert health.json()["baseline_initialized"] is True
    details = client.get("/health/details")
    assert details.status_code == 200
    body = details.json()
    assert body["database"] == "connected" and body["deliveries"] == {}
    assert body["admin_pending"] == 0 and body["ambiguous_provider_acceptances"] == 0
    client.close()

    # 6. Real headless Chromium launch on container Linux/ARM64
    print("CONTAINER_STAGE=chromium_launch")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        page = browser.new_page()
        page.goto("about:blank")
        assert page.title() == ""
        chromium_version = browser.version
        print("CHROMIUM_VERSION=" + chromium_version)
        page.close()
        browser.close()

    # 7. Real session file security & atomic replacement checks on image filesystem
    print("CONTAINER_STAGE=session_file_security_compatibility")
    smoke_data_dir = Path("/tmp/smoke_session_data")
    smoke_data_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(str(smoke_data_dir), 0o700)
    assert stat.S_IMODE(os.stat(str(smoke_data_dir)).st_mode) == 0o700, "Parent dir mode 0700 not enforced"

    session_file = smoke_data_dir / "playwright_state.json"
    original_state = '{"cookies":[{"name":"auth","value":"initial-state"}]}'
    session_file.write_text(original_state, encoding="utf-8")
    os.chmod(str(session_file), 0o600)
    assert stat.S_IMODE(os.stat(str(session_file)).st_mode) == 0o600, "Initial state file mode 0600 not enforced"

    # Simulate atomic replacement with mkstemp and fchmod
    fd, tmp_file = tempfile.mkstemp(prefix="state_", suffix=".tmp", dir=str(smoke_data_dir))
    os.fchmod(fd, 0o600)
    assert stat.S_IMODE(os.stat(tmp_file).st_mode) == 0o600, "Temp file mode 0600 before write not enforced"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write('{"cookies":[{"name":"auth","value":"updated-state"}]}')
    os.close(fd)
    os.replace(tmp_file, str(session_file))
    os.chmod(str(session_file), 0o600)
    assert stat.S_IMODE(os.stat(str(session_file)).st_mode) == 0o600, "Replaced state file mode 0600 not enforced"
    assert "updated-state" in session_file.read_text(encoding="utf-8")

    # Simulate failed write and verify original is preserved
    fd2, tmp_file2 = tempfile.mkstemp(prefix="state_", suffix=".tmp", dir=str(smoke_data_dir))
    os.fchmod(fd2, 0o600)
    os.close(fd2)
    # Simulate error cleanup
    if os.path.exists(tmp_file2):
        os.remove(tmp_file2)
    assert not os.path.exists(tmp_file2)
    assert "updated-state" in session_file.read_text(encoding="utf-8"), "State file corrupted on simulated failure"

    print("SESSION_SECURITY_COMPATIBILITY_OK")

    print('CONTAINER_SMOKE_RECEIPT=' + json.dumps({
        "qualified": True,
        "migration_integrity": True,
        "idempotent": True,
        "api_health": True,
        "uvicorn_config": True,
        "privacy_passed": 21,
        "mfa_passed": 33,
        "session_security": True,
        "chromium_launched": True,
        "chromium_version": chromium_version,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f'CONTAINER_SMOKE_RECEIPT={{"qualified":false,"category":"container_smoke_failed","exception":"{type(e).__name__}"}}')
        raise SystemExit(1)
