"""Real controller-host death, sibling physical authority and parent-owned custody."""
import os
import json
import sys
from contextlib import contextmanager
import pytest
import isolation_bootstrap as isolation
from test_notification_migration_privacy import encrypted,NOW
from test_cummins_end_to_end_v2 import Clock
from app.database.notification_migrator import create_schema
from app.database.event_repository import EventRepository


@contextmanager
def hosted(x,clock,*,enable=True):
    from app.monitoring.runtime_supervisor import ChildKeyRelay
    create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=clock,record_mode=True)
    with isolation.start_test_child('controller-host-crash-test') as host:
        relay=ChildKeyRelay(repo,x['codec'],None,controller_host=host,
            provider_identity='fixture-custody',custody_generation=1)
        try:
            relay.open(host)
            elected=relay.request(host,'elect_controller',{})
            relay.accept_controller_host(host)
            if enable:
                relay.request(host,'enable_controller',{})
                relay.refresh(host)
            yield repo,host,relay,elected
        finally:
            try:relay.close_child(host)
            finally:relay.close()


def test_h01_genuine_host_elects_encrypted_controller_and_closes_normally(encrypted):
    from app.notifications.transport_arbiter import get_state
    x=encrypted;clock=Clock();clock.now=NOW
    with hosted(x,clock) as (repo,host,relay,elected):
        state=get_state(repo,'r2a:controller')
        assert elected['pid']==host.ready['pid']!=os.getpid()
        assert elected['generation']==state['generation']==1
        assert state['lease_ms']==NOW+100
        assert relay.request(host,'controller_status',{})['generation']==1
        assert repo._runtime()['send_mode']=='V2' and repo._runtime()['writer_mode']=='OFF'
        assert relay.rpc_count>0
    assert get_state(repo,'r2a:controller')['lease_ms']==NOW


def test_h01_h04_h14_h15_fixed_host_is_guarded_and_positively_exits():
    baseline=set(isolation._child_processes)
    with isolation.start_test_child('controller-host-crash-test') as host:
        assert host.ready['pid']!=os.getpid() and host.ready['app_loaded'] is False
        assert host.request('probe',{'check':'denials'})['denied']==6
        with pytest.raises(RuntimeError):host.request('run',{'command':'must-not-execute'})
        with pytest.raises(ValueError):host.exit_evidence()
        isolation.terminate_test_controller(host)
        assert host.exit_evidence()['pid']==host.ready['pid']
        assert host.exit_evidence()['role']=='controller-host-crash-test'
    assert set(isolation._child_processes)==baseline and not host.grant_path.exists()


