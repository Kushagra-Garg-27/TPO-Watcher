"""Guarded fictional schema tests; no production database or transport."""
import importlib.util
import sqlite3

import pytest
from app.database.repository import DatabaseRepository


@pytest.fixture
def legacy_path(tmp_path):
    path = tmp_path / "fictional.sqlite"
    DatabaseRepository(str(path))
    return path


def implementation():
    assert importlib.util.find_spec("app.database.notification_migrator") is not None, "S1 schema implementation is missing"
    from app.database import notification_migrator
    return notification_migrator


def test_v01_schema_creation_and_repeat_verification(legacy_path):
    m = implementation()
    with m.connect_disposable(legacy_path) as conn:
        m.create_schema(conn, now_ms=1000)
        evidence = m.verify_schema(conn)
        assert evidence["tables"] == 27
        assert evidence["triggers"] == 85
        assert evidence["foreign_keys"] == 1
        assert evidence["journal_mode"] == "wal"
        assert evidence["busy_timeout"] == 100
        assert sqlite3.sqlite_version_info >= (3, 37, 0)
        assert conn.execute("SELECT writer_mode,fanout_mode,send_mode FROM notification_runtime").fetchone()[:] == ("OFF", "OFF", "OFF")
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        m.create_schema(conn, now_ms=2000)
        assert conn.execute("SELECT count(*) FROM notification_schema_migrations").fetchone()[0] == 1
        assert m.SOURCE_SQL_SHA256 == "D99A1EA79BFBD5A47D135A34602BBBE06B5DBA276D910562AE12A188D370081D"
        print("V01", evidence)


def test_v01_shape_tampering_is_rejected(legacy_path):
    m = implementation()
    with m.connect_disposable(legacy_path) as conn:
        m.create_schema(conn, now_ms=1000)
        conn.execute("DROP TRIGGER outbox_insert_guard")
        with pytest.raises(m.SchemaError, match="shape"):
            m.verify_schema(conn)


def test_v01_strict_null_and_enum_constraints(legacy_path):
    m = implementation()
    with m.connect_disposable(legacy_path) as conn:
        m.create_schema(conn, now_ms=1000)
        for values in [(None, "SOURCE", 1), ("x", "WRONG", 1), ("x", "SOURCE", -1), ("x", "SOURCE", "bad")]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO payload_tombstones VALUES(?,?,?)", values)


def test_v01_import_has_no_schema_side_effects(legacy_path):
    m = implementation()
    with m.connect_disposable(legacy_path) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0] == 0

# Transaction contracts use the actual isolated repository, never copied algorithms.
from contextlib import contextmanager
from dataclasses import replace
from time import perf_counter
import threading


class Clock:
    def __init__(self):
        self.now = 1000
    def __call__(self):
        return self.now


def test_transaction_implementation_is_available():
    assert importlib.util.find_spec("app.database.event_repository") is not None, "T0-T7 implementation is missing"


@pytest.fixture
def model(legacy_path):
    m = implementation()
    assert importlib.util.find_spec("app.database.event_repository") is not None, "T0-T7 implementation is missing"
    from app.database.event_repository import EventRepository
    with m.connect_disposable(legacy_path) as conn:
        m.create_schema(conn, now_ms=1000)
        clock = Clock()
        repo = EventRepository(conn, clock=clock)
        repo.activate()
        yield repo, conn, clock, legacy_path


def observed(repo, state="A", *, company="company-a", branch="CS", **extra):
    owner = repo.acquire_intake()
    batch = repo.admit(owner, admission_key=repo.new_key(), items=[dict(id=company,state=state,branch=branch,**extra)], complete=True)
    event = repo.apply_item(owner, batch, 0)
    repo.finalize_batch(owner, batch)
    return event


def intent(conn, event):
    return conn.execute("SELECT intent_id FROM notification_intents WHERE source_event_id=? AND kind='SOURCE_EVENT'", (event,)).fetchone()[0]


def queued(repo, conn, event, channel="SUBSCRIBER", **kwargs):
    owner = repo.claim_outbox(intent(conn,event), channel)
    repo.seal(owner)
    while conn.execute("SELECT status FROM event_outbox WHERE intent_id=? AND channel=?", (owner.key[0],channel)).fetchone()[0] != "COMPLETE":
        repo.fanout_page(owner, **kwargs)
    return conn.execute("SELECT delivery_id FROM notification_deliveries_v2 WHERE intent_id=? AND channel=? ORDER BY delivery_id", owner.key).fetchall()


def sending(repo, conn, *, end=False):
    claim = repo.claim_delivery()
    assert claim is not None
    permit = repo.authorize_send(claim)
    assert permit is not None
    return claim, permit


def state(conn, delivery):
    return dict(conn.execute("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?", (delivery,)).fetchone())


def gate(conn, account="account-1"):
    return dict(conn.execute("SELECT * FROM smtp_account_gate WHERE account_key=?", (account,)).fetchone())


def facts(repo, permit, *kinds):
    for kind in kinds:
        repo.record_fact(permit.invocation_id, kind, fact_key=kind)


def test_v06_f02_a_b_a_and_prior_sent_update(model):
    repo,c,clock,_ = model
    repo.add_user()
    events = []
    for value in ("A","B","A"):
        event = observed(repo,value)
        events.append(event)
        assert len(queued(repo,c,event)) == 1
        claim,permit = sending(repo,c)
        facts(repo,permit,"ACCEPTED","ENDED")
        assert repo.acknowledge(claim) == "SENT"
    assert len(set(events)) == 3
    assert [r[0] for r in c.execute("SELECT revision FROM opportunity_events ORDER BY revision")] == [1,2,3]
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0] == 3
    assert observed(repo,"A") is None
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 3


@pytest.mark.parametrize("step", ["legacy","baseline","event","routes","intent","outboxes","head","checkpoint"])
def test_f06_every_write_step_rolls_back_and_resume_is_idempotent(model,step):
    repo,c,clock,_ = model
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="atomic",items=[dict(id="company-a",state="A",branch="CS")],complete=True)
    def fail(stage):
        if stage == step:
            raise RuntimeError("injected " + step)
    with pytest.raises(RuntimeError,match="injected"):
        repo.apply_item(h,b,0,fault=fail)
    for table in ("companies","opportunity_state","opportunity_events","notification_intents","event_outbox"):
        assert c.execute("SELECT count(*) FROM "+table).fetchone()[0] == 0
    assert c.execute("SELECT status FROM source_batch_items").fetchone()[0] == "PENDING"
    event=repo.apply_item(h,b,0)
    assert repo.apply_item(h,b,0) == event
    repo.finalize_batch(h,b)
    assert repo.apply_item(h,b,0) == event
    assert c.execute("SELECT count(*) FROM event_outbox").fetchone()[0] == 2
    assert observed(repo,"A") is None
    assert c.execute("SELECT count(*) FROM event_outbox").fetchone()[0] == 2


def test_f06_unknown_commit_inspects_stable_checkpoint(model):
    repo,c,clock,path=model
    from app.database.event_repository import CommitUnknown
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="ack-loss",items=[dict(id="company-a",state="A",branch="CS")],complete=True)
    with pytest.raises(CommitUnknown):
        repo.apply_item(h,b,0,lose_ack=True)
    from app.database.event_repository import EventRepository
    with implementation().connect_disposable(path) as other:
        recovered=EventRepository(other,clock=clock)
        event=recovered.inspect_item(b,0)
        assert recovered.apply_item(h,b,0)==event
        assert other.execute("SELECT count(*) FROM event_outbox").fetchone()[0]==2
    assert event
    assert repo.apply_item(h,b,0)==event
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert c.execute("SELECT count(*) FROM event_outbox").fetchone()[0]==2


