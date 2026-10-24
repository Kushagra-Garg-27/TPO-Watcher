"""Actual watcher v2 integration, fictional feed/transport and disposable SQLite only."""
import importlib.util
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.database.repository import DatabaseRepository
from app.database.notification_migrator import connect_disposable, create_schema
from app.database.event_repository import EventRepository, FakeTransport
from app.tpo.models import CompanyRecord


class Clock:
    def __init__(self):
        self.now = 1791936000000
    def __call__(self):
        return self.now


class Feed:
    synthetic = True
    source_complete = False
    source_timezone = None
    source_timezone_evidence = None
    def __init__(self, records):
        self.records, self.fetches = records, 0
    async def fetch_companies(self):
        self.fetches += 1
        return self.records


class SMTP(FakeTransport):
    def __init__(self, outcome="ACCEPTED", ended=True):
        super().__init__(outcome)
        self.ended, self.messages = ended, []
        self.outcomes = {}
    def submit_message(self, invocation, message):
        from app.monitoring.notification_pipeline import TransportResult
        self.calls.append(invocation)
        self.messages.append(dict(message, invocation_id=invocation))
        result = self.outcomes.get(message["channel"], self.outcome)
        if isinstance(result, BaseException):
            raise result
        return TransportResult(result, self.ended)


def record(**changes):
    data = dict(id="fictional-company", company="Fictional Engineering",
                regStartdate="14-Oct-2026", regStarttime="11:31",
                regEnddate="15-Oct-2026", regEndtime="10:00",
                regStartdatenew=None, regEnddatenew=None,
                tpoprogram="VIT-BTech-Computer Engineering",
                programnew="VIIT-BTech-Information Technology",
                organization="VIT,VIIT", placementtype="Internship + Performance based PPO",
                internshiptype="Internship", maxPackage="25000", minPackage="20000", isactive="True")
    data.update(changes)
    return CompanyRecord(**data)


@pytest.fixture
def integrated(tmp_path):
    assert importlib.util.find_spec("app.monitoring.notification_pipeline") is not None, "integrated v2 pipeline missing"
    from app.monitoring.notification_pipeline import NotificationPipeline, V2Options
    from app.monitoring.watcher import PlacementWatcher
    path = tmp_path / "integrated.sqlite"
    db = DatabaseRepository(str(path))
    uid = db.users.create_user("fictional-student@example.invalid", 2028, "VIT_CE")
    db.users.set_verified(uid)
    with connect_disposable(path) as c:
        clock = Clock()
        create_schema(c, now_ms=clock())
        repo = EventRepository(c, clock=clock)
        repo.add_admin()
        smtp = SMTP()
        options = V2Options(writer=True, fanout=True, drain=True, transport=True, info_ttl_ms=60000)
        pipeline = NotificationPipeline(repo, transport=smtp, options=options)
        feed = Feed([record()])
        watcher = PlacementWatcher(db=db, notification_pipeline=pipeline, source_client=feed)
        yield dict(c=c, db=db, clock=clock, repo=repo, pipeline=pipeline,
                   feed=feed, smtp=smtp, watcher=watcher, user=uid, path=path)


@pytest.mark.asyncio
async def test_actual_watcher_atomic_event_and_fake_delivery(integrated):
    x = integrated
    assert await x["watcher"].check_once() is True
    c = x["c"]
    assert c.execute("SELECT company FROM companies").fetchone()[0] == "Fictional Engineering"
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM notification_intents").fetchone()[0] == 1
    assert {r[0] for r in c.execute("SELECT channel FROM event_outbox")} == {"ADMIN", "SUBSCRIBER"}
    assert c.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM pending_notifications").fetchone()[0] == 0
    # Delivery is a separate pump, never another source fetch.
    assert await x["watcher"].delivery_worker.process_batch_once() == 2
    assert x["feed"].fetches == 1
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0] == 2
    assert len(x["smtp"].calls) == 2
    bodies = [m["html"] for m in x["smtp"].messages]
    assert all("11:31" in body and "10:00" in body for body in bodies)
    assert all("timezone unverified" in body.lower() and "unit unknown" in body.lower() for body in bodies)
    assert "does not confirm full eligibility" in bodies[1].lower()
    assert await x["watcher"].check_once() is True
    assert c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 1
    assert await x["watcher"].delivery_worker.process_batch_once() == 0

