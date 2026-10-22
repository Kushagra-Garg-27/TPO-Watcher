"""Production capabilities exercised only on bootstrap-confined fictional DBs."""
import hashlib
import secrets
import sqlite3

import pytest

from test_notification_migration_privacy import history, legacy_digest, NOW


def authority(path, *, maintenance=False):
    from app.database import notification_migrator as migration
    assert hasattr(migration, 'ProductionDatabaseAuthority'), 'production connection authority is missing'
    return migration.ProductionDatabaseAuthority.bind(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), maintenance=maintenance)


def test_production_database_requires_exact_registered_connection_and_readiness(tmp_path):
    from app.database import notification_migrator as migration
    path = tmp_path / 'fictional.sqlite'
    history(path)
    bound = authority(path)
    with bound.scope():
        with pytest.raises(migration.SchemaError):
            with bound.connect():
                pass
    with sqlite3.connect(path) as unregistered:
        assert not migration.production_authorized(unregistered)
    with pytest.raises(migration.SchemaError):
        migration.ProductionDatabaseAuthority.bind(path, expected_sha256='0' * 64)


def test_production_connection_rejects_attach_and_replaced_database(tmp_path):
    from app.database import notification_migrator as migration
    path = tmp_path / 'fictional.sqlite'
    initial = sqlite3.connect(path)
    initial.execute('CREATE TABLE fixture(value TEXT)')
    initial.close()
    bound = authority(path, maintenance=True)
    with bound.scope(), bound.connect(require_ready=False) as connection:
        assert migration.production_authorized(connection)
        other = tmp_path / 'other.sqlite'
        connection.execute("ATTACH DATABASE '" + str(other).replace("'", "''") + "' AS other")
        with pytest.raises(migration.SchemaError):
            migration.require_connection(connection)
        connection.execute('DETACH DATABASE other')
    moved = tmp_path / 'old.sqlite'
    path.rename(moved)
    path.write_bytes(moved.read_bytes())
    with pytest.raises(migration.SchemaError):
        with bound.scope(), bound.connect(require_ready=False):
            pass


def test_production_migration_preserves_history_and_has_durable_readiness(tmp_path):
    from app.database import notification_migrator as migration
    from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
    from app.database.protected_payloads import ProtectedPayloads, protected_scope
    path = tmp_path / 'fictional.sqlite'
    history(path)
    bound = authority(path, maintenance=True)
    provider = ProductionKeyProvider(tmp_path / 'vault.bin', wrapping_keys={1: secrets.token_bytes(32)}, active_generation=1, checkpoints=SQLiteCheckpointStore(tmp_path / 'checkpoints.sqlite'), create=True)
    with bound.scope():
        codec = ProtectedPayloads(provider, str(path))
        with protected_scope(path, codec), bound.connect(require_ready=False) as connection:
            before = legacy_digest(connection)
            with pytest.raises(migration.SchemaError, match='recovery artifact'):
                migration.create_schema(connection, now_ms=NOW)
            from app.database.production_recovery import create_recovery_artifact
            create_recovery_artifact(connection, tmp_path / 'before-migration', custody_reference='fictional-custody', writer_quiescence='fictional-maintenance')
            migration.create_schema(connection, now_ms=NOW)
            migration.create_schema(connection, now_ms=NOW + 1)
            assert legacy_digest(connection) == before
            assert connection.execute('SELECT count(*) FROM notification_intents').fetchone()[0] == 0
            assert connection.execute('SELECT count(*) FROM transport_invocations').fetchone()[0] == 0
            assert {r[0] for r in connection.execute('SELECT source_key FROM opportunity_state')} == {'production-legacy'}
            assert {r[0] for r in connection.execute('SELECT status FROM notification_deliveries')} == {'SENT', 'FAILED', 'PENDING', 'PROCESSING'}
            from app.database.account_schema import install_account_schema
            with migration.atomic(connection):
                install_account_schema(connection)
            with pytest.raises(migration.SchemaError, match='enrollment'):
                bound.mark_ready(connection)
            bound.enroll(connection, policy_reference='fictional-owner-review-no-send')
            bound.mark_ready(connection)
        with protected_scope(path, codec), bound.connect() as connection:
            assert migration.production_authorized(connection)


