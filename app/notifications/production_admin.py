"""Offline administrator audience admission; no implicit legacy/test adoption."""
import hashlib
import json
import secrets

from app.auth.accounts import OperatorAuthorization
from app.database.key_provider import PayloadContext
from app.database.notification_migrator import active_production_authority, atomic
from app.database.protected_payloads import require_binding
from app.notifications.production_smtp import mailbox


def register_admin(connection, destination, *, authority, ownership_evidence, now_ms):
    bound = active_production_authority(connection)
    if (bound is None or not bound.maintenance or connection.in_transaction or
            type(authority) is not OperatorAuthorization or type(now_ms) is not int or now_ms <= 0):
        raise ValueError('explicit protected maintenance unit and operator authority required')
    if (not isinstance(ownership_evidence, str) or not 1 <= len(ownership_evidence.strip()) <= 200 or
            any(ord(c) < 32 for c in ownership_evidence)):
        raise ValueError('administrator destination ownership evidence required')
    destination = mailbox(destination)
    digest = hashlib.sha256(destination.encode()).hexdigest()
    key = 'production:admin-destination:' + digest
    existing = connection.execute('SELECT value FROM system_state WHERE key=?', (key,)).fetchone()
    if existing:
        previous = json.loads(existing[0])
        if previous['active'] is not True:
            raise ValueError('revoked administrator requires a separately reviewed recovery')
        if previous['ownership_evidence'] != ownership_evidence:
            raise ValueError('existing administrator ownership evidence differs')
        return previous['recipient_key']
    handle = secrets.token_hex(16)
    codec = require_binding(connection)
    context = PayloadContext(codec.namespace, handle, 'RECIPIENT', hashlib.sha256(b'ADMIN:' + handle.encode()).hexdigest())
    sealed = codec.encrypt(context, {'destination': destination})
    record = dict(recipient_key=handle, active=True, actor=authority.actor,
        approval_reference=authority.authorization_reference, ownership_evidence=ownership_evidence,
        destination_digest=digest, created_ms=now_ms)
    with atomic(connection):
        if connection.execute('SELECT 1 FROM system_state WHERE key=?', (key,)).fetchone():
            raise ValueError('administrator registration changed during preparation')
        connection.execute("INSERT INTO recipient_handles VALUES(?,'ADMIN',?)", (handle, now_ms))
        connection.execute('INSERT INTO recipient_associations(recipient_key,protected_destination,key_reference) VALUES(?,?,?)',
            (handle, sealed.blob, sealed.reference))
        for marker in (key, 'production:admin:' + handle):
            connection.execute('INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)',
                (marker, json.dumps(record, sort_keys=True), str(now_ms)))
    return handle


def admin_eligible(connection, recipient_key):
    row = connection.execute('SELECT value FROM system_state WHERE key=?', ('production:admin:' + recipient_key,)).fetchone()
    if not row:
        return False
    try:
        record = json.loads(row[0])
        return bool(record['active'] is True and record['recipient_key'] == recipient_key
            and record['approval_reference'] and record['ownership_evidence'] and record['actor'])
    except (ValueError, KeyError, TypeError):
        return False


def revoke_admin(connection, recipient_key, *, authority, now_ms):
    bound = active_production_authority(connection)
    if bound is None or not bound.maintenance or not connection.in_transaction or type(authority) is not OperatorAuthorization:
        raise ValueError('explicit protected maintenance and operator authority required')
    key = 'production:admin:' + recipient_key
    row = connection.execute('SELECT value FROM system_state WHERE key=?', (key,)).fetchone()
    if not row:
        raise ValueError('registered administrator unavailable')
    record = json.loads(row[0])
    record.update(active=False, revoked_ms=now_ms, revocation_reference=authority.authorization_reference)
    for marker in (key, 'production:admin-destination:' + record['destination_digest']):
        connection.execute('UPDATE system_state SET value=?,updated_at=? WHERE key=?',
            (json.dumps(record, sort_keys=True), str(now_ms), marker))
