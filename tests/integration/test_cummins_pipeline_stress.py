"""Phase C1 bounded synthetic pipeline/recovery/concurrency/load evidence.

Only guarded disposable SQLite and fake external boundaries. Contract defects
intentionally remain red; characterization passes do not imply remediation.
"""
import json
import math
import threading
import time
import tracemalloc
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("C1 requires the established isolation bootstrap")

import pytest
from app.database.repository import DatabaseRepository
from app.database.sqlite_repository import SQLiteDeliveryRepository
from app.monitoring import watcher as watcher_module
from app.monitoring.detector import CompanyDetector
from app.subscribers.fanout import FanoutEngine
from app.subscribers import worker as worker_module
from app.subscribers.worker import DeliveryWorker
from app.tpo.models import CompanyRecord

SYNTHETIC_ID = "SYNTHETIC-CUMMINS-AUDIT-001"
CE = "VIT-BTech-Computer Engineering"
IT = "VIT-BTech-Information Technology"


class ContractFailure(AssertionError):
    """Behavioral contract only; never synchronization or setup errors."""


def require_contract(ok, finding, observed):
    if not ok:
        raise ContractFailure(f"{finding}: unmet declared contract; observed={observed!r}")


def evidence(kind, data):
    """No addresses, tokens, headers or subscriber rows in the evidence ledger."""
    path = isolation.ROOT / "phase-c1-evidence.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": kind, **data}, sort_keys=True) + "\n")
    print("C1_EVIDENCE_PATH:", path)
    print("C1_EVIDENCE:", json.dumps({"kind": kind, **data}, sort_keys=True))


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    @classmethod
    def read(cls, markup):
        parser = cls()
        parser.feed(markup)
        return " ".join(" ".join(parser.parts).split())


class FakeNotifier:
    """Count fake acceptance, retaining only selected sanitized visible fields."""
    def __init__(self, student_outcomes=(), admin_outcomes=()):
        self.student_outcomes = list(student_outcomes)
        self.admin_outcomes = list(admin_outcomes)
        self.student_calls = 0
        self.admin_calls = 0
        self.admin_errors = 0
        self.facts = []

    def _send_email_to(self, to_email, subject, html_content, extra_headers=None):
        assert to_email.endswith("@example.invalid")
        self.student_calls += 1
        text = TextOnly.read(html_content)
        self.facts.append({
            "type": "NEW" if "NEW:" in subject else "UPDATE",
            "initial_deadline": "14-Oct-2026 10:00" in text,
            "latest_deadline": "15-Oct-2026 18:00" in text,
            "status_context": any(term in text.lower() for term in ("active status", "closed", "reopened")),
            "lpa_label": "Package (LPA)" in text,
        })
        outcome = self.student_outcomes.pop(0) if self.student_outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _admin(self):
        self.admin_calls += 1
        outcome = self.admin_outcomes.pop(0) if self.admin_outcomes else True
        if isinstance(outcome, Exception):
            self.admin_errors += 1
            raise outcome
        return outcome

    def send_new_company_notification(self, company, target_match):
        assert company.id.startswith("SYNTHETIC-")
        return self._admin()

    def send_update_notification(self, company, changes):
        assert company.id.startswith("SYNTHETIC-")
        return self._admin()


def company(**changes):
    values = dict(
        id=SYNTHETIC_ID, company="Fictional Pipeline Employer",
        company_code="SYNTHETIC-ONLY", regStartdate="13-Oct-2026",
        regStarttime="11:31", regEnddate="14-Oct-2026", regEndtime="10:00",
        maxPackage="25000.0", minPackage="25000.0", isactive="True",
        placementtype="Internship + Performance based PPO", internshiptype="N/A",
        academicyear="2026-27", tpoprogram=CE, organization="VIT",
    )
    values.update(changes)
    return CompanyRecord(**values)


@pytest.fixture
def audit_repo(tmp_path):
    path = (tmp_path / "synthetic-cummins-stress.sqlite").resolve()
    assert path.is_relative_to(isolation.ROOT)
    return DatabaseRepository(str(path))


