"""Phase-3/4 integration-safety regressions: mid-observation interruption,
concurrent-writer convergence, drain gating and transport cancellation."""
import asyncio
import threading
import time

import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

import pytest
from app.database.repository import DatabaseRepository
from app.monitoring.detector import CompanyDetector

from test_cummins_pipeline_stress import (
    audit_repo, add_user, company, delivery_row, expire_claim, queue_one,
    FakeNotifier, real_worker, safe_watcher, SYNTHETIC_ID,
)

CONTROL = "SYNTHETIC-CONTROL"
OTHER = "SYNTHETIC-COMPANY-002"


def test_mid_observation_interruption_converges_on_replay(audit_repo, monkeypatch):
    """A crash while processing company 2 of 3 leaves only self-consistent
    committed units; an identical replay creates each missing event exactly
    once and no duplicates."""
    audit_repo.set_baseline_initialized()
    detector = CompanyDetector(audit_repo)
    feed = [company(id=CONTROL, company="Fictional Control"),
            company(id=OTHER, company="Fictional Second Employer"),
            company()]

    processed = {"n": 0}
    native = DatabaseRepository._upsert_company_conn

    def crash_on_second(self, conn, record, *args, **kwargs):
        existed = native(self, conn, record, *args, **kwargs)
        processed["n"] += 1
        if processed["n"] == 2:
            raise RuntimeError("fictional interruption mid-observation")
        return existed

    monkeypatch.setattr(DatabaseRepository, "_upsert_company_conn", crash_on_second)
    with pytest.raises(RuntimeError):
        detector.process_fetched_companies(feed)
    monkeypatch.undo()

    # Partial state: first company committed with its obligation, nothing else.
    assert audit_repo.get_company(CONTROL) is not None
    assert audit_repo.get_company(OTHER) is None
    assert [n["company_id"] for n in audit_repo.get_pending_notifications()] == [CONTROL]

    result = detector.process_fetched_companies(feed)
    assert len(result.new_companies) == 2  # OTHER + SYNTHETIC_ID
    pending = sorted(n["company_id"] for n in audit_repo.get_pending_notifications())
    assert pending == sorted([CONTROL, OTHER, SYNTHETIC_ID])

    # Further identical replays add nothing.
    detector.process_fetched_companies(feed)
    assert len(audit_repo.get_pending_notifications()) == 3


def test_concurrent_new_introductions_converge_to_one_obligation(audit_repo):
    """Two serialized apply units racing to introduce the same company
    produce exactly one NEW obligation (in-transaction existence guard)."""
    record = company()
    audit_repo.apply_company_event(record, is_new=True, notification_type="NEW", changes=[])
    # The second writer believed the company was new (pre-read raced) but its
    # serialized transaction observes the row: no second NEW obligation.
    audit_repo.apply_company_event(record, is_new=True, notification_type="NEW", changes=[])
    assert len(audit_repo.get_pending_notifications()) == 1


def test_identical_pending_update_obligation_is_not_duplicated(audit_repo):
    audit_repo.set_baseline_initialized()
    audit_repo.apply_company_event(company(), is_new=False)
    snapshot = company(regEndtime="18:00").model_dump_json()
    for _ in range(2):
        audit_repo.apply_company_event(
            company(regEndtime="18:00"), notification_type="UPDATE",
            changes=["Registration field regEndtime changed."], event_snapshot=snapshot)
    updates = [n for n in audit_repo.get_pending_notifications()
               if n["notification_type"] == "UPDATE"]
    assert len(updates) == 1
    # Distinct events still accumulate independently (A→B→C semantics).
    audit_repo.apply_company_event(
        company(regEndtime="20:00"), notification_type="UPDATE",
        changes=["Registration field regEndtime changed."],
        event_snapshot=company(regEndtime="20:00").model_dump_json())
    assert len([n for n in audit_repo.get_pending_notifications()
                if n["notification_type"] == "UPDATE"]) == 2


@pytest.mark.asyncio
async def test_independent_drain_is_default_off(audit_repo, monkeypatch):
    """Safe deployment gate: with no interval configured the loop exits
    immediately and never dispatches on its own."""
    queue_one(audit_repo)
    watcher, _ = safe_watcher(audit_repo, monkeypatch, FakeNotifier(), [[company()]])
    from app.config import settings
    monkeypatch.setattr(settings, "DELIVERY_DRAIN_INTERVAL_SECONDS", 0.0)
    ticks = []
    monkeypatch.setattr(watcher, "drain_once", lambda: ticks.append(1) or asyncio.sleep(0))
    await asyncio.wait_for(watcher.run_delivery_loop(), timeout=2)
    assert ticks == []
    # Explicit opt-in enables the bounded loop; cancellation stops it cleanly.
    monkeypatch.setattr(settings, "DELIVERY_DRAIN_INTERVAL_SECONDS", 0.05)
    loop_task = asyncio.create_task(watcher.run_delivery_loop())
    await asyncio.sleep(0.16)
    loop_task.cancel()
    # The loop stops gracefully on cancellation (its documented shutdown
    # path) rather than propagating into the supervisor.
    await asyncio.wait_for(loop_task, timeout=2)
    assert len(ticks) >= 2


@pytest.mark.asyncio
async def test_cancellation_during_offloop_transport_leaves_recoverable_claim(audit_repo):
    """Cancelling the worker while SMTP runs in a thread cannot acknowledge;
    the claim recovers through lease expiry per the at-least-once contract."""
    queue_one(audit_repo)

    class SlowNotifier(FakeNotifier):
        def _send_email_to(self, *args, **kwargs):
            time.sleep(0.3)
            return True

    worker = real_worker(audit_repo, SlowNotifier())
    task = asyncio.create_task(worker.process_batch_once())
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = delivery_row(audit_repo)
    assert row["status"] == "PROCESSING"
    expire_claim(audit_repo, row["id"])
    assert audit_repo.deliveries.recover_stale_leases() == 1
    notifier = FakeNotifier()
    assert await real_worker(audit_repo, notifier).process_batch_once() == 1
    assert delivery_row(audit_repo)["status"] == "SENT"
