import asyncio
import json
import pytest

from app.database.repository import DatabaseRepository
from app.database.notification_migrator import connect_disposable, create_schema
from app.database.event_repository import EventRepository, FakeTransport, PolicyDenied, Fenced
from app.monitoring.notification_pipeline import NotificationPipeline, V2Options, TransportResult
from app.monitoring.watcher import PlacementWatcher
from app.tpo.client import TPOClient, API_ENDPOINT, AuthenticationError
from app.tpo.listing import ListingContract
from app.auth.manager import AuthManager
from app.monitoring.detector import CompanyDetector


class SyntheticResponse:
    url = API_ENDPOINT

    def __init__(self, body, status=200):
        self.body, self.status = body, status
        self.ok = 200 <= status < 300

    async def text(self):
        return self.body if isinstance(self.body, str) else json.dumps(self.body)

    async def json(self):
        return self.body if isinstance(self.body, dict) else json.loads(self.body)


class SyntheticCapture:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @property
    def value(self):
        async def result():
            if isinstance(self.response, Exception):
                raise self.response
            return self.response
        return result()


class SyntheticPage:
    url = "https://tpo.vierp.in/home"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.first = self
        self.fetch_count = 0

    def is_closed(self):
        return False

    def expect_response(self, predicate, **kwargs):
        self.fetch_count += 1
        response = next(self.responses)
        if not isinstance(response, Exception):
            assert predicate(response)
        return SyntheticCapture(response)

    def locator(self, selector):
        return self

    async def is_visible(self):
        return True

    async def click(self, **kwargs):
        pass

    async def goto(self, url, **kwargs):
        self.url = url

    async def count(self):
        return 0


class NoSendTransport(FakeTransport):
    def __init__(self):
        super().__init__("ACCEPTED")
        self.messages = []

    def submit_message(self, invocation, message):
        self.calls.append(invocation)
        self.messages.append(dict(message, invocation_id=invocation))
        return TransportResult("ACCEPTED", True)


class UncertainTransport(FakeTransport):
    def __init__(self):
        super().__init__("UNKNOWN")
        self.messages = []

    def submit_message(self, invocation, message):
        self.calls.append(invocation)
        self.messages.append(dict(message, invocation_id=invocation))
        return TransportResult("UNKNOWN", False)


def synthetic_contract(allow_complete=False):
    return ListingContract(
        source_namespace="synthetic-watcher-integration",
        institution="VIT",
        cohort=2028,
        evidence_reference="synthetic-dashboard-spec",
        freshness_ms=60000,
        allow_complete=allow_complete,
    )


def sample_company(identity=9001, company_name="Synthetic Systems Inc", **overrides):
    record = {
        "id": identity,
        "company": company_name,
        "company_code": f"COD-{identity}",
        "locations": None,
        "placement_process_locations": None,
        "job_description": None,
        "maxPackage": 18000,
        "minPackage": 0,
        "from_schedule_date": None,
        "to_schedule_date": None,
        "regStartdate": None,
        "regStarttime": None,
        "regEnddate": None,
        "regEndtime": None,
        "regStartdatenew": None,
        "regEnddatenew": None,
        "contact_person_name": "Synthetic Recruiter",
        "contact_email": "recruiter@example.invalid",
        "contact_phone": None,
        "academicyear": "2026-27",
        "semester": None,
        "placementtype": "Internship + PPO",
        "companytype": "Product",
        "internshiptype": None,
        "industry": [],
        "skill": [],
        "isactive": True,
        "isoffertobeacceptedbystudent": False,
        "tpoprogram": ["VIT-BTech-Computer Engineering"],
        "programnew": ["VIIT-BTech-Information Technology"],
        "organization": ["VIT", "VIIT"],
    }
    record.update(overrides)
    return record


