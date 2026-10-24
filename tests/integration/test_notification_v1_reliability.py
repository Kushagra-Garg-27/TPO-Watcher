"""V1 reliability boundaries using guarded disposable SQLite and fictional SMTP."""
import json
import sqlite3
from contextlib import closing

import pytest

from app.database.sqlite_repository import SQLiteDeliveryRepository
from app.monitoring.detector import CompanyDetector
from app.subscribers.fanout import FanoutEngine
from test_cummins_pipeline_stress import (
    audit_repo, add_user, company, delivery_row, expire_claim, queue_one,
    FakeNotifier, real_worker, safe_watcher,
)


@pytest.mark.parametrize("action", ["sent", "retry", "terminal"])
def test_old_claim_cannot_mutate_a_new_processing_claim(audit_repo, action):
    queue_one(audit_repo)
    old = audit_repo.deliveries.claim_pending_batch()[0]
    expire_claim(audit_repo, old["id"])
    audit_repo.deliveries.recover_stale_leases()
    other = SQLiteDeliveryRepository(audit_repo.db_path)
    current = other.claim_pending_batch()[0]
    if action == "sent":
        audit_repo.deliveries.mark_sent(old["id"])
    else:
        audit_repo.deliveries.release_failed(old["id"], "fictional stale worker", terminal=action == "terminal")
    assert delivery_row(audit_repo)["status"] == "PROCESSING"
    assert delivery_row(audit_repo)["attempt_count"] == 2
    other.mark_sent(current["id"])
    assert delivery_row(audit_repo)["status"] == "SENT"


@pytest.mark.parametrize("action", ["sent", "retry"])
def test_serialized_numeric_id_is_not_claim_authority(audit_repo, action):
    queue_one(audit_repo)
    claim = audit_repo.deliveries.claim_pending_batch()[0]
    numeric_id = json.loads(json.dumps(claim["id"]))
    if action == "sent":
        audit_repo.deliveries.mark_sent(numeric_id)
    else:
        audit_repo.deliveries.release_failed(numeric_id, "fictional missing authority")
    assert delivery_row(audit_repo)["status"] == "PROCESSING"
    audit_repo.deliveries.mark_sent(claim["id"])
    assert delivery_row(audit_repo)["status"] == "SENT"


@pytest.mark.parametrize("action", ["sent", "retry"])
def test_expired_claim_cannot_complete_before_recovery(audit_repo, action):
    queue_one(audit_repo)
    claim = audit_repo.deliveries.claim_pending_batch()[0]
    expire_claim(audit_repo, claim["id"])
    if action == "sent":
        audit_repo.deliveries.mark_sent(claim["id"])
    else:
        audit_repo.deliveries.release_failed(claim["id"], "fictional late result")
    assert delivery_row(audit_repo)["status"] == "PROCESSING"
    assert audit_repo.deliveries.recover_stale_leases() == 1


def test_replaying_the_same_sent_update_does_not_requeue(audit_repo):
    add_user(audit_repo)
    record = company()
    audit_repo.upsert_company(record)
    fanout = FanoutEngine(audit_repo.users, audit_repo.deliveries)
    assert fanout.dispatch_opportunity(record, "UPDATE") == 1
    claim = audit_repo.deliveries.claim_pending_batch()[0]
    audit_repo.deliveries.mark_sent(claim["id"])
    assert fanout.dispatch_opportunity(record, "UPDATE") == 0
    assert delivery_row(audit_repo)["status"] == "SENT"
    assert fanout.dispatch_opportunity(record.model_copy(update={"regEndtime": "18:00"}), "UPDATE") == 1


@pytest.mark.asyncio
async def test_admin_retry_does_not_repeat_completed_subscriber_fanout(audit_repo, monkeypatch):
    add_user(audit_repo)
    audit_repo.upsert_company(company())
    audit_repo.set_baseline_initialized()
    notifier = FakeNotifier(admin_outcomes=[False, True])
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company(regEndtime="18:00")]])
    assert await watcher.check_once() is True
    assert notifier.student_calls == 1
    assert len(audit_repo.get_pending_notifications()) == 1
    assert await watcher.check_once() is True
    assert notifier.student_calls == 1
    assert not audit_repo.get_pending_notifications()


@pytest.mark.asyncio
async def test_current_preference_change_suppresses_previously_queued_new(audit_repo):
    queue_one(audit_repo)
    with closing(audit_repo._get_conn()) as conn:
        conn.execute("UPDATE user_preferences SET pref_internship=0,pref_placement=0,pref_ppo=0")
        conn.commit()
    notifier = FakeNotifier()
    assert await real_worker(audit_repo, notifier).process_batch_once() == 0
    assert notifier.student_calls == 0
    assert delivery_row(audit_repo)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_time_only_cycles_remain_distinct_legitimate_updates(audit_repo, monkeypatch):
    add_user(audit_repo)
    detector = CompanyDetector(audit_repo)
    control = company(id="SYNTHETIC-CONTROL")
    detector.process_fetched_companies([control])
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [
        [control, company()], [control, company(regEndtime="09:00")],
        [control, company(regEndtime="18:00")], [control, company(regEndtime="18:00")],
    ])
    for _ in range(4):
        assert await watcher.check_once() is True
    assert notifier.student_calls == 3
    assert notifier.admin_calls == 3
    assert not audit_repo.get_pending_notifications()


