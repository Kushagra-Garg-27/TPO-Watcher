"""Explicit runtime selection cannot be enabled by environment settings."""
import pytest


def test_s01_runtime_defaults_are_inert_and_environment_cannot_enable_v2(monkeypatch):
    from app.config import RuntimeConfig,RuntimeMode,Settings
    from app.main import create_application
    monkeypatch.setenv('NOTIFICATION_RUNTIME_MODE','V2_FAKE')
    assert RuntimeConfig().mode is RuntimeMode.LEGACY
    app=create_application()
    assert getattr(app.state,'notification_runtime',None) is None
    assert not hasattr(Settings(),'NOTIFICATION_RUNTIME_MODE')


@pytest.mark.parametrize('changes',[{'mode':'V2_FAKE'},{'idle_wait_ms':0},{'generation':True},
    {'mode':'SMTP'},{'idle_wait_ms':10000}])
def test_explicit_runtime_config_rejects_unsafe_values(changes):
    from app.config import RuntimeConfig
    with pytest.raises((ValueError,TypeError)):RuntimeConfig(**changes)