@pytest.fixture
def isolated_watcher_v2(tmp_path):
    db_path = tmp_path / "watcher_v2_integration.sqlite"
    legacy_repo = DatabaseRepository(str(db_path))

    with connect_disposable(db_path) as conn:
        create_schema(conn, now_ms=1000)
        transport = NoSendTransport()
        v2_repo = EventRepository(conn, clock=lambda: 1000, record_mode=True)
        options = V2Options(writer=True, fanout=True, drain=True, transport=True, info_ttl_ms=60000)
        pipeline = NotificationPipeline(v2_repo, transport=transport, options=options)
        pipeline.bind_legacy(legacy_repo)
        contract = synthetic_contract()
        pipeline.bind_listing_contract(contract)

        # 1. Qualified, consenting student (eligible cohort 2028, eligible branch VIT_CE, verified, active)
        u1 = legacy_repo.users.create_user("student_eligible@example.invalid", 2028, "VIT_CE")
        legacy_repo.users.set_verified(u1)

        # 2. Unverified student (eligible branch/cohort, but NOT verified)
        legacy_repo.users.create_user("student_unverified@example.invalid", 2028, "VIT_CE")

        # 3. Ineligible branch student (verified, cohort 2028, but non-matching branch VIT_MECH)
        u3 = legacy_repo.users.create_user("student_wrong_branch@example.invalid", 2028, "VIT_MECH")
        legacy_repo.users.set_verified(u3)

        # 4. Inactive/unconsented student (verified, cohort 2028, VIT_CE, but opted-out is_active=0)
        u4 = legacy_repo.users.create_user("student_inactive@example.invalid", 2028, "VIT_CE")
        legacy_repo.users.set_verified(u4)
        legacy_repo.users.set_unsubscribed(u4)

        yield conn, legacy_repo, pipeline, transport, contract


# ---------------------------------------------------------------------------
# Scenario 1 & 2: Six distinct company IDs with five distinct names, proving
# company ID—not name—is the stable key, and successful source observation
# through approved synthetic TPOClient.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_1_and_2_distinct_company_ids_and_observation(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    # Scenario 1: Six distinct company IDs with five distinct names.
    # IDs 9001 and 9006 share the exact name 'Alpha Corp' to prove ID is the stable key.
    companies_data = [
        sample_company(9001, "Alpha Corp"),
        sample_company(9002, "Beta Systems"),
        sample_company(9003, "Gamma Labs"),
        sample_company(9004, "Delta Tech"),
        sample_company(9005, "Epsilon AI"),
        sample_company(9006, "Alpha Corp"),
    ]
    envelope = {"status": "200", "company_list": companies_data}
    synthetic_page = SyntheticPage([SyntheticResponse(envelope)])

    # Scenario 2: Successful source observation through approved synthetic TPOClient
    client = TPOClient(
        None,
        synthetic_page,
        listing_contract=contract,
        clock=lambda: 1000,
        synthetic=True,
    )
    watcher = PlacementWatcher(
        db=legacy_repo,
        notification_pipeline=pipeline,
        source_client=client,
    )

    poll_ok = await watcher.check_once()
    assert poll_ok is True
    assert synthetic_page.fetch_count == 1

    # Verify company ID (not name) is the stable key
    stored_companies = conn.execute("SELECT id, company FROM companies ORDER BY id").fetchall()
    assert len(stored_companies) == 6
    stored_ids = [str(row[0]) for row in stored_companies]
    assert stored_ids == ["9001", "9002", "9003", "9004", "9005", "9006"]
    
    # 9001 and 9006 have identical name but separate rows
    assert stored_companies[0][1] == "Alpha Corp"
    assert stored_companies[5][1] == "Alpha Corp"
    assert stored_companies[0][0] != stored_companies[5][0]

    # Verify that company ID 9001 and 9006 have distinct opportunity_state company_keys
    company_keys = conn.execute("SELECT company_key FROM opportunity_state ORDER BY company_key").fetchall()
    assert len(set(ck[0] for ck in company_keys)) == 6


# ---------------------------------------------------------------------------
# Scenario 3: Correct immutable source events and company projection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_3_immutable_source_events_and_projection(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [
        sample_company(9001, "Alpha Corp"),
        sample_company(9002, "Beta Systems"),
        sample_company(9003, "Gamma Labs"),
        sample_company(9004, "Delta Tech"),
        sample_company(9005, "Epsilon AI"),
        sample_company(9006, "Alpha Corp"),
    ]
    envelope = {"status": "200", "company_list": companies_data}
    page = SyntheticPage([SyntheticResponse(envelope)])
    client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

    assert await watcher.check_once() is True

    # Immutable source events in opportunity_events
    events = conn.execute("SELECT company_key, reasons_json, event_id FROM opportunity_events ORDER BY event_id").fetchall()
    assert len(events) == 6
    event_ids = [ev[2] for ev in events]
    assert len(set(event_ids)) == 6
    assert all(isinstance(eid, str) and len(eid) == 32 for eid in event_ids)
    for ev in events:
        assert json.loads(ev[1]) == ["NEW"]

    # Projections into legacy companies table
    projections = conn.execute("SELECT id, max_package, eligible_programs FROM companies ORDER BY id").fetchall()
    assert len(projections) == 6
    assert all(p[1] == '18000' for p in projections)
    assert all("Computer Engineering" in p[2] for p in projections)


