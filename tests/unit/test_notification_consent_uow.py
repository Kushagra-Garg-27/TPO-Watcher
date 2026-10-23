"""Atomic consent hooks through real unbound/default repositories."""
import hashlib
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest
from app.api import routes_auth
from app.database.sqlite_repository import SQLiteUserRepository, SQLiteTokenRepository
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"integration"))
from test_cummins_end_to_end_v2 import integrated


@pytest.mark.asyncio
async def test_unbound_repository_suppresses_before_opt_in_can_restore_eligibility(integrated):
    x=integrated
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    user=SQLiteUserRepository(str(x["path"]))
    for enabled in (False,True):user.update_preferences(x["user"],enabled,False,False)
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


@pytest.mark.asyncio
async def test_unbound_sealed_suppression_needs_no_new_info_send_lifetime(integrated):
    x=integrated
    await x["watcher"].check_once()
    intent=x["c"].execute("SELECT intent_id FROM notification_intents").fetchone()[0]
    x["repo"].seal(x["repo"].claim_outbox(intent,"SUBSCRIBER"))
    SQLiteUserRepository(str(x["path"])).set_unsubscribed(x["user"])
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


@pytest.mark.asyncio
async def test_explicit_legacy_cancellation_is_held_in_v2_without_d10(integrated):
    x=integrated
    await x["watcher"].check_once()
    x["db"].deliveries.enqueue_deliveries([dict(user_id=x["user"],company_id="fictional-company",notification_type="NEW")])
    with pytest.raises(RuntimeError,match="D10"):
        x["db"].deliveries.cancel_pending_deliveries_for_user(x["user"])
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries").fetchone()[0]==1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["preferences","unsubscribe","verify"])
async def test_failure_after_real_suppression_rolls_back_user_token_and_delivery(integrated, monkeypatch, mutation):
    from app.database.event_repository import EventRepository
    x=integrated
    if mutation=="verify":x["repo"].change_user(x["user"],is_verified=0)
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    users=SQLiteUserRepository(str(x["path"]))
    tokens=SQLiteTokenRepository(str(x["path"]))
    digest=hashlib.sha256(b"fictional-control-token").hexdigest()
    kind="SIGNUP_VERIFY" if mutation=="verify" else "UNSUBSCRIBE"
    tokens.create_token(x["user"],digest,kind,datetime.now(timezone.utc)+timedelta(minutes=15))
    before=[tuple(r) for r in x["c"].execute("SELECT status,cancel_requested_ms FROM notification_deliveries_v2 ORDER BY delivery_id")]
    original=EventRepository.sync_consent
    def fail_after_suppression(self,*args,**kwargs):
        original(self,*args,**kwargs)
        raise RuntimeError("controlled suppression rollback")
    monkeypatch.setattr(EventRepository,"sync_consent",fail_after_suppression)
    with pytest.raises(RuntimeError,match="controlled suppression rollback"):
        if mutation=="preferences":users.update_preferences(x["user"],False,False,False)
        elif mutation=="verify":tokens.claim_and_verify(digest)
        else:tokens.claim_and_unsubscribe(digest)
    assert [tuple(r) for r in x["c"].execute("SELECT status,cancel_requested_ms FROM notification_deliveries_v2 ORDER BY delivery_id")]==before
    assert x["c"].execute("SELECT used_at FROM action_tokens WHERE token_hash=?",(digest,)).fetchone()[0] is None
    assert x["c"].execute("SELECT pref_internship FROM user_preferences WHERE user_id=?",(x["user"],)).fetchone()[0]==1
    assert tuple(x["c"].execute("SELECT is_active,is_verified FROM users WHERE id=?",(x["user"],)).fetchone())==(1,0 if mutation=="verify" else 1)


@pytest.mark.parametrize("sidecar", ["version","association","preferences"])
def test_missing_subscriber_sidecar_fails_closed_without_partial_token_use(integrated, sidecar):
    x=integrated; digest=hashlib.sha256(b"fictional-sidecar-token").hexdigest()
    x["db"].tokens.create_token(x["user"],digest,"MANAGE_PREFS",datetime.now(timezone.utc)+timedelta(minutes=15))
    if sidecar=="version":
        x["c"].execute("DROP TRIGGER subscriber_versions_no_delete")
        x["c"].execute("DELETE FROM subscriber_versions WHERE user_id=?",(x["user"],))
    elif sidecar=="association":
        x["c"].execute("DROP TRIGGER association_erase")
        x["c"].execute("DELETE FROM recipient_associations WHERE user_id=?",(x["user"],))
    else:x["c"].execute("DELETE FROM user_preferences WHERE user_id=?",(x["user"],))
    tokens=SQLiteTokenRepository(str(x["path"]))
    with pytest.raises((sqlite3.IntegrityError,RuntimeError)):
        tokens.claim_action_token(digest,"MANAGE_PREFS")
    assert x["c"].execute("SELECT used_at FROM action_tokens WHERE token_hash=?",(digest,)).fetchone()[0] is None


@pytest.mark.asyncio
async def test_runtime_off_does_not_lose_suppression_or_enable_modes(integrated):
    x=integrated
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    x["pipeline"].shutdown()
    before=tuple(x["c"].execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())
    users=SQLiteUserRepository(str(x["path"]))
    for enabled in (False,True):users.update_preferences(x["user"],enabled,False,False)
    assert tuple(x["c"].execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())==before
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


