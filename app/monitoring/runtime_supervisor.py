"""Explicit guard-confined runtime capabilities. Importing starts nothing."""
from dataclasses import asdict, dataclass
from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import queue
import secrets
import threading
import time
import sqlite3

from app.database.key_provider import KeyUnavailable, PayloadContext, KeyGrant
from app.database.protected_payloads import ProtectedPayloads, namespace
from app.notifications.transport_arbiter import guard, get_state, RuntimeController


class _Custody:
    """One bounded in-process custody lane; timeout poisons it, never retries."""
    METHODS=('schema_required','database_state','resolve_migration','check_database',
             'begin_database','finish_database','allocate','expected','unwrap',
             'prepare_erasure','erasure','revoke','rewrap','retire_generation')

    def __init__(self, provider, timeout):
        self.methods={name:getattr(provider,name) for name in self.METHODS}
        self.timeout=timeout
        self.tasks=queue.Queue(maxsize=1)
        self.lock=threading.Lock()
        self.failed=False
        self.closed=False
        self.thread=threading.Thread(target=self._run,daemon=True,name='fictional-custody')
        self.thread.start()

    def _run(self):
        while True:
            task=self.tasks.get()
            if task is None:return
            method,args,kwargs,reply=task
            try:reply.put((True,self.methods[method](*args,**kwargs)))
            except BaseException:reply.put((False,None))

    def call(self, method, *args, **kwargs):
        if not self.lock.acquire(timeout=self.timeout):raise KeyUnavailable('fictional custody lane occupied')
        try:
            if self.failed or self.closed or method not in self.methods:
                raise KeyUnavailable('fictional custody unavailable')
            reply=queue.Queue(maxsize=1)
            self.tasks.put_nowait((method,args,kwargs,reply))
            try:ok,result=reply.get(timeout=self.timeout)
            except queue.Empty:
                self.failed=True
                raise KeyUnavailable('fictional custody outcome unresolved') from None
            if not ok:raise KeyUnavailable('fictional custody operation unavailable')
            return result
        finally:self.lock.release()

    def __getattr__(self,name):
        if name not in self.METHODS:raise AttributeError(name)
        return lambda *args,**kwargs:self.call(name,*args,**kwargs)

    def close(self):
        self.closed=True
        try:self.tasks.put_nowait(None)
        except queue.Full:pass
        self.thread.join(timeout=.1)


