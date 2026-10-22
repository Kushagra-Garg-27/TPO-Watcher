"""Actual entry point, writer, API, worker and guarded physical child lifecycle."""
import asyncio
from contextlib import contextmanager
import pytest
import isolation_bootstrap as isolation
from test_notification_migration_privacy import encrypted,NOW
from test_cummins_end_to_end_v2 import Feed,record,Clock
from app.database.notification_migrator import create_schema
from app.database.event_repository import EventRepository


@contextmanager
def running(x,*,mode=None,generation=1,records=None):
    from app.monitoring.runtime_supervisor import RuntimeSupervisor,LegacyExclusion
    from app.config import RuntimeConfig,RuntimeMode
    create_schema(x['c'],now_ms=NOW)
    clock=Clock();clock.now=NOW
    feed=Feed(records if records is not None else [record(id='runtime-new')])
    with isolation.start_test_child('authority') as child:
        runtime=RuntimeSupervisor(connection=x['c'],database=x['db'],codec=x['codec'],source=feed,
            authority=child,clock=clock,exclusion=LegacyExclusion(True,True,True,True),
            config=RuntimeConfig(mode=mode or RuntimeMode.V2_FAKE,controller_identity='fixture-owner',generation=generation,
                provider_identity='fixture-custody',custody_generation=1))
        try:
            runtime.start()
            yield runtime,feed,child,clock
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_s04_s08_s10_s26_real_entrypoint_selects_one_writer_and_independent_drain(encrypted):
    from app.main import run_guarded_once,create_application
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        app=create_application(runtime=runtime)
        assert app.state.notification_runtime is runtime
        before=x['c'].execute('SELECT count(*) FROM notification_deliveries').fetchone()[0]
        assert await run_guarded_once(runtime,poll=True,drain=False) is True
        assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==1
        assert x['c'].execute('SELECT count(*) FROM notification_intents').fetchone()[0]==1
        assert x['c'].execute('SELECT count(*) FROM notification_deliveries').fetchone()[0]==before
        assert runtime.relay.request(child,'status',{})['physical_calls']==0
        assert await runtime.drain_once()==1
        assert feed.fetches==1
        assert runtime.relay.request(child,'status',{})['physical_calls']==1
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_s09_identical_poll_does_not_duplicate_event_or_intent(encrypted):
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        assert await runtime.poll_once()
        assert await runtime.poll_once()
        assert feed.fetches==2
        assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==1
        assert x['c'].execute('SELECT count(*) FROM notification_intents').fetchone()[0]==1


@pytest.mark.asyncio
async def test_s26_old_writer_worker_and_direct_sender_are_fenced(encrypted):
    from app.monitoring.watcher import PlacementWatcher
    from app.subscribers.worker import DeliveryWorker
    from app.notifications.email import EmailNotificationProvider
    from app.database.event_repository import PolicyDenied
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        with pytest.raises(PolicyDenied):PlacementWatcher(db=x['db'])
        worker=DeliveryWorker(x['db'].deliveries,x['db'].tokens)
        with pytest.raises(PolicyDenied):await worker.process_batch_once()
        with pytest.raises(PolicyDenied):worker._send_smtp_email('fictional@example.invalid','fixture','fixture')
        with pytest.raises(PolicyDenied):EmailNotificationProvider()._send_email_to('fictional@example.invalid','fixture','fixture')
        assert runtime.relay.request(child,'status',{})['physical_calls']==0


def test_s26_attached_pipeline_cannot_become_a_second_writer(encrypted):
    from app.monitoring.notification_pipeline import NotificationPipeline,V2Options
    from app.database.event_repository import PolicyDenied
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        with pytest.raises(PolicyDenied):
            NotificationPipeline(runtime.repo,options=V2Options(writer=True),role='client')


