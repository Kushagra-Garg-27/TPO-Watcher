"""Guard-only AEAD experiment; explicit scoped binding, no production defaults.

Authority preparation and warming happen outside SQLite write units. A scoped
session caches authorized test grants for short units; it is not a production
revocation/coherency protocol. Test authority custody lives exclusively in tests.
"""
from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.database.key_provider import KeyUnavailable, PayloadContext

_bindings = ContextVar("fictional_protected_payload_bindings", default=())
PREFIX = b"TPO-AEAD-1:"
MARKER = "r2bc:protected"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate field')
        result[key]=value
    return result


@dataclass(frozen=True)
class Sealed:
    blob: bytes
    reference: str


def namespace(path):
    from app.database.notification_migrator import _inside
    return str(_inside(Path(path).resolve()))


class ProtectedPayloads:
    def __init__(self, provider, database_namespace):
        self.namespace = namespace(database_namespace)
        self.provider = provider
        self.expected_schema = bool(provider.schema_required(self.namespace))
        self._prepared = defaultdict(deque)
        self._grants = {}

    def _connection(self,connection):
        if namespace(connection.execute('PRAGMA database_list').fetchone()[2])!=self.namespace:
            raise KeyUnavailable('protected database namespace rejected')

    def _validate(self, context):
        if (not isinstance(context, PayloadContext) or context.namespace != self.namespace
                or not context.payload_id or context.purpose not in ("SOURCE", "SNAPSHOT", "APPROVAL", "RECIPIENT")
                or len(context.owner_digest) != 64
                or any(c not in "0123456789abcdef" for c in context.owner_digest)):
            raise KeyUnavailable("protected context rejected")

    @staticmethod
    def _key(grant):
        if not isinstance(grant.key, bytes) or len(grant.key) != 32 or not grant.reference or grant.generation < 1:
            raise KeyUnavailable("invalid authority grant")
        return grant.key

    def encrypt(self, context, data):
        self._validate(context)
        try:
            grant = self.provider.allocate(context, uuid.uuid4().hex)
            key = self._key(grant)
            nonce = secrets.token_bytes(12)
            header = dict(version=1, algorithm="AES-256-GCM", reference=grant.reference, **context.as_dict())
            ciphertext = AESGCM(key).encrypt(nonce, canonical(data), canonical(header))
            self._grants[grant.reference] = (context, grant)
            return Sealed(PREFIX + canonical(dict(header=header, nonce=nonce.hex(), ciphertext=ciphertext.hex())), grant.reference)
        except KeyUnavailable: raise
        except Exception: raise KeyUnavailable("protected encryption unavailable") from None

    def _decode(self, context, blob, reference, grant):
        self._validate(context)
        try:
            if not isinstance(blob, bytes) or not blob.startswith(PREFIX): raise ValueError()
            envelope = json.loads(blob[len(PREFIX):],object_pairs_hook=_unique_object)
            if set(envelope) != {"header", "nonce", "ciphertext"}: raise ValueError()
            expected = dict(version=1, algorithm="AES-256-GCM", reference=reference, **context.as_dict())
            if envelope["header"] != expected or type(envelope["header"]["version"]) is not int: raise ValueError()
            nonce = bytes.fromhex(envelope["nonce"])
            ciphertext = bytes.fromhex(envelope["ciphertext"])
            if len(nonce) != 12 or len(ciphertext) < 16 or grant.reference != reference: raise ValueError()
            plaintext = AESGCM(self._key(grant)).decrypt(nonce, ciphertext, canonical(expected))
            return json.loads(plaintext)
        except Exception: raise KeyUnavailable("protected authentication rejected") from None

    def decrypt(self, context, blob, reference):
        try:
            self._validate(context)
            if self.provider.expected(reference) != context: raise KeyUnavailable("protected context rejected")
            grant = self.provider.unwrap(reference, context)
            return self._decode(context, blob, reference, grant)
        except KeyUnavailable: raise
        except Exception: raise KeyUnavailable("protected authority unavailable") from None

    def prepare(self, purpose, data, owner):
        context = PayloadContext(self.namespace, uuid.uuid4().hex, purpose, hashlib.sha256(canonical(owner)).hexdigest())
        sealed = self.encrypt(context, data)
        self._prepared[(purpose, hashlib.sha256(canonical(data)).hexdigest(),context.owner_digest)].append((context, sealed, self._grants[sealed.reference][1]))
        return context.payload_id

    def insert(self, connection, purpose, data, now_ms, key=None, *, owner=None):
        self._connection(connection)
        if owner is None:raise KeyUnavailable('explicit protected owner required')
        expected_owner=hashlib.sha256(canonical(owner)).hexdigest()
        queue = self._prepared[(purpose, hashlib.sha256(canonical(data)).hexdigest(),expected_owner)]
        if not queue: raise KeyUnavailable("protected payload not prepared outside transaction")
        context, sealed, grant = queue.popleft()
        if (key is not None and key != context.payload_id) or context.owner_digest!=expected_owner: raise KeyUnavailable("prepared identity mismatch")
        connection.execute("INSERT INTO payload_tombstones VALUES(?,?,?)", (context.payload_id, purpose, now_ms))
        connection.execute("INSERT INTO protected_payloads VALUES(?,?,?)", (context.payload_id, sealed.blob, sealed.reference))
        self._grants[sealed.reference] = (context, grant)
        return context.payload_id

    def warm(self, connection):
        self._connection(connection)
        if connection.in_transaction: raise KeyUnavailable("authority warming requires no write transaction")
        self.provider.check_database(self.namespace,self.fingerprint(connection))
        self._grants.clear()
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='protected_payloads'").fetchone():return
        for row in connection.execute("SELECT p.*,t.purpose FROM protected_payloads p JOIN payload_tombstones t USING(payload_id)"):
            context = self.provider.expected(row["key_reference"])
            self._validate(context)
            if context.payload_id != row["payload_id"] or context.purpose != row["purpose"]:
                raise KeyUnavailable("protected row identity mismatch")
            self._check_owner(connection,context)
            grant = self.provider.unwrap(row["key_reference"], context)
            self._decode(context, row["encrypted_bytes"], row["key_reference"], grant)
            self._grants[row["key_reference"]] = (context, grant)

    @staticmethod
    def fingerprint(connection, *, legacy=False):
        """Bounded fictional recovery evidence, hash only outside SQLite."""
        tables=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if legacy:
            from app.database.notification_migrator import statements,_definition
            added={_definition(s)[1] for s in statements() if _definition(s)[0]=='table'}
            tables=[t for t in tables if t not in added]
        evidence=[]
        for table in tables:
            quoted='"'+table.replace('"','""')+'"'
            rows=[]
            for row in connection.execute('SELECT * FROM '+quoted):
                if legacy and table=='system_state' and row[0] in (MARKER,'r2bc:migration_manifest'):continue
                rows.append(canonical([(type(v).__name__,v.hex() if isinstance(v,bytes) else v) for v in row]).hex())
            evidence.append((table,sorted(rows)))
        return hashlib.sha256(canonical(evidence)).hexdigest()

    def begin_unit(self,connection):
        self.warm(connection)
        self.provider.begin_database(self.namespace,self.fingerprint(connection))

    def finish_unit(self,connection):
        if connection.in_transaction:raise KeyUnavailable('commit outcome unresolved')
        installed=bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name='notification_schema_migrations'").fetchone())
        self.provider.finish_database(self.namespace,self.fingerprint(connection),installed)
        self.expected_schema=installed

    def reconcile_migration(self,connection,approval):
        """Explicit fictional operator recovery, only an unactivated initial seed.

        Never an arbitrary checkpoint reset: independent original legacy hash,
        authenticated baseline contents/owners, schema and complete manifest
        must agree. Post-installation units/consent/transport cannot use this.
        """
        from app.database.event_repository import EventRepository
        from app.database.notification_migrator import require_connection,verify_schema,_verify_protected_manifest
        require_connection(connection)
        self._connection(connection)
        EventRepository._operator(approval,'RECONCILE_MIGRATION',self.namespace)
        if connection.in_transaction:raise KeyUnavailable('migration recovery requires maintenance')
        state=self.provider.database_state(self.namespace)
        current=self.fingerprint(connection)
        if not state['pending'] and state.get('resolution')==approval.reference and state['fingerprint']==current:
            return 'COMMITTED' if state['schema'] else 'NOT_COMMITTED'
        if not state['pending'] or state['schema']:
            raise KeyUnavailable('only pending initial migration can be reconciled')
        if current==state['fingerprint']:
            from app.database.notification_migrator import _legacy_preflight,_protected_preflight
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('notification_schema_migrations','protected_payloads','subscriber_versions','notification_runtime')").fetchone():
                raise KeyUnavailable('partial migration shape cannot be reconciled')
            _legacy_preflight(connection);_protected_preflight(connection)
            self.provider.resolve_migration(self.namespace,current,current,approval.reference,committed=False)
            self.expected_schema=False
            return 'NOT_COMMITTED'
        if self.fingerprint(connection,legacy=True)!=state['fingerprint']:
            raise KeyUnavailable('original migration history mismatch')
        verify_schema(connection);_verify_protected_manifest(connection)
        runtime=connection.execute('SELECT epoch,writer_mode,fanout_mode,send_mode FROM notification_runtime').fetchone()
        if tuple(runtime)!=(0,'OFF','OFF','OFF'):
            raise KeyUnavailable('activated migration cannot be reconciled')
        for table in ('source_batches','opportunity_events','notification_intents','transport_invocations','transport_facts','privacy_actions'):
            if connection.execute('SELECT count(*) FROM '+table).fetchone()[0]:
                raise KeyUnavailable('migration recovery has newer durable facts')
        if connection.execute('SELECT 1 FROM subscriber_versions WHERE consent_epoch<>0 OR profile_epoch<>0 LIMIT 1').fetchone():
            raise KeyUnavailable('migration recovery has newer consent')
        companies=connection.execute('SELECT * FROM companies').fetchall()
        if connection.execute('SELECT count(*) FROM protected_payloads').fetchone()[0]!=len(companies):
            raise KeyUnavailable('migration protected manifest count mismatch')
        for old in companies:
            row=connection.execute('SELECT p.*,t.purpose FROM opportunity_state s JOIN protected_payloads p ON p.payload_id=s.snapshot_payload_id JOIN payload_tombstones t USING(payload_id) WHERE s.legacy_company_id=? AND s.revision=0 AND s.head_event_id IS NULL',(old['id'],)).fetchone()
            if not row:raise KeyUnavailable('migration baseline identity mismatch')
            context=self.provider.expected(row['key_reference'])
            if context.payload_id!=row['payload_id'] or context.purpose!='SNAPSHOT':
                raise KeyUnavailable('migration baseline purpose mismatch')
            self._check_owner(connection,context,{'company':old['id']})
            if self.decrypt(context,row['encrypted_bytes'],row['key_reference'])!=dict(old):
                raise KeyUnavailable('migration baseline contents mismatch')
        if self.fingerprint(connection)!=current:raise KeyUnavailable('migration changed during recovery')
        self.provider.resolve_migration(self.namespace,state['fingerprint'],current,approval.reference)
        self.expected_schema=True
        return 'COMMITTED'

    def _check_owner(self,connection,context,owner=None):
        if owner is None and context.purpose=='SNAPSHOT':
            rows=connection.execute('SELECT legacy_company_id FROM opportunity_state WHERE snapshot_payload_id=? UNION SELECT s.legacy_company_id FROM opportunity_events e JOIN opportunity_state s USING(company_key) WHERE e.before_payload_id=? OR e.after_payload_id=?',(context.payload_id,context.payload_id,context.payload_id)).fetchall()
            if len(rows)!=1:raise KeyUnavailable('snapshot owner association rejected')
            owner={'company':rows[0][0]}
        elif owner is None and context.purpose=='SOURCE':
            batch=connection.execute('SELECT admission_key FROM source_batches WHERE payload_id=?',(context.payload_id,)).fetchone()
            item=connection.execute('SELECT trusted_company_id FROM source_batch_items WHERE payload_id=?',(context.payload_id,)).fetchone()
            if bool(batch)==bool(item):raise KeyUnavailable('source owner association rejected')
            owner={'admission':batch[0]} if batch else {'source':item[0]}
        if owner is not None and hashlib.sha256(canonical(owner)).hexdigest()!=context.owner_digest:
            raise KeyUnavailable('protected owner association rejected')

    def load(self, connection, key, *, owner=None):
        self._connection(connection)
        row = connection.execute("SELECT p.*,t.purpose FROM protected_payloads p JOIN payload_tombstones t USING(payload_id) WHERE payload_id=?", (key,)).fetchone()
        if row is None: raise KeyUnavailable("protected payload unavailable")
        if not connection.in_transaction:
            context=self.provider.expected(row['key_reference'])
            if context.payload_id!=key or context.purpose!=row['purpose']:raise KeyUnavailable('protected row identity mismatch')
            self._check_owner(connection,context,owner)
            return self.decrypt(context,row['encrypted_bytes'],row['key_reference'])
        cached = self._grants.get(row["key_reference"])
        if cached is None: raise KeyUnavailable("protected session not warmed")
        context, grant = cached
        if context.payload_id != key or context.purpose != row["purpose"]: raise KeyUnavailable("protected row identity mismatch")
        self._check_owner(connection,context,owner)
        return self._decode(context, row["encrypted_bytes"], row["key_reference"], grant)

    def rotate_data_key(self, *args):
        raise KeyUnavailable("DEK rewrite requires separately approved physical schema")


