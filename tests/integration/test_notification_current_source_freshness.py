"""Current-source contract atop the existing immutable event pipeline."""
import pytest
from test_cummins_end_to_end_v2 import integrated, record


def enable_current(x):
    from app.tpo.listing import ListingContract
    policy=ListingContract("fixture-v1","VIT",2028,"fictional-listing",60000,allow_complete=True)
    x["pipeline"].bind_listing_contract(policy)
    return policy


@pytest.mark.asyncio
@pytest.mark.parametrize("materialized", [False,True])
@pytest.mark.parametrize("change", [{"regEndtime":"09:00"}, {"isactive":"False"},
    {"programnew":"VIT-BTech-Information Technology"}, {"job_description":"New genuine requirement"}])
async def test_current_new_is_terminally_suppressed_after_later_change(integrated,materialized,change):
    x=integrated
    enable_current(x)
    assert await x["watcher"].check_once()
    first=x["pipeline"].last_events[0]
    if materialized:x["pipeline"].fanout_once()
    x["feed"].records=[record(**change)]
    assert await x["watcher"].check_once()
    assert x["pipeline"].last_events
    await x["watcher"].delivery_worker.process_batch_once()
    rows=x["c"].execute("""SELECT d.status FROM notification_deliveries_v2 d
        JOIN notification_intents i USING(intent_id) WHERE i.source_event_id=?""",(first,)).fetchall()
    assert len(rows)==2 and {r[0] for r in rows}=={"SUPPRESSED"}
    first_ids={r[0] for r in x["c"].execute("""SELECT delivery_id FROM notification_deliveries_v2 d
        JOIN notification_intents i USING(intent_id) WHERE i.source_event_id=?""",(first,))}
    assert not first_ids & {m["delivery_id"] for m in x["smtp"].messages}
    x["feed"].records=[record()]
    assert await x["watcher"].check_once()
    await x["watcher"].delivery_worker.process_batch_once()
    assert {r[0] for r in x["c"].execute("""SELECT d.status FROM notification_deliveries_v2 d
        JOIN notification_intents i USING(intent_id) WHERE i.source_event_id=?""",(first,))}=={"SUPPRESSED"}


@pytest.mark.asyncio
async def test_partial_and_outage_do_not_cancel_or_fabricate_closure(integrated):
    x=integrated
    enable_current(x)
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    x["feed"].records=[]
    x["feed"].source_complete=False
    assert await x["watcher"].check_once()
    async def unavailable():raise OSError("fictional source outage")
    x["feed"].fetch_companies=unavailable
    assert not await x["watcher"].check_once()
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert await x["watcher"].delivery_worker.process_batch_once()==2


@pytest.mark.asyncio
async def test_freshness_expiry_holds_then_unchanged_observation_recovers(integrated):
    x=integrated
    enable_current(x)
    # INFO TTL is deliberately longer than source evidence in this scenario.
    x["pipeline"].repository.info_ttl_ms=120000
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    x["clock"].now+=60001
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert {r[0] for r in x["c"].execute("SELECT status FROM notification_deliveries_v2")}=={"PENDING"}
    assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert await x["watcher"].delivery_worker.process_batch_once()==2


@pytest.mark.asyncio
async def test_complete_absence_suppresses_old_new_without_fabricating_employer_closure(integrated):
    x=integrated
    enable_current(x)
    assert await x["watcher"].check_once()
    first=x["pipeline"].last_events[0]
    x["feed"].records=[]
    x["feed"].source_complete=True
    assert await x["watcher"].check_once()
    await x["watcher"].delivery_worker.process_batch_once()
    assert {r[0] for r in x["c"].execute("""SELECT d.status FROM notification_deliveries_v2 d
        JOIN notification_intents i USING(intent_id) WHERE i.source_event_id=?""",(first,))}=={"SUPPRESSED"}


@pytest.mark.asyncio
async def test_local_fixture_cannot_consume_expired_source_permit(integrated):
    from app.database.event_repository import Fenced
    x=integrated
    enable_current(x)
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    claim=x["repo"].claim_delivery(channel="SUBSCRIBER")
    permit=x["repo"].authorize_send(claim)
    x["clock"].now+=60001
    with pytest.raises(Fenced):x["repo"].submit_fake(permit,x["smtp"])
    assert x["smtp"].calls==[]


def test_new_pipeline_recovers_installed_listing_contract(integrated):
    from app.monitoring.notification_pipeline import NotificationPipeline
    x=integrated
    policy=enable_current(x)
    attached=NotificationPipeline(x["repo"],options=x["pipeline"].options,transport=x["smtp"])
    assert attached.listing_contract==policy


@pytest.mark.asyncio
async def test_verified_past_deadline_never_sends_new_as_current(integrated):
    from app.tpo.listing import ListingContract
    x=integrated
    x["pipeline"].bind_listing_contract(ListingContract("past-deadline","VIT",2028,
        "fictional",60000,source_timezone="UTC",source_timezone_evidence="fictional-field-contract"))
    x["feed"].records=[record(regEnddate="01-Oct-2026",regEndtime="10:00")]
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert {r[0] for r in x["c"].execute("SELECT status FROM notification_deliveries_v2")}=={"SUPPRESSED"}
    assert x["smtp"].calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("between_drains_ms",[0,1100])
async def test_stale_page_does_not_starve_fresh_company_and_refresh_unholds(integrated,between_drains_ms):
    x=integrated
    enable_current(x)
    x["repo"].info_ttl_ms=120000
    for i in range(25):
        uid=x["db"].users.create_user(f"fairness-{i}@example.invalid",2028,"VIT_CE")
        x["db"].users.set_verified(uid)
    await x["watcher"].check_once();x["pipeline"].fanout_once()
    old=x["repo"]._one("SELECT event_id FROM opportunity_events")["event_id"]
    x["clock"].now+=60001
    x["feed"].records=[record(id="fresh-company")]
    await x["watcher"].check_once()
    for _ in range(4):
        await x["watcher"].delivery_worker.process_batch_once()
        x["clock"].now+=between_drains_ms
    for _ in range(2):await x["watcher"].delivery_worker.process_batch_once()
    assert x["c"].execute("""SELECT count(*) FROM notification_deliveries_v2 d JOIN notification_intents i USING(intent_id)
        WHERE i.source_event_id<>? AND d.status='SENT'""",(old,)).fetchone()[0]==27
    assert {tuple(r) for r in x["c"].execute("""SELECT d.status,d.attempt_count FROM notification_deliveries_v2 d
        JOIN notification_intents i USING(intent_id) WHERE i.source_event_id=?""",(old,))}=={("PENDING",0)}
    assert x["pipeline"].next_wait_ms()>10
    # A positive unchanged observation releases the source hold immediately,
    # without changing the old event or burning an attempt on the hold.
    x["feed"].records=[record(),record(id="fresh-company")]
    await x["watcher"].check_once()
    for _ in range(3):await x["watcher"].delivery_worker.process_batch_once()
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==54

