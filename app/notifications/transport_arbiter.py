"""R2a guard-confined fake physical authority. No SMTP or startup side effects.

Minimal controller/admission/consumption identities use existing system_state;
the S1 physical schema, invocation journal and END gate constraints are unchanged.
"""
from dataclasses import asdict
from collections import deque
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading

from app.database.event_repository import EventRepository,FakeTransport,Handle,Fenced,PolicyDenied,CommitUnknown
from app.database.notification_migrator import atomic,connect_disposable,verify_schema
from app.notifications.transport_protocol import InvocationIdentity,EphemeralMail,MailOutcome,integer,opaque,wire


def guard():
    g=sys.modules.get("isolation_bootstrap")
    if g is None or not g._installed:raise PolicyDenied("installed fictional isolation required")
    return g


def get_state(repo,key):
    row=repo._one("SELECT value FROM system_state WHERE key=?",(key,))
    return json.loads(row["value"]) if row else None


def put_state(repo,key,value):
    repo.c.execute("INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                   (key,json.dumps(value,sort_keys=True,separators=(",",":")),str(repo.now())))


class RuntimeController:
    """Only the explicit guarded parent controller can mutate shared modes."""
    _legacy={}
    def __init__(self,repo,token,generation):
        self.repository,self.token,self.generation=repo,token,generation

    @classmethod
    def acquire(cls,repo,*,lease_ms=86400000,reuse=False):
        if guard().child_role not in (None,'controller-host-crash-test'):raise PolicyDenied("child/client cannot elect controller")
        if type(lease_ms) is not int or not 1<=lease_ms<=86400000:raise ValueError("bounded controller lease required")
        path=repo.c.execute("PRAGMA database_list").fetchone()[2]
        with atomic(repo.c):
            old=get_state(repo,"r2a:controller")
            cached=cls._legacy.get(path)
            if reuse and old and cached==(old["token"],old["generation"]) and old["lease_ms"]>repo.now():
                return cls(repo,*cached)
            if old and old["lease_ms"]>repo.now():raise Fenced("live controller already elected")
            if old is None and repo._one("SELECT 1 FROM smtp_account_gate WHERE state<>'FREE' LIMIT 1"):
                raise Fenced("legacy account permission must positively retire before shared enrollment")
            token=secrets.token_hex(32)
            generation=(old["generation"]+1) if old else 1
            put_state(repo,"r2a:controller",dict(token=token,generation=generation,lease_ms=repo.now()+lease_ms))
            repo.c.execute("UPDATE notification_runtime SET writer_mode='OFF',fanout_mode='OFF',send_mode='OFF',epoch=epoch+1")
        cls._legacy[path]=(token,generation)
        return cls(repo,token,generation)

    def _owned(self):
        if guard().child_role not in (None,'controller-host-crash-test'):raise PolicyDenied("attached child cannot control runtime")
        old=get_state(self.repository,"r2a:controller")
        if not old or old["token"]!=self.token or old["generation"]!=self.generation or old["lease_ms"]<=self.repository.now():
            raise Fenced("stale controller")
        return old

    def enable(self,options):
        from app.monitoring.notification_pipeline import V2Options
        if not isinstance(options,V2Options):raise ValueError("explicit fictional options required")
        with atomic(self.repository.c):
            self._owned()
            desired=("V2" if options.writer else "OFF","V2" if options.fanout else "OFF",
                     "V2" if options.drain and options.transport else "OFF")
            row=self.repository._runtime()
            changed=desired!=(row["writer_mode"],row["fanout_mode"],row["send_mode"])
            self.repository.c.execute("UPDATE notification_runtime SET writer_mode=?,fanout_mode=?,send_mode=?,epoch=epoch+?",(*desired,int(changed)))

    def shutdown(self):
        with atomic(self.repository.c):
            old=self._owned()
            self.repository.c.execute("UPDATE notification_runtime SET writer_mode='OFF',fanout_mode='OFF',send_mode='OFF',epoch=epoch+1")
            put_state(self.repository,"r2a:controller",dict(old,lease_ms=self.repository.now()))


def identity_for(repo,invocation):
    v=repo._one("SELECT * FROM transport_invocations WHERE invocation_id=?",(invocation,))
    if not v:raise PolicyDenied("durable invocation required")
    return InvocationIdentity(v["account_key"],v["invocation_id"],v["submission_key"],v["owner_instance"],
        v["gate_generation"],v["owner_epoch"],v["delivery_id"],v["delivery_generation"])


def supervisor_end(repo,child,invocation):
    g=guard()
    if not isinstance(child,g.GuardedChild) or child.role!="authority":raise PolicyDenied("owned authority process required")
    proof=child.exit_evidence()
    identity=identity_for(repo,invocation)
    owner=get_state(repo,"r2a:authority:"+identity.account)
    if not owner or owner["identity"]!=proof["identity"] or owner["pid"]!=proof["pid"] or identity.authority!=proof["identity"]:
        raise PolicyDenied("process proof does not cover original invocation")
    # Bootstrap roles cannot spawn descendants; the fixed fake sink creates
    # threads only in this authenticated, positively exited Python process.
    repo.record_fact(invocation,"PROCESS_EXIT_CONFIRMED",fact_key="owned-python-exit",
                     evidence_reference="r2a:process-exit:"+proof["identity"])
    return proof


class _PhysicalFake:
    def __init__(self,repo,identity):
        if guard().child_role!="authority" or guard().child_identity!=identity:raise PolicyDenied("sole guarded physical authority required")
        self.repo,self.identity=repo,identity
        self.calls=deque(maxlen=32)
        self.total_calls=0
        self.active={}
        self.fidelity={}
        self.ledger=Path(repo.c.execute("PRAGMA database_list").fetchone()[2]).with_suffix(".fake-calls.jsonl")

    def _record(self,invocation,phase,message_class):
        # Test-owned metadata only; no destination, HTML or action token.
        with self.ledger.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(dict(invocation=invocation,authority=self.identity,pid=os.getpid(),phase=phase,message_class=message_class))+"\n")

    def invoke(self,identity,mail,*,kind,ended,fault):
        if self.active:raise PolicyDenied("physical fake account occupied")
        stop,started,done=threading.Event(),threading.Event(),threading.Event()
        def native_caller():
            self.calls.append(identity.invocation)
            self.total_calls+=1
            self._record(identity.invocation,"START",mail.message_class)
            started.set()
            if fault=="during_physical":os._exit(73)
            if kind=="ACCEPTED" or fault=="accept_then_error":self._record(identity.invocation,"ACCEPTED",mail.message_class)
            if fault=="after_acceptance":os._exit(74)
            if not ended:stop.wait(10)
            self._record(identity.invocation,"END",mail.message_class)
            done.set()
        thread=threading.Thread(target=native_caller,daemon=True)
        self.active[identity.invocation]=(thread,stop,done)
        self.fidelity[identity.invocation]=dict(message_class=mail.message_class,
            destination_hash=hashlib.sha256(mail.destination.encode()).hexdigest(),
            subject_hash=hashlib.sha256(mail.subject.encode()).hexdigest(),html_hash=hashlib.sha256(mail.html.encode()).hexdigest(),
            headers=list(mail.headers),expires_ms=mail.expires_ms)
        if len(self.fidelity)>32:self.fidelity.pop(next(iter(self.fidelity)))
        thread.start()
        if not started.wait(2):raise TimeoutError("fake caller start timeout")
        if ended:
            thread.join(2)
            if thread.is_alive():raise TimeoutError("fake caller END timeout")
            self.active.pop(identity.invocation)
        return "UNKNOWN" if fault=="accept_then_error" else kind

    def end(self,invocation):
        item=self.active.get(invocation)
        if item:
            thread,stop,done=item
            stop.set()
            thread.join(2)
            if thread.is_alive() or not done.is_set():raise PolicyDenied("caller END not proven")
            self.active.pop(invocation)
        return True


