"""True guarded processes; only external feed and physical fake boundary substituted."""
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import replace,asdict

import pytest
import isolation_bootstrap as isolation
from test_cummins_end_to_end_v2 import integrated,record


@contextmanager
def topology(x,*,clients=1):
    from contextlib import ExitStack
    from app.notifications.transport_arbiter import RuntimeController
    controller=RuntimeController.acquire(x["repo"])
    controller.enable(x["pipeline"].options)
    with ExitStack() as stack:
        authority=stack.enter_context(isolation.start_test_child("authority"))
        authority.request("open",dict(path=str(x["path"]),now=x["clock"]()))
        identity=authority.request("elect",{})["authority"]
        attached=[]
        for _ in range(clients):
            client=stack.enter_context(isolation.start_test_child("client"))
            client.request("open",dict(path=str(x["path"]),now=x["clock"](),authority=identity))
            attached.append(client)
        yield controller,authority,attached


def configure(authority,kind="ACCEPTED",ended=True,fault=None):
    authority.request("configure",dict(kind=kind,ended=ended,fault=fault))


def prepare(client,authority):
    return client.request("prepare",{"message_class":"ACCOUNT"},relay=authority.request)


def gate(x):return x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]


@pytest.mark.asyncio
async def test_two_processes_cannot_replay_one_account_invocation(integrated):
    from app.notifications.transport_arbiter import RuntimeController,AuthorityGateway
    x=integrated
    controller=RuntimeController.acquire(x["repo"])
    assert x["repo"]._runtime()["send_mode"]=="OFF"
    controller.enable(x["pipeline"].options)
    with isolation.start_test_child("authority") as authority, isolation.start_test_child("client") as first, isolation.start_test_child("client") as second:
        authority.request("open",dict(path=str(x["path"]),now=x["clock"]()))
        registration=authority.request("elect",{})
        for child in (first,second):
            child.request("open",dict(path=str(x["path"]),now=x["clock"](),authority=registration["authority"]))
        ticket=first.request("prepare",{"message_class":"ACCOUNT"},relay=authority.request)
        grant=authority.request("admit",ticket)
        result=authority.request("consume",grant)
        assert result["kind"]=="ACCEPTED" and result["ended"] is True
        with pytest.raises(RuntimeError):second.request("prepare",{"replay":grant},relay=authority.request)
        with pytest.raises(RuntimeError):authority.request("consume",grant)
        status=authority.request("status",{})
        assert status["physical_calls"]==1 and status["active"]==0
        assert len({os.getpid(),authority.ready["pid"],first.ready["pid"],second.ready["pid"]})==4
        assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="FREE"
        sys.__stdout__.write("\nR2A_TOPOLOGY "+json.dumps(dict(parent_pid=os.getpid(),authority_pid=authority.ready["pid"],
            client_pids=[first.ready["pid"],second.ready["pid"]],authority=registration["authority"],
            invocation=ticket["identity"]["invocation"],nonce_hashes=[c.ready["nonce_hash"] for c in (authority,first,second)],
            physical_calls=status["physical_calls"],active=status["active"],gate="FREE"))+"\n")


def test_account_preparation_recovery_is_exact_and_never_frees_invocation(integrated):
    from app.database.event_repository import Fenced
    x=integrated
    h=x["repo"].reserve_account()
    with pytest.raises(Fenced):x["repo"].recover_account(h)
    x["clock"].now+=101
    assert x["repo"].recover_account(h) is True
    assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="FREE"


def test_attached_pipeline_cannot_change_controller_modes(integrated):
    from app.notifications.transport_arbiter import RuntimeController
    from app.monitoring.notification_pipeline import NotificationPipeline,V2Options
    from app.database.event_repository import PolicyDenied
    x=integrated
    controller=RuntimeController.acquire(x["repo"])
    controller.enable(x["pipeline"].options)
    epoch=x["repo"]._runtime()["epoch"]
    attached=NotificationPipeline(x["repo"],options=V2Options(),role="client")
    assert x["repo"]._runtime()["epoch"]==epoch
    with pytest.raises(PolicyDenied):attached._set_modes()
    attached.shutdown()
    assert x["repo"]._runtime()["epoch"]==epoch
    assert x["repo"]._runtime()["send_mode"]=="V2"