def add_user(repo, serial=0, branch="VIT_CE", verified=True, **prefs):
    uid = repo.users.create_user(f"synthetic-{serial}@example.invalid", 2028, branch, **prefs)
    if verified:
        repo.users.set_verified(uid)
    return uid


def real_worker(repo, notifier):
    return DeliveryWorker(repo.deliveries, repo.tokens, notifier, send_interval_seconds=0)


def queue_one(repo):
    repo.upsert_company(company())
    add_user(repo)
    assert FanoutEngine(repo.users, repo.deliveries).dispatch_opportunity(company(), "NEW") == 1


def delivery_row(repo):
    with closing(repo._get_conn()) as conn:
        rows = conn.execute(
            "SELECT id,status,attempt_count,max_attempts,lease_expires_at FROM notification_deliveries"
        ).fetchall()
    assert len(rows) == 1
    return dict(rows[0])


def expire_claim(repo, delivery_id):
    with closing(repo._get_conn()) as conn:
        conn.execute("UPDATE notification_deliveries SET lease_expires_at=? WHERE id=?",
                     ("2000-01-01T00:00:00+00:00", delivery_id))
        conn.commit()


def safe_watcher(repo, monkeypatch, notifier, feeds):
    state = {"fetches": 0}

    class FakeAuth:
        context = None

        async def is_authenticated(self):
            return True

        async def get_valid_page(self):
            return None

    class FakeClient:
        def __init__(self, *args):
            pass

        async def fetch_companies(self):
            index = min(state["fetches"], len(feeds) - 1)
            state["fetches"] += 1
            return feeds[index]

    monkeypatch.setattr(watcher_module, "AuthManager", FakeAuth)
    monkeypatch.setattr(watcher_module, "TPOClient", FakeClient)
    monkeypatch.setattr(watcher_module, "TimezoneScheduler", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(watcher_module, "EmailNotificationProvider", lambda: notifier)
    monkeypatch.setattr(watcher_module, "send_healthcheck_ping", lambda *a, **kw: None)
    monkeypatch.setattr(worker_module.settings, "BASE_URL", "https://audit.example.invalid")
    result = watcher_module.PlacementWatcher(db=repo)
    result.delivery_worker.send_interval_seconds = 0
    return result, state


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [10, 100, 1000])
async def test_f04_synthetic_load_one_check_then_bounded_manual_drain(audit_repo, monkeypatch, n):
    audit_repo.upsert_company(company())
    audit_repo.set_baseline_initialized()
    tracer_was_active = tracemalloc.is_tracing()
    assert not tracer_was_active
    tracemalloc.start()
    try:
        started = time.perf_counter()
        for serial in range(n):
            add_user(audit_repo, serial)
        creation_s = time.perf_counter() - started
        candidates = len(audit_repo.users.get_active_subscribers_for_branch("VIT_CE", 2028))
        fanout = FanoutEngine(audit_repo.users, audit_repo.deliveries)
        started = time.perf_counter()
        inserted = fanout.dispatch_opportunity(company(), "NEW")
        fanout_s = time.perf_counter() - started
        assert candidates == inserted == n
        assert audit_repo.deliveries.get_delivery_stats()["PENDING"] == n
        started = time.perf_counter()
        replay = fanout.dispatch_opportunity(company(), "NEW")
        replay_s = time.perf_counter() - started
        assert replay == 0

        notifier = FakeNotifier()
        watcher, fetch_state = safe_watcher(audit_repo, monkeypatch, notifier, [[company()]])
        claim_calls = 0
        original_claim = audit_repo.deliveries.claim_pending_batch

        def counted_claim(*args, **kwargs):
            nonlocal claim_calls
            claim_calls += 1
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(audit_repo.deliveries, "claim_pending_batch", counted_claim)
        started = time.perf_counter()
        assert await watcher.check_once() is True
        first_count = notifier.student_calls
        first_stats = audit_repo.deliveries.get_delivery_stats()
        assert first_count == min(n, 25)
        assert first_stats["PENDING"] == max(n - 25, 0)
        assert fetch_state["fetches"] == 1
        batches = 1
        for _ in range(math.ceil(n / 25) + 2):
            sent = await watcher.delivery_worker.process_batch_once()
            if sent == 0:
                break
            batches += 1
        else:
            raise RuntimeError("C1 bounded drain did not terminate")
        drain_s = time.perf_counter() - started
        stats = audit_repo.deliveries.get_delivery_stats()
        assert batches == math.ceil(n / 25)
        assert claim_calls == batches + 1
        assert notifier.student_calls == n
        assert stats == {"SENT": n, "PENDING": 0, "PROCESSING": 0, "FAILED": 0}
        assert fanout.dispatch_opportunity(company(), "NEW") == 0
        assert await watcher.delivery_worker.process_batch_once() == 0
        assert notifier.student_calls == n
        with closing(audit_repo._get_conn()) as conn:
            token_count = conn.execute("SELECT count(*) FROM action_tokens").fetchone()[0]
        assert token_count == 2 * n
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    main_path = Path(audit_repo.db_path)
    sizes = {name: (Path(str(main_path) + suffix).stat().st_size
                   if Path(str(main_path) + suffix).exists() else 0)
             for name, suffix in (("db_main_bytes", ""), ("wal_bytes", "-wal"), ("shm_bytes", "-shm"))}
    evidence("load", dict(n=n, creation_s=creation_s, candidates=candidates,
                         inserted=inserted, fanout_s=fanout_s, replay_count=replay,
                         replay_s=replay_s, first_check_sent=first_count,
                         after_first_pending=first_stats["PENDING"],
                         drain_batches=batches, claim_calls_including_empty_probes=claim_calls,
                         fake_calls=notifier.student_calls, first_check_plus_manual_drain_s=drain_s,
                         peak_python_bytes=peak, token_rows=token_count,
                         final_stats=stats, **sizes))