class FakeAuthority:
    def __init__(self,repo):
        if guard().child_role!="authority":raise PolicyDenied("authority role required")
        self.repo=repo
        self.identity=guard().child_identity
        self.sink=_PhysicalFake(repo,self.identity)
        self.grants={}
        self.waiting={k:[] for k in ("ADMIN","SUBSCRIBER","ACCOUNT")}
        self.cursor=0
        self.config=dict(kind="ACCEPTED",ended=True,fault=None)

    def elect(self):
        with atomic(self.repo.c):
            c=get_state(self.repo,"r2a:controller")
            if not c or c["lease_ms"]<=self.repo.now():raise Fenced("live controller required")
            old=get_state(self.repo,"r2a:authority:account-1")
            gate=self.repo._one("SELECT * FROM smtp_account_gate WHERE account_key='account-1'")
            if gate["state"]!="FREE" and (not old or old["identity"]!=self.identity):raise Fenced("physical account lacks END")
            if self.sink.active:raise Fenced("active caller cannot change authority/controller binding")
            put_state(self.repo,"r2a:authority:account-1",dict(identity=self.identity,pid=os.getpid(),controller_generation=c["generation"]))
        self.repo.transport_authority=self.identity
        return dict(authority=self.identity,pid=os.getpid())

    def _owned(self):
        self.repo.transport_authority=self.identity
        return self.repo.check_transport_authority()

    def slot(self,data):
        if set(data)!={"slot_request","message_class","expires_ms"}:raise ValueError("exact slot request required")
        request=opaque(data["slot_request"])
        expires=integer(data["expires_ms"],1)
        channel=data["message_class"]
        if channel not in self.waiting:raise ValueError("mail class required")
        now=self.repo.now()
        for key in self.waiting:self.waiting[key]=[x for x in self.waiting[key] if x[1]>now]
        if expires<=now:return dict(kind="EXPIRED")
        if expires>now+10000:raise ValueError("bounded ephemeral admission lifetime required")
        if not any(any(x[0]==request for x in entries) for entries in self.waiting.values()):
            if len(self.waiting[channel])>=2 or sum(map(len,self.waiting.values()))>=6:return dict(kind="BUSY")
            self.waiting[channel].append((request,expires))
        with atomic(self.repo.c):
            self._owned()
            gate=self.repo._one("SELECT * FROM smtp_account_gate WHERE account_key='account-1'")
            old=get_state(self.repo,"r2a:slot:account-1")
            if old and old["expires_ms"]>now and gate["generation"]==old["prior_generation"]:
                return dict(kind="GRANTED",slot=old["token"]) if old["request"]==request else dict(kind="BUSY")
            if gate["state"]!="FREE":return dict(kind="BUSY")
            classes=tuple(self.waiting)
            selected=next((classes[(self.cursor+i)%3] for i in range(3) if self.waiting[classes[(self.cursor+i)%3]]),None)
            if selected!=channel or self.waiting[channel][0][0]!=request:return dict(kind="BUSY")
            self.waiting[channel].pop(0)
            self.cursor=(classes.index(channel)+1)%3
            token=secrets.token_hex(32)
            put_state(self.repo,"r2a:slot:account-1",dict(token=token,request=request,message_class=channel,authority=self.identity,
                epoch=self.repo._runtime()["epoch"],expires_ms=min(expires,now+1000),prior_generation=gate["generation"]))
            return dict(kind="GRANTED",slot=token)

    def _validate(self,identity):
        if identity.authority!=self.identity or identity!=identity_for(self.repo,identity.invocation):raise Fenced("invocation identity mismatch")
        gate=self.repo._one("SELECT * FROM smtp_account_gate WHERE account_key=?",(identity.account,))
        if not gate or gate["state"] not in ("INFLIGHT","BLOCKED") or gate["invocation_id"]!=identity.invocation or gate["generation"]!=identity.gate_generation or gate["submission_key"]!=identity.submission:
            raise Fenced("exact active account invocation required")
        return gate

    def admit(self,data):
        if "slot_request" in data:return self.slot(data)
        if set(data)!={"identity","mail"}:raise ValueError("exact invocation admission required")
        identity=InvocationIdentity.from_wire(data["identity"])
        mail=EphemeralMail.from_wire(data["mail"])
        with atomic(self.repo.c):
            self._owned()
            gate=self._validate(identity)
            v=self.repo._one("SELECT message_class FROM transport_invocations WHERE invocation_id=?",(identity.invocation,))
            if v["message_class"]!=mail.message_class:raise Fenced("mail class does not match invocation")
            if identity.epoch!=self.repo._runtime()["epoch"] or gate["lease_ms"]<=self.repo.now() or mail.expires_ms<=self.repo.now():raise Fenced("expired admission")
            if get_state(self.repo,"r2a:admitted:"+identity.invocation) or get_state(self.repo,"r2a:consumed:"+identity.invocation):raise PolicyDenied("admission cannot be reissued")
            grant=secrets.token_hex(32)
            put_state(self.repo,"r2a:admitted:"+identity.invocation,dict(authority=self.identity,grant_hash=hashlib.sha256(grant.encode()).hexdigest()))
            self.grants[grant]=(identity,mail)
        if self.config["fault"]=="after_admission":os._exit(71)
        return dict(identity=identity.to_wire(),grant=grant)

    def consume(self,data):
        if set(data)!={"identity","grant"}:raise ValueError("exact grant required")
        identity=InvocationIdentity.from_wire(data["identity"])
        grant=opaque(data["grant"],64)
        item=self.grants.get(grant)
        if item is None or item[0]!=identity:raise PolicyDenied("single confirmed authority grant required")
        mail=item[1]
        with atomic(self.repo.c):
            self._owned()
            gate=self._validate(identity)
            admission=get_state(self.repo,"r2a:admitted:"+identity.invocation)
            if not admission or admission["authority"]!=self.identity or admission["grant_hash"]!=hashlib.sha256(grant.encode()).hexdigest():
                raise PolicyDenied("durable exact authority admission required")
            if identity.epoch!=self.repo._runtime()["epoch"] or gate["lease_ms"]<=self.repo.now() or mail.expires_ms<=self.repo.now():raise Fenced("expired physical permit")
            if self.repo._one("SELECT 1 FROM transport_facts WHERE invocation_id=? AND fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED','UNKNOWN')",(identity.invocation,)):
                raise PolicyDenied("retired/uncertain invocation cannot start")
            if identity.delivery_id is not None:
                d=self.repo._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(identity.delivery_id,))
                if d["status"]!="SENDING" or d["generation"]!=identity.attempt_generation or d["cancel_requested_ms"] is not None or not self.repo._authorized(self.repo._audience(d)):
                    raise Fenced("current delivery authorization rejected")
                if not self.repo._source_recent(self.repo._context(d["intent_id"])[1]):
                    raise Fenced("current source observation expired before physical consumption")
                if d["channel"]=="SUBSCRIBER":
                    current=self.repo._one("SELECT u.email FROM users u JOIN recipient_associations a ON a.user_id=u.id WHERE a.recipient_key=?",(d["recipient_key"],))
                    if not current or current["email"]!=mail.destination:raise Fenced("destination changed before physical consumption")
            self.repo.c.execute("INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)",
                ("r2a:consumed:"+identity.invocation,json.dumps(identity.to_wire(),sort_keys=True),str(self.repo.now())))
            self.grants.pop(grant)
        if self.config["fault"]=="after_consumption":os._exit(72)
        # Single authority owns the physical capability. Any ambiguous death
        # after durable consumption cannot reconstruct this volatile permit.
        kind=self.sink.invoke(identity,mail,**self.config)
        self.repo.record_fact(identity.invocation,kind,fact_key="authority-outcome",evidence_reference="r2a:authority:"+self.identity)
        if self.config["ended"]:
            self.repo.record_fact(identity.invocation,"ENDED",fact_key="authority-native-end",evidence_reference="r2a:thread-end:"+self.identity)
        if self.config["fault"]=="after_end":os._exit(75)
        return MailOutcome(kind,identity.invocation,self.config["ended"]).to_wire()

    def end(self,data):
        identity=InvocationIdentity.from_wire(data)
        if identity.authority!=self.identity or identity!=identity_for(self.repo,identity.invocation):raise Fenced("wrong END identity")
        if not get_state(self.repo,"r2a:admitted:"+identity.invocation):raise PolicyDenied("no authority evidence for invocation")
        self.sink.end(identity.invocation)
        for token,item in list(self.grants.items()):
            if item[0]==identity:self.grants.pop(token)
        self.repo.record_fact(identity.invocation,"ENDED",fact_key="authority-native-end",evidence_reference="r2a:thread-end:"+self.identity)
        return dict(ended=True)

    def status(self):
        return dict(authority=self.identity,pid=os.getpid(),physical_calls=self.sink.total_calls,active=len(self.sink.active),
            call_ids=list(self.sink.calls),waiting={k:len(v) for k,v in self.waiting.items()},gate=self.repo._one("SELECT state FROM smtp_account_gate WHERE account_key='account-1'")["state"],
            fidelity=list(self.sink.fidelity.values()))


