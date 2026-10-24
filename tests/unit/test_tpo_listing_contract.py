"""Pure source-shape validation; no portal, credentials or network."""
import pytest


def contract(**changes):
    from app.tpo.listing import ListingContract
    return ListingContract(**dict(source_namespace="fixture-v1", institution="VIT",
        cohort=2028, evidence_reference="fictional-scoped-dashboard",
        freshness_ms=60000, **changes))


def page(items, number=1, total=1, **changes):
    from app.tpo.listing import ListingPage
    return ListingPage(body={"status": "success", "company_list": items},
        number=number, total=total, scope="fixture-v1", authenticated=True, **changes)


def company(identity="new-unseen", **changes):
    return dict(id=identity, company="Fictional Unseen Employer",
        organization="VIT", tpoprogram="VIT-BTech-Computer Engineering", **changes)


def test_positive_page_is_not_implicitly_complete_and_preserves_unknown_fields():
    from app.tpo.listing import decode_listing_pages
    result = decode_listing_pages([page([company(job_title="Engineer", cgpa="7.0")])],
        contract=contract(), observed_ms=100)
    assert not result.complete
    assert result.records[0].model_dump()["cgpa"] == "7.0"
    assert result.records[0].model_dump()["job_title"] == "Engineer"
    assert result.records[0].regEndtime is None


@pytest.mark.parametrize("body", [{}, {"company_list": None}, {"company_list": [] ,"error":"expired"},
    {"status":"failure","company_list":[]}, {"company_list":"invalid"}])
def test_missing_defaulted_or_error_list_never_becomes_empty_census(body):
    from app.tpo.listing import decode_listing_pages, ListingPage, SourceEvidenceError
    with pytest.raises(SourceEvidenceError):
        decode_listing_pages([ListingPage(body,1,1,"fixture-v1",True)],
            contract=contract(allow_complete=True), observed_ms=100, complete=True)


@pytest.mark.parametrize("identity", [None, True, 1.5, "", "  ", {}, "null"])
def test_invalid_source_identity_is_rejected_before_model_coercion(identity):
    from app.tpo.listing import decode_listing_pages, SourceEvidenceError
    with pytest.raises(SourceEvidenceError):
        decode_listing_pages([page([company(identity)])],contract=contract(),observed_ms=100)


def test_pagination_scope_and_duplicate_accounting():
    from app.tpo.listing import decode_listing_pages, SourceEvidenceError, ListingPage
    policy=contract(allow_complete=True)
    pages=[page([company("one")],1,2),page([company("one"),company("two")],2,2)]
    result=decode_listing_pages(pages,contract=policy,observed_ms=100,complete=True)
    assert result.complete and [x.id for x in result.records]==["one","two"]
    assert not decode_listing_pages(pages[:1],contract=policy,observed_ms=100).complete
    for invalid in (pages[:1], [pages[0],page([company("one",job_title="conflict")],2,2)],
                    [ListingPage({"company_list":[]},1,1,"foreign-scope",True)],
                    [ListingPage({"company_list":[]},1,1,"fixture-v1",False)]):
        with pytest.raises(SourceEvidenceError):
            decode_listing_pages(invalid,contract=policy,observed_ms=100,complete=True)


def test_complete_requires_explicit_contract_and_deadline_authority_is_paired():
    from app.tpo.listing import decode_listing_pages, SourceEvidenceError
    with pytest.raises(SourceEvidenceError):
        decode_listing_pages([page([])],contract=contract(),observed_ms=100,complete=True)
    with pytest.raises(ValueError):
        contract(source_timezone="Asia/Kolkata")


@pytest.mark.parametrize("observed,complete",[(True,False),(-1,False),(100,"true"),(100,True)])
def test_direct_observation_cannot_bypass_contract(observed,complete):
    from app.tpo.listing import ListingObservation,SourceEvidenceError
    with pytest.raises(SourceEvidenceError):ListingObservation((),contract(),observed,complete)


@pytest.mark.asyncio
async def test_actual_browser_adapter_decodes_only_the_exact_dashboard_response():
    import json
    from app.tpo.client import TPOClient,API_ENDPOINT
    class Response:
        url=API_ENDPOINT+"?fixture=1"
        status=200
        ok=True
        async def text(self):return json.dumps({"company_list":[company(job_title="Fictional role")]})
    class Capture:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @property
        def value(self):
            async def result():return Response()
            return result()
    class Page:
        url="https://tpo.vierp.in/home"
        first=None
        def __init__(self):self.first=self
        def is_closed(self):return False
        def expect_response(self,predicate,**kwargs):
            assert predicate(Response())
            wrong=Response();wrong.url=API_ENDPOINT+"-detail"
            assert not predicate(wrong)
            return Capture()
        def locator(self,selector):return self
        async def is_visible(self):return True
        async def click(self):pass
        async def goto(self,*args,**kwargs):pass
    client=TPOClient(None,Page(),listing_contract=contract(allow_complete=True),clock=lambda:100)
    observation=await client.fetch_listing()
    assert not observation.complete
    assert observation.records[0].model_dump()["job_title"]=="Fictional role"

