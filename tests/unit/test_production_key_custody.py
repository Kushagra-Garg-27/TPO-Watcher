"""Real custody implementation with fictional keys confined by bootstrap."""
import importlib.util
import secrets

import pytest


def implementation():
    assert importlib.util.find_spec('app.database.production_key_provider'), 'production custody is missing'
    from app.database.production_key_provider import ProductionKeyProvider, SQLiteCheckpointStore
    return ProductionKeyProvider, SQLiteCheckpointStore


def test_durable_keys_are_encrypted_separate_from_injected_wrapping_keys(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext
    key = secrets.token_bytes(32)
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    provider = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor, create=True)
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    grant = provider.allocate(context, 'allocation-one')
    reopened = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor)
    assert reopened.allocate(context, 'allocation-one') == grant
    assert reopened.unwrap(grant.reference, context) == grant
    assert key not in path.read_bytes() and grant.key not in path.read_bytes()


def test_revocation_and_database_checkpoint_cannot_rewind_after_restore(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    key = secrets.token_bytes(32)
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    provider = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor, create=True)
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    grant = provider.allocate(context, 'allocation-one')
    stale = path.read_bytes()
    provider.begin_database(context.namespace, 'before')
    with pytest.raises(KeyUnavailable):
        provider.check_database(context.namespace, 'before')
    provider.finish_database(context.namespace, 'after', True)
    provider.prepare_erasure('erase-one', context, grant.reference)
    assert provider.revoke('erase-one') and provider.revoke('erase-one')
    with pytest.raises(KeyUnavailable):
        provider.unwrap(grant.reference, context)
    path.write_bytes(stale)
    with pytest.raises(KeyUnavailable):
        Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor)


def test_rotation_reuses_operation_and_requires_recovery_ack_before_retirement(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    keys = {1: secrets.token_bytes(32), 2: secrets.token_bytes(32)}
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    old = Provider(path, wrapping_keys=keys, active_generation=1, checkpoints=anchor, create=True)
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    grant = old.allocate(context, 'allocation-one')
    current = Provider(path, wrapping_keys=keys, active_generation=2, checkpoints=anchor)
    with pytest.raises(KeyUnavailable):
        current.retire_generation(1, recovery_acknowledged=True)
    assert current.rewrap(grant.reference, 'rotate-one') == 2
    assert current.rewrap(grant.reference, 'rotate-one') == 2
    with pytest.raises(KeyUnavailable):
        old.allocate(context, 'stale-controller')
    with pytest.raises(KeyUnavailable):
        current.retire_generation(1, recovery_acknowledged=False)
    current.retire_generation(1, recovery_acknowledged=True)
    reopened = Provider(path, wrapping_keys={2: keys[2]}, active_generation=2, checkpoints=anchor)
    assert reopened.unwrap(grant.reference, context).key == grant.key


def test_interrupted_checkpoint_requires_exact_forward_recovery(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    key = secrets.token_bytes(32)
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    provider = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor, create=True)
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    original = anchor.advance
    anchor.advance = lambda *args: (_ for _ in ()).throw(OSError('fictional interruption'))
    with pytest.raises(KeyUnavailable):
        provider.allocate(context, 'allocation-one')
    anchor.advance = original
    with pytest.raises(KeyUnavailable):
        Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor)
    Provider.recover_checkpoint(path, wrapping_keys={1: key}, checkpoints=anchor)
    recovered = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor)
    assert recovered.allocate(context, 'allocation-one').reference


def test_active_database_unit_fences_concurrent_key_revocation(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    key = secrets.token_bytes(32)
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    provider = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor, create=True)
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    grant = provider.allocate(context, 'allocation-one')
    provider.prepare_erasure('erase-one', context, grant.reference)
    provider.begin_database(context.namespace, 'before')
    with pytest.raises(KeyUnavailable):
        provider.revoke('erase-one')
    provider.finish_database(context.namespace, 'before', False)
    assert provider.revoke('erase-one')


def test_encrypted_recovery_export_cannot_restore_over_newer_external_checkpoint(tmp_path):
    Provider, Checkpoints = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    key = secrets.token_bytes(32)
    anchor = Checkpoints(tmp_path / 'external-checkpoints.sqlite')
    path = tmp_path / 'vault.bin'
    provider = Provider(path, wrapping_keys={1: key}, active_generation=1, checkpoints=anchor, create=True)
    assert hasattr(provider, 'export_recovery'), 'durable custody recovery export is missing'
    context = PayloadContext(str(tmp_path / 'database.sqlite'), 'one', 'SOURCE', 'a' * 64)
    grant = provider.allocate(context, 'allocation-one')
    exported = provider.export_recovery(tmp_path / 'recovery-vault.bin')
    provider.prepare_erasure('erase-one', context, grant.reference)
    provider.revoke('erase-one')
    path.write_bytes((tmp_path / 'recovery-vault.bin').read_bytes())
    with pytest.raises(KeyUnavailable):
        Provider.recover_checkpoint(path, wrapping_keys={1: key}, checkpoints=anchor)
    assert exported['checkpoint'][0] >= 1
    assert key not in (tmp_path / 'recovery-vault.bin').read_bytes()