@pytest.mark.asyncio
@pytest.mark.parametrize('snapshot_active,expected_sent', [('True', 0), ('False', 1)])
async def test_queued_update_cannot_claim_active_after_current_closure(audit_repo, snapshot_active, expected_sent):
    add_user(audit_repo)
    record = company(isactive=snapshot_active)
    audit_repo.upsert_company(record)
    assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(record, 'UPDATE') == 1
    with closing(audit_repo._get_conn()) as conn:
        conn.execute("UPDATE companies SET is_active='False'")
        conn.commit()
    notifier = FakeNotifier()
    assert await real_worker(audit_repo, notifier).process_batch_once() == expected_sent
    assert notifier.student_calls == expected_sent


def test_standalone_initialization_and_existing_production_schema_are_compatible(audit_repo, tmp_path):
    from app.database.migrations import run_migrations
    standalone = str(tmp_path / 'standalone.sqlite')
    run_migrations(standalone)
    with sqlite3.connect(standalone) as conn:
        assert {r[1] for r in conn.execute('PRAGMA table_info(system_state)')} == {'key', 'value', 'updated_at'}
    with closing(audit_repo._get_conn()) as conn:
        conn.execute("INSERT INTO system_state(key,value,updated_at) VALUES('fictional-preserved','yes','2026-10-24')")
        conn.commit()
        before = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    run_migrations(audit_repo.db_path)
    with closing(audit_repo._get_conn()) as conn:
        assert conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall() == before
        assert conn.execute("SELECT value FROM system_state WHERE key='fictional-preserved'").fetchone()[0] == 'yes'


@pytest.mark.asyncio
async def test_claim_expiring_during_render_never_reaches_transport(audit_repo, monkeypatch):
    queue_one(audit_repo)
    notifier = FakeNotifier()
    worker = real_worker(audit_repo, notifier)
    render = worker._format_opportunity_email
    def expire_during_render(*args, **kwargs):
        result = render(*args, **kwargs)
        expire_claim(audit_repo, delivery_row(audit_repo)['id'])
        return result
    monkeypatch.setattr(worker, '_format_opportunity_email', expire_during_render)
    assert await worker.process_batch_once() == 0
    assert notifier.student_calls == 0
    assert audit_repo.deliveries.recover_stale_leases() == 1


@pytest.mark.asyncio
async def test_crash_between_fanout_enqueue_and_receipt_replays_without_duplicate_send(audit_repo, monkeypatch):
    add_user(audit_repo)
    audit_repo.upsert_company(company())
    audit_repo.set_baseline_initialized()
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company(regEndtime='18:00')]])
    write = audit_repo.set_system_state
    crashed = False
    def fail_once(key, value):
        nonlocal crashed
        if key.startswith('notification:v1:fanout:') and not crashed:
            crashed = True
            raise RuntimeError('fictional interruption after durable recipient enqueue')
        return write(key, value)
    monkeypatch.setattr(audit_repo, 'set_system_state', fail_once)
    assert await watcher.check_once() is False
    assert notifier.student_calls == 1 and notifier.admin_calls == 0
    assert len(audit_repo.get_pending_notifications()) == 1
    assert await watcher.check_once() is True
    assert notifier.student_calls == 1 and notifier.admin_calls == 1
    assert not audit_repo.get_pending_notifications()


@pytest.mark.asyncio
async def test_distinct_update_waits_for_prior_update_instead_of_losing_obligation(audit_repo, monkeypatch):
    add_user(audit_repo)
    earlier = company(regEndtime='09:00')
    audit_repo.upsert_company(earlier)
    audit_repo.set_baseline_initialized()
    assert FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(earlier, 'UPDATE') == 1
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company(regEndtime='18:00')]])
    assert await watcher.check_once() is False
    assert notifier.student_calls == 1 and notifier.admin_calls == 0
    assert len(audit_repo.get_pending_notifications()) == 1
    assert await watcher.check_once() is True
    assert notifier.student_calls == 2 and notifier.admin_calls == 1
    assert not audit_repo.get_pending_notifications()


@pytest.mark.asyncio
async def test_transport_exception_does_not_log_or_persist_private_message(audit_repo, caplog):
    queue_one(audit_repo)
    class PrivateFailure(FakeNotifier):
        def _send_email_to(self, *args, **kwargs):
            raise RuntimeError('PRIVATE-SMTP-TOKEN fictional@example.invalid')
    assert await real_worker(audit_repo, PrivateFailure()).process_batch_once() == 0
    with closing(audit_repo._get_conn()) as conn:
        error = conn.execute('SELECT error_message FROM notification_deliveries').fetchone()[0]
    assert 'PRIVATE-SMTP-TOKEN' not in caplog.text + error
    assert 'fictional@example.invalid' not in caplog.text + error
    assert delivery_row(audit_repo)['status'] == 'PENDING'
