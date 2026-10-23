"""Integrated crash/transport/queue assertions; only feed and transport are fake."""
import asyncio
import json
import threading
from dataclasses import replace
from time import perf_counter

import pytest
from test_cummins_end_to_end_v2 import integrated, record, SMTP

from app.database.event_repository import EventRepository, Fenced, PolicyDenied, FakeTransport
from app.database.notification_migrator import connect_disposable
from app.monitoring.notification_pipeline import NotificationPipeline, V2Options
from app.monitoring.watcher import PlacementWatcher
from app.subscribers.worker import DeliveryWorker


def worker_restart(x, connection=None, transport=None):
    repository=EventRepository(connection or x["c"],clock=x["clock"])
    pipeline=NotificationPipeline(repository,transport=transport or x["smtp"],options=x["pipeline"].options)
    worker=DeliveryWorker(x["db"].deliveries,x["db"].tokens,notification_pipeline=pipeline)
    return pipeline,worker


@pytest.mark.asyncio
@pytest.mark.parametrize("delay",[11,60001])
async def test_delayed_source_resume_preserves_observation_and_info_expiry(integrated,delay):
    x=integrated
    observed=x["clock"]()
    def fail(point):
        if point=="event":raise RuntimeError("fictional source interruption")
    x["pipeline"].faults["source"]=fail
    assert await x["watcher"].check_once() is False
    assert x["c"].execute("SELECT observed_ms FROM source_batches").fetchone()[0]==observed
    x["clock"].now+=delay
    x["pipeline"].faults.clear()
    assert await x["watcher"].check_once()
    event=x["c"].execute("SELECT observed_ms,persisted_ms FROM opportunity_events").fetchone()
    assert event[:]==(observed,observed+delay)
    assert x["feed"].fetches==1
    sent=await x["watcher"].delivery_worker.process_batch_once()
    assert {r[0] for r in x["c"].execute("SELECT expires_ms FROM notification_deliveries_v2")}=={observed+60000}
    if delay>60000:
        assert sent==0 and x["smtp"].calls==[]
        assert {r[0] for r in x["c"].execute("SELECT status FROM notification_deliveries_v2")}=={"SUPPRESSED"}
    else:
        assert sent==2
        assert all(m["observed_ms"]==observed and m["persisted_ms"]==observed+delay for m in x["smtp"].messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage",["legacy","baseline","event","routes","intent","outboxes","head","checkpoint"])
async def test_source_crash_rolls_back_and_actual_watcher_resumes(integrated,stage):
    x=integrated
    def fail(point):
        if point==stage:raise RuntimeError("fictional interruption")
    x["pipeline"].faults["source"]=fail
    assert await x["watcher"].check_once() is False
    c=x["c"]
    assert c.execute("SELECT count(*) FROM companies").fetchone()[0]==0
    for table in ("opportunity_state","opportunity_events","notification_intents","event_outbox"):
        assert c.execute("SELECT count(*) FROM "+table).fetchone()[0]==0
    assert c.execute("SELECT status FROM source_batch_items").fetchone()[0]=="PENDING"
    x["pipeline"].faults.clear()
    assert await x["watcher"].check_once()
    assert x["feed"].fetches==1
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert len(x["smtp"].calls)==2


@pytest.mark.asyncio
async def test_event_commit_before_fanout_restart_uses_durable_obligations(integrated):
    x=integrated
    def crash():raise RuntimeError("after committed source unit")
    x["pipeline"].faults["after_source"]=crash
    assert await x["watcher"].check_once() is False
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert x["c"].execute("SELECT count(*) FROM event_outbox").fetchone()[0]==2
    pipeline,worker=worker_restart(x)
    assert await worker.process_batch_once()==2
    assert x["feed"].fetches==1 and len(x["smtp"].calls)==2
    x["pipeline"].faults.clear()
    assert await x["watcher"].check_once()
    assert await worker.process_batch_once()==0


@pytest.mark.asyncio
@pytest.mark.parametrize("point",["seal","page"])
async def test_seal_page_crash_rolls_back_then_other_channel_and_restart(integrated,point):
    x=integrated
    assert await x["watcher"].check_once()
    def fail(stage):raise RuntimeError("fanout boundary")
    x["pipeline"].faults[point]=fail
    await x["watcher"].delivery_worker.process_batch_once()
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
    assert x["c"].execute("SELECT sum(processed_count) FROM event_outbox").fetchone()[0]==0
    if point=="seal":
        assert x["c"].execute("SELECT count(*) FROM event_audience").fetchone()[0]==0
    x["clock"].now+=1001
    pipeline,worker=worker_restart(x)
    assert await worker.process_batch_once()==2
    assert len(x["smtp"].calls)==2


@pytest.mark.asyncio
@pytest.mark.parametrize("failed",["ADMIN","SUBSCRIBER"])
async def test_channel_failure_does_not_erase_other_channel(integrated,failed):
    x=integrated
    x["smtp"].outcomes[failed]="NOT_ACCEPTED"
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==1
    rows={r[0]:r[1] for r in x["c"].execute("SELECT channel,status FROM notification_deliveries_v2")}
    assert rows[failed]=="PENDING"
    assert rows["SUBSCRIBER" if failed=="ADMIN" else "ADMIN"]=="SENT"
    x["smtp"].outcomes.clear()
    x["clock"].now+=11
    assert await x["watcher"].delivery_worker.process_batch_once()==1
    assert len(x["smtp"].calls)==3


@pytest.mark.asyncio
async def test_admin_renderer_failure_has_no_invocation_and_student_progresses(integrated):
    x=integrated
    base=x["pipeline"].renderer
    def renderer(**kwargs):
        if kwargs["channel"]=="ADMIN":raise ValueError("fictional renderer failure")
        return base(**kwargs)
    x["pipeline"].renderer=renderer
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==1
    assert [r[0] for r in x["c"].execute("SELECT message_class FROM transport_invocations")]==["SUBSCRIBER"]
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='ADMIN'").fetchone()[0]=="PENDING"
    x["pipeline"].renderer=base
    x["clock"].now+=11
    assert await x["watcher"].delivery_worker.process_batch_once()==1


@pytest.mark.asyncio
@pytest.mark.parametrize("race",["consent_return","consent_raise","lease_return","lease_raise"])
async def test_worker_held_preparation_race_does_not_stop_independent_pump(integrated,race):
    x=integrated
    other=x["db"].users.create_user("fictional-other@example.invalid",2028,"VIT_CE")
    x["db"].users.set_verified(other)
    assert await x["watcher"].check_once()
    base=x["pipeline"].renderer
    raced=[]
    def renderer(**kwargs):
        if kwargs["channel"]=="SUBSCRIBER" and not raced:
            row=x["c"].execute("""SELECT d.delivery_id,a.user_id FROM notification_deliveries_v2 d
                JOIN recipient_associations a ON a.recipient_key=d.recipient_key
                WHERE d.status='PROCESSING' AND d.channel='SUBSCRIBER'""").fetchone()
            raced.append(row["delivery_id"])
            if race.startswith("consent"):
                x["db"].users.set_unsubscribed(row["user_id"])
            else:
                x["clock"].now+=101
            if race.endswith("raise"):raise ValueError("fictional preparation interrupted after fencing")
        return base(**kwargs)
    x["pipeline"].renderer=renderer
    stop=asyncio.Event()
    ticks=[]
    async def wait(seconds):
        assert not x["pipeline"].closed
        modes=x["c"].execute("SELECT writer_mode,fanout_mode,send_mode FROM notification_runtime").fetchone()
        assert modes[:]==("V2","V2","V2")
        ticks.append(seconds)
        x["clock"].now+=11
        if len(ticks)==3:stop.set()
    await x["watcher"].delivery_worker.run_v2_loop(stop,wait=wait)
    assert len(ticks)==3 and x["feed"].fetches==1
    target=x["c"].execute("SELECT status,generation FROM notification_deliveries_v2 WHERE delivery_id=?",(raced[0],)).fetchone()
    assert target["status"]==("SUPPRESSED" if race.startswith("consent") else "SENT")
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE delivery_id<>? AND status='SENT'",(raced[0],)).fetchone()[0]==2
    assert x["c"].execute("SELECT count(*) FROM transport_invocations WHERE delivery_id=? AND delivery_generation=1",(raced[0],)).fetchone()[0]==0
    assert len(x["smtp"].calls)==(2 if race.startswith("consent") else 3)


@pytest.mark.asyncio
async def test_recovery_candidate_reclaimed_on_other_connection_keeps_pump_live(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    stale=x["repo"].claim_delivery(channel="ADMIN")
    x["clock"].now+=101
    newer=[]
    errors=[]
    with connect_disposable(x["path"]) as other_connection:
        other=EventRepository(other_connection,clock=x["clock"],record_mode=True,info_ttl_ms=60000)
        def interleave(sql):
            if sql=="BEGIN IMMEDIATE" and not newer:
                try:
                    other.recover_delivery(stale.key)
                    x["clock"].now+=11
                    newer.append(other.claim_delivery(channel="ADMIN"))
                except Exception as exc:errors.append(type(exc).__name__)
        x["c"].set_trace_callback(interleave)
        stop=asyncio.Event()
        ticks=[]
        async def wait(seconds):
            assert not x["pipeline"].closed and errors==[]
            assert x["c"].execute("SELECT send_mode FROM notification_runtime").fetchone()[0]=="V2"
            if not ticks:
                row=x["c"].execute("SELECT status,generation,claim_token FROM notification_deliveries_v2 WHERE delivery_id=?",(stale.key,)).fetchone()
                assert row[:]==("PROCESSING",newer[0].generation,newer[0].token)
                assert newer[0].generation>stale.generation
                other.fail_preparation(newer[0],"FICTIONAL_COMPETING_OWNER_RELEASE")
            ticks.append(seconds)
            x["clock"].now+=11
            if len(ticks)==2:stop.set()
        try:
            await x["watcher"].delivery_worker.run_v2_loop(stop,wait=wait)
        finally:x["c"].set_trace_callback(None)
    assert len(newer)==1 and len(ticks)==2 and x["feed"].fetches==1
    assert x["c"].execute("SELECT count(*) FROM transport_invocations WHERE delivery_id=? AND delivery_generation<=?",(stale.key,newer[0].generation)).fetchone()[0]==0
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==2
    assert len(x["smtp"].calls)==2


@pytest.mark.asyncio
async def test_claim_ack_inspection_consent_race_keeps_pump_live(integrated):
    x=integrated
    other=x["db"].users.create_user("fictional-inspection-other@example.invalid",2028,"VIT_CE")
    x["db"].users.set_verified(other)
    assert await x["watcher"].check_once()
    x["pipeline"].faults["claim_ack"]=True
    selected=[]
    suppressed=[]
    errors=[]
    def interleave(sql):
        try:
            if sql.startswith("SELECT a.*,d.status") and not selected:
                row=x["c"].execute("""SELECT d.delivery_id,a.user_id FROM notification_deliveries_v2 d
                    JOIN recipient_associations a ON a.recipient_key=d.recipient_key
                    WHERE d.status='PROCESSING' AND d.channel='SUBSCRIBER'""").fetchone()
                if row:selected.append(tuple(row))
            elif sql=="SELECT * FROM notification_runtime WHERE singleton=1" and selected and not suppressed:
                suppressed.append(selected[0][0])
                x["db"].users.set_unsubscribed(selected[0][1])
        except Exception as exc:errors.append(type(exc).__name__)
    x["c"].set_trace_callback(interleave)
    stop=asyncio.Event()
    ticks=[]
    async def wait(seconds):
        assert not x["pipeline"].closed and errors==[]
        assert x["c"].execute("SELECT send_mode FROM notification_runtime").fetchone()[0]=="V2"
        ticks.append(seconds)
        if len(ticks)==2:stop.set()
    try:
        await x["watcher"].delivery_worker.run_v2_loop(stop,wait=wait)
    finally:x["c"].set_trace_callback(None)
    assert len(ticks)==2 and len(suppressed)==1 and x["feed"].fetches==1
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?",(suppressed[0],)).fetchone()[0]=="SUPPRESSED"
    assert x["c"].execute("SELECT count(*) FROM transport_invocations WHERE delivery_id=?",(suppressed[0],)).fetchone()[0]==0
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==2
    assert len(x["smtp"].calls)==2


@pytest.mark.asyncio
async def test_held_admin_visible_while_student_proceeds(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["c"].execute("UPDATE event_outbox SET status='HELD',hold_reason='FICTIONAL_OWNER_HOLD' WHERE channel='ADMIN'")
    assert await x["watcher"].delivery_worker.process_batch_once()==1
    assert x["c"].execute("SELECT status FROM event_outbox WHERE channel='ADMIN'").fetchone()[0]=="HELD"
    assert [m["channel"] for m in x["smtp"].messages]==["SUBSCRIBER"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,ended,status",[("ACCEPTED",False,"SENT"),("UNKNOWN",False,"UNCERTAIN"),("UNKNOWN",True,"UNCERTAIN"),("NOT_ACCEPTED",False,"UNCERTAIN")])
async def test_delayed_end_unknown_no_automatic_resend(integrated,kind,ended,status):
    x=integrated
    x["smtp"].outcome,x["smtp"].ended=kind,ended
    assert await x["watcher"].check_once()
    await x["watcher"].delivery_worker.process_batch_once()
    row=x["c"].execute("SELECT * FROM notification_deliveries_v2 ORDER BY delivery_id LIMIT 1").fetchone()
    assert row["status"]==status
    calls=len(x["smtp"].calls)
    x["clock"].now+=101
    if not ended:
        assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0] in ("INFLIGHT","BLOCKED")
        assert await x["watcher"].delivery_worker.process_batch_once()==0
        assert len(x["smtp"].calls)==calls
        x["repo"].record_fact(row["invocation_id"],"ENDED",fact_key="delayed-positive-end")
        assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="FREE"
    # A separate ready obligation can advance after END, but uncertain row cannot.
    x["smtp"].outcome,x["smtp"].ended="ACCEPTED",True
    await x["watcher"].delivery_worker.process_batch_once()
    assert sum(m["delivery_id"]==row["delivery_id"] for m in x["smtp"].messages)==1
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?",(row["delivery_id"],)).fetchone()[0]==status


@pytest.mark.asyncio
async def test_five_finalized_failures_exhaust_and_ack_loss_does_not_resend(integrated):
    x=integrated
    x["smtp"].outcome="NOT_ACCEPTED"
    assert await x["watcher"].check_once()
    for _ in range(5):
        assert await x["watcher"].delivery_worker.process_batch_once()==0
        x["clock"].now+=11
    assert {tuple(r) for r in x["c"].execute("SELECT status,attempt_count FROM notification_deliveries_v2")}=={("FAILED",5)}
    assert len(x["smtp"].calls)==10
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    x["feed"].records=[record(regStarttime="09:00")]
    assert await x["watcher"].check_once()
    x["smtp"].outcome="ACCEPTED"
    x["pipeline"].faults["ack"]=True
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert len(x["smtp"].calls)==12


@pytest.mark.asyncio
async def test_stale_completion_and_safe_preparation_recovery(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    old=x["repo"].claim_delivery(lease_ms=10)
    x["clock"].now+=11
    assert x["repo"].recover_delivery(old.key)=="PENDING"
    x["clock"].now+=11
    newer=x["repo"].claim_delivery()
    assert newer.key==old.key
    with pytest.raises(Fenced):x["repo"].authorize_send(old)
    permit=x["repo"].authorize_send(newer)
    x["repo"].record_fact(permit.invocation_id,"ACCEPTED",fact_key="new-owner")
    x["repo"].record_fact(permit.invocation_id,"ENDED",fact_key="new-owner-end")
    assert x["repo"].acknowledge(newer)=="SENT"
    with pytest.raises(Fenced):x["repo"].acknowledge(old)
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?",(old.key,)).fetchone()[0]=="SENT"


@pytest.mark.asyncio
async def test_two_real_worker_connections_share_one_transport_slot(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    barrier=threading.Barrier(2)
    results,errors=[],[]
    def run():
        try:
            with connect_disposable(x["path"]) as c:
                fake=SMTP(ended=False)
                pipeline,worker=worker_restart(x,c,fake)
                barrier.wait(timeout=3)
                results.append((asyncio.run(worker.process_batch_once()),len(fake.calls)))
        except BaseException as exc:errors.append(type(exc).__name__)
    threads=[threading.Thread(target=run) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=8)
    assert all(not thread.is_alive() for thread in threads) and errors==[]
    assert sum(r[1] for r in results)==1
    assert x["c"].execute("SELECT count(*) FROM delivery_attempts").fetchone()[0]==1
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="INFLIGHT"


@pytest.mark.asyncio
@pytest.mark.parametrize("size",[100,1000])
async def test_independent_pump_drains_entire_queue_without_source_poll(integrated,size):
    x=integrated
    for i in range(size-1):
        uid=x["db"].users.create_user("cohort"+str(i)+"@example.invalid",2028,"VIT_CE")
        x["db"].users.set_verified(uid)
    assert await x["watcher"].check_once()
    stop=asyncio.Event()
    waits=[]
    async def wait(seconds):
        assert seconds>=0.01
        waits.append(seconds)
        x["clock"].now+=int(seconds*1000)
        sent=x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]
        if sent==size+1:stop.set()
        assert len(waits)<=50,dict(outboxes=[tuple(r) for r in x["c"].execute("SELECT channel,status,hold_reason,audience_count,processed_count FROM event_outbox")],deliveries=[tuple(r) for r in x["c"].execute("SELECT status,count(*) FROM notification_deliveries_v2 GROUP BY status")])
    start=perf_counter()
    await x["watcher"].delivery_worker.run_v2_loop(stop,wait=wait)
    assert x["feed"].fetches==1
    assert len(x["smtp"].messages)==size+1
    assert len(waits)>1 and x["pipeline"].closed
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==size+1
    print("V2_DRAIN",dict(size=size,quanta=len(waits),fake_calls=len(x["smtp"].calls),elapsed_ms=round((perf_counter()-start)*1000,3)))


@pytest.mark.asyncio
async def test_pending_old_snapshot_and_later_update_render_distinct_facts(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["feed"].records=[record(regStarttime="08:45")]
    assert await x["watcher"].check_once()
    x["clock"].now+=1000
    assert await x["watcher"].delivery_worker.process_batch_once()==4
    bodies=[m["html"] for m in x["smtp"].messages]
    assert sum("11:31" in b for b in bodies)==2
    assert sum("08:45" in b for b in bodies)==2
    assert all(m["persisted_ms"]<m["rendered_ms"] for m in x["smtp"].messages)


@pytest.mark.asyncio
async def test_default_off_and_shutdown_preserve_active_uncertainty(integrated):
    x=integrated
    assert V2Options()==V2Options(writer=False,fanout=False,drain=False,transport=False)
    x["smtp"].ended=False
    x["smtp"].outcome="UNKNOWN"
    assert await x["watcher"].check_once()
    await x["watcher"].delivery_worker.process_batch_once()
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"
    await x["watcher"].shutdown()
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert x["c"].execute("SELECT send_mode FROM notification_runtime").fetchone()[0]=="OFF"
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"
    assert len(x["smtp"].calls)==1

@pytest.mark.asyncio
async def test_claim_and_send_commit_ack_loss_never_duplicates_physical_submit(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["pipeline"].faults["claim_ack"]=True
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert len(x["smtp"].calls)==2
    x["pipeline"].faults.clear()
    x["feed"].records=[record(regEndtime="09:00")]
    assert await x["watcher"].check_once()
    x["pipeline"].faults["send_ack"]=True
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert len(x["smtp"].calls)==2
    rows=x["c"].execute("SELECT status,invocation_id FROM notification_deliveries_v2 WHERE status='UNCERTAIN'").fetchone()
    assert rows[0]=="UNCERTAIN" and rows[1] is not None
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"


@pytest.mark.asyncio
async def test_failed_first_recipient_does_not_stop_unrelated_ready_work(integrated):
    x=integrated
    for i in range(4):
        uid=x["db"].users.create_user("progress"+str(i)+"@example.invalid",2028,"VIT_CE")
        x["db"].users.set_verified(uid)
    class FirstNegative(SMTP):
        def submit_message(self,invocation,message):
            result=super().submit_message(invocation,message)
            from app.monitoring.notification_pipeline import TransportResult
            return TransportResult("NOT_ACCEPTED" if len(self.calls)==1 else "ACCEPTED",True)
    fake=FirstNegative()
    x["pipeline"].transport=fake
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==5
    assert len(fake.calls)==6
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]==1
    assert x["pipeline"].next_wait_ms()==10
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    x["clock"].now+=11
    assert await x["watcher"].delivery_worker.process_batch_once()==1


@pytest.mark.asyncio
async def test_blocked_account_pump_waits_and_shutdown_stops_new_permits(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["smtp"].outcome,x["smtp"].ended="UNKNOWN",False
    stop=asyncio.Event()
    waits=[]
    async def wait(seconds):
        waits.append(seconds)
        assert seconds==x["pipeline"].options.idle_wait_ms/1000
        x["clock"].now+=int(seconds*1000)
        if len(waits)==3:stop.set()
    await x["watcher"].delivery_worker.run_v2_loop(stop,wait=wait)
    assert len(waits)==3 and len(x["smtp"].calls)==1 and x["feed"].fetches==1
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"


@pytest.mark.asyncio
async def test_explicit_supersession_preserves_event_and_suppresses_pending_snapshot(integrated):
    x=integrated
    from app.database.event_repository import OperatorApproval
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    x["feed"].records=[record(regStarttime="08:30")]
    assert await x["watcher"].check_once()
    events=[r[0] for r in x["c"].execute("SELECT event_id FROM opportunity_events ORDER BY revision")]
    x["repo"].supersede(*events,OperatorApproval("SUPERSEDE",events[0]+":"+events[1],"fictional-explicit-supersession"))
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==2
    assert x["c"].execute("SELECT count(*) FROM event_relations").fetchone()[0]==1
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert all("08:30" in m["html"] and "11:31" not in m["html"] for m in x["smtp"].messages)

@pytest.mark.asyncio
async def test_coherent_seal_loads_immutable_matching_context_once_not_per_recipient(integrated):
    x=integrated
    for i in range(9):
        uid=x["db"].users.create_user("seal"+str(i)+"@example.invalid",2028,"VIT_CE")
        x["db"].users.set_verified(uid)
    assert await x["watcher"].check_once()
    key=x["c"].execute("SELECT intent_id FROM notification_intents").fetchone()[0]
    h=x["repo"].claim_outbox(key,"SUBSCRIBER")
    queries=[]
    x["c"].set_trace_callback(queries.append)
    try:assert x["repo"].seal(h)==10
    finally:x["c"].set_trace_callback(None)
    snapshot_reads=[q for q in queries if q.startswith("SELECT encrypted_bytes FROM protected_payloads")]
    assert len(snapshot_reads)<=2,"one immutable event matching context per coherent seal, not one query/parse per student"