@pytest.mark.asyncio
async def test_twelve_cycle_actual_watcher_lifecycle(integrated):
    x=integrated
    c,db=x["c"],x["db"]
    extra={}
    for branch in ("VIT_IT","VIT_CSE_DS","VIT_AIDS"):
        uid=db.users.create_user(branch.lower()+"@example.invalid",2028,branch)
        db.users.set_verified(uid)
        extra[branch]=uid
    totals=[]
    async def cycle(records,complete=False,expected=0):
        x["feed"].records=records
        x["feed"].source_complete=complete
        x["clock"].now+=100
        before=c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]
        assert await x["watcher"].check_once()
        after=c.execute("SELECT count(*) FROM opportunity_events").fetchone()[0]
        assert after-before==expected
        for _ in range(3):
            await x["watcher"].delivery_worker.process_batch_once()
        totals.append(after)
    a=record()
    await cycle([a],expected=1)                                # A: new
    await cycle([record(regStarttime="10:00")],expected=1)       # B: earlier
    await cycle([record(regStarttime="12:00")],expected=1)       # C: later
    await cycle([record(regStartdate="16-Oct-2026")],expected=1) # D: date
    await cycle([],complete=False,expected=0)                  # E: uncertain visibility
    await cycle([record(regStartdate="16-Oct-2026")],expected=0)# F: same reappears
    g=record(regStartdate="16-Oct-2026",programnew="VIT-BTech-Information Technology,VIIT-BTech-Artificial Intelligence and Data Science")
    await cycle([g],expected=1)                                # G: independent union
    h=g.model_copy(update={"organization":"VIIT"})
    await cycle([h],expected=1)                                # H: VIT removed
    await cycle([g.model_copy(update={"regEndtime":"09:30"})],expected=1) # I: new UPDATE
    await cycle([g.model_copy(update={"regEndtime":"09:30"})],expected=0) # J: identical
    await cycle([],complete=True,expected=1)                   # K: authoritative absence
    await cycle([],complete=False,expected=0)                  # L: unverified empty
    assert totals==[1,2,3,4,4,4,5,6,7,7,8,8]
    assert [r[0] for r in c.execute("SELECT revision FROM opportunity_events ORDER BY revision")]==list(range(1,9))
    assert c.execute("SELECT count(*) FROM notification_intents").fetchone()[0]==8
    assert c.execute("SELECT count(*) FROM event_outbox").fetchone()[0]==16
    assert c.execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="FREE"
    assert c.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0]==0
    assert c.execute("SELECT count(*) FROM pending_notifications").fetchone()[0]==0
    by_branch={}
    for row in c.execute("""SELECT u.branch_canonical,count(*) FROM notification_deliveries_v2 d
        JOIN recipient_associations r USING(recipient_key) JOIN users u ON r.user_id=u.id
        WHERE d.status='SENT' GROUP BY u.branch_canonical"""):
        by_branch[row[0]]=row[1]
    assert by_branch=={"VIT_CE":7,"VIT_IT":3}
    assert len(x["smtp"].calls)==18
    assert all("employer registration closure" in m["html"] or "employer registration is open or reopened" in m["html"] for m in x["smtp"].messages)
    absent=[m for m in x["smtp"].messages if "no longer observed" in m["html"]]
    assert len(absent)==3
    assert x["feed"].fetches==12
    print("V2_TWELVE_CYCLE",dict(events=totals,recipients=by_branch,fake_accepted=18,fetches=12))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[
    ("regStarttime","09:00"),("regEndtime","09:00"),("regStartdate","16-Oct-2026"),
    ("regEnddate","17-Oct-2026"),("regStartdatenew","2026-10-14T09:30:00"),
    ("regEnddatenew","2026-10-16T10:00:00"),
    ("organization","VIIT"),("tpoprogram","VIT-BTech-Information Technology"),
    ("programnew","VIT-BTech-Information Technology"),
])
async def test_b1f_material_fields_through_atomic_watcher(integrated,field,value):
    x=integrated
    assert await x["watcher"].check_once()
    x["feed"].records=[record(**{field:value})]
    assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==2


@pytest.mark.asyncio
async def test_b1f_formatting_equivalence_and_a_b_a(integrated):
    x=integrated
    for data in (record(),record(regStarttime="11:31:00",tpoprogram=" vit - btech - computer engineering "),
                 record(regStarttime="09:00"),record()):
        x["feed"].records=[data]
        assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==3