def test_v05_fetch_resume_lease_fencing_and_finalization(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    first=repo.acquire_intake(lease_ms=50)
    b=repo.admit(first,admission_key="resume",items=[dict(id="company-a",state="A",branch="CS")],complete=True)
    with pytest.raises(Fenced):
        repo.acquire_intake()
    clock.now+=51
    second=repo.acquire_intake()
    assert second.generation>first.generation
    assert c.execute("SELECT intake_generation FROM source_batches").fetchone()[0]==first.generation
    with pytest.raises(Fenced):
        repo.apply_item(first,b,0)
    repo.apply_item(second,b,0)
    repo.finalize_batch(second,b)
    repo.finalize_batch(second,b)
    assert c.execute("SELECT active_batch_id,intake_token,intake_lease_ms FROM notification_runtime").fetchone()[:] == (None,None,None)


def test_v05_poison_prevents_early_and_late_absence(model):
    repo,c,clock,_=model
    from app.database.event_repository import PolicyDenied
    observed(repo,company="missing-company")
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="poison",items=[
        dict(id="good",state="A",branch="CS"),
        dict(id="poison",state="bad",branch="CS",valid=False)],complete=True)
    missing=c.execute("SELECT company_key FROM opportunity_state WHERE legacy_company_id='missing-company'").fetchone()[0]
    with pytest.raises(PolicyDenied):
        repo.reconcile(h,b,missing)
    repo.apply_item(h,b,0)
    with pytest.raises(PolicyDenied):
        repo.reconcile(h,b,missing)
    repo.apply_item(h,b,1)
    with pytest.raises(PolicyDenied):
        repo.reconcile(h,b,missing)
    repo.finalize_batch(h,b)
    assert c.execute("SELECT status,reconciliation_allowed FROM source_batches WHERE batch_id=?",(b,)).fetchone()[:]==("COMPLETE_WITH_HOLDS",0)
    assert c.execute("SELECT count(*) FROM opportunity_events WHERE origin='RECONCILE'").fetchone()[0]==0
    assert c.execute("SELECT status FROM source_reconciliation_work WHERE batch_id=?",(b,)).fetchone()[0]=="HELD"


def test_v05_complete_reconciliation_and_partial_source(model):
    repo,c,clock,_=model
    observed(repo,company="one")
    key=c.execute("SELECT company_key FROM opportunity_state").fetchone()[0]
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="absence",items=[],complete=True)
    event=repo.reconcile(h,b,key)
    repo.finalize_batch(h,b)
    assert c.execute("SELECT origin FROM opportunity_events WHERE event_id=?",(event,)).fetchone()[0]=="RECONCILE"
    h=repo.acquire_intake()
    p=repo.admit(h,admission_key="partial",items=[],complete=False)
    from app.database.event_repository import PolicyDenied
    with pytest.raises(PolicyDenied):
        repo.reconcile(h,p,key)
    repo.finalize_batch(h,p)
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==2


@pytest.mark.parametrize("items,max_items", [
    ([dict(id="a",state="A"),dict(id="a",state="B")],100),
    ([dict(state="A")],100),
    ([dict(id="a",state="A"),dict(id="b",state="B")],1)])
def test_v05_global_hold_no_truncation_and_scoped_recovery(model,items,max_items):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval,PolicyDenied
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="held",items=items,complete=True,max_items=max_items)
    assert c.execute("SELECT status FROM source_batches").fetchone()[0]=="HELD_GLOBAL"
    assert c.execute("SELECT count(*) FROM source_batch_items").fetchone()[0]==len(items)
    with pytest.raises(PolicyDenied):
        repo.apply_item(h,b,0)
    with pytest.raises(PolicyDenied):
        repo.recover_batch(h,b,None)
    repo.recover_batch(h,b,OperatorApproval("RECOVER_BATCH",b,"fake-one-use"))
    # Hold recovery cannot restore discarded absence authority or duplicate identity trust.
    assert c.execute("SELECT reconciliation_allowed FROM source_batches").fetchone()[0]==0


