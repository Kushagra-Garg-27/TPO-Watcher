"""Production capability behavior with fictional source/browser/SMTP boundaries."""
import hashlib
from contextlib import contextmanager
import pytest
from test_notification_migration_privacy import encrypted, NOW
from test_cummins_end_to_end_v2 import record
from app.database.notification_migrator import create_schema
from app.tpo.listing import ListingContract, ListingObservation
from app.tpo.production_source import AuthenticatedVIERPSource, SourceAuthorization


def test_production_owner_lock_excludes_another_instance_until_physical_release(tmp_path):
    from app.monitoring.production_runtime import OwnerLock
    path=tmp_path/'owner.lock'
    first=OwnerLock(path); second=OwnerLock(path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError):second.acquire()
    finally:first.close()
    second.acquire();second.close()


def test_pilot_requires_separate_scope_recipients_caps_expiry_and_sender():
    from app.monitoring.production_runtime import PilotAuthorization
    with pytest.raises(ValueError):PilotAuthorization('approval',(),0,0,0,'sender@example.invalid','source-scope')


class Browser:
    outcomes=[]
    def __init__(self,*args,listing_contract,clock):self.contract,self.clock=listing_contract,clock
    async def fetch_listing(self):
        value=self.outcomes.pop(0)
        if isinstance(value,Exception):raise value
        return ListingObservation(tuple(value),self.contract,self.clock(),False)


class Auth:
    context=object()
    async def get_valid_page(self):return object()
    async def stop(self):pass


@contextmanager
def build(x, *, pilot=None, transport=None, clock=None, config=None):
    from app.database.notification_migrator import ProductionDatabaseAuthority
    from app.monitoring.production_runtime import ProductionRuntime, ProductionConfig, LegacyShutdownEvidence
    create_schema(x['c'],now_ms=NOW)
    from app.database.account_schema import install_account_schema
    from app.database.notification_migrator import atomic
    with atomic(x['c']):install_account_schema(x['c'])
    # Checkpoint the fictional WAL before binding exact production-shaped identity.
    x['c'].execute('PRAGMA wal_checkpoint(FULL)')
    authority=ProductionDatabaseAuthority.bind(x['path'],expected_sha256=hashlib.sha256(x['path'].read_bytes()).hexdigest(),maintenance=True)
    contract=ListingContract('vierp','VIT',2028,'source-scope',60000)
    clock=clock or (lambda:NOW)
    source=AuthenticatedVIERPSource(authorization=SourceAuthorization('operator','authorization'),contract=contract,
        clock=clock,auth=Auth(),client_factory=Browser)
    with authority.scope(),authority.connect(require_ready=False) as c:
        authority.enroll(c,policy_reference='operator-reviewed-production-policy')
        yield ProductionRuntime(authority=authority,connection=c,database=x['db'],codec=x['codec'],source=source,
            clock=clock,config=config or ProductionConfig(),pilot=pilot,transport=transport,
            exclusion=LegacyShutdownEvidence('operator-reviewed-services',True,True,True,True))


@pytest.mark.asyncio
async def test_nosend_startup_runs_real_pipeline_but_cannot_consume_mail(encrypted):
    x=encrypted
    Browser.outcomes=[[record(id='active-a')],[record(id='active-a',maxPackage='29000')],[record(id='active-a')]]
    with build(x) as runtime:
        runtime.start()
        try:
            for _ in range(3):assert await runtime.poll_once()
            assert {r[0] for r in x['c'].execute('SELECT source_key FROM source_batches')}=={'production:vierp'}
            assert x['c'].execute("SELECT source_key FROM opportunity_state WHERE legacy_company_id='active-a'").fetchone()[0]=='production:vierp'
            await runtime.drain_once()
            assert x['c'].execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==3
            assert x['c'].execute('SELECT count(*) FROM notification_intents').fetchone()[0]==3
            assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
            assert runtime.diagnostics()['mode']=='NO_SEND'
            assert runtime.account_gateway().send_account('fictional@example.invalid','subject','html',NOW+100).kind=='BUSY'
            with pytest.raises(RuntimeError):
                with build(x) as duplicate:duplicate.start()
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_source_error_does_not_create_empty_batch_and_drain_still_progresses(encrypted):
    x=encrypted;Browser.outcomes=[[record(id='active-a')],ValueError('invalid source')]
    with build(x) as runtime:
        runtime.start()
        try:
            assert await runtime.poll_once()
            assert await runtime.poll_once() is False
            assert await runtime.drain_once()>=0
            assert x['c'].execute('SELECT count(*) FROM source_batches').fetchone()[0]==1
            assert x['c'].execute("SELECT is_active FROM companies WHERE id='active-a'").fetchone()[0]=='True'
            assert runtime.diagnostics()['last_poll_ok'] is False
        finally:runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,want',[('accepted','SENT'),('disconnected','UNCERTAIN')])
