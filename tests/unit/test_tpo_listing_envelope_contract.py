"""Synthetic VIERP envelopes through the real decoder/client; no portal access."""
import json

import pytest
from playwright.async_api import Error as PlaywrightError

from app.tpo.client import API_ENDPOINT, AuthenticationError, TPOClient
from app.tpo.listing import (
    ListingContract, ListingPage, SourceEvidenceError, decode_listing_pages,
    validate_listing_body,
)


def fictional_company(identity=701, **changes):
    record = dict(
        id=identity, company="Fictional Shared Employer", company_code="fixture",
        locations=None, placement_process_locations=None, job_description=None,
        maxPackage=12345, minPackage=0, from_schedule_date=None, to_schedule_date=None,
        regStartdate=None, regStarttime=None, regEnddate=None, regEndtime=None,
        regStartdatenew=None, regEnddatenew=None,
        contact_person_name="Fictional contact", contact_email="contact@example.invalid",
        contact_phone=None, academicyear="2026-27", semester=None,
        placementtype="Internship", companytype="Private", internshiptype=None,
        industry=[], skill=[], isactive=True, isoffertobeacceptedbystudent=False,
        tpoprogram=["VIT-BTech-Computer Engineering", "BTech-Information Technology"],
        programnew=[None, "VIIT-BTech-Information Technology", "VU-BTech-Mechanical Engineering"],
        organization=["VIT", "VIIT", "VU"],
    )
    record.update(changes)
    return record


def fictional_contract():
    # Explicit fictional test policy, never inferred from the academic-year label.
    return ListingContract("fictional-envelope", "VIT", 2028, "synthetic-owner-policy", 60000)


def envelope(records, status="200"):
    return {"status": status, "company_list": records}


def decode(body):
    return decode_listing_pages(
        [ListingPage(body, 1, 1, "fictional-envelope", True)],
        contract=fictional_contract(), observed_ms=100,
    )


@pytest.mark.parametrize("records", [[], [fictional_company()]])
def test_string_200_accepts_only_a_valid_current_listing(records):
    body = envelope(records)
    assert validate_listing_body(body) is None
    result = decode(body)
    assert [record.id for record in result.records] == (["701"] if records else [])
    assert result.complete is False


@pytest.mark.parametrize("status", [None, "success", "Success", True, 200])
def test_established_success_variants_keep_their_return_contract(status):
    assert validate_listing_body(envelope([], status)) is None


@pytest.mark.parametrize("status", [
    "201", "400", "500", "error", "failed", "", " 200", "200 ",
    "0200", "200.0", "SUCCESS", "true", 201, 400, 500, False, {}, [],
])
def test_undocumented_status_never_authorizes_even_a_valid_array(status):
    with pytest.raises(SourceEvidenceError):
        decode(envelope([fictional_company()], status))


@pytest.mark.parametrize("body", [
    {"status": "200"}, {"status": "200", "company_list": None},
    {"status": "200", "company_list": {}}, {"status": "200", "company_list": "[]"},
    {"status": "200", "company_list": [], "error": "fictional-expired"},
    None, [], "{malformed", 200, True,
])
def test_status_cannot_bypass_envelope_or_list_validation(body):
    with pytest.raises(SourceEvidenceError):
        decode(body)


@pytest.mark.parametrize("identity", [None, True, False, 1.5, -1, "", " 701", "null", {}, []])
def test_invalid_identity_is_rejected_before_model_coercion(identity):
    with pytest.raises(SourceEvidenceError):
        decode(envelope([fictional_company(identity)]))


def test_distinct_numeric_ids_survive_same_name_and_nullable_field_decoding():
    first = fictional_company()
    del first["regEnddatenew"]
    result = decode(envelope([first, fictional_company(702)]))
    assert [record.id for record in result.records] == ["701", "702"]
    assert {record.company for record in result.records} == {"Fictional Shared Employer"}
    record = result.records[0]
    assert record.regStartdate is None and record.regEndtime is None
    assert record.regEnddatenew is None
    assert record.programnew == "VIIT-BTech-Information Technology, VU-BTech-Mechanical Engineering"
    assert record.organization == "VIT, VIIT, VU"
    assert record.maxPackage == "12345" and record.minPackage == "0"
    assert record.academicyear == "2026-27"
    assert "graduation_year" not in record.model_dump()
    assert "compensation_unit" not in record.model_dump()
    assert result.contract.source_timezone is None

    from app.subscribers.canonical import extract_eligible_canonical_branches
    branches = extract_eligible_canonical_branches(record.tpoprogram, record.programnew, record.organization)
    assert {branch.value for branch in branches} == {"VIT_CE"}


def test_conflicting_duplicate_id_is_not_silently_merged():
    with pytest.raises(SourceEvidenceError):
        decode(envelope([fictional_company(), fictional_company(company="Another fictional employer")]))


class Response:
    url = API_ENDPOINT

    def __init__(self, body, status=200):
        self.body, self.status = body, status
        self.ok = 200 <= status < 300

    async def text(self):
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


class Capture:
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


class FictionalPage:
    """Only the browser boundary is fake; client parsing/cache behavior stays real."""
    url = "https://tpo.vierp.in/home"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.first = self

    def is_closed(self):
        return False

    def expect_response(self, predicate, **kwargs):
        response = next(self.responses)
        if not isinstance(response, Exception):
            assert predicate(response)
        return Capture(response)

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