def test_f05_deterministic_two_connections_require_disjoint_claims(audit_repo, monkeypatch):
    queue_one(audit_repo)
    repos = {name: SQLiteDeliveryRepository(audit_repo.db_path) for name in ("A", "B")}
    events = {name: threading.Event() for name in ("A_selected", "B_selected", "A_done")}
    snapshots, returned, errors, transaction_states, trace = {}, {}, [], {}, []

    class CandidateCursor:
        def __init__(self, cursor, connection, name):
            self.cursor, self.connection, self.name = cursor, connection, name

        def fetchall(self):
            rows = self.cursor.fetchall()  # Fully finish the real SELECT before waiting.
            snapshots[self.name] = [row["id"] for row in rows]
            transaction_states[self.name] = self.connection.in_transaction
            trace.append(self.name + "_selected")
            events[self.name + "_selected"].set()
            # Never wait while holding a write transaction. An exclusive future
            # implementation can finish its claim before another reader selects.
            if rows and not self.connection.in_transaction:
                gate = "B_selected" if self.name == "A" else "A_done"
                if not events[gate].wait(5):
                    raise RuntimeError("C1 candidate gate timeout")
            return rows

    class ConnectionProxy:
        def __init__(self, connection, name):
            self.connection, self.name = connection, name

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            try:
                return self.connection.__exit__(*args)
            finally:
                self.connection.close()

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT id FROM notification_deliveries"):
                return CandidateCursor(cursor, self.connection, self.name)
            return cursor

        def commit(self):
            self.connection.commit()
            trace.append(self.name + "_committed")

    for name, repo in repos.items():
        native = repo._get_conn
        monkeypatch.setattr(repo, "_get_conn", lambda name=name, native=native:
                            ConnectionProxy(native(), name))

    def claim(name):
        try:
            if name == "B" and not events["A_selected"].wait(5):
                raise RuntimeError("C1 first reader gate timeout")
            returned[name] = repos[name].claim_pending_batch(batch_size=1, lease_seconds=300)
            trace.append(name + "_returned")
        except Exception as error:
            errors.append((name, type(error).__name__, str(error)))
        finally:
            if name == "A":
                events["A_done"].set()

    threads = [threading.Thread(target=claim, args=(name,), name=f"C1-claim-{name}")
               for name in ("A", "B")]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    finally:
        for event in events.values():
            event.set()
        for thread in threads:
            thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors, errors
    assert set(returned) == {"A", "B"}
    ids_a = {row["id"] for row in returned["A"]}
    ids_b = {row["id"] for row in returned["B"]}
    if not any(transaction_states.values()):
        assert snapshots["A"] == snapshots["B"] and len(snapshots["A"]) == 1
        assert trace.index("A_committed") < trace.index("B_committed")
    evidence("claim_race", dict(snapshots=snapshots, a_returned=sorted(ids_a),
                               b_returned=sorted(ids_b), transactions=transaction_states,
                               trace=trace, attempt_count=delivery_row(audit_repo)["attempt_count"]))
    require_contract(ids_a.isdisjoint(ids_b), "F05",
                     {"A": sorted(ids_a), "B": sorted(ids_b), "trace": trace})


