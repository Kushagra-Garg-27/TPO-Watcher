import json
import pytest


def test_active_config_defaults_to_no_send_and_never_reads_smtp_mount(tmp_path):
    from app.production_entrypoint import RuntimeFileConfig
    config=dict(version=1,public_origin='https://watcher.example.invalid',database='/data/watcher.sqlite',database_sha256='a'*64,
        vault='/custody/vault.json',checkpoints='/custody/checkpoints.sqlite',wrapping_keys={'1':'/run/secrets/key'},
        active_generation=1,source_authorization={'identity':'operator','evidence_reference':'source-approved'},
        listing_contract={'source_namespace':'vierp','institution':'VIT','cohort':2028,'evidence_reference':'source-scope','freshness_ms':60000},
        legacy_shutdown={'reference':'all-old-senders-stopped','writer':True,'subscriber':True,'admin':True,'account':True},
        auth_username_file='/run/secrets/source-user',auth_password_file='/run/secrets/source-password',
        session_state='/sessions/vierp.json',session_secret_file='/run/secrets/web-session')
    for key in ('database','vault','checkpoints','auth_username_file','auth_password_file','session_state','session_secret_file'):
        config[key]=str(tmp_path/config[key].lstrip('/'))
    value=RuntimeFileConfig.from_mapping(config)
    assert value.pilot is None and value.smtp is None
    assert value.poll_interval_ms==60000


def test_active_config_rejects_smtp_without_separate_pilot(tmp_path):
    from app.production_entrypoint import RuntimeFileConfig
    with pytest.raises(ValueError):RuntimeFileConfig.from_mapping({'version':1,'smtp':{'host':'smtp.example.invalid'}})


def test_duplicate_config_fields_are_rejected(tmp_path):
    from app.production_entrypoint import load_config
    path=tmp_path/'config.json';path.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError):load_config(path)


def test_existing_production_repository_open_never_runs_migrations(tmp_path,monkeypatch):
    from app.database.sqlite_repository import SQLiteUserRepository
    import sqlite3
    from app.database.repository import DatabaseRepository
    from app.database.account_schema import install_account_schema
    path=tmp_path/'existing.sqlite';DatabaseRepository(str(path))
    with sqlite3.connect(path) as connection:install_account_schema(connection)
    monkeypatch.setattr('app.database.sqlite_repository.run_migrations',lambda *a:pytest.fail('implicit migration'))
    repo=SQLiteUserRepository.open_existing(str(path))
    assert repo.db_path==str(path) and repo._unit_connection is None