@pytest.mark.asyncio
async def test_matching_preferences_and_specializations_through_actual_fanout(integrated):
    x=integrated
    for branch in ("VIT_CSE_DS","VIT_AIDS","VIT_IT"):
        uid=x["db"].users.create_user(branch+"@example.invalid",2028,branch)
        x["db"].users.set_verified(uid)
    x["db"].users.update_preferences(x["user"],False,True,False)
    x["feed"].records=[record(tpoprogram="VIT-BTech-Computer Science and Engineering (Data Science)",programnew="VIIT-BTech-Artificial Intelligence and Data Science")]
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert [r[0] for r in x["c"].execute("SELECT selected_branch FROM event_audience WHERE channel='SUBSCRIBER'")]==["VIT_CSE_DS"]
    x["feed"].records=[record(placementtype="Placement",internshiptype="Not Mentioned")]
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert x["c"].execute("SELECT count(*) FROM event_audience WHERE selected_branch='VIT_AIDS'").fetchone()[0]==0

@pytest.mark.asyncio
async def test_verified_source_zone_is_explicit_and_expired_action_does_not_send(integrated):
    x=integrated
    x["feed"].source_timezone="Asia/Kolkata"
    x["feed"].source_timezone_evidence="fictional-official-zone-contract"
    assert await x["watcher"].check_once()
    event=x["c"].execute("SELECT window_quality,deadline_ms,zone_evidence_reference FROM opportunity_events").fetchone()
    assert event[0]=="VERIFIED_INSTANT" and event[1] is not None and event[2]=="fictional-official-zone-contract"
    x["pipeline"].fanout_once()
    assert x["c"].execute("SELECT role FROM event_audience WHERE channel='SUBSCRIBER'").fetchone()[0]=="ACTION"
    x["clock"].now=event[1]+1
    assert await x["watcher"].delivery_worker.process_batch_once()==0
    assert x["smtp"].calls==[]
    assert {r[0] for r in x["c"].execute("SELECT status FROM notification_deliveries_v2")}=={"SUPPRESSED"}


@pytest.mark.asyncio
async def test_false_active_flag_with_verified_zone_is_only_informational(integrated):
    x=integrated
    x["feed"].source_timezone="Asia/Kolkata"
    x["feed"].source_timezone_evidence="fictional-official-zone-contract"
    x["feed"].records=[record(isactive="False")]
    assert await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    assert x["c"].execute("SELECT role FROM event_audience WHERE channel='SUBSCRIBER'").fetchone()[0]=="INFO"


@pytest.mark.asyncio
async def test_full_flags_still_reject_nonfake_transport_and_unmarked_feed(integrated):
    x=integrated
    from app.monitoring.notification_pipeline import NotificationPipeline
    from app.database.event_repository import PolicyDenied
    class RealLike:
        def submit_message(self,*args):raise AssertionError("must never call")
    with pytest.raises(PolicyDenied,match="fictional message transport"):
        NotificationPipeline(x["repo"],transport=RealLike(),options=x["pipeline"].options)
    x["feed"].synthetic=False
    assert await x["watcher"].check_once() is False
    assert x["feed"].fetches==0 and x["smtp"].calls==[]


