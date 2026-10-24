"""Real guarded processes and encrypted fixtures; no physical network transport."""
import importlib.util
import os
import json
import threading
import time
from contextlib import contextmanager

import pytest
import isolation_bootstrap as isolation
from test_notification_migration_privacy import encrypted, NOW
from app.database.notification_migrator import create_schema
from app.database.event_repository import EventRepository
from app.notifications.transport_arbiter import RuntimeController


@contextmanager
def custody(x):
    from app.monitoring.runtime_supervisor import ChildKeyRelay
    from app.monitoring.notification_pipeline import V2Options
    create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=lambda:NOW,record_mode=True)
    controller=RuntimeController.acquire(repo)
    relay=ChildKeyRelay(repo,x['codec'],controller,provider_identity='fictional-custody',custody_generation=1)
    try:yield repo,controller,relay
    finally:
        relay.close()
        if not relay.custody.failed:
            if any(k['revoked'] for k in x['authority'].state['keys'].values()):
                from app.database.key_provider import KeyUnavailable
                with pytest.raises(KeyUnavailable):controller.shutdown()
            else:controller.shutdown()


@pytest.mark.parametrize('field,value',[
    ('namespace','foreign.sqlite'),('controller_generation',999),('role','client'),
    ('expires_ms',NOW-1),('epoch',999),('custody_generation',999),('provider','foreign'),
    ('child','foreign-child'),('token','0'*64),
])
def test_k04_k07_wrong_capability_never_constructs_repository(encrypted,field,value):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        cap=relay.issue(child);cap[field]=value
        with pytest.raises(RuntimeError,match='KeyUnavailable'):
            relay.request(child,'open',dict(path=str(x['path']),now=NOW,key_capability=cap))
        assert repo._runtime()['send_mode']=='OFF'
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0


@pytest.mark.parametrize('attack',['replay','method','fields','namespace','owner','purpose','reference','checkpoint','key_generation'])
def test_k16_k20_actual_child_rpc_rejects_malformed_or_privileged_request(encrypted,attack):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        cap=relay.issue(child)
        def corrupted(op,args):
            data=json.loads(json.dumps(args))
            if attack=='replay' and data['counter']>1:data['counter']-=1
            elif attack=='method':data['operation']='revoke'
            elif attack=='fields':data['unapproved']='value'
            elif attack=='namespace' and 'namespace' in data['arguments']:data['arguments']['namespace']='foreign'
            elif attack in ('owner','purpose') and data['operation']=='unwrap':
                data['arguments']['context']['owner_digest' if attack=='owner' else 'purpose']='0'*64 if attack=='owner' else 'APPROVAL'
            elif attack=='reference' and data['operation']=='expected':data['arguments']['reference']='invalid-reference'
            elif attack=='key_generation' and data['operation']=='unwrap':data['arguments']['generation']=999
            elif attack=='checkpoint':
                data['operation']='finish_database'
                data['arguments']=dict(namespace=str(x['path']),fingerprint=x['codec'].fingerprint(x['c']),schema=True)
            return relay._rpc(child,op,data)
        with pytest.raises(RuntimeError,match='KeyUnavailable') as error:
            child.request('open',dict(path=str(x['path']),now=NOW,key_capability=cap),relay=corrupted)
        assert 'key=' not in str(error.value) and 'fictional@example' not in str(error.value)
        assert repo._runtime()['send_mode']=='OFF'


