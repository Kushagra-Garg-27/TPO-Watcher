"""Whole startup wiring against a migrated fictional store; no source or SMTP."""
import gc
import hashlib
import secrets
import pytest

from fastapi.testclient import TestClient
from test_notification_migration_privacy import history


@pytest.fixture
def production_runtime(tmp_path, monkeypatch):
    from app.database.production_maintenance import main
    from app.production_entrypoint import RuntimeFileConfig, configured_runtime, application_for
    from app.config import settings
    for name in ('TPO_USERNAME', 'TPO_PASSWORD', 'SECRET_KEY', 'BASE_URL', 'PUBLIC_HOST', 'PUBLIC_PORT', 'COOKIE_SECURE'):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    path = tmp_path / 'fictional.sqlite'
    history(path)
    files = {}
    for name, data in [('wrapping', secrets.token_bytes(32)), ('source-user', b'fictional-operator'),
            ('source-password', b'fictional-password'), ('session-secret', secrets.token_hex(32).encode())]:
        file = tmp_path / name
        file.write_bytes(data)
        file.chmod(0o600)
        files[name] = str(file)
    vault, checkpoint = str(tmp_path / 'vault'), str(tmp_path / 'checkpoint.sqlite')
    gc.collect()
    assert main(['migrate', '--database', str(path), '--database-sha256', hashlib.sha256(path.read_bytes()).hexdigest(),
        '--vault', vault, '--checkpoint', checkpoint, '--wrapping-key', '1=' + files['wrapping'],
        '--active-generation', '1', '--approval-reference', 'fictional-owner-approval',
        '--quiescence-reference', 'fictional-stopped', '--policy-reference', 'fictional-no-send',
        '--initialize-vault', '--recovery-directory', str(tmp_path / 'backup')]) == 0
    config = RuntimeFileConfig.from_mapping(dict(version=1, public_origin='https://watcher.example.invalid',database=str(path),
        database_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), vault=vault, checkpoints=checkpoint,
        wrapping_keys={'1': files['wrapping']}, active_generation=1,
        source_authorization={'identity': 'fictional-operator', 'evidence_reference': 'fictional-source-authorization'},
        listing_contract={'source_namespace': 'fictional-startup', 'institution': 'VIT', 'cohort': 2028,
            'evidence_reference': 'fictional-source-scope', 'freshness_ms': 60000},
        legacy_shutdown={'reference': 'fictional-exclusion', 'writer': True, 'subscriber': True, 'admin': True, 'account': True},
        auth_username_file=files['source-user'], auth_password_file=files['source-password'],
        session_state=str(tmp_path / 'browser-state.json'), session_secret_file=files['session-secret']))
    with configured_runtime(config) as runtime:
        runtime.start()
        yield runtime


def test_configured_no_send_runtime_uses_exact_migrated_store_for_api_threads(production_runtime):
    from app.production_entrypoint import application_for
    runtime = production_runtime
    assert runtime.pilot is None and runtime.transport is None
    with TestClient(application_for(runtime)) as client:
        assert client.get('/health').json()['notification']['state'] == 'NO_SEND'
        assert client.get('/api/v1/preferences').status_code == 401
        assert client.get('/api/v1/opportunities').status_code == 200
        listing = client.get('/api/v1/opportunity-listing')
        assert listing.status_code == 200
        assert listing.json()['status'] != 'VERIFIED'
    assert runtime.c.execute('SELECT count(*) FROM transport_invocations').fetchone()[0] == 0


def test_production_origin_uses_approved_https_origin_behind_http_proxy(production_runtime):
    from app.production_entrypoint import application_for
    from app.api.routes_account import require_same_origin
    from starlette.requests import Request
    from fastapi import HTTPException
    application=application_for(production_runtime)
    def request(origin):
        return Request({'type':'http','scheme':'http','server':('watcher',8000),'path':'/',
            'headers':[(b'host',b'watcher:8000'),(b'origin',origin.encode()),
                (b'x-forwarded-proto',b'https'),(b'x-forwarded-host',b'attacker.invalid')],
            'app':application})
    require_same_origin(request('https://watcher.example.invalid'))
    with pytest.raises(HTTPException):require_same_origin(request('https://attacker.invalid'))
    with pytest.raises(HTTPException):require_same_origin(request('http://watcher:8000'))