@pytest.mark.asyncio
async def test_f06_failed_fanout_after_persistence_is_replayed(audit_repo, monkeypatch):
    audit_repo.set_baseline_initialized()
    add_user(audit_repo)
    notifier = FakeNotifier()
    watcher, fetch_state = safe_watcher(audit_repo, monkeypatch, notifier, [[company()], [company()]])
    original = watcher.fanout.dispatch_opportunity
    invocations = 0

    def fail_once(*args, **kwargs):
        nonlocal invocations
        invocations += 1
        if invocations == 1:
            raise RuntimeError("Synthetic crash boundary before fanout")
        return original(*args, **kwargs)

    monkeypatch.setattr(watcher.fanout, "dispatch_opportunity", fail_once)
    assert await watcher.check_once() is False
    assert audit_repo.get_company(SYNTHETIC_ID) is not None
    assert len(audit_repo.get_pending_notifications()) == 1
    assert sum(audit_repo.deliveries.get_delivery_stats().values()) == 0
    assert notifier.admin_calls == notifier.student_calls == 0
    assert await watcher.check_once() is True
    assert fetch_state["fetches"] == 2 and notifier.admin_calls == 1
    assert not audit_repo.get_pending_notifications()
    evidence("lost_fanout", dict(fetches=fetch_state["fetches"], fanout_calls=invocations,
                                admin_calls=notifier.admin_calls,
                                student_calls=notifier.student_calls,
                                stats=audit_repo.deliveries.get_delivery_stats()))
    require_contract(notifier.student_calls == 1, "F06",
                     {"fanout_calls": invocations, "subscriber_sends": notifier.student_calls})


def test_f12_exhausted_crash_recovery_requires_terminal_state(audit_repo):
    queue_one(audit_repo)
    counts = []
    for _ in range(5):
        claimed = audit_repo.deliveries.claim_pending_batch(batch_size=1, lease_seconds=300)
        assert len(claimed) == 1
        expire_claim(audit_repo, claimed[0]["id"])
        assert audit_repo.deliveries.recover_stale_leases() == 1
        counts.append(delivery_row(audit_repo)["attempt_count"])
    row = delivery_row(audit_repo)
    assert counts == [1, 2, 3, 4, 5]
    assert row["attempt_count"] == row["max_attempts"] == 5
    assert audit_repo.deliveries.claim_pending_batch(batch_size=1) == []
    evidence("exhausted_lease", dict(attempts=counts, status=row["status"], sixth_claim=0))
    require_contract(row["status"] == "FAILED", "F12",
                     {"status": row["status"], "attempts": row["attempt_count"], "sixth_claim": 0})