def test_s06_account_factory_uses_same_child_without_changing_epoch(encrypted,monkeypatch):
    from app.api.email_service import get_email_service
    x=encrypted
    monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        before=runtime.repo._runtime()['epoch']
        result=get_email_service().send_verification_email('fictional@example.invalid','fixture-token',expires_ms=NOW+1000)
        assert result.kind=='ACCEPTED'
        assert runtime.repo._runtime()['epoch']==before
        assert runtime.relay.request(child,'status',{})['physical_calls']==1


def test_s06_s11_actual_account_route_non_enumeration_and_shared_gate(encrypted,monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.app import app
    x=encrypted
    monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        with TestClient(app) as client:
            yes=client.post('/api/v1/preferences/request-link',json={'email':'fictional@example.invalid'})
            no=client.post('/api/v1/preferences/request-link',json={'email':'missing@example.invalid'})
        assert yes.status_code==no.status_code==200
        assert yes.json()==no.json()
        assert runtime.relay.request(child,'status',{})['physical_calls']==1


def test_s03_off_attachment_does_not_change_epoch_and_fences_mail(encrypted):
    from app.config import RuntimeMode
    from app.notifications.email import EmailNotificationProvider
    from app.database.event_repository import PolicyDenied
    x=encrypted
    with running(x,mode=RuntimeMode.OFF) as (runtime,feed,child,clock):
        assert runtime.repo._runtime()['epoch']==0
        assert runtime.controller is None
        with pytest.raises(PolicyDenied):EmailNotificationProvider()._send_email_to('f@example.invalid','fixture','fixture')
        assert feed.fetches==0


@pytest.mark.parametrize('shape',['missing','partial','foreign'])
def test_s02_invalid_schema_refused_without_installing_or_changing_it(tmp_path,shape):
    from app.database.repository import DatabaseRepository
    from app.database.notification_migrator import connect_disposable,SchemaError
    from app.monitoring.runtime_supervisor import RuntimeSupervisor
    from app.config import RuntimeConfig,RuntimeMode
    path=tmp_path/'invalid.sqlite';db=DatabaseRepository(str(path))
    with connect_disposable(path) as c:
        if shape=='partial':c.execute('CREATE TABLE notification_runtime(singleton INTEGER)')
        if shape=='foreign':c.execute('CREATE TABLE notification_schema_migrations(version INTEGER)')
        before=c.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
        runtime=RuntimeSupervisor(connection=c,database=db,config=RuntimeConfig(mode=RuntimeMode.OFF))
        with pytest.raises(SchemaError):runtime.start()
        assert c.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()==before


@pytest.mark.asyncio
async def test_s07_worker_client_exit_does_not_disable_account_api(encrypted,monkeypatch):
    from app.api.email_service import get_email_service
    x=encrypted;monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,authority,clock), isolation.start_test_child('client') as client:
        await runtime.poll_once();runtime.pipeline.fanout_once()
        epoch=runtime.repo._runtime()['epoch']
        runtime.relay.open(client)
        result=runtime.relay.request(client,'worker',{'channel':'SUBSCRIBER'},transport=authority)
        assert result['sent']==1 and result['attempts']==1
        assert runtime.repo._runtime()['epoch']==epoch
        runtime.relay.close_child(client)
        assert get_email_service().send_verification_email('f@example.invalid','fixture',expires_ms=clock()+1000).kind=='ACCEPTED'
        assert runtime.repo._runtime()['epoch']==epoch
        assert runtime.relay.request(authority,'status',{})['physical_calls']==2