@pytest.mark.parametrize("field,value",[("account","other-account"),("gate_generation",999),("authority","0"*32),("invocation","0"*32),("submission","0"*32),("epoch",999)])
def test_wrong_exact_identity_cannot_consume_or_end(integrated,field,value):
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        grant=authority.request("admit",ticket)
        wrong=dict(grant,identity=dict(grant["identity"],**{field:value}))
        with pytest.raises(RuntimeError):authority.request("consume",wrong)
        with pytest.raises(RuntimeError):authority.request("end",wrong["identity"])
        assert authority.request("status",{})["physical_calls"]==0 and gate(x)=="INFLIGHT"
        assert authority.request("consume",grant)["kind"]=="ACCEPTED"
        authority.request("end",grant["identity"])
        authority.request("end",grant["identity"])
        assert authority.request("status",{})["physical_calls"]==1 and gate(x)=="FREE"


@pytest.mark.parametrize("kind",["ACCEPTED","NOT_ACCEPTED","UNKNOWN"])
def test_typed_outcome_and_lease_does_not_end_active_call(integrated,kind):
    x=integrated
    with topology(x,clients=2) as (_,authority,clients):
        configure(authority,kind,False)
        ticket=prepare(clients[0],authority)
        grant=authority.request("admit",ticket)
        assert authority.request("consume",grant)["kind"]==kind
        x["clock"].now+=101
        for child in (authority,*clients):child.request("advance",{"now":x["clock"]()})
        assert prepare(clients[1],authority)=={"kind":"BUSY"}
        assert gate(x)=="INFLIGHT"
        assert authority.request("status",{})["active"]==1
        authority.request("end",grant["identity"])
        assert gate(x)=="FREE"


@pytest.mark.parametrize("fault,calls",[("after_admission",0),("after_consumption",0),("during_physical",1),("after_acceptance",1),("after_end",1)])
def test_authority_crash_never_reconstructs_admission(integrated,fault,calls):
    from app.notifications.transport_arbiter import supervisor_end
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        configure(authority,fault=fault)
        with pytest.raises((EOFError,OSError)):
            grant=authority.request("admit",ticket)
            authority.request("consume",grant)
        invocation=ticket["identity"]["invocation"]
        x["repo"].record_fact(invocation,"UNKNOWN",fact_key="producer-lost-response")
        ledger=x["path"].with_suffix(".fake-calls.jsonl")
        count=sum(json.loads(line)["phase"]=="START" for line in ledger.read_text().splitlines()) if ledger.exists() else 0
        assert count==calls
        if fault!="after_end":assert gate(x)=="INFLIGHT"
        with isolation.start_test_child("authority") as successor:
            successor.request("open",dict(path=str(x["path"]),now=x["clock"]()))
            if gate(x)!="FREE":
                with pytest.raises(RuntimeError):successor.request("elect",{})
            proof=supervisor_end(x["repo"],authority,invocation)
            assert proof["pid"]==authority.ready["pid"] and gate(x)=="FREE"
            successor.request("elect",{})
            with pytest.raises(RuntimeError):successor.request("admit",ticket)
            assert successor.request("status",{})["physical_calls"]==0


def test_client_exit_proof_cannot_end_authority_thread(integrated):
    from app.notifications.transport_arbiter import supervisor_end
    from app.database.event_repository import PolicyDenied
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        configure(authority,ended=False)
        grant=authority.request("admit",ticket)
        authority.request("consume",grant)
        clients[0].terminate()
        with pytest.raises(PolicyDenied):supervisor_end(x["repo"],clients[0],ticket["identity"]["invocation"])
        assert authority.request("status",{})["active"]==1 and gate(x)=="INFLIGHT"
        authority.request("end",grant["identity"])


