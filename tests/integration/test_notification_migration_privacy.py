"""Real migration/consent paths, fictional history and external test custody only."""
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from app.database.repository import DatabaseRepository
from app.database.notification_migrator import connect_disposable, create_schema, SchemaError
from app.database.event_repository import EventRepository, OperatorApproval
from app.monitoring.notification_pipeline import NotificationPipeline, V2Options
from test_cummins_end_to_end_v2 import record
from tests.unit.test_notification_payload_key_lifecycle import Authority, implementation

STAMP = "2026-10-01T00:00:00+00:00"
NOW = 1792022400000
LEGACY = ("companies", "users", "user_preferences", "action_tokens", "notification_deliveries", "pending_notifications")


def legacy_digest(c):
    return hashlib.sha256(json.dumps({t:[tuple(r) for r in c.execute("SELECT * FROM "+t+" ORDER BY 1")] for t in LEGACY}, sort_keys=True).encode()).hexdigest()


def history(path):
    db = DatabaseRepository(str(path))
    old = record(id="history-a")
    db.upsert_company(old)
    for index in range(1,4): db.upsert_company(record(id='history-'+str(index)))
    with sqlite3.connect(path) as c:
        c.execute("UPDATE companies SET first_seen_at=?,last_seen_at=?", (STAMP, STAMP))
        c.execute("INSERT INTO users(email,graduation_year,branch_canonical,is_verified,is_active,created_at) VALUES('fictional@example.invalid',2028,'VIT_CE',1,1,?)",(STAMP,))
        c.execute("INSERT INTO user_preferences VALUES(1,1,1,1,?)",(STAMP,))
        c.execute("INSERT INTO action_tokens(user_id,token_hash,token_type,expires_at,created_at) VALUES(1,?,'SIGNUP_VERIFY',?,?)",('a'*64, '2026-11-01T00:00:00+00:00',STAMP))
        for index,status in enumerate(("SENT","FAILED","PENDING","PROCESSING")):
            # Distinct company identities preserve the existing uniqueness contract.
            cid = 'history-a' if index==0 else 'history-'+str(index)
            if index:
                c.execute("UPDATE companies SET first_seen_at=?,last_seen_at=? WHERE id=?",(STAMP,STAMP,cid))
            c.execute("INSERT INTO notification_deliveries(user_id,company_id,notification_type,status,created_at) VALUES(1,?,'NEW',?,?)",(cid,status,STAMP))
        c.execute("INSERT INTO pending_notifications(company_id,notification_type,changes_json,created_at) VALUES('history-a','UPDATE','{}',?)",(STAMP,))
    return db


@pytest.fixture
def encrypted(tmp_path):
    Codec=implementation()
    from app.database.protected_payloads import protected_scope
    path=tmp_path/'history.sqlite'
    db=history(path)
    authority=Authority(tmp_path/'authority.json')
    codec=Codec(authority,str(path))
    with protected_scope(path,codec), connect_disposable(path) as c:
        yield dict(path=path,db=db,c=c,authority=authority,codec=codec)


def test_v1_v2_v4_v5_encrypted_migration_preserves_history_and_never_replays(encrypted):
    x=encrypted; c=x['c']; before=legacy_digest(c)
    create_schema(c,now_ms=NOW)
    assert legacy_digest(c)==before
    assert {tuple(r) for r in c.execute('SELECT revision,head_event_id FROM opportunity_state')}=={(0,None)}
    assert c.execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==0
    assert c.execute('SELECT count(*) FROM notification_intents').fetchone()[0]==0
    assert {r[0] for r in c.execute('SELECT decision FROM legacy_notification_dispositions')}=={'HISTORICAL_ONLY','HOLD_REVIEW'}
    assert c.execute("SELECT count(*) FROM legacy_notification_dispositions WHERE decision='HOLD_REVIEW'").fetchone()[0]==3
    assert tuple(c.execute('SELECT writer_mode,fanout_mode,send_mode FROM notification_runtime').fetchone())==('OFF','OFF','OFF')
    assert all(bytes(r[0]).startswith(b'TPO-AEAD-1:') for r in c.execute('SELECT encrypted_bytes FROM protected_payloads'))
    assert x['db'].deliveries.claim_pending_batch(10)==[]
    create_schema(c,now_ms=NOW+10)
    assert legacy_digest(c)==before
    assert c.execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0


