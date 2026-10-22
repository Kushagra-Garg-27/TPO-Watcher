"""Durable, injected production custody. Importing this module performs no I/O.

The encrypted vault, monotonic checkpoint database and wrapping-key mount are
separate recovery dependencies. Provision their directories with owner-only
access. Checkpoints must never be restored with an older application backup.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.database.key_provider import KeyGrant, KeyUnavailable, PayloadContext


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate custody field')
        value[key] = item
    return value


class SQLiteCheckpointStore:
    """Independent durable CAS anchor; keep on the custody recovery volume."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise KeyUnavailable('explicit checkpoint path required')
        with self._connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS checkpoints (identity TEXT PRIMARY KEY, revision INTEGER NOT NULL, digest TEXT NOT NULL)')

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(str(self.path), isolation_level=None, timeout=1)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA busy_timeout=1000')
            yield connection
        finally:
            connection.close()

    def read(self, identity):
        with self._connection() as connection:
            row = connection.execute('SELECT revision,digest FROM checkpoints WHERE identity=?', (identity,)).fetchone()
            return tuple(row) if row else (0, '')

    def advance(self, identity, previous, current):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT revision,digest FROM checkpoints WHERE identity=?', (identity,)).fetchone()
            actual = tuple(row) if row else (0, '')
            if actual != tuple(previous) or current[0] != actual[0] + 1:
                raise KeyUnavailable('custody checkpoint conflict')
            connection.execute('INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(identity) DO UPDATE SET revision=excluded.revision,digest=excluded.digest', (identity, *current))
            connection.commit()