def test_f13_stale_release_cannot_overwrite_newer_sent(audit_repo):
    queue_one(audit_repo)
    owner_a = audit_repo.deliveries.claim_pending_batch(batch_size=1)[0]
    expire_claim(audit_repo, owner_a["id"])
    assert audit_repo.deliveries.recover_stale_leases() == 1
    owner_b = audit_repo.deliveries.claim_pending_batch(batch_size=1)[0]
    assert owner_b["attempt_count"] == 2
    audit_repo.deliveries.mark_sent(owner_b["id"])
    assert delivery_row(audit_repo)["status"] == "SENT"
    audit_repo.deliveries.release_failed(owner_a["id"], "Synthetic stale A release")
    row = delivery_row(audit_repo)
    evidence("stale_owner", dict(new_owner_completed="SENT", after_stale_release=row["status"],
                                attempts=row["attempt_count"]))
    require_contract(row["status"] == "SENT", "F13", {"after_stale_release": row["status"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("outcomes,expected_attempts", [
    ([False, False, True], 3),
    ([RuntimeError("Synthetic mail exception"), True], 2),
    ([False] * 5, 5),
])
async def test_fake_delivery_retry_and_terminal_limits(audit_repo, outcomes, expected_attempts):
    queue_one(audit_repo)
    notifier = FakeNotifier(student_outcomes=outcomes)
    delivery_worker = real_worker(audit_repo, notifier)
    successful = 0
    for _ in range(expected_attempts):
        successful += await delivery_worker.process_batch_once()
    row = delivery_row(audit_repo)
    terminal = "FAILED" if expected_attempts == 5 else "SENT"
    assert row["status"] == terminal
    assert row["attempt_count"] == expected_attempts
    assert notifier.student_calls == expected_attempts
    assert successful == (0 if terminal == "FAILED" else 1)
    assert await delivery_worker.process_batch_once() == 0
    assert notifier.student_calls == expected_attempts
    evidence("retry", dict(attempts=expected_attempts, final_status=terminal,
                           fake_calls=notifier.student_calls, successful=successful))


@pytest.mark.asyncio
async def test_f13_fake_acceptance_then_lost_ack_is_at_least_once(audit_repo, monkeypatch):
    queue_one(audit_repo)
    notifier = FakeNotifier()
    native = audit_repo.deliveries.mark_sent
    calls = 0

    def lose_first_ack(delivery_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Synthetic post-acceptance acknowledgment failure")
        return native(delivery_id)

    monkeypatch.setattr(audit_repo.deliveries, "mark_sent", lose_first_ack)
    delivery_worker = real_worker(audit_repo, notifier)
    assert await delivery_worker.process_batch_once() == 0
    assert delivery_row(audit_repo)["status"] == "PENDING"
    assert await delivery_worker.process_batch_once() == 1
    assert delivery_row(audit_repo)["status"] == "SENT"
    assert notifier.student_calls == 2
    evidence("smtp_uncertainty", dict(queue_rows=1, fake_acceptances=2, final_status="SENT"))


@pytest.mark.asyncio
async def test_f16_false_admin_does_not_block_other_admins_or_subscriber(audit_repo, monkeypatch):
    audit_repo.set_baseline_initialized()
    add_user(audit_repo)
    second = company(id="SYNTHETIC-CUMMINS-AUDIT-002", company="Fictional Second Employer")
    notifier = FakeNotifier(admin_outcomes=[False, True, True])
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company(), second]])
    assert await watcher.check_once() is True
    assert notifier.admin_calls == 2 and notifier.student_calls == 2
    assert len(audit_repo.get_pending_notifications()) == 1
    assert await watcher.check_once() is True
    assert notifier.admin_calls == 3 and notifier.student_calls == 2
    assert not audit_repo.get_pending_notifications()
    evidence("admin_false_retry", dict(admin_calls=3, subscriber_calls=2, admin_pending=0))


@pytest.mark.asyncio
async def test_f16_admin_exception_must_not_block_student_processing(audit_repo, monkeypatch):
    audit_repo.set_baseline_initialized()
    add_user(audit_repo)
    second = company(id="SYNTHETIC-CUMMINS-AUDIT-002", company="Fictional Second Employer")
    notifier = FakeNotifier(admin_outcomes=[RuntimeError("Synthetic admin exception"), True])
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company(), second]])
    result = await watcher.check_once()
    assert notifier.admin_errors == 1
    assert sum(audit_repo.deliveries.get_delivery_stats().values()) == 2
    evidence("admin_exception", dict(check_result=result, admin_calls=notifier.admin_calls,
                                    student_calls=notifier.student_calls,
                                    admin_pending=len(audit_repo.get_pending_notifications()),
                                    stats=audit_repo.deliveries.get_delivery_stats()))
    require_contract(notifier.student_calls == 2, "F16",
                     {"admin_calls": notifier.admin_calls, "subscriber_sends": notifier.student_calls})