@pytest.mark.asyncio
async def test_unsafe_records_disable_all_absence_and_source_failure_preserves_state(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    x["feed"].source_complete=True
    x["feed"].records=[record(id="bad",tpoprogram="VIT-BTech-Computer Engineering(")]
    assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert x["c"].execute("SELECT status FROM source_batch_items WHERE trusted_company_id='bad'").fetchone()[0]=="QUARANTINED"
    assert x["c"].execute("SELECT revision FROM opportunity_state WHERE legacy_company_id='fictional-company'").fetchone() is not None

@pytest.mark.asyncio
async def test_installed_schema_off_flags_do_not_fetch_or_send(integrated):
    x=integrated
    from app.monitoring.notification_pipeline import NotificationPipeline,V2Options
    pipeline=NotificationPipeline(x["repo"],options=V2Options())
    from app.monitoring.watcher import PlacementWatcher
    watcher=PlacementWatcher(db=x["db"],notification_pipeline=pipeline,source_client=x["feed"])
    assert await watcher.check_once() is False
    assert await watcher.delivery_worker.process_batch_once()==0
    assert x["feed"].fetches==0 and x["smtp"].calls==[]
    assert x["c"].execute("SELECT writer_mode,fanout_mode,send_mode FROM notification_runtime").fetchone()[:]==("OFF","OFF","OFF")


@pytest.mark.asyncio
@pytest.mark.parametrize("case",["duplicate","missing_id","no_completeness","failed_fetch"])
async def test_bad_feed_never_invents_absence_or_partial_company_transition(integrated,case):
    x=integrated
    assert await x["watcher"].check_once()
    if case=="duplicate":x["feed"].records=[record(),record(regStarttime="09:00")]
    elif case=="missing_id":x["feed"].records=[record(id=None)]
    elif case=="no_completeness":x["feed"].source_complete=None
    else:
        async def fail():raise RuntimeError("fictional source unavailable")
        x["feed"].fetch_companies=fail
    assert await x["watcher"].check_once() is False
    assert x["c"].execute("SELECT count(*) FROM opportunity_events").fetchone()[0]==1
    assert x["c"].execute("SELECT revision FROM opportunity_state").fetchone()[0]==1
    assert x["c"].execute("SELECT count(*) FROM event_outbox").fetchone()[0]==2

@pytest.mark.asyncio
@pytest.mark.parametrize("extra",[
    {"regEnddatenew":"2026-10-16T10:00:00"},
    {"regEnddatenew":"not-a-date"},
    {"regEndtime":"not-a-time"},
])
async def test_conflicting_or_unparsed_deadline_stays_informational(integrated,extra):
    x=integrated
    x["feed"].source_timezone="Asia/Kolkata"
    x["feed"].source_timezone_evidence="fictional-explicit-source-zone"
    x["feed"].records=[record(**extra)]
    assert await x["watcher"].check_once()
    event=x["c"].execute("SELECT window_quality,deadline_ms FROM opportunity_events").fetchone()
    assert event[:]==("SOURCE_LOCAL",None)
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert all("timezone unverified" in m["html"] for m in x["smtp"].messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra",[
    {"regEnddate":"2026-10-15T10:00:00+00:00","regEndtime":"10:00"},
    {"regEnddatenew":"2026-10-15T10:00:00+00:00"},
    {"regEnddate":"2026-10-15T10:00:00+00:00","regEndtime":None,"regEnddatenew":"2026-10-15T10:00:00"},
])
async def test_mixed_offset_local_deadline_disagreement_stays_info(integrated,extra):
    x=integrated
    x["feed"].source_timezone="Asia/Kolkata"
    x["feed"].source_timezone_evidence="fictional-explicit-source-zone"
    x["feed"].records=[record(**extra)]
    assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT window_quality,deadline_ms FROM opportunity_events").fetchone()[:]==("SOURCE_LOCAL",None)
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    assert {r[0] for r in x["c"].execute("SELECT role FROM event_audience WHERE channel='SUBSCRIBER'")}=={"INFO"}
    assert all("timezone unverified" in m["html"] for m in x["smtp"].messages)


@pytest.mark.asyncio
async def test_mixed_deadline_candidates_agree_after_explicit_zone_resolution(integrated):
    x=integrated
    x["feed"].source_timezone="Asia/Kolkata"
    x["feed"].source_timezone_evidence="fictional-explicit-source-zone"
    x["feed"].records=[record(regEnddate="2026-10-15T04:30:00+00:00",regEndtime="10:00",
                              regEnddatenew="2026-10-15T10:00:00")]
    assert await x["watcher"].check_once()
    event=x["c"].execute("SELECT window_quality,deadline_ms FROM opportunity_events").fetchone()
    assert event[:]==("VERIFIED_INSTANT",int(datetime(2026,10,15,4,30,tzinfo=timezone.utc).timestamp()*1000))
    x["pipeline"].fanout_once()
    assert x["c"].execute("SELECT role FROM event_audience WHERE channel='SUBSCRIBER'").fetchone()[0]=="ACTION"


@pytest.mark.asyncio
@pytest.mark.parametrize("day,clock",[("2026-11-01","01:30"),("2026-03-08","02:30")])
async def test_ambiguous_or_nonexistent_source_local_time_stays_info(integrated,day,clock):
    x=integrated
    x["feed"].source_timezone="America/New_York"
    x["feed"].source_timezone_evidence="fictional-explicit-source-zone"
    x["feed"].records=[record(regEnddate=day,regEndtime=clock)]
    assert await x["watcher"].check_once()
    assert x["c"].execute("SELECT window_quality,deadline_ms FROM opportunity_events").fetchone()[:]==("SOURCE_LOCAL",None)
    x["pipeline"].fanout_once()
    assert x["c"].execute("SELECT role FROM event_audience WHERE channel='SUBSCRIBER'").fetchone()[0]=="INFO"





