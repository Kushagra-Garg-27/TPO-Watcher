"""Encrypted real application boundaries and guarded child transport only."""
import asyncio
import sqlite3
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
import isolation_bootstrap as isolation
from test_notification_migration_privacy import encrypted,NOW
from test_cummins_end_to_end_v2 import record,Clock


class Source:
    synthetic=True
    def __init__(self,clock,contract):
        self.clock,self.contract=clock,contract
        self.records=[record(id="release-company")]
        self.complete=False
        self.fail=False
        self.fetches=0
        self.observed_ms=None

    async def fetch_listing(self):
        from app.tpo.listing import ListingPage,decode_listing_pages
        self.fetches+=1
        if self.fail:raise OSError("fictional outage")
        return decode_listing_pages([ListingPage({"company_list":[r.model_dump() for r in self.records]},
            1,1,self.contract.source_namespace,True)],contract=self.contract,
            observed_ms=self.clock() if self.observed_ms is None else self.observed_ms,complete=self.complete)


@contextmanager
def runtime_for(x,*,freshness_ms=60000,generation=1,now_ms=NOW,autostart=True,explicit_contract=True):
    from app.database.notification_migrator import create_schema
    from app.monitoring.runtime_supervisor import RuntimeSupervisor,LegacyExclusion
    from app.config import RuntimeConfig,RuntimeMode
    from app.tpo.listing import ListingContract
    create_schema(x["c"],now_ms=NOW)
    clock=Clock();clock.now=now_ms
    contract=ListingContract("release-fixture","VIT",2028,"fictional-scoped-list",freshness_ms,True)
    source=Source(clock,contract)
    with isolation.start_test_child("authority") as child:
        runtime=RuntimeSupervisor(connection=x["c"],database=x["db"],codec=x["codec"],
            source=source,authority=child,clock=clock,listing_contract=contract if explicit_contract else None,
            exclusion=LegacyExclusion(True,True,True,True),
            config=RuntimeConfig(mode=RuntimeMode.V2_FAKE,controller_identity="release-owner",generation=generation,
                provider_identity="release-custody",custody_generation=1))
        try:
            if autostart:runtime.start()
            yield runtime,source,clock,child
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_real_application_exact_audience_projection_and_channels(encrypted):
    from app.main import create_application
    from app.database.event_repository import PolicyDenied
    x=encrypted
    users={}
    with pytest.raises(sqlite3.IntegrityError):
        x["db"].users.create_user("wrongyear@example.invalid",2027,"VIT_CE")
    for label,year,branch,verified,active in [
        ("matching",2028,"VIT_CE",True,True),("wrongbranch",2028,"VIT_IT",True,True),
        ("unverified",2028,"VIT_CE",False,True),
        ("inactive",2028,"VIT_CE",True,False),("foreign",2028,"VIIT_CE",True,True),
        ("unknown",2028,"UNKNOWN",True,True)]:
        uid=x["db"].users.create_user(label+"@example.invalid",year,branch)
        if verified:x["db"].users.set_verified(uid)
        if not active:x["db"].users.set_unsubscribed(uid)
        users[label]=uid
    no_preferences=x["db"].users.create_user("preferences-off@example.invalid",2028,"VIT_CE")
    x["db"].users.set_verified(no_preferences)
    with runtime_for(x) as (runtime,source,clock,child):
        runtime.repo.set_preferences(no_preferences,pref_internship=False,pref_placement=False,pref_ppo=False)
        runtime.repo.add_admin()
        app=create_application(runtime=runtime)
        assert await runtime.poll_once()
        runtime.pipeline.fanout_once()
        selected={r[0] for r in x["c"].execute("""SELECT a.user_id FROM event_audience e
            JOIN recipient_associations a USING(recipient_key) WHERE e.channel='SUBSCRIBER'""")}
        assert selected=={1,users["matching"]}
        assert x["c"].execute("SELECT count(*) FROM event_outbox").fetchone()[0]==2
        with TestClient(app) as client:
            response=client.get("/api/v1/opportunity-listing")
            assert response.status_code==200 and response.json()["total"]==1
            assert response.json()["items"][0]["id"]=="release-company"
        assert await runtime.drain_once()==3
        assert runtime.relay.request(child,"status",{})["physical_calls"]==3
        assert await runtime.poll_once()
        assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
        with pytest.raises(PolicyDenied):
            runtime.repo.approve("REDRIVE",runtime.repo._one("SELECT company_key FROM opportunity_events")["company_key"],1)