@pytest.mark.parametrize('mutation',[
    "UPDATE companies SET id=' ' WHERE id='history-a'",
    "UPDATE companies SET first_seen_at='ambiguous'",
    "UPDATE companies SET first_seen_at='2026-10-15T05:00:00+05:30'",
    "UPDATE companies SET first_seen_at='2026-10-15T00:00:00Z'",
    "UPDATE companies SET raw_data_json='broken'",
    "UPDATE companies SET is_active='Unknown'",
    "UPDATE users SET is_active=2",
    "DELETE FROM user_preferences",
    "UPDATE user_preferences SET pref_ppo=-1",
    "UPDATE action_tokens SET token_type='UNKNOWN'",
    "UPDATE action_tokens SET expires_at='invalid'",
    "UPDATE notification_deliveries SET status='UNKNOWN'",
    "UPDATE notification_deliveries SET notification_type='UNKNOWN'",
    "UPDATE notification_deliveries SET attempt_count=0.5",
    "UPDATE pending_notifications SET company_id='missing'",
])
def test_v3_malformed_history_refused_atomically(encrypted,mutation):
    c=encrypted['c']; c.execute('PRAGMA foreign_keys=OFF')
    c.execute(mutation);c.execute('PRAGMA foreign_keys=ON')
    before=legacy_digest(c)
    with pytest.raises(SchemaError): create_schema(c,now_ms=NOW)
    assert legacy_digest(c)==before
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0]==0


def test_v10_hard_delete_and_erased_identity_rebinding_restricted(encrypted):
    c=encrypted['c'];create_schema(c,now_ms=NOW)
    with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM users WHERE id=1')
    with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM subscriber_versions WHERE user_id=1')
    repo=EventRepository(c,clock=lambda:NOW)
    target=c.execute('SELECT snapshot_payload_id FROM opportunity_state LIMIT 1').fetchone()[0]
    assert repo.erase_payload(target,OperatorApproval('ERASE',target,'erase-a'))=='COMPLETE'
    assert c.execute('SELECT count(*) FROM payload_tombstones WHERE payload_id=?',(target,)).fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO protected_payloads VALUES(?,X'00','replacement')",(target,))
    assert c.execute('SELECT email FROM users WHERE id=1').fetchone()[0]=='fictional@example.invalid'


@pytest.mark.parametrize('stage',['prepared','notification_runtime','seeded','before_commit','committed'])
def test_v13_migration_interruptions_resolve_exact_commit(encrypted,stage):
    x=encrypted;c=x['c'];before=legacy_digest(c)
    def fail(name):
        if name==stage:raise RuntimeError('fictional migration interruption')
    with pytest.raises(RuntimeError):create_schema(c,now_ms=NOW,fault=fail)
    committed=c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_schema_migrations'").fetchone()[0]
    assert bool(committed)==(stage=='committed')
    if committed:
        identifiers=[tuple(r) for r in c.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]
        assert c.execute("SELECT value FROM system_state WHERE key='r2bc:migration_manifest'").fetchone() is not None
    create_schema(c,now_ms=NOW)
    assert legacy_digest(c)==before
    if committed:assert [tuple(r) for r in c.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]==identifiers
    assert c.execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==0


@pytest.mark.parametrize('stage',['before_prepare','prepared','before_database','database_committed','before_revocation','revoked'])
def test_v9_v13_erasure_crash_recovery_uses_external_action(encrypted,stage):
    from app.database.protected_payloads import ProtectedPayloads
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    repo=EventRepository(c,clock=lambda:NOW)
    target=c.execute('SELECT snapshot_payload_id FROM opportunity_state LIMIT 1').fetchone()[0]
    approval=OperatorApproval('ERASE',target,'erase-crash')
    def fail(name):
        if name==stage:raise RuntimeError('fictional erasure interruption')
    with pytest.raises(RuntimeError):repo.erase_payload(target,approval,fault=fail)
    # Reconstruct external custody from disk, not surviving in-memory fixture state.
    authority=Authority(x['authority'].path)
    x['codec'].provider=authority
    assert repo.erase_payload(target,approval)=='COMPLETE'
    action=authority.erasure(approval.reference)
    assert action['acknowledged'] and authority.state['keys'][action['reference']]['revoked']
    assert c.execute('SELECT count(*) FROM protected_payloads WHERE payload_id=?',(target,)).fetchone()[0]==0