def test_v04_coherent_seal_zero_duplicate_and_no_partial_commit(model):
    repo,c,clock,_=model
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    assert repo.seal(h)==0
    assert c.execute("SELECT status,audience_count FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(intent(c,event),)).fetchone()[:]==("COMPLETE",0)
    repo.add_user()
    event=observed(repo,"B")
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    def fail(stage):
        if stage=="audience":
            raise RuntimeError("cohort interruption")
    with pytest.raises(RuntimeError,match="cohort"):
        repo.seal(h,fault=fail)
    assert c.execute("SELECT count(*) FROM event_audience WHERE intent_id=?",(h.key[0],)).fetchone()[0]==0
    assert repo.seal(h)==1
    assert repo.seal(h)==1
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO event_audience(intent_id,channel,recipient_key,role,selected_branch,consent_epoch,profile_epoch,sealed_policy_version,created_ms) SELECT intent_id,channel,recipient_key,role,selected_branch,consent_epoch,profile_epoch,sealed_policy_version,created_ms FROM event_audience LIMIT 1")


def test_v04_sparse_page_rollback_and_expired_owner(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    for _ in range(3):
        repo.add_user()
    repo.add_admin()
    event=observed(repo)
    admin=repo.claim_outbox(intent(c,event),"ADMIN")
    repo.seal(admin) # Interleaved global audience IDs make subscriber IDs non-counts.
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER",lease_ms=50)
    repo.seal(h)
    def fail(stage):
        if stage=="delivery":
            raise RuntimeError("page interruption")
    with pytest.raises(RuntimeError,match="page"):
        repo.fanout_page(h,fault=fail)
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
    assert c.execute("SELECT processed_count,last_audience_id FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(h.key[0],)).fetchone()[:]==(0,0)
    clock.now+=51
    with pytest.raises(Fenced):
        repo.fanout_page(h)
    resumed=repo.claim_outbox(h.key[0],"SUBSCRIBER")
    assert repo.fanout_page(resumed)==3
    row=c.execute("SELECT processed_count,last_audience_id FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(h.key[0],)).fetchone()
    assert row[0]==3 and row[1]>3
    assert repo.fanout_page(resumed)==0


@pytest.mark.parametrize("outcomes,expected", [
    (("ACCEPTED",),"SENT"),(("NOT_ACCEPTED",),"PENDING"),(("UNKNOWN",),"UNCERTAIN"),
    (("UNKNOWN","ACCEPTED"),"UNCERTAIN"),(("ACCEPTED","UNKNOWN"),"UNCERTAIN"),
    (("NOT_ACCEPTED","ACCEPTED"),"UNCERTAIN")])
def test_v08_real_journal_classification_and_end(outcomes,expected,model):
    repo,c,clock,_=model
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,permit=sending(repo,c)
    facts(repo,permit,*outcomes)
    assert gate(c)["state"]=="INFLIGHT"
    facts(repo,permit,"ENDED")
    assert gate(c)["state"]=="FREE"
    assert repo.acknowledge(claim)==expected
    d=state(c,claim.key)
    assert d["status"]==expected
    assert d["claim_token"] is None and d["lease_ms"] is None
    if expected=="UNCERTAIN":
        assert d["invocation_id"]==permit.invocation_id
        assert repo.claim_delivery() is None


def test_v08_expired_send_late_end_epoch_and_generation_reuse(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    repo.add_user()
    queued(repo,c,observed(repo))
    old,permit=sending(repo,c)
    clock.now=old.lease_ms+1
    assert repo.recover_delivery(old.key)=="UNCERTAIN"
    assert gate(c)["state"]=="BLOCKED"
    with pytest.raises(Fenced):
        repo.acknowledge(old)
    repo.bump_epoch()
    facts(repo,permit,"UNKNOWN","ENDED")
    assert state(c,old.key)["status"]=="UNCERTAIN"
    assert gate(c)["state"]=="FREE"
    queued(repo,c,observed(repo,"B"))
    newer=repo.claim_delivery()
    assert newer is not None and gate(c)["generation"]>old.gate_generation
    assert repo.release_ended(permit.invocation_id) is False
    assert gate(c)["state"]=="RESERVED"


def test_v03_claim_cap_recovery_and_illegal_terminal_reset(model):
    repo,c,clock,_=model
    repo.add_user()
    ids=queued(repo,c,observed(repo))
    delivery=ids[0][0]
    for n in range(1,6):
        clock.now=max(clock.now,state(c,delivery)["next_ms"])
        claim=repo.claim_delivery(lease_ms=10)
        assert claim is not None
        assert state(c,delivery)["attempt_count"]==n
        clock.now=claim.lease_ms+1
        expected="FAILED" if n==5 else "PENDING"
        assert repo.recover_delivery(delivery)==expected
    assert repo.claim_delivery() is None
    assert c.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0]==5
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE notification_deliveries_v2 SET status='PENDING',finished_ms=NULL WHERE delivery_id=?",(delivery,))
    assert state(c,delivery)["status"]=="FAILED"


def test_v08_single_use_fake_permit_account_and_terminal_late_conflict(model):
    repo,c,clock,_=model
    from app.database.event_repository import FakeTransport,PolicyDenied
    h=repo.reserve_account()
    permit=repo.authorize_account(h)
    fake=FakeTransport("UNKNOWN")
    repo.submit_fake(permit,fake)
    with pytest.raises(PolicyDenied):
        repo.submit_fake(permit,fake)
    assert len(fake.calls)==1
    facts(repo,permit,"UNKNOWN")
    assert gate(c)["state"]=="INFLIGHT"
    facts(repo,permit,"ENDED")
    assert gate(c)["state"]=="FREE"
    assert c.execute("SELECT message_class,delivery_id FROM transport_invocations").fetchone()[:]==("ACCOUNT",None)
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,p=sending(repo,c)
    facts(repo,p,"ACCEPTED","ENDED")
    repo.acknowledge(claim)
    facts(repo,p,"UNKNOWN") # weaker terminal observation
    assert c.execute("SELECT send_mode FROM notification_runtime").fetchone()[0]=="V2"
    facts(repo,p,"NOT_ACCEPTED") # authoritative terminal contradiction
    assert c.execute("SELECT send_mode FROM notification_runtime").fetchone()[0]=="OFF"
    assert state(c,claim.key)["status"]=="SENT"


def test_v06_manual_redrive_approval_idempotency_no_revision(model):
    repo,c,clock,_=model
    from app.database.event_repository import PolicyDenied
    repo.add_user()
    event=observed(repo)
    delivery=queued(repo,c,event)[0][0]
    with pytest.raises(PolicyDenied):
        repo.redrive(delivery,None)
    company=state(c,delivery)["company_key"]
    approval=repo.approve("REDRIVE",company,str(delivery))
    new=repo.redrive(delivery,approval)
    assert repo.redrive(delivery,approval)==new
    assert c.execute("SELECT revision FROM opportunity_state").fetchone()[0]==1
    assert c.execute("SELECT count(*) FROM notification_intents").fetchone()[0]==2
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    wrong=repo.approve("REDRIVE",company,"999")
    with pytest.raises(PolicyDenied):
        repo.redrive(delivery,wrong)


def test_v09_off_on_profile_prefs_and_sending_cancel(model):
    repo,c,clock,_=model
    key=repo.add_user()
    user=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(key,)).fetchone()[0]
    first=queued(repo,c,observed(repo))[0][0]
    repo.change_user(user,is_active=0)
    assert state(c,first)["status"]=="SUPPRESSED"
    repo.change_user(user,is_active=1)
    assert state(c,first)["status"]=="SUPPRESSED"
    second=queued(repo,c,observed(repo,"B"))[0][0]
    claim,permit=sending(repo,c)
    repo.set_preferences(user,pref_internship=0)
    assert state(c,second)["status"]=="SENDING"
    assert state(c,second)["cancel_requested_ms"]==clock.now
    assert gate(c)["state"]=="BLOCKED"
    facts(repo,permit,"UNKNOWN","ENDED")
    assert repo.acknowledge(claim)=="UNCERTAIN"
    repo.set_preferences(user,pref_internship=1)
    third=queued(repo,c,observed(repo,"C"))[0][0]
    repo.change_user(user,branch_canonical="ME")
    assert state(c,third)["status"]=="SUPPRESSED"
    assert c.execute("SELECT updated_ms FROM subscriber_versions WHERE user_id=?",(user,)).fetchone()[0]==clock.now


def test_v09_privacy_erasure_no_rebinding_and_hard_delete_limit(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval,PolicyDenied
    key=repo.add_user()
    user=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(key,)).fetchone()[0]
    event=observed(repo)
    delivery=queued(repo,c,event)[0][0]
    snapshot=c.execute("SELECT after_payload_id FROM opportunity_events WHERE event_id=?",(event,)).fetchone()[0]
    with pytest.raises(PolicyDenied):
        repo.erase_payload(snapshot,None)
    repo.erase_payload(snapshot,OperatorApproval("ERASE",snapshot,"erase-1"))
    assert state(c,delivery)["status"]=="SUPPRESSED"
    assert c.execute("SELECT count(*) FROM payload_tombstones WHERE payload_id=?",(snapshot,)).fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO protected_payloads VALUES(?,x'01','fictional')",(snapshot,))
    repo.erase_recipient(key,OperatorApproval("DEIDENTIFY",key,"erase-2"))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO recipient_associations(recipient_key,user_id) VALUES(?,?)",(key,user))
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM users WHERE id=?",(user,))
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]


def test_v07_two_connections_threaded_claim_and_bounded_busy(model):
    repo,c,clock,path=model
    repo.add_user()
    queued(repo,c,observed(repo))
    m=implementation()
    from app.database.event_repository import EventRepository
    barrier=threading.Barrier(2)
    results=[]
    errors=[]
    def worker():
        try:
            with m.connect_disposable(path) as other:
                r=EventRepository(other,clock=clock)
                barrier.wait(timeout=3)
                results.append(r.claim_delivery())
        except BaseException as exc:
            errors.append(exc)
    threads=[threading.Thread(target=worker) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    assert errors==[]
    assert len([r for r in results if r is not None])==1
    assert c.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0]==1
    assert state(c,results[0].key if results[0] else results[1].key)["attempt_count"]==1
    with m.connect_disposable(path,busy_timeout_ms=25) as other:
        c.execute("BEGIN IMMEDIATE")
        try:
            start=perf_counter()
            with pytest.raises(sqlite3.OperationalError,match="locked"):
                other.execute("BEGIN IMMEDIATE")
            elapsed=perf_counter()-start
            assert elapsed<1
            print("V07",dict(connections=2,winners=1,attempts=1,busy_ms=round(elapsed*1000,3)))
        finally:
            c.rollback()


@pytest.mark.parametrize("size",[10,100,1000])
def test_v11_capacity_cohort_pages_wal_query_plan_and_bounded_quantum(model,size):
    repo,c,clock,path=model
    from app.database.event_repository import FakeTransport
    for _ in range(size):repo.add_user()
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    start=perf_counter()
    assert repo.seal(h)==size
    seal_ms=(perf_counter()-start)*1000
    assert seal_ms<500, "fictional observed seal envelope exceeded"
    pages=[]
    start=perf_counter()
    while c.execute("SELECT status FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(h.key[0],)).fetchone()[0]!="COMPLETE":
        pages.append(repo.fanout_page(h))
    page_ms=(perf_counter()-start)*1000
    assert sum(pages)==size and all(0<n<=100 for n in pages)
    fake=FakeTransport("NOT_ACCEPTED")
    drain_start=perf_counter()
    examined=repo.drain_fake(fake,quantum=25)
    drain_ms=(perf_counter()-drain_start)*1000
    assert examined==min(25,size)
    assert len(fake.calls)==examined
    assert gate(c)["state"]=="FREE"
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE attempt_count=1").fetchone()[0]==examined
    plan=[tuple(r) for r in c.execute("EXPLAIN QUERY PLAN SELECT audience_id FROM event_audience WHERE intent_id=? AND channel='SUBSCRIBER' AND audience_id>0 ORDER BY audience_id LIMIT 100",(h.key[0],))]
    assert any("INDEX" in str(r) for r in plan)
    pages_count=c.execute("PRAGMA page_count").fetchone()[0]
    page_size=c.execute("PRAGMA page_size").fetchone()[0]
    wal=path.with_name(path.name+"-wal")
    print("V11",dict(size=size,seal_ms=round(seal_ms,3),fanout_ms=round(page_ms,3),pages=pages,
                    db_bytes=pages_count*page_size,db_file_bytes=path.stat().st_size,
                    wal_bytes=wal.stat().st_size if wal.exists() else 0,drain_ms=round(drain_ms,3),
                    claims=examined,remaining=size-examined,query_plan=plan))

def test_v02_cross_scope_fk_checks_and_direct_initial_bypasses(model):
    repo,c,clock,_=model
    key=repo.add_user()
    a=observed(repo)
    # A partial observation has no absence-work obligation for other companies.
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="other",items=[dict(id="other",state="X",branch="CS")],complete=False)
    other=repo.apply_item(h,b,0)
    repo.finalize_batch(h,b)
    ordinary=intent(c,a)
    other_intent=intent(c,other)
    delivery=queued(repo,c,a)[0][0]
    source=c.execute("SELECT * FROM opportunity_events WHERE event_id=?",(a,)).fetchone()
    wrong=c.execute("SELECT company_key FROM opportunity_events WHERE event_id=?",(other,)).fetchone()[0]
    batch=source["batch_id"]
    neg=[
        ("UPDATE opportunity_state SET head_event_id=? WHERE company_key=?",(other,source["company_key"])),
        ("INSERT INTO notification_intents(intent_id,company_key,kind,source_event_id,snapshot_payload_id,policy_version,config_version,created_ms) VALUES('invalid',?,'SOURCE_EVENT',?,?,'F-S0','F-S0',1000)",(wrong,a,source["after_payload_id"])),
        ("UPDATE notification_deliveries_v2 SET audience_id=999 WHERE delivery_id=?",(delivery,)),
        ("UPDATE notification_deliveries_v2 SET intent_id=? WHERE delivery_id=?",(other_intent,delivery)),
        ("UPDATE notification_deliveries_v2 SET resolution_id='missing' WHERE delivery_id=?",(delivery,)),
        ("INSERT INTO event_outbox(intent_id,channel,status,runtime_epoch,next_ms,max_failures,created_ms,sealed_ms,audience_count,completed_ms) VALUES('missing','ADMIN','COMPLETE',0,1000,3,1000,1000,0,1000)",()),
        ("INSERT INTO source_batch_items(batch_id,item_index,payload_id,status,company_key,event_id,completed_ms) VALUES(?,100,?,'APPLIED',?,?,1000)",(batch,source["after_payload_id"],wrong,other)),
        ("INSERT INTO source_reconciliation_work(batch_id,company_key,status,event_id,completed_ms) VALUES(?,?,'APPLIED',?,1000)",(batch,wrong,other)),
        ("INSERT INTO delivery_attempts VALUES(?,1,1,'fake',0,'fake',1000,1100,'account-1',0,'fake-submission')",(delivery,)),
        ("UPDATE notification_deliveries_v2 SET max_attempts=6 WHERE delivery_id=?",(delivery,)),
        ("UPDATE notification_deliveries_v2 SET claim_token='orphan-token' WHERE delivery_id=?",(delivery,)),
        ("UPDATE notification_deliveries_v2 SET status='UNCERTAIN',finished_ms=1000 WHERE delivery_id=?",(delivery,)),
        ("UPDATE smtp_account_gate SET state='BLOCKED' WHERE account_key='account-1'",()),
    ]
    for sql,values in neg:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql,values)
    assert state(c,delivery)["status"]=="PENDING"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0]==1
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]


