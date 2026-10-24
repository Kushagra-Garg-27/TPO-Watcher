"""F05/F06/F07 event durability contracts on guarded disposable SQLite.

F05: the company transition and its durable notification obligation commit
atomically. F06: a terminal FAILED UPDATE accepts a later distinct event while
preserving terminal history. F07: deferred obligations carry and render their
own event-time snapshots with approved semantic event identity.
"""
import json
from contextlib import closing

import pytest

from app.database.repository import DatabaseRepository
from app.monitoring.detector import CompanyDetector
from app.subscribers.fanout import FanoutEngine
from app.tpo.models import CompanyRecord

from test_cummins_pipeline_stress import (
    audit_repo, add_user, company, delivery_row, expire_claim,
    FakeNotifier, real_worker, safe_watcher, SYNTHETIC_ID,
)


@pytest.fixture
def seeded(audit_repo):
    audit_repo.set_baseline_initialized()
    return audit_repo


def crash_between_company_and_obligation(monkeypatch, once=True):
    """Simulate an interruption after the company write, before the obligation."""
    native = DatabaseRepository._upsert_company_conn
    state = {"fired": False}

    def crash_after_upsert(self, conn, record, *args, **kwargs):
        native(self, conn, record, *args, **kwargs)
        if not state["fired"]:
            state["fired"] = True
            raise RuntimeError("fictional interruption between company write and obligation write")

    monkeypatch.setattr(DatabaseRepository, "_upsert_company_conn", crash_after_upsert)
    return state


def test_f05_new_company_and_obligation_commit_atomically(seeded, monkeypatch):
    detector = CompanyDetector(seeded)
    crash_between_company_and_obligation(monkeypatch)
    with pytest.raises(RuntimeError):
        detector.process_fetched_companies([company()])
    monkeypatch.undo()

    # Neither the company nor its NEW obligation survived the rolled-back unit.
    assert seeded.get_company(SYNTHETIC_ID) is None
    assert seeded.get_pending_notifications() == []

    # Restart with the identical observation recreates exactly one NEW event.
    result = detector.process_fetched_companies([company()])
    assert len(result.new_companies) == 1
    pending = seeded.get_pending_notifications()
    assert len(pending) == 1 and pending[0]["notification_type"] == "NEW"
    assert seeded.get_company(SYNTHETIC_ID) is not None
    # An unchanged replay creates no second obligation.
    detector.process_fetched_companies([company()])
    assert len(seeded.get_pending_notifications()) == 1


def test_f05_update_company_and_obligation_commit_atomically(seeded, monkeypatch):
    seeded.upsert_company(company())
    detector = CompanyDetector(seeded)
    crash_between_company_and_obligation(monkeypatch)
    with pytest.raises(RuntimeError):
        detector.process_fetched_companies([company(regEnddate="15-Oct-2026")])
    monkeypatch.undo()

    stored = seeded.get_company(SYNTHETIC_ID)
    assert stored["registration_end"] == "14-Oct-2026"
    assert seeded.get_pending_notifications() == []

    result = detector.process_fetched_companies([company(regEnddate="15-Oct-2026")])
    assert len(result.updated_companies) == 1
    pending = seeded.get_pending_notifications()
    assert len(pending) == 1 and pending[0]["notification_type"] == "UPDATE"
    assert seeded.get_company(SYNTHETIC_ID)["registration_end"] == "15-Oct-2026"


def test_f05_baseline_ingest_records_companies_without_obligations(audit_repo):
    detector = CompanyDetector(audit_repo)
    result = detector.process_fetched_companies([company()])
    assert result.new_companies == []
    assert audit_repo.get_pending_notifications() == []
    assert audit_repo.get_company(SYNTHETIC_ID) is not None
    assert audit_repo.is_baseline_initialized()


def test_f06_failed_update_does_not_block_distinct_later_update(seeded):
    add_user(seeded)
    seeded.upsert_company(company())
    fanout = FanoutEngine(seeded.users, seeded.deliveries)

    assert fanout.dispatch_opportunity(company(regEndtime="09:00"), "UPDATE") == 1
    for _ in range(5):
        claimed = seeded.deliveries.claim_pending_batch(batch_size=1)
        assert len(claimed) == 1
        expire_claim(seeded, claimed[0]["id"])
        assert seeded.deliveries.recover_stale_leases() == 1
    assert delivery_row(seeded)["status"] == "FAILED"

    # Same-event replay stays terminal; no blanket FAILED requeue occurs.
    assert fanout.dispatch_opportunity(company(regEndtime="09:00"), "UPDATE") == 0
    assert delivery_row(seeded)["status"] == "FAILED"

    # A genuinely distinct later deadline update is independently queueable.
    assert fanout.dispatch_opportunity(company(regEndtime="18:00"), "UPDATE") == 1
    row = delivery_row(seeded)
    assert row["status"] == "PENDING" and row["attempt_count"] == 0

    # The exhausted event's terminal outcome remains auditable.
    with closing(seeded._get_conn()) as conn:
        logged = conn.execute(
            "SELECT prior_status, event_snapshot, notification_type"
            " FROM notification_delivery_outcome_log").fetchall()
    assert len(logged) == 1
    assert logged[0]["prior_status"] == "FAILED"
    assert logged[0]["notification_type"] == "UPDATE"
    assert json.loads(logged[0]["event_snapshot"])["regEndtime"] == "09:00"

    # NEW lifetime deduplication is unaffected by the UPDATE history.
    assert fanout.dispatch_opportunity(company(regEndtime="20:00"), "NEW") == 1
    assert fanout.dispatch_opportunity(company(regEndtime="21:00"), "NEW") == 0


