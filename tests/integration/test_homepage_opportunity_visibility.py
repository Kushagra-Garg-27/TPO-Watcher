"""Integration test suite for Homepage Opportunities Visibility Audit and Corrective Implementation.

Tests the contract boundary between legacy stored detections and verified listing evidence:
- Case A: Verified current listings exist with fresh evidence
- Case B: Historical detections exist without verified listing contract
- Case C: Empty historical and verified results
- Stale per-company evidence
- Multiple independent Cummins India records
- Verbatim compensation and registration deadline fidelity
- Zero mock/placeholder companies in production bundle and API
"""
import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api import routes_opportunities
from app.database.repository import DatabaseRepository
from test_cummins_end_to_end_v2 import integrated, record
from test_notification_current_source_freshness import enable_current


def test_case_b_historical_records_when_unbound(tmp_path, monkeypatch):
    """Case B: When no listing contract is bound, /opportunity-listing returns UNVERIFIED

    with 0 items, while /opportunities returns the legacy stored active records.
    """
    path = tmp_path / "case_b.sqlite"
    repo = DatabaseRepository(str(path))
    repo.set_baseline_initialized()

    # Insert 5 active companies with known deadlines and compensation
    repo.upsert_company(record(id="5988", company="Forbes Marshall", maxPackage="9.0", isactive="True", regEnddate="23-Oct-2026"))
    repo.upsert_company(record(id="5985", company="Valentius Kryptix", maxPackage="0.0", isactive="True", regEnddate="21-Oct-2026"))
    repo.upsert_company(record(id="5969", company="Eaton", maxPackage="10.0", isactive="True", regEnddate="16-Oct-2026"))
    repo.upsert_company(record(id="5971", company="Cummins India", maxPackage="25000.0", isactive="True", regEnddate=None))
    repo.upsert_company(record(id="5952", company="Cummins India", maxPackage="25000.0", isactive="True", regEnddate="14-Oct-2026"))

    monkeypatch.setattr(routes_opportunities, "DB_PATH", str(path))
    client = TestClient(create_app())

    # 1. Verified listing endpoint returns clean UNVERIFIED empty state
    listing_res = client.get("/api/v1/opportunity-listing")
    assert listing_res.status_code == 200
    listing_data = listing_res.json()
    assert listing_data["status"] == "UNVERIFIED"
    assert listing_data["items"] == []
    assert listing_data["total"] == 0

    # 2. Compatibility opportunities endpoint returns all 5 active historical records
    opps_res = client.get("/api/v1/opportunities")
    assert opps_res.status_code == 200
    opps_data = opps_res.json()
    assert len(opps_data) == 5

    ids = [item["id"] for item in opps_data]
    assert "5988" in ids
    assert "5985" in ids
    assert "5969" in ids
    assert "5971" in ids
    assert "5952" in ids

    # Both Cummins records preserved independently
    cummins_records = [item for item in opps_data if item["company"] == "Cummins India"]
    assert len(cummins_records) == 2
    cummins_ids = {r["id"] for r in cummins_records}
    assert cummins_ids == {"5971", "5952"}

    # Field-level fidelity: expired vs missing registration windows and raw compensation
    cummins_5971 = next(r for r in cummins_records if r["id"] == "5971")
    assert cummins_5971["registration_end"] is None
    assert cummins_5971["max_package"] in ("25000", "25000.0")
    assert "compensation_unit" not in cummins_5971

    cummins_5952 = next(r for r in cummins_records if r["id"] == "5952")
    assert cummins_5952["registration_end"] == "14-Oct-2026"
    assert cummins_5952["max_package"] in ("25000", "25000.0")
    assert "compensation_unit" not in cummins_5952

    forb = next(r for r in opps_data if r["id"] == "5988")
    assert forb["registration_end"] == "23-Oct-2026"
    assert forb["max_package"] in ("9.0", "9")
    assert "compensation_unit" not in forb


def test_inactive_history_records_are_suppressed(tmp_path, monkeypatch):
    """Case C: Inactive historical records (is_active='False') are never displayed."""
    path = tmp_path / "inactive.sqlite"
    repo = DatabaseRepository(str(path))
    repo.set_baseline_initialized()
    repo.upsert_company(record(id="inactive_1", company="Past Company 1", isactive="False"))
    repo.upsert_company(record(id="inactive_2", company="Past Company 2", isactive="False"))

    monkeypatch.setattr(routes_opportunities, "DB_PATH", str(path))
    client = TestClient(create_app())

    listing_res = client.get("/api/v1/opportunity-listing")
    assert listing_res.status_code == 200
    assert listing_res.json()["items"] == []
    assert listing_res.json()["status"] == "UNVERIFIED"

    opps_res = client.get("/api/v1/opportunities")
    assert opps_res.status_code == 200
    assert opps_res.json() == []


