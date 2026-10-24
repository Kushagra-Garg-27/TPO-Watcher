"""Real token/user/preference boundaries with v2 hooks, fictional disposable data."""
import hashlib
import sqlite3
import secrets
from datetime import datetime,timezone,timedelta

import pytest
from fastapi.testclient import TestClient
from app.api.app import app
from app.api import routes_auth,routes_preferences
from app.api.session import create_session_token
from app.config import settings

from test_cummins_end_to_end_v2 import integrated, record


def token(x,kind="UNSUBSCRIBE"):
    raw=secrets.token_urlsafe(32)
    x["db"].tokens.create_token(x["user"],hashlib.sha256(raw.encode()).hexdigest(),kind,
        datetime.now(timezone.utc)+timedelta(minutes=15))
    return raw


@pytest.fixture
def api(integrated):
    x=integrated
    old=dict(app.dependency_overrides)
    app.dependency_overrides[routes_auth.get_db_repos]=lambda:(x["db"].users,x["db"].tokens)
    app.dependency_overrides[routes_preferences.get_db_repos]=lambda:(x["db"].users,x["db"].tokens)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase",["before_seal","after_seal","processing","sending"])
@pytest.mark.parametrize("endpoint",["confirm","one_click","user_repository"])
async def test_unsubscribe_all_current_boundaries_irreversible(integrated,api,phase,endpoint):
    x=integrated
    assert await x["watcher"].check_once()
    repo=x["repo"]
    claim=permit=None
    if phase=="after_seal":
        key=x["c"].execute("SELECT intent_id FROM notification_intents").fetchone()[0]
        handle=repo.claim_outbox(key,"SUBSCRIBER")
        repo.seal(handle)
        x["pipeline"].outbox_handles[(key,"SUBSCRIBER")]=handle
    elif phase in ("processing","sending"):
        x["pipeline"].fanout_once()
        claim=repo.claim_delivery(channel="SUBSCRIBER")
        if phase=="sending":permit=repo.authorize_send(claim)
    raw=token(x)
    if endpoint=="confirm":
        response=api.post("/api/v1/unsubscribe/confirm",json={"token":raw})
        assert response.status_code==200
    elif endpoint=="one_click":
        response=api.post("/api/v1/unsubscribe/one-click?token="+raw,
            content="List-Unsubscribe=One-Click",headers={"Content-Type":"application/x-www-form-urlencoded"})
        assert response.status_code==200
    else:x["db"].users.set_unsubscribed(x["user"])
    assert x["c"].execute("SELECT is_active FROM users WHERE id=?",(x["user"],)).fetchone()[0]==0
    if endpoint!="user_repository":
        assert x["db"].tokens.claim_and_unsubscribe(hashlib.sha256(raw.encode()).hexdigest()) is None
    rows=x["c"].execute("SELECT * FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchall()
    if phase=="before_seal":assert rows==[]
    elif phase=="sending":
        assert rows[0]["status"]=="SENDING" and rows[0]["cancel_requested_ms"] is not None
        assert x["c"].execute("SELECT state FROM smtp_account_gate").fetchone()[0]=="BLOCKED"
        repo.record_fact(permit.invocation_id,"UNKNOWN",fact_key="post-unsubscribe")
        assert repo.recover_delivery(claim.key,unknown_commit=True)=="UNCERTAIN"
        repo.record_fact(permit.invocation_id,"ENDED",fact_key="explicit-fake-end")
    else:assert rows[0]["status"]=="SUPPRESSED"
    # Explicit simulated reactivation retains existing supported year/branch policy.
    repo.change_user(x["user"],is_active=1)
    await x["watcher"].delivery_worker.process_batch_once()
    if phase!="before_seal":
        assert x["c"].execute("SELECT count(*) FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER' AND status='SENT'").fetchone()[0]==0
    x["feed"].records=[record(regEndtime="09:00")]
    assert await x["watcher"].check_once()
    await x["watcher"].delivery_worker.process_batch_once()
    assert x["c"].execute("""SELECT count(*) FROM notification_deliveries_v2 d JOIN notification_intents i USING(intent_id)
        JOIN opportunity_events e ON e.event_id=i.source_event_id WHERE d.channel='SUBSCRIBER' AND e.revision=2 AND d.status='SENT'""").fetchone()[0]==1


@pytest.mark.asyncio
async def test_authenticated_preference_route_off_on_after_seal(integrated,api):
    x=integrated
    assert await x["watcher"].check_once()
    key=x["c"].execute("SELECT intent_id FROM notification_intents").fetchone()[0]
    h=x["repo"].claim_outbox(key,"SUBSCRIBER")
    x["repo"].seal(h)
    x["pipeline"].outbox_handles[(key,"SUBSCRIBER")]=h
    session=create_session_token(user_id=x["user"],secret_key=settings.SECRET_KEY,max_age_seconds=3600)
    api.cookies.set("tpo_session",session)
    for enabled in (False,True):
        response=api.put("/api/v1/preferences",json={"pref_internship":enabled,"pref_placement":False,"pref_ppo":False})
        assert response.status_code==200
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"
    assert await x["watcher"].delivery_worker.process_batch_once()==1
    assert [m["channel"] for m in x["smtp"].messages]==["ADMIN"]


@pytest.mark.asyncio
async def test_new_signup_repository_and_token_verification_hooks(integrated):
    x=integrated
    uid=x["db"].users.create_user("new-fictional@example.invalid",2028,"VIT_IT")
    assert x["c"].execute("SELECT count(*) FROM recipient_associations WHERE user_id=?",(uid,)).fetchone()[0]==1
    assert x["c"].execute("SELECT count(*) FROM subscriber_versions WHERE user_id=?",(uid,)).fetchone()[0]==1
    raw=secrets.token_urlsafe(32)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    x["db"].tokens.create_token(uid,digest,"SIGNUP_VERIFY",datetime.now(timezone.utc)+timedelta(minutes=15))
    assert x["db"].tokens.claim_and_verify(digest)==uid
    assert x["db"].tokens.claim_and_verify(digest) is None
    x["feed"].records=[record(programnew="VIT-BTech-Information Technology")]
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==3


@pytest.mark.asyncio
async def test_missing_sidecar_rolls_back_token_and_unsubscribe(integrated):
    x=integrated
    raw=token(x)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    # Fault fixture removes sidecar within disposable DB; actual token mutation must fail closed.
    x["c"].execute("DROP TRIGGER subscriber_versions_no_delete")
    x["c"].execute("DELETE FROM subscriber_versions WHERE user_id=?",(x["user"],))
    with pytest.raises(sqlite3.IntegrityError,match="missing subscriber versions"):
        x["db"].tokens.claim_and_unsubscribe(digest)
    assert x["c"].execute("SELECT is_active FROM users WHERE id=?",(x["user"],)).fetchone()[0]==1
    assert x["c"].execute("SELECT used_at FROM action_tokens WHERE token_hash=?",(digest,)).fetchone()[0] is None


@pytest.mark.asyncio
async def test_emitted_token_links_finite_and_absent_from_event_snapshot(integrated):
    x=integrated
    assert await x["watcher"].check_once()
    assert await x["watcher"].delivery_worker.process_batch_once()==2
    body=next(m["html"] for m in x["smtp"].messages if m["channel"]=="SUBSCRIBER")
    assert "/unsubscribe#token=" in body and "/preferences/confirm#token=" in body
    import re
    raws=re.findall(r"#token=([^\"<]+)",body)
    assert len(raws)==2
    vault=b" ".join(bytes(r[0]) for r in x["c"].execute("SELECT encrypted_bytes FROM protected_payloads"))
    for raw in raws:
        assert raw.encode() not in vault
        row=x["c"].execute("SELECT expires_at,used_at FROM action_tokens WHERE token_hash=?",(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
        assert row is not None and datetime.fromisoformat(row[0])>datetime.now(timezone.utc) and row[1] is None