@pytest.mark.parametrize('composed', [False, True])
def test_account_mutation_stages_custody_before_sqlite_commit(production_runtime, monkeypatch, composed):
    from contextlib import nullcontext
    from app.database.notification_compatibility import repository_factory, repository_unit
    runtime = production_runtime
    users, tokens = repository_factory(runtime.authority.path)
    original = runtime.codec.stage_unit
    staged = []
    def stage(connection):
        assert connection.in_transaction, 'Custody must stage before SQLite commit'
        staged.append(True)
        return original(connection)
    monkeypatch.setattr(runtime.codec, 'stage_unit', stage)
    with repository_unit((users, tokens)) if composed else nullcontext():
        uid = users.create_user('new.fictional@vit.edu', 2028, 'VIT_CE')
    assert staged
    assert users.get_by_id(uid)['email'] == 'new.fictional@vit.edu'


def test_rejected_account_unit_rolls_back_without_poisoning_custody(production_runtime):
    from app.database.notification_compatibility import repository_factory, repository_unit
    users, tokens = repository_factory(production_runtime.authority.path)
    with pytest.raises(ValueError, match='fictional rejected request'):
        with repository_unit((users, tokens)):
            users.create_user('rollback.fictional@vit.edu', 2028, 'VIT_CE')
            raise ValueError('fictional rejected request')
    assert users.get_by_email('rollback.fictional@vit.edu') is None
    uid = users.create_user('subsequent.fictional@vit.edu', 2028, 'VIT_CE')
    assert users.get_by_id(uid) is not None


def test_committed_account_change_survives_lost_custody_ack(production_runtime, monkeypatch):
    from app.database.notification_compatibility import repository_factory, repository_unit
    from app.database.key_provider import KeyUnavailable
    runtime=production_runtime
    users,tokens=repository_factory(runtime.authority.path)
    provider=runtime.codec.provider
    original=provider.finish_database
    def lost_ack(*args):raise KeyUnavailable('fictional lost final ack')
    monkeypatch.setattr(provider,'finish_database',lost_ack)
    try:
        with pytest.raises(KeyUnavailable,match='fictional lost final ack'):
            with repository_unit((users,tokens)):
                uid=users.create_user('committed.fictional@vit.edu',2028,'VIT_CE')
        state=provider.database_state(runtime.codec.namespace)
        fingerprint=runtime.codec.fingerprint(runtime.c)
        assert state['pending'] and state['staged']['fingerprint']==fingerprint
        assert runtime.c.execute('SELECT email FROM users WHERE id=?',(uid,)).fetchone()[0]=='committed.fictional@vit.edu'
        # Exercise provider's forward-only recovery; CLI authorization is covered separately.
        assert provider.resolve_database_unit(runtime.codec.namespace,fingerprint,'fictional-recovery')=='COMMITTED'
    finally:
        monkeypatch.setattr(provider,'finish_database',original)
    assert users.get_by_id(uid)['email']=='committed.fictional@vit.edu'


def test_configured_https_origin_works_behind_untrusted_http_proxy_scope(production_runtime):
    from app.production_entrypoint import application_for
    from app.api.routes_account import require_same_origin
    from starlette.requests import Request
    application=application_for(production_runtime)
    scope=dict(type='http',method='POST',scheme='http',server=('internal-service',8000),path='/',root_path='',
        headers=[(b'host',b'watcher.example.invalid'),(b'origin',b'https://watcher.example.invalid')],app=application)
    require_same_origin(Request(scope))
    scope['headers']=[(b'host',b'watcher.example.invalid'),(b'origin',b'https://attacker.example.invalid')]
    from fastapi import HTTPException
    with pytest.raises(HTTPException):require_same_origin(Request(scope))