@pytest.mark.parametrize('failure',['revoke','ack'])
def test_v9_v14_revocation_uncertainty_never_reports_completed(encrypted,failure):
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW);repo=EventRepository(c,clock=lambda:NOW)
    target=c.execute('SELECT snapshot_payload_id FROM opportunity_state LIMIT 1').fetchone()[0]
    approval=OperatorApproval('ERASE',target,'erase-uncertain')
    x['authority'].failure=failure
    assert repo.erase_payload(target,approval)=='REVOCATION_PENDING'
    assert c.execute('SELECT count(*) FROM protected_payloads WHERE payload_id=?',(target,)).fetchone()[0]==0
    x['codec'].provider=Authority(x['authority'].path)
    assert repo.erase_payload(target,approval)=='COMPLETE'


def test_v9_admin_destination_erasure_has_independent_key_journal(encrypted):
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW);repo=EventRepository(c,clock=lambda:NOW)
    recipient=repo.add_admin()
    blob=c.execute('SELECT protected_destination FROM recipient_associations WHERE recipient_key=?',(recipient,)).fetchone()[0]
    assert bytes(blob).startswith(b'TPO-AEAD-1:')
    assert repo.erase_recipient(recipient,OperatorApproval('DEIDENTIFY',recipient,'erase-admin'))=='COMPLETE'
    assert x['authority'].erasure('erase-admin')['acknowledged']
    assert c.execute('SELECT count(*) FROM recipient_handles WHERE recipient_key=?',(recipient,)).fetchone()[0]==1


def test_v12_unbound_encrypted_factory_and_marker_downgrade_refused(encrypted):
    from app.database.protected_payloads import KeyUnavailable,protected_scope
    from app.database.notification_compatibility import repository_factory
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    c.execute("DELETE FROM system_state WHERE key='r2bc:protected'")
    with pytest.raises(KeyUnavailable):repository_factory(str(x['path']))


def test_v13_allocation_failure_does_not_install_partial_schema(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];before=legacy_digest(c);x['authority'].failure='allocate'
    with pytest.raises(KeyUnavailable):create_schema(c,now_ms=NOW)
    assert legacy_digest(c)==before
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0]==0


def test_v9_admin_revocation_cannot_complete_after_external_journal_loss(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW);repo=EventRepository(c,clock=lambda:NOW)
    target=repo.add_admin();approval=OperatorApproval('DEIDENTIFY',target,'admin-pending')
    x['authority'].failure='revoke'
    assert repo.erase_recipient(target,approval)=='REVOCATION_PENDING'
    x['authority'].failure=None
    del x['authority'].state['actions'][approval.reference]
    with pytest.raises(KeyUnavailable):repo.erase_recipient(target,approval)


def test_v13_external_key_operations_never_run_in_write_units(encrypted):
    x=encrypted;c=x['c'];authority=x['authority']
    for name in ('allocate','unwrap','expected','prepare_erasure','revoke'):
        original=getattr(authority,name)
        def checked(*args,_original=original,**kwargs):
            assert not c.in_transaction
            return _original(*args,**kwargs)
        setattr(authority,name,checked)
    create_schema(c,now_ms=NOW)
    repo=EventRepository(c,clock=lambda:NOW);repo.activate()
    owner=repo.acquire_intake()
    batch=repo.admit(owner,admission_key='outside-write',items=[dict(id='fresh',state='OPEN',branch='VIT_CE')],complete=False)
    repo.apply_item(owner,batch,0);repo.finalize_batch(owner,batch)
    target=c.execute("SELECT snapshot_payload_id FROM opportunity_state WHERE legacy_company_id='fresh'").fetchone()[0]
    assert repo.erase_payload(target,OperatorApproval('ERASE',target,'outside-write-erase'))=='COMPLETE'