@pytest.mark.asyncio
async def test_v2_unsubscribe_preserves_legacy_unsent_history_for_pending_d10(integrated):
    x=integrated
    await x["watcher"].check_once(); x["pipeline"].fanout_once()
    x["db"].deliveries.enqueue_deliveries([dict(user_id=x["user"],company_id="fictional-company",notification_type="NEW")])
    SQLiteUserRepository(str(x["path"])).set_unsubscribed(x["user"])
    assert x["c"].execute("SELECT count(*) FROM notification_deliveries").fetchone()[0]==1
    assert x["c"].execute("SELECT status FROM notification_deliveries_v2 WHERE channel='SUBSCRIBER'").fetchone()[0]=="SUPPRESSED"


def test_partial_runtime_is_rejected_without_factory_activation(integrated,monkeypatch):
    x=integrated
    monkeypatch.setattr("app.database.repository.DB_PATH",str(x["path"]))
    before=tuple(x["c"].execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())
    x["c"].execute("UPDATE notification_runtime SET config_version='unsupported'")
    with pytest.raises(RuntimeError,match="runtime"):
        routes_auth.get_db_repos()
    assert tuple(x["c"].execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())==before


def test_legacy_repository_created_before_schema_still_attaches_on_later_mutation(tmp_path):
    from app.database.repository import DatabaseRepository
    from app.database.notification_migrator import connect_disposable,create_schema
    path=tmp_path/"late-runtime.sqlite"
    db=DatabaseRepository(str(path))
    uid=db.users.create_user("late@example.invalid",2028,"VIT_CE")
    with connect_disposable(path) as c:
        create_schema(c,now_ms=1000)
        before=tuple(c.execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())
        db.users.update_preferences(uid,False,False,False)
        assert tuple(c.execute("SELECT writer_mode,fanout_mode,send_mode,epoch FROM notification_runtime").fetchone())==before
        assert c.execute("SELECT count(*) FROM recipient_associations WHERE user_id=?",(uid,)).fetchone()[0]==1


def test_legacy_absent_runtime_factory_keeps_schema_and_modes_absent(tmp_path,monkeypatch):
    from app.database.repository import DatabaseRepository
    path=tmp_path/"legacy-only.sqlite";DatabaseRepository(str(path))
    monkeypatch.setattr("app.database.repository.DB_PATH",str(path))
    users,tokens=routes_auth.get_db_repos()
    uid=users.create_user("legacy@example.invalid",2028,"VIT_CE")
    users.set_unsubscribed(uid)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0]==0


def test_missing_consent_epoch_trigger_fails_before_mutating_user(integrated):
    x=integrated
    x["c"].execute("DROP TRIGGER subscriber_epochs")
    with pytest.raises(RuntimeError,match="consent.*trigger"):
        SQLiteUserRepository(str(x["path"])).set_unsubscribed(x["user"])
    assert x["c"].execute("SELECT is_active FROM users WHERE id=?",(x["user"],)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_preserved_legacy_pending_cannot_replay_after_opt_out_then_opt_in(integrated):
    x=integrated
    await x["watcher"].check_once()
    x["db"].deliveries.enqueue_deliveries([dict(user_id=x["user"],company_id="fictional-company",notification_type="NEW")])
    SQLiteUserRepository(str(x["path"])).set_unsubscribed(x["user"])
    x["repo"].change_user(x["user"],is_active=1)  # fixture only
    assert x["db"].deliveries.claim_pending_batch()==[]
    assert tuple(x["c"].execute("SELECT status,attempt_count FROM notification_deliveries").fetchone())==("PENDING",0)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage",["claim","authorize"])
async def test_sticky_cancellation_cannot_acquire_another_permission(integrated,stage):
    from app.database.notification_migrator import atomic
    x=integrated;repo=x["repo"]
    await x["watcher"].check_once();x["pipeline"].fanout_once()
    claim=repo.claim_delivery(channel="SUBSCRIBER")
    if stage=="claim":
        permit=repo.authorize_send(claim)
        with atomic(x["c"]):repo._suppress(dict(x["c"].execute("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(claim.key,)).fetchone()),"CONTROLLED_OLD_CANCELLATION")
        repo.record_fact(permit.invocation_id,"NOT_ACCEPTED",fact_key="controlled-old-negative")
        repo.record_fact(permit.invocation_id,"ENDED",fact_key="controlled-old-end")
        # Persist the valid historical state produced by the old T7 bug.
        with atomic(x["c"]):repo._finish(dict(x["c"].execute("SELECT * FROM notification_deliveries_v2 WHERE delivery_id=?",(claim.key,)).fetchone()),"PENDING",reason="OLD_NEGATIVE_ACK")
        x["clock"].now+=11
        assert repo.claim_delivery(channel="SUBSCRIBER") is None
    else:
        # Historical PROCESSING state from an older claim that ignored the bit.
        with atomic(x["c"]):x["c"].execute("UPDATE notification_deliveries_v2 SET cancel_requested_ms=? WHERE delivery_id=?",(x["clock"](),claim.key))
        assert repo.authorize_send(claim) is None
        assert x["c"].execute("SELECT count(*) FROM transport_invocations").fetchone()[0]==0
    row=x["c"].execute("SELECT status,attempt_count FROM notification_deliveries_v2 WHERE delivery_id=?",(claim.key,)).fetchone()
    assert tuple(row)==("SUPPRESSED",1)
