"""Actual API and frozen watcher/default factories on fictional encrypted state."""
import json
import hashlib
import secrets
from datetime import datetime,timezone,timedelta
import pytest
from fastapi.testclient import TestClient
from app.api.app import app
from app.database.event_repository import EventRepository
from app.database.notification_migrator import create_schema
from app.monitoring.notification_pipeline import NotificationPipeline, V2Options
from app.monitoring.watcher import PlacementWatcher
from test_cummins_end_to_end_v2 import Feed, record
from test_notification_migration_privacy import encrypted, NOW, STAMP


@pytest.mark.asyncio
async def test_v4_v6_v11_frozen_watcher_default_and_bound_factory_paths(encrypted,monkeypatch):
    from app.api import routes_auth,routes_opportunities
    from app.database.protected_payloads import protected_scope
    from app.database.notification_compatibility import repository_unit
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    repo=EventRepository(c,clock=lambda:NOW,record_mode=True)
    pipeline=NotificationPipeline(repo,options=V2Options(writer=True,fanout=True,info_ttl_ms=60000))
    feed=Feed([record(id='new-a'),record(id='history-a',maxPackage='30000'),record(id='inactive-new',isactive='False')])
    watcher=PlacementWatcher(db=x['db'],notification_pipeline=pipeline,source_client=feed)
    assert await watcher.check_once()
    assert c.execute("SELECT first_seen_at FROM companies WHERE id='history-a'").fetchone()[0]==STAMP
    reasons=[json.loads(r[0]) for r in c.execute("SELECT reasons_json FROM opportunity_events e JOIN opportunity_state s USING(company_key) WHERE s.legacy_company_id='history-a'")]
    assert reasons and all('NEW' not in r for r in reasons)
    pipeline.fanout_once()
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]>0
    # Actual default factory constructs fresh EventRepository inside caller UoW.
    monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    users,tokens=routes_auth.get_db_repos()
    raw=secrets.token_urlsafe(32)
    tokens.create_token(1,hashlib.sha256(raw.encode()).hexdigest(),'UNSUBSCRIBE',datetime.now(timezone.utc)+timedelta(minutes=10))
    with TestClient(app) as client:
        assert client.post('/api/v1/unsubscribe/confirm',json={'token':raw}).status_code==200
    assert c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER' AND status='SUPPRESSED'").fetchone()[0]>0
    # Bound pipeline precedence also constructs a fresh EventRepository, through frozen code.
    x['db'].users.bind_notification_v2(pipeline)
    x['db'].users.update_preferences(1,True,False,False)
    repo.change_user(1,is_active=1)
    assert {r[0] for r in c.execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'")}=={'SUPPRESSED'}
    assert c.execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
    monkeypatch.setattr(routes_opportunities,'DB_PATH',str(x['path']))
    with TestClient(app) as client:
        response=client.get('/api/v1/opportunities')
    assert response.status_code==200
    data=response.json()
    assert len(data)==5 and data[0]['id']=='new-a'
    assert next(r for r in data if r['id']=='history-a')['first_seen_at']==STAMP
    assert all(r['is_active']=='True' for r in data)
    assert 'inactive-new' not in {r['id'] for r in data}
    assert {'id','company','max_package','min_package','placement_type','registration_end','eligible_programs','company_type','is_active','first_seen_at'}==set(data[0])
    assert data[0]['first_seen_at'].startswith('2026-10-15T')


def test_v6_default_unit_rolls_back_identity_and_suppression(encrypted,monkeypatch):
    from app.api import routes_auth
    from app.database.notification_compatibility import repository_unit
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    users,tokens=routes_auth.get_db_repos()
    epoch=c.execute('SELECT consent_epoch FROM subscriber_versions WHERE user_id=1').fetchone()[0]
    with pytest.raises(RuntimeError):
        with repository_unit((users,tokens)):
            users.set_unsubscribed(1)
            raise RuntimeError('fictional transaction interruption')
    assert c.execute('SELECT is_active FROM users WHERE id=1').fetchone()[0]==1
    assert c.execute('SELECT consent_epoch FROM subscriber_versions WHERE user_id=1').fetchone()[0]==epoch