def test_v12_v14_stale_terminal_recovery_preserves_accepted_and_optout(encrypted):
    from app.database.key_provider import KeyUnavailable
    from app.database.protected_payloads import protected_scope,ProtectedPayloads
    from app.database.event_repository import FakeTransport,Fenced,CommitUnknown
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    repo=EventRepository(c,clock=lambda:NOW);repo.activate()
    owner=repo.acquire_intake()
    batch=repo.admit(owner,admission_key='recover-current',items=[dict(id='fresh',state='OPEN',branch='VIT_CE',placement='Internship',deadline_ms=NOW+100000)],complete=False)
    with pytest.raises(CommitUnknown):repo.apply_item(owner,batch,0,lose_ack=True)
    event=repo.inspect_item(batch,0)
    assert repo.apply_item(owner,batch,0)==event
    repo.finalize_batch(owner,batch)
    intent=c.execute('SELECT intent_id FROM notification_intents WHERE source_event_id=?',(event,)).fetchone()[0]
    handle=repo.claim_outbox(intent,'SUBSCRIBER');repo.seal(handle);repo.fanout_page(handle)
    claim=repo.claim_delivery(channel='SUBSCRIBER');assert claim is not None
    permit=repo.authorize_send(claim)
    result=repo.submit_fake(permit,FakeTransport())
    repo.record_fact(permit.invocation_id,result,fact_key='fake-provider-return')
    repo.record_fact(permit.invocation_id,'ENDED',fact_key='synchronous-fake-return')
    repo.acknowledge(claim)
    assert repo.recover_delivery(claim.key)=='SENT'
    assert c.execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ACCEPTED'").fetchone()[0]==1
    # A newer opt-out and fixture re-enable cannot reset an already accepted obligation.
    x['db'].users.set_unsubscribed(1);repo.change_user(1,is_active=1)
    assert c.execute('SELECT status FROM notification_deliveries_v2 WHERE delivery_id=?',(claim.key,)).fetchone()[0]=='SENT'
    assert repo.claim_delivery(channel='SUBSCRIBER') is None


def test_v12_partial_and_foreign_schema_are_refused(encrypted):
    from app.database.notification_compatibility import consent_binding
    from app.database.key_provider import KeyUnavailable
    c=encrypted['c']
    c.execute('CREATE TABLE notification_runtime(singleton INTEGER)')
    with pytest.raises((KeyUnavailable,SchemaError)):create_schema(c,now_ms=NOW)
    with pytest.raises((KeyUnavailable,SchemaError)):consent_binding(c)


def test_v12_encrypted_factory_without_scoped_authority_fails_closed(tmp_path):
    from app.database.protected_payloads import protected_scope
    from app.database.notification_compatibility import repository_factory
    from app.database.key_provider import KeyUnavailable
    path=tmp_path/'unbound.sqlite';history(path)
    codec=implementation()(Authority(tmp_path/'external.json'),str(path))
    with protected_scope(path,codec),connect_disposable(path) as c:create_schema(c,now_ms=NOW)
    with pytest.raises(KeyUnavailable):repository_factory(str(path))


def test_v1_marker_free_factory_does_not_install_notification_schema(tmp_path):
    from app.database.notification_compatibility import repository_factory
    path=tmp_path/'legacy.sqlite';history(path)
    users,_=repository_factory(str(path));assert users.get_by_id(1)['is_active']==1
    with connect_disposable(path) as c:
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='notification_runtime'").fetchone()[0]==0


def test_v14_stale_consent_restore_is_refused_by_actual_default_factory(encrypted):
    from app.database.notification_compatibility import repository_factory
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    x['db'].users.set_unsubscribed(1)
    # Simulate an unsafe stale restore outside supported mutation units.
    c.execute('UPDATE users SET is_active=1 WHERE id=1')
    c.execute('UPDATE subscriber_versions SET consent_epoch=0 WHERE user_id=1')
    with pytest.raises(KeyUnavailable):repository_factory(str(x['path']))
    with pytest.raises(KeyUnavailable):repository_factory(str(x['path']))


def test_v14_stale_external_journal_cannot_restore_revoked_keys(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW);repo=EventRepository(c,clock=lambda:NOW)
    old=x['authority'].path.read_bytes()
    target=c.execute('SELECT snapshot_payload_id FROM opportunity_state LIMIT 1').fetchone()[0]
    assert repo.erase_payload(target,OperatorApproval('ERASE',target,'freshness-erase'))=='COMPLETE'
    x['authority'].path.write_bytes(old)
    with pytest.raises(KeyUnavailable):Authority(x['authority'].path)