def test_v02_deferred_head_commit_rollback(model):
    repo,c,clock,_=model
    a=observed(repo)
    event=c.execute("SELECT * FROM opportunity_events").fetchone()
    from app.database.notification_migrator import atomic
    with pytest.raises(sqlite3.IntegrityError):
        with atomic(c):
            # Deferred positive head references a missing event; transaction must disappear.
            c.execute("DROP TRIGGER state_head_guard")
            c.execute("UPDATE opportunity_state SET revision=2,head_event_id='missing' WHERE company_key=?",(event["company_key"],))
    assert c.execute("SELECT revision,head_event_id FROM opportunity_state").fetchone()[:]==(1,a)
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='state_head_guard'").fetchone()[0]==1


@pytest.mark.parametrize("phase",["PENDING","PROCESSING","SENDING"])
def test_v09_opt_out_is_atomic_at_each_delivery_phase(model,phase):
    repo,c,clock,_=model
    key=repo.add_user()
    uid=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(key,)).fetchone()[0]
    delivery=queued(repo,c,observed(repo))[0][0]
    claim=repo.claim_delivery() if phase!="PENDING" else None
    permit=repo.authorize_send(claim) if phase=="SENDING" else None
    repo.change_user(uid,is_active=0)
    d=state(c,delivery)
    if phase=="SENDING":
        assert d["status"]=="SENDING" and d["cancel_requested_ms"]==clock.now
        assert gate(c)["state"]=="BLOCKED"
        facts(repo,permit,"NOT_ACCEPTED","ENDED")
        assert repo.acknowledge(claim)=="SUPPRESSED"
    else:
        assert d["status"]=="SUPPRESSED" and d["claim_token"] is None and d["lease_ms"] is None
        assert gate(c)["state"]=="FREE"
    repo.change_user(uid,is_active=1)
    assert state(c,delivery)["status"]=="SUPPRESSED"


def test_v03_five_definitive_rejections_exhaust_then_no_retry(model):
    repo,c,clock,_=model
    repo.add_user()
    delivery=queued(repo,c,observed(repo))[0][0]
    for n in range(1,6):
        clock.now=max(clock.now,state(c,delivery)["next_ms"])
        claim,permit=sending(repo,c)
        facts(repo,permit,"NOT_ACCEPTED","PROCESS_EXIT_CONFIRMED")
        assert repo.acknowledge(claim)==("FAILED" if n==5 else "PENDING")
        assert state(c,delivery)["attempt_count"]==n
    assert repo.claim_delivery() is None
    assert gate(c)["state"]=="FREE"


@pytest.mark.parametrize("confirm",[False,True])
def test_v08_operator_uncertainty_resolution_is_scoped_and_one_use(model,confirm):
    repo,c,clock,_=model
    from app.database.event_repository import PolicyDenied
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,p=sending(repo,c)
    facts(repo,p,"UNKNOWN","ENDED")
    repo.acknowledge(claim)
    d=state(c,claim.key)
    with pytest.raises(PolicyDenied):
        repo.resolve(claim.key,None,confirm_accepted=confirm)
    approval=repo.approve("RESOLVE",d["company_key"],str(claim.key))
    resolution=repo.resolve(claim.key,approval,confirm_accepted=confirm)
    assert repo.resolve(claim.key,approval,confirm_accepted=confirm)==resolution
    assert state(c,claim.key)["status"]==("SENT" if confirm else "UNCERTAIN")
    assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==1