@pytest.mark.asyncio
async def test_s12_s22_actual_preference_optout_is_irreversible(encrypted,monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.app import app
    from app.api.session import create_session_token
    from app.config import settings
    x=encrypted;monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once();runtime.pipeline.fanout_once()
        with TestClient(app) as client:
            client.cookies.set('tpo_session',create_session_token(user_id=1,secret_key=settings.SECRET_KEY,max_age_seconds=3600))
            for enabled in (False,True):
                response=client.put('/api/v1/preferences',json=dict(pref_internship=enabled,pref_placement=False,pref_ppo=False))
                assert response.status_code==200
                assert {r[0] for r in x['c'].execute("SELECT status FROM notification_deliveries_v2")}=={'SUPPRESSED'}
        epoch=x['c'].execute('SELECT profile_epoch FROM subscriber_versions WHERE user_id=1').fetchone()[0]
        assert await runtime.drain_once()==0
        assert runtime.relay.request(child,'status',{})['physical_calls']==0
    with running(x,generation=2) as (runtime,feed,child,clock):
        assert x['c'].execute('SELECT profile_epoch FROM subscriber_versions WHERE user_id=1').fetchone()[0]==epoch
        assert await runtime.drain_once()==0
        assert runtime.relay.request(child,'status',{})['physical_calls']==0


@pytest.mark.asyncio
async def test_s11_all_three_classes_share_one_physical_account(encrypted,monkeypatch):
    from app.api.email_service import get_email_service
    from app.notifications.transport_arbiter import identity_for
    x=encrypted;monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        runtime.repo.add_admin()
        await runtime.poll_once();runtime.pipeline.fanout_once()
        runtime.relay.request(child,'configure',dict(kind='UNKNOWN',ended=False,fault=None))
        assert await runtime.drain_once()==0
        status=runtime.relay.request(child,'status',{})
        assert status['physical_calls']==1 and status['active']==1 and status['gate']=='BLOCKED'
        assert get_email_service().send_verification_email('f@example.invalid','fixture',expires_ms=clock()+1000).kind=='BUSY'
        assert await runtime.drain_once()==0
        invocation=status['call_ids'][0]
        clock.now+=200
        runtime.relay.request(child,'advance',{'now':clock()})
        assert runtime.relay.request(child,'status',{})['active']==1
        runtime.relay.request(child,'end',identity_for(runtime.repo,invocation).to_wire())
        assert runtime.relay.request(child,'status',{})['gate']=='FREE'
        runtime.relay.request(child,'configure',dict(kind='ACCEPTED',ended=True,fault=None))
        # Service the authority's finite round-robin waiters without resending UNKNOWN.
        results=[]
        for _ in range(3):
            results.append(await runtime.drain_once())
            result=get_email_service().send_verification_email('f@example.invalid','fixture',expires_ms=clock()+1000)
            if result.kind=='ACCEPTED':break
        assert result.kind=='ACCEPTED'
        status=runtime.relay.request(child,'status',{})
        assert {r['message_class'] for r in status['fidelity']}=={'ADMIN','SUBSCRIBER','ACCOUNT'}
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='UNCERTAIN'").fetchone()[0]==1


@pytest.mark.asyncio
async def test_s05_s19_competing_controller_and_stale_runtime_are_fenced(encrypted):
    from app.database.event_repository import Fenced
    from app.database.key_provider import KeyUnavailable
    from app.notifications.transport_arbiter import RuntimeController
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        before=runtime.repo._runtime()['epoch']
        with pytest.raises(Fenced):RuntimeController.acquire(runtime.repo)
        assert runtime.repo._runtime()['epoch']==before
        # A replacement requires the old parent's fictional lease to expire.
        clock.now+=86400001
        replacement=RuntimeController.acquire(runtime.repo)
        with pytest.raises(KeyUnavailable):await runtime.poll_once()
        with pytest.raises(RuntimeError,match='KeyUnavailable'):runtime.relay.request(child,'elect',{})
        with pytest.raises(Fenced):runtime.stop()
        assert feed.fetches==0
        replacement.shutdown()


@pytest.mark.asyncio
async def test_s14_worker_process_dies_before_claim_pending_work_survives(encrypted):
    x=encrypted
    with running(x) as (runtime,feed,child,clock), isolation.start_test_child('client') as worker:
        await runtime.poll_once();runtime.pipeline.fanout_once()
        runtime.relay.open(worker)
        with pytest.raises(EOFError):runtime.relay.request(worker,'crash',{})
        assert {r[0] for r in x['c'].execute('SELECT status FROM notification_deliveries_v2')}=={'PENDING'}
        assert await runtime.drain_once()==1
        assert runtime.relay.request(child,'status',{})['physical_calls']==1


@pytest.mark.asyncio
async def test_s15_pre_invocation_interruption_recovers_without_fabricated_transport(encrypted,monkeypatch):
    class Interrupted(BaseException):pass
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once()
        def interrupt(*args,**kwargs):raise Interrupted()
        monkeypatch.setattr(runtime.watcher.delivery_worker,'_generate_token',interrupt)
        with pytest.raises(Interrupted):await runtime.drain_once()
        assert {r[0] for r in x['c'].execute('SELECT status FROM notification_deliveries_v2')}=={'PROCESSING'}
        assert runtime.relay.request(child,'status',{})['physical_calls']==0
    with running(x,generation=2) as (runtime,feed,child,clock):
        assert {r[0] for r in x['c'].execute('SELECT status FROM notification_deliveries_v2')}=={'PENDING'}
        clock.now=x['c'].execute('SELECT max(next_ms) FROM notification_deliveries_v2').fetchone()[0]
        runtime.relay.request(child,'advance',{'now':clock()})
        assert await runtime.drain_once()==1
        assert runtime.relay.request(child,'status',{})['physical_calls']==1


@pytest.mark.asyncio
async def test_s16_s23_accepted_lost_ack_and_restart_never_duplicate(encrypted):
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once()
        runtime.pipeline.faults['ack']=True
        assert await runtime.drain_once()==1
        facts=[tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts ORDER BY 1,2,3')]
        assert runtime.relay.request(child,'status',{})['physical_calls']==1
    with running(x,generation=2) as (runtime,feed,child,clock):
        assert [tuple(r) for r in x['c'].execute('SELECT * FROM transport_facts ORDER BY 1,2,3')]==facts
        assert await runtime.drain_once()==0
        assert runtime.relay.request(child,'status',{})['physical_calls']==0


@pytest.mark.asyncio
async def test_s20_source_commit_interruption_retry_does_not_duplicate(encrypted):
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        def interrupted():raise RuntimeError('fictional acknowledgement interruption')
        runtime.pipeline.faults['after_source']=interrupted
        assert await runtime.poll_once() is False
        assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==1
        runtime.pipeline.faults.clear()
        assert await runtime.poll_once() is True
        assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==1
        assert x['c'].execute('SELECT count(*) FROM notification_intents').fetchone()[0]==1


@pytest.mark.asyncio
async def test_s21_seeded_baseline_and_repeated_stop_never_replay_history(encrypted):
    x=encrypted
    for generation in (1,2):
        with running(x,generation=generation,records=[record(id='history-a')]) as (runtime,feed,child,clock):
            assert await runtime.poll_once()
            assert await runtime.drain_once()==0
            assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==0
            assert {r[0] for r in x['c'].execute('SELECT revision FROM opportunity_state')}=={0}
            runtime.stop();epoch=runtime.repo._runtime()['epoch'];runtime.stop()
            assert runtime.repo._runtime()['epoch']==epoch


@pytest.mark.asyncio
async def test_s18_k15_controller_interruption_and_physical_death_need_positive_exit(encrypted):
    from app.notifications.transport_arbiter import RuntimeController,supervisor_end
    from app.database.event_repository import Fenced
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once()
        runtime.relay.request(child,'configure',dict(kind='UNKNOWN',ended=False,fault=None))
        assert await runtime.drain_once()==0
        invocation=runtime.relay.request(child,'status',{})['call_ids'][0]
        clock.now+=86400001
        replacement=RuntimeController.acquire(runtime.repo)
        assert x['c'].execute('SELECT state FROM smtp_account_gate').fetchone()[0]=='BLOCKED'
        with pytest.raises(Fenced):runtime.stop()
        child.terminate()
        assert x['c'].execute('SELECT state FROM smtp_account_gate').fetchone()[0]=='BLOCKED'
        proof=supervisor_end(runtime.repo,child,invocation)
        assert proof['identity']==child.ready['identity']
        assert x['c'].execute('SELECT state FROM smtp_account_gate').fetchone()[0]=='FREE'
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='UNCERTAIN'").fetchone()[0]==1
        replacement.shutdown()


@pytest.mark.asyncio
async def test_s10_drain_only_exceeds_one_quantum_and_backoff_is_finite(encrypted):
    from app.config import RuntimeMode
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        for _ in range(26):runtime.repo.add_user(branch='VIT_CE')
        await runtime.poll_once();runtime.pipeline.fanout_once()
    with running(x,generation=2,mode=RuntimeMode.DRAIN_ONLY_FAKE) as (runtime,feed,child,clock):
        with pytest.raises(KeyUnavailable):await runtime.poll_once()
        assert await runtime.drain_once()==25
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]==2
        assert await runtime.drain_once()==2
        assert feed.fetches==0
        waits=[];stop=asyncio.Event()
        async def wait(seconds):waits.append(seconds);stop.set()
        await runtime.drain_loop(stop,wait=wait)
        assert waits==[.1]


@pytest.mark.asyncio
async def test_s10_expired_session_between_quanta_refreshes_without_source_poll(encrypted,monkeypatch):
    """Missing drain-only refresh must fail after the old wall-clock deadline."""
    from types import SimpleNamespace
    from app.monitoring import runtime_supervisor as supervisor
    from app.config import RuntimeMode
    # Replace this module's clock reference only. OS/child/asyncio clocks stay real.
    elapsed=[1000.0]
    monkeypatch.setattr(supervisor,'time',SimpleNamespace(monotonic=lambda:elapsed[0]))
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        for _ in range(26):runtime.repo.add_user(branch='VIT_CE')
        await runtime.poll_once();runtime.pipeline.fanout_once()
    with running(x,generation=2,mode=RuntimeMode.DRAIN_ONLY_FAKE) as (runtime,feed,child,clock):
        assert runtime.listing_contract is None
        assert await runtime.drain_once()==25
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]==2
        elapsed[0]=1061.0
        assert await runtime.drain_once()==2
        assert feed.fetches==0 and runtime.relay.lifetime_ms==60000
        status=runtime.relay.request(child,'status',{})
        assert status['physical_calls']==27 and status['active']==0 and status['gate']=='FREE'
        assert x['c'].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='SENT'").fetchone()[0]==27
        assert x['c'].execute("SELECT count(DISTINCT invocation_id) FROM transport_facts WHERE fact_kind='ENDED'").fetchone()[0]==27
        assert x['c'].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='UNKNOWN'").fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize('denial',['expired-owner','stale-generation','foreign-owner','closed-relay','poisoned-custody','dead-session'])