def test_controller_shutdown_and_successor_fence_unconsumed_grant(integrated):
    from app.notifications.transport_arbiter import RuntimeController
    from app.database.event_repository import Fenced
    x=integrated
    with topology(x) as (controller,authority,clients):
        ticket=prepare(clients[0],authority)
        grant=authority.request("admit",ticket)
        controller.shutdown()
        successor=RuntimeController.acquire(x["repo"])
        successor.enable(x["pipeline"].options)
        with pytest.raises(Fenced):controller.enable(x["pipeline"].options)
        with pytest.raises(RuntimeError):authority.request("consume",grant)
        with pytest.raises(RuntimeError):clients[0].request("elect",{})
        assert authority.request("status",{})["physical_calls"]==0 and gate(x)=="INFLIGHT"
        authority.request("end",grant["identity"])
        authority.request("elect",{})
        with pytest.raises(RuntimeError):authority.request("consume",grant)


def test_expired_account_message_cannot_consume(integrated):
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        ticket["mail"]["expires_ms"]=x["clock"]()+10
        grant=authority.request("admit",ticket)
        authority.request("advance",{"now":x["clock"]()+11})
        assert x["repo"]._one("SELECT lease_ms FROM smtp_account_gate")["lease_ms"]>x["clock"]()+11
        with pytest.raises(RuntimeError):authority.request("consume",grant)
        assert authority.request("status",{})["physical_calls"]==0 and gate(x)=="INFLIGHT"
        authority.request("end",grant["identity"])


@pytest.mark.asyncio
@pytest.mark.parametrize("stage",["reservation","attempt","t5","invocation","t6"])
async def test_repository_crash_boundaries_preserve_t5_t6(integrated,stage):
    from app.database.event_repository import CommitUnknown
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    def crash(at):
        if at==stage:raise RuntimeError("fictional transaction interruption")
    if stage in ("reservation","attempt"):
        with pytest.raises(RuntimeError):x["repo"].claim_delivery(channel="ADMIN",fault=crash)
        assert gate(x)=="FREE" and x["c"].execute("SELECT count(*) FROM delivery_attempts").fetchone()[0]==0
    elif stage=="t5":
        with pytest.raises(CommitUnknown) as failure:x["repo"].claim_delivery(channel="ADMIN",lose_ack=True)
        h=x["repo"].inspect_claim(failure.value.key)
        assert h is not None and gate(x)=="RESERVED"
    else:
        h=x["repo"].claim_delivery(channel="ADMIN")
        with pytest.raises((RuntimeError,CommitUnknown)):
            x["repo"].authorize_send(h,fault=crash,lose_ack=stage=="t6")
        count=x["c"].execute("SELECT count(*) FROM transport_invocations").fetchone()[0]
        assert count==int(stage=="t6") and gate(x)==("INFLIGHT" if stage=="t6" else "RESERVED")


def test_account_authority_death_before_t6_recovers_only_exact_preparation(integrated):
    from app.database.event_repository import Handle,Fenced
    x=integrated
    with topology(x) as (_,authority,clients):
        data=clients[0].request("prepare",{"message_class":"ACCOUNT","stage":"reserved"},relay=authority.request)
        h=Handle(**data)
        authority.terminate()
        with pytest.raises(Fenced):x["repo"].recover_account(replace(h,submission_key="0"*32))
        with pytest.raises(Fenced):x["repo"].recover_account(h)
        x["clock"].now+=101
        assert x["repo"].recover_account(h) and gate(x)=="FREE"
        assert x["c"].execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0


def test_account_authority_death_after_t6_requires_positive_process_end(integrated):
    from app.database.event_repository import Handle,Fenced
    from app.notifications.transport_arbiter import supervisor_end
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        authority.terminate()
        x["clock"].now+=1001
        g=x["repo"]._one("SELECT * FROM smtp_account_gate")
        h=Handle("ACCOUNT",g["account_key"],g["owner_token"],g["generation"],g["runtime_epoch"],g["lease_ms"],g["generation"],g["submission_key"])
        with pytest.raises(Fenced):x["repo"].recover_account(h)
        assert gate(x)=="INFLIGHT"
        supervisor_end(x["repo"],authority,ticket["identity"]["invocation"])
        assert gate(x)=="FREE"


