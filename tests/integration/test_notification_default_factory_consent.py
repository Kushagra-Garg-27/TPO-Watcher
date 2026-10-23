"""Real default API factories; fictional disposable v2 state, no factory overrides."""
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from app.api.app import app
from app.api import routes_auth, routes_preferences
from app.api.email_service import get_email_service
from app.api.session import create_session_token
from app.config import settings
from test_cummins_end_to_end_v2 import integrated, record


class MailSink:
    def __init__(self): self.tokens=[]
    def send_verification_email(self, destination, token):
        self.tokens.append(("SIGNUP_VERIFY", token)); return True
    def send_preference_link_email(self, destination, token):
        self.tokens.append(("MANAGE_PREFS", token)); return True


@pytest.fixture
def default_api(integrated, monkeypatch):
    x=integrated
    monkeypatch.setattr("app.database.repository.DB_PATH", str(x["path"]))
    old=dict(app.dependency_overrides)
    app.dependency_overrides.pop(routes_auth.get_db_repos, None)
    app.dependency_overrides.pop(routes_preferences.get_db_repos, None)
    sink=MailSink()
    app.dependency_overrides[get_email_service]=lambda:sink
    try:
        with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
            client.mail_sink=sink
            yield client
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(old)


def issue(x, kind="UNSUBSCRIBE", user=None, expires=None):
    raw=secrets.token_urlsafe(32)
    x["db"].tokens.create_token(user or x["user"], hashlib.sha256(raw.encode()).hexdigest(), kind,
        expires or datetime.now(timezone.utc)+timedelta(minutes=15))
    return raw


def unsubscribe(client, variant, raw):
    if variant=="confirm":return client.post("/api/v1/unsubscribe/confirm", json={"token":raw})
    if variant=="legacy":return client.post("/api/v1/unsubscribe?token="+raw)
    return client.post("/api/v1/unsubscribe/one-click?token="+raw, content="List-Unsubscribe=One-Click",
        headers={"Content-Type":"application/x-www-form-urlencoded"})


def session(client, user):
    client.cookies.set("tpo_session", create_session_token(user_id=user, secret_key=settings.SECRET_KEY, max_age_seconds=3600))


def signup_payload(email="new-default@example.invalid"):
    return dict(email=email, graduation_year=2028, branch_canonical="VIT_CE",
        pref_internship=True, pref_placement=True, pref_ppo=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["confirm","legacy","one_click"])
@pytest.mark.parametrize("phase", ["sealed","pending","processing","sending"])
async def test_default_unsubscribe_preserves_irreversible_obligation(integrated, default_api, variant, phase):
    x=integrated; repo=x["repo"]
    await x["watcher"].check_once()
    if phase=="sealed":
        intent=x["c"].execute("SELECT intent_id FROM notification_intents").fetchone()[0]
        repo.seal(repo.claim_outbox(intent,"SUBSCRIBER"))
    else:x["pipeline"].fanout_once()
    claim=permit=None
    if phase in ("processing","sending"):
        claim=repo.claim_delivery(channel="SUBSCRIBER")
        if phase=="sending":permit=repo.authorize_send(claim)
    raw=issue(x)
    assert unsubscribe(default_api,variant,raw).status_code==200
    row=x["c"].execute("SELECT status,cancel_requested_ms FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()
    if phase=="sending":
        assert row[0]=="SENDING" and row[1] is not None
        assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"
        repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="request-cancel-is-not-recall")
        assert repo.recover_delivery(claim.key,unknown_commit=True)=="UNCERTAIN"
        repo.record_fact(permit.invocation_id,"ENDED",fact_key="positive-test-end")
    else:assert row is not None and row[0]=="SUPPRESSED"
    assert unsubscribe(default_api,variant,raw).status_code==400
    repo.change_user(x["user"],is_active=1)  # fixture action, no reactivation endpoint
    if phase!="sending":
        assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


