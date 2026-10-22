"""Bounded production sender lifecycle with fictional in-process providers only."""
import asyncio
from dataclasses import replace
import threading
import time

import pytest

from test_notification_migration_privacy import encrypted, NOW
from test_production_runtime import build, Browser
from test_cummins_end_to_end_v2 import record
from tests.unit.test_production_smtp import Server, adapter


def pilot():
    from app.monitoring.production_runtime import PilotAuthorization
    return PilotAuthorization('deadline-pilot', ('fictional-admin@example.invalid',), 1, 1,
        NOW + 60000, 'sender@example.invalid', 'source-scope')


@pytest.mark.asyncio
async def test_stalled_provider_preserves_loop_and_holds_owner_until_physical_return(encrypted):
    from app.database.event_repository import EventRepository
    from app.database.notification_migrator import create_schema
    from app.monitoring.production_runtime import OwnerLock
    x = encrypted
    create_schema(x['c'], now_ms=NOW)
    released = threading.Event()
    entered = threading.Event()
    class SlowServer(Server):
        def ehlo(self):
            entered.set()
            released.wait(5)
            return super().ehlo()
    server = SlowServer()
    Browser.outcomes = [[record(id='deadline-company')]]
    transport=adapter(server)
    transport.settings=replace(transport.settings,timeout_seconds=1)
    with build(x, pilot=pilot(), transport=transport) as runtime:
        from app.notifications.production_admin import register_admin
        from app.auth.accounts import OperatorAuthorization
        register_admin(runtime.c,'fictional-admin@example.invalid',
            authority=OperatorAuthorization('owner','deadline-pilot'),ownership_evidence='fictional-inbox',now_ms=NOW)
        runtime.start()
        try:
            await runtime.poll_once()
            draining = asyncio.create_task(runtime.drain_once())
            await asyncio.sleep(.03)
            assert not draining.done()
            await asyncio.wait_for(draining, timeout=3)
            assert entered.is_set()
            assert runtime.state == 'SHUTDOWN_HOLD'
            assert x['c'].execute('SELECT status FROM notification_deliveries_v2').fetchone()[0] == 'UNCERTAIN'
            assert x['c'].execute("SELECT count(*) FROM transport_facts WHERE fact_kind='ENDED'").fetchone()[0] == 0
            runtime.stop()
            other = OwnerLock(runtime.lock.path)
            with pytest.raises(RuntimeError):
                other.acquire()
            released.set()
            for _ in range(100):
                if runtime.lock.file is None:
                    break
                await asyncio.sleep(.01)
            assert runtime.lock.file is None
            assert server.data_calls == 0
        finally:
            released.set()
            runtime.stop()


@pytest.mark.asyncio
async def test_expired_credentials_are_pruned_by_no_send_runtime(encrypted):
    from app.database.notification_migrator import atomic
    x = encrypted
    Browser.outcomes = []
    with build(x) as runtime:
        runtime.start()
        try:
            with atomic(runtime.c):
                runtime.c.execute('INSERT INTO account_sessions VALUES(?,1,0,0,NULL)', ('f' * 64,))
                runtime.c.execute("INSERT INTO destination_challenges VALUES(?,1,'fictional@example.invalid',0,NULL)", ('e' * 64,))
            await runtime.drain_once()
            runtime.prune_credentials_once()
            assert runtime.c.execute('SELECT count(*) FROM account_sessions').fetchone()[0] == 0
            assert runtime.c.execute('SELECT count(*) FROM destination_challenges').fetchone()[0] == 0
            assert runtime.c.execute('SELECT count(*) FROM notification_deliveries').fetchone()[0] == 4
        finally:
            runtime.stop()
