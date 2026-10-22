"""Inert entry and real SQLite boundaries, confined by the unchanged guard."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
import isolation_bootstrap as isolation


def snapshot(tmp_path):
    root=tmp_path/'public';root.mkdir()
    now=int(time.time()*1000)
    nullable=('max_package','min_package','placement_type','registration_end','eligible_programs',
        'company_type','first_seen_at','registration_start','registration_start_time','registration_end_time',
        'registration_start_alternate','registration_end_alternate','deadline_timezone','compensation_unit',
        'organizations','job_title','job_description','academic_year')
    item=dict.fromkeys(nullable)
    item.update(id='fixture-1',company='Fictional Company',is_active='True',listing_status='CURRENT',
        eligibility_status='PROGRAMME_MATCH_ONLY',observed_ms=now,valid_until_ms=now+60000)
    listing=dict(items=[item],total=1,shown=1,status='VERIFIED',observed_ms=now)
    path=root/'opportunities.sqlite'
    with sqlite3.connect(str(path)) as c:
        c.execute('CREATE TABLE public_listing(singleton INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        c.execute('INSERT INTO public_listing VALUES(1,?)',(json.dumps(listing),))
    c.close()
    policy=dict(version=1,profile='INERT_REHEARSAL',snapshot_root=str(root),
        snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    policy_path=tmp_path/'runtime-policy.json';policy_path.write_text(json.dumps(policy))
    return policy_path,path,policy,listing


def test_inert_entry_and_policy_exist():
    assert importlib.util.find_spec('app.production_main') is not None
    assert importlib.util.find_spec('app.monitoring.deployment_policy') is not None


def application(tmp_path):
    from app.production_main import application_from_policy
    policy,path,body,listing=snapshot(tmp_path)
    return application_from_policy(policy),path,body,listing


def test_inert_valid_read_surfaces_health_and_shutdown(tmp_path):
    app,path,policy,listing=application(tmp_path)
    before=path.read_bytes();before_files=set(path.parent.iterdir())
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/signup').status_code==200
        assert client.get('/assets/'+next(p.name for p in (Path(__file__).parents[2]/'app/static/assets').iterdir() if p.suffix=='.js')).status_code==200
        assert client.get('/api/v1/opportunity-listing').json()==listing
        assert client.get('/api/v1/opportunities').json()==listing['items']
        assert client.get('/health/live').json()['live'] is True
        health=client.get('/health/ready')
        assert health.status_code==200
        assert health.json()==dict(mode='INERT_REHEARSAL',live=True,read_only_ready=True,
            notification_ready=False,polling=False,sending=False,consent_processing=False)
        assert client.get('/health').json()==health.json()
        assert not getattr(app.state,'notification_runtime',None)
    assert path.read_bytes()==before and set(path.parent.iterdir())==before_files
    assert not app.state.inert_active


@pytest.mark.parametrize('method,url',[
    ('POST','/api/v1/auth/signup'),('GET','/api/v1/auth/verify?token=fixture'),
    ('POST','/api/v1/auth/verify/confirm'),('POST','/api/v1/preferences/request-link'),
    ('GET','/api/v1/preferences'),('PUT','/api/v1/preferences'),
    ('POST','/api/v1/unsubscribe/confirm'),('GET','/api/v1/unsubscribe/one-click?token=fixture'),
    ('POST','/api/v1/preferences/confirm'),('GET','/api/v1/admin/redrive'),
    ('POST','/api/v1/admin/recover'),('DELETE','/api/v1/opportunities'),
    ('POST','/anything'),('OPTIONS','/api/v1/auth/signup')])
def test_inert_routes_cannot_mutate_or_issue_email(tmp_path,method,url):
    app,path,_,_=application(tmp_path);before=path.read_bytes()
    with TestClient(app) as client:
        response=client.request(method,url,json={'token':'fictional','email':'fictional@example.invalid'})
        assert response.status_code==503
        assert response.json()=={'detail':'Read-only maintenance; operation unavailable'}
    assert path.read_bytes()==before


@pytest.mark.parametrize('change',[
    {'profile':'V2_FAKE'},{'profile':'LIVE'},{'sending':True},{'polling':True},
    {'initialize_database':True},{'SMTP_PASSWORD':'must-not-appear'},
    {'snapshot_root':'/app/data'},{'snapshot_root':'../public'},{'version':True},
    {'snapshot_sha256':'bad'},{'snapshot_root':'/run/tpo/rehearsal/../data'}])
def test_inert_policy_rejects_privileged_unknown_or_invalid_settings(tmp_path,change):
    from app.monitoring.deployment_policy import load_policy,PolicyError
    file,path,body,_=snapshot(tmp_path);body.update(change);file.write_text(json.dumps(body))
    with pytest.raises(PolicyError) as error:load_policy(file)
    assert 'must-not-appear' not in str(error.value)


@pytest.mark.parametrize('raw',['{','{}','null','{"version":1,"version":1}','NaN','['*1100+'0'+']'*1100])
def test_inert_malformed_policy_fails_closed(tmp_path,raw):
    from app.production_main import application_from_policy
    from app.monitoring.deployment_policy import PolicyError
    path=tmp_path/'runtime-policy.json';path.write_text(raw)
    with pytest.raises(PolicyError):application_from_policy(path)


def test_inert_missing_policy_and_missing_snapshot_do_not_create_files(tmp_path):
    from app.production_main import application_from_policy
    from app.monitoring.deployment_policy import PolicyError
    with pytest.raises(PolicyError):application_from_policy(tmp_path/'runtime-policy.json')
    assert not (tmp_path/'runtime-policy.json').exists()
    file,path,_,_=snapshot(tmp_path);path.unlink()
    with pytest.raises(PolicyError):application_from_policy(file)
    assert not path.exists()


def test_inert_actual_persistence_boundary_denies_every_write(tmp_path):
    from app.monitoring.deployment_policy import load_policy,read_snapshot
    file,path,_,_=snapshot(tmp_path);before=path.read_bytes()
    with read_snapshot(load_policy(file)) as c:
        for sql in ('DELETE FROM public_listing','CREATE TABLE forbidden(x)',
            'PRAGMA query_only=OFF',"ATTACH DATABASE ':memory:' AS extra",'VACUUM'):
            with pytest.raises(sqlite3.DatabaseError):c.execute(sql)
        assert c.execute('SELECT count(*) FROM public_listing').fetchone()[0]==1
    assert path.read_bytes()==before and {p.name for p in path.parent.iterdir()}=={'opportunities.sqlite'}


@pytest.mark.parametrize('fault',['missing','modified','invalid-schema','extra-private-field'])
def test_inert_readiness_fails_closed_on_missing_or_unapproved_state(tmp_path,fault):
    app,path,policy,listing=application(tmp_path)
    if fault=='missing':path.unlink()
    elif fault=='modified':path.write_bytes(b'not a database')
    else:
        with sqlite3.connect(str(path)) as c:
            if fault=='invalid-schema':c.execute('DROP TABLE public_listing')
            else:
                listing['items'][0]['private_email']='fictional@example.invalid'
                c.execute('UPDATE public_listing SET payload=?',(json.dumps(listing),))
        c.close()
    with TestClient(app) as client:
        assert client.get('/health/live').status_code==200
        assert client.get('/health/ready').status_code==503
        assert client.get('/api/v1/opportunity-listing').status_code==503


def test_inert_main_requires_policy_and_uses_only_single_inert_process(tmp_path,monkeypatch):
    from app.production_main import main
    import uvicorn
    file,_,_,_=snapshot(tmp_path);calls=[]
    def run(app,**kwargs):calls.append((app,kwargs))
    monkeypatch.setattr(uvicorn,'run',run)
    assert main(['--policy',str(file)])==0
    assert len(calls)==1 and calls[0][1]['host']=='0.0.0.0' and calls[0][1]['port']==8000
    assert calls[0][1]['workers']==1 and calls[0][1]['reload'] is False
    with pytest.raises(SystemExit):main([])
    assert main(['--policy',str(tmp_path/'missing.json')])==2
    assert len(calls)==1


def test_inert_repeated_lifecycle_and_environment_cannot_activate_senders(tmp_path,monkeypatch):
    app,path,_,_=application(tmp_path);before=path.read_bytes()
    for key in ('SMTP_HOST','SMTP_PASSWORD','TPO_PASSWORD','RUNTIME_MODE','DB_PATH'):
        monkeypatch.setenv(key,'fictional-activation-must-be-ignored')
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get('/health/ready').json()['sending'] is False
            assert client.get('/health/ready').json()['polling'] is False
        assert not app.state.inert_active
    assert path.read_bytes()==before


def test_inert_legacy_factory_and_runtime_activation_hold(tmp_path):
    from app.api.app import create_app
    legacy=create_app()
    with TestClient(legacy) as client:
        assert client.post('/api/v1/auth/signup',json={}).status_code==422
    app,_,_,_=application(tmp_path)
    app.state.notification_runtime=object()
    with TestClient(app) as client:
        assert client.get('/health/ready').status_code==503
        assert client.get('/api/v1/opportunities').status_code==503


@pytest.mark.parametrize('fault',['private-field','private-table','private-prefix-table','duplicate-id','wrong-count','stale','sidecar','check-expression'])
def test_inert_valid_digest_does_not_bypass_public_schema_or_freshness(tmp_path,fault):
    from app.production_main import application_from_policy
    from app.monitoring.deployment_policy import PolicyError
    file,path,policy,listing=snapshot(tmp_path)
    if fault=='private-field':listing['items'][0]['private_email']='fictional@example.invalid'
    elif fault=='duplicate-id':listing['items']*=2;listing['shown']=2;listing['total']=2
    elif fault=='wrong-count':listing['total']=0
    elif fault=='stale':listing['items'][0]['valid_until_ms']=listing['items'][0]['observed_ms']
    with sqlite3.connect(str(path)) as c:
        if fault=='private-table':c.execute('CREATE TABLE users(email TEXT)')
        if fault=='private-prefix-table':c.execute('CREATE TABLE sqlitePrivate(email TEXT)')
        if fault=='check-expression':
            c.execute('DROP TABLE public_listing')
            c.execute('CREATE TABLE public_listing(singleton INTEGER PRIMARY KEY,payload TEXT NOT NULL CHECK(length(payload)>0))')
            c.execute('INSERT INTO public_listing VALUES(1,?)',(json.dumps(listing),))
        c.execute('UPDATE public_listing SET payload=?',(json.dumps(listing),))
    c.close()
    if fault=='sidecar':Path(str(path)+'-wal').write_bytes(b'fictional')
    policy['snapshot_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();file.write_text(json.dumps(policy))
    if fault=='stale':
        app=application_from_policy(file)
        with TestClient(app) as client:
            result=client.get('/api/v1/opportunity-listing').json()
            assert result['status']=='STALE' and result['items']==[]
    else:
        with pytest.raises(PolicyError):application_from_policy(file)


def test_inert_connected_bytes_cannot_differ_from_approved_path_digest(tmp_path,monkeypatch):
    from app.monitoring.deployment_policy import load_policy,read_snapshot,PolicyError
    file,path,_,listing=snapshot(tmp_path);approved=path.read_bytes()
    listing['items'][0]['company']='Fictional replacement'
    with sqlite3.connect(str(path)) as c:c.execute('UPDATE public_listing SET payload=?',(json.dumps(listing),))
    c.close();replacement=path.read_bytes();path.write_bytes(approved)
    other=tmp_path/'replacement.sqlite';other.write_bytes(replacement)
    native=sqlite3.connect
    def replace_before_open(database,*args,**kwargs):
        # Model a pathname resolving to another inode while the checked path
        # continues to expose the original bytes (also works on Windows).
        return native(other.as_uri()+'?mode=ro',*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',replace_before_open)
    with pytest.raises(PolicyError):
        with read_snapshot(load_policy(file)):pytest.fail('Unapproved connection accepted')


def test_inert_unknown_cli_flags_never_echo_private_values(capsys):
    from app.production_main import main
    with pytest.raises(SystemExit) as error:main(['--policy','fictional','--smtp-password','fictional-private-value'])
    assert error.value.code==2
    assert 'fictional-private-value' not in capsys.readouterr().err
