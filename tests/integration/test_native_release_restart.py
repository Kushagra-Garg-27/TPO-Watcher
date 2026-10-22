"""Real configured no-send runtime survives a stopped owner and refreshed identity."""
from dataclasses import replace
import hashlib
from fastapi.testclient import TestClient
from test_production_entrypoint_startup import production_runtime


def test_no_send_health_and_restart_with_current_checkpoint(tmp_path, monkeypatch):
    import app.production_entrypoint as entry
    captured=[]
    original=entry.RuntimeFileConfig.from_mapping.__func__
    def capture(cls, data):
        config=original(cls,data)
        captured.append(config)
        return config
    monkeypatch.setattr(entry.RuntimeFileConfig,'from_mapping',classmethod(capture))
    fixture=production_runtime.__wrapped__(tmp_path,monkeypatch)
    try:
        runtime=next(fixture)
        with TestClient(entry.application_for(runtime)) as client:
            assert client.get('/health').json()['notification']['state']=='NO_SEND'
        generation=runtime.generation
    finally:fixture.close()
    config=replace(captured[0],database_sha256=hashlib.sha256(runtime.authority.path.read_bytes()).hexdigest())
    assert runtime.closed and runtime.lock.file is None
    from app.monitoring.production_runtime import current_production_runtime
    assert current_production_runtime(config.database) is None
    with entry.configured_runtime(config) as successor:
        successor.start()
        assert successor.generation==generation+1
        with TestClient(entry.application_for(successor)) as client:
            assert client.get('/health').json()['notification']['state']=='NO_SEND'
        assert successor.transport is None and successor.pilot is None
        assert successor.c.execute('SELECT count(*) FROM transport_invocations').fetchone()[0]==0
