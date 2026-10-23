"""Actual mail helper interfaces through the guard-confined shared fake authority."""
import hashlib
import json
import pytest
import isolation_bootstrap as isolation
from test_cummins_end_to_end_v2 import integrated,record
from test_notification_transport_multiprocess import topology,configure,gate


def account_request(client,authority,now,method="verification",expiry=None):
    return client.request("account",dict(method=method,destination="fictional-account@example.invalid",token="fictional-ephemeral-marker",expires_ms=now+1000 if expiry is None else expiry),relay=authority.request)


def admin_request(client,authority):
    return client.request("admin",dict(company=record().model_dump()),relay=authority.request)


@pytest.mark.asyncio
async def test_existing_three_mail_classes_share_authority(integrated):
    from app.notifications.transport_arbiter import RuntimeController
    x=integrated
    controller=RuntimeController.acquire(x["repo"])
    controller.enable(x["pipeline"].options)
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with isolation.start_test_child("authority") as authority, isolation.start_test_child("client") as client:
        authority.request("open",dict(path=str(x["path"]),now=x["clock"]()))
        identity=authority.request("elect",{})["authority"]
        client.request("open",dict(path=str(x["path"]),now=x["clock"](),authority=identity))
        phases=[]
        def relay(operation,data):
            try:result=authority.request(operation,data)
            except Exception as exc:
                phases.append((operation,str(exc)))
                raise
            phases.append((operation,"reply"))
            return result
        admin=client.request("admin",dict(company=record().model_dump()),relay=relay)
        assert admin["kind"]=="ACCEPTED",phases
        sub=client.request("worker",{},relay=authority.request)
        assert sub["sent"]==1
        account=client.request("account",dict(method="verification",destination="fictional-account@example.invalid",token="fictional-verification-marker",expires_ms=x["clock"]()+1000),relay=authority.request)
        assert account["kind"]=="ACCEPTED"
        status=authority.request("status",{})
        assert status["physical_calls"]==3 and status["active"]==0
        assert {m["message_class"] for m in status["fidelity"]}=={"ADMIN","SUBSCRIBER","ACCOUNT"}
        submail=next(m for m in status["fidelity"] if m["message_class"]=="SUBSCRIBER")
        assert set(submail["headers"])=={"List-Unsubscribe","List-Unsubscribe-Post"}
        assert submail["destination_hash"]==hashlib.sha256(b"fictional-student@example.invalid").hexdigest()
        metadata=" ".join(r[0] for r in x["c"].execute("SELECT value FROM system_state"))
        assert "fictional-verification-marker" not in metadata and "<html" not in metadata and "@" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("active,competitor",[("ADMIN","SUBSCRIBER"),("SUBSCRIBER","ACCOUNT"),("ACCOUNT","ADMIN")])
async def test_three_real_helper_contentions_have_one_physical_caller(integrated,active,competitor):
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x,clients=2) as (_,authority,clients):
        configure(authority,ended=False)
        def dispatch(child,channel):
            if channel=="ADMIN":return admin_request(child,authority)
            if channel=="ACCOUNT":return account_request(child,authority,x["clock"]())
            return child.request("worker",{"channel":"SUBSCRIBER"},relay=authority.request)
        first=dispatch(clients[0],active)
        assert first.get("kind")=="ACCEPTED" or first.get("sent")==1
        pending_before=x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]
        second=dispatch(clients[1],competitor)
        assert second.get("kind")=="BUSY" or second.get("sent")==0
        assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE status='PENDING'").fetchone()[0]==pending_before
        status=authority.request("status",{})
        assert status["active"]==1 and status["physical_calls"]==1 and gate(x)=="INFLIGHT"
        invocation=status["call_ids"][0]
        from app.notifications.transport_arbiter import identity_for
        authority.request("end",identity_for(x["repo"],invocation).to_wire())
        configure(authority)
        # A worker also offered ADMIN while blocked; finite waiting requests can
        # expire before this deterministic next opportunity, without busy-spin.
        x["clock"].now+=1001
        for child in (authority,*clients):child.request("advance",{"now":x["clock"]()})
        third=dispatch(clients[1],competitor)
        assert third.get("kind")=="ACCEPTED" or third.get("sent")==1
        assert authority.request("status",{})["physical_calls"]==2 and gate(x)=="FREE"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary",["drop_admission","drop_consumption","accept_then_error","before_t7","after_t7"])