# ---------------------------------------------------------------------------
# Scenario 4: Atomic notification intent and outbox generation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_4_atomic_intent_and_outbox_generation(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [
        sample_company(9001, "Alpha Corp"),
        sample_company(9002, "Beta Systems"),
        sample_company(9003, "Gamma Labs"),
        sample_company(9004, "Delta Tech"),
        sample_company(9005, "Epsilon AI"),
        sample_company(9006, "Alpha Corp"),
    ]
    envelope = {"status": "200", "company_list": companies_data}
    page = SyntheticPage([SyntheticResponse(envelope)])
    client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

    assert await watcher.check_once() is True

    # 6 notification intents generated atomically with source events
    intents = conn.execute("SELECT intent_id, kind FROM notification_intents ORDER BY intent_id").fetchall()
    assert len(intents) == 6
    assert all(i[1] == "SOURCE_EVENT" for i in intents)

    # 12 outbox entries: 1 ADMIN and 1 SUBSCRIBER per intent
    outbox = conn.execute("SELECT intent_id, channel, status FROM event_outbox").fetchall()
    assert len(outbox) == 12
    admin_outbox = [row for row in outbox if row[1] == "ADMIN"]
    sub_outbox = [row for row in outbox if row[1] == "SUBSCRIBER"]
    assert len(admin_outbox) == 6
    assert len(sub_outbox) == 6
    assert all(row[2] == "PENDING" for row in outbox)


