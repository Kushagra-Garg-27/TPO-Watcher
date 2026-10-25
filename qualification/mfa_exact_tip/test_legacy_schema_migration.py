"""Gate C: migration and rollback safety against the previously DEPLOYED schema.

The fixture database is hand-built with the exact schema bytes of the deployed
lineage (eb086ae: no pending_notifications.event_snapshot_json, no
notification_delivery_outcome_log) plus fictional records covering every
delivery/obligation state, an unexpired claim and an ambiguity marker.
"""
import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from app.database.repository import DatabaseRepository

EMAIL = "legacy-subscriber@example.invalid"
CID = "SYNTHETIC-LEGACY-COMPANY"


def _legacy_schema_statements():
    """Schema exactly as the deployed lineage creates it (no remediation columns)."""
    return [
        """CREATE TABLE companies (
            id TEXT PRIMARY KEY, company TEXT, company_code TEXT,
            registration_start TEXT, registration_end TEXT, max_package TEXT,
            min_package TEXT, placement_type TEXT, academic_year TEXT,
            company_type TEXT, internship_type TEXT, skills TEXT,
            eligible_programs TEXT, organizations TEXT, is_active TEXT,
            first_seen_at TIMESTAMP, last_seen_at TIMESTAMP,
            last_notified_at TIMESTAMP, raw_data_json TEXT)""",
        """CREATE TABLE system_state (
            key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)""",
        """CREATE TABLE pending_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT,
            notification_type TEXT, changes_json TEXT, created_at TIMESTAMP,
            FOREIGN KEY(company_id) REFERENCES companies(id))""",
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL COLLATE NOCASE,
            graduation_year INTEGER NOT NULL CHECK (graduation_year = 2028),
            branch_canonical TEXT NOT NULL, is_verified INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMP NOT NULL,
            verified_at TIMESTAMP, updated_at TIMESTAMP)""",
        """CREATE TABLE user_preferences (
            user_id INTEGER PRIMARY KEY, pref_internship INTEGER NOT NULL DEFAULT 1,
            pref_placement INTEGER NOT NULL DEFAULT 1, pref_ppo INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""",
        """CREATE TABLE action_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL, token_type TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP NOT NULL, used_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)""",
        """CREATE TABLE notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            company_id TEXT NOT NULL, notification_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', lease_expires_at TIMESTAMP,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5, last_attempt_at TIMESTAMP,
            error_message TEXT, created_at TIMESTAMP NOT NULL, sent_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            CONSTRAINT uq_delivery_user_company_type UNIQUE (user_id, company_id, notification_type))""",
    ]


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy-deployed.sqlite"
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=20)).isoformat()
    with closing(sqlite3.connect(str(path))) as conn:
        for statement in _legacy_schema_statements():
            conn.execute(statement)
        def seed_company(cid, name):
            raw = json.dumps({"id": cid, "company": name, "isactive": "True",
                              "tpoprogram": "VIT-BTech-Computer Engineering",
                              "organization": "VIT", "regEnddate": "14-Oct-2026"})
            conn.execute(
                "INSERT INTO companies (id, company, is_active, first_seen_at, last_seen_at,"
                " eligible_programs, organizations, raw_data_json) VALUES (?,?,?,?,?,?,?,?)",
                (cid, name, "True", now.isoformat(), now.isoformat(),
                 "VIT-BTech-Computer Engineering", "VIT", raw))

        # One company per delivery state: the unique (user, company, type)
        # constraint means a single pair cannot hold several states.
        seed_company(CID, "Fictional Legacy Employer")
        seed_company("SYNTHETIC-LEGACY-B", "Fictional Company B")
        seed_company("SYNTHETIC-LEGACY-C", "Fictional Company C")
        seed_company("SYNTHETIC-LEGACY-D", "Fictional Company D")
        conn.execute(
            "INSERT INTO users (email, graduation_year, branch_canonical, is_verified,"
            " is_active, created_at) VALUES (?,?,?,?,1,?)",
            (EMAIL, 2028, "VIT_CE", 1, now.isoformat()))
        conn.execute(
            "INSERT INTO user_preferences (user_id, pref_internship, pref_placement,"
            " pref_ppo, updated_at) VALUES (1,1,1,1,?)", (now.isoformat(),))
        # Delivery states: historical SENT NEW (A), PENDING UPDATE (B),
        # PROCESSING UPDATE with an unexpired claim (C), terminal FAILED UPDATE (D).
        conn.execute(
            "INSERT INTO notification_deliveries (user_id, company_id, notification_type,"
            " status, attempt_count, created_at, sent_at) VALUES (1,?,'NEW','SENT',1,?,?)",
            (CID, now.isoformat(), now.isoformat()))
        conn.execute(
            "INSERT INTO notification_deliveries (user_id, company_id, notification_type,"
            " status, attempt_count, created_at) VALUES (1,?,'UPDATE','PENDING',0,?)",
            ("SYNTHETIC-LEGACY-B", now.isoformat()))
        conn.execute(
            "INSERT INTO notification_deliveries (user_id, company_id, notification_type,"
            " status, attempt_count, lease_expires_at, created_at)"
            " VALUES (1,?,'UPDATE','PROCESSING',2,?,?)",
            ("SYNTHETIC-LEGACY-C", future, now.isoformat()))
        conn.execute(
            "INSERT INTO notification_deliveries (user_id, company_id, notification_type,"
            " status, attempt_count, error_message, created_at)"
            " VALUES (1,?,'UPDATE','FAILED',5,'fictional exhaustion',?)",
            ("SYNTHETIC-LEGACY-D", now.isoformat()))
        conn.execute("UPDATE notification_deliveries SET max_attempts=5")
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES"
            " ('baseline_initialized','true',?),"
            " ('last_successful_watcher_run',?,?),"
            " ('consecutive_watcher_failures','5',?),"
            " ('notification:v1:claim:2','legacyclaimtoken',?),"
            " ('notification:v1:ambiguous_acceptance:99','2026-10-24T00:00:00+00:00',?)",
            (now.isoformat(), now.isoformat(), now.isoformat(), now.isoformat(),
             now.isoformat(), now.isoformat()))
        conn.execute(
            "INSERT INTO pending_notifications (company_id, notification_type,"
            " changes_json, created_at) VALUES (?,?,?,?)",
            ("SYNTHETIC-LEGACY-B", "UPDATE",
             json.dumps(["Registration field regEnddate changed."]), now.isoformat()))
        conn.commit()
    return str(path)


def _counts(path):
    with closing(sqlite3.connect(path)) as conn:
        return {
            "companies": conn.execute("SELECT count(*) FROM companies").fetchone()[0],
            "users": conn.execute("SELECT count(*) FROM users").fetchone()[0],
            "deliveries": conn.execute(
                "SELECT status, count(*) FROM notification_deliveries GROUP BY status").fetchall(),
            "pending_admin": conn.execute(
                "SELECT count(*) FROM pending_notifications").fetchone()[0],
            "baseline": conn.execute(
                "SELECT value FROM system_state WHERE key='baseline_initialized'").fetchone()[0],
        }


def test_gate_c_migration_preserves_data_and_is_idempotent(legacy_db):
    before = _counts(legacy_db)
    repo = DatabaseRepository(legacy_db)          # runs init + migrations once
    with closing(sqlite3.connect(legacy_db)) as conn:
        schema_first = conn.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        # New additive structures exist; legacy obligation snapshots stay NULL
        # (no fabricated history).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_notifications)")}
        assert "event_snapshot_json" in cols
        assert conn.execute(
            "SELECT count(*) FROM pending_notifications WHERE event_snapshot_json IS NOT NULL"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM notification_delivery_outcome_log").fetchone()[0] == 0
        # Downgrade compatibility: every added column is nullable, so the old
        # application's INSERTs (which omit it) remain valid.
        for table, column in (("pending_notifications", "event_snapshot_json"),):
            info = [r for r in conn.execute(f"PRAGMA table_info({table})") if r[1] == column]
            assert info and info[0][3] == 0, f"{column} must stay nullable"
    DatabaseRepository(legacy_db)                 # idempotent second initialization
    with closing(sqlite3.connect(legacy_db)) as conn:
        schema_second = conn.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    assert schema_first == schema_second
    assert _counts(legacy_db) == before
    assert repo.is_baseline_initialized() is True


@pytest.mark.asyncio
async def test_gate_c_new_worker_does_not_replay_history(legacy_db, monkeypatch):
    repo = DatabaseRepository(legacy_db)
    from app.subscribers.worker import DeliveryWorker

    sent = []

    class Notifier:
        def _send_email_to(self, to_email, subject, html, extra_headers=None, **kw):
            sent.append(subject)
            return True

    worker = DeliveryWorker(repo.deliveries, repo.tokens, Notifier(), send_interval_seconds=0)
    delivered = await worker.process_batch_once()

    # Only the PENDING row dispatches. The unexpired PROCESSING claim, the
    # historical SENT row and the terminal FAILED row are untouched.
    assert delivered == 1
    assert len(sent) == 1
    with closing(repo._get_conn()) as conn:
        statuses = dict(conn.execute(
            "SELECT id, status FROM notification_deliveries").fetchall())
        assert statuses == {1: "SENT", 2: "SENT", 3: "PROCESSING", 4: "FAILED"}
        # The requeued PENDING→SENT row consumed the sole obligation; no NEW
        # (id 1) re-dispatch occurred (sent count == 1 proves it).
        snapshots = conn.execute(
            "SELECT count(*) FROM pending_notifications WHERE event_snapshot_json IS NOT NULL"
        ).fetchone()[0]
        assert snapshots == 0


@pytest.mark.asyncio
async def test_gate_c_legacy_obligation_converges_with_documented_edge(legacy_db, monkeypatch):
    """Legacy admin obligation + a snapshot-less delivery row.

    The deployed worker always writes raw_data_snapshot on enqueue, so NULL
    snapshots are a theoretical pre-fanout-era shape. Characterized behavior:
    the fanout defers behind the in-flight row, the row sends, the obligation
    requeues once (one legacy-edge duplicate under the approved at-least-once
    contract), then converges — no loop, no fabricated pending_notifications
    snapshots, exactly one admin delivery."""
    from app.monitoring.watcher import PlacementWatcher
    from app.monitoring import watcher as watcher_module

    monkeypatch.setattr(watcher_module, "AuthManager", lambda **kw: None)
    monkeypatch.setattr(watcher_module, "TimezoneScheduler", lambda **kw: None)

    class Notifier:
        def __init__(self):
            self.student_sends = 0
            self.admin_outcomes = []

        def _send_email_to(self, to_email, subject, html, extra_headers=None, **kw):
            self.student_sends += 1
            return True

        def send_update_notification(self, company, changes):
            self.admin_outcomes.append((company.company, changes))
            return True

    notifier = Notifier()
    repo = DatabaseRepository(legacy_db)
    monkeypatch.setattr(watcher_module, "EmailNotificationProvider", lambda: notifier)
    watcher = PlacementWatcher(db=repo)
    watcher.delivery_worker.send_interval_seconds = 0

    # Pass 1: fanout defers behind the snapshot-less PENDING row.
    await watcher._process_notifications()
    assert notifier.admin_outcomes == []
    assert len(repo.get_pending_notifications()) == 1
    assert await watcher.delivery_worker.process_batch_once() == 1

    # Pass 2: obligation requeues the row (legacy duplicate) and admin sends.
    await watcher._process_notifications()
    assert notifier.admin_outcomes == [("Fictional Company B",
                                        ["Registration field regEnddate changed."])]
    assert repo.get_pending_notifications() == []
    assert await watcher.delivery_worker.process_batch_once() == 1

    # Convergence: no further obligations, rows or sends.
    await watcher._process_notifications()
    assert await watcher.delivery_worker.process_batch_once() == 0
    assert notifier.student_sends == 2  # one legacy-edge duplicate, documented
    with closing(repo._get_conn()) as conn:
        snapshots = [r[0] for r in conn.execute(
            "SELECT raw_data_snapshot FROM notification_deliveries WHERE id=2")]
        assert snapshots[0] is not None  # row now carries its event snapshot