class ChildKeyRelay:
    """Parent-owned custody; exact read/checkpoint RPC, never administrative RPC.

    Calls are serial with bounded waits. Children cannot allocate, erase, rotate,
    enumerate custody, reconcile migration, or select a provider method dynamically.
    """
    RPC='protected-key-v1'
    CAP_FIELDS={'token','child','role','namespace','provider','custody_generation',
                'controller_generation','epoch','expires_ms','authority'}

    def __init__(self,repository,codec,controller,*,provider_identity,custody_generation,
                 lifetime_ms=60000,timeout=.5,controller_host=None):
        g=guard()
        if g.child_role is not None:raise KeyUnavailable('parent custody required')
        self.controller_host=controller_host
        self.host_pending=controller_host is not None
        if self.host_pending:
            if (controller is not None or not isinstance(controller_host,g.GuardedChild)
                or controller_host.role!='controller-host-crash-test' or controller_host.closed
                or g._child_processes.get(controller_host.nonce) is not controller_host
                or controller_host.process.poll() is not None):
                raise KeyUnavailable('exact owned controller host required')
            self.host_previous=get_state(repository,'r2a:controller')
            if self.host_previous and self.host_previous['lease_ms']>repository.now():
                raise KeyUnavailable('existing controller must expire or shut down')
            self.host_epoch=repository._runtime()['epoch']
            controller=RuntimeController(repository,secrets.token_hex(32),
                self.host_previous['generation']+1 if self.host_previous else 1)
        if (not isinstance(codec,ProtectedPayloads) or not isinstance(controller,RuntimeController)
            or controller.repository is not repository or codec.namespace!=namespace(repository._database_path)
            or not isinstance(provider_identity,str) or not 1<=len(provider_identity)<=64
            or type(custody_generation) is not int or custody_generation<1
            or type(lifetime_ms) is not int or not 100<=lifetime_ms<=60000
            or type(timeout) not in (int,float) or not .01<=timeout<=1):
            raise KeyUnavailable('explicit bounded custody capability required')
        if not self.host_pending:controller._owned()
        self.repo,self.codec,self.controller=repository,codec,controller
        self.provider_identity,self.custody_generation=provider_identity,custody_generation
        self.lifetime_ms,self.timeout=lifetime_ms,timeout
        self.original_provider=codec.provider
        self.custody=_Custody(codec.provider,timeout)
        codec.provider=self.custody
        self.sessions={}
        self.closed=False
        self.rpc_count=0
        self.authority_child=None
        self.pending_session=None

    def issue(self,child,*,_resume=False):
        g=guard()
        if self.closed or self.custody.failed or not isinstance(child,g.GuardedChild) or child.closed:
            raise KeyUnavailable('live guarded child required')
        if self.host_pending:
            if child is not self.controller_host:raise KeyUnavailable('host election must finish first')
        else:self.controller._owned()
        owner=get_state(self.repo,'r2a:authority:account-1')
        if child is self.controller_host:
            if child.process.poll() is not None:raise KeyUnavailable('controller host exited')
        elif child.role=='authority':
            gate=self.repo._one("SELECT state FROM smtp_account_gate WHERE account_key='account-1'")
            if _resume:
                if (g._child_processes.get(child.nonce) is not child or child.process.poll() is not None
                    or not owner or owner['identity']!=child.ready['identity'] or owner['pid']!=child.ready['pid']):
                    raise KeyUnavailable('exact surviving authority required')
            elif gate['state']!='FREE':raise KeyUnavailable('physical END required before authority replacement')
            for session in self.sessions.values():
                if session['child'] is not self.controller_host:session['dead']=True
            self.authority_child=child.ready['identity']
        elif child.role!='client' or not owner:raise KeyUnavailable('registered physical authority required')
        cap=dict(token=secrets.token_hex(32),child=child.ready['identity'],role=child.role,
                 namespace=self.codec.namespace,provider=self.provider_identity,
                 custody_generation=self.custody_generation,controller_generation=self.controller.generation,
                 epoch=self.repo._runtime()['epoch'],expires_ms=self.repo.now()+self.lifetime_ms,
                 authority=child.ready['identity'] if child.role in ('authority','controller-host-crash-test') else owner['identity'])
        self.sessions[child.ready['identity']]=dict(child=child,cap=cap,counter=0,dead=False,deadline=time.monotonic()+self.lifetime_ms/1000)
        return dict(cap)

    def _rpc(self,child,operation,data):
        # API requests may run on another thread. Never borrow the controller's
        # SQLite connection there; this is a short, configured read-only view.
        from app.database.notification_migrator import connect_disposable
        with connect_disposable(self.codec.namespace) as connection:
            return self._rpc_on(child,operation,data,connection)

    def _rpc_on(self,child,operation,data,connection):
        try:
            if operation!=self.RPC or type(data) is not dict or set(data)!={'capability','counter','operation','arguments'}:
                raise KeyUnavailable('key request rejected')
            if len(json.dumps(data).encode())>8192:raise KeyUnavailable('key request rejected')
            session=self.sessions.get(child.ready['identity'])
            if (self.closed or self.custody.failed or not session or session['dead'] or session['child'] is not child
                or data['capability']!=session['cap'] or session['cap']['role']!=child.role
                or type(data['counter']) is not int or data['counter']!=session['counter']+1
                or data['counter']>8192 or time.monotonic()>=session['deadline']):raise KeyUnavailable('key session rejected')
            cap=session['cap']
            def state(key):
                row=connection.execute('SELECT value FROM system_state WHERE key=?',(key,)).fetchone()
                return json.loads(row[0]) if row else None
            elected=state('r2a:controller')
            host=child is self.controller_host
            epoch=connection.execute('SELECT epoch FROM notification_runtime').fetchone()[0]
            host_finish=(host and data['operation']=='finish_database' and self.pending_session==child.ready['identity'])
            if host and (child.process.poll() is not None or guard()._child_processes.get(child.nonce) is not child):
                raise KeyUnavailable('controller host unavailable')
            if self.host_pending:
                new_owner=(elected and elected['generation']==self.controller.generation
                    and elected['lease_ms']==self.repo.now()+100
                    and (not self.host_previous or elected['token']!=self.host_previous['token'])
                    and epoch==self.host_epoch+1 and host_finish
                    and all(connection.execute('SELECT '+k+' FROM notification_runtime').fetchone()[0]=='OFF'
                            for k in ('writer_mode','fanout_mode','send_mode')))
                if not host or not (elected==self.host_previous and epoch==self.host_epoch or new_owner):
                    raise KeyUnavailable('host election scope rejected')
            elif (not elected or elected['token']!=self.controller.token or elected['generation']!=self.controller.generation
                  or elected['lease_ms']<self.repo.now() or (elected['lease_ms']==self.repo.now() and not host_finish)):
                raise KeyUnavailable('stale controller')
            owner=state('r2a:authority:account-1')
            authority_ok=(True if host else self.authority_child==child.ready['identity'] if child.role=='authority'
                          else bool(owner and owner['identity']==cap['authority'] and self.authority_child==cap['authority']))
            if (cap['namespace']!=namespace(self.repo._database_path) or cap['provider']!=self.provider_identity
                or cap['custody_generation']!=self.custody_generation
                or cap['controller_generation']!=self.controller.generation
                or not (cap['epoch']==epoch or host_finish and cap['epoch']+1==epoch) or cap['expires_ms']<=self.repo.now()
                or not authority_ok or connection.in_transaction):raise KeyUnavailable('key session rejected')
            # Counter is consumed even on a failed operation: ambiguous requests
            # cannot be retried as if no custody operation happened.
            session['counter']=data['counter']; self.rpc_count+=1
            op,args=data['operation'],data['arguments']
            if type(args) is not dict:raise KeyUnavailable('key arguments rejected')
            if op=='schema_required' and set(args)=={'namespace'} and args['namespace']==self.codec.namespace:
                result=self.custody.schema_required(self.codec.namespace)
            elif op in ('check_database','begin_database','finish_database'):
                fields={'namespace','fingerprint'}|({'schema'} if op=='finish_database' else set())
                if set(args)!=fields or args['namespace']!=self.codec.namespace or args['fingerprint']!=self.codec.fingerprint(connection):
                    raise KeyUnavailable('checkpoint identity rejected')
                if op=='check_database':self.custody.check_database(args['namespace'],args['fingerprint'])
                elif op=='begin_database':
                    if self.pending_session is not None:raise KeyUnavailable('checkpoint unit already pending')
                    self.custody.begin_database(args['namespace'],args['fingerprint'])
                    self.pending_session=child.ready['identity']
                else:
                    if args['schema'] is not True or self.pending_session!=child.ready['identity']:
                        raise KeyUnavailable('owned installed checkpoint required')
                    self.custody.finish_database(args['namespace'],args['fingerprint'],True)
                    self.pending_session=None
                result=None
            elif op in ('expected','unwrap'):
                fields={'reference'}|({'context'} if op=='unwrap' else set())
                if set(args)!=fields or not isinstance(args['reference'],str) or not 1<=len(args['reference'])<=128:
                    raise KeyUnavailable('key reference rejected')
                ref=args['reference']
                rows=connection.execute('SELECT p.payload_id,t.purpose FROM protected_payloads p JOIN payload_tombstones t USING(payload_id) WHERE p.key_reference=?',(ref,)).fetchall()
                # No arbitrary custody enumeration or orphan/recipient key grants.
                if len(rows)!=1 or rows[0][1] not in ('SOURCE','SNAPSHOT'):
                    raise KeyUnavailable('delivery-read protected reference required')
                context=self.custody.expected(ref)
                self.codec._validate(context)
                if context.payload_id!=rows[0][0] or context.purpose!=rows[0][1]:
                    raise KeyUnavailable('protected reference identity rejected')
                self.codec._check_owner(connection,context)
                if op=='expected':result=context.as_dict()
                else:
                    if args['context']!=context.as_dict():raise KeyUnavailable('protected scope rejected')
                    grant=self.custody.unwrap(ref,context)
                    # Custody-session generation is independent of each record's
                    # KEK wrapping generation; partial external rewrap is valid.
                    if type(grant.generation) is not int or grant.generation<1 or grant.reference!=ref:
                        raise KeyUnavailable('key wrapping generation rejected')
                    ProtectedPayloads._key(grant)
                    result=dict(reference=grant.reference,key=grant.key.hex(),generation=grant.generation)
            else:raise KeyUnavailable('provider operation not authorized')
            return dict(counter=data['counter'],result=result)
        except Exception:
            raise KeyUnavailable('guarded key capability unavailable') from None

    def request(self,child,operation,data=None,*,transport=None,timeout=5):
        if type(timeout) not in (int,float) or not 0<timeout<=10:
            raise KeyUnavailable('finite child request wait required')
        deadline=time.monotonic()+timeout
        remaining=512
        def dispatch(op,args):
            nonlocal remaining
            remaining-=1
            if remaining<0 or time.monotonic()>=deadline:
                session=self.sessions.get(child.ready['identity'])
                if session:session['dead']=True
                raise KeyUnavailable('bounded child request exhausted')
            if op==self.RPC:return self._rpc(child,op,args)
            session=self.sessions.get(child.ready['identity'])
            if (op not in ('admit','consume') or child.role!='client' or transport is None
                or transport.role!='authority' or not session or session['dead']
                or session['cap']['authority']!=transport.ready['identity']
                or session['cap']['expires_ms']<=self.repo.now() or time.monotonic()>=session['deadline']):
                raise KeyUnavailable('client transport operation not authorized')
            from app.database.notification_migrator import connect_disposable
            with connect_disposable(self.codec.namespace) as connection:
                row=connection.execute("SELECT value FROM system_state WHERE key='r2a:controller'").fetchone()
                owner=json.loads(row[0]) if row else None
                epoch=connection.execute('SELECT epoch FROM notification_runtime').fetchone()[0]
                if (not owner or owner['token']!=self.controller.token
                    or owner['generation']!=session['cap']['controller_generation']
                    or owner['lease_ms']<=self.repo.now() or epoch!=session['cap']['epoch']):
                    raise KeyUnavailable('stale client transport session')
            return self.request(transport,op,args,timeout=max(.001,deadline-time.monotonic()))
        # Bound waiting for the frozen bootstrap's existing lock without editing
        # that protocol. It is reentrant for this same-thread request call.
        if not child._request_lock.acquire(timeout=timeout):raise KeyUnavailable('child request lane occupied')
        try:
            return child.request(operation,data,relay=dispatch,timeout=max(.001,deadline-time.monotonic()))
        finally:child._request_lock.release()

    def open(self,child):
        cap=self.issue(child)
        data=dict(path=self.codec.namespace,now=self.repo.now(),key_capability=cap)
        if child.role=='client':
            authority=get_state(self.repo,'r2a:authority:account-1')
            if not authority:raise KeyUnavailable('registered physical authority required')
            data['authority']=authority['identity']
        try:return self.request(child,'open',data)
        except BaseException:
            self.sessions.pop(child.ready['identity'],None)
            raise

    def accept_controller_host(self,child):
        if not self.host_pending or child is not self.controller_host or child.process.poll() is not None:
            raise KeyUnavailable('pending live host required')
        observed=self.request(child,'controller_status',{})
        owner=get_state(self.repo,'r2a:controller')
        if (not owner or owner['generation']!=self.controller.generation
            or observed!=dict(pid=child.ready['pid'],generation=owner['generation'],lease_ms=owner['lease_ms'])
            or owner['lease_ms']!=self.repo.now()+100 or self.repo._runtime()['epoch']!=self.host_epoch+1
            or self.pending_session is not None):raise KeyUnavailable('committed host election required')
        self.controller=RuntimeController(self.repo,owner['token'],owner['generation'])
        self.host_pending=False
        self.refresh(child)
        return self.controller

    def resume_authority(self,child):
        """Rebind the same proven live physical owner; never replace it or assert END."""
        if self.host_pending or child.role!='authority':raise KeyUnavailable('host election and physical authority required')
        cap=self.issue(child,_resume=True)
        return self.request(child,'advance',dict(now=self.repo.now(),key_capability=cap))

    def refresh(self,child):
        # Refresh the same elected authority without replacing live ownership.
        session=self.sessions.get(child.ready['identity'])
        if not session or session['dead']:raise KeyUnavailable('live child session required')
        self.controller._owned()
        cap=dict(session['cap'],token=secrets.token_hex(32),epoch=self.repo._runtime()['epoch'],expires_ms=self.repo.now()+self.lifetime_ms)
        session.update(cap=cap,counter=0,deadline=time.monotonic()+self.lifetime_ms/1000)
        return self.request(child,'advance',dict(now=self.repo.now(),key_capability=cap))

    def close_child(self,child):
        try:
            if not child.closed and child.process.poll() is None:self.request(child,'shutdown',{},timeout=2)
        finally:
            self.sessions.pop(child.ready['identity'],None)

    def close(self):
        if self.closed:return
        self.closed=True
        self.sessions.clear()
        failed=self.custody.failed
        self.custody.close()
        # A timed-out call may still complete. Never restore a second custody
        # access lane while that unresolved operation could mutate its journal.
        if not failed and not self.custody.thread.is_alive():self.codec.provider=self.original_provider