def test_bounded_class_fairness_opportunities_without_physical_spin(integrated):
    import secrets
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        def request(channel,token):
            return authority.request("admit",dict(slot_request=token,message_class=channel,expires_ms=x["clock"]()+10000))
        ids={k:secrets.token_hex(16) for k in ("ADMIN","SUBSCRIBER","ACCOUNT")}
        assert request("ADMIN",ids["ADMIN"])["kind"]=="GRANTED"
        for _ in range(9):request("ADMIN",secrets.token_hex(16))
        assert request("SUBSCRIBER",ids["SUBSCRIBER"])["kind"]=="BUSY"
        assert request("ACCOUNT",ids["ACCOUNT"])["kind"]=="BUSY"
        for _ in range(9):request("ADMIN",secrets.token_hex(16))
        waiting=authority.request("status",{})["waiting"]
        assert sum(waiting.values())<=6 and waiting["ADMIN"]<=2 and waiting["SUBSCRIBER"]==1 and waiting["ACCOUNT"]==1
        winners=[]
        for expected in ("SUBSCRIBER","ACCOUNT","ADMIN"):
            x["clock"].now+=1001
            authority.request("advance",{"now":x["clock"]()})
            if expected=="ADMIN":
                # Existing admitted ADMIN requests expire; the other classes
                # already received the next two fair opportunities.
                x["clock"].now+=10000
                authority.request("advance",{"now":x["clock"]()})
                ids[expected]=secrets.token_hex(16)
            result=request(expected,ids[expected])
            assert result["kind"]=="GRANTED"
            winners.append(expected)
        assert winners==["SUBSCRIBER","ACCOUNT","ADMIN"]
        assert authority.request("status",{})["physical_calls"]==0 and gate(x)=="FREE"


def test_expired_controller_cannot_admit_new_account_request(integrated):
    x=integrated
    with topology(x) as (_,authority,clients):
        x["clock"].now+=86400001
        for child in (authority,*clients):child.request("advance",{"now":x["clock"]()})
        with pytest.raises(RuntimeError):prepare(clients[0],authority)
        assert authority.request("status",{})["physical_calls"]==0 and gate(x)=="FREE"


def test_simultaneous_process_clients_have_one_active_physical_invocation(integrated):
    from concurrent.futures import ThreadPoolExecutor
    from test_notification_transport_mail_paths import account_request
    x=integrated
    with topology(x,clients=2) as (_,authority,clients):
        configure(authority,ended=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(account_request,c,authority,x["clock"]()) for c in clients]
            replies=[future.result(timeout=8) for future in futures]
        assert sorted(r["kind"] for r in replies)==["ACCEPTED","BUSY"]
        status=authority.request("status",{})
        assert status["physical_calls"]==1 and status["active"]==1
        assert len({c.ready["pid"] for c in clients}|{authority.ready["pid"],os.getpid()})==4


def test_controller_shutdown_keeps_active_invocation_and_exact_end(integrated):
    x=integrated
    with topology(x) as (controller,authority,clients):
        configure(authority,ended=False)
        ticket=prepare(clients[0],authority)
        grant=authority.request("admit",ticket)
        authority.request("consume",grant)
        epoch=x["repo"]._runtime()["epoch"]
        clients[0].close()
        assert x["repo"]._runtime()["epoch"]==epoch
        controller.shutdown()
        assert authority.request("status",{})["active"]==1 and gate(x)=="INFLIGHT"
        authority.request("end",grant["identity"])
        assert gate(x)=="FREE" and x["repo"]._runtime()["send_mode"]=="OFF"


def test_consumed_capacity_slot_cannot_bypass_fair_admission(integrated):
    from app.notifications.transport_arbiter import get_state
    from app.database.event_repository import Fenced
    x=integrated
    with topology(x) as (_,authority,clients):
        ticket=prepare(clients[0],authority)
        grant=authority.request("admit",ticket)
        authority.request("consume",grant)
        x["repo"].transport_authority=ticket["identity"]["authority"]
        x["repo"].transport_slot=get_state(x["repo"],"r2a:slot:account-1")["token"]
        with pytest.raises(Fenced):x["repo"].reserve_account()
        assert authority.request("status",{})["physical_calls"]==1