def test_v13_database_commit_with_lost_external_ack_holds_without_reseeding(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];x['authority'].failure='finish_database'
    with pytest.raises(KeyUnavailable):create_schema(c,now_ms=NOW)
    identifiers=[tuple(r) for r in c.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]
    assert identifiers
    x['codec'].provider=Authority(x['authority'].path)
    with pytest.raises(KeyUnavailable):create_schema(c,now_ms=NOW)
    assert [tuple(r) for r in c.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]==identifiers
    assert c.execute('SELECT count(*) FROM opportunity_events').fetchone()[0]==0
    approval=OperatorApproval('RECONCILE_MIGRATION',x['codec'].namespace,'resolve-migration-a')
    assert x['codec'].reconcile_migration(c,approval)=='COMMITTED'
    assert x['codec'].reconcile_migration(c,approval)=='COMMITTED'
    create_schema(c,now_ms=NOW)
    assert [tuple(r) for r in c.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]==identifiers


@pytest.mark.parametrize('interrupt',[False,True])
def test_v7_existing_update_cannot_lend_its_prepared_baseline_to_new_company(encrypted,interrupt):
    from app.database.protected_payloads import canonical
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW);repo=EventRepository(c,clock=lambda:NOW)
    repo.activate();owner=repo.acquire_intake()
    batch=repo.admit(owner,admission_key='owner-order',items=[
        dict(id='history-a',state='OPEN',branch='VIT_CE'),
        dict(id='fresh-b',state='OPEN',branch='VIT_CE'),
        dict(id='fresh-c',state='OPEN',branch='VIT_CE')],complete=False)
    repo.apply_item(owner,batch,0)
    if interrupt:
        def fail(stage):
            if stage=='baseline':raise RuntimeError('rollback owner preparation')
        with pytest.raises(RuntimeError):repo.apply_item(owner,batch,1,fault=fail)
    for index in (1,2):repo.apply_item(owner,batch,index)
    for company in ('fresh-b','fresh-c'):
        row=c.execute('SELECT p.key_reference FROM opportunity_events e JOIN opportunity_state s USING(company_key) JOIN protected_payloads p ON p.payload_id=e.before_payload_id WHERE s.legacy_company_id=?',(company,)).fetchone()
        assert x['authority'].expected(row[0]).owner_digest==hashlib.sha256(canonical({'company':company})).hexdigest()


def test_v13_lost_rollback_ack_reconciles_uncommitted_exact_history(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];before=legacy_digest(c);x['authority'].failure='finish_database'
    def fail(stage):
        if stage=='seeded':raise RuntimeError('rollback migration')
    with pytest.raises(KeyUnavailable):create_schema(c,now_ms=NOW,fault=fail)
    assert legacy_digest(c)==before
    x['codec'].provider=Authority(x['authority'].path)
    approval=OperatorApproval('RECONCILE_MIGRATION',x['codec'].namespace,'resolve-not-committed')
    assert x['codec'].reconcile_migration(c,approval)=='NOT_COMMITTED'
    assert x['codec'].reconcile_migration(c,approval)=='NOT_COMMITTED'
    create_schema(c,now_ms=NOW)
    assert legacy_digest(c)==before


def test_v13_migration_repair_cannot_reset_later_consent(encrypted):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];create_schema(c,now_ms=NOW)
    x['authority'].failure='finish_database'
    with pytest.raises(KeyUnavailable):x['db'].users.set_unsubscribed(1)
    assert c.execute('SELECT is_active FROM users WHERE id=1').fetchone()[0]==0
    x['codec'].provider=Authority(x['authority'].path)
    with pytest.raises(KeyUnavailable):
        x['codec'].reconcile_migration(c,OperatorApproval('RECONCILE_MIGRATION',x['codec'].namespace,'must-not-reset'))


def test_v13_reconciliation_rejects_identical_legacy_database_in_wrong_namespace(encrypted,tmp_path):
    from app.database.key_provider import KeyUnavailable
    x=encrypted;c=x['c'];x['authority'].failure='finish_database'
    with pytest.raises(KeyUnavailable):create_schema(c,now_ms=NOW)
    x['codec'].provider=Authority(x['authority'].path)
    other=tmp_path/'other.sqlite';history(other)
    with connect_disposable(other) as second:
        with pytest.raises(KeyUnavailable):
            x['codec'].reconcile_migration(second,OperatorApproval('RECONCILE_MIGRATION',x['codec'].namespace,'wrong-database'))