_runtimes=ContextVar('explicit_fictional_runtime',default=())


def current_runtime(path=None):
    """Metadata lookup only; never opens a DB or starts a service."""
    if path is None:
        return _runtimes.get()[-1][1] if _runtimes.get() else None
    target=str(Path(path).resolve())
    return next((runtime for name,runtime in _runtimes.get() if name==target),None)


@dataclass(frozen=True)
class LegacyExclusion:
    writer: bool=False
    subscriber: bool=False
    admin: bool=False
    account: bool=False

    def validate(self):
        if any(getattr(self,k) is not True for k in ('writer','subscriber','admin','account')):
            raise KeyUnavailable('explicit fictional legacy exclusion required')


class RuntimeSupervisor:
    """Injected local lifecycle only. No scheduler, process creation or migration."""
    def __init__(self,*,connection=None,database=None,codec=None,source=None,authority=None,
                 clock=None,exclusion=None,config=None,listing_contract=None):
        from app.config import RuntimeConfig
        self.config=config if config is not None else RuntimeConfig()
        if not isinstance(self.config,RuntimeConfig):raise ValueError('explicit runtime configuration required')
        self.c,self.database,self.codec,self.source=connection,database,codec,source
        self.authority,self.clock,self.exclusion=authority,clock,exclusion
        if listing_contract is not None:
            from app.tpo.listing import ListingContract
            if not isinstance(listing_contract,ListingContract):raise ValueError('typed listing contract required')
        self.listing_contract=listing_contract
        self.last_poll_ok=None
        self.last_poll_ms=None
        self.last_drain_ms=None
        self.started=False
        self.closed=False
        self.controller=self.relay=self.pipeline=self.watcher=self._scope=None
        self._runtime_token=None
        self.state='NEW'
        self.account_requests={}
        self.account_lock=threading.RLock()

    def start(self):
        from app.config import RuntimeMode
        from app.database.notification_migrator import require_connection,verify_schema
        from app.database.protected_payloads import binding,protected_scope
        from app.database.event_repository import EventRepository,Fenced
        from app.monitoring.notification_pipeline import NotificationPipeline,V2Options
        from app.monitoring.watcher import PlacementWatcher
        from app.notifications.transport_arbiter import AuthorityGateway
        if self.closed:raise KeyUnavailable('stopped supervisor cannot restart; construct a fresh owner')
        if self.started:return self
        if self.config.mode is RuntimeMode.LEGACY:
            self.state='LEGACY';self.started=True;return self
        g=guard()
        if g.child_role is not None:raise KeyUnavailable('parent supervisor required')
        if not isinstance(self.c,sqlite3.Connection):raise KeyUnavailable('explicit disposable connection required')
        require_connection(self.c)
        try:verify_schema(self.c)
        except sqlite3.Error:
            from app.database.notification_migrator import SchemaError
            raise SchemaError('complete compatible preinstalled schema required') from None
        if (not isinstance(self.codec,ProtectedPayloads) or not callable(self.clock)
            or self.database is None or namespace(self.database.db_path)!=self.codec.namespace
            or namespace(self.c.execute('PRAGMA database_list').fetchone()[2])!=self.codec.namespace
            or not isinstance(self.exclusion,LegacyExclusion)):
            raise KeyUnavailable('complete explicit disposable capabilities required')
        self.exclusion.validate()
        if current_runtime(self.codec.namespace) is not None:raise Fenced('runtime already bound')
        if binding(self.c) is None:
            self._scope=protected_scope(self.codec.namespace,self.codec);self._scope.__enter__()
        elif binding(self.c) is not self.codec:raise KeyUnavailable('foreign protected scope')
        try:
            self.codec.warm(self.c)
            self.repo=EventRepository(self.c,clock=self.clock,record_mode=True,info_ttl_ms=self.config.info_ttl_ms)
            if self.listing_contract is None:self.listing_contract=self.repo._listing_policy()
            if self.listing_contract is not None:
                if self.repo._one("""SELECT 1 FROM protected_payloads p JOIN payload_tombstones t
                    USING(payload_id) WHERE t.purpose='APPROVAL' LIMIT 1"""):
                    raise KeyUnavailable('restricted pilot excludes existing administrative approval payloads')
                # Bind before opening the child so every repository, including
                # physical consumption, reads the same durable source policy.
                configured=NotificationPipeline(self.repo,options=V2Options(),role='client')
                configured.bind_listing_contract(self.listing_contract)
            if self.config.mode in (RuntimeMode.OFF,RuntimeMode.SHUTDOWN_HOLD):
                if any(self.repo._runtime()[k]!='OFF' for k in ('writer_mode','fanout_mode','send_mode')):
                    raise KeyUnavailable('OFF attachment cannot take over active runtime')
                self._runtime_token=_runtimes.set((*_runtimes.get(),(self.codec.namespace,self)))
                self.state=self.config.mode.value;self.started=True;return self
            if (not isinstance(self.authority,g.GuardedChild) or self.authority.role!='authority' or self.authority.closed
                or self.authority.process.poll() is not None or self.config.controller_identity is None or self.config.generation is None
                or self.config.provider_identity is None or self.config.custody_generation is None
                or (self.config.mode is RuntimeMode.V2_FAKE and getattr(self.source,'synthetic',False) is not True)):
                raise KeyUnavailable('explicit source, authority and controller capabilities required')
            old=get_state(self.repo,'r2a:controller')
            expected=old['generation']+1 if old else 1
            if self.config.generation!=expected:raise Fenced('requested runtime generation is stale')
            self.controller=RuntimeController.acquire(self.repo)
            self.relay=ChildKeyRelay(self.repo,self.codec,self.controller,
                provider_identity=self.config.provider_identity,custody_generation=self.config.custody_generation)
            self.relay.open(self.authority)
            identity=self.relay.request(self.authority,'elect',{})['authority']
            self.gateway=AuthorityGateway(self.repo,identity,lambda op,data:self.relay.request(self.authority,op,data))
            options=V2Options(writer=self.config.mode is RuntimeMode.V2_FAKE,fanout=True,drain=True,transport=True,
                              info_ttl_ms=self.config.info_ttl_ms,idle_wait_ms=self.config.idle_wait_ms)
            self.controller.enable(options)
            self.relay.refresh(self.authority)
            self.pipeline=NotificationPipeline(self.repo,transport=self.gateway,options=options,role='client')
            self.pipeline.listing_contract=self.listing_contract
            self.watcher=PlacementWatcher(db=self.database,notification_pipeline=self.pipeline,
                source_client=self.source if self.source is not None else _NoSource())
            self.pipeline.runtime_owner=self
            self._runtime_token=_runtimes.set((*_runtimes.get(),(self.codec.namespace,self)))
            self.started=True;self.state=self.config.mode.value
            return self
        except BaseException as failure:
            try:self.stop()
            except BaseException as cleanup_failure:raise failure from cleanup_failure
            raise

    def require_running(self,operation):
        from app.config import RuntimeMode
        if not self.started or self.closed or self.state not in ('V2_FAKE','DRAIN_ONLY_FAKE'):
            raise KeyUnavailable('guarded runtime is held')
        from app.database.notification_migrator import connect_disposable
        with connect_disposable(self.codec.namespace) as connection:
            row=connection.execute("SELECT value FROM system_state WHERE key='r2a:controller'").fetchone()
            elected=json.loads(row[0]) if row else None
            if (not elected or elected['token']!=self.controller.token or elected['generation']!=self.controller.generation
                or elected['lease_ms']<=self.clock()):raise KeyUnavailable('stale runtime controller')
        if self.relay.closed or self.relay.custody.failed:raise KeyUnavailable('runtime custody unavailable')
        if operation=='writer' and self.config.mode is not RuntimeMode.V2_FAKE:raise KeyUnavailable('source writer is OFF')

    async def poll_once(self):
        self.require_running('writer')
        if self.listing_contract is not None:self.relay.refresh(self.authority)
        self.last_poll_ok=await self.watcher.check_once()
        self.last_poll_ms=self.clock()
        return self.last_poll_ok

    def account_gateway(self):
        self.require_running('account')
        return _RuntimeAccountGateway(self)

    async def drain_once(self):
        self.require_running('drain')
        if self.listing_contract is not None:self.relay.refresh(self.authority)
        result=await self.watcher.delivery_worker.process_batch_once()
        self.last_drain_ms=self.clock()
        return result

    def configure(self,options):
        """Explicit elected-owner switches; no environment activation."""
        from app.monitoring.notification_pipeline import V2Options
        self.require_running('drain')
        if not isinstance(options,V2Options):raise ValueError('typed switches required')
        if options.fanout and options.info_ttl_ms is None:raise ValueError('explicit INFO lifetime required')
        self.controller.enable(options)
        self.pipeline.options=options
        self.repo.info_ttl_ms=options.info_ttl_ms
        self.relay.refresh(self.authority)

    def diagnostics(self):
        authority_alive=bool(self.authority and not self.authority.closed and self.authority.process.poll() is None)
        try:
            self.require_running('drain')
            available=authority_alive
        except Exception:available=False
        return dict(state=self.state,started=self.started,closed=self.closed,
            runtime_available=available,
            authority_alive=authority_alive,
            production_enabled=False,source_contract_bound=self.listing_contract is not None,
            last_poll_ok=self.last_poll_ok,last_poll_ms=self.last_poll_ms,last_drain_ms=self.last_drain_ms,
            custody_available=bool(self.relay and not self.relay.closed and not self.relay.custody.failed))

    async def run(self,stop,*,poll_interval_ms=60000):
        import asyncio
        if type(poll_interval_ms) is not int or not 10<=poll_interval_ms<=3600000:
            raise ValueError('bounded poll interval required')
        self.start()
        if self.listing_contract is None:
            self.stop()
            raise KeyUnavailable('operational loop requires an installed explicit listing contract')
        async def poll_loop():
            while not stop.is_set():
                if self.pipeline.options.writer:await self.poll_once()
                await asyncio.sleep(poll_interval_ms/1000)
        tasks=[asyncio.create_task(poll_loop()),asyncio.create_task(self.drain_loop(stop)),
               asyncio.create_task(stop.wait())]
        try:
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.stop()

    async def drain_loop(self,stop,*,wait=None):
        import asyncio
        wait=wait or asyncio.sleep
        while not stop.is_set():
            await self.drain_once()
            await wait(self.pipeline.next_wait_ms()/1000)

    def hold(self):
        from app.monitoring.notification_pipeline import V2Options
        if self.state=='SHUTDOWN_HOLD':return
        if self.controller:
            self.controller.enable(V2Options())
            self.state='SHUTDOWN_HOLD'
            if self.relay and not self.relay.custody.failed:self.relay.refresh(self.authority)

    def stop(self):
        if self.closed:return
        try:
            if self.controller:
                try:
                    self.hold()
                    if self.pipeline:self.pipeline.closed=True;self.pipeline.recover_once()
                finally:
                    # A failed open/refresh must not skip owned-controller release.
                    # Shutdown changes modes/lease only; it never asserts physical END.
                    try:
                        if self.relay:self.relay.close_child(self.authority)
                    finally:self.controller.shutdown()
        finally:
            self.closed=True;self.state='SHUTDOWN_HOLD'
            if self.relay:self.relay.close()
            if self._runtime_token is not None:_runtimes.reset(self._runtime_token);self._runtime_token=None
            if self._scope:self._scope.__exit__(None,None,None);self._scope=None