async def test_unknown_or_lost_ack_never_resends_and_keeps_unrelated_work(integrated,boundary):
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x) as (_,authority,clients):
        if boundary=="accept_then_error":configure(authority,fault="accept_then_error")
        calls=[]
        def relay(operation,data):
            result=authority.request(operation,data)
            calls.append(operation)
            if (boundary=="drop_admission" and operation=="admit" and "identity" in data) or (boundary=="drop_consumption" and operation=="consume"):
                raise TimeoutError("fictional acknowledgment lost")
            if boundary=="before_t7" and operation=="consume":
                clients[0].terminate()
                raise EOFError("fictional producer exit after physical reply")
            return result
        try:reply=clients[0].request("worker",{"channel":"ADMIN"},relay=relay)
        except (EOFError,BrokenPipeError,OSError):reply=None
        row=x["repo"]._one("SELECT * FROM notification_deliveries_v2 WHERE channel='ADMIN'")
        if boundary=="after_t7":
            clients[0].terminate()
            assert row["status"]=="SENT"
        elif boundary=="before_t7":
            assert row["status"]=="SENDING"
            x["clock"].now+=101
            assert x["repo"].recover_delivery(row["delivery_id"])=="UNCERTAIN"
        else:assert row["status"]=="UNCERTAIN"
        assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="PENDING"
        before=authority.request("status",{})["physical_calls"]
        assert before==int(boundary!="drop_admission")
        if clients[0].process.poll() is None:
            again=clients[0].request("worker",{"channel":"ADMIN"},relay=authority.request)
            # Once capacity is safely free, unrelated subscriber work may send;
            # the original ADMIN invocation never appears twice in the ledger.
            assert again["sent"]<=1
        ledger=x["path"].with_suffix(".fake-calls.jsonl")
        starts=[json.loads(line)["invocation"] for line in ledger.read_text().splitlines() if json.loads(line)["phase"]=="START"] if ledger.exists() else []
        assert starts.count(row["invocation_id"])==before


def test_account_expiry_busy_typed_response_and_no_enumeration(integrated):
    x=integrated
    with topology(x) as (_,authority,clients):
        configure(authority,ended=False)
        accepted=account_request(clients[0],authority,x["clock"](),method="preferences")
        assert accepted["kind"]=="ACCEPTED" and accepted["ended"] is False
        assert account_request(clients[0],authority,x["clock"]())["kind"]=="BUSY"
        assert account_request(clients[0],authority,x["clock"](),expiry=x["clock"]())["kind"]=="EXPIRED"
        assert authority.request("status",{})["physical_calls"]==1
        assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2").fetchone()[0]==0
        from app.notifications.transport_arbiter import identity_for
        authority.request("end",identity_for(x["repo"],accepted["invocation"]).to_wire())


def test_actual_account_helper_fidelity_privacy_and_no_smtp_fallback(integrated,monkeypatch):
    from app.notifications.transport_arbiter import AuthorityGateway
    from app.notifications.email import EmailNotificationProvider
    from app.api.email_service import EmailService
    from app.subscribers.worker import DeliveryWorker
    from unittest.mock import Mock
    import smtplib
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        identity=authority.request("status",{})["authority"]
        gateway=AuthorityGateway(x["repo"],identity,authority.request)
        smtp=Mock(side_effect=AssertionError("fictional path attempted SMTP"))
        monkeypatch.setattr(smtplib,"SMTP",smtp)
        service=EmailService(transport=gateway)
        epoch=x["repo"]._runtime()["epoch"]
        result=service.send_verification_email("fictional@example.invalid","fictional-raw-marker",expires_ms=x["clock"]()+1000)
        assert result.kind=="ACCEPTED" and bool(result)
        message=gateway.last_mail
        assert message.message_class=="ACCOUNT" and message.destination=="fictional@example.invalid"
        assert "/verify#token=fictional-raw-marker" in message.html and "24 hours" in message.html
        assert "https://watcher.example.invalid" in message.html and message.expires_ms==x["clock"]()+1000
        assert service.send_preference_link_email("fictional@example.invalid","fictional-preference-marker").kind=="ACCEPTED"
        assert "15 minutes" in gateway.last_mail.html and "/preferences/confirm#token=" in gateway.last_mail.html
        assert x["repo"]._runtime()["epoch"]==epoch
        with pytest.raises(ValueError):EmailNotificationProvider(transport=gateway)._send_email_to("fictional@example.invalid","Example","<p>x</p>")
        with pytest.raises(ValueError):DeliveryWorker(x["db"].deliveries,x["db"].tokens,EmailNotificationProvider(transport=gateway))
        with pytest.raises(ValueError):x["watcher"].delivery_worker._send_smtp_email("fictional@example.invalid","Example","<p>x</p>")
        smtp.assert_not_called()
        for table in ("system_state","transport_invocations","transport_facts","protected_payloads"):
            dump=str([tuple(row) for row in x["c"].execute("SELECT * FROM "+table).fetchall()])
            assert "fictional-raw-marker" not in dump and "fictional-preference-marker" not in dump and "<html" not in dump and "fictional@example.invalid" not in dump


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["destination","consent"])
async def test_current_destination_and_consent_rechecked_at_consumption(integrated,change):
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x) as (_,authority,clients):
        def relay(operation,data):
            if operation=="consume":
                if change=="destination":x["c"].execute("UPDATE users SET email='new-fictional@example.invalid' WHERE id=?",(x["user"],))
                else:x["db"].users.set_unsubscribed(x["user"])
            return authority.request(operation,data)
        clients[0].request("worker",{"channel":"SUBSCRIBER"},relay=relay)
        assert authority.request("status",{})["physical_calls"]==0
        row=x["repo"]._one("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'")
        assert row["status"]=="UNCERTAIN"


