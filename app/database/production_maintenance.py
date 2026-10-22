"""Explicit offline maintenance entry point; no source polling or transport.

Run only after the reviewed owner checkpoint, with legacy and new writers and
physical senders stopped. No default database, key file, or recovery path exists.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from time import time_ns

from app.database.key_provider import KeyUnavailable
from app.database.notification_migrator import ProductionDatabaseAuthority, SchemaError, atomic, create_schema
from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
from app.database.production_recovery import MigrationRecoveryApproval, create_recovery_artifact
from app.database.protected_payloads import ProtectedPayloads, protected_scope


@contextmanager
def exclusive_maintenance(authority):
    from app.monitoring.production_runtime import OwnerLock
    from app.database.event_repository import Fenced
    lock = OwnerLock(authority.path.with_suffix(authority.path.suffix + '.runtime.lock'))
    try:
        lock.acquire()
    except Fenced:
        raise SchemaError('active runtime excludes maintenance') from None
    try:
        yield
    finally:
        lock.close()


def load_wrapping_keys(specifications):
    """Load raw 32-byte keys from separately injected owner-only secret files."""
    keys = {}
    for specification in specifications:
        try:
            generation, filename = specification.split('=', 1)
            generation = int(generation)
            path = Path(filename)
            if (generation < 1 or generation in keys or not path.is_absolute()
                    or path.is_symlink() or not path.is_file()
                    or os.name != 'nt' and path.stat().st_mode & 0o077):
                raise ValueError()
            key = path.read_bytes()
            if len(key) != 32:
                raise ValueError()
            keys[generation] = key
        except (OSError, ValueError):
            raise KeyUnavailable('mounted wrapping key unavailable or permissions rejected') from None
    if not keys:
        raise KeyUnavailable('injected wrapping keys required')
    return keys


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('operation', choices=('migrate', 'verify', 'backup', 'reconcile-migration', 'reconcile-unit', 'review-account', 'register-admin', 'revoke-admin'))
    result.add_argument('--database', required=True)
    result.add_argument('--database-sha256', required=True)
    result.add_argument('--vault', required=True)
    result.add_argument('--checkpoint', required=True)
    result.add_argument('--wrapping-key', action='append', required=True, metavar='GENERATION=ABSOLUTE_SECRET_FILE')
    result.add_argument('--active-generation', type=int, required=True)
    result.add_argument('--approval-reference', required=True)
    result.add_argument('--initialize-vault', action='store_true')
    result.add_argument('--recovery-directory')
    result.add_argument('--policy-reference')
    result.add_argument('--quiescence-reference')
    result.add_argument('--user-id', type=int)
    result.add_argument('--decision', choices=('APPROVED', 'REJECTED', 'SUSPENDED', 'REVOKED'))
    result.add_argument('--actor')
    result.add_argument('--review-evidence')
    result.add_argument('--institution', choices=('VIT_PUNE',))
    result.add_argument('--cohort', type=int)
    result.add_argument('--branch')
    result.add_argument('--role', choices=('SUBSCRIBER', 'ADMINISTRATOR'), default='SUBSCRIBER')
    result.add_argument('--admin-destination-file')
    result.add_argument('--admin-recipient-key')
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        if not arguments.approval_reference.strip():
            raise SchemaError('explicit reviewed owner approval reference required')
        if arguments.initialize_vault and arguments.operation != 'migrate':
            raise SchemaError('vault initialization is restricted to explicit migration')
        if arguments.operation in ('migrate', 'backup') and (not arguments.recovery_directory or not arguments.quiescence_reference):
            raise SchemaError('new recovery directory and stopped-writer evidence required')
        if arguments.operation == 'migrate' and not arguments.policy_reference:
            raise SchemaError('reviewed production policy reference required')
        if arguments.operation == 'review-account' and (not arguments.quiescence_reference or
                not arguments.user_id or arguments.user_id < 1 or not arguments.decision or
                not arguments.actor or not arguments.review_evidence):
            raise SchemaError('explicit account, operator evidence and stopped-writer reference required')
        if arguments.operation in ('register-admin', 'revoke-admin') and (not arguments.quiescence_reference or not arguments.actor):
            raise SchemaError('operator and stopped-writer evidence required for administrator audience')
        if arguments.operation == 'register-admin' and (not arguments.admin_destination_file or not arguments.review_evidence):
            raise SchemaError('protected administrator destination and ownership evidence required')
        if arguments.operation == 'revoke-admin' and not arguments.admin_recipient_key:
            raise SchemaError('exact administrator recipient identity required')
        keys = load_wrapping_keys(arguments.wrapping_key)
        provider = ProductionKeyProvider(arguments.vault, wrapping_keys=keys,
            active_generation=arguments.active_generation,
            checkpoints=SQLiteCheckpointStore(arguments.checkpoint), create=arguments.initialize_vault)
        authority = ProductionDatabaseAuthority.bind(arguments.database,
            expected_sha256=arguments.database_sha256, maintenance=True)
        with exclusive_maintenance(authority), authority.scope():
            codec = ProtectedPayloads(provider, str(authority.path))
            with protected_scope(authority.path, codec), authority.connect(require_ready=False) as connection:
                backup = None
                if arguments.operation in ('migrate', 'backup'):
                    backup = create_recovery_artifact(connection, Path(arguments.recovery_directory),
                        custody_reference=provider.identity,
                        writer_quiescence=arguments.quiescence_reference)
                if arguments.operation == 'migrate':
                    create_schema(connection, now_ms=time_ns() // 1_000_000)
                    from app.database.account_schema import install_account_schema
                    with atomic(connection):
                        install_account_schema(connection)
                    authority.enroll(connection, policy_reference=arguments.policy_reference)
                    authority.mark_ready(connection)
                    status = 'READY_NO_SEND'
                elif arguments.operation == 'reconcile-migration':
                    status = codec.reconcile_migration(connection, MigrationRecoveryApproval(authority, arguments.approval_reference))
                elif arguments.operation == 'reconcile-unit':
                    status = codec.reconcile_database_unit(connection, MigrationRecoveryApproval(authority, arguments.approval_reference))
                elif arguments.operation == 'verify':
                    authority.mark_ready(connection)
                    status = 'READY_NO_SEND'
                elif arguments.operation == 'review-account':
                    from app.auth.accounts import AccountService, OperatorAuthorization
                    authority.mark_ready(connection)
                    with atomic(connection):
                        AccountService(connection).review(arguments.user_id, arguments.decision,
                            authority=OperatorAuthorization(arguments.actor, arguments.approval_reference),
                            institution=arguments.institution, cohort=arguments.cohort, branch=arguments.branch,
                            evidence_reference=arguments.review_evidence, role=arguments.role)
                    status = 'ACCOUNT_REVIEW_RECORDED_NO_SEND'
                elif arguments.operation in ('register-admin', 'revoke-admin'):
                    from app.auth.accounts import OperatorAuthorization
                    from app.notifications.production_admin import register_admin, revoke_admin
                    from app.production_entrypoint import _secret
                    authority.mark_ready(connection)
                    operator = OperatorAuthorization(arguments.actor, arguments.approval_reference)
                    if arguments.operation == 'register-admin':
                        admin_key = register_admin(connection, _secret(arguments.admin_destination_file),
                            authority=operator, ownership_evidence=arguments.review_evidence, now_ms=time_ns() // 1_000_000)
                    else:
                        admin_key = arguments.admin_recipient_key
                        with atomic(connection):
                            revoke_admin(connection, admin_key, authority=operator, now_ms=time_ns() // 1_000_000)
                    status = 'ADMIN_AUDIENCE_RECORDED_NO_SEND'
                else:
                    status = 'BACKUP_VERIFIED'
                result = dict(status=status, physical_send_enabled=False, approval_reference=arguments.approval_reference)
                if arguments.operation in ('register-admin', 'revoke-admin'):
                    result['admin_recipient_key'] = admin_key
                if backup:
                    result['recovery_manifest'] = backup['manifest']
                print(json.dumps(result, sort_keys=True))
                return 0
    except (KeyUnavailable, SchemaError, OSError, ValueError):
        # Key custody, data, provider exceptions and secret filenames are omitted.
        print(json.dumps(dict(status='BLOCKED', reason='maintenance validation or custody failed')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