def test_k03_k10_k12_k14_authority_and_client_share_only_parent_custody(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        relay.open(authority);identity=relay.request(authority,'elect',{})['authority']
        relay.open(client)
        assert relay.sessions[client.ready['identity']]['cap']['namespace']==str(x['path'])
        assert relay.request(client,'status',{})['epoch']==repo._runtime()['epoch']
        assert len({os.getpid(),authority.ready['pid'],client.ready['pid']})==3
        relay.close_child(client)
        assert len(relay.sessions)==1
        x['authority'].failure='unwrap'
        with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.request(authority,'elect',{})
        x['authority'].failure=None
        assert relay.request(authority,'status',{})['physical_calls']==0
        relay.close_child(authority)
        assert relay.rpc_count>10


def test_k09_k11_revoked_key_fails_real_child_startup(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        key=next(iter(x['authority'].state['keys'].values()));key['revoked']=True
        with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.open(child)
        assert repo._runtime()['send_mode']=='OFF'


def test_k08_blocked_provider_has_bounded_wait_and_never_starts_physical_send(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        release=threading.Event()
        original=relay.custody.methods['schema_required']
        relay.custody.methods['schema_required']=lambda ns: (release.wait(2),original(ns))[1]
        started=time.monotonic()
        try:
            with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.open(child)
            assert time.monotonic()-started<1.5
            assert relay.custody.failed
            assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
        finally:release.set()


def test_k01_parent_scope_does_not_cross_child_boundary(encrypted):
    x=encrypted; create_schema(x['c'],now_ms=NOW)
    with isolation.start_test_child('authority') as child:
        with pytest.raises(RuntimeError,match='KeyUnavailable'):
            child.request('open',dict(path=str(x['path']),now=NOW))
    assert x['c'].execute('SELECT epoch FROM notification_runtime').fetchone()[0]==0


def test_k02_actual_encrypted_child_constructor_uses_parent_custody(encrypted):
    assert importlib.util.find_spec('app.monitoring.runtime_supervisor') is not None, 'explicit child key relay missing'
    from app.monitoring.runtime_supervisor import ChildKeyRelay
    x=encrypted; create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=lambda:NOW,record_mode=True)
    controller=RuntimeController.acquire(repo)
    relay=ChildKeyRelay(repo,x['codec'],controller,provider_identity='fictional-custody',custody_generation=1)
    try:
        with isolation.start_test_child('authority') as child:
            relay.open(child)
            assert child.ready['pid']!=os.getpid()
            assert relay.request(child,'elect',{})['pid']==child.ready['pid']
            assert relay.request(child,'status',{})['physical_calls']==0
            relay.close_child(child)
        assert not relay.sessions
        assert x['authority'].database_state(str(x['path']))['pending'] is False
    finally:
        controller.shutdown()
        relay.close()


def test_k07_expired_old_session_and_replayed_request_cannot_reconnect(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        captured=[]
        cap=relay.issue(child)
        def observe(op,data):
            captured.append(json.loads(json.dumps(data)))
            return relay._rpc(child,op,data)
        child.request('open',dict(path=str(x['path']),now=NOW,key_capability=cap),relay=observe)
        from app.database.key_provider import KeyUnavailable
        with pytest.raises(KeyUnavailable):relay._rpc(child,relay.RPC,captured[-1])
        relay.refresh(child)
        with pytest.raises(KeyUnavailable):relay._rpc(child,relay.RPC,captured[0])
        relay.sessions[child.ready['identity']]['deadline']=time.monotonic()-1
        with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.request(child,'elect',{})


def test_k13_replacing_authority_fences_old_child_and_client_capabilities(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as first, isolation.start_test_child('authority') as second, isolation.start_test_child('client') as client:
        relay.open(first);relay.request(first,'elect',{});relay.open(client)
        relay.open(second);relay.request(second,'elect',{})
        with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.request(first,'elect',{})
        # Transport relay refusal uses the existing redacted RuntimeError
        # contract; the protected-provider refusal above is KeyUnavailable.
        with pytest.raises(RuntimeError):
            relay.request(client,'prepare',{'message_class':'ACCOUNT'},transport=second)
        assert relay.request(second,'status',{})['physical_calls']==0


@pytest.mark.parametrize('attack',['counter','key','generation','fields'])
def test_k16_corrupt_provider_response_is_redacted(encrypted,attack):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        cap=relay.issue(child)
        def corrupt(op,data):
            result=relay._rpc(child,op,data)
            if attack=='counter':result['counter']+=1
            elif attack=='fields':result['private-extra']='sentinel-private-body'
            elif data['operation']=='unwrap':
                result['result']['key' if attack=='key' else 'generation']='sentinel-private-key'
            return result
        with pytest.raises(RuntimeError,match='KeyUnavailable') as exc:
            child.request('open',dict(path=str(x['path']),now=NOW,key_capability=cap),relay=corrupt)
        assert 'sentinel-private' not in str(exc.value)


def test_k17_key_rpc_never_occurs_in_child_write_transaction(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        relay.open(child)
        # A genuine child elect includes warm, begin and finish. The proxy refuses
        # IPC with c.in_transaction; the provider records parent-side observations.
        observed=[]
        for name in ('expected','unwrap','check_database','begin_database','finish_database'):
            original=relay.custody.methods[name]
            def audit(*args,_name=name,_method=original,**kwargs):
                observed.append(_name)
                return _method(*args,**kwargs)
            relay.custody.methods[name]=audit
        relay.request(child,'elect',{})
        assert {'expected','unwrap','check_database','begin_database','finish_database'}<=set(observed)
        assert relay.pending_session is None


def test_k19_consumed_account_permit_cannot_replay_after_provider_refresh(encrypted):
    from app.monitoring.notification_pipeline import V2Options
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        relay.open(authority);relay.request(authority,'elect',{})
        controller.enable(V2Options(drain=True,transport=True));relay.refresh(authority)
        relay.open(client)
        ticket=relay.request(client,'prepare',{'message_class':'ACCOUNT'},transport=authority)
        grant=relay.request(authority,'admit',ticket)
        assert relay.request(authority,'consume',grant)['kind']=='ACCEPTED'
        relay.refresh(authority);relay.refresh(client)
        with pytest.raises(RuntimeError):relay.request(authority,'consume',grant)
        assert relay.request(authority,'status',{})['physical_calls']==1


def test_ipc_missing_provider_reply_cannot_be_treated_as_new_permission(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        cap=relay.issue(child)
        with pytest.raises(RuntimeError,match='KeyUnavailable'):
            child.request('open',dict(path=str(x['path']),now=NOW,key_capability=cap),
                relay=lambda op,data: (_ for _ in ()).throw(TimeoutError('private-sentinel')))
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0


def test_provider_service_lane_contention_has_a_finite_wait(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        relay.custody.lock.acquire()
        timer=threading.Timer(1,relay.custody.lock.release);timer.start()
        start=time.monotonic()
        try:
            with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.open(child)
            assert time.monotonic()-start<.8
        finally:timer.join()


def test_child_send_capability_cannot_unwrap_administrative_approval(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        company=x['c'].execute('SELECT company_key FROM opportunity_state LIMIT 1').fetchone()[0]
        repo.approve('REDRIVE',company,'fictional-delivery')
        # Frozen codec warms every vault row. A sender must hold rather than
        # obtain an unrelated administrative approval payload key as a side effect.
        with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.open(child)
        assert repo._runtime()['send_mode']=='OFF'


def test_client_forged_transport_control_frame_cannot_crash_authority(encrypted,monkeypatch):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        relay.open(authority);relay.request(authority,'elect',{})
        cap=relay.issue(client)
        receive=client._receive
        def forged(timeout):
            frame=receive(timeout)
            if frame.get('rpc'):frame.update(operation='crash',payload={})
            return frame
        monkeypatch.setattr(client,'_receive',forged)
        with pytest.raises(RuntimeError):
            relay.request(client,'open',dict(path=str(x['path']),now=NOW,authority=authority.ready['identity'],key_capability=cap),transport=authority)
        assert relay.request(authority,'status',{})['physical_calls']==0


def test_existing_child_request_lock_contention_has_bounded_wait(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        relay.open(child)
        entered=threading.Event();release=threading.Event()
        def hold_lock():
            with child._request_lock:entered.set();release.wait(2)
        thread=threading.Thread(target=hold_lock);thread.start();assert entered.wait(1)
        try:
            from app.database.key_provider import KeyUnavailable
            start=time.monotonic()
            with pytest.raises(KeyUnavailable):relay.request(child,'status',{},timeout=.1)
            assert time.monotonic()-start<.5
        finally:release.set();thread.join(2)


def test_k08_parent_cancellation_revokes_session_without_new_invocation(encrypted,monkeypatch):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        relay.open(child);relay.request(child,'elect',{})
        def canceled(*args):raise KeyboardInterrupt()
        monkeypatch.setattr(relay,'_rpc',canceled)
        with pytest.raises(KeyboardInterrupt):relay.request(child,'elect',{})
        # Explicit lifecycle owner terminates the now-abandoned stdio session;
        # cancellation alone is not a physical END or a new permit.
        child.terminate();relay.close_child(child)
        assert child.ready['identity'] not in relay.sessions
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
        assert x['c'].execute('SELECT count(*) FROM transport_facts').fetchone()[0]==0


def test_actual_child_rejects_foreign_database_ciphertext_without_schema_weakening(encrypted,tmp_path):
    from test_notification_migration_privacy import history
    from tests.unit.test_notification_payload_key_lifecycle import Authority
    from app.database.protected_payloads import ProtectedPayloads,protected_scope
    from app.database.notification_migrator import connect_disposable
    x=encrypted;foreign=tmp_path/'foreign.sqlite';history(foreign)
    foreign_codec=ProtectedPayloads(Authority(tmp_path/'foreign-authority.json'),str(foreign))
    with protected_scope(foreign,foreign_codec),connect_disposable(foreign) as c:
        create_schema(c,now_ms=NOW)
        foreign_blob=c.execute('SELECT p.encrypted_bytes FROM protected_payloads p JOIN opportunity_state s ON s.snapshot_payload_id=p.payload_id WHERE s.legacy_company_id=?',('history-a',)).fetchone()[0]
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        row=x['c'].execute('SELECT p.rowid,p.encrypted_bytes FROM protected_payloads p JOIN opportunity_state s ON s.snapshot_payload_id=p.payload_id WHERE s.legacy_company_id=?',('history-a',)).fetchone()
        assert len(row[1])==len(foreign_blob)
        # Model a physical ciphertext substitution through a bounded SQLite BLOB
        # handle, retaining all original schema/triggers and independent custody.
        with x['c'].blobopen('protected_payloads','encrypted_bytes',row[0]) as blob:blob.write(foreign_blob)
        try:
            with pytest.raises(RuntimeError,match='KeyUnavailable'):relay.open(child)
            assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
        finally:
            with x['c'].blobopen('protected_payloads','encrypted_bytes',row[0]) as blob:blob.write(row[1])


def test_child_preserves_external_rewrap_with_mixed_wrapping_generations(encrypted):
    x=encrypted
    with custody(x) as (repo,controller,relay), isolation.start_test_child('authority') as child:
        before=[tuple(r) for r in x['c'].execute('SELECT payload_id,encrypted_bytes,key_reference FROM protected_payloads ORDER BY 1')]
        reference=before[0][2]
        assert x['authority'].rewrap(reference,'fixture-rewrap')==2
        assert {k['generation'] for k in x['authority'].state['keys'].values()}=={1,2}
        relay.open(child);relay.request(child,'elect',{})
        assert relay.request(child,'status',{})['physical_calls']==0
        assert [tuple(r) for r in x['c'].execute('SELECT payload_id,encrypted_bytes,key_reference FROM protected_payloads ORDER BY 1')]==before