@pytest.mark.asyncio
async def test_f16_admin_fake_acceptance_then_lost_ack_retries(audit_repo, monkeypatch):
    audit_repo.set_baseline_initialized()
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[company()]])
    native = audit_repo.remove_pending_notification
    calls = 0

    def fail_ack_once(notification_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Synthetic admin acknowledgment failure")
        return native(notification_id)

    monkeypatch.setattr(audit_repo, "remove_pending_notification", fail_ack_once)
    assert await watcher.check_once() is False
    assert notifier.admin_calls == 1 and len(audit_repo.get_pending_notifications()) == 1
    assert await watcher.check_once() is True
    assert notifier.admin_calls == 2 and not audit_repo.get_pending_notifications()
    evidence("admin_ack_uncertainty", dict(fake_acceptances=2, admin_pending=0))


@pytest.mark.asyncio
async def test_f10_queued_new_after_feed_deactivation_requires_freshness(audit_repo):
    queue_one(audit_repo)
    control = company(id="SYNTHETIC-CONTROL", company="Fictional Control")
    audit_repo.upsert_company(control)
    assert audit_repo.deactivate_missing_companies({control.id}) == [SYNTHETIC_ID]
    assert audit_repo.get_company(SYNTHETIC_ID)["is_active"] == "False"
    notifier = FakeNotifier()
    sent = await real_worker(audit_repo, notifier).process_batch_once()
    evidence("stale_new", dict(normalized_active=False, fake_calls=notifier.student_calls,
                              sent=sent, facts=notifier.facts))
    # Declared freshness policy only for NEW, not a ban on closure UPDATE.
    require_contract(notifier.student_calls == 0, "F10",
                     {"inactive_new_fake_sends": notifier.student_calls})


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_changes", [
    {},
    {"regEnddate": "2026-10-16"},
    {"regEndtime": "18:00:00"},
], ids=["unchanged", "equivalent-deadline-date", "equivalent-deadline-time"])
async def test_full_no_reset_natural_cycle_characterization(audit_repo, monkeypatch, replay_changes):
    add_user(audit_repo, 0, "VIT_CE")
    add_user(audit_repo, 1, "VIT_IT")
    control = company(id="SYNTHETIC-CONTROL", company="Fictional Control")
    detector = CompanyDetector(audit_repo)
    detector.process_fetched_companies([control])
    notifier = FakeNotifier()
    watcher, _ = safe_watcher(audit_repo, monkeypatch, notifier, [[control]])
    fanout = watcher.fanout
    current = company()
    steps = [
        ("A_NEW", {}),
        ("B_EARLIER", {"regEndtime": "09:00"}),
        ("C_LATER", {"regEndtime": "18:00"}),
        ("D_DATE", {"regEnddate": "15-Oct-2026"}),
        ("E_CLOSED", {"isactive": "False"}),
        ("F_REOPENED", {"isactive": "True"}),
        ("G_PROGRAMS", {"tpoprogram": CE + ", " + IT}),
        ("H_ORGANIZATION", {"organization": "VU"}),
        ("I_LATER_UPDATE", {"regEnddate": "16-Oct-2026"}),
        ("J_REPLAY", replay_changes),
        ("K_DISAPPEAR", {}),
        ("L_EMPTY", {}),
    ]
    snapshots = []
    for name, changes in steps:
        current = current.model_copy(update=changes)
        feed = [] if name == "L_EMPTY" else [control] if name == "K_DISAPPEAR" else [control, current]
        result = detector.process_fetched_companies(feed)
        inserted = 0
        for new in result.new_companies:
            inserted += fanout.dispatch_opportunity(new, "NEW")
        for updated, _changes in result.updated_companies:
            inserted += fanout.dispatch_opportunity(updated, "UPDATE")
        stored = audit_repo.get_company(SYNTHETIC_ID)
        raw = json.loads(stored["raw_data_json"])
        pending = len(audit_repo.get_pending_notifications())
        prior_admin = notifier.admin_calls
        prior_facts = len(notifier.facts)
        await watcher._process_notifications()
        sent = await watcher.delivery_worker.process_batch_once()
        with closing(audit_repo._get_conn()) as conn:
            types = [row[0] for row in conn.execute(
                "SELECT notification_type FROM notification_deliveries ORDER BY id").fetchall()]
        snapshots.append(dict(
            step=name, new=len(result.new_companies), updates=len(result.updated_companies),
            deactivated=list(result.deactivated_companies),
            changes=[change for _, values in result.updated_companies for change in values],
            admin_pending_before=pending, admin_attempts=notifier.admin_calls - prior_admin,
            enqueued=inserted, sent=sent, types=types,
            normalized=dict(end_date=stored["registration_end"], active=stored["is_active"],
                            programs=stored["eligible_programs"], organization=stored["organizations"]),
            raw=dict(end_date=raw["regEnddate"], end_time=raw["regEndtime"],
                     active=raw["isactive"], programs=raw["tpoprogram"], organization=raw["organization"]),
            render_facts=notifier.facts[prior_facts:],
            stats=audit_repo.deliveries.get_delivery_stats(),
        ))
    # Approved policy: real deadline changes are meaningful UPDATEs. Closure,
    # reopening and programme changes retain their existing distinct-update
    # contracts; unchanged or equivalent deadline observations are not events.
    assert [(row["new"], row["updates"], row["enqueued"], row["sent"]) for row in snapshots] == [
        (1, 0, 1, 1), (0, 1, 1, 1), (0, 1, 1, 1), (0, 1, 1, 1),
        (0, 1, 1, 1), (0, 1, 1, 1), (0, 1, 2, 2), (0, 1, 0, 0),
        (0, 1, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0),
    ]
    assert [row["admin_pending_before"] for row in snapshots] == [1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0]
    assert snapshots[-1]["stats"]["SENT"] == 3
    assert snapshots[-1]["types"] == ["NEW", "UPDATE", "UPDATE"]
    assert snapshots[4]["raw"]["active"] == "False"
    assert snapshots[5]["raw"]["active"] == "True"
    assert snapshots[7]["raw"]["organization"] == "VU"
    # H/I's zero fanout is institution exclusion; E/F remain distinct updates.
    # G reaches both eligible subscribers; replay J reaches neither.
    assert snapshots[10]["deactivated"] == [SYNTHETIC_ID]
    assert snapshots[10]["normalized"]["active"] == "False"
    assert snapshots[10]["raw"]["active"] == "True"
    assert snapshots[11]["deactivated"] == []
    assert snapshots[11]["normalized"]["active"] == "False"
    assert not audit_repo.get_pending_notifications()
    evidence("lifecycle", dict(steps=snapshots))


@pytest.mark.parametrize("verified,active,internship,ppo,branch,want", [
    (True, True, True, False, "VIT_CE", 1),
    (True, True, False, True, "VIT_CE", 1),
    (True, True, False, False, "VIT_CE", 0),
    (False, True, True, True, "VIT_CE", 0),
    (True, False, True, True, "VIT_CE", 0),
    (True, True, True, True, "VIT_AIDS", 0),
])
def test_routing_safety_matrix(audit_repo, verified, active, internship, ppo, branch, want):
    audit_repo.upsert_company(company())
    uid = add_user(audit_repo, branch=branch, verified=verified,
                   pref_internship=internship, pref_placement=True, pref_ppo=ppo)
    if not active:
        audit_repo.users.set_unsubscribed(uid)
    actual = FanoutEngine(audit_repo.users, audit_repo.deliveries).dispatch_opportunity(company())
    assert actual == want
    assert audit_repo.deliveries.get_delivery_stats()["PENDING"] == want