class _NoSource:
    synthetic=True
    async def fetch_companies(self):raise KeyUnavailable('drain-only source is unavailable')


from app.notifications.transport_arbiter import AuthorityGateway


class _RuntimeAccountGateway(AuthorityGateway):
    """A thread-owned repository per API call; producer exit never changes modes."""
    def __init__(self,runtime):
        self.runtime=runtime
        self.repository=_ClockView(runtime.clock)
        self.action_base='https://watcher.example.invalid'

    def send_account(self,destination,subject,html,expires_ms):
        from app.database.notification_migrator import connect_disposable
        from app.database.event_repository import EventRepository
        from app.database.protected_payloads import binding
        runtime=self.runtime
        runtime.require_running('account')
        with runtime.account_lock, connect_disposable(runtime.codec.namespace) as connection:
            if binding(connection) is not runtime.codec:raise KeyUnavailable('explicit API key scope required')
            repo=EventRepository(connection,clock=runtime.clock,record_mode=True)
            gateway=AuthorityGateway(repo,runtime.gateway.authority,
                lambda op,data:runtime.relay.request(runtime.authority,op,data))
            gateway.requests=runtime.account_requests
            return gateway.send_account(destination,subject,html,expires_ms)


class _ClockView:
    def __init__(self,clock):self.clock=clock
    def now(self):return self.clock()