async def test_s10_boundary_refresh_cannot_restore_invalid_authority(encrypted,monkeypatch,denial):
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        session=runtime.relay.sessions[child.ready['identity']]
        # Scoped fault injection is undone before normal owner cleanup.
        with monkeypatch.context() as fault:
            if denial=='expired-owner':fault.setattr(clock,'now',NOW+86400001)
            elif denial=='stale-generation':fault.setattr(runtime.controller,'generation',999)
            elif denial=='foreign-owner':fault.setattr(runtime.controller,'token','fictional-foreign-owner')
            elif denial=='closed-relay':fault.setattr(runtime.relay,'closed',True)
            elif denial=='poisoned-custody':fault.setattr(runtime.relay.custody,'failed',True)
            else:fault.setitem(session,'dead',True)
            with pytest.raises(KeyUnavailable):await runtime.drain_once()
            assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
            assert feed.fetches==0


@pytest.mark.asyncio
async def test_s10_boundary_refresh_does_not_revive_terminated_child(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        child.terminate()
        assert child.process.poll() is not None
        with pytest.raises((KeyUnavailable,ValueError,OSError,EOFError)):await runtime.drain_once()
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
        assert feed.fetches==0
        with pytest.raises((KeyUnavailable,ValueError,OSError,EOFError)):runtime.stop()
        assert runtime.closed


@pytest.mark.asyncio
async def test_s10_expiry_inside_accepted_invocation_preserves_unknown_and_no_resend(encrypted,monkeypatch):
    from types import SimpleNamespace
    from app.monitoring import runtime_supervisor as supervisor
    from app.database.key_provider import KeyUnavailable
    elapsed=[1000.0]
    monkeypatch.setattr(supervisor,'time',SimpleNamespace(monotonic=lambda:elapsed[0]))
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once()
        request=runtime.relay.request;rpc=runtime.relay._rpc_on
        failures=[]
        def nearing_expiry(target,operation,*args,**kwargs):
            if operation=='consume':elapsed[0]=1059.0
            return request(target,operation,*args,**kwargs)
        def expire_after_acceptance(target,operation,data,connection):
            accepted=x['c'].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ACCEPTED'").fetchone()[0]
            if data['operation']=='begin_database' and accepted and not failures:
                elapsed[0]=1061.0
                try:return rpc(target,operation,data,connection)
                except KeyUnavailable as exc:
                    failures.append(str(exc));raise
            return rpc(target,operation,data,connection)
        with monkeypatch.context() as boundary:
            boundary.setattr(runtime.relay,'request',nearing_expiry)
            boundary.setattr(runtime.relay,'_rpc_on',expire_after_acceptance)
            assert await runtime.drain_once()==0
        assert failures==['guarded key capability unavailable']
        assert tuple(x['c'].execute("SELECT status,attempt_count FROM notification_deliveries_v2").fetchone())==('UNCERTAIN',1)
        kinds={r[0] for r in x['c'].execute('SELECT fact_kind FROM transport_facts')}
        assert kinds=={'ACCEPTED','UNKNOWN'}
        assert x['c'].execute('SELECT state FROM smtp_account_gate').fetchone()[0]=='BLOCKED'
        # A new authorized quantum refreshes custody, never ambiguous transport.
        assert await runtime.drain_once()==0
        status=runtime.relay.request(child,'status',{})
        assert status['physical_calls']==1 and status['active']==0 and status['gate']=='BLOCKED'
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==1
        assert x['c'].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ENDED'").fetchone()[0]==0
        assert feed.fetches==1


@pytest.mark.asyncio
async def test_s29_actual_projection_api_and_separate_pruning(encrypted,monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.app import app
    from app.api import routes_opportunities
    x=encrypted;monkeypatch.setattr(routes_opportunities,'DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once()
        before=feed.fetches
        assert isinstance(await runtime.watcher.prune_stale_tokens_job(),int)
        assert feed.fetches==before
        with TestClient(app) as client:response=client.get('/api/v1/opportunities')
        assert response.status_code==200
        assert response.json()[0]['id']=='runtime-new'
        assert next(r for r in response.json() if r['id']=='history-a')['first_seen_at']=='2026-10-01T00:00:00+00:00'
        assert runtime.relay.request(child,'status',{})['physical_calls']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('started',[False,True])
async def test_s12_s13_optout_races_actual_physical_consumption(encrypted,monkeypatch,started):
    import hashlib
    from datetime import datetime,timezone,timedelta
    from fastapi.testclient import TestClient
    from app.api.app import app
    from app.notifications.transport_protocol import EphemeralMail
    from app.notifications.transport_arbiter import identity_for
    x=encrypted;monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once();runtime.pipeline.fanout_once()
        assert runtime.gateway.prepare_slot('SUBSCRIBER')
        claim=runtime.repo.claim_delivery(channel='SUBSCRIBER')
        permit=runtime.repo.authorize_send(claim)
        mail=EphemeralMail('SUBSCRIBER','fictional@example.invalid','fixture','<p>fixture</p>',NOW+1000,
            {'List-Unsubscribe':'<https://watcher.example.invalid/unsubscribe>',
             'List-Unsubscribe-Post':'List-Unsubscribe=One-Click'})
        grant=runtime.relay.request(child,'admit',runtime.gateway.ticket(permit,mail))
        runtime.relay.request(child,'configure',dict(kind='ACCEPTED',ended=False,fault=None))
        if started:assert runtime.relay.request(child,'consume',grant)['kind']=='ACCEPTED'
        token='fictional-unsubscribe-race'
        x['db'].tokens.create_token(1,hashlib.sha256(token.encode()).hexdigest(),'UNSUBSCRIBE',datetime.now(timezone.utc)+timedelta(minutes=10))
        with TestClient(app) as client:
            assert client.post('/api/v1/unsubscribe/confirm',json={'token':token}).status_code==200
        if not started:
            with pytest.raises(RuntimeError):runtime.relay.request(child,'consume',grant)
        status=runtime.relay.request(child,'status',{})
        assert status['physical_calls']==int(started) and status['active']==int(started)
        runtime.relay.request(child,'end',identity_for(runtime.repo,permit.invocation_id).to_wire())
        if started:
            assert runtime.repo.acknowledge(claim)=='SENT'
            assert x['c'].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ACCEPTED'").fetchone()[0]==1
        else:assert runtime.repo.recover_delivery(claim.key,unknown_commit=True)=='UNCERTAIN'
        assert x['c'].execute('SELECT is_active FROM users WHERE id=1').fetchone()[0]==0


def test_s24_k09_provider_failure_and_missing_binding_leave_startup_unstarted(encrypted):
    from app.monitoring.runtime_supervisor import RuntimeSupervisor,LegacyExclusion
    from app.config import RuntimeConfig,RuntimeMode
    from app.database.key_provider import KeyUnavailable
    x=encrypted;create_schema(x['c'],now_ms=NOW)
    before=x['codec'].fingerprint(x['c'])
    for codec in (None,x['codec']):
        x['authority'].failure='unwrap'
        runtime=RuntimeSupervisor(connection=x['c'],database=x['db'],codec=codec,clock=lambda:NOW,
            exclusion=LegacyExclusion(True,True,True,True),config=RuntimeConfig(mode=RuntimeMode.V2_FAKE))
        with pytest.raises(KeyUnavailable):runtime.start()
        assert not runtime.started
        assert x['codec'].fingerprint(x['c'])==before
        assert x['c'].execute('SELECT epoch FROM notification_runtime').fetchone()[0]==0
    x['authority'].failure=None


def test_s01_s27_s28_application_construction_does_not_start_or_migrate(encrypted):
    from app.main import create_application
    from app.monitoring.runtime_supervisor import RuntimeSupervisor
    from app.config import RuntimeConfig,RuntimeMode
    x=encrypted
    before=x['codec'].fingerprint(x['c'])
    runtime=RuntimeSupervisor(config=RuntimeConfig(mode=RuntimeMode.V2_FAKE))
    application=create_application(runtime=runtime)
    assert application.state.notification_runtime is runtime and not runtime.started
    assert x['codec'].fingerprint(x['c'])==before
    assert not x['c'].execute("SELECT 1 FROM sqlite_master WHERE name='notification_runtime'").fetchone()


def test_s24_k09_child_open_failure_releases_elected_controller_without_lease_expiry(encrypted):
    from app.monitoring.runtime_supervisor import RuntimeSupervisor,LegacyExclusion,current_runtime
    from app.notifications.transport_arbiter import RuntimeController,get_state
    from app.config import RuntimeConfig,RuntimeMode
    x=encrypted;create_schema(x['c'],now_ms=NOW)
    repo=EventRepository(x['c'],clock=lambda:NOW,record_mode=True)
    company=x['c'].execute('SELECT company_key FROM opportunity_state LIMIT 1').fetchone()[0]
    repo.approve('REDRIVE',company,'fictional-delivery')
    provider=x['codec'].provider
    with isolation.start_test_child('authority') as child:
        runtime=RuntimeSupervisor(connection=x['c'],database=x['db'],codec=x['codec'],source=Feed([]),
            authority=child,clock=lambda:NOW,exclusion=LegacyExclusion(True,True,True,True),
            config=RuntimeConfig(mode=RuntimeMode.V2_FAKE,controller_identity='fixture-owner',generation=1,
                provider_identity='fixture-custody',custody_generation=1))
        # Parent warm-up succeeds; the real child refuses the administrative key
        # after election. Cleanup must release ownership despite its absent session.
        with pytest.raises(Exception) as failure:runtime.start()
        owner=get_state(repo,'r2a:controller')
        assert owner['lease_ms']==NOW
        assert isinstance(failure.value,RuntimeError) and 'KeyUnavailable' in str(failure.value)
        assert runtime.closed and not runtime.started and current_runtime(x['path']) is None
        assert x['codec'].provider is provider
        assert all(repo._runtime()[key]=='OFF' for key in ('writer_mode','fanout_mode','send_mode'))
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
        assert x['c'].execute('SELECT count(*) FROM transport_facts').fetchone()[0]==0
        replacement=RuntimeController.acquire(repo)
        assert replacement.generation==owner['generation']+1
        replacement.shutdown()


@pytest.mark.asyncio
async def test_s30_restart_preserves_consent_acceptance_uncertainty_and_erasure(encrypted):
    from app.database.event_repository import OperatorApproval
    x=encrypted
    with running(x) as (runtime,feed,child,clock):
        await runtime.poll_once();assert await runtime.drain_once()==1
        feed.records=[record(id='runtime-new',maxPackage='29000')]
        await runtime.poll_once()
        runtime.relay.request(child,'configure',dict(kind='UNKNOWN',ended=True,fault=None))
        assert await runtime.drain_once()==0
        runtime.repo.change_user(1,is_active=0)
        payload=x['c'].execute('SELECT before_payload_id FROM opportunity_events ORDER BY revision DESC LIMIT 1').fetchone()[0]
        assert runtime.repo.erase_payload(payload,OperatorApproval('ERASE',payload,'fictional-owner'))=='COMPLETE'
        preserved={table:[tuple(r) for r in x['c'].execute('SELECT * FROM '+table+' ORDER BY 1,2')]
            for table in ('subscriber_versions','transport_facts','privacy_actions','payload_tombstones')}
    with running(x,generation=2) as (runtime,feed,child,clock):
        for table,rows in preserved.items():assert [tuple(r) for r in x['c'].execute('SELECT * FROM '+table+' ORDER BY 1,2')]==rows
        assert await runtime.drain_once()==0
        assert runtime.relay.request(child,'status',{})['physical_calls']==0


def test_s25_shutdown_hold_retires_unconsumed_account_grant(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted
    with running(x) as (runtime,feed,authority,clock), isolation.start_test_child('client') as client:
        runtime.relay.open(client)
        ticket=runtime.relay.request(client,'prepare',{'message_class':'ACCOUNT'},transport=authority)
        grant=runtime.relay.request(authority,'admit',ticket)
        runtime.hold()
        with pytest.raises(KeyUnavailable):runtime.account_gateway()
        with pytest.raises(RuntimeError):runtime.relay.request(authority,'consume',grant)
        assert runtime.relay.request(authority,'status',{})['physical_calls']==0
        assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==1