@pytest.mark.asyncio
async def test_default_preferences_unrelated_change_and_off_on_restart(integrated, default_api):
    x=integrated
    x["feed"].records=[record(placementtype="Internship",programnew="VIT-BTech-Computer Engineering")]
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    session(default_api,x["user"])
    before=x["c"].execute("SELECT profile_epoch FROM subscriber_versions WHERE user_id=?",(x["user"],)).fetchone()[0]
    for enabled,want in ((True,"PENDING"),(False,"SUPPRESSED"),(True,"SUPPRESSED")):
        r=default_api.put("/api/v1/preferences",json=dict(pref_internship=enabled,pref_placement=False,pref_ppo=False))
        assert r.status_code==200
        assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]==want
    assert x["c"].execute("SELECT profile_epoch FROM subscriber_versions WHERE user_id=?",(x["user"],)).fetchone()[0]==before+3
    # Reconstruct the actual factory, then mutate on a new thread-owned connection.
    user_repo,_=routes_preferences.get_db_repos()
    user_repo.update_preferences(x["user"],True,False,False)
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"
    assert x["c"].execute("SELECT count(*) FROM delivery_attempts WHERE delivery_id IN (SELECT delivery_id FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER')").fetchone()[0]==0


def test_default_signup_association_and_verification(integrated, default_api):
    x=integrated
    assert default_api.post("/api/v1/auth/signup",json=signup_payload()).status_code==201
    uid=x["c"].execute("SELECT id FROM users WHERE email='new-default@example.invalid'").fetchone()[0]
    assert x["c"].execute("SELECT count(*) FROM recipient_associations WHERE user_id=?",(uid,)).fetchone()[0]==1
    assert x["c"].execute("SELECT count(*) FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()[0]==1
    raw=default_api.mail_sink.tokens[-1][1]
    assert default_api.post("/api/v1/auth/verify/confirm",json={"token":raw}).status_code==200
    assert default_api.post("/api/v1/auth/verify/confirm",json={"token":raw}).status_code==400
    assert x["c"].execute("SELECT is_verified FROM users WHERE id=?",(uid,)).fetchone()[0]==1


@pytest.mark.parametrize("existing", [False,True])
def test_default_signup_token_failure_rolls_back_identity_preferences_and_supersession(integrated, default_api, monkeypatch, existing):
    from app.database.sqlite_repository import SQLiteTokenRepository
    x=integrated; email="rollback-default@example.invalid"
    if existing:
        uid=x["db"].users.create_user(email,2028,"VIT_CE")
        issue(x,"SIGNUP_VERIFY",user=uid)
    before=x["c"].execute("SELECT count(*) FROM action_tokens").fetchone()[0]
    original=SQLiteTokenRepository.create_token
    def fail_after_insert(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError("controlled token insert failure")
    monkeypatch.setattr(SQLiteTokenRepository,"create_token",fail_after_insert)
    payload=signup_payload(email);payload["pref_internship"]=False
    assert default_api.post("/api/v1/auth/signup",json=payload).status_code==500
    assert x["c"].execute("SELECT count(*) FROM action_tokens").fetchone()[0]==before
    if existing:
        assert x["c"].execute("SELECT pref_internship FROM user_preferences WHERE user_id=?",(uid,)).fetchone()[0]==1
        assert x["c"].execute("SELECT used_at FROM action_tokens WHERE user_id=?",(uid,)).fetchone()[0] is None
    else:assert x["c"].execute("SELECT count(*) FROM users WHERE email=?",(email,)).fetchone()[0]==0


def test_existing_unverified_signup_supersedes_then_activates_same_association(integrated,default_api):
    x=integrated; email="unverified-default@example.invalid"
    uid=x["db"].users.create_user(email,2028,"VIT_CE")
    old=issue(x,"SIGNUP_VERIFY",user=uid)
    recipient=x["c"].execute("SELECT recipient_key FROM recipient_associations WHERE user_id=?",(uid,)).fetchone()[0]
    payload=signup_payload(email);payload["pref_ppo"]=False
    assert default_api.post("/api/v1/auth/signup",json=payload).status_code==201
    fresh=default_api.mail_sink.tokens[-1][1]
    assert default_api.post("/api/v1/auth/verify/confirm",json={"token":old}).status_code==400
    assert default_api.post("/api/v1/auth/verify/confirm",json={"token":fresh}).status_code==200
    assert x["c"].execute("SELECT recipient_key FROM recipient_associations WHERE user_id=?",(uid,)).fetchone()[0]==recipient
    assert tuple(x["c"].execute("SELECT is_active,is_verified FROM users WHERE id=?",(uid,)).fetchone())==(1,1)


def test_default_preference_exchange_is_single_use_and_owns_session(integrated,default_api):
    x=integrated
    first=issue(x,"MANAGE_PREFS"); second=issue(x,"MANAGE_PREFS")
    response=default_api.post("/api/v1/preferences/confirm",json={"token":first})
    assert response.status_code==200 and "tpo_session" in response.cookies
    assert default_api.get("/api/v1/preferences").json()["email"]=="fictional-student@example.invalid"
    assert default_api.post("/api/v1/preferences/confirm",json={"token":first}).status_code==400
    assert default_api.post("/api/v1/preferences/confirm",json={"token":second}).status_code==400


def test_exchange_failure_rolls_back_consumption_and_supersession(integrated,default_api,monkeypatch):
    from app.database.sqlite_repository import SQLiteTokenRepository
    x=integrated; raw=issue(x,"MANAGE_PREFS"); issue(x,"MANAGE_PREFS")
    original=SQLiteTokenRepository.invalidate_user_tokens
    def fail_after_invalidate(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError("controlled exchange failure")
    monkeypatch.setattr(SQLiteTokenRepository,"invalidate_user_tokens",fail_after_invalidate)
    assert default_api.post("/api/v1/preferences/confirm",json={"token":raw}).status_code==500
    assert "tpo_session" not in default_api.cookies
    assert x["c"].execute("SELECT count(*) FROM action_tokens WHERE used_at IS NOT NULL").fetchone()[0]==0


@pytest.mark.parametrize("state",["inactive","unverified","expired","wrong_kind"])
def test_preference_token_cannot_authorize_ineligible_or_wrong_identity_state(integrated,default_api,state):
    x=integrated
    raw=issue(x,"SIGNUP_VERIFY" if state=="wrong_kind" else "MANAGE_PREFS",
        expires=datetime.now(timezone.utc)-timedelta(seconds=1) if state=="expired" else None)
    if state=="inactive":x["repo"].change_user(x["user"],is_active=0)
    if state=="unverified":x["repo"].change_user(x["user"],is_verified=0)
    assert default_api.post("/api/v1/preferences/confirm",json={"token":raw}).status_code==400
    assert "tpo_session" not in default_api.cookies
    assert x["c"].execute("SELECT used_at FROM action_tokens WHERE token_hash=?",(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()[0] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("started",[False,True])
async def test_unsubscribe_competes_with_actual_authority_without_claiming_recall(integrated,default_api,started):
    from test_notification_transport_multiprocess import topology, configure
    from app.notifications.transport_arbiter import AuthorityGateway, identity_for
    from app.notifications.transport_protocol import EphemeralMail
    x=integrated
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    with topology(x,clients=0) as (_,authority,_):
        gateway=AuthorityGateway(x["repo"],authority.ready["identity"],authority.request)
        assert gateway.prepare_slot("SUBSCRIBER")
        claim=x["repo"].claim_delivery(channel="SUBSCRIBER")
        permit=x["repo"].authorize_send(claim)
        mail=EphemeralMail("SUBSCRIBER","fictional-student@example.invalid","Fixture","<p>Fixture</p>",x["clock"]()+1000,
            {"List-Unsubscribe":"<https://watcher.example.invalid/unsubscribe>","List-Unsubscribe-Post":"List-Unsubscribe=One-Click"})
        grant=authority.request("admit",gateway.ticket(permit,mail))
        configure(authority,ended=False)
        if started:assert authority.request("consume",grant)["kind"]=="ACCEPTED"
        assert unsubscribe(default_api,"confirm",issue(x)).status_code==200
        if not started:
            with pytest.raises(RuntimeError):authority.request("consume",grant)
            assert authority.request("status",{})["physical_calls"]==0
        else:
            status=authority.request("status",{})
            assert status["physical_calls"]==1 and status["active"]==1 and status["gate"]=="BLOCKED"
            assert x["repo"].acknowledge(claim)=="SENT"
            assert authority.request("status",{})["active"]==1
        row=x["c"].execute("SELECT status,cancel_requested_ms FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()
        assert row[1] is not None
        authority.request("end",identity_for(x["repo"],permit.invocation_id).to_wire())
        assert authority.request("status",{})["gate"]=="FREE"


@pytest.mark.parametrize("flow",["request_link","verified_signup"])
def test_account_token_supersession_rolls_back_on_insert_failure(integrated,default_api,monkeypatch,flow):
    from app.database.sqlite_repository import SQLiteTokenRepository
    x=integrated;raw=issue(x,"MANAGE_PREFS")
    original=SQLiteTokenRepository.create_token
    def fail(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError("controlled account token rollback")
    monkeypatch.setattr(SQLiteTokenRepository,"create_token",fail)
    if flow=="request_link":response=default_api.post("/api/v1/preferences/request-link",json={"email":"fictional-student@example.invalid"})
    else:response=default_api.post("/api/v1/auth/signup",json=signup_payload("fictional-student@example.invalid"))
    assert response.status_code==500
    assert x["c"].execute("SELECT count(*) FROM action_tokens").fetchone()[0]==1
    assert x["c"].execute("SELECT used_at FROM action_tokens WHERE token_hash=?",(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()[0] is None


@pytest.mark.asyncio
async def test_closed_database_and_new_api_client_preserve_suppressed_history(integrated,default_api):
    from app.database.repository import DatabaseRepository
    from app.database.notification_migrator import connect_disposable
    from app.database.event_repository import EventRepository
    x=integrated
    await x["watcher"].check_once();x["pipeline"].fanout_once()
    assert unsubscribe(default_api,"confirm",issue(x)).status_code==200
    x["c"].close()
    restarted=DatabaseRepository(str(x["path"]))
    with connect_disposable(x["path"]) as connection:
        repo=EventRepository(connection,clock=x["clock"],record_mode=True)
        repo.change_user(x["user"],is_active=1)  # fixture only
        restarted.users.update_preferences(x["user"],True,True,True)
        assert connection.execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"
        with TestClient(app,base_url="https://testserver") as client:
            session(client,x["user"])
            assert client.get("/api/v1/preferences").status_code==200
            assert client.put("/api/v1/preferences",json=dict(pref_internship=True,pref_placement=True,pref_ppo=True)).status_code==200
        assert connection.execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,want",[("NOT_ACCEPTED","SUPPRESSED"),("ACCEPTED","SENT"),("UNKNOWN","UNCERTAIN")])
async def test_sending_preference_off_on_keeps_cancellation_after_final_outcome(integrated,default_api,kind,want):
    from test_notification_transport_multiprocess import topology, configure
    from app.notifications.transport_arbiter import AuthorityGateway,identity_for
    from app.notifications.transport_protocol import EphemeralMail
    x=integrated
    await x["watcher"].check_once();x["pipeline"].fanout_once()
    session(default_api,x["user"])
    with topology(x,clients=1) as (_,authority,clients):
        # Complete the independent ADMIN obligation so subsequent worker calls
        # can only attempt this subscriber obligation.
        assert clients[0].request("worker",{"channel":"ADMIN"},relay=authority.request)["sent"]==1
        assert authority.request("status",{})["physical_calls"]==1
        gateway=AuthorityGateway(x["repo"],authority.ready["identity"],authority.request)
        assert gateway.prepare_slot("SUBSCRIBER")
        claim=x["repo"].claim_delivery(channel="SUBSCRIBER")
        permit=x["repo"].authorize_send(claim)
        mail=EphemeralMail("SUBSCRIBER","fictional-student@example.invalid","Fixture","<p>Fixture</p>",x["clock"]()+1000,
            {"List-Unsubscribe":"<https://watcher.example.invalid/unsubscribe>","List-Unsubscribe-Post":"List-Unsubscribe=One-Click"})
        configure(authority,kind=kind,ended=False)
        grant=authority.request("admit",gateway.ticket(permit,mail))
        assert authority.request("consume",grant)["kind"]==kind
        for enabled in (False,True):
            assert default_api.put("/api/v1/preferences",json=dict(pref_internship=enabled,pref_placement=enabled,pref_ppo=enabled)).status_code==200
        authority.request("end",identity_for(x["repo"],permit.invocation_id).to_wire())
        assert x["repo"].acknowledge(claim)==want
        x["clock"].now+=11
        for child in (authority,*clients):child.request("advance",{"now":x["clock"]()})
        assert clients[0].request("worker",{"channel":"SUBSCRIBER"},relay=authority.request)["sent"]==0
        assert authority.request("status",{})["physical_calls"]==2
        row=x["c"].execute("SELECT status,attempt_count,cancel_requested_ms FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()
        assert row[0]==want and row[1]==1 and row[2] is not None
