"""Fictional browser responses only; never contact the portal."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tpo.client import API_ENDPOINT, AuthenticationError, TPOClient
from app.tpo.listing import SourceEvidenceError


def browser_client(body=None, *, text=None, status=200, content_type="application/json"):
    response = SimpleNamespace(url=API_ENDPOINT + "?token=PRIVATE_QUERY", status=status,
        ok=200 <= status < 300, headers={"content-type": content_type},
        text=AsyncMock(return_value=json.dumps(body) if text is None else text))

    class Capture:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @property
        def value(self):
            async def result():
                return response
            return result()

    page = MagicMock()
    page.url = "https://tpo.vierp.in/home"
    page.is_closed.return_value = False
    page.locator.return_value.first.is_visible = AsyncMock(return_value=True)
    page.locator.return_value.first.click = AsyncMock()
    page.goto = AsyncMock()
    page.expect_response.side_effect = lambda predicate, **kwargs: Capture() if predicate(response) else None
    return TPOClient(None, page), response


@pytest.mark.parametrize("body,category", [
    ([], "non_object"),
    ({"status": "failure", "company_list": []}, "invalid_status"),
    ({}, "missing_company_list"),
    ({"company_list": None}, "invalid_company_list_type"),
    ({"company_list": {}}, "invalid_company_list_type"),
    ({"status": "SUCCESS", "company_list": []}, "invalid_status"),
    ({"status": {}, "company_list": []}, "invalid_status"),
    ({"data": {"company_list": []}}, "missing_company_list"),
    ({"result": {"company_list": []}, "companies": []}, "missing_company_list"),
    ({"company_list": [{"id": True, "company": "Fictional"}]}, "invalid_company_identity"),
    ({"company_list": [{"id": str(i), "company": "Fictional"} for i in range(2001)]}, "oversized_company_list"),
    ({"error": True, "company_list": [{"id": "one", "company": "Fictional"}]}, "application_error"),
])
async def test_rejected_envelopes_keep_source_exception_and_emit_structure(body, category, caplog):
    client, _ = browser_client(body)
    message = ("stable source identity required before coercion" if category == "invalid_company_identity"
               else "explicit successful company_list required")
    with caplog.at_level(logging.WARNING), pytest.raises(SourceEvidenceError, match=message):
        await client.fetch_companies()
    assert f"category={category}" in caplog.text
    assert "http_status=200" in caplog.text
    assert "content_type=application/json" in caplog.text
    assert "company_list" in caplog.text
    assert client._listing_body is None


@pytest.mark.parametrize("body", [
    {"status": "success", "company_list": []},
    {"status": "Success", "company_list": [{"id": "one", "company": "Fictional"}]},
])
async def test_original_valid_envelope(body, caplog):
    client, _ = browser_client(body)
    assert len(await client.fetch_companies()) == len(body["company_list"])
    assert "Rejected TPO response" not in caplog.text


@pytest.mark.parametrize("status", [True, 200, 1, 200.0])
async def test_numeric_status_guard_and_pydantic_are_distinct(status, caplog):
    # Existing guard uses Python equality (1 == True); Pydantic still rejects.
    client, _ = browser_client({"status": status, "company_list": []})
    with caplog.at_level(logging.WARNING), pytest.raises(Exception, match="Unexpected API schema"):
        await client.fetch_companies()
    assert "category=invalid_company_schema" in caplog.text


@pytest.mark.parametrize("status,body,text,category", [
    (200, None, "{PRIVATE_BODY", "malformed_json"),
    (200, None, "", "empty_response"),
    (401, None, None, "session_expired_http"),
    (403, None, None, "session_expired_http"),
    (500, None, None, "http_failure"),
    (200, {"error": "PRIVATE_BODY", "msg": "PRIVATE_MESSAGE"}, None, "application_error"),
    (200, {"company_list": [{"id": "one", "company": {"PRIVATE_FIELD": "PRIVATE_BODY"}}]}, None, "invalid_company_schema"),
])
async def test_diagnostics_never_include_values(status, body, text, category, caplog):
    client, response = browser_client(body, text=text, status=status,
        content_type="text/html; private=PRIVATE_HEADER\nPRIVATE_INJECTION")
    with caplog.at_level(logging.WARNING), pytest.raises(Exception) as exc:
        await client.fetch_companies()
    output = caplog.text + str(exc.value)
    assert f"category={category}" in output
    assert "PRIVATE" not in output
    if status in (401, 403):
        assert isinstance(exc.value, AuthenticationError)
        response.text.assert_not_awaited()


async def test_wrappers_and_list_length_are_structural_only(caplog):
    client, _ = browser_client({"data": {}, "result": [], "companies": "PRIVATE", "company_list": [None]})
    with caplog.at_level(logging.WARNING), pytest.raises(SourceEvidenceError):
        await client.fetch_companies()
    assert "'data': 'object'" in caplog.text
    assert "'result': 'array'" in caplog.text
    assert "'companies': 'string'" in caplog.text
    assert "company_list_length=1" in caplog.text
    assert "PRIVATE" not in caplog.text


async def test_playwright_exception_query_is_not_logged(caplog):
    from playwright.async_api import Error as PlaywrightError
    client, _ = browser_client({})
    client.page.expect_response.side_effect = PlaywrightError("https://fictional.invalid/?token=PRIVATE_QUERY")
    client.page.locator.return_value.count = AsyncMock(return_value=0)
    with caplog.at_level(logging.WARNING), pytest.raises(PlaywrightError):
        await client.fetch_companies()
    assert "Playwright error in fetch_companies." in caplog.text
    assert "PRIVATE" not in caplog.text


async def test_playwright_login_redirect_keeps_session_expiration_path(caplog):
    from playwright.async_api import Error as PlaywrightError
    client, _ = browser_client({})
    client.page.url = "https://tpo.vierp.in/login?token=PRIVATE_QUERY"
    client.page.goto = AsyncMock()
    client.page.wait_for_timeout = AsyncMock()
    client.page.expect_response.side_effect = PlaywrightError("PRIVATE_PLAYWRIGHT_ERROR")
    with caplog.at_level(logging.WARNING), pytest.raises(AuthenticationError, match="redirected to login"):
        await client.fetch_companies()
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("success", [True, False])
async def test_scheduler_logs_actual_outcome_without_extra_effects(success, caplog, monkeypatch):
    from app.monitoring.watcher import PlacementWatcher
    watcher = PlacementWatcher.__new__(PlacementWatcher)
    watcher.notification_pipeline = None
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    watcher.scheduler = SimpleNamespace(tz=timezone.utc,
        get_next_run=MagicMock(side_effect=[now, asyncio.CancelledError()]))
    watcher.check_once = AsyncMock(return_value=success)
    watcher.record_heartbeat_success = MagicMock()
    watcher.record_heartbeat_failure = MagicMock()
    ping = MagicMock()
    monkeypatch.setattr("app.monitoring.watcher.send_healthcheck_ping", ping)
    with caplog.at_level(logging.INFO):
        await watcher.run_forever()
    watcher.check_once.assert_awaited_once_with()
    watcher.record_heartbeat_success.assert_not_called()
    watcher.record_heartbeat_failure.assert_not_called()
    ping.assert_not_called()
    assert ("TPO check completed successfully." in caplog.text) is success
    assert ("TPO check failed." in caplog.text) is (not success)




@pytest.mark.parametrize("body", [{}, {"status": "failure", "company_list": []},
    {"company_list": [{"id": "one", "company": "Fictional"}, {"id": None, "company": "Invalid"}]},
    {"company_list": [{"id": "one", "company": "Fictional"}, {"id": "two", "company": {}}]}])
async def test_real_client_rejection_stops_all_business_writes(body, tmp_path, monkeypatch):
    import sqlite3
    from app.database.repository import DatabaseRepository
    from app.monitoring.watcher import PlacementWatcher
    from app.tpo.models import CompanyRecord
    repo = DatabaseRepository(str(tmp_path / "rejected.sqlite"))
    repo.upsert_company(CompanyRecord(id="existing", company="Fictional Existing", isactive="True"))
    repo.set_baseline_initialized()
    repo.users.create_user("fictional@vit.edu", 2028, "VIT_CE")
    repo.add_pending_notification("existing", "NEW", [])
    repo.set_system_state("last_successful_watcher_run", "prior-success")
    repo.set_system_state("consecutive_watcher_failures", "2")

    def business_snapshot():
        with sqlite3.connect(repo.db_path) as conn:
            return [line for line in conn.iterdump() if '"system_state"' not in line]

    before = business_snapshot()
    client, _ = browser_client(body)
    watcher = PlacementWatcher(db=repo)
    watcher.auth = SimpleNamespace(context=None, is_authenticated=AsyncMock(return_value=True),
        get_valid_page=AsyncMock(return_value=client.page))
    watcher._process_notifications = AsyncMock()
    watcher.delivery_worker = SimpleNamespace(process_batch_once=AsyncMock())
    watcher.fanout = MagicMock()
    monkeypatch.setattr("app.monitoring.watcher.TPOClient", lambda *args: client)
    ping = MagicMock()
    monkeypatch.setattr("app.monitoring.watcher.send_healthcheck_ping", ping)
    assert await watcher.check_once() is False
    assert business_snapshot() == before
    assert repo.get_system_state("last_successful_watcher_run") == "prior-success"
    assert repo.get_system_state("consecutive_watcher_failures") == "3"
    watcher._process_notifications.assert_not_awaited()
    watcher.delivery_worker.process_batch_once.assert_not_awaited()
    watcher.fanout.dispatch_opportunity.assert_not_called()
    ping.assert_called_once()
    assert ping.call_args.kwargs == {"success": False}