def test_case_d_api_error_handling(monkeypatch):
    """Case D: When database path is invalid or unreadable, both endpoints return 503."""
    monkeypatch.setattr(routes_opportunities, "DB_PATH", "non_existent_dir/unreachable.sqlite")
    client = TestClient(create_app())

    opps_res = client.get("/api/v1/opportunities")
    assert opps_res.status_code == 503
    assert opps_res.json()["detail"] == "Opportunity projection is unavailable"

    listing_res = client.get("/api/v1/opportunity-listing")
    assert listing_res.status_code == 503
    assert listing_res.json()["detail"] == "Verified listing is unavailable"


@pytest.mark.asyncio
async def test_case_a_multiple_genuine_verified_listings(integrated, monkeypatch):
    """Case A: When listing contract is bound and fresh observations exist,

    /opportunity-listing returns VERIFIED status with rich source metadata.
    """
    x = integrated
    enable_current(x)
    x["feed"].records = [
        record(id="co1", company="Company Alpha", job_description="Details Alpha", maxpackage="12.0", isactive="True"),
        record(id="co2", company="Company Beta", job_description="Details Beta", maxpackage="18.0", isactive="True"),
    ]
    assert await x["watcher"].check_once()

    app = create_app()
    app.state.opportunity_clock = x["clock"]
    monkeypatch.setattr(routes_opportunities, "DB_PATH", str(x["path"]))

    client = TestClient(app)
    res = client.get("/api/v1/opportunity-listing")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "VERIFIED"
    assert data["total"] == 2
    assert len(data["items"]) == 2

    co_ids = {r["id"] for r in data["items"]}
    assert co_ids == {"co1", "co2"}
    for item in data["items"]:
        assert item["listing_status"] == "CURRENT"
        assert item["eligibility_status"] == "PROGRAMME_MATCH_ONLY"
        assert type(item["observed_ms"]) is int
        assert item["valid_until_ms"] > item["observed_ms"]


@pytest.mark.asyncio
async def test_stale_per_company_evidence_transitions_to_stale(integrated, monkeypatch):
    """Stale evidence: When now_ms exceeds freshness window,

    /opportunity-listing status transitions to STALE and items are suppressed.
    When bound, /opportunities also returns empty.
    """
    x = integrated
    enable_current(x)
    x["feed"].records = [record(id="stale_co", isactive="True")]
    assert await x["watcher"].check_once()

    app = create_app()
    app.state.opportunity_clock = x["clock"]
    monkeypatch.setattr(routes_opportunities, "DB_PATH", str(x["path"]))

    client = TestClient(app)
    fresh_res = client.get("/api/v1/opportunity-listing").json()
    assert fresh_res["status"] == "VERIFIED"
    assert len(fresh_res["items"]) == 1

    # Advance clock past 60s freshness
    x["clock"].now += 60001
    stale_res = client.get("/api/v1/opportunity-listing").json()
    assert stale_res["status"] == "STALE"
    assert stale_res["items"] == []

    # When bound, legacy projection delegates to listing projection
    opps_res = client.get("/api/v1/opportunities").json()
    assert opps_res == []


def test_case_c_empty_historical_and_verified_results(tmp_path, monkeypatch):
    """Case C: Empty database returns empty results for both endpoints."""
    path = tmp_path / "empty.sqlite"
    repo = DatabaseRepository(str(path))
    repo.set_baseline_initialized()

    monkeypatch.setattr(routes_opportunities, "DB_PATH", str(path))
    client = TestClient(create_app())

    listing_res = client.get("/api/v1/opportunity-listing")
    assert listing_res.status_code == 200
    assert listing_res.json()["items"] == []
    assert listing_res.json()["total"] == 0

    opps_res = client.get("/api/v1/opportunities")
    assert opps_res.status_code == 200
    assert opps_res.json() == []


def test_no_mock_companies_in_built_frontend_assets():
    """Verify built frontend assets do not contain hardcoded placeholder companies."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    static_dir = repo_root / "app" / "static"
    index_html = static_dir / "index.html"
    assert index_html.exists(), "index.html must exist"

    content = index_html.read_text(encoding="utf-8")
    assert "Google" not in content
    assert "Microsoft" not in content
    assert "Goldman Sachs" not in content

    # Check JS assets in static/assets
    assets_dir = static_dir / "assets"
    js_files = list(assets_dir.glob("index-*.js"))
    assert len(js_files) > 0, "Main index JS bundle must exist"
    for js_file in js_files:
        js_text = js_file.read_text(encoding="utf-8")
        assert "Goldman Sachs" not in js_text