# ---------------------------------------------------------------------------
# Scenario 5: Qualified, consenting fictional audiences
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_5_qualified_consenting_fictional_audiences(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [
        sample_company(9001, "Alpha Corp"),
        sample_company(9002, "Beta Systems"),
    ]
    envelope = {"status": "200", "company_list": companies_data}
    page = SyntheticPage([SyntheticResponse(envelope)])
    client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

    assert await watcher.check_once() is True

    # Drain deliveries
    delivered = await watcher.delivery_worker.process_batch_once()
    # Exactly 2 subscriber deliveries: only student_eligible@example.invalid qualifies.
    # Unverified student, wrong branch (VIT_MECH), and inactive opted-out student are excluded.
    assert delivered == 2

    deliveries = conn.execute("SELECT recipient_key, channel, status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchall()
    assert len(deliveries) == 2
    assert all(d[2] == "SENT" for d in deliveries)

    # Verify recipient_key maps to eligible student only
    associations = conn.execute("SELECT user_id, recipient_key FROM recipient_associations").fetchall()
    eligible_user = legacy_repo.users.get_by_email("student_eligible@example.invalid")
    eligible_recipient_key = [a[1] for a in associations if a[0] == eligible_user["id"]][0]
    assert all(d[0] == eligible_recipient_key for d in deliveries)


# ---------------------------------------------------------------------------
# Scenario 6: Independent delivery draining without another source poll
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_6_independent_delivery_draining_without_source_poll(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [sample_company(9001, "Alpha Corp")]
    envelope = {"status": "200", "company_list": companies_data}
    page = SyntheticPage([SyntheticResponse(envelope)])
    client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

    # Initial poll
    assert await watcher.check_once() is True
    assert page.fetch_count == 1

    # Draining delivery
    delivered = await watcher.delivery_worker.process_batch_once()
    assert delivered == 1
    # Page fetch count MUST remain exactly 1 - no additional poll during drain
    assert page.fetch_count == 1

    # Second drain call when empty: still no poll
    empty_drain = await watcher.delivery_worker.process_batch_once()
    assert empty_drain == 0
    assert page.fetch_count == 1


# ---------------------------------------------------------------------------
# Scenario 7: No physical email transmission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_7_no_physical_email_transmission(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [sample_company(9001, "Alpha Corp")]
    envelope = {"status": "200", "company_list": companies_data}
    page = SyntheticPage([SyntheticResponse(envelope)])
    client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

    assert await watcher.check_once() is True
    delivered = await watcher.delivery_worker.process_batch_once()
    assert delivered == 1

    # Verified recorded in synthetic transport, zero external socket / SMTP transmission
    assert len(transport.calls) == 1
    assert len(transport.messages) == 1
    msg = transport.messages[0]
    assert msg["channel"] == "SUBSCRIBER"
    assert "Alpha Corp" in msg["subject"] or "Alpha Corp" in msg["html"]
    assert "headers" in msg
    assert "List-Unsubscribe" in msg["headers"]


# ---------------------------------------------------------------------------
# Scenario 8: Source AuthenticationError fails safely without implicit login
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_8_auth_error_fails_safely_without_implicit_login(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    # 401 error envelope from source
    synthetic_page = SyntheticPage([SyntheticResponse({"status": "401", "error": "Session Expired"}, status=401)])
    client = TPOClient(
        None,
        synthetic_page,
        listing_contract=contract,
        clock=lambda: 1000,
        synthetic=True,
    )
    watcher = PlacementWatcher(
        db=legacy_repo,
        notification_pipeline=pipeline,
        source_client=client,
    )

    # AuthManager is None for synthetic v2
    assert watcher.auth is None

    # Check should catch AuthenticationError, log, and return False safely without re-authenticating
    result = await watcher.check_once()
    assert result is False
    assert watcher.auth is None
    assert conn.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Scenario 9: Rejection of missing or unapproved source client
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_9_rejection_of_missing_or_unapproved_source_client(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    # 1. Missing source client rejected at init
    with pytest.raises(ValueError, match="explicit fictional source client required"):
        PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=None)

    # 2. Source client without synthetic=True rejected at init
    unapproved_client = object()
    with pytest.raises(ValueError, match="explicit fictional feed required"):
        PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=unapproved_client)

    # 3. Source client with synthetic=False rejected
    class NonSyntheticFeed:
        synthetic = False
        async def fetch_companies(self): return []

    with pytest.raises(ValueError, match="explicit fictional feed required"):
        PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=NonSyntheticFeed())

    # 4. Source client missing callable fetch method rejected
    class MissingCallableFeed:
        synthetic = True

    with pytest.raises(ValueError, match="callable fetch_companies or fetch_listing"):
        PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=MissingCallableFeed())

    # 5. Missing source client rejected at check_once() if altered
    valid_page = SyntheticPage([SyntheticResponse({"status": "200", "company_list": []})])
    valid_client = TPOClient(None, valid_page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=valid_client)

    watcher.source_client = None
    with pytest.raises(PolicyDenied, match="explicit fictional source client required"):
        await watcher.check_once()

    # 6. Unapproved non-synthetic source client rejected safely at check_once() (returns False, 0 fetches)
    watcher.source_client = NonSyntheticFeed()
    assert await watcher.check_once() is False

    # 7. Feed missing callable rejected safely at check_once() (returns False, 0 fetches)
    watcher.source_client = MissingCallableFeed()
    assert await watcher.check_once() is False


# ---------------------------------------------------------------------------
# Scenario 10: Incomplete source observations cannot falsely remove companies
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_10_incomplete_observations_never_falsely_reconcile(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    # Observation 1: company 9001 present
    page1 = SyntheticPage([SyntheticResponse({"status": "200", "company_list": [sample_company(9001)]})])
    client1 = TPOClient(None, page1, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher1 = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client1)
    assert await watcher1.check_once() is True

    assert conn.execute("SELECT count(*) FROM companies WHERE id='9001'").fetchone()[0] == 1

    # Observation 2: only company 9002 present, but observation is incomplete (complete=False)
    page2 = SyntheticPage([SyntheticResponse({"status": "200", "company_list": [sample_company(9002)]})])
    client2 = TPOClient(None, page2, listing_contract=contract, clock=lambda: 1000, synthetic=True)
    watcher2 = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client2)
    assert await watcher2.check_once() is True

    # Company 9001 must NOT be deactivated or reconciled away because the source observation is partial
    assert conn.execute("SELECT count(*) FROM companies WHERE id='9001'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM companies WHERE id='9002'").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Scenario 11: Writer ownership, uncertainty, and no-retry guarantees
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_11_writer_fencing_and_uncertainty_safety(tmp_path):
    db_path = tmp_path / "uncertainty_fencing.sqlite"
    legacy_repo = DatabaseRepository(str(db_path))

    with connect_disposable(db_path) as conn:
        create_schema(conn, now_ms=1000)
        uncertain_transport = UncertainTransport()
        v2_repo = EventRepository(conn, clock=lambda: 1000, record_mode=True)
        options = V2Options(writer=True, fanout=True, drain=True, transport=True, info_ttl_ms=60000)
        pipeline = NotificationPipeline(v2_repo, transport=uncertain_transport, options=options)
        pipeline.bind_legacy(legacy_repo)
        contract = synthetic_contract()
        pipeline.bind_listing_contract(contract)

        user_id = legacy_repo.users.create_user("uncertain_student@example.invalid", 2028, "VIT_CE")
        legacy_repo.users.set_verified(user_id)

        page = SyntheticPage([SyntheticResponse({"status": "200", "company_list": [sample_company(9001)]})])
        client = TPOClient(None, page, listing_contract=contract, clock=lambda: 1000, synthetic=True)
        watcher = PlacementWatcher(db=legacy_repo, notification_pipeline=pipeline, source_client=client)

        assert await watcher.check_once() is True

        # Process draining under UNKNOWN transport outcome
        # Under T6, UNKNOWN outcome is recorded but never causes immediate physical resend
        sent = await watcher.delivery_worker.process_batch_once()
        assert sent == 0  # not counted as definitive SENT
        # Delivery must be recovered for backoff, not duplicated
        delivery = conn.execute("SELECT status FROM notification_deliveries_v2 WHERE delivery_id=1").fetchone()
        assert delivery[0] == "UNCERTAIN"  # Preserves T6 uncertainty contract, preventing duplicate physical send


# ---------------------------------------------------------------------------
# Scenario 12: Legacy v1 behavior preserved when v2 is not selected
# ---------------------------------------------------------------------------
def test_scenario_12_legacy_v1_preservation_outside_v2(tmp_path):
    db_path = tmp_path / "legacy_v1.sqlite"
    legacy_repo = DatabaseRepository(str(db_path))

    watcher = PlacementWatcher(db=legacy_repo)
    assert watcher.notification_pipeline is None
    assert isinstance(watcher.auth, AuthManager)
    assert isinstance(watcher.detector, CompanyDetector)
    assert watcher.delivery_worker.notification_pipeline is None


# ---------------------------------------------------------------------------
# End-to-End: Full cycle integrating Scenarios 1-7 in an end-to-end run
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_watcher_notification_pipeline_full_cycle(isolated_watcher_v2):
    conn, legacy_repo, pipeline, transport, contract = isolated_watcher_v2

    companies_data = [
        sample_company(9001, "Alpha Corp"),
        sample_company(9002, "Beta Systems"),
        sample_company(9003, "Gamma Labs"),
        sample_company(9004, "Delta Tech"),
        sample_company(9005, "Epsilon AI"),
        sample_company(9006, "Alpha Corp"),
    ]
    envelope = {"status": "200", "company_list": companies_data}
    synthetic_page = SyntheticPage([SyntheticResponse(envelope)])

    client = TPOClient(
        None,
        synthetic_page,
        listing_contract=contract,
        clock=lambda: 1000,
        synthetic=True,
    )
    watcher = PlacementWatcher(
        db=legacy_repo,
        notification_pipeline=pipeline,
        source_client=client,
    )

    # 1. Observation
    poll_ok = await watcher.check_once()
    assert poll_ok is True
    assert synthetic_page.fetch_count == 1

    # 2. Projection & Events
    events = conn.execute("SELECT company_key FROM opportunity_events ORDER BY event_id").fetchall()
    assert len(events) == 6
    stored = conn.execute("SELECT id FROM companies ORDER BY id").fetchall()
    assert len(stored) == 6

    # 3. Intent & Outbox
    intents = conn.execute("SELECT intent_id FROM notification_intents").fetchall()
    assert len(intents) == 6
    outbox = conn.execute("SELECT intent_id, channel, status FROM event_outbox").fetchall()
    assert len(outbox) == 12

    # 4. Qualified audience draining
    delivered = await watcher.delivery_worker.process_batch_once()
    assert delivered == 6  # 6 for the 1 eligible verified student
    assert synthetic_page.fetch_count == 1  # No secondary poll

    # 5. Clean shutdown
    await watcher.shutdown()