def test_v08_unknown_claim_send_and_ack_commits_never_duplicate_invocation(model):
    repo,c,clock,_=model
    from app.database.event_repository import CommitUnknown
    repo.add_user()
    queued(repo,c,observed(repo))
    with pytest.raises(CommitUnknown) as error:
        repo.claim_delivery(lose_ack=True)
    claim=repo.inspect_claim(error.value.key)
    assert claim is not None
    with pytest.raises(CommitUnknown) as error:
        repo.authorize_send(claim,lose_ack=True)
    invocation=error.value.key
    assert repo.recover_delivery(claim.key,unknown_commit=True)=="UNCERTAIN"
    assert gate(c)["state"]=="BLOCKED"
    repo.record_fact(invocation,"ENDED",fact_key="fake-positive-no-permit-end")
    assert gate(c)["state"]=="FREE"
    assert repo.claim_delivery() is None
    assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==1
    # A separate event demonstrates an accepted commit with lost ACK.
    queued(repo,c,observed(repo,"B"))
    newer,p=sending(repo,c)
    facts(repo,p,"ACCEPTED","ENDED")
    with pytest.raises(CommitUnknown):
        repo.acknowledge(newer,lose_ack=True)
    assert state(c,newer.key)["status"]=="SENT"
    assert repo.claim_delivery() is None


