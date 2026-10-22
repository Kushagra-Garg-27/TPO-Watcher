"""Explicit production capability, single process ownership and bounded pilot.

The S1 representation version is reused; its fictional fixture authorization is
not. A separately enrolled exact database, protected custody and source approval
are required. No environment flag silently grants physical delivery authority.
"""
import asyncio
from contextlib import contextmanager, ExitStack
from contextvars import copy_context
from dataclasses import dataclass
import hashlib
import json
import os
import queue
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

from app.database.event_repository import EventRepository, Fenced, PolicyDenied
from app.database.key_provider import KeyUnavailable
from app.database.notification_migrator import atomic, active_production_authority
from app.database.protected_payloads import ProtectedPayloads, binding
from app.monitoring.notification_pipeline import NotificationPipeline, V2Options, TransportResult
from app.notifications.production_smtp import SMTPTransport, mailbox
from app.notifications.transport_protocol import MailOutcome
from app.tpo.production_source import AuthenticatedVIERPSource

_registry={}
_registry_lock=threading.RLock()


def current_production_runtime(path=None):
    with _registry_lock:
        if path is None:return next(iter(_registry.values()),None)
        return _registry.get(str(Path(path).resolve()))


class OwnerLock:
    """Kernel-owned lock remains held until this process closes/exits."""
    def __init__(self,path):
        self.path,self.file=Path(path),None
        self._close_lock=threading.Lock()
    def acquire(self):
        if self.file is not None:raise Fenced('owner lock already held')
        if self.path.is_symlink():raise Fenced('owner lock may not be a symlink')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        handle=os.fdopen(fd,'r+b',buffering=0)
        try:
            if os.name=='nt':
                import msvcrt
                if self.path.stat().st_size==0:handle.write(b'0')
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close();raise Fenced('another production source/worker owner is active') from None
        self.file=handle
    def close(self):
        with self._close_lock:
            if self.file is not None:
                self.file.close();self.file=None


@dataclass(frozen=True)
class LegacyShutdownEvidence:
    reference:str
    writer:bool=False
    subscriber:bool=False
    admin:bool=False
    account:bool=False
    def validate(self):
        if (not isinstance(self.reference,str) or not self.reference.strip()
                or any(getattr(self,k) is not True for k in ('writer','subscriber','admin','account'))):
            raise KeyUnavailable('independent legacy writer and every sender shutdown evidence required')


@dataclass(frozen=True)
class ProductionConfig:
    info_ttl_ms:int=60000
    idle_wait_ms:int=1000
    action_base:str='https://tpowatcher.duckdns.org'
    def __post_init__(self):
        V2Options(info_ttl_ms=self.info_ttl_ms,idle_wait_ms=self.idle_wait_ms)
        parsed=urlparse(self.action_base)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('explicit HTTPS application origin required')


@dataclass(frozen=True)
class PilotAuthorization:
    reference:str
    recipients:tuple[str,...]
    per_recipient_limit:int
    total_limit:int
    expires_ms:int
    sender:str
    source_scope_reference:str
    minimum_interval_ms:int=1500
    message_classes:tuple[str,...]=('ADMIN','SUBSCRIBER')
    def __post_init__(self):
        if (not isinstance(self.reference,str) or not self.reference.strip() or len(self.reference)>256
                or not isinstance(self.recipients,tuple) or not 1<=len(self.recipients)<=25
                or len(set(self.recipients))!=len(self.recipients)
                or type(self.per_recipient_limit) is not int or not 1<=self.per_recipient_limit<=10
                or type(self.total_limit) is not int or not 1<=self.total_limit<=100
                or type(self.expires_ms) is not int or self.expires_ms<=0
                or not isinstance(self.source_scope_reference,str) or not self.source_scope_reference.strip()
                or type(self.minimum_interval_ms) is not int or not 1000<=self.minimum_interval_ms<=3600000
                or not isinstance(self.message_classes,tuple) or not self.message_classes
                or len(set(self.message_classes))!=len(self.message_classes)
                or set(self.message_classes)-{'ADMIN','SUBSCRIBER','ACCOUNT'}):
            raise ValueError('separately approved finite pilot recipients, scope and limits required')
        mailbox(self.sender)
        for destination in self.recipients:mailbox(destination)