@pytest.mark.asyncio
async def test_f07_deferred_obligations_render_their_own_event_snapshots(seeded, monkeypatch):
    add_user(seeded)
    seeded.upsert_company(company())
    detector = CompanyDetector(seeded)
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(seeded, monkeypatch, notifier, [[company()]])

    # Event A (09:00) is fully delivered first.
    detector.process_fetched_companies([company(regEndtime="09:00")])
    await watcher._process_notifications()
    assert await watcher.delivery_worker.process_batch_once() == 1

    # Events B (18:00) then C (20:00) arrive before B drains; the stored
    # company row now carries C. Each obligation keeps its own snapshot.
    detector.process_fetched_companies([company(regEndtime="18:00")])
    detector.process_fetched_companies([company(regEndtime="20:00")])
    assert len(seeded.get_pending_notifications()) == 2

    await watcher._process_notifications()
    # B requeues with B's snapshot even though the current row holds C, and
    # C defers behind the in-flight distinct event rather than collapsing.
    assert current_snapshot(seeded)["regEndtime"] == "18:00"
    assert await watcher.delivery_worker.process_batch_once() == 1

    await watcher._process_notifications()
    assert current_snapshot(seeded)["regEndtime"] == "20:00"
    assert await watcher.delivery_worker.process_batch_once() == 1

    assert notifier.student_calls == 3
    assert not seeded.get_pending_notifications()


@pytest.mark.asyncio
async def test_f07_legacy_obligation_without_snapshot_falls_back_to_current_row(seeded, monkeypatch):
    seeded.upsert_company(company())
    seeded.add_pending_notification(SYNTHETIC_ID, "UPDATE", [])  # legacy row: NULL snapshot
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(seeded, monkeypatch, notifier, [[company()]])
    await watcher._process_notifications()
    assert notifier.admin_calls == 1
    assert not seeded.get_pending_notifications()


def current_snapshot(repo):
    with closing(repo._get_conn()) as conn:
        row = conn.execute(
            "SELECT raw_data_snapshot FROM notification_deliveries"
            " WHERE notification_type='UPDATE' ORDER BY id DESC").fetchone()
    return json.loads(row["raw_data_snapshot"])


def test_f07_equivalent_time_replay_after_sent_does_not_requeue(seeded):
    add_user(seeded)
    fanout = FanoutEngine(seeded.users, seeded.deliveries)
    seeded.upsert_company(company(regEndtime="10:00"))
    assert fanout.dispatch_opportunity(company(regEndtime="10:00"), "UPDATE") == 1
    claim = seeded.deliveries.claim_pending_batch(batch_size=1)[0]
    seeded.deliveries.mark_sent(claim["id"])

    # "10:00:00" is an equivalent representation of "10:00": bytewise
    # different, semantically the same event. It must not requeue.
    assert fanout.dispatch_opportunity(company(regEndtime="10:00:00"), "UPDATE") == 0
    assert delivery_row(seeded)["status"] == "SENT"

    # A genuinely later deadline still requeues exactly once.
    assert fanout.dispatch_opportunity(company(regEndtime="18:00"), "UPDATE") == 1
    assert fanout.dispatch_opportunity(company(regEndtime="18:00"), "UPDATE") == 0


def test_f07_snapshot_event_identity_covers_packaging_and_closure(seeded):
    from app.database.sqlite_repository import SQLiteDeliveryRepository
    distinct = SQLiteDeliveryRepository._snapshot_event_distinct
    base = company().model_dump_json()
    assert distinct(base, base) is False
    assert distinct(base, company(maxPackage="30000.0").model_dump_json()) is True
    assert distinct(base, company(isactive="False").model_dump_json()) is True
    assert distinct(base, company(regEndtime="10:00:00").model_dump_json()) is False
    assert distinct(None, base) is True