@contextmanager
def _file_lock(path):
    # OS locks are released by process exit, including an unclean exit.
    with open(path, 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise KeyUnavailable('custody is already owned') from None
            try:
                yield
            finally:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise KeyUnavailable('custody is already owned') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ProductionKeyProvider:
    """KeyProvider with AEAD vault, durable revocations and rollback detection.

    Keys are supplied by the caller from separately protected secret custody;
    they are never generated into, or serialized alongside, the vault. All
    mutations reload under an OS lock and authenticate the external checkpoint.
    """
    def __init__(self, path, *, wrapping_keys, active_generation, checkpoints, create=False):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise KeyUnavailable('explicit vault path required')
        self.path = self.path.resolve()
        self.identity = hashlib.sha256(str(self.path).encode()).hexdigest()
        self.keys = dict(wrapping_keys)
        if (type(active_generation) is not int or active_generation < 1
                or active_generation not in self.keys
                or any(type(k) is not int or k < 1 or not isinstance(v, bytes) or len(v) != 32 for k, v in self.keys.items())):
            raise KeyUnavailable('injected wrapping keys rejected')
        self.generation = active_generation
        self.checkpoints = checkpoints
        if getattr(checkpoints, 'path', None) == self.path:
            raise KeyUnavailable('independent checkpoint custody required')
        self._mutex = threading.RLock()
        with self._locked():
            if self.path.exists():
                self._load()
            elif create and checkpoints.read(self.identity) == (0, ''):
                self._write({'keys': {}, 'actions': {}, 'databases': {}, 'retired': [], 'active_generation': active_generation}, (0, ''))
            else:
                raise KeyUnavailable('custody vault missing')

    @contextmanager
    def _locked(self):
        try:
            with self._mutex, _file_lock(self.path.with_suffix(self.path.suffix + '.lock')):
                yield
        except KeyUnavailable:
            raise
        except Exception:
            raise KeyUnavailable('custody unavailable') from None

    @staticmethod
    def _open(path, keys):
        blob = path.read_bytes()
        envelope = json.loads(blob, object_pairs_hook=_unique)
        if set(envelope) != {'header', 'nonce', 'ciphertext'}:
            raise KeyUnavailable('custody envelope rejected')
        header = envelope['header']
        if (set(header) != {'version', 'identity', 'revision', 'previous', 'generation'}
                or header['version'] != 1 or type(header['revision']) is not int or header['revision'] < 1
                or type(header['generation']) is not int):
            raise KeyUnavailable('custody header rejected')
        expected = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
        if header['identity'] != expected:
            raise KeyUnavailable('custody identity rejected')
        key = keys[header['generation']]
        state = json.loads(AESGCM(key).decrypt(bytes.fromhex(envelope['nonce']), bytes.fromhex(envelope['ciphertext']), _json(header)), object_pairs_hook=_unique)
        return state, (header['revision'], hashlib.sha256(blob).hexdigest()), header

    def _load(self):
        try:
            state, checkpoint, _ = self._open(self.path, self.keys)
            if self.checkpoints.read(self.identity) != checkpoint:
                raise KeyUnavailable('custody rollback or unresolved checkpoint')
            if self.generation in state['retired'] or self.generation < state['active_generation']:
                raise KeyUnavailable('retired wrapping generation rejected')
            return state, checkpoint
        except KeyUnavailable:
            raise
        except Exception:
            raise KeyUnavailable('custody authentication unavailable') from None

    def _write(self, state, previous):
        state['active_generation'] = self.generation
        header = dict(version=1, identity=self.identity, revision=previous[0] + 1, previous=previous[1], generation=self.generation)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.keys[self.generation]).encrypt(nonce, _json(state), _json(header))
        blob = _json(dict(header=header, nonce=nonce.hex(), ciphertext=ciphertext.hex()))
        pending = self.path.with_suffix(self.path.suffix + '.pending')
        descriptor = os.open(str(pending), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(blob); handle.flush(); os.fsync(handle.fileno())
        os.replace(pending, self.path)
        if os.name != 'nt':
            directory = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.checkpoints.advance(self.identity, previous, (header['revision'], hashlib.sha256(blob).hexdigest()))

    @classmethod
    def recover_checkpoint(cls, path, *, wrapping_keys, checkpoints):
        """Explicit forward-only repair of a vault commit whose CAS ack failed."""
        path = Path(path).resolve()
        try:
            with _file_lock(path.with_suffix(path.suffix + '.lock')):
                _, current, header = cls._open(path, wrapping_keys)
                old = checkpoints.read(header['identity'])
                if current == old:
                    return
                if current[0] != old[0] + 1 or header['previous'] != old[1]:
                    raise KeyUnavailable('forward checkpoint evidence rejected')
                checkpoints.advance(header['identity'], old, current)
        except KeyUnavailable:
            raise
        except Exception:
            raise KeyUnavailable('checkpoint recovery unavailable') from None

    def _operation(self, callback, *, write=False):
        with self._locked():
            state, checkpoint = self._load()
            result = callback(state)
            if write:
                self._write(state, checkpoint)
            return copy.deepcopy(result)

    def export_recovery(self, destination):
        """Export authenticated encrypted bytes and their current external anchor.

        The anchor itself remains live and is never rewound from this export.
        Separately recover the injected key generations and the current anchor.
        """
        destination = Path(destination)
        if not destination.is_absolute() or destination.is_symlink() or destination.exists():
            raise KeyUnavailable('new custody recovery destination required')
        with self._locked():
            _, checkpoint = self._load()
            blob = self.path.read_bytes()
            if hashlib.sha256(blob).hexdigest() != checkpoint[1]:
                raise KeyUnavailable('custody changed during recovery export')
            descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(blob); handle.flush(); os.fsync(handle.fileno())
            return dict(identity=self.identity, original_path=str(self.path), checkpoint=checkpoint,
                path=str(destination), sha256=checkpoint[1],
                recovery_policy='CURRENT_ANCHOR_REQUIRED_NEVER_REWIND_CHECKPOINT')

    @staticmethod
    def _context(context):
        if (not isinstance(context, PayloadContext) or not Path(context.namespace).is_absolute()
                or not context.payload_id or context.purpose not in ('SOURCE', 'SNAPSHOT', 'APPROVAL', 'RECIPIENT')
                or len(context.owner_digest) != 64 or any(c not in '0123456789abcdef' for c in context.owner_digest)):
            raise KeyUnavailable('key context rejected')
        return context.as_dict()

    def _unwrap(self, state, reference, context):
        row = state['keys'].get(reference)
        if not row or row['revoked'] or row['context'] != self._context(context) or row['generation'] in state['retired']:
            raise KeyUnavailable('key unavailable or unauthorized')
        aad = _json(dict(reference=reference, context=row['context'], generation=row['generation']))
        key = AESGCM(self.keys[row['generation']]).decrypt(bytes.fromhex(row['nonce']), bytes.fromhex(row['wrapped']), aad)
        return KeyGrant(reference, key, row['generation'])

    @staticmethod
    def _mutation_available(state, namespace):
        if state['databases'].get(namespace, {}).get('pending'):
            raise KeyUnavailable('key mutation fenced by active or unresolved database unit')

    def validate_unit_keys(self, namespace, grants):
        """Revalidate warmed grants after the durable mutation fence is held."""
        def validate(state):
            if not state['databases'].get(namespace, {}).get('pending'):
                raise KeyUnavailable('database unit fence required')
            for reference, (context, cached) in grants.items():
                grant = self._unwrap(state, reference, context)
                if grant.key != cached.key:
                    raise KeyUnavailable('warmed key grant changed')
        self._operation(validate)

    def allocate(self, context, operation):
        expected = self._context(context)
        def allocate(state):
            self._mutation_available(state, context.namespace)
            if not operation:
                raise KeyUnavailable('allocation operation required')
            for reference, row in state['keys'].items():
                if row['operation'] == operation:
                    return self._unwrap(state, reference, context)
            reference = secrets.token_hex(24)
            key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
            aad = _json(dict(reference=reference, context=expected, generation=self.generation))
            wrapped = AESGCM(self.keys[self.generation]).encrypt(nonce, key, aad)
            state['keys'][reference] = dict(context=expected, operation=operation, generation=self.generation, nonce=nonce.hex(), wrapped=wrapped.hex(), revoked=False)
            return KeyGrant(reference, key, self.generation)
        return self._operation(allocate, write=True)

    def expected(self, reference):
        def expected(state):
            row = state['keys'].get(reference)
            if not row:
                raise KeyUnavailable('key unavailable')
            return PayloadContext(**row['context'])
        return self._operation(expected)

    def unwrap(self, reference, context):
        return self._operation(lambda state: self._unwrap(state, reference, context))

    def schema_required(self, namespace):
        return self._operation(lambda state: state['databases'].get(namespace, {}).get('schema', False))

    def database_state(self, namespace):
        return self._operation(lambda state: state['databases'].get(namespace, dict(fingerprint=None, pending=False, schema=False)))

    @staticmethod
    def _check(state, namespace, fingerprint):
        row = state['databases'].get(namespace)
        if row and (row['pending'] or row['fingerprint'] != fingerprint):
            raise KeyUnavailable('database recovery freshness or commit unresolved')

    def check_database(self, namespace, fingerprint):
        self._operation(lambda state: self._check(state, namespace, fingerprint))

    def begin_database(self, namespace, fingerprint):
        def begin(state):
            self._check(state, namespace, fingerprint)
            old = state['databases'].get(namespace, {})
            state['databases'][namespace] = dict(fingerprint=fingerprint, pending=True, schema=old.get('schema', False))
        self._operation(begin, write=True)

    def finish_database(self, namespace, fingerprint, schema):
        def finish(state):
            old = state['databases'].get(namespace)
            if not old or not old['pending'] or (old['schema'] and not schema):
                raise KeyUnavailable('database checkpoint transition rejected')
            state['databases'][namespace] = dict(fingerprint=fingerprint, pending=False, schema=bool(schema))
        self._operation(finish, write=True)

    def stage_database_commit(self, namespace, fingerprint, schema):
        """Durable intended outcome before SQLite COMMIT, containing hashes only."""
        def stage(state):
            row = state['databases'].get(namespace)
            if not row or not row['pending'] or (row['schema'] and not schema):
                raise KeyUnavailable('database commit staging rejected')
            row['staged'] = dict(fingerprint=fingerprint, schema=bool(schema))
        self._operation(stage, write=True)

    def resolve_database_unit(self, namespace, fingerprint, authorization):
        """Acknowledge exactly original or staged state; never mutate SQLite."""
        def resolve(state):
            row = state['databases'].get(namespace)
            if not row or not authorization or not row['schema']:
                raise KeyUnavailable('post-migration recovery evidence rejected')
            if not row['pending'] and row.get('resolution') == authorization and row['fingerprint'] == fingerprint:
                return row['outcome']
            if not row['pending']:
                raise KeyUnavailable('no unresolved database unit')
            staged = row.get('staged')
            if staged and staged['fingerprint'] == fingerprint:
                schema, outcome = staged['schema'], 'COMMITTED'
            elif row['fingerprint'] == fingerprint:
                schema, outcome = row['schema'], 'NOT_COMMITTED'
            else:
                raise KeyUnavailable('current database differs from durable commit evidence')
            state['databases'][namespace] = dict(fingerprint=fingerprint, pending=False,
                schema=schema, resolution=authorization, outcome=outcome)
            return outcome
        return self._operation(resolve, write=True)

    def resolve_migration(self, namespace, original, current, authorization, *, committed=True):
        def resolve(state):
            old = state['databases'][namespace]
            if not old['pending'] and old.get('resolution') == authorization and old['fingerprint'] == current:
                return
            if not authorization or not old['pending'] or old['schema'] or old['fingerprint'] != original:
                raise KeyUnavailable('initial migration recovery rejected')
            state['databases'][namespace] = dict(fingerprint=current, pending=False, schema=bool(committed), resolution=authorization)
        self._operation(resolve, write=True)

    def prepare_erasure(self, action, context, reference):
        def prepare(state):
            self._mutation_available(state, context.namespace)
            row = state['keys'].get(reference)
            if not action or not row or row['context'] != self._context(context):
                raise KeyUnavailable('erasure scope rejected')
            old = state['actions'].get(action)
            value = dict(context=context.as_dict(), reference=reference, generation=row['generation'], acknowledged=False)
            if old and {k: v for k, v in old.items() if k != 'acknowledged'} != {k: v for k, v in value.items() if k != 'acknowledged'}:
                raise KeyUnavailable('erasure operation conflict')
            if not old:
                state['actions'][action] = value
        self._operation(prepare, write=True)

    def erasure(self, action):
        return self._operation(lambda state: state['actions'][action])

    def revoke(self, action):
        def revoke(state):
            row = state['actions'][action]
            self._mutation_available(state, row['context']['namespace'])
            key = state['keys'][row['reference']]
            key.update(revoked=True, wrapped='', nonce='')
            row['acknowledged'] = True
            return True
        return self._operation(revoke, write=True)

    def rewrap(self, reference, operation):
        def rewrap(state):
            row = state['keys'][reference]
            self._mutation_available(state, row['context']['namespace'])
            if not operation:
                raise KeyUnavailable('rotation operation required')
            if row.get('rotation') == operation:
                return row['generation']
            grant = self._unwrap(state, reference, PayloadContext(**row['context']))
            nonce = secrets.token_bytes(12)
            aad = _json(dict(reference=reference, context=row['context'], generation=self.generation))
            wrapped = AESGCM(self.keys[self.generation]).encrypt(nonce, grant.key, aad)
            row.update(generation=self.generation, nonce=nonce.hex(), wrapped=wrapped.hex(), rotation=operation)
            return self.generation
        return self._operation(rewrap, write=True)

    def retire_generation(self, generation, *, recovery_acknowledged=False):
        def retire(state):
            if (not recovery_acknowledged or generation == self.generation
                    or any(row['generation'] == generation and not row['revoked'] for row in state['keys'].values())
                    or any(row['generation'] == generation and not row['acknowledged'] for row in state['actions'].values())):
                raise KeyUnavailable('wrapping retirement evidence incomplete')
            if generation not in state['retired']:
                state['retired'].append(generation)
        self._operation(retire, write=True)
