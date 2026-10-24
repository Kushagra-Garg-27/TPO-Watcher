"""Non-enumerating default routes with actual guarded R2a ACCOUNT helpers."""
import pytest
from app.api.app import app
from app.api.email_service import EmailService, get_email_service
from app.notifications.transport_arbiter import AuthorityGateway
from app.notifications.transport_protocol import MailOutcome
from app.database.notification_migrator import connect_disposable
from app.database.event_repository import EventRepository
from test_cummins_end_to_end_v2 import integrated
from test_notification_default_factory_consent import default_api, signup_payload
from test_notification_transport_mail_paths import topology, configure


@pytest.mark.parametrize("outcome",["ACCEPTED","NOT_ACCEPTED","UNKNOWN","BUSY","EXPIRED"])
def test_default_account_request_response_does_not_reveal_existence_or_provider_state(integrated,default_api,outcome):
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        gateway=AuthorityGateway(x["repo"],authority.ready["identity"],authority.request)
        service=EmailService(transport=gateway)
        configure(authority,kind=outcome if outcome in ("ACCEPTED","NOT_ACCEPTED","UNKNOWN") else "ACCEPTED")
        if outcome=="BUSY":
            configure(authority,ended=False)
            assert service.send_preference_link_email("busy@example.invalid","fictional") .kind=="ACCEPTED"
        diagnostics=[]
        def injected_service():
            with connect_disposable(x["path"]) as c:
                local=EmailService(transport=AuthorityGateway(EventRepository(c,clock=x["clock"],record_mode=True),authority.ready["identity"],authority.request))
                if outcome=="EXPIRED":
                    send=local.send_preference_link_email
                    local.send_preference_link_email=lambda destination,token:send(destination,token,expires_ms=x["clock"]()-1)
                yield local
                if getattr(local,"last_account_result",None) is not None:diagnostics.append(local.last_account_result)
        app.dependency_overrides[get_email_service]=injected_service
        responses=[]
        for destination in ("fictional-student@example.invalid","unknown@example.invalid"):
            r=default_api.post("/api/v1/preferences/request-link",json={"email":destination})
            assert r.status_code==200
            responses.append(r.json())
        assert responses[0]==responses[1]
        assert "sent" not in responses[0]["message"].lower()
        assert "received" in responses[0]["message"].lower()
        assert diagnostics==[outcome]
        assert authority.request("status",{})["physical_calls"]==(0 if outcome=="EXPIRED" else 1)


@pytest.mark.parametrize("account",["new","unverified","verified","inactive"])
def test_signup_public_request_contract_is_uniform(integrated,default_api,account):
    x=integrated; email="signup-contract@example.invalid"
    if account!="new":
        uid=x["db"].users.create_user(email,2028,"VIT_CE")
        if account in ("verified","inactive"):x["db"].users.set_verified(uid)
        if account=="inactive":x["db"].users.set_unsubscribed(uid)
    response=default_api.post("/api/v1/auth/signup",json=signup_payload(email))
    assert response.status_code==201
    assert response.json()=={"status":"success","message":"Request received. If eligible, an account link will be attempted. Please check your inbox."}


def test_pre_invocation_validation_failure_has_typed_internal_result(integrated):
    x=integrated
    with topology(x,clients=0) as (_,authority,_):
        service=EmailService(transport=AuthorityGateway(x["repo"],authority.ready["identity"],authority.request))
        with pytest.raises(ValueError):
            service.send_verification_email("fictional@example.invalid","fixture",expires_ms=x["clock"]()+86400001)
        assert service.last_account_result=="NOT_INVOKED"
        assert authority.request("status",{})["physical_calls"]==0


def test_default_service_in_v2_without_injected_authority_cannot_fall_back_to_smtp(integrated,default_api,monkeypatch):
    app.dependency_overrides.pop(get_email_service,None)
    response=default_api.post("/api/v1/preferences/request-link",json={"email":"fictional-student@example.invalid"})
    assert response.status_code==200 and "received" in response.json()["message"].lower()
    service=get_email_service()
    def forbidden_fallback(*args,**kwargs):raise AssertionError("legacy SMTP dispatch reached")
    monkeypatch.setattr(service.notifier,"_send_email_to",forbidden_fallback)
    result=service.send_preference_link_email("fictional-student@example.invalid","fictional-marker")
    assert isinstance(result,MailOutcome) and result.kind=="BUSY"


def test_default_mail_factory_does_not_install_or_migrate_a_database(tmp_path,monkeypatch):
    from app.database.sqlite_repository import SQLiteUserRepository
    path=tmp_path/"not-created.sqlite"
    monkeypatch.setattr("app.database.repository.DB_PATH",str(path))
    def forbidden_migration(*args,**kwargs):raise AssertionError("mail factory attempted migration")
    monkeypatch.setattr("app.database.sqlite_repository.run_migrations",forbidden_migration)
    service=get_email_service()
    assert isinstance(service,EmailService) and not path.exists()