class AuthorityGateway(FakeTransport):
    """Producer-side adapter has IPC only, never a physical fake or SMTP method."""
    def __init__(self,repository,authority,rpc,*,admin_destination="fictional-admin@example.invalid"):
        guard()
        self.repository,self.authority,self.rpc=repository,opaque(authority),rpc
        self.admin_destination=admin_destination
        self.action_base="https://watcher.example.invalid"
        self.requests={}
        self.last_mail=None
        repository.transport_authority=self.authority
        repository.check_transport_authority()

    def _rpc(self,operation,data):
        return self.rpc(operation,data)

    def prepare_slot(self,message_class,expires_ms=None):
        now=self.repository.now()
        if message_class in ("ADMIN","SUBSCRIBER") and not self.repository._one(
                "SELECT 1 FROM notification_deliveries_v2 WHERE channel=? AND status='PENDING' AND next_ms<=?",
                (message_class,now)):
            return False
        request=self.requests.setdefault(message_class,secrets.token_hex(16))
        result=self._rpc("admit",dict(slot_request=request,message_class=message_class,expires_ms=min(expires_ms or now+1000,now+1000)))
        if result["kind"]!="GRANTED":return False
        self.repository.transport_slot=result["slot"]
        self.requests.pop(message_class,None)
        return True

    def ticket(self,permit,mail):
        return dict(identity=identity_for(self.repository,permit.invocation_id).to_wire(),mail=mail.to_wire())

    def _invoke(self,identity,mail):
        grant=wire(self._rpc("admit",dict(identity=identity.to_wire(),mail=mail.to_wire())),("identity","grant"))
        if InvocationIdentity.from_wire(grant["identity"])!=identity:raise PolicyDenied("admission response identity mismatch")
        opaque(grant["grant"],64)
        result=MailOutcome.from_wire(self._rpc("consume",grant))
        if result.invocation!=identity.invocation:raise PolicyDenied("outcome response identity mismatch")
        return result

    def submit_message(self,invocation,message):
        from app.monitoring.notification_pipeline import TransportResult
        identity=identity_for(self.repository,invocation)
        d=self.repository._one("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(identity.delivery_id,))
        mapping=self.repository._one("SELECT * FROM recipient_associations WHERE recipient_key=?",(d["recipient_key"],))
        if not mapping:raise PolicyDenied("current destination mapping missing")
        if d["channel"]=="SUBSCRIBER":
            user=self.repository._one("SELECT email FROM users WHERE id=?",(mapping["user_id"],))
            destination=user["email"]
        else:destination=self.admin_destination
        mail=EphemeralMail(d["channel"],destination,message["subject"],message["html"],d["expires_ms"],message.get("headers",{}))
        self.last_mail=mail
        result=self._invoke(identity,mail)
        return TransportResult(result.kind,result.ended)

    def send_account(self,destination,subject,html,expires_ms):
        now=self.repository.now()
        mail=EphemeralMail("ACCOUNT",destination,subject,html,expires_ms,{})
        if expires_ms<=now:return MailOutcome("EXPIRED")
        try:
            if not self.prepare_slot("ACCOUNT",expires_ms):return MailOutcome("BUSY")
            h=self.repository.reserve_account()
        except (EOFError,TimeoutError,OSError,RuntimeError):return MailOutcome("BUSY")
        if h is None:return MailOutcome("BUSY")
        try:permit=self.repository.authorize_account(h)
        except (Fenced,PolicyDenied):return MailOutcome("BUSY")
        self.last_mail=mail
        try:
            return self._invoke(identity_for(self.repository,permit.invocation_id),mail)
        except (EOFError,TimeoutError,OSError,RuntimeError):
            self.repository.record_fact(permit.invocation_id,"UNKNOWN",fact_key="account-rpc-unknown")
            with atomic(self.repository.c):self.repository._block(permit.invocation_id)
            return MailOutcome("UNKNOWN",permit.invocation_id,False)

    def send_admin(self,company_key):
        from app.notifications.opportunity_renderer import render_opportunity
        repo=self.repository
        state=repo._one("SELECT company_key FROM opportunity_state WHERE legacy_company_id=?",(company_key,))
        if not state:return MailOutcome("BUSY")
        try:
            if not self.prepare_slot("ADMIN"):return MailOutcome("BUSY")
            h=repo.claim_delivery(channel="ADMIN",company_key=state["company_key"])
        except (EOFError,TimeoutError,OSError,RuntimeError):return MailOutcome("BUSY")
        if h is None:return MailOutcome("BUSY")
        d=repo._delivery(h,("PROCESSING",))
        i,e=repo._context(d["intent_id"])
        a=repo._audience(d)
        rendered=render_opportunity(snapshot=repo._load(i["snapshot_payload_id"]),reasons=json.loads(e["reasons_json"]),
            role=a["role"],channel="ADMIN",branch=a["selected_branch"],observed_ms=e["observed_ms"],persisted_ms=e["persisted_ms"],rendered_ms=repo.now(),links={})
        permit=repo.authorize_send(h)
        if permit is None:return MailOutcome("EXPIRED")
        try:
            result=repo.submit_fake(permit,self,message=dict(subject=rendered.subject,html=rendered.html))
            repo.acknowledge(h)
            return MailOutcome(result.kind,permit.invocation_id,result.ended)
        except (EOFError,TimeoutError,OSError,RuntimeError):
            repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="admin-rpc-unknown")
            repo.recover_delivery(h.key,unknown_commit=True)
            return MailOutcome("UNKNOWN",permit.invocation_id,False)