def evidence(case,repo,host,authority=None,**extra):
    from app.notifications.transport_arbiter import get_state
    owner=get_state(repo,'r2a:controller')
    value=dict(case=case,parent_pid=os.getpid(),host_pid=host.ready['pid'],
        authority_pid=authority.ready['pid'] if authority else None,
        controller_generation=owner['generation'],lease_ms=owner['lease_ms'],
        gate=repo._one('SELECT state FROM smtp_account_gate')['state'],
        end_facts=repo._one("SELECT count(*) n FROM transport_facts WHERE fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED')")['n'],
        **extra)
    sys.__stdout__.write('\nR2_HOST_EVIDENCE '+json.dumps(value,sort_keys=True)+'\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('completion,fault',[('end',None),('process_exit','accept_then_error')])
async def test_h02_h11_real_host_death_preserves_active_authority_and_newer_facts(encrypted,completion,fault):
    from test_notification_runtime_supervision import running
    from test_cummins_end_to_end_v2 import record
    from app.notifications.transport_arbiter import get_state,identity_for,supervisor_end
    from app.database.event_repository import OperatorApproval,Fenced,PolicyDenied
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    # Completed accepted history predates this host. A later revision is queued.
    with running(x) as (runtime,feed,old_authority,clock):
        await runtime.poll_once();assert await runtime.drain_once()==1
        runtime.repo.add_user(branch='VIT_CE')
        feed.records=[record(id='runtime-new',maxPackage='29000')]
        await runtime.poll_once();runtime.pipeline.fanout_once()
    baseline_children=set(isolation._child_processes)
    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x,clock) as (repo,host,relay,elected):
            relay.open(authority);relay.request(authority,'elect',{})
            relay.open(client)
            relay.request(authority,'configure',dict(kind='UNKNOWN',ended=False,fault=fault))
            captured=[];request=relay.request
            def traced(child,operation,data=None,**kwargs):
                result=request(child,operation,data,**kwargs)
                if child is authority and operation=='admit' and 'grant' in result:captured.append(result)
                return result
            relay.request=traced
            assert relay.request(client,'worker',{'channel':'SUBSCRIBER'},transport=authority)['sent']==0
            status=relay.request(authority,'status',{})
            invocation=status['call_ids'][0]
            identity=identity_for(repo,invocation).to_wire()
            assert identity['delivery_id'] is not None and len(captured)==1
            assert len({os.getpid(),host.ready['pid'],authority.ready['pid'],client.ready['pid']})==4
            assert status['physical_calls']==status['active']==1
            target_facts=[tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts WHERE invocation_id=? ORDER BY 1,2,3',(invocation,))]
            assert 'UNKNOWN' in {r['fact_kind'] for r in x['c'].execute('SELECT * FROM transport_facts WHERE invocation_id=?',(invocation,))}
            physical_before=[json.loads(line) for line in x['path'].with_suffix('.fake-calls.jsonl').read_text().splitlines() if json.loads(line)['invocation']==invocation]
            assert [r['phase'] for r in physical_before].count('START')==1 and all(r['phase']!='END' for r in physical_before)
            if fault:assert any(r['phase']=='ACCEPTED' for r in physical_before)
            old_controller=relay.controller
            old_client_cap=dict(relay.sessions[client.ready['identity']]['cap'])
            host_proof=isolation.terminate_test_controller(host)
            assert host_proof['pid']==host.ready['pid'] and host.process.poll() is not None
            assert authority.process.poll() is None
            with pytest.raises((BrokenPipeError,OSError,EOFError)):relay.request(host,'controller_status',{})
            after=relay.request(authority,'status',{})
            assert after['physical_calls']==after['active']==1 and after['gate']=='BLOCKED'
            assert not any(json.loads(line)['phase']=='END' for line in x['path'].with_suffix('.fake-calls.jsonl').read_text().splitlines() if json.loads(line)['invocation']==invocation)
            assert [tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts WHERE invocation_id=? ORDER BY 1,2,3',(invocation,))]==target_facts
            with pytest.raises(PolicyDenied):supervisor_end(repo,host,invocation)
            clock.now+=101
            assert repo._one('SELECT state FROM smtp_account_gate')['state']=='BLOCKED'
            with pytest.raises(RuntimeError):relay.request(authority,'consume',captured[0])
            # Later owner-authorized consent, suppression and erasure cannot be
            # reset by a successor's startup or by host process-exit evidence.
            repo.change_user(1,is_active=0)
            for user in x['c'].execute('SELECT id FROM users WHERE id<>1').fetchall():repo.change_user(user[0],is_active=0)
            assert {'SENT','UNCERTAIN','SUPPRESSED'}<={row[0] for row in x['c'].execute('SELECT status FROM notification_deliveries_v2')}
            payload=x['c'].execute('SELECT before_payload_id FROM opportunity_events ORDER BY revision DESC LIMIT 1').fetchone()[0]
            assert repo.erase_payload(payload,OperatorApproval('ERASE',payload,'fictional-host-crash-owner'))=='COMPLETE'
            tables=('opportunity_events','subscriber_versions','privacy_actions','payload_tombstones','transport_facts')
            preserved={table:[tuple(r) for r in x['c'].execute('SELECT * FROM '+table+' ORDER BY 1,2')] for table in tables}
            deliveries=[tuple(r) for r in x['c'].execute('SELECT * FROM notification_deliveries_v2 ORDER BY 1')]
            evidence('H02-H09-before-successor-'+completion,repo,host,authority,client_pid=client.ready['pid'],
                host_exit=host_proof,invocation_identity=identity,physical_calls=1,physical_active=1,
                target_facts_unchanged=True,physical_acceptance=bool(fault))
        with hosted(x,clock) as (restarted,successor,new_relay,next_elected):
            assert next_elected['generation']==elected['generation']+1
            with pytest.raises(Fenced):old_controller._owned()
            with pytest.raises(KeyUnavailable):relay._rpc(client,'protected-key-v1',{})
            with pytest.raises(RuntimeError):new_relay.request(client,'advance',dict(now=clock(),key_capability=old_client_cap))
            new_relay.resume_authority(authority)
            assert new_relay.request(authority,'status',{})['active']==1
            with pytest.raises(RuntimeError):new_relay.request(authority,'consume',captured[0])
            with pytest.raises(RuntimeError):new_relay.request(authority,'elect',{})
            with pytest.raises(RuntimeError):new_relay.request(authority,'end',dict(identity,gate_generation=identity['gate_generation']+1))
            assert restarted._one('SELECT state FROM smtp_account_gate')['state']=='BLOCKED'
            with isolation.start_test_child('authority') as unrelated:
                unrelated.terminate()
                with pytest.raises(PolicyDenied):supervisor_end(restarted,unrelated,invocation)
            assert restarted._one('SELECT state FROM smtp_account_gate')['state']=='BLOCKED'
            for table,rows in preserved.items():
                assert [tuple(r) for r in x['c'].execute('SELECT * FROM '+table+' ORDER BY 1,2')]==rows
            if completion=='end':new_relay.request(authority,'end',identity)
            else:
                authority.terminate()
                assert restarted._one('SELECT state FROM smtp_account_gate')['state']=='BLOCKED'
                assert supervisor_end(restarted,authority,invocation)['pid']==authority.ready['pid']
            assert restarted._one('SELECT state FROM smtp_account_gate')['state']=='FREE'
            # Recovery adds only proven END evidence; pre-existing rows survive.
            for table,rows in preserved.items():
                current=[tuple(r) for r in x['c'].execute('SELECT * FROM '+table+' ORDER BY 1,2')]
                assert all(row in current for row in rows)
            assert [tuple(r) for r in x['c'].execute('SELECT * FROM notification_deliveries_v2 ORDER BY 1')]==deliveries
            with isolation.start_test_child('authority') as fresh_authority, isolation.start_test_child('client') as fresh_client:
                new_relay.open(fresh_authority);new_relay.request(fresh_authority,'elect',{})
                new_relay.open(fresh_client)
                assert new_relay.request(fresh_client,'worker',{'channel':'SUBSCRIBER'},transport=fresh_authority)['attempts']==0
                assert new_relay.request(fresh_authority,'status',{})['physical_calls']==0
                new_relay.close_child(fresh_client);new_relay.close_child(fresh_authority)
            evidence('H08-H11-after-exact-completion-'+completion,restarted,successor,authority,
                previous_host_pid=host.ready['pid'],invocation_identity=identity,physical_calls=1,
                successor_physical_calls=0,newer_facts_preserved=True)
    assert set(isolation._child_processes)==baseline_children


@pytest.mark.parametrize('enable',[False,True])
def test_h12_crash_after_actual_acquisition_recovers_only_after_bounded_lease(encrypted,enable):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;clock=Clock();clock.now=NOW
    with hosted(x,clock,enable=enable) as (repo,host,relay,elected):
        proof=isolation.terminate_test_controller(host)
        assert proof['pid']==host.ready['pid']
        evidence('H12-startup-'+str(enable),repo,host,host_exit=proof,physical_calls=0)
    with pytest.raises(KeyUnavailable):
        with hosted(x,clock):pass
    clock.now+=101
    with hosted(x,clock) as (repo,successor,relay,next_elected):
        assert next_elected['generation']==elected['generation']+1
        assert repo._one('SELECT count(*) n FROM transport_invocations')['n']==0
        assert repo._one('SELECT count(*) n FROM transport_facts')['n']==0
        evidence('H12-recovered-'+str(enable),repo,successor,previous_host_pid=host.ready['pid'],physical_calls=0)


def test_h12_h13_repeated_startup_host_deaths_preserve_one_surviving_physical_call(encrypted):
    from app.notifications.transport_arbiter import identity_for
    x=encrypted;clock=Clock();clock.now=NOW
    children=set(isolation._child_processes)
    with isolation.start_test_child('authority') as authority, isolation.start_test_child('client') as client:
        with hosted(x,clock) as (repo,host,relay,elected):
            relay.open(authority);relay.request(authority,'elect',{});relay.open(client)
            relay.request(authority,'configure',dict(kind='UNKNOWN',ended=False,fault=None))
            ticket=relay.request(client,'prepare',{'message_class':'ACCOUNT'},transport=authority)
            grant=relay.request(authority,'admit',ticket)
            assert relay.request(authority,'consume',grant)['kind']=='UNKNOWN'
            invocation=grant['identity']['invocation']
            identity=identity_for(repo,invocation).to_wire()
            initial_facts=[tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts ORDER BY 1,2,3')]
            proof=isolation.terminate_test_controller(host)
            assert authority.request('status',{})['active']==1
            evidence('H13-initial-host-death',repo,host,authority,host_exit=proof,invocation_identity=identity,physical_calls=1)
        for cycle in (1,2):
            clock.now+=101
            with hosted(x,clock,enable=False) as (repo,host,relay,replacement):
                assert replacement['generation']==elected['generation']+cycle
                # Crash during startup: this host acquired the real lease but
                # never enabled modes or rebound the independent physical caller.
                proof=isolation.terminate_test_controller(host)
                status=authority.request('status',{})
                assert authority.process.poll() is None and status['active']==status['physical_calls']==1
                assert status['gate']!='FREE'
                assert [tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts ORDER BY 1,2,3')]==initial_facts
                assert not any(json.loads(line)['phase']=='END' for line in x['path'].with_suffix('.fake-calls.jsonl').read_text().splitlines())
                evidence('H12-H13-startup-crash-'+str(cycle),repo,host,authority,host_exit=proof,invocation_identity=identity,physical_calls=1)
        clock.now+=101
        with hosted(x,clock) as (repo,host,relay,last):
            relay.resume_authority(authority)
            with pytest.raises(RuntimeError):relay.request(authority,'consume',grant)
            with pytest.raises(RuntimeError):relay.request(authority,'elect',{})
            assert relay.request(authority,'status',{})['physical_calls']==1
            relay.request(authority,'end',identity)
            assert relay.request(authority,'status',{})['active']==0
            assert repo._one('SELECT state FROM smtp_account_gate')['state']=='FREE'
            assert repo._one('SELECT count(*) n FROM transport_invocations')['n']==1
            evidence('H13-exact-final-END',repo,host,authority,invocation_identity=identity,physical_calls=1)
    assert set(isolation._child_processes)==children


@pytest.mark.parametrize('field,value',[('role','authority'),('controller_generation',999),('namespace','foreign.sqlite')])
def test_h14_host_capability_substitution_cannot_elect(encrypted,field,value):
    from app.monitoring.runtime_supervisor import ChildKeyRelay
    from app.notifications.transport_arbiter import get_state
    x=encrypted;create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=lambda:NOW,record_mode=True)
    with isolation.start_test_child('controller-host-crash-test') as host:
        relay=ChildKeyRelay(repo,x['codec'],None,controller_host=host,provider_identity='fixture-custody',custody_generation=1)
        try:
            cap=relay.issue(host);cap[field]=value
            with pytest.raises(RuntimeError):relay.request(host,'open',dict(path=str(x['path']),now=NOW,key_capability=cap))
            assert get_state(repo,'r2a:controller') is None
            assert repo._one('SELECT count(*) n FROM transport_invocations')['n']==0
        finally:relay.close()


def test_h14_host_preserves_administrative_approval_key_hold(encrypted):
    from app.notifications.transport_arbiter import get_state
    x=encrypted;create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=lambda:NOW,record_mode=True)
    company=repo._one('SELECT company_key FROM opportunity_state')['company_key']
    repo.approve('REDRIVE',company,'fictional-held-delivery')
    clock=Clock();clock.now=NOW
    with pytest.raises(RuntimeError):
        with hosted(x,clock):pass
    assert get_state(repo,'r2a:controller') is None