async def test_authorized_pilot_uses_durable_caps_and_never_retries_ambiguous_acceptance(encrypted,outcome,want):
    from app.database.event_repository import EventRepository
    from app.monitoring.production_runtime import PilotAuthorization
    from tests.unit.test_production_smtp import Server,adapter
    x=encrypted;create_schema(x['c'],now_ms=NOW)
    clock=[NOW]
    pilot=PilotAuthorization('separate-owner-pilot',('fictional-admin@example.invalid',),1,1,NOW+60000,
        'sender@example.invalid','source-scope')
    server=Server(outcome)
    Browser.outcomes=[[record(id='active-a')],[record(id='active-a',maxPackage='29000')]]
    with build(x,pilot=pilot,transport=adapter(server),clock=lambda:clock[0]) as runtime:
        from app.notifications.production_admin import register_admin
        from app.auth.accounts import OperatorAuthorization
        from app.database.notification_migrator import atomic
        register_admin(runtime.c, 'fictional-admin@example.invalid',
            authority=OperatorAuthorization('fictional-owner', 'separate-owner-pilot'),
            ownership_evidence='fictional-admin-inbox-ownership', now_ms=NOW)
        runtime.start()
        try:
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert x['c'].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='ADMIN'").fetchone()[0]==want, {
                'deliveries':[dict(r) for r in x['c'].execute('SELECT status,reason_code,attempt_count FROM notification_deliveries_v2')],
                'gate':[dict(r) for r in x['c'].execute('SELECT * FROM smtp_account_gate')],
                'data_calls':server.data_calls}
            clock[0]+=2000
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert server.data_calls==1
            assert x['c'].execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==1
            assert {r[0] for r in x['c'].execute('SELECT evidence_type FROM transport_facts')}=={'PRODUCTION_SMTP_ADAPTER'}
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_production_runtime_excludes_legacy_senders_and_unreviewed_students(encrypted):
    from app.notifications.email import EmailNotificationProvider
    from app.monitoring.watcher import PlacementWatcher
    from app.database.event_repository import PolicyDenied
    x=encrypted;Browser.outcomes=[[record(id='active-a')]]
    with build(x) as runtime:
        runtime.start()
        try:
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert x['c'].execute("SELECT count(*) FROM event_audience WHERE channel='SUBSCRIBER'").fetchone()[0]==0
            with pytest.raises(PolicyDenied):PlacementWatcher(db=x['db'])
            with pytest.raises(PolicyDenied):EmailNotificationProvider()._send_email_to('fictional@example.invalid','subject','html')
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_production_repository_and_api_threads_share_custody_without_legacy_fallback(encrypted,monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from app.main import create_application
    from app.api.email_service import get_email_service
    x=encrypted;Browser.outcomes=[[record(id='active-a')]]
    monkeypatch.setattr('app.database.repository.DB_PATH',str(x['path']))
    with build(x) as runtime:
        runtime.start()
        try:
            assert await runtime.poll_once()
            application=create_application(runtime=runtime)
            def request():
                assert x['db'].users.get_by_id(1)['id']==1
                uid=x['db'].users.create_user('new@example.invalid',2028,'VIT_CE')
                assert x['db'].users.get_by_id(uid)['is_verified']==0
                result=get_email_service().send_verification_email('new@example.invalid','fictional-token')
                assert result.kind=='BUSY'
                with TestClient(application) as client:
                    response=client.get('/api/v1/opportunities')
                assert response.status_code==200 and response.json()[0]['id']=='active-a'
            await asyncio.to_thread(request)
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_explicit_account_pilot_shares_caps_with_opportunity_transport(encrypted):
    import asyncio
    from app.monitoring.production_runtime import PilotAuthorization
    from tests.unit.test_production_smtp import Server,adapter
    x=encrypted
    pilot=PilotAuthorization('account-pilot',('verify@example.invalid',),1,1,NOW+60000,
        'sender@example.invalid','source-scope',message_classes=('ACCOUNT',))
    server=Server()
    with build(x,pilot=pilot,transport=adapter(server)) as runtime:
        runtime.start()
        try:
            request=asyncio.create_task(asyncio.to_thread(runtime.account_gateway().send_account,
                'verify@example.invalid','Verify ownership','<p>Fictional token</p>',NOW+10000))
            for _ in range(20):
                await asyncio.sleep(.01)
                if runtime.pending_account_requests:break
            await runtime.drain_once()
            result=await request
            assert result.kind=='ACCEPTED' and result.ended is True
            assert x['c'].execute("SELECT message_class FROM transport_invocations").fetchone()[0]=='ACCOUNT'
            assert runtime.account_gateway().send_account('other@example.invalid','subject','html',NOW+1000).kind=='BUSY'
            assert server.data_calls==1
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_api_thread_consent_suppresses_existing_qualified_obligation(encrypted):
    import asyncio
    from app.auth.accounts import AccountService
    from test_production_accounts import ready
    from app.database.notification_migrator import atomic
    x=encrypted;Browser.outcomes=[[record(id='active-a')]]
    with build(x) as runtime:
        runtime.start()
        try:
            with atomic(runtime.c):ready(AccountService(runtime.c,clock=runtime.clock),1)
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert runtime.c.execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING' AND channel='SUBSCRIBER'").fetchone()[0]==1
            await asyncio.to_thread(x['db'].users.set_unsubscribed,1)
            assert runtime.c.execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=='SUPPRESSED'
        finally:runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_stalled_smtp_keeps_loop_responsive_and_owner_fenced_until_thread_ends(encrypted,cancel):
    import asyncio
    import threading
    from dataclasses import replace
    from app.monitoring.production_runtime import PilotAuthorization, OwnerLock
    from tests.unit.test_production_smtp import Server,adapter
    from app.auth.accounts import OperatorAuthorization
    from app.notifications.production_admin import register_admin
    release=threading.Event();entered=threading.Event()
    class SlowServer(Server):
        def data(self,value):
            entered.set()
            release.wait(5)
            return super().data(value)
    server=SlowServer();transport=adapter(server)
    transport.settings=replace(transport.settings,timeout_seconds=1)
    pilot=PilotAuthorization('bounded-pilot',('fictional-admin@example.invalid',),2,2,NOW+60000,
        'sender@example.invalid','source-scope')
    Browser.outcomes=[[record(id='bounded-a')]]
    with build(encrypted,pilot=pilot,transport=transport) as runtime:
        register_admin(runtime.c,'fictional-admin@example.invalid',
            authority=OperatorAuthorization('owner','bounded-pilot'),ownership_evidence='fictional-inbox',now_ms=NOW)
        runtime.start()
        other=OwnerLock(runtime.lock.path)
        try:
            assert await runtime.poll_once()
            drain=asyncio.create_task(runtime.drain_once())
            ticks=0
            while not drain.done():
                ticks+=1
                await asyncio.sleep(.02)
                if cancel and entered.is_set() and ticks>=5:drain.cancel();break
            if cancel:
                with pytest.raises(asyncio.CancelledError):await drain
            else:await drain
            assert entered.is_set() and ticks>=5
            assert runtime.state=='SHUTDOWN_HOLD'
            assert runtime.c.execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ENDED'").fetchone()[0]==0
            assert runtime.c.execute("SELECT state FROM smtp_account_gate").fetchone()[0]=='BLOCKED'
            runtime.stop()
            with pytest.raises(RuntimeError):other.acquire()
        finally:
            release.set()
            while runtime._physical_thread and runtime._physical_thread.is_alive():await asyncio.sleep(.01)
            runtime.stop()
            other.acquire();other.close()


def test_daily_retention_uses_protected_repository_without_removing_delivery_facts(encrypted,monkeypatch):
    clock=[NOW]
    with build(encrypted,clock=lambda:clock[0]) as runtime:
        runtime.start()
        try:
            calls=[]
            original=runtime.database.tokens.prune_stale_tokens
            def prune(*,retention_days):
                calls.append(retention_days)
                return original(retention_days=retention_days)
            monkeypatch.setattr(runtime.database.tokens,'prune_stale_tokens',prune)
            before=runtime.c.execute('SELECT count(*) FROM notification_deliveries').fetchone()[0]
            runtime.prune_credentials_once();runtime.prune_credentials_once()
            clock[0]+=86400000
            runtime.prune_credentials_once()
            assert calls==[30,30]
            assert runtime.c.execute('SELECT count(*) FROM notification_deliveries').fetchone()[0]==before
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_info_expiry_during_smtp_setup_blocks_data_even_with_fresh_source(encrypted):
    from app.monitoring.production_runtime import PilotAuthorization,ProductionConfig
    from app.auth.accounts import AccountService
    from test_production_accounts import ready
    from app.database.notification_migrator import atomic
    from tests.unit.test_production_smtp import Server,adapter
    clock=[NOW]
    class ExpiringServer(Server):
        def rcpt(self,destination):
            clock[0]+=2000
            return super().rcpt(destination)
    server=ExpiringServer()
    pilot=PilotAuthorization('expiry-pilot',('destination@example.invalid',),1,1,NOW+60000,
        'sender@example.invalid','source-scope',message_classes=('SUBSCRIBER',))
    Browser.outcomes=[[record(id='expiry-a')]]
    with build(encrypted,pilot=pilot,transport=adapter(server),clock=lambda:clock[0],
            config=ProductionConfig(info_ttl_ms=1000)) as runtime:
        runtime.start()
        try:
            with atomic(runtime.c):ready(AccountService(runtime.c,clock=runtime.clock),1)
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert server.data_calls==0
            assert runtime.c.execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==1
            assert runtime.c.execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ACCEPTED'").fetchone()[0]==0
        finally:runtime.stop()


@pytest.mark.asyncio
async def test_production_notification_preferences_link_exchanges_for_account_session(encrypted,monkeypatch):
    from email import message_from_bytes,policy
    import re
    from fastapi.testclient import TestClient
    from app.main import create_application
    from app.monitoring.production_runtime import PilotAuthorization
    from app.auth.accounts import AccountService
    from test_production_accounts import ready
    from app.database.notification_migrator import atomic
    from tests.unit.test_production_smtp import Server,adapter
    class CaptureServer(Server):
        def data(self,value):
            self.message=message_from_bytes(value,policy=policy.default)
            return super().data(value)
    server=CaptureServer()
    pilot=PilotAuthorization('link-pilot',('destination@example.invalid',),1,1,NOW+60000,
        'sender@example.invalid','source-scope',message_classes=('SUBSCRIBER',))
    Browser.outcomes=[[record(id='link-a')]]
    monkeypatch.setattr('app.database.repository.DB_PATH',str(encrypted['path']))
    with build(encrypted,pilot=pilot,transport=adapter(server)) as runtime:
        runtime.start()
        try:
            with atomic(runtime.c):ready(AccountService(runtime.c,clock=runtime.clock),1)
            assert await runtime.poll_once()
            await runtime.drain_once()
            assert server.data_calls==1
            html=server.message.get_body(preferencelist=('html',)).get_content()
            token=re.search(r'/preferences/confirm#token=([^"<\s]+)',html).group(1)
            with TestClient(create_application(runtime=runtime),base_url='https://tpowatcher.duckdns.org') as client:
                response=client.post('/api/v1/preferences/confirm',json={'token':token})
                assert response.status_code==200,response.text
                assert client.get('/api/v1/account').status_code==200
                assert client.post('/api/v1/preferences/confirm',json={'token':token}).status_code!=200
        finally:runtime.stop()