def fictional_client(responses, clock=None):
    return TPOClient(None, FictionalPage(responses), listing_contract=fictional_contract(),
                     clock=clock or (lambda: 100))


@pytest.mark.asyncio
async def test_success_replaces_previous_body_and_empty_does_not_replay_history():
    client = fictional_client([
        Response(envelope([fictional_company()])),
        Response(envelope([fictional_company(702)])), Response(envelope([])),
    ])
    assert [record.id for record in (await client.fetch_listing()).records] == ["701"]
    assert [record.id for record in (await client.fetch_listing()).records] == ["702"]
    empty = await client.fetch_listing()
    assert empty.records == () and empty.complete is False


FAILURES = [
    (Response(envelope([]), 401), AuthenticationError),
    (Response(envelope([]), 403), AuthenticationError),
    (Response(envelope([]), 500), Exception),
    (Response(envelope([], "failed")), SourceEvidenceError),
    (Response({"status": "200"}), SourceEvidenceError),
    (Response({"status": "200", "company_list": None}), SourceEvidenceError),
    (Response({"status": "200", "company_list": [], "error": "expired"}), AuthenticationError),
    (Response("{not-json"), Exception),
    (Response([]), SourceEvidenceError),
    (PlaywrightError("fictional timeout"), PlaywrightError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected_error", FAILURES,
                         ids=["http401", "http403", "http500", "body-failure", "missing-list",
                              "null-list", "auth-body", "malformed-json", "nonobject", "timeout"])
async def test_failed_request_cannot_return_cached_body_as_a_fresh_observation(failure, expected_error):
    clock_calls = []

    def clock():
        clock_calls.append(100 + len(clock_calls))
        return clock_calls[-1]

    client = fictional_client([Response(envelope([fictional_company()])), failure,
                               Response(envelope([]))], clock)
    previous = await client.fetch_listing()
    with pytest.raises(expected_error):
        await client.fetch_listing()
    # Old private storage may remain, but no new observation or evidence clock is issued.
    assert previous.observed_ms == 100 and clock_calls == [100]
    current = await client.fetch_listing()
    assert current.records == () and current.observed_ms == 101 and not current.complete


@pytest.fixture
def pipeline_case(tmp_path):
    from app.database.repository import DatabaseRepository
    from app.database.notification_migrator import connect_disposable, create_schema
    from app.database.event_repository import EventRepository
    from app.monitoring.notification_pipeline import NotificationPipeline, V2Options

    path = tmp_path / "fictional-envelope.sqlite"
    db = DatabaseRepository(str(path))
    with connect_disposable(path) as connection:
        create_schema(connection, now_ms=100)
        repository = EventRepository(connection, clock=lambda: 100)
        pipeline = NotificationPipeline(repository, options=V2Options(writer=True))
        pipeline.bind_legacy(db)
        pipeline.bind_listing_contract(fictional_contract())
        yield connection, pipeline


@pytest.mark.asyncio
async def test_partial_empty_and_failure_do_not_fabricate_disappearance(pipeline_case):
    connection, pipeline = pipeline_case
    client = fictional_client([Response(envelope([fictional_company(), fictional_company(702)])),
                               Response(envelope([])), Response(envelope([], "failed"))])
    client.synthetic = True  # This test alone supplies the explicit fictional adapter marker.
    await pipeline.poll(client)
    assert {row[0] for row in connection.execute("SELECT id FROM companies")} == {"701", "702"}
    assert connection.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 2
    await pipeline.poll(client)
    assert connection.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 2
    assert {row[0] for row in connection.execute("SELECT is_active FROM companies")} == {"True"}
    assert connection.execute("SELECT count(*) FROM opportunity_events WHERE origin='RECONCILE'").fetchone()[0] == 0
    with pytest.raises(SourceEvidenceError):
        await pipeline.poll(client)
    assert connection.execute("SELECT count(*) FROM opportunity_events").fetchone()[0] == 2
    assert {row[0] for row in connection.execute("SELECT is_active FROM companies")} == {"True"}
    assert connection.execute("SELECT count(*) FROM opportunity_events WHERE origin='RECONCILE'").fetchone()[0] == 0


def test_inconsistent_dates_and_units_do_not_acquire_invented_authority():
    from app.monitoring.notification_pipeline import NotificationPipeline
    from app.tpo.normalization import registration_conflicts
    from app.notifications.opportunity_renderer import render_opportunity

    record = decode(envelope([fictional_company(
        regEnddate="01-Jan-2026", regEndtime="10:00", regEnddatenew="2026-01-02T10:00:00",
    )])).records[0]
    assert registration_conflicts(record.model_dump()) == ("regEnd",)
    assert NotificationPipeline._deadline(record, fictional_contract()) == (None, None)
    rendered = render_opportunity(
        snapshot={"record": record.model_dump(), "state": "OBSERVED", "verified_deadline": False},
        reasons=["NEW"], role="INFO", channel="SUBSCRIBER", branch="VIT_CE",
        observed_ms=100, persisted_ms=100, rendered_ms=100,
    )
    assert "unit unknown" in rendered.html and "timezone unverified" in rendered.html
    assert "LPA" not in rendered.html and "2028" not in rendered.html
    assert "does not confirm full eligibility" in rendered.html
