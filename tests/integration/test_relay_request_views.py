"""Relay read views stay current, read-only, and isolated across API threads."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_notification_migration_privacy import encrypted
from test_notification_runtime_supervision import running


def test_request_view_sees_later_commits_and_closes_on_failure(encrypted):
    from app.database.notification_migrator import atomic
    with running(encrypted) as (runtime, _, child, _):
        relay=runtime.relay
        with pytest.raises(ValueError,match='fixture-request-failure'):
            with relay._request_view():
                view=relay._request_views.connection
                assert view is not runtime.repo.c and not view.in_transaction
                assert view.execute('PRAGMA query_only').fetchone()[0]==1
                with pytest.raises(sqlite3.OperationalError,match='readonly'):
                    view.execute("INSERT INTO system_state(key,value) VALUES('request-probe','forbidden')")
                assert view.execute("SELECT value FROM system_state WHERE key='request-probe'").fetchone() is None
                with atomic(runtime.repo.c):
                    runtime.repo.c.execute("INSERT INTO system_state(key,value) VALUES('request-probe','committed')")
                with relay._request_view():
                    assert relay._request_views.connection is view
                    assert view.execute("SELECT value FROM system_state WHERE key='request-probe'").fetchone()[0]=='committed'
                assert relay.request(child,'status',{})['physical_calls']==0
                assert not view.in_transaction
                raise ValueError('fixture-request-failure')
        with pytest.raises(sqlite3.ProgrammingError):view.execute('SELECT 1')
        assert getattr(relay._request_views,'connection',None) is None
        assert relay.request(child,'status',{})['physical_calls']==0


def test_api_thread_owns_its_view_without_borrowing_controller_connection(encrypted):
    with running(encrypted) as (runtime, _, child, _):
        relay=runtime.relay
        with relay._request_view():
            owner_view=relay._request_views.connection
            def api_request():
                with relay._request_view():
                    api_view=relay._request_views.connection
                    assert api_view is not owner_view and api_view is not runtime.repo.c
                    result=relay.request(child,'status',{})
                    assert not api_view.in_transaction
                assert getattr(relay._request_views,'connection',None) is None
                with pytest.raises(sqlite3.ProgrammingError):api_view.execute('SELECT 1')
                return result
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(api_request).result(timeout=5)['physical_calls']==0
            assert relay._request_views.connection is owner_view
            assert owner_view.execute('SELECT 1').fetchone()[0]==1
