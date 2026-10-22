"""Consistent SQLite recovery artifacts; never overwrite an existing database.

Recovery mounts an artifact at a new path for inspection. Activation requires
the current custody checkpoint and explicit deployment review; this module
cannot rewind a live database, its consent facts, or its delivery journals.
"""
import hashlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3

from app.database.notification_migrator import SchemaError, require_connection, active_production_authority
from app.database.protected_payloads import ProtectedPayloads, binding


@dataclass(frozen=True)
class MigrationRecoveryApproval:
    """Trusted controller admission scoped to one exact maintenance authority."""
    authority: object
    reference: str


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _verify(connection):
    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
        raise SchemaError('recovery artifact integrity rejected')


def create_recovery_artifact(connection, directory, *, custody_reference, writer_quiescence):
    """Freeze writers, back up committed WAL state, verify and hash the result.

    The caller must stop application writers/physical workers first. A SQLite
    IMMEDIATE transaction proves exclusive writer ownership during this backup;
    supplied references identify the reviewed external custody/quiescence facts.
    """
    require_connection(connection)
    authority = active_production_authority(connection)
    if authority is None or not authority.maintenance or connection.in_transaction:
        raise SchemaError('exclusive production maintenance connection required')
    if not custody_reference or not writer_quiescence:
        raise SchemaError('custody and writer quiescence evidence required')
    directory = Path(directory)
    if not directory.is_absolute() or directory.exists() or directory.is_symlink():
        raise SchemaError('new absolute recovery directory required')
    codec = binding(connection)
    if codec:
        codec.warm(connection)
    checkpoint = codec.provider.database_state(codec.namespace) if codec else None
    source = authority.path
    directory.mkdir(mode=0o700)
    destination = directory / 'database.sqlite'
    manifest_path = directory / 'manifest.json'
    connection.execute('BEGIN IMMEDIATE')
    try:
        before = ProtectedPayloads.fingerprint(connection)
        _verify(connection)
        reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, isolation_level=None)
        snapshot = sqlite3.connect(str(destination), isolation_level=None)
        try:
            reader.backup(snapshot, pages=128, sleep=0.01)
            _verify(snapshot)
            if ProtectedPayloads.fingerprint(snapshot) != before:
                raise SchemaError('recovery snapshot contents mismatch')
        finally:
            snapshot.close()
            reader.close()
        if ProtectedPayloads.fingerprint(connection) != before:
            raise SchemaError('database changed during recovery snapshot')
        journal_mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
        manifest = dict(version=1, database=str(destination), source=str(source),
            source_device=authority.file_identity[0], source_inode=authority.file_identity[1],
            sha256=_digest(destination), logical_fingerprint=before, integrity='ok',
            journal_mode=journal_mode, custody_reference=custody_reference,
            custody_checkpoint=checkpoint, writer_quiescence=writer_quiescence,
            recovery_policy='NEW_PATH_INSPECTION_ONLY_CURRENT_CUSTODY_REQUIRED')
        if codec and hasattr(codec.provider, 'export_recovery'):
            manifest['encrypted_custody_export'] = codec.provider.export_recovery(directory / 'custody.vault')
            if codec.provider.database_state(codec.namespace) != checkpoint:
                raise SchemaError('custody changed during database recovery export')
        with manifest_path.open('x', encoding='utf-8') as handle:
            json.dump(manifest, handle, sort_keys=True)
            handle.flush(); os.fsync(handle.fileno())
        manifest['manifest'] = str(manifest_path)
        authority._recovery_artifact = dict(manifest)
        return manifest
    finally:
        connection.rollback()


def require_recovery_artifact(connection):
    """Prove the current maintenance database still matches its verified backup."""
    authority = active_production_authority(connection)
    artifact = getattr(authority, '_recovery_artifact', None)
    if (not artifact or _digest(Path(artifact['database'])) != artifact['sha256']
            or artifact['logical_fingerprint'] != ProtectedPayloads.fingerprint(connection)):
        raise SchemaError('verified current recovery artifact required before migration')


def restore_to_new_path(manifest_path, target):
    """Verified inspection restore. Never replace a file, live WAL or journal."""
    manifest_path, target = Path(manifest_path), Path(target)
    if not manifest_path.is_absolute() or not target.is_absolute():
        raise SchemaError('explicit recovery paths required')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    source = Path(manifest['database'])
    if (manifest.get('version') != 1 or manifest.get('integrity') != 'ok'
            or source.resolve().parent != manifest_path.resolve().parent
            or source.is_symlink() or source.resolve() == target.resolve()
            or target.exists() or target.is_symlink()
            or any(Path(str(target) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
            or _digest(source) != manifest.get('sha256')):
        raise SchemaError('recovery identity or destination rejected')
    check = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    try:
        _verify(check)
        if ProtectedPayloads.fingerprint(check) != manifest.get('logical_fingerprint'):
            raise SchemaError('recovery content fingerprint rejected')
    finally:
        check.close()
    descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with source.open('rb') as reader, os.fdopen(descriptor, 'wb') as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                writer.write(chunk)
            writer.flush(); os.fsync(writer.fileno())
        if _digest(target) != manifest['sha256']:
            raise SchemaError('restored artifact changed during copy')
    except BaseException:
        # Leave a failed artifact for inspection; never retry over it.
        raise
    return str(target.resolve())