def test_v07_distinct_account_capacity_and_stale_claim_fencing(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    repo.add_user()
    first=queued(repo,c,observed(repo))[0][0]
    repo.add_account("account-2")
    second=queued(repo,c,observed(repo,"B"),account_key="account-2")[0][0]
    claim=repo.claim_delivery(lease_ms=10)
    account=repo.claim_delivery(account_key="account-2")
    assert claim is not None and account is not None
    assert claim.key==first and account.key==second
    assert repo.claim_delivery() is None
    assert gate(c,"account-2")["state"]=="RESERVED"
    clock.now=claim.lease_ms+1
    repo.recover_delivery(claim.key)
    clock.now=max(clock.now,state(c,first)["next_ms"])
    newer=repo.claim_delivery()
    assert newer is not None
    with pytest.raises(Fenced):
        repo.authorize_send(claim)
    assert state(c,newer.key)["status"]=="PROCESSING"


def test_v04_seal_budget_hold_and_approved_recovery(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval,PolicyDenied
    repo.add_user()
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    with pytest.raises(PolicyDenied,match="budget"):
        repo.seal(h,budget_ms=0)
    row=c.execute("SELECT status,sealed_ms,audience_count FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(h.key[0],)).fetchone()
    assert row[:]==("HELD",None,None)
    assert c.execute("SELECT count(*) FROM event_audience").fetchone()[0]==0
    with pytest.raises(PolicyDenied):
        repo.release_outbox(h.key[0],"SUBSCRIBER",None)
    repo.release_outbox(h.key[0],"SUBSCRIBER",OperatorApproval("RELEASE_OUTBOX",h.key[0]+":SUBSCRIBER","budget-recovery"))
    newer=repo.claim_outbox(h.key[0],"SUBSCRIBER")
    assert repo.seal(newer)==1


def test_v09_verify_profile_preference_delete_insert_and_missing_sidecar(model):
    repo,c,clock,_=model
    repo.add_user(verified=0)
    key=repo.add_user()
    uid=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(key,)).fetchone()[0]
    assert c.execute("SELECT updated_ms FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()[0]==clock.now
    old=queued(repo,c,observed(repo))[0][0]
    repo.delete_preferences(uid)
    assert state(c,old)["status"]=="SUPPRESSED"
    repo.insert_preferences(uid,pref_internship=1)
    assert state(c,old)["status"]=="SUPPRESSED"
    repo.change_user(uid,email="changed-fictional@example.invalid")
    before=c.execute("SELECT consent_epoch,profile_epoch FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        repo.change_user(uid,graduation_year=2027)
    assert c.execute("SELECT consent_epoch,profile_epoch FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()[:]==before[:]
    # Explicit corrupt-sidecar fault injection, confined to this disposable fixture.
    c.execute("DROP TRIGGER subscriber_versions_no_delete")
    c.execute("DELETE FROM subscriber_versions WHERE user_id=?",(uid,))
    with pytest.raises(sqlite3.IntegrityError,match="missing"):
        repo.change_user(uid,is_active=0)
    assert c.execute("SELECT is_active FROM users WHERE id=?",(uid,)).fetchone()[0]==1


def test_v06_supersession_preserves_events_and_only_pending_history(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval
    repo.add_user()
    first=observed(repo)
    old=queued(repo,c,first)[0][0]
    second=observed(repo,"B")
    repo.supersede(first,second,OperatorApproval("SUPERSEDE",first+":"+second,"supersede-1"))
    assert state(c,old)["status"]=="SUPPRESSED"
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==2
    assert c.execute("SELECT relation FROM event_relations").fetchone()[0]=="SUPERSEDES"

def test_v02_composite_fk_negative_reaches_foreign_key_enforcement(model):
    repo,c,clock,_=model
    key=repo.add_user()
    a=observed(repo)
    old_delivery=queued(repo,c,a)[0][0]
    old_audience=state(c,old_delivery)["audience_id"]
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="fk-other",items=[dict(id="other",state="A",branch="CS")],complete=False)
    e=repo.apply_item(h,b,0)
    repo.finalize_batch(h,b)
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="fk-other-2",items=[dict(id="other",state="B",branch="CS")],complete=False)
    later=repo.apply_item(h,b,0)
    repo.finalize_batch(h,b)
    other_key=c.execute("SELECT company_key FROM opportunity_events WHERE event_id=?",(later,)).fetchone()[0]
    # Revisions 1<2 satisfy relation_order. Only the company-scoped FK can reject.
    with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
        c.execute("INSERT INTO event_relations VALUES(?,?,?,'SUPERSEDES','F-S0',1000)",(other_key,a,later))
    with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
        c.execute("INSERT INTO event_routes VALUES('missing','AFTER','VIT','CS','fictional')")
    oi=intent(c,later)
    box=repo.claim_outbox(oi,"SUBSCRIBER")
    repo.seal(box) # Sealed parent exists, but old audience belongs to another intent.
    with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
        c.execute("""INSERT INTO notification_deliveries_v2
            (company_key,audience_id,intent_id,channel,recipient_key,account_key,status,max_attempts,
             runtime_epoch,next_ms,priority,created_ms) VALUES(?,?,?,'SUBSCRIBER',?,'account-1','PENDING',5,0,1000,0,1000)""",
            (other_key,old_audience,oi,key))
    assert c.execute("PRAGMA foreign_key_check").fetchall()==[]


def test_f06_sql_constraint_failure_rolls_back_business_unit(model):
    repo,c,clock,_=model
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="sql-failure",items=[dict(id="new",state="A",branch="CS")],complete=True)
    def corrupt(stage):
        if stage=="outboxes":
            c.execute("INSERT INTO event_routes VALUES('missing','AFTER','VIT','CS','fixture')")
    with pytest.raises(sqlite3.IntegrityError,match="FOREIGN KEY"):
        repo.apply_item(h,b,0,fault=corrupt)
    assert c.execute("SELECT count(*) FROM companies").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM event_outbox").fetchone()[0]==0
    assert c.execute("SELECT status FROM source_batch_items").fetchone()[0]=="PENDING"


def test_v04_expiry_during_page_rolls_back_delivery_and_cursor(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    repo.add_user()
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER",lease_ms=10)
    repo.seal(h)
    def expire(stage):
        if stage=="delivery":clock.now=h.lease_ms+1
    with pytest.raises(Fenced):
        repo.fanout_page(h,fault=expire)
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
    assert c.execute("SELECT processed_count FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(h.key[0],)).fetchone()[0]==0


def test_v04_sqlite_interrupt_rolls_back_complete_cohort(model):
    repo,c,clock,_=model
    for _ in range(3):repo.add_user()
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    def interrupt(stage):
        if stage=="audience":c.set_progress_handler(lambda:1,1)
    try:
        with pytest.raises(sqlite3.OperationalError,match="interrupted"):
            repo.seal(h,fault=interrupt)
    finally:
        c.set_progress_handler(None,0)
    assert not c.in_transaction
    assert c.execute("SELECT count(*) FROM event_audience").fetchone()[0]==0
    assert c.execute("SELECT status,hold_reason FROM event_outbox WHERE intent_id=? AND channel=?",h.key).fetchone()[:]==("HELD","SQLITE_INTERRUPT")
    from app.database.event_repository import OperatorApproval
    repo.release_outbox(*h.key,OperatorApproval("RELEASE_OUTBOX",h.key[0]+":"+h.key[1],"interrupt-resolved"))
    replacement=repo.claim_outbox(*h.key)
    assert repo.seal(replacement)==3


def test_v04_preparation_cap_and_approved_retry_preserve_failure_count(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval
    repo.add_user()
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER",lease_ms=10)
    for _ in range(3):
        clock.now=h.lease_ms+1
        h=repo.claim_outbox(intent(c,event),"SUBSCRIBER",lease_ms=10)
        if h is None:break
    assert h is None
    row=c.execute("SELECT status,failure_count FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(intent(c,event),)).fetchone()
    assert row[:]==("HELD",3)
    repo.release_outbox(intent(c,event),"SUBSCRIBER",OperatorApproval("RELEASE_OUTBOX",intent(c,event)+":SUBSCRIBER","resolved-cap"))
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    assert h is not None
    assert c.execute("SELECT failure_count FROM event_outbox WHERE intent_id=? AND channel='SUBSCRIBER'",(intent(c,event),)).fetchone()[0]==3
    assert repo.seal(h)==1


def test_v08_invocation_duplicate_and_no_timer_gate_release(model):
    repo,c,clock,_=model
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,p=sending(repo,c)
    invocation=c.execute("SELECT * FROM transport_invocations WHERE invocation_id=?",(p.invocation_id,)).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO transport_invocations SELECT 'other',account_key,gate_generation,submission_key,owner_instance,owner_epoch,delivery_id,delivery_generation,message_class,intent_ms FROM transport_invocations")
    with pytest.raises(sqlite3.IntegrityError,match="end proof"):
        c.execute("""UPDATE smtp_account_gate SET state='FREE',owner_token=NULL,runtime_epoch=NULL,submission_key=NULL,
            delivery_id=NULL,invocation_id=NULL,lease_ms=NULL WHERE account_key='account-1'""")
    facts(repo,p,"UNKNOWN")
    assert repo.acknowledge(claim)=="UNCERTAIN"
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE notification_deliveries_v2 SET status='PENDING',finished_ms=NULL,invocation_id=NULL,send_started_ms=NULL WHERE delivery_id=?",(claim.key,))
    assert gate(c)["state"]=="BLOCKED"


def test_v08_stale_accepted_fact_cannot_ack_but_scoped_resolution_can(model):
    repo,c,clock,_=model
    from app.database.event_repository import Fenced
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,p=sending(repo,c)
    clock.now=claim.lease_ms+1
    facts(repo,p,"ACCEPTED","ENDED")
    with pytest.raises(Fenced):
        repo.acknowledge(claim)
    assert repo.recover_delivery(claim.key)=="UNCERTAIN"
    approval=repo.approve("RESOLVE",state(c,claim.key)["company_key"],str(claim.key))
    repo.resolve(claim.key,approval,confirm_accepted=True)
    assert state(c,claim.key)["status"]=="SENT"


def test_v09_payload_and_recipient_erasure_preserves_active_uncertainty(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval
    key=repo.add_user()
    event=observed(repo)
    queued(repo,c,event)
    claim,p=sending(repo,c)
    repo.erase_recipient(key,OperatorApproval("DEIDENTIFY",key,"active-erasure"))
    assert state(c,claim.key)["status"]=="SENDING"
    assert state(c,claim.key)["cancel_requested_ms"]==clock.now
    assert gate(c)["state"]=="BLOCKED"
    facts(repo,p,"UNKNOWN","ENDED")
    assert repo.acknowledge(claim)=="UNCERTAIN"
    assert gate(c)["state"]=="FREE"


def test_v09_verify_activate_clock_and_unrelated_preference(model):
    repo,c,clock,_=model
    key=repo.add_user(verified=0,active=0)
    uid=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(key,)).fetchone()[0]
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    assert repo.seal(h)==0
    repo.change_user(uid,is_verified=1,is_active=1)
    event=observed(repo,"B")
    delivery=queued(repo,c,event)[0][0]
    repo.set_preferences(uid,pref_placement=0)
    assert state(c,delivery)["status"]=="PENDING" # fictional internship scope unaffected
    assert c.execute("SELECT consent_epoch FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()[0]==1


def test_v08_action_freshness_and_no_permit_on_invocation_rollback(model):
    repo,c,clock,_=model
    from app.database.event_repository import PolicyDenied
    repo.add_user()
    event=observed(repo,verified_deadline=False)
    queued(repo,c,event)
    claim=repo.claim_delivery()
    assert repo.authorize_send(claim) is None
    assert state(c,claim.key)["status"]=="SUPPRESSED"
    assert gate(c)["state"]=="FREE"
    queued(repo,c,observed(repo,"B"))
    claim=repo.claim_delivery()
    def fail(stage):
        if stage=="invocation":raise RuntimeError("intent rollback")
    with pytest.raises(RuntimeError,match="intent"):
        repo.authorize_send(claim,fault=fail)
    assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0
    assert state(c,claim.key)["status"]=="PROCESSING" and gate(c)["state"]=="RESERVED"


def test_v11_shared_admin_subscriber_account_fake_gate_progress(model):
    repo,c,clock,_=model
    from app.database.event_repository import FakeTransport
    repo.add_user()
    repo.add_admin()
    event=observed(repo)
    queued(repo,c,event,"SUBSCRIBER")
    queued(repo,c,event,"ADMIN")
    management=repo.reserve_account()
    assert repo.claim_delivery() is None
    p=repo.authorize_account(management)
    fake=FakeTransport("ACCEPTED")
    repo.submit_fake(p,fake)
    facts(repo,p,"ACCEPTED","ENDED")
    assert repo.drain_fake(fake,quantum=25)==2
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==2
    assert {r[0] for r in c.execute("SELECT message_class FROM transport_invocations")}=={"ACCOUNT","ADMIN","SUBSCRIBER"}
    assert len(fake.calls)==3 and gate(c)["state"]=="FREE"


def test_v01_synthetic_paths_are_mandatory(tmp_path):
    from pathlib import Path
    m=implementation()
    with pytest.raises(m.SchemaError,match="disposable"):
        with m.connect_disposable(Path("relative.sqlite")):
            pytest.fail("relative database unexpectedly opened")

def test_v01_unexpected_new_schema_object_is_rejected(legacy_path):
    m=implementation()
    with m.connect_disposable(legacy_path) as c:
        m.create_schema(c,now_ms=1000)
        c.execute("CREATE INDEX unexpected_notification_index ON notification_intents(created_ms)")
        with pytest.raises(m.SchemaError,match="shape"):
            m.verify_schema(c)

@pytest.mark.parametrize("bad",[{"state":None},{"branch":{"invalid":True}},{"deadline_ms":"not-an-instant"},{"state":float("nan")},{"deadline_ms":None,"verified_deadline":True}])
def test_v05_malformed_trustworthy_record_quarantines_without_revision(model,bad):
    repo,c,clock,_=model
    h=repo.acquire_intake()
    item=dict(id="poison",state="A",branch="CS")
    item.update(bad)
    b=repo.admit(h,admission_key="invalid-material",items=[item],complete=True)
    assert repo.apply_item(h,b,0) is None
    repo.finalize_batch(h,b)
    assert c.execute("SELECT status,error_category FROM source_batch_items").fetchone()[:]==("QUARANTINED","FICTIONAL_INVALID")
    assert c.execute("SELECT count(*) FROM companies").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==0


def test_v06_explicit_deadline_change_is_material(model):
    repo,c,clock,_=model
    first=observed(repo,deadline_ms=200000)
    second=observed(repo,deadline_ms=300000)
    assert first!=second
    assert c.execute("SELECT revision FROM opportunity_state").fetchone()[0]==2

@pytest.mark.parametrize("stage",["BEFORE_SUBMISSION","PROTOCOL_RCPT_REJECTION"])
def test_v08_definitive_negative_before_and_after_fake_submission(model,stage):
    repo,c,clock,_=model
    from app.database.event_repository import FakeTransport
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,p=sending(repo,c)
    fake=FakeTransport("NOT_ACCEPTED")
    if stage=="PROTOCOL_RCPT_REJECTION":
        assert repo.submit_fake(p,fake)=="NOT_ACCEPTED"
    repo.record_fact(p.invocation_id,"NOT_ACCEPTED",fact_key="final-negative",evidence_reference="fictional:"+stage)
    repo.record_fact(p.invocation_id,"ENDED",fact_key="confirmed-fake-end")
    assert repo.acknowledge(claim)=="PENDING"
    assert len(fake.calls)==int(stage=="PROTOCOL_RCPT_REJECTION")
    assert gate(c)["state"]=="FREE"
    assert c.execute("SELECT evidence_reference FROM transport_facts WHERE fact_kind='NOT_ACCEPTED'").fetchone()[0]=="fictional:"+stage

def test_v05_duplicate_identity_remains_quarantined_after_global_recovery(model):
    repo,c,clock,_=model
    from app.database.event_repository import OperatorApproval
    h=repo.acquire_intake()
    b=repo.admit(h,admission_key="ambiguous-identity",items=[
        dict(id="same",state="A",branch="CS"),dict(id="same",state="B",branch="CS")],complete=True)
    repo.recover_batch(h,b,OperatorApproval("RECOVER_BATCH",b,"review-ambiguous"))
    assert repo.apply_item(h,b,0) is None
    assert repo.apply_item(h,b,1) is None
    repo.finalize_batch(h,b)
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM source_batch_items WHERE status='QUARANTINED'").fetchone()[0]==2

def test_v08_cancelled_logical_future_does_not_end_active_fake_thread(model):
    from concurrent.futures import Future
    from app.database.event_repository import FakeTransport
    repo,c,clock,_=model
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,permit=sending(repo,c)
    started,finish=threading.Event(),threading.Event()
    logical=Future()
    errors=[]
    class ActiveFake(FakeTransport):
        def submit(self,invocation):
            self.calls.append(invocation)
            started.set()
            if not finish.wait(timeout=2):
                raise RuntimeError("bounded fake end timeout")
            return "UNKNOWN"
    fake=ActiveFake("UNKNOWN")
    def physical():
        try:
            outcome=repo.submit_fake(permit,fake)
            if not logical.cancelled():logical.set_result(outcome)
        except BaseException as exc:
            errors.append(exc)
    worker=threading.Thread(target=physical)
    worker.start()
    try:
        assert started.wait(timeout=1)
        assert logical.cancel() and logical.cancelled()
        assert worker.is_alive()
        clock.now=claim.lease_ms+1
        assert repo.recover_delivery(claim.key)=="UNCERTAIN"
        assert gate(c)["state"]=="BLOCKED"
        repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="cancelled-logical-observer")
        assert repo.release_ended(permit.invocation_id) is False
        assert worker.is_alive() and repo.claim_delivery() is None
        repo.bump_epoch()
    finally:
        finish.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and errors==[] and fake.calls==[permit.invocation_id]
    repo.record_fact(permit.invocation_id,"ENDED",fact_key="positive-thread-join",evidence_reference="fictional-thread-confirmed-ended")
    assert gate(c)["state"]=="FREE"
    assert state(c,claim.key)["status"]=="UNCERTAIN"
    queued(repo,c,observed(repo,"B"))
    assert repo.claim_delivery() is not None



@pytest.mark.parametrize("mutation",["preference","branch","activation","verification","delete_preferences"])
def test_v09_sealed_audience_opt_out_on_before_fanout_is_irreversible(model,mutation):
    repo,c,clock,_=model
    recipient=repo.add_user()
    user=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(recipient,)).fetchone()[0]
    event=observed(repo)
    h=repo.claim_outbox(intent(c,event),"SUBSCRIBER")
    assert repo.seal(h)==1
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
    if mutation=="preference":
        repo.set_preferences(user,pref_internship=0)
        repo.set_preferences(user,pref_internship=1)
    elif mutation=="branch":
        repo.change_user(user,branch_canonical="CE")
        repo.change_user(user,branch_canonical="CS")
    elif mutation=="activation":
        repo.change_user(user,is_active=0)
        repo.change_user(user,is_active=1)
    elif mutation=="verification":
        repo.change_user(user,is_verified=0)
        repo.change_user(user,is_verified=1)
    else:
        repo.delete_preferences(user)
        repo.insert_preferences(user)
    before=c.execute("SELECT status,attempt_count FROM notification_deliveries_v2").fetchall()
    assert [tuple(r) for r in before]==[("SUPPRESSED",0)],"revocation must persist in the mutation transaction before fanout"
    assert repo.fanout_page(h)==1
    assert c.execute("SELECT processed_count,status FROM event_outbox WHERE intent_id=? AND channel=?",h.key).fetchone()[:]==(1,"COMPLETE")
    assert repo.claim_delivery() is None
    assert c.execute("SELECT status FROM notification_deliveries_v2").fetchone()[0]=="SUPPRESSED"
    queued(repo,c,observed(repo,"B"))
    assert repo.claim_delivery() is not None


def test_v09_unrelated_preference_before_fanout_preserves_current_eligibility(model):
    repo,c,clock,_=model
    recipient=repo.add_user()
    user=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(recipient,)).fetchone()[0]
    h=repo.claim_outbox(intent(c,observed(repo)),"SUBSCRIBER")
    assert repo.seal(h)==1
    repo.set_preferences(user,pref_placement=0)
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
    repo.fanout_page(h)
    assert repo.claim_delivery() is not None


def test_v09_legacy_year_policy_rejects_unapproved_mutation_without_partial_consent(model):
    repo,c,clock,_=model
    recipient=repo.add_user()
    user=c.execute("SELECT user_id FROM recipient_associations WHERE recipient_key=?",(recipient,)).fetchone()[0]
    queued(repo,c,observed(repo))
    before=tuple(c.execute("SELECT * FROM subscriber_versions WHERE user_id=?",(user,)).fetchone())
    with pytest.raises(sqlite3.IntegrityError,match="graduation_year = 2028"):
        repo.change_user(user,graduation_year=2029)
    assert tuple(c.execute("SELECT * FROM subscriber_versions WHERE user_id=?",(user,)).fetchone())==before
    assert c.execute("SELECT graduation_year FROM users WHERE id=?",(user,)).fetchone()[0]==2028
    assert c.execute("SELECT status FROM notification_deliveries_v2").fetchone()[0]=="PENDING"


@pytest.mark.parametrize("ended_kind",["ENDED","PROCESS_EXIT_CONFIRMED"])
def test_v08_unused_permit_retired_by_distinct_connection_end_before_gate_reuse(model,ended_kind):
    repo,c,clock,path=model
    from app.database.event_repository import EventRepository,FakeTransport,PolicyDenied
    repo.add_user()
    queued(repo,c,observed(repo))
    first,old_permit=sending(repo,c)
    repo.record_fact(old_permit.invocation_id,"NOT_ACCEPTED",fact_key="before-submission")
    with implementation().connect_disposable(path) as other:
        EventRepository(other,clock=clock).record_fact(old_permit.invocation_id,ended_kind,fact_key="confirmed-no-submission-end")
    assert repo.acknowledge(first)=="PENDING"
    clock.now+=11
    newer,new_permit=sending(repo,c)
    fake=FakeTransport()
    with pytest.raises(PolicyDenied,match="permit"):
        repo.submit_fake(old_permit,fake)
    assert fake.calls==[]
    assert gate(c)["invocation_id"]==new_permit.invocation_id
    assert repo.submit_fake(new_permit,fake)=="ACCEPTED"
    assert fake.calls==[new_permit.invocation_id]


@pytest.mark.parametrize("complete",[False,True])
def test_v05_admission_replay_rejects_conflicting_completeness(model,complete):
    repo,c,clock,_=model
    from app.database.event_repository import PolicyDenied
    h=repo.acquire_intake()
    items=[dict(id="a",state="A",branch="CS")]
    batch=repo.admit(h,admission_key="same-admission",items=items,complete=complete)
    before=tuple(c.execute("SELECT * FROM source_batches WHERE batch_id=?",(batch,)).fetchone())
    with pytest.raises(PolicyDenied,match="completeness"):
        repo.admit(h,admission_key="same-admission",items=items,complete=not complete)
    assert tuple(c.execute("SELECT * FROM source_batches WHERE batch_id=?",(batch,)).fetchone())==before
    assert repo.admit(h,admission_key="same-admission",items=items,complete=complete,max_items=1)==batch


@pytest.mark.parametrize("violation",["predecessor","wrong_company_intent"])
def test_v02_precise_event_predecessor_and_ordinary_company_controls(model,violation):
    repo,c,clock,_=model
    first=observed(repo)
    owner=repo.acquire_intake()
    batch=repo.admit(owner,admission_key="scope-other",items=[dict(id="other",state="X",branch="CS")],complete=False)
    other=repo.apply_item(owner,batch,0)
    repo.finalize_batch(owner,batch)
    wrong_company=c.execute("SELECT company_key FROM opportunity_events WHERE event_id=?",(other,)).fetchone()[0]
    owner=repo.acquire_intake()
    batch=repo.admit(owner,admission_key="scope-next",items=[dict(id="company-a",state="B",branch="CS")],complete=False)
    def violate(stage):
        if violation=="predecessor" and stage=="baseline":
            row=dict(c.execute("SELECT * FROM opportunity_events WHERE event_id=?",(first,)).fetchone())
            row.update(event_id="wrong-predecessor",revision=2,previous_revision=1,previous_event_id=other,batch_id=batch,item_index=0)
            c.execute("INSERT INTO opportunity_events("+",".join(row)+") VALUES("+",".join("?" for _ in row)+")",tuple(row.values()))
        if violation=="wrong_company_intent" and stage=="routes":
            event=dict(c.execute("SELECT * FROM opportunity_events WHERE batch_id=?",(batch,)).fetchone())
            assert c.execute("SELECT count(*) FROM notification_intents WHERE source_event_id=?",(event["event_id"],)).fetchone()[0]==0
            c.execute("""INSERT INTO notification_intents
                (intent_id,company_key,kind,source_event_id,snapshot_payload_id,policy_version,config_version,created_ms)
                VALUES('wrong-company',?,'SOURCE_EVENT',?,?,'F-S0','F-S0',1000)""",
                (wrong_company,event["event_id"],event["after_payload_id"]))
    expected="event is not next committed company revision" if violation=="predecessor" else "FOREIGN KEY"
    with pytest.raises(sqlite3.IntegrityError,match=expected):
        repo.apply_item(owner,batch,0,fault=violate)
    assert c.execute("SELECT status FROM source_batch_items WHERE batch_id=?",(batch,)).fetchone()[0]=="PENDING"
    assert c.execute("SELECT count(*) FROM opportunity_events WHERE batch_id=?",(batch,)).fetchone()[0]==0
    assert c.execute("SELECT revision,head_event_id FROM opportunity_state WHERE legacy_company_id='company-a'").fetchone()[:]==(1,first)
    assert repo.apply_item(owner,batch,0) is not None


def test_v06_sql_ordinary_intent_and_delivery_uniqueness_controls(model):
    repo,c,clock,_=model
    repo.add_user()
    event=observed(repo)
    ordinary=intent(c,event)
    with pytest.raises(sqlite3.IntegrityError,match="UNIQUE constraint failed: notification_intents.source_event_id"):
        c.execute("""INSERT INTO notification_intents
            (intent_id,company_key,kind,source_event_id,snapshot_payload_id,policy_version,config_version,created_ms)
            SELECT 'duplicate-ordinary',company_key,kind,source_event_id,snapshot_payload_id,policy_version,config_version,created_ms
            FROM notification_intents WHERE intent_id=?""",(ordinary,))
    delivery=queued(repo,c,event)[0][0]
    with pytest.raises(sqlite3.IntegrityError,match="UNIQUE constraint failed: notification_deliveries_v2.intent_id, notification_deliveries_v2.channel, notification_deliveries_v2.recipient_key"):
        c.execute("""INSERT INTO notification_deliveries_v2
            (company_key,audience_id,intent_id,channel,recipient_key,account_key,status,max_attempts,runtime_epoch,next_ms,expires_ms,priority,created_ms)
            SELECT company_key,audience_id,intent_id,channel,recipient_key,account_key,status,max_attempts,runtime_epoch,next_ms,expires_ms,priority,created_ms
            FROM notification_deliveries_v2 WHERE delivery_id=?""",(delivery,))
    assert c.execute("SELECT count(*) FROM notification_intents WHERE kind='SOURCE_EVENT'").fetchone()[0]==1
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==1


@pytest.mark.parametrize("wrong_field",["delivery_generation","gate_generation"])
def test_v08_precise_wrong_generation_fresh_invocation_control(model,wrong_field):
    repo,c,clock,_=model
    repo.add_user()
    queued(repo,c,observed(repo))
    claim=repo.claim_delivery()
    assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0
    generation=claim.generation+int(wrong_field=="delivery_generation")
    gate_generation=claim.gate_generation+int(wrong_field=="gate_generation")
    with pytest.raises(sqlite3.IntegrityError,match="invocation attempt/account mismatch"):
        c.execute("INSERT INTO transport_invocations VALUES('fresh-invalid','account-1',?,?,'fictional-owner',?,?,?,?,?)",
                  (gate_generation,claim.submission_key,claim.epoch,claim.key,generation,"SUBSCRIBER",clock.now))
    assert c.execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0
    assert gate(c)["state"]=="RESERVED"
    assert repo.authorize_send(claim) is not None


def test_v08_concurrent_fake_permit_consumption_has_one_winner(model):
    repo,c,clock,_=model
    from app.database.event_repository import FakeTransport,PolicyDenied
    repo.add_user()
    queued(repo,c,observed(repo))
    claim,permit=sending(repo,c)
    fake=FakeTransport()
    barrier=threading.Barrier(2)
    results,errors=[],[]
    def submit():
        try:
            barrier.wait(timeout=2)
            results.append(repo.submit_fake(permit,fake))
        except PolicyDenied:
            results.append("DENIED")
        except BaseException as exc:
            errors.append(exc)
    threads=[threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads) and errors==[]
    assert sorted(results)==["ACCEPTED","DENIED"]
    assert fake.calls==[permit.invocation_id]