@pytest.mark.asyncio
async def test_late_evidence_preserves_terminal_truth_and_controller_epoch(integrated):
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x) as (_,authority,clients):
        configure(authority,kind="UNKNOWN")
        clients[0].request("worker",{"channel":"ADMIN"},relay=authority.request)
        row=x["repo"]._one("SELECT * FROM notification_deliveries_v2 WHERE channel='ADMIN'")
        assert row["status"]=="UNCERTAIN" and gate(x)=="FREE"
        x["repo"].record_fact(row["invocation_id"],"ACCEPTED",fact_key="late-fictional-acceptance")
        assert x["repo"]._one("SELECT status FROM notification_deliveries_v2 WHERE channel='ADMIN'")["status"]=="UNCERTAIN"
        epoch=x["repo"]._runtime()["epoch"]
        x["repo"].record_fact(row["invocation_id"],"NOT_ACCEPTED",fact_key="conflicting-fictional-negative")
        assert x["repo"]._runtime()["epoch"]==epoch
        assert account_request(clients[0],authority,x["clock"]())["kind"]=="BUSY"
        assert authority.request("status",{})["physical_calls"]==1


@pytest.mark.parametrize("wrong_reply",["admission","outcome"])
def test_ipc_reply_identity_mismatch_is_unknown_never_a_resend(integrated,wrong_reply):
    from app.notifications.transport_arbiter import AuthorityGateway,identity_for
    from app.api.email_service import EmailService
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        identity=authority.request("status",{})["authority"]
        def rpc(operation,data):
            result=authority.request(operation,data)
            if wrong_reply=="admission" and operation=="admit" and "identity" in result:
                return dict(result,identity=dict(result["identity"],submission="0"*32))
            if wrong_reply=="outcome" and operation=="consume":return dict(result,invocation="0"*32)
            return result
        gateway=AuthorityGateway(x["repo"],identity,rpc)
        result=EmailService(transport=gateway).send_verification_email("fictional@example.invalid","fictional-marker")
        assert result.kind=="UNKNOWN" and not bool(result)
        assert authority.request("status",{})["physical_calls"]==int(wrong_reply=="outcome")
        assert x["c"].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='UNKNOWN'").fetchone()[0]==1
        authority.request("end",identity_for(x["repo"],result.invocation).to_wire())


def test_legacy_mock_notifier_remains_compatible(integrated):
    from unittest.mock import MagicMock
    from app.api.email_service import EmailService
    from app.subscribers.worker import DeliveryWorker
    x=integrated
    notifier=MagicMock()
    notifier._send_email_to.return_value=True
    DeliveryWorker(x["db"].deliveries,x["db"].tokens,notifier)
    assert EmailService(notifier).send_verification_email("fictional@example.invalid","fictional-marker") is True


@pytest.mark.asyncio
async def test_admin_update_helper_renders_the_sealed_update_intent(integrated):
    from app.notifications.transport_arbiter import AuthorityGateway
    from app.notifications.email import EmailNotificationProvider
    x=integrated
    await x["watcher"].check_once()
    x["pipeline"].fanout_once()
    with topology(x,clients=0) as (_,authority,_):
        gateway=AuthorityGateway(x["repo"],authority.request("status",{})["authority"],authority.request)
        provider=EmailNotificationProvider(transport=gateway)
        assert provider.send_new_company_notification(record(),False).kind=="ACCEPTED"
        updated=record(regEndtime="12:45")
        x["feed"].records=[updated]
        assert await x["watcher"].check_once()
        x["pipeline"].fanout_once()
        assert provider.send_update_notification(updated,["Registration changed"]).kind=="ACCEPTED"
        assert "12:45" in gateway.last_mail.html
        assert authority.request("status",{})["physical_calls"]==2


def test_completed_account_diagnostics_are_bounded_and_redacted(integrated):
    from app.notifications.transport_arbiter import AuthorityGateway
    from app.api.email_service import EmailService
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        gateway=AuthorityGateway(x["repo"],authority.request("status",{})["authority"],authority.request)
        service=EmailService(transport=gateway)
        for index in range(35):
            assert service.send_verification_email("fictional@example.invalid","fictional-marker-"+str(index)).kind=="ACCEPTED"
        status=authority.request("status",{})
        assert status["physical_calls"]==35 and status["active"]==0
        assert len(status["fidelity"])<=32 and len(status["call_ids"])<=32
        assert all(set(entry)=={"message_class","destination_hash","subject_hash","html_hash","headers","expires_ms"} for entry in status["fidelity"])
        assert "fictional-marker-" not in json.dumps(status) and "fictional@example.invalid" not in json.dumps(status)
