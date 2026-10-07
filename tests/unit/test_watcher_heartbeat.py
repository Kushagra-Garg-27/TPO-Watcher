import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.api.app import app
from app.database.repository import DatabaseRepository
from app.monitoring.watcher import PlacementWatcher

@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_hb.sqlite")
    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()
    monkeypatch.setattr("app.database.repository.DB_PATH", db_path)
    monkeypatch.setattr("app.health.server.DB_PATH", db_path)
    client = TestClient(app)
    watcher = PlacementWatcher(db=repo)
    return watcher, repo, client

# 1. Successful scheduled run updates heartbeat
def test_successful_scheduled_run_updates_heartbeat(test_setup):
    watcher, repo, _ = test_setup
    assert repo.get_system_state("last_successful_watcher_run") is None

    watcher.record_heartbeat_success()

    ts = repo.get_system_state("last_successful_watcher_run")
    assert ts is not None
    assert repo.get_system_state("consecutive_watcher_failures") == "0"

# 2. Failed run does not falsely update success heartbeat
def test_failed_run_does_not_falsely_update_success_heartbeat(test_setup):
    watcher, repo, _ = test_setup
    initial_success = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    repo.set_system_state("last_successful_watcher_run", initial_success)

    watcher.record_heartbeat_failure()

    # Success timestamp must remain untouched
    assert repo.get_system_state("last_successful_watcher_run") == initial_success
    # Failure indicators recorded
    assert repo.get_system_state("last_failed_watcher_run") is not None
    assert repo.get_system_state("consecutive_watcher_failures") == "1"

# 3. Timestamp stored in UTC
def test_timestamp_stored_in_utc(test_setup):
    watcher, repo, _ = test_setup
    watcher.record_heartbeat_success()
    ts_str = repo.get_system_state("last_successful_watcher_run")
    dt = datetime.fromisoformat(ts_str)
    assert dt.tzinfo == timezone.utc

# 4. Fresh heartbeat -> ok (<= 11h)
def test_fresh_heartbeat_returns_ok(test_setup):
    _, repo, client = test_setup
    fresh_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    repo.set_system_state("last_successful_watcher_run", fresh_time)

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["watcher"] == "ok"

# 5. Old heartbeat -> stale (> 11h)
def test_old_heartbeat_returns_stale(test_setup):
    _, repo, client = test_setup
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=11, minutes=5)).isoformat()
    repo.set_system_state("last_successful_watcher_run", stale_time)

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["watcher"] == "stale"

# 6. Missing heartbeat -> unknown
def test_missing_heartbeat_returns_unknown(test_setup):
    _, repo, client = test_setup
    # system_state has no last_successful_watcher_run
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["watcher"] == "unknown"

# 7. HTTP status unchanged for all three heartbeat states
@pytest.mark.parametrize("hours_offset,expected_status", [
    (2, "ok"),
    (14, "stale"),
    (None, "unknown"),
])
def test_http_status_unchanged_for_all_three_heartbeat_states(test_setup, hours_offset, expected_status):
    _, repo, client = test_setup
    if hours_offset is not None:
        t = (datetime.now(timezone.utc) - timedelta(hours=hours_offset)).isoformat()
        repo.set_system_state("last_successful_watcher_run", t)

    res = client.get("/health")
    # Must ALWAYS be 200 OK regardless of ok / stale / unknown
    assert res.status_code == 200
    assert res.json()["watcher"] == expected_status

# 8. All existing /health fields preserved
def test_all_existing_health_fields_preserved(test_setup):
    _, repo, client = test_setup
    repo.set_system_state("last_successful_watcher_run", datetime.now(timezone.utc).isoformat())

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["baseline_initialized"] is True
    assert isinstance(data["deliveries"], dict)
    assert data["watcher"] == "ok"
    # Ensure no internal/secret details leaked
    assert "timestamp" not in data
    assert "failures" not in data