@pytest.mark.parametrize("phase",["startup","shutdown"])
def test_controller_election_between_legacy_check_and_transaction_is_fenced(integrated,monkeypatch,phase):
    import app.monitoring.notification_pipeline as module
    from app.notifications.transport_arbiter import RuntimeController
    from app.database.event_repository import PolicyDenied
    x=integrated
    original=module.atomic
    elections=[]
    @contextmanager
    def election_before_lock(connection):
        if not elections:
            RuntimeController.acquire(x["repo"])
            elections.append(x["repo"]._runtime()["epoch"])
        with original(connection):yield
    monkeypatch.setattr(module,"atomic",election_before_lock)
    if phase=="startup":
        with pytest.raises(PolicyDenied):
            module.NotificationPipeline(x["repo"],transport=x["smtp"],options=x["pipeline"].options)
    else:x["pipeline"].shutdown()
    runtime=x["repo"]._runtime()
    assert runtime["epoch"]==elections[0] and runtime["send_mode"]=="OFF"


@pytest.mark.parametrize("expired",[False,True])
def test_shared_registration_fences_unbound_account_producer(integrated,expired):
    from app.database.event_repository import EventRepository,Fenced
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        unbound=EventRepository(x["c"],clock=x["clock"])
        if expired:x["clock"].now+=86400001
        with pytest.raises(Fenced):unbound.reserve_account()
        assert gate(x)=="FREE" and authority.request("status",{})["physical_calls"]==0


@pytest.mark.asyncio
async def test_shared_registration_fences_unbound_delivery_worker(integrated):
    from app.monitoring.notification_pipeline import NotificationPipeline
    from app.database.event_repository import PolicyDenied
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x,clients=0) as (_,authority,_):
        assert await x["watcher"].delivery_worker.process_batch_once()==0
        assert x["smtp"].calls==[] and gate(x)=="FREE"
        with pytest.raises(PolicyDenied):NotificationPipeline(x["repo"],transport=x["smtp"],options=x["pipeline"].options,role="client")
        assert authority.request("status",{})["physical_calls"]==0


@pytest.mark.parametrize("operation",["activate","bump_epoch"])
def test_legacy_runtime_mutators_cannot_override_registered_controller(integrated,operation):
    from app.notifications.transport_arbiter import RuntimeController
    from app.database.event_repository import PolicyDenied
    x=integrated
    controller=RuntimeController.acquire(x["repo"])
    controller.shutdown()
    epoch=x["repo"]._runtime()["epoch"]
    with pytest.raises(PolicyDenied):getattr(x["repo"],operation)()
    assert x["repo"]._runtime()["epoch"]==epoch and x["repo"]._runtime()["send_mode"]=="OFF"


def test_legacy_inflight_permission_must_end_before_shared_enrollment(integrated):
    from app.notifications.transport_arbiter import RuntimeController
    from app.database.event_repository import Fenced,PolicyDenied,FakeTransport
    x=integrated
    h=x["repo"].reserve_account()
    permit=x["repo"].authorize_account(h)
    with pytest.raises(Fenced):RuntimeController.acquire(x["repo"])
    x["repo"].record_fact(permit.invocation_id,"ENDED",fact_key="positive-never-invoked-legacy-retirement")
    controller=RuntimeController.acquire(x["repo"])
    controller.enable(x["pipeline"].options)
    sink=FakeTransport()
    with pytest.raises((Fenced,PolicyDenied)):x["repo"].submit_fake(permit,sink)
    assert sink.calls==[]


@pytest.mark.parametrize("threaded",[False,True])
def test_lost_volatile_binding_cannot_submit_an_existing_shared_permit(integrated,threaded):
    from app.notifications.transport_arbiter import AuthorityGateway
    from app.database.event_repository import FakeTransport,Fenced
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        gateway=AuthorityGateway(x["repo"],authority.request("status",{})["authority"],authority.request)
        assert gateway.prepare_slot("ACCOUNT")
        permit=x["repo"].authorize_account(x["repo"].reserve_account())
        x["repo"].transport_authority=None
        sink=FakeTransport()
        if threaded:
            import threading
            errors=[]
            def submit():
                try:x["repo"].submit_fake(permit,sink)
                except Exception as exc:errors.append(exc)
            thread=threading.Thread(target=submit)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive() and len(errors)==1 and isinstance(errors[0],Fenced)
        else:
            with pytest.raises(Fenced):x["repo"].submit_fake(permit,sink)
        assert sink.calls==[] and authority.request("status",{})["physical_calls"]==0