@pytest.mark.asyncio
async def test_application_runs_poll_and_independent_drain_with_observable_shutdown(encrypted):
    from app.main import run_application
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        await runtime.poll_once()
        entered=asyncio.Event()
        async def waiting_source():
            entered.set()
            await asyncio.Event().wait()
        source.fetch_listing=waiting_source
        stop=asyncio.Event()
        async def observe():
            try:
                await asyncio.wait_for(entered.wait(),5)
                for _ in range(50):
                    if x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]:return
                    await asyncio.sleep(.01)
                raise AssertionError("drain depended on completion of source poll")
            finally:stop.set()
        observer=asyncio.create_task(observe())
        try:
            # Lifecycle cleanup runs in the same Context that owns its bindings.
            await run_application(runtime,stop,poll_interval_ms=20)
            await observer
        finally:
            observer.cancel()
            await asyncio.gather(observer,return_exceptions=True)
        assert runtime.closed
        assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_selected_modes_and_final_physical_freshness(encrypted):
    from app.monitoring.notification_pipeline import V2Options
    from app.notifications.transport_protocol import EphemeralMail
    from app.notifications.transport_arbiter import identity_for
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        runtime.configure(V2Options(writer=True,fanout=True,drain=False,transport=False,info_ttl_ms=60000))
        assert await runtime.poll_once()
        runtime.pipeline.fanout_once()
        assert await runtime.drain_once()==0
        assert runtime.relay.request(child,"status",{})["physical_calls"]==0
        runtime.configure(V2Options(writer=True,fanout=True,drain=True,transport=True,info_ttl_ms=60000))
        assert runtime.gateway.prepare_slot("SUBSCRIBER")
        claim=runtime.repo.claim_delivery(channel="SUBSCRIBER")
        permit=runtime.repo.authorize_send(claim)
        mail=EphemeralMail("SUBSCRIBER","fictional@example.invalid","Fictional","Fictional",clock()+1000,
            {"List-Unsubscribe":"<https://watcher.example.invalid/unsubscribe>",
             "List-Unsubscribe-Post":"List-Unsubscribe=One-Click"})
        ticket=runtime.gateway.ticket(permit,mail)
        grant=runtime.relay.request(child,"admit",ticket)
        source.records=[record(id="release-company",regEndtime="09:00")]
        assert await runtime.poll_once()
        with pytest.raises(RuntimeError):
            runtime.relay.request(child,"consume",grant)
        assert runtime.relay.request(child,"status",{})["physical_calls"]==0
        assert not x["c"].execute("SELECT 1 FROM transport_facts WHERE fact_kind='ACCEPTED'").fetchone()


def test_health_does_not_claim_held_runtime_is_running(encrypted):
    from app.main import create_application
    with runtime_for(encrypted) as (runtime,source,clock,child):
        with TestClient(create_application(runtime=runtime)) as client:
            assert client.get("/health").status_code==200
            runtime.hold()
            response=client.get("/health")
            assert response.status_code==503
            assert response.json()["notification"]["production_enabled"] is False


def test_health_observes_owned_transport_process_exit(encrypted):
    from app.main import create_application
    with runtime_for(encrypted) as (runtime,source,clock,child):
        with TestClient(create_application(runtime=runtime)) as client:
            assert client.get("/health").status_code==200
            child.terminate()
            assert child.process.poll() is not None
            assert client.get("/health").status_code==503
            # Existing shutdown reports unavailable relay while still retiring
            # owned controller/custody in its finally blocks; no fabricated END.
            with pytest.raises((OSError,EOFError)):runtime.stop()
            assert runtime.closed


@pytest.mark.asyncio
async def test_observation_expiry_after_permit_is_rechecked_by_physical_child(encrypted):
    from app.notifications.transport_protocol import EphemeralMail
    x=encrypted
    with runtime_for(x,freshness_ms=20) as (runtime,source,clock,child):
        await runtime.poll_once()
        runtime.pipeline.fanout_once()
        assert runtime.gateway.prepare_slot("SUBSCRIBER")
        claim=runtime.repo.claim_delivery(channel="SUBSCRIBER")
        permit=runtime.repo.authorize_send(claim)
        mail=EphemeralMail("SUBSCRIBER","fictional@example.invalid","Fictional","Fictional",clock()+1000,
            {"List-Unsubscribe":"<https://watcher.example.invalid/unsubscribe>",
             "List-Unsubscribe-Post":"List-Unsubscribe=One-Click"})
        grant=runtime.relay.request(child,"admit",runtime.gateway.ticket(permit,mail))
        clock.now+=21
        runtime.relay.refresh(child)
        with pytest.raises(RuntimeError):
            runtime.relay.request(child,"consume",grant)
        assert runtime.relay.request(child,"status",{})["physical_calls"]==0