class ProductionRuntime:
    def __init__(self,*,authority,connection,database,codec,source,clock,exclusion,
                 config=None,pilot=None,transport=None):
        self.authority,self.c,self.database,self.codec=authority,connection,database,codec
        self.source,self.clock,self.exclusion=source,clock,exclusion
        self.config=config or ProductionConfig()
        if not isinstance(self.config,ProductionConfig):raise ValueError('typed production configuration required')
        if (pilot is None)!=(transport is None) or pilot is not None and (
                not isinstance(pilot,PilotAuthorization) or not isinstance(transport,SMTPTransport)):
            raise ValueError('physical transport requires separate explicit pilot authorization')
        self.pilot,self.transport=pilot,transport
        self.identity='production-'+secrets.token_hex(16)
        self.token=secrets.token_hex(32)
        self.generation=None;self.started=False;self.closed=False;self.state='NEW'
        self.last_poll_ok=self.last_poll_ms=self.last_drain_ms=None
        self.pipeline=None;self.repo=None;self.worker=None
        self.lock=OwnerLock(Path(authority.path).with_suffix(Path(authority.path).suffix+'.runtime.lock'))
        self._send_lock=threading.RLock()
        self._accounts=queue.Queue(maxsize=25)
        self._physical_thread=None
        self._physical_cancel=None
        self._physical_done=None
        self.last_prune_ms=None

    def validate_connection(self,connection):
        if active_production_authority(connection) is not self.authority:
            raise PolicyDenied('exact active production database capability required')

    def validate_source(self,source):
        if source is not self.source or not isinstance(source,AuthenticatedVIERPSource) or source.synthetic is not False:
            raise PolicyDenied('exact trusted production source required')

    def start(self):
        if self.closed:raise Fenced('stopped production runtime cannot restart')
        if self.started:return self
        self.validate_connection(self.c);self.validate_source(self.source)
        if (not isinstance(self.exclusion,LegacyShutdownEvidence) or not isinstance(self.codec,ProtectedPayloads)
                or binding(self.c) is not self.codec or not callable(self.clock)
                or Path(self.database.db_path).resolve()!=self.authority.path):
            raise KeyUnavailable('complete production capabilities required')
        self.exclusion.validate();self.authority.mark_ready(self.c)
        from app.database.account_schema import account_policy_enabled
        if not account_policy_enabled(self.c):raise PolicyDenied('installed production student account policy required')
        if self.pilot and (self.pilot.expires_ms<=self.clock() or self.pilot.expires_ms>self.clock()+86400000
                or self.pilot.sender!=self.transport.settings.sender
                or self.pilot.source_scope_reference!=self.source.contract.evidence_reference):
            raise PolicyDenied('current pilot sender and independently approved source scope required')
        self.lock.acquire()
        try:
            self.repo=EventRepository(self.c,clock=self.clock,record_mode=True,
                info_ttl_ms=self.config.info_ttl_ms,production_binding=self)
            with atomic(self.c):
                old=self.repo._one("SELECT value FROM system_state WHERE key='production:controller'")
                self.generation=json.loads(old['value'])['generation']+1 if old else 1
                if self.repo._one("SELECT 1 FROM system_state WHERE key='r2a:controller'"):
                    raise PolicyDenied('fictional controller cannot be adopted as production authority')
                self._put('production:controller',dict(token=self.token,identity=self.identity,generation=self.generation,
                    pid=os.getpid(),state='RUNNING',legacy_evidence=self.exclusion.reference))
                self.c.execute("UPDATE notification_runtime SET writer_mode='V2',fanout_mode='V2',send_mode=?,epoch=epoch+1",
                    ('V2' if self.pilot else 'OFF',))
            self.started=True;self.state='PILOT' if self.pilot else 'NO_SEND'
            options=V2Options(writer=True,fanout=True,drain=bool(self.pilot),transport=bool(self.pilot),
                info_ttl_ms=self.config.info_ttl_ms,idle_wait_ms=self.config.idle_wait_ms)
            self.pipeline=NotificationPipeline(self.repo,transport=self,options=options,role='production',production_binding=self)
            self.pipeline.runtime_owner=self
            self.pipeline.bind_listing_contract(self.source.contract)
            self.pipeline.bind_legacy(self.database)
            from app.subscribers.worker import DeliveryWorker
            self.worker=DeliveryWorker(self.database.deliveries,self.database.tokens,notification_pipeline=self.pipeline,
                batch_size=10,lease_seconds=60)
            self.pipeline.recover_once()
            with _registry_lock:
                key=str(self.authority.path)
                if key in _registry:raise Fenced('production runtime already registered')
                _registry[key]=self
            return self
        except BaseException:
            if self.started:self.stop()
            else:self.lock.close()
            raise

    @property
    def action_base(self):return self.config.action_base

    def _put(self,key,value):
        self.c.execute("INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key,json.dumps(value,sort_keys=True,separators=(',',':')),str(self.clock())))

    @contextmanager
    def repository_connection(self):
        """Thread-owned API unit with the same exact-file and custody boundary."""
        from app.database.notification_migrator import production_namespace
        from app.database.protected_payloads import bound_codec, protected_scope
        with self._send_lock,ExitStack() as stack:
            if self.closed:raise KeyUnavailable('production repository owner stopped')
            if production_namespace(self.authority.path) is None:stack.enter_context(self.authority.scope())
            existing=bound_codec(self.authority.path)
            if existing is None:stack.enter_context(protected_scope(self.authority.path,self.codec))
            elif existing is not self.codec:raise KeyUnavailable('foreign API custody scope')
            connection=stack.enter_context(self.authority.connect())
            with atomic(connection):yield connection

    def require_running(self,operation,*,connection=None):
        if not self.started or self.closed or self.state not in ('NO_SEND','PILOT') or self.lock.file is None:
            raise KeyUnavailable('production runtime is held')
        connection=self.c if connection is None else connection
        self.validate_connection(connection)
        row=connection.execute("SELECT value FROM system_state WHERE key='production:controller'").fetchone()
        owner=json.loads(row[0]) if row else {}
        if owner.get('token')!=self.token or owner.get('state')!='RUNNING':raise Fenced('stale production runtime')
        if operation=='send' and (self.pilot is None or self.pilot.expires_ms<=self.clock()):
            raise KeyUnavailable('physical sending is not authorized')

    def recipient_eligible(self,key,*,connection=None):
        connection=self.c if connection is None else connection
        self.validate_connection(connection)
        mapping=connection.execute('SELECT * FROM recipient_associations WHERE recipient_key=?',(key,)).fetchone()
        if not mapping:return False
        if mapping['user_id'] is None:
            # Explicit administrator destination registration is separate from students.
            from app.notifications.production_admin import admin_eligible
            return admin_eligible(connection,key) and bool(connection.execute("SELECT 1 FROM recipient_handles WHERE recipient_key=? AND role='ADMIN'",(key,)).fetchone())
        from app.auth.accounts import production_notification_eligible
        return production_notification_eligible(connection,mapping['user_id'],now_ms=self.clock())

    def _destination(self,recipient_key):
        mapping=self.repo._one('SELECT * FROM recipient_associations WHERE recipient_key=?',(recipient_key,))
        if not mapping:raise PolicyDenied('recipient association unavailable')
        if mapping['user_id'] is not None:
            from app.auth.accounts import production_recipient_destination
            destination=production_recipient_destination(self.c,mapping['user_id'])
            if destination is None:raise PolicyDenied('verified notification destination required')
            return mailbox(destination)
        from app.database.key_provider import PayloadContext
        context=PayloadContext(self.codec.namespace,recipient_key,'RECIPIENT',hashlib.sha256(b'ADMIN:'+recipient_key.encode()).hexdigest())
        return mailbox(self.codec.decrypt(context,mapping['protected_destination'],mapping['key_reference'])['destination'])

    def _usage(self):
        key='production:pilot:'+hashlib.sha256(self.pilot.reference.encode()).hexdigest()
        row=self.repo._one('SELECT value FROM system_state WHERE key=?',(key,))
        policy=hashlib.sha256(json.dumps(self.pilot.__dict__,sort_keys=True).encode()).hexdigest()
        value=json.loads(row['value']) if row else dict(policy=policy,total=0,recipients={},last_ms=None)
        if value['policy']!=policy:raise PolicyDenied('pilot approval may not be reused with changed scope')
        return key,value

    def _destination_permitted(self,destination):
        if self.pilot is None or self.pilot.expires_ms<=self.clock() or destination not in self.pilot.recipients:return False
        _,usage=self._usage();digest=hashlib.sha256(destination.encode()).hexdigest()
        return (usage['total']<self.pilot.total_limit and usage['recipients'].get(digest,0)<self.pilot.per_recipient_limit
            and (usage['last_ms'] is None or self.clock()-usage['last_ms']>=self.pilot.minimum_interval_ms))

    def delivery_permitted(self,delivery):
        if self.pilot is None or delivery['channel'] not in self.pilot.message_classes:return False
        try:return self._destination_permitted(self._destination(delivery['recipient_key']))
        except (ValueError,KeyUnavailable,PolicyDenied):return False

    def reserve_dispatch(self,delivery):
        if not self.c.in_transaction:raise PolicyDenied('dispatch caps require invocation transaction')
        self.require_running('send')
        destination=self._destination(delivery['recipient_key'])
        self._reserve_destination(destination)

    def reserve_account_dispatch(self,destination):
        if (not self.c.in_transaction or self.pilot is None or 'ACCOUNT' not in self.pilot.message_classes):
            raise PolicyDenied('explicit ACCOUNT pilot authorization required')
        self._reserve_destination(destination)

    def _reserve_destination(self,destination):
        if not self._destination_permitted(destination):raise Fenced('pilot recipient, pacing or cap denied')
        key,usage=self._usage();digest=hashlib.sha256(destination.encode()).hexdigest()
        usage['total']+=1;usage['recipients'][digest]=usage['recipients'].get(digest,0)+1;usage['last_ms']=self.clock()
        self._put(key,usage)

    async def submit_delivery(self,permit,message):
        self.require_running('send')
        with self._send_lock:
            with atomic(self.c):
                if (self.repo._permits.get(permit.invocation_id) is not permit or permit.consumed or permit.retired):
                    raise Fenced('single-use production permit required')
                delivery=self.repo._delivery(permit.handle,('SENDING',),send_mode=True)
                event=self.repo._context(delivery['intent_id'])[1]
                if not self.repo._authorized(self.repo._audience(delivery)) or not self.repo._source_recent(event):
                    raise Fenced('current consent and source required at consumption')
                destination=self._destination(delivery['recipient_key'])
                if destination not in self.pilot.recipients:raise Fenced('approved destination changed')
                delivery_expiry=delivery['expires_ms']
                permit.consumed=True
        def authorize_data():
            with self._send_lock:
                self.require_running('send')
                delivery=self.repo._delivery(permit.handle,('SENDING',),send_mode=True)
                event=self.repo._context(delivery['intent_id'])[1]
                return (self.repo._authorized(self.repo._audience(delivery)) and self.repo._source_recent(event)
                    and (delivery['expires_ms'] is None or delivery['expires_ms']>self.clock())
                    and self._destination(delivery['recipient_key'])==destination)
        try:
            result=await self._physical_send(destination,message['subject'],message['html'],
                message.get('headers',{}),authorize_data=authorize_data,expires_ms=delivery_expiry)
        except asyncio.CancelledError:
            self._record_result(permit.invocation_id,TransportResult('UNKNOWN',False))
            raise
        self._record_result(permit.invocation_id,result)
        return result

    async def _physical_send(self,destination,subject,html,headers,*,authorize_data,expires_ms=None):
        """Keep SQLite on its owner thread; a timed-out sender retains its fence.

        Cancellation never proves physical END. Late provider results stay held
        for operator reconciliation rather than reopening the invocation.
        """
        if self._physical_thread is not None and self._physical_thread.is_alive():
            raise Fenced('previous physical sender has not ended')
        loop=asyncio.get_running_loop()
        owner_context=copy_context()
        cancelled=threading.Event();done=threading.Event();outcome=[]
        expiry=min(self.pilot.expires_ms,expires_ms or self.pilot.expires_ms)
        duration=min(self.transport.settings.timeout_seconds,max(0,(expiry-self.clock())/1000))
        deadline=time.monotonic()+duration
        def request_data_authority():
            ready=threading.Event();allowed=[]
            def check():
                try:allowed.append(not cancelled.is_set() and bool(authorize_data()))
                except Exception:allowed.append(False)
                finally:ready.set()
            try:loop.call_soon_threadsafe(check,context=owner_context)
            except RuntimeError:return False
            return ready.wait(max(0,deadline-time.monotonic())) and bool(allowed and allowed[0]) and not cancelled.is_set()
        def physical():
            try:
                outcome.append(self.transport.send(destination,subject,html,headers,
                    deadline_monotonic=deadline,authority_expires_ms=expiry,clock=self.clock,
                    cancelled=cancelled,authorize_data=request_data_authority))
            except BaseException:outcome.append(TransportResult('UNKNOWN',False))
            finally:
                done.set()
                # stop() cannot release this process's lock while we can still act.
                if self.closed:self.lock.close()
        thread=threading.Thread(target=physical,name='tpo-physical-smtp',daemon=True)
        self._physical_thread,self._physical_cancel,self._physical_done=thread,cancelled,done
        thread.start()
        try:
            while not done.is_set() and time.monotonic()<deadline:
                await asyncio.sleep(min(.01,max(0,deadline-time.monotonic())))
            if done.is_set():return outcome[0]
            cancelled.set()
            self.hold()
            return TransportResult('UNKNOWN',False)
        except BaseException:
            cancelled.set()
            self.hold()
            raise

    def _record_result(self,invocation,result):
        if not isinstance(result,TransportResult):raise PolicyDenied('typed production outcome required')
        self.repo.record_fact(invocation,result.kind,fact_key='smtp-result',evidence_type='PRODUCTION_SMTP_ADAPTER',
            evidence_reference='smtp:'+invocation)
        if result.ended:self.repo.record_fact(invocation,'ENDED',fact_key='socket-closed',
            evidence_type='PRODUCTION_SMTP_ADAPTER',evidence_reference='socket-close:'+invocation)
        else:
            with atomic(self.c):self.repo._block(invocation)

    async def poll_once(self):
        self.require_running('writer')
        try:
            observation=await self.source.fetch_listing()
            with self._send_lock:self.last_poll_ok=bool(await self.pipeline.poll(self.source,observation=observation))
        except Exception:self.last_poll_ok=False
        self.last_poll_ms=self.clock()
        return self.last_poll_ok

    async def drain_once(self):
        self.require_running('drain')
        if self.pilot and self.pilot.expires_ms<=self.clock():self.hold();return 0
        with self._send_lock:
            self.pipeline.recover_once();progress=self.pipeline.fanout_once()
            if self.pilot:
                progress+=await self._drain_account_once()
                if self.state=='PILOT':progress+=await self.worker.process_batch_once()
        self.last_drain_ms=self.clock()
        return progress

    def account_gateway(self):return ProductionAccountGateway(self)

    @property
    def pending_account_requests(self):return self._accounts.qsize()

    async def _drain_account_once(self):
        try:request=self._accounts.get_nowait()
        except queue.Empty:return 0
        invocation=None
        try:
            with request['lock']:
                if request['cancelled'] or request['expires']<=self.clock() or time.monotonic()>request['deadline']:
                    request['result']=MailOutcome('EXPIRED');return 0
                if not self._destination_permitted(request['destination']):
                    request['result']=MailOutcome('BUSY');return 0
                handle=self.repo.reserve_account(lease_ms=60000)
                if handle is None:request['result']=MailOutcome('BUSY');return 0
                permit=self.repo.authorize_account(handle,production_destination=request['destination'])
                invocation=permit.invocation_id
                request['invocation']=invocation
                permit.consumed=True
            def authorize_data():
                self.require_running('send')
                return not request['cancelled'] and request['expires']>self.clock()
            result=await self._physical_send(request['destination'],request['subject'],request['html'],{},
                authorize_data=authorize_data,expires_ms=request['expires'])
            self._record_result(invocation,result)
            if result.kind=='UNKNOWN':
                with atomic(self.c):self.repo._block(invocation)
            request['result']=MailOutcome(result.kind,invocation,result.ended)
            return 1
        except asyncio.CancelledError:
            if invocation:
                self._record_result(invocation,TransportResult('UNKNOWN',False))
                request['result']=MailOutcome('UNKNOWN',invocation,False)
            else:request['result']=MailOutcome('BUSY')
            raise
        except Exception:
            if invocation:
                self.repo.record_fact(invocation,'UNKNOWN',fact_key='account-exception',
                    evidence_type='PRODUCTION_SMTP_ADAPTER',evidence_reference='account-exception:'+invocation)
                with atomic(self.c):self.repo._block(invocation)
                request['result']=MailOutcome('UNKNOWN',invocation,False)
            else:request['result']=MailOutcome('BUSY')
            return 0
        finally:request['ready'].set()

    def diagnostics(self):
        return dict(state=self.state,mode='PILOT' if self.pilot else 'NO_SEND',started=self.started,
            closed=self.closed,production_enabled=self.started and not self.closed,last_poll_ok=self.last_poll_ok,
            runtime_available=self.started and not self.closed and self.state in ('NO_SEND','PILOT'),
            last_poll_ms=self.last_poll_ms,last_drain_ms=self.last_drain_ms,source=self.source.diagnostics())

    async def run(self,stop,*,poll_interval_ms=60000):
        if type(poll_interval_ms) is not int or not 1000<=poll_interval_ms<=3600000:raise ValueError('bounded polling interval required')
        self.start()
        async def poll_loop():
            while not stop.is_set():
                await self.poll_once()
                await asyncio.sleep(poll_interval_ms/1000)
        async def drain_loop():
            while not stop.is_set():
                await self.drain_once()
                self.prune_credentials_once()
                await asyncio.sleep(self.config.idle_wait_ms/1000)
        tasks=[asyncio.create_task(poll_loop()),asyncio.create_task(drain_loop()),asyncio.create_task(stop.wait())]
        try:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.stop();await self.source.close()

    def prune_credentials_once(self):
        """Daily expired credential cleanup; delivery and consent facts remain."""
        self.require_running('maintenance')
        now=self.clock()
        if self.last_prune_ms is not None and now-self.last_prune_ms<86400000:return 0
        count=self.database.tokens.prune_stale_tokens(retention_days=30)
        self.last_prune_ms=now
        return count

    def hold(self):
        if not self.started or self.closed or self.state=='SHUTDOWN_HOLD':return
        with atomic(self.c):
            self.c.execute("UPDATE notification_runtime SET writer_mode='OFF',fanout_mode='OFF',send_mode='OFF',epoch=epoch+1")
            self._put('production:controller',dict(token=self.token,identity=self.identity,generation=self.generation,pid=os.getpid(),state='HELD'))
        self.state='SHUTDOWN_HOLD'

    def stop(self):
        if self.closed:return
        if self._physical_cancel is not None:self._physical_cancel.set()
        try:self.hold()
        finally:
            if self.pipeline:self.pipeline.closed=True
            self.closed=True
            while not self._accounts.empty():
                try:
                    request=self._accounts.get_nowait();request['result']=MailOutcome('BUSY');request['ready'].set()
                except queue.Empty:break
            with _registry_lock:
                if _registry.get(str(self.authority.path)) is self:_registry.pop(str(self.authority.path))
            if self._physical_thread is None or self._physical_done.is_set():self.lock.close()


class ProductionAccountGateway:
    """Account mail is held unless explicitly within the same authorized pilot.

    API dispatch receives an honest BUSY outcome in no-send mode, so no token is
    falsely reported as delivered and no legacy SMTP fallback is possible.
    """
    def __init__(self,runtime):
        self.runtime=runtime
        self.repository=SimpleNamespace(now=runtime.clock)
        self.action_base=runtime.action_base
    def send_account(self,destination,subject,html,expires_ms):
        runtime=self.runtime;pilot=runtime.pilot
        if (pilot is None or 'ACCOUNT' not in pilot.message_classes or not runtime.started or runtime.closed
                or runtime.state!='PILOT' or pilot.expires_ms<=runtime.clock()
                or destination not in pilot.recipients):return MailOutcome('BUSY')
        mailbox(destination)
        if type(expires_ms) is not int or expires_ms<=runtime.clock():return MailOutcome('EXPIRED')
        request=dict(destination=destination,subject=subject,html=html,expires=expires_ms,
            deadline=time.monotonic()+35,ready=threading.Event(),lock=threading.Lock(),cancelled=False,
            invocation=None,result=None)
        try:runtime._accounts.put_nowait(request)
        except queue.Full:return MailOutcome('BUSY')
        if request['ready'].wait(35):return request['result']
        with request['lock']:
            request['cancelled']=True
            return MailOutcome('UNKNOWN',request['invocation'],False) if request['invocation'] else MailOutcome('BUSY')