def binding(connection):
    if not _bindings.get():return None
    target = namespace(connection.execute("PRAGMA database_list").fetchone()[2])
    matches = [codec for name, codec in _bindings.get() if name == target]
    if len(matches) > 1: raise KeyUnavailable("ambiguous protected binding")
    return matches[0] if matches else None


def require_binding(connection):
    codec = binding(connection)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marked = "system_state" in tables and connection.execute("SELECT value FROM system_state WHERE key=?", (MARKER,)).fetchone()
    encrypted = "protected_payloads" in tables and connection.execute("SELECT 1 FROM protected_payloads WHERE key_reference<>'fictional-key' LIMIT 1").fetchone()
    if (marked or encrypted) and codec is None: raise KeyUnavailable("explicit protected authority binding required")
    if marked and marked[0] != codec.namespace: raise KeyUnavailable("protected namespace mismatch")
    if codec and "notification_schema_migrations" in tables and not marked:
        raise KeyUnavailable("protected capability marker missing")
    if codec and codec.expected_schema and 'notification_schema_migrations' not in tables:
        raise KeyUnavailable('protected schema downgrade forbidden')
    return codec


@contextmanager
def protected_scope(path, codec):
    target = namespace(path)
    if not isinstance(codec, ProtectedPayloads) or codec.namespace != target:
        raise KeyUnavailable("same-database protected binding required")
    if any(name == target for name, _ in _bindings.get()): raise KeyUnavailable("nested protected binding forbidden")
    token = _bindings.set((*_bindings.get(), (target, codec)))
    try: yield codec
    finally: _bindings.reset(token)
