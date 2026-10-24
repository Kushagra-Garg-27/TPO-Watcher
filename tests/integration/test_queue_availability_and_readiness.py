"""F10/F12/F04: queue independence, truthful readiness detail, ambiguity surfacing.

Fictional feeds/transports on disposable SQLite only.
"""
import asyncio
import inspect
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from app.database.repository import DatabaseRepository
from app.monitoring import watcher as watcher_module
from app.monitoring.watcher import PlacementWatcher

from test_cummins_pipeline_stress import (
    audit_repo, add_user, company, delivery_row, queue_one,
    FakeNotifier, real_worker, safe_watcher,
)


@pytest.mark.asyncio
async def test_f10_queue_drains_while_source_is_unavailable(audit_repo, monkeypatch):
    queue_one(audit_repo)
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company()]])

    async def failing_fetch(self=None, *args, **kwargs):
        raise RuntimeError("fictional source outage")

    monkeypatch.setattr(watcher_module, "TPOClient", failing_fetch)
    assert await watcher.check_once() is False
    # The independent drain tick still delivers the queued obligation.
    assert await watcher.drain_once() == 1
    assert notifier.student_calls == 1
    assert delivery_row(audit_repo)["status"] == "SENT"


@pytest.mark.asyncio
async def test_f10_drain_tick_survives_worker_failure(audit_repo, monkeypatch):
    queue_one(audit_repo)
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company()]])

    def exploding_drain():
        raise RuntimeError("fictional drain crash")

    monkeypatch.setattr(watcher.delivery_worker, "process_batch_once", exploding_drain)
    assert await watcher.drain_once() == 0
    monkeypatch.undo()
    assert await watcher.drain_once() == 1


def test_f10_main_starts_independent_delivery_loop():
    import app.main as main_module
    source = inspect.getsource(main_module)
    assert "run_delivery_loop" in source
    assert "run_pruning_loop" in source


@pytest.mark.asyncio
async def test_f04_unacknowledged_acceptance_is_surfaced(audit_repo, monkeypatch):
    queue_one(audit_repo)
    notifier = FakeNotifier()
    native = audit_repo.deliveries.mark_sent
    calls = 0

    def lose_first_ack(delivery_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("fictional post-acceptance acknowledgment failure")
        return native(delivery_id)

    monkeypatch.setattr(audit_repo.deliveries, "mark_sent", lose_first_ack)
    worker = real_worker(audit_repo, notifier)
    assert await worker.process_batch_once() == 0
    with closing(audit_repo._get_conn()) as conn:
        markers = conn.execute(
            "SELECT key FROM system_state"
            " WHERE key LIKE 'notification:v1:ambiguous_acceptance:%'").fetchall()
    assert len(markers) == 1


def _health_details_for(monkeypatch, tmp_path, seed):
    from fastapi.testclient import TestClient
    from app.api.app import create_app
    from app.health import server as health_server

    db_path = str(tmp_path / "health-details.sqlite")
    repo = DatabaseRepository(db_path)
    seed(repo)
    monkeypatch.setattr(health_server, "DB_PATH", db_path)
    return TestClient(create_app()).get("/health/details")


@pytest.mark.asyncio
async def test_f12_health_details_reports_fresh_success_and_zero_backlog(monkeypatch, tmp_path):
    def seed(repo):
        repo.set_baseline_initialized()
        repo.set_system_state("last_successful_watcher_run",
                              datetime.now(timezone.utc).isoformat())
        repo.set_system_state("consecutive_watcher_failures", "0")
        repo.set_system_state("last_scheduled_check_started",
                              datetime.now(timezone.utc).isoformat())
        repo.set_system_state("last_admin_drain_complete", "true")
        repo.set_system_state("last_observation_company_count", "8")
    response = _health_details_for(monkeypatch, tmp_path, seed)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["watcher_fresh"] is True
    assert body["consecutive_watcher_failures"] == 0
    assert body["missed_scheduled_start"] is False
    assert body["last_observation_company_count"] == 8
    assert body["admin_pending"] == 0
    assert body["ambiguous_provider_acceptances"] == 0
    assert body["deliveries"].get("PENDING", 0) == 0


def test_f12_health_details_reports_stale_degraded_and_backlog(monkeypatch, tmp_path):
    def seed(repo):
        repo.set_baseline_initialized()
        repo.set_system_state("last_successful_watcher_run",
                              (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat())
        repo.set_system_state("consecutive_watcher_failures", "5")
        repo.set_system_state("last_scheduled_check_started",
                              (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat())
        add_user(repo)
        repo.upsert_company(company())
        from app.subscribers.fanout import FanoutEngine
        FanoutEngine(repo.users, repo.deliveries).dispatch_opportunity(company(), "NEW")
        repo.add_pending_notification(company().id, "NEW", [])
    response = _health_details_for(monkeypatch, tmp_path, seed)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["watcher_fresh"] is False
    assert body["consecutive_watcher_failures"] == 5
    # Exact per-schedule evaluation (00:00/10:00/17:00 IST + 30m slack):
    # a 20h-old start ledger has missed at least one due instant…
    assert body["missed_scheduled_start"] is True
    assert body["deliveries"]["PENDING"] == 1
    assert body["admin_pending"] == 1


def test_f12_health_details_without_ledger_is_unknown(monkeypatch, tmp_path):
    response = _health_details_for(monkeypatch, tmp_path, lambda repo: None)
    assert response.status_code == 200
    body = response.json()
    assert body["missed_scheduled_start"] == "unknown"
    assert body["status"] == "degraded"


def test_f12_missed_start_is_precise_not_coarse(monkeypatch, tmp_path):
    from app.health.server import _missed_scheduled_start
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 25, 16, 0, 0, tzinfo=tz)  # between 10:00 and 17:00
    started_recent = datetime(2026, 10, 25, 10, 5, 0, tzinfo=tz)
    assert _missed_scheduled_start(started_recent, now) is False  # 17:00 not yet due+slack
    started_early = datetime(2026, 10, 25, 9, 0, 0, tzinfo=tz)
    assert _missed_scheduled_start(started_early, now) is True  # 10:00 due passed by >30m
    # 6h-old ledger can never be missed under any start time on this schedule
    # (largest gap 10h) — the coarse bound and exact method agree here.
    started_6h = datetime(2026, 10, 25, 10, 0, 0, tzinfo=tz)
    assert _missed_scheduled_start(started_6h, datetime(2026, 10, 25, 16, 0, 0, tzinfo=tz)) is False
