import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from test_cummins_end_to_end_v2 import integrated,record
from test_notification_current_source_freshness import enable_current


@pytest.mark.asyncio
async def test_listing_api_is_read_only_current_scoped_and_keeps_history(integrated,monkeypatch):
    from app.api.app import create_app
    from app.api import routes_opportunities
    x=integrated
    enable_current(x)
    x["feed"].records=[record(id="a",job_description="Authentic fictional details"),
        record(id="b",organization="VIIT",tpoprogram="VIIT-BTech-Computer Engineering"),
        record(id="c",isactive="False")]
    assert await x["watcher"].check_once()
    first=x["c"].execute("SELECT first_seen_at FROM companies WHERE id='a'").fetchone()[0]
    app=create_app()
    app.state.opportunity_clock=x["clock"]
    monkeypatch.setattr(routes_opportunities,"DB_PATH",str(x["path"]))
    # API construction and reads must not call the repository's migration constructor.
    monkeypatch.setattr("app.database.repository.DatabaseRepository.__init__",
        lambda *a,**k: (_ for _ in ()).throw(AssertionError("API attempted migration")))
    with TestClient(app) as client:
        data=client.get("/api/v1/opportunity-listing").json()
        assert data["status"]=="VERIFIED" and data["total"]==1
        assert [r["id"] for r in data["items"]]==["a"]
        item=data["items"][0]
        assert item["registration_end_time"]=="10:00"
        assert item["deadline_timezone"] is None
        assert item["compensation_unit"] is None
        assert item["organizations"]=="VIT,VIIT"
        assert "VIT-" in item["eligible_programs"] and "VIIT-" in item["eligible_programs"]
        assert item["job_description"]=="Authentic fictional details"
        assert item["listing_status"]=="CURRENT"
        assert "contact_email" not in item
        x["clock"].now+=60001
        stale=client.get("/api/v1/opportunity-listing").json()
        assert stale["items"]==[] and stale["status"]=="STALE"
        assert client.get("/api/v1/opportunities").json()==[]
        assert x["c"].execute("SELECT count(*) FROM companies").fetchone()[0]==3
        x["feed"].records=[record(id="a",regEndtime="11:00")]
        assert await x["watcher"].check_once()
        refreshed=client.get("/api/v1/opportunity-listing").json()
        assert refreshed["items"][0]["first_seen_at"]==first
        assert refreshed["items"][0]["registration_end_time"]=="11:00"
        x["feed"].records=[]
        x["feed"].source_complete=True
        assert await x["watcher"].check_once()
        assert client.get("/api/v1/opportunity-listing").json()["items"]==[]
    assert x["c"].execute("SELECT count(*) FROM companies").fetchone()[0]==3


def test_unbound_history_is_not_claimed_current(tmp_path,monkeypatch):
    from app.database.repository import DatabaseRepository
    from app.api.app import create_app
    from app.api import routes_opportunities
    path=tmp_path/"legacy.sqlite"
    db=DatabaseRepository(str(path)); db.upsert_company(record())
    monkeypatch.setattr(routes_opportunities,"DB_PATH",str(path))
    with TestClient(create_app()) as client:
        result=client.get("/api/v1/opportunity-listing")
    assert result.status_code==200
    assert result.json()["status"]=="UNVERIFIED" and result.json()["items"]==[]


def test_missing_database_read_does_not_create_database(tmp_path,monkeypatch):
    from app.api.app import create_app
    from app.api import routes_opportunities
    path=tmp_path/"missing.sqlite"
    monkeypatch.setattr(routes_opportunities,"DB_PATH",str(path))
    with TestClient(create_app()) as client:
        response=client.get("/api/v1/opportunity-listing")
    assert response.status_code==503 and not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("active",["true","TRUE"," True "])
async def test_listing_and_notification_authorization_agree_on_active_flag(integrated,monkeypatch,active):
    from app.api.app import create_app
    from app.api import routes_opportunities
    x=integrated
    enable_current(x)
    x["feed"].records=[record(isactive=active)]
    assert await x["watcher"].check_once()
    app=create_app();app.state.opportunity_clock=x["clock"]
    monkeypatch.setattr(routes_opportunities,"DB_PATH",str(x["path"]))
    with TestClient(app) as client:
        result=client.get("/api/v1/opportunity-listing").json()
        assert result["total"]==1 and result["items"][0]["id"]=="fictional-company"
        assert client.get("/api/v1/opportunities").json()[0]["id"]=="fictional-company"
    assert x["c"].execute("SELECT is_active FROM companies").fetchone()[0]==active
    assert await x["watcher"].delivery_worker.process_batch_once()==2