def test_production_migration_ack_loss_requires_production_scoped_forward_recovery(tmp_path):
    from app.database import notification_migrator as migration
    from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
    from app.database.protected_payloads import ProtectedPayloads, protected_scope
    from app.database import production_recovery as recovery
    from app.database.key_provider import KeyUnavailable
    from app.database.event_repository import OperatorApproval
    assert hasattr(recovery, 'MigrationRecoveryApproval'), 'production migration recovery approval is missing'
    path = tmp_path / 'fictional.sqlite'
    history(path)
    bound = authority(path, maintenance=True)
    provider = ProductionKeyProvider(tmp_path / 'vault.bin', wrapping_keys={1: secrets.token_bytes(32)}, active_generation=1, checkpoints=SQLiteCheckpointStore(tmp_path / 'checkpoints.sqlite'), create=True)
    with bound.scope():
        codec = ProtectedPayloads(provider, str(path))
        with protected_scope(path, codec), bound.connect(require_ready=False) as connection:
            recovery.create_recovery_artifact(connection, tmp_path / 'before-migration', custody_reference='fictional-custody', writer_quiescence='fictional-maintenance')
            finish = provider.finish_database
            provider.finish_database = lambda *args: (_ for _ in ()).throw(KeyUnavailable('fictional lost ack'))
            with pytest.raises(KeyUnavailable):
                migration.create_schema(connection, now_ms=NOW)
            provider.finish_database = finish
            identities = [tuple(r) for r in connection.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')]
            with pytest.raises(KeyUnavailable):
                codec.reconcile_migration(connection, OperatorApproval('RECONCILE_MIGRATION', str(path), 'fictional-is-not-production-authority'))
            approval = recovery.MigrationRecoveryApproval(bound, 'fictional-maintenance-recovery')
            assert codec.reconcile_migration(connection, approval) == 'COMMITTED'
            assert codec.reconcile_migration(connection, approval) == 'COMMITTED'
            assert [tuple(r) for r in connection.execute('SELECT company_key,snapshot_payload_id FROM opportunity_state ORDER BY company_key')] == identities


def test_backup_uses_sqlite_snapshot_and_refuses_restore_over_newer_facts(tmp_path):
    from app.database import notification_migrator as migration
    import importlib.util
    assert importlib.util.find_spec('app.database.production_recovery'), 'production recovery is missing'
    from app.database.production_recovery import create_recovery_artifact, restore_to_new_path
    path = tmp_path / 'fictional.sqlite'
    history(path)
    bound = authority(path, maintenance=True)
    with bound.scope(), bound.connect(require_ready=False) as connection:
        artifact = create_recovery_artifact(connection, tmp_path / 'recovery', custody_reference='fictional-custody-v1', writer_quiescence='fictional-exclusive-maintenance')
        assert artifact['integrity'] == 'ok'
        assert artifact['journal_mode'] == 'wal'
        snapshot = sqlite3.connect(artifact['database'])
        try:
            assert snapshot.execute('SELECT count(*) FROM notification_deliveries').fetchone()[0] == 4
        finally:
            snapshot.close()
        # Restores always target a fresh path; production data is never replaced.
        with pytest.raises(migration.SchemaError):
            restore_to_new_path(artifact['manifest'], path)
        target = tmp_path / 'recovered.sqlite'
        restored = restore_to_new_path(artifact['manifest'], target)
        assert restored == str(target.resolve())
        with pytest.raises(migration.SchemaError):
            restore_to_new_path(artifact['manifest'], target)


@pytest.mark.parametrize('historical_status', ['UNKNOWN', 'ACK', 'ACK_FAILED'])
def test_production_migration_never_replays_historical_uncertainty(tmp_path, historical_status):
    from app.database import notification_migrator as migration
    from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
    from app.database.protected_payloads import ProtectedPayloads, protected_scope
    from app.database.production_recovery import create_recovery_artifact
    path = tmp_path / 'fictional.sqlite'
    history(path)
    raw = sqlite3.connect(path)
    raw.execute('UPDATE notification_deliveries SET status=? WHERE id=1', (historical_status,))
    raw.commit(); raw.close()
    bound = authority(path, maintenance=True)
    provider = ProductionKeyProvider(tmp_path / 'vault.bin', wrapping_keys={1: secrets.token_bytes(32)}, active_generation=1, checkpoints=SQLiteCheckpointStore(tmp_path / 'checkpoints.sqlite'), create=True)
    with bound.scope():
        codec = ProtectedPayloads(provider, str(path))
        with protected_scope(path, codec), bound.connect(require_ready=False) as connection:
            before = legacy_digest(connection)
            create_recovery_artifact(connection, tmp_path / 'before-migration', custody_reference='fictional-custody', writer_quiescence='fictional-maintenance')
            migration.create_schema(connection, now_ms=NOW)
            assert legacy_digest(connection) == before
            assert tuple(connection.execute('SELECT original_status,decision FROM legacy_notification_dispositions WHERE legacy_kind=\'SUBSCRIBER\' AND legacy_row_id=1').fetchone()) == (historical_status, 'HISTORICAL_ONLY')
            assert connection.execute('SELECT count(*) FROM notification_intents').fetchone()[0] == 0


def test_maintenance_entry_point_executes_backup_schema_account_policy_and_readiness(tmp_path, capsys):
    import importlib.util
    assert importlib.util.find_spec('app.database.production_maintenance'), 'production maintenance entry point is missing'
    from app.database.production_maintenance import main
    path = tmp_path / 'fictional.sqlite'
    history(path)
    secret = tmp_path / 'mounted-wrapping-key'
    secret.write_bytes(secrets.token_bytes(32))
    secret.chmod(0o600)
    result = main(['migrate', '--database', str(path), '--database-sha256', hashlib.sha256(path.read_bytes()).hexdigest(),
        '--vault', str(tmp_path / 'vault.bin'), '--checkpoint', str(tmp_path / 'checkpoints.sqlite'),
        '--wrapping-key', '1=' + str(secret), '--active-generation', '1', '--initialize-vault',
        '--recovery-directory', str(tmp_path / 'recovery'), '--policy-reference', 'fictional-no-send-policy',
        '--quiescence-reference', 'fictional-worker-stopped', '--approval-reference', 'fictional-test-authorization'])
    assert result == 0
    output = capsys.readouterr().out
    assert 'READY_NO_SEND' in output
    assert secret.read_bytes().hex() not in output
    raw = sqlite3.connect(path)
    try:
        assert raw.execute('SELECT count(*) FROM notification_intents').fetchone()[0] == 0
        assert tuple(raw.execute('SELECT writer_mode,fanout_mode,send_mode FROM notification_runtime').fetchone()) == ('OFF', 'OFF', 'OFF')
    finally:
        raw.close()


def test_post_migration_forward_recovery_accepts_only_exact_staged_consent_state(tmp_path):
    from app.database import notification_migrator as migration
    from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
    from app.database.protected_payloads import ProtectedPayloads, protected_scope
    from app.database.production_recovery import create_recovery_artifact, MigrationRecoveryApproval
    from app.database.key_provider import KeyUnavailable
    path = tmp_path / 'fictional.sqlite'
    history(path)
    bound = authority(path, maintenance=True)
    provider = ProductionKeyProvider(tmp_path / 'vault.bin', wrapping_keys={1: secrets.token_bytes(32)}, active_generation=1, checkpoints=SQLiteCheckpointStore(tmp_path / 'checkpoints.sqlite'), create=True)
    assert hasattr(provider, 'resolve_database_unit'), 'staged database recovery is missing'
    with bound.scope():
        codec = ProtectedPayloads(provider, str(path))
        with protected_scope(path, codec), bound.connect(require_ready=False) as connection:
            create_recovery_artifact(connection, tmp_path / 'recovery', custody_reference='fictional-custody', writer_quiescence='fictional-maintenance')
            migration.create_schema(connection, now_ms=NOW)
            finish = provider.finish_database
            provider.finish_database = lambda *args: (_ for _ in ()).throw(KeyUnavailable('fictional lost ack'))
            with pytest.raises(KeyUnavailable), migration.atomic(connection):
                connection.execute('UPDATE users SET is_active=0 WHERE id=1')
            provider.finish_database = finish
            assert connection.execute('SELECT is_active FROM users WHERE id=1').fetchone()[0] == 0
            approved = MigrationRecoveryApproval(bound, 'fictional-preserve-current-consent')
            assert codec.reconcile_database_unit(connection, approved) == 'COMMITTED'
            assert codec.reconcile_database_unit(connection, approved) == 'COMMITTED'
            codec.warm(connection)
            # A mutation outside the staged transaction cannot masquerade as its result.
            provider.finish_database = lambda *args: (_ for _ in ()).throw(KeyUnavailable('fictional lost ack'))
            with pytest.raises(KeyUnavailable), migration.atomic(connection):
                connection.execute("UPDATE users SET updated_at='2026-10-22T00:00:00+00:00' WHERE id=1")
            provider.finish_database = finish
            connection.execute('UPDATE users SET is_active=1 WHERE id=1')
            with pytest.raises(KeyUnavailable):
                codec.reconcile_database_unit(connection, MigrationRecoveryApproval(bound, 'must-not-rewind-consent'))