class _ChildKeyProvider:
    """Memory-only, restricted capability over the existing authenticated relay."""
    FIELDS={'token','child','role','namespace','provider','custody_generation',
            'controller_generation','epoch','expires_ms','authority'}

    def __init__(self,owner,capability,connection):
        self.owner,self.connection=owner,connection
        self.counter=0
        self.closed=False
        self.replace(capability)

    def replace(self,capability):
        from app.database.key_provider import KeyUnavailable
        from app.database.protected_payloads import namespace
        if (type(capability) is not dict or set(capability)!=self.FIELDS
            or capability['child']!=guard().child_identity or capability['role']!=self.owner.role
            or capability['namespace']!=namespace(self.connection.execute('PRAGMA database_list').fetchone()[2])
            or not isinstance(capability['token'],str) or len(capability['token'])!=64
            or any(type(capability[k]) is not int or capability[k]<0 for k in
                   ('custody_generation','controller_generation','epoch','expires_ms'))):
            raise KeyUnavailable('protected child capability rejected')
        self.capability=dict(capability)
        self.counter=0

    def _call(self,operation,arguments):
        from app.database.key_provider import KeyUnavailable
        try:
            if self.closed or self.connection.in_transaction:raise KeyUnavailable('protected RPC outside write units only')
            if self.capability['expires_ms']<=self.owner.now:raise KeyUnavailable('protected capability expired')
            self.counter+=1
            data=dict(capability=self.capability,counter=self.counter,operation=operation,arguments=arguments)
            result=self.owner._relay('protected-key-v1',data)
            if type(result) is not dict or set(result)!={'counter','result'} or result['counter']!=self.counter:
                raise KeyUnavailable('protected response identity rejected')
            return result['result']
        except Exception:raise KeyUnavailable('guarded protected authority unavailable') from None

    def schema_required(self,namespace):return self._call('schema_required',dict(namespace=namespace))
    def check_database(self,namespace,fingerprint):return self._call('check_database',dict(namespace=namespace,fingerprint=fingerprint))
    def begin_database(self,namespace,fingerprint):return self._call('begin_database',dict(namespace=namespace,fingerprint=fingerprint))
    def finish_database(self,namespace,fingerprint,schema):return self._call('finish_database',dict(namespace=namespace,fingerprint=fingerprint,schema=schema))
    def expected(self,reference):
        from app.database.key_provider import PayloadContext,KeyUnavailable
        try:return PayloadContext(**self._call('expected',dict(reference=reference)))
        except Exception:raise KeyUnavailable('invalid protected context response') from None
    def unwrap(self,reference,context):
        from app.database.key_provider import KeyGrant,KeyUnavailable
        try:
            value=self._call('unwrap',dict(reference=reference,context=context.as_dict()))
            if (type(value) is not dict or set(value)!={'reference','key','generation'}
                or value['reference']!=reference or type(value['generation']) is not int
                or value['generation']<1):raise KeyUnavailable('invalid protected grant')
            key=bytes.fromhex(value['key'])
            if len(key)!=32:raise KeyUnavailable('invalid protected grant')
            return KeyGrant(value['reference'],key,value['generation'])
        except Exception:raise KeyUnavailable('invalid protected grant response') from None