@pytest.mark.asyncio
async def test_lifecycle_safe_retry_unknown_backlog_and_encrypted_restart(encrypted):
    from app.notifications.transport_arbiter import identity_for
    from app.main import create_application
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        source.complete=True
        # The encrypted fixture already has four historical source identities.
        # A complete census includes them; omission would correctly emit four
        # separate absence updates rather than testing one recipient's retry.
        source.records.extend(record(id=key) for key in ("history-a","history-1","history-2","history-3"))
        await runtime.poll_once()
        source.complete=False
        runtime.relay.request(child,"configure",dict(kind="NOT_ACCEPTED",ended=True,fault=None))
        assert await runtime.drain_once()==0
        delivery=runtime.repo._one("SELECT * FROM notification_deliveries_v2")
        assert delivery["status"]=="PENDING" and delivery["attempt_count"]==1
        assert {r[0] for r in x["c"].execute("SELECT fact_kind FROM transport_facts")}=={"NOT_ACCEPTED","ENDED"}
        clock.now+=11
        runtime.relay.request(child,"configure",dict(kind="ACCEPTED",ended=True,fault=None))
        assert await runtime.drain_once()==1
        assert runtime.relay.request(child,"status",{})["physical_calls"]==2
        source.records=[record(id="release-company",regEndtime="09:00")]
        await runtime.poll_once()
        runtime.relay.request(child,"configure",dict(kind="UNKNOWN",ended=False,fault=None))
        assert await runtime.drain_once()==0
        status=runtime.relay.request(child,"status",{})
        assert status["physical_calls"]==3 and status["active"]==1
        assert runtime.repo._one("SELECT status FROM notification_deliveries_v2 WHERE status='UNCERTAIN'")
        # A second identical company name remains a distinct source identity,
        # and source intake/API advance while an earlier physical call blocks.
        source.records.append(record(id="release-second"))
        await runtime.poll_once()
        assert await runtime.drain_once()==0
        assert x["c"].execute("SELECT count(DISTINCT company_key) FROM opportunity_events").fetchone()[0]==2
        with TestClient(create_application(runtime=runtime)) as client:
            listing=client.get("/api/v1/opportunity-listing").json()
            assert {"release-company","release-second"}<={r["id"] for r in listing["items"]}
            assert listing["total"]==6 and listing["shown"]==5
        assert runtime.relay.request(child,"status",{})["physical_calls"]==3
        invocation=status["call_ids"][-1]
        runtime.relay.request(child,"end",identity_for(runtime.repo,invocation).to_wire())
        assert runtime.relay.request(child,"status",{})["gate"]=="FREE"
        facts=[tuple(r) for r in x["c"].execute("SELECT * FROM transport_facts ORDER BY 1,2")]
        versions=[tuple(r) for r in x["c"].execute("SELECT * FROM subscriber_versions ORDER BY 1")]
        events=[tuple(r) for r in x["c"].execute("SELECT * FROM opportunity_events ORDER BY event_id")]
    with runtime_for(x,generation=2,now_ms=NOW+11) as (runtime,source,clock,child):
        assert [tuple(r) for r in x["c"].execute("SELECT * FROM transport_facts ORDER BY 1,2")]==facts
        assert [tuple(r) for r in x["c"].execute("SELECT * FROM subscriber_versions ORDER BY 1")]==versions
        assert [tuple(r) for r in x["c"].execute("SELECT * FROM opportunity_events ORDER BY event_id")]==events
        assert await runtime.drain_once()==1
        assert runtime.relay.request(child,"status",{})["physical_calls"]==1
        assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='UNCERTAIN'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_optout_optin_and_late_subscriber_do_not_resurrect_old_audience(encrypted):
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        runtime.repo.add_admin()
        await runtime.poll_once();runtime.pipeline.fanout_once()
        old=runtime.repo._one("SELECT event_id FROM opportunity_events")["event_id"]
        x["db"].users.set_unsubscribed(1)
        runtime.repo.change_user(1,is_active=1)
        late=x["db"].users.create_user("late@example.invalid",2028,"VIT_CE")
        x["db"].users.set_verified(late)
        assert await runtime.drain_once()==1 # independent ADMIN obligation
        assert {r[0] for r in x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'")}=={"SUPPRESSED"}
        source.records=[record(id="release-company",regEndtime="09:00")]
        await runtime.poll_once();runtime.pipeline.fanout_once()
        selected={r[0] for r in x["c"].execute("""SELECT a.user_id FROM event_audience e JOIN recipient_associations a USING(recipient_key)
            JOIN notification_intents i USING(intent_id) WHERE e.channel='SUBSCRIBER' AND i.source_event_id<>?""",(old,))}
        assert selected=={1,late}
        assert await runtime.drain_once()==3
        assert runtime.relay.request(child,"status",{})["physical_calls"]==4


@pytest.mark.asyncio
async def test_started_submission_preserves_acceptance_after_source_update(encrypted):
    from app.notifications.transport_protocol import EphemeralMail
    from app.notifications.transport_arbiter import identity_for
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        await runtime.poll_once();runtime.pipeline.fanout_once()
        assert runtime.gateway.prepare_slot("SUBSCRIBER")
        claim=runtime.repo.claim_delivery(channel="SUBSCRIBER")
        permit=runtime.repo.authorize_send(claim)
        mail=EphemeralMail("SUBSCRIBER","fictional@example.invalid","Fictional","Fictional",clock()+1000,
            {"List-Unsubscribe":"<https://watcher.example.invalid/unsubscribe>","List-Unsubscribe-Post":"List-Unsubscribe=One-Click"})
        grant=runtime.relay.request(child,"admit",runtime.gateway.ticket(permit,mail))
        runtime.relay.request(child,"configure",dict(kind="ACCEPTED",ended=False,fault=None))
        runtime.relay.request(child,"consume",grant)
        assert runtime.relay.request(child,"status",{})["active"]==1
        source.records=[record(id="release-company",isactive="False")]
        await runtime.poll_once()
        assert runtime.repo.acknowledge(claim)=="SENT"
        assert runtime.relay.request(child,"status",{})["gate"]!="FREE"
        runtime.relay.request(child,"end",identity_for(runtime.repo,permit.invocation_id).to_wire())
        assert runtime.relay.request(child,"status",{})["gate"]=="FREE"
        assert runtime.repo._one("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?",(claim.key,))["status"]=="SENT"


def test_rendered_source_details_are_immutable_escaped_and_not_full_eligibility():
    from app.notifications.opportunity_renderer import render_opportunity
    rendered=render_opportunity(snapshot={"state":"OBSERVED","record":record(job_title="<Engineer>",
        cgpa="7.5",backlog_restrictions="None permitted",job_description="Fictional authentic details").model_dump()},
        reasons=["NEW"],role="INFO",channel="SUBSCRIBER",branch="VIT_CE",observed_ms=NOW,persisted_ms=NOW,rendered_ms=NOW)
    assert "&lt;Engineer&gt;" in rendered.html and "7.5" in rendered.html
    assert "None permitted" in rendered.html and "Fictional authentic details" in rendered.html
    assert "does not confirm full eligibility" in rendered.html


@pytest.mark.asyncio
async def test_restart_recovers_policy_before_operational_loop_and_api_construction(encrypted):
    from app.main import create_application,run_application
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        await runtime.poll_once()
    with runtime_for(x,generation=2,autostart=False,explicit_contract=False) as (runtime,source,clock,child):
        app=create_application(runtime=runtime)
        assert app.state.opportunity_database==x["db"].db_path
        assert not runtime.started
        with TestClient(app) as client:
            assert client.get("/health").status_code==503
            assert client.get("/api/v1/opportunity-listing").json()["total"]==1
        stop=asyncio.Event();stop.set()
        await run_application(runtime,stop,poll_interval_ms=20)
        assert runtime.closed and runtime.listing_contract==source.contract


@pytest.mark.asyncio
async def test_older_complete_census_cannot_replace_newer_positive_observation(encrypted):
    import json
    x=encrypted
    with runtime_for(x) as (runtime,source,clock,child):
        source.observed_ms=NOW-100
        await runtime.poll_once()
        event=runtime.repo._one("SELECT * FROM opportunity_events")
        assert event["observed_ms"]==NOW-100
        clock.now+=100
        source.observed_ms=NOW+50
        await runtime.poll_once()
        before=[tuple(r) for r in x["c"].execute("SELECT * FROM opportunity_events ORDER BY event_id")]
        source.observed_ms=NOW
        source.records=[];source.complete=True
        assert not await runtime.poll_once()
        assert [tuple(r) for r in x["c"].execute("SELECT * FROM opportunity_events ORDER BY event_id")]==before
        evidence=json.loads(runtime.repo._one("SELECT value FROM system_state WHERE key LIKE 'notification:listing:%'")["value"])
        assert evidence["state"]=="OBSERVED" and evidence["observed_ms"]==NOW+50

