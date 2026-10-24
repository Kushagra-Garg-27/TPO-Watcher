"""Pure snapshot truthfulness; no database, clock inference or transport."""
import importlib.util
import pytest


def renderer():
    assert importlib.util.find_spec("app.notifications.opportunity_renderer") is not None, "snapshot renderer missing"
    from app.notifications.opportunity_renderer import render_opportunity
    return render_opportunity


def test_source_local_times_units_and_safe_html_are_honest():
    result=renderer()(snapshot={"state":"OBSERVED","record":{"company":"<script>bad</script>","regStartdate":"14-Oct-2026","regStarttime":"11:31","regEnddate":"15-Oct-2026","regEndtime":"10:00","maxPackage":"25000","tpoprogram":"CE (VIT)"}},reasons=["NEW"],role="INFO",channel="SUBSCRIBER",branch="CE",observed_ms=1000,persisted_ms=1100,rendered_ms=5000,links={"unsubscribe":"javascript:alert(1)"})
    assert "&lt;script&gt;" in result.html and "<script>" not in result.html
    assert "11:31" in result.html and "10:00" in result.html
    assert "timezone unverified" in result.html.lower()
    assert "unit unknown" in result.html.lower() and "LPA" not in result.html
    assert "does not confirm full eligibility" in result.html.lower()
    assert "javascript:" not in result.html
    assert result.observed_ms==1000 and result.persisted_ms==1100 and result.rendered_ms==5000

@pytest.mark.parametrize("values,word",[
    ({"compensation_unit":"LPA","compensation_unit_evidence":"fictional-explicit-unit"},"LPA"),
    ({"compensation_unit":"monthly stipend","compensation_unit_evidence":"fictional-explicit-unit"},"monthly stipend"),
    ({"compensation_unit":"LPA"},"unit unknown"),
])
def test_compensation_requires_explicit_evidence(values,word):
    result=renderer()(snapshot={"state":"OBSERVED","record":dict(company="Fictional",maxPackage="25000",**values)},
        reasons=["UPDATE"],role="INFO",channel="ADMIN",branch=None,observed_ms=1000,persisted_ms=1100,rendered_ms=5000)
    assert word in result.html


def test_visibility_is_not_employer_closure_or_reopening_and_clocks_are_separate():
    result=renderer()(snapshot={"state":"ABSENT","record":{"company":"Fictional"}},reasons=["Source visibility changed"],
        role="INFO",channel="SUBSCRIBER",branch="VIT_CE",observed_ms=1000,persisted_ms=2000,rendered_ms=9000)
    assert "no longer observed" in result.html and "does not establish employer registration closure" in result.html
    assert "1970-01-01T00:00:01" in result.html and "1970-01-01T00:00:02" in result.html and "1970-01-01T00:00:09" in result.html


def test_verified_timezone_label_requires_provenance():
    result=renderer()(snapshot={"state":"OBSERVED","verified_deadline":True,"record":{"company":"Fictional","regEnddate":"14-Oct-2026","regEndtime":"10:00"}},
        reasons=["NEW"],role="ACTION",channel="SUBSCRIBER",branch="VIT_CE",observed_ms=1000,persisted_ms=2000,rendered_ms=3000)
    assert "timezone unverified" in result.html.lower()