class ChildRole:
    """Fixed bootstrap entry; no caller-selected module, code or generic eval."""
    def __init__(self,role,emit,nonce):
        if guard().child_role!=role:raise PolicyDenied("bootstrap role mismatch")
        self.role,self.emit,self.nonce=role,emit,nonce
        self.context,self.repo,self.authority,self.gateway=None,None,None,None
        self.key_scope,self.key_provider=None,None
        self.controller=None
        self.now=0
        self.sequence=0

    def _relay(self,operation,payload):
        self.emit(dict(rpc=True,sequence=self.sequence,nonce_hash=hashlib.sha256(self.nonce.encode()).hexdigest(),operation=operation,payload=payload))
        line=sys.stdin.readline(131073)
        if not line or len(line.encode())>131072:raise EOFError("authority relay lost")
        frame=json.loads(line)
        if frame.get("nonce")!=self.nonce or frame.get("sequence")!=self.sequence:raise PolicyDenied("authenticated relay response required")
        if "rpc_error" in frame:raise RuntimeError("authority reply unavailable")
        if set(frame)!={"nonce","sequence","rpc_result"}:raise PolicyDenied("exact relay response required")
        return frame["rpc_result"]

    def handle(self,operation,data):
        if operation=="open":
            allowed={"path","now"} if self.role in ('authority','controller-host-crash-test') else {"path","now","authority"}
            if self.context is not None or set(data) not in (allowed,allowed|{'key_capability'}):raise ValueError("one exact child fixture binding")
            if self.role=='controller-host-crash-test' and 'key_capability' not in data:
                raise PolicyDenied('explicit encrypted controller-host capability required')
            self.now=integer(data["now"])
            self.context=connect_disposable(Path(data["path"]))
            c=self.context.__enter__()
            verify_schema(c)
            try:
                if 'key_capability' in data:
                    from app.database.protected_payloads import ProtectedPayloads,protected_scope
                    self.key_provider=_ChildKeyProvider(self,data['key_capability'],c)
                    codec=ProtectedPayloads(self.key_provider,data['path'])
                    self.key_scope=protected_scope(data['path'],codec)
                    self.key_scope.__enter__()
                self.repo=EventRepository(c,clock=lambda:self.now,record_mode=True,info_ttl_ms=60000)
            except BaseException:
                self.close()
                raise
            if self.role=="authority":self.authority=FakeAuthority(self.repo)
            elif self.role=='client':self.gateway=AuthorityGateway(self.repo,data["authority"],self._relay)
            return dict(pid=os.getpid(),role=self.role)
        if self.repo is None:raise PolicyDenied("fixture binding required")
        if operation=="advance":
            if set(data) not in ({"now"},{'now','key_capability'}) or integer(data["now"])<self.now:raise ValueError("monotonic fictional clock required")
            self.now=data["now"]
            if 'key_capability' in data:
                if self.key_provider is None:raise PolicyDenied('protected session required')
                self.key_provider.replace(data['key_capability'])
                self.key_provider.schema_required(self.key_provider.capability['namespace'])
            return dict(now=self.now)
        if operation=="crash":os._exit(70)
        if self.role=='controller-host-crash-test':
            if data!={}:raise PolicyDenied('exact controller-host operation required')
            if operation=='elect_controller' and self.controller is None:
                self.controller=RuntimeController.acquire(self.repo,lease_ms=100)
            elif operation=='enable_controller' and self.controller is not None:
                from app.monitoring.notification_pipeline import V2Options
                self.controller.enable(V2Options(drain=True,transport=True))
            elif operation!='controller_status' or self.controller is None:
                raise PolicyDenied('undeclared controller-host operation')
            owner=self.controller._owned()
            return dict(pid=os.getpid(),generation=self.controller.generation,lease_ms=owner['lease_ms'])
        if self.role=="authority":
            if operation=="elect" and data=={}:return self.authority.elect()
            if operation=="admit":return self.authority.admit(data)
            if operation=="consume":return self.authority.consume(data)
            if operation=="end":return self.authority.end(data)
            if operation=="status" and data=={}:return self.authority.status()
            if operation=="configure":
                if set(data)!={"kind","ended","fault"} or data["kind"] not in ("ACCEPTED","NOT_ACCEPTED","UNKNOWN") or type(data["ended"]) is not bool or data["fault"] not in (None,"after_admission","after_consumption","during_physical","after_acceptance","after_end","accept_then_error"):
                    raise ValueError("fixed fictional fault schedule required")
                self.authority.config=data
                return dict(configured=True)
        else:
            if operation=="worker" and (data=={} or (set(data)=={"channel"} and data["channel"] in ("ADMIN","SUBSCRIBER"))):
                from app.database.repository import DatabaseRepository
                from app.monitoring.notification_pipeline import NotificationPipeline,V2Options
                from app.subscribers.worker import DeliveryWorker
                database=DatabaseRepository(str(Path(self.repo.c.execute("PRAGMA database_list").fetchone()[2])))
                pipeline=NotificationPipeline(self.repo,transport=self.gateway,options=V2Options(fanout=True,drain=True,transport=True,info_ttl_ms=60000),role="client")
                worker=DeliveryWorker(database.deliveries,database.tokens,notification_pipeline=pipeline,batch_size=1 if data else 25)
                if data:worker._v2_channel=data["channel"]
                sent=asyncio.run(worker.process_batch_once())
                pipeline.shutdown()
                return dict(sent=sent,attempts=worker.last_v2_attempts,pid=os.getpid())
            if operation=="admin":
                from app.notifications.email import EmailNotificationProvider
                from app.tpo.models import CompanyRecord
                if set(data)!={"company"}:raise ValueError("fixed company helper request required")
                return EmailNotificationProvider(transport=self.gateway).send_new_company_notification(CompanyRecord(**data["company"]),False).to_wire()
            if operation=="account":
                from app.api.email_service import EmailService
                if set(data)!={"method","destination","token","expires_ms"} or data["method"] not in ("verification","preferences"):
                    raise ValueError("fixed ACCOUNT helper request required")
                service=EmailService(transport=self.gateway)
                send=service.send_verification_email if data["method"]=="verification" else service.send_preference_link_email
                return send(data["destination"],data["token"],expires_ms=data["expires_ms"]).to_wire()
            if operation=="prepare":
                if data not in ({"message_class":"ACCOUNT"},{"message_class":"ACCOUNT","stage":"reserved"}):raise PolicyDenied("only exact ACCOUNT prepare")
                if not self.gateway.prepare_slot("ACCOUNT"):return dict(kind="BUSY")
                h=self.repo.reserve_account()
                if h is None:return dict(kind="BUSY")
                if data.get("stage")=="reserved":return asdict(h)
                permit=self.repo.authorize_account(h)
                mail=EphemeralMail("ACCOUNT","fictional@example.invalid","Fictional account request","<p>finite fictional request</p>",self.now+1000,{})
                return self.gateway.ticket(permit,mail)
            if operation=="status" and data=={}:
                return dict(pid=os.getpid(),epoch=self.repo._runtime()["epoch"],send_mode=self.repo._runtime()["send_mode"])
        raise PolicyDenied("undeclared role operation")

    def close(self):
        try:
            if self.controller:self.controller.shutdown()
            if self.authority:
                for invocation in list(self.authority.sink.active):
                    self.authority.end(identity_for(self.repo,invocation).to_wire())
        finally:
            if self.key_provider:self.key_provider.closed=True
            if self.key_scope:self.key_scope.__exit__(None,None,None);self.key_scope=None
            if self.context:self.context.__exit__(None,None,None);self.context=None
