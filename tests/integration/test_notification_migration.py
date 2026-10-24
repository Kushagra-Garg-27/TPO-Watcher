"""Fictional additive migration preservation; actual legacy schema, disposable only."""
import hashlib
import importlib.util
import json
import sqlite3

import pytest
from app.database.repository import DatabaseRepository

LEGACY = ("companies", "users", "user_preferences", "action_tokens",
          "notification_deliveries", "pending_notifications", "system_state")


def digest(conn):
    return hashlib.sha256(json.dumps({t: [tuple(r) for r in conn.execute("SELECT * FROM " + t + " ORDER BY 1")]
                                     for t in LEGACY}, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "history.sqlite"
    DatabaseRepository(str(path))
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO companies(id,company,first_seen_at,last_seen_at,raw_data_json) VALUES('legacy-a','Fictional','old-first','old-last','{opaque:fictional}')")
        c.execute("INSERT INTO users(email,graduation_year,branch_canonical,is_verified,is_active,created_at) VALUES('fixture@example.invalid',2028,'CS',1,1,'old-user')")
        c.execute("INSERT INTO user_preferences VALUES(1,1,1,1,'old-pref')")
        c.execute("INSERT INTO action_tokens(user_id,token_hash,token_type,expires_at,created_at) VALUES(1,'fictional-not-a-token','verify','old-expiry','old-token')")
        for i, (status, attempts, lease) in enumerate([
                ("SENT", 1, None), ("FAILED", 5, None), ("PENDING", 0, None),
                ("PENDING", 5, None), ("PROCESSING", 1, "2099-valid"),
                ("PROCESSING", 2, "2000-expired")]):
            c.execute("INSERT INTO notification_deliveries(user_id,company_id,notification_type,status,attempt_count,lease_expires_at,created_at) VALUES(1,'legacy-a',?,?,?,?, 'old-delivery')",
                      ("fixture-" + str(i), status, attempts, lease))
        c.execute("INSERT INTO pending_notifications(company_id,notification_type,changes_json,created_at) VALUES('legacy-a','NEW','fictional-json','old-admin')")
        c.execute("INSERT INTO system_state VALUES('fictional','preserve','old-state')")
    return path


def impl():
    assert importlib.util.find_spec("app.database.notification_migrator") is not None, "S1 migration is missing"
    from app.database import notification_migrator
    return notification_migrator


def test_v10_preservation_baseline_and_rerun(history):
    m = impl()
    with m.connect_disposable(history) as c:
        before = digest(c)
        m.create_schema(c, now_ms=1000)
        assert digest(c) == before
        assert c.execute("SELECT revision,head_event_id FROM opportunity_state").fetchone()[:] == (0, None)
        assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM notification_intents").fetchone()[0] == 0
        assert c.execute("SELECT decision,count(*) FROM legacy_notification_dispositions GROUP BY decision ORDER BY decision").fetchall()[0][:] == ("HISTORICAL_ONLY", 2)
        assert c.execute("SELECT count(*) FROM legacy_notification_dispositions WHERE decision='HOLD_REVIEW'").fetchone()[0] == 5
        assert c.execute("SELECT consent_epoch,profile_epoch FROM subscriber_versions").fetchone()[:] == (0,0)
        m.create_schema(c, now_ms=2000)
        assert digest(c) == before
        assert c.execute("SELECT count(*) FROM legacy_notification_dispositions").fetchone()[0] == 7
        assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0] == 0


def test_v10_interrupted_migration_is_atomic_and_retryable(history):
    m = impl()
    with m.connect_disposable(history) as c:
        before = digest(c)
        def fail(stage):
            if stage == "seeded":
                raise RuntimeError("injected migration interruption")
        with pytest.raises(RuntimeError, match="injected"):
            m.create_schema(c, now_ms=1000, fault=fail)
        assert digest(c) == before
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0] == 0
        m.create_schema(c, now_ms=1000)
        assert digest(c) == before


def test_v10_checksum_mismatch_rejected(history):
    m = impl()
    with m.connect_disposable(history) as c:
        m.create_schema(c, now_ms=1000)
        # Simulate externally corrupted metadata without weakening normal guards.
        c.execute("DROP TRIGGER notification_schema_migrations_no_update")
        c.execute("UPDATE notification_schema_migrations SET checksum='wrong'")
        with pytest.raises(m.SchemaError, match="checksum"):
            m.create_schema(c, now_ms=1000)


@pytest.mark.parametrize("mutation", [
    "UPDATE notification_deliveries SET status='UNRECOGNIZED' WHERE id=1",
    "UPDATE notification_deliveries SET attempt_count=-1 WHERE id=1",
    "UPDATE notification_deliveries SET attempt_count=8 WHERE id=1",
    "UPDATE pending_notifications SET company_id=NULL",
    "UPDATE notification_deliveries SET company_id='missing' WHERE id=1",
    "UPDATE notification_deliveries SET user_id=999 WHERE id=1",
])
def test_v10_abnormal_legacy_rows_stop_without_loss(history, mutation):
    m = impl()
    with m.connect_disposable(history) as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute(mutation)
        c.execute("PRAGMA foreign_keys=ON")
        before = digest(c)
        with pytest.raises(m.SchemaError, match="legacy"):
            m.create_schema(c, now_ms=1000)
        assert digest(c) == before
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='opportunity_events'").fetchone()[0] == 0

def test_v10_legacy_catchup_has_explicit_provenance_no_invented_event(history):
    m=impl()
    from app.database.event_repository import EventRepository
    with m.connect_disposable(history) as c:
        m.create_schema(c,now_ms=1000)
        repo=EventRepository(c,clock=lambda:1000)
        legacy=c.execute("SELECT * FROM legacy_notification_dispositions WHERE decision='HOLD_REVIEW' LIMIT 1").fetchone()
        auth=repo.approve("LEGACY_CATCHUP",legacy["company_key"],legacy["disposition_id"])
        key=repo.catchup(legacy["disposition_id"],auth)
        assert repo.catchup(legacy["disposition_id"],auth)==key
        assert c.execute("SELECT kind,source_event_id,legacy_disposition_id FROM notification_intents").fetchone()[:]==("LEGACY_CATCHUP",None,legacy["disposition_id"])
        assert {r[0] for r in c.execute("SELECT status FROM event_outbox")}=={"HELD"}
        assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0


def test_v10_company_and_subscriber_hard_delete_restrictions(history):
    m=impl()
    with m.connect_disposable(history) as c:
        m.create_schema(c,now_ms=1000)
        with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
            c.execute("DELETE FROM companies WHERE id='legacy-a'")
        with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
            c.execute("DELETE FROM users WHERE id=1")
        assert c.execute("SELECT count(*) FROM companies").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM users").fetchone()[0]==1

def test_v10_first_material_transition_of_seeded_company_is_update(history):
    m=impl()
    from app.database.event_repository import EventRepository
    with m.connect_disposable(history) as c:
        m.create_schema(c,now_ms=1000)
        repo=EventRepository(c,clock=lambda:1000)
        repo.activate()
        owner=repo.acquire_intake()
        batch=repo.admit(owner,admission_key="legacy-change",items=[dict(id="legacy-a",state="changed",branch="CS")],complete=False)
        event=repo.apply_item(owner,batch,0)
        repo.finalize_batch(owner,batch)
        assert json.loads(c.execute("SELECT reasons_json FROM opportunity_events WHERE event_id=?",(event,)).fetchone()[0])==["UPDATE"]
        assert c.execute("SELECT first_seen_at FROM companies WHERE id='legacy-a'").fetchone()[0]=="old-first"
        assert c.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0]==6
