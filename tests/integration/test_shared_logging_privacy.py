"""Privacy at the actual shared console sink and watcher failure boundaries.

All values are fictional. No browser, provider, AWS or production database is used.
"""
import asyncio
import io
import logging
import sys
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.logging_config import setup_logging
from app.tpo.client import AuthenticationError
from test_cummins_pipeline_stress import audit_repo, safe_watcher, FakeNotifier


@pytest.fixture
def console(monkeypatch):
    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (logger.handlers[:], logger.level, logger.propagate)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(settings, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "fictional-provider-password-82")
    setup_logging()
    try:
        yield stream
    finally:
        for name, (handlers, level, propagate) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate


@pytest.mark.parametrize("message,private", [
    ("recipient=student-canary@example.invalid", "student-canary@example.invalid"),
    ("request https://source.invalid/api?token=fictional-query-secret", "fictional-query-secret"),
    ("provider said fictional-provider-password-82", "fictional-provider-password-82"),
    ('payload {"access_token": "fictional-access-secret"}', "fictional-access-secret"),
    ("Authorization: Bearer fictional-bearer-secret", "fictional-bearer-secret"),
    ("Cookie: sid=fictional-cookie-secret; refresh=fictional-refresh-secret", "fictional-cookie-secret"),
    ("headers={'Authorization': 'Bearer fictional-dict-bearer'}", "fictional-dict-bearer"),
    ('headers={"Cookie": "sid=fictional-dict-cookie"}', "fictional-dict-cookie"),
])
def test_shared_sink_redacts_sensitive_values_from_nested_logger(console, message, private):
    logging.getLogger("app.third_party_boundary").warning(message)
    output = console.getvalue()
    assert private not in output
    assert "[redacted]" in output


def test_oversized_message_is_withheld_before_pattern_redaction(console):
    # Withhold the whole value: truncating first could expose part of a secret.
    message = "x" * 4090 + "fictional-provider-password-82" + "y" * 4090
    logging.getLogger("app.provider").error(message)
    output = console.getvalue()
    assert "log_message_oversized" in output
    assert "x" * 100 not in output
    assert "fictional-provider" not in output
    assert len(output) < 200


def test_shared_sink_suppresses_exception_arguments_and_traceback_text(console):
    private = "fictional-unstructured-provider-rejection-384"
    try:
        raise RuntimeError(private)
    except RuntimeError as exc:
        logging.getLogger("app.provider").exception("Dispatch failed: %s", exc)
    output = console.getvalue()
    assert private not in output
    assert "Dispatch failed" in output
    assert "exception_details_suppressed" in output


def test_exception_object_as_message_is_not_stringified(console):
    logging.getLogger("app.provider").error(
        RuntimeError("fictional-unstructured-message-secret"))
    output = console.getvalue()
    assert "fictional-unstructured-message-secret" not in output
    assert "exception_details_suppressed" in output


def test_invalid_format_never_falls_back_to_logging_error_dump(console, capsys):
    record = logging.LogRecord("app.provider", logging.ERROR, __file__, 1,
                               "count=%d", ("fictional-format-secret",), None)
    # Exercise the configured production sink. Pytest installs additional raw
    # capture handlers during its call phase; they are not application sinks.
    logging.getLogger().handlers[0].handle(record)
    output = console.getvalue()
    assert "fictional-format-secret" not in output
    assert "fictional-format-secret" not in capsys.readouterr().err
    assert "log_message_unavailable" in output


def test_shared_sink_drops_cached_exception_and_stack_text(console):
    record = logging.LogRecord("app.provider", logging.ERROR, __file__, 1,
                               "Dispatch failed", (), None)
    record.exc_text = "fictional-cached-exception-secret"
    record.stack_info = "fictional-stack-secret"
    logging.getLogger(record.name).handle(record)
    output = console.getvalue()
    assert "fictional-cached-exception-secret" not in output
    assert "fictional-stack-secret" not in output
    assert "Dispatch failed" in output


def test_uvicorn_error_uses_same_safe_sink_after_application_config(console):
    from app.main import get_uvicorn_config

    get_uvicorn_config(object())  # Configuration only: never binds or serves.
    try:
        raise RuntimeError("fictional-asgi-exception-secret")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("ASGI operation failed")
    output = console.getvalue()
    assert "fictional-asgi-exception-secret" not in output
    assert "ASGI operation failed" in output
    assert "exception_details_suppressed" in output


def test_shared_sink_preserves_fixed_source_diagnostic_evidence(console):
    evidence = (
        "Source response rejected: category=invalid_status http_status=200 "
        "content_type=application/json json_type=object status_type=string "
        "company_list_type=array company_list_length=8 status_category=other_string"
    )
    logging.getLogger("app.tpo.client").warning(evidence)
    assert evidence in console.getvalue()


@pytest.mark.asyncio
async def test_authentication_exception_never_enters_raw_log_record(audit_repo, monkeypatch, caplog):
    notifier = FakeNotifier()
    watcher, state = safe_watcher(audit_repo, monkeypatch, notifier, [[]])
    auth_type = type(watcher.auth)
    monkeypatch.setattr(auth_type, "is_authenticated", AsyncMock(
        side_effect=AuthenticationError("fictional-unstructured-auth-secret")))
    monkeypatch.setattr(auth_type, "stop", AsyncMock(), raising=False)
    caplog.set_level(logging.INFO, logger="app")

    assert await watcher.check_once() is False
    assert "fictional-unstructured-auth-secret" not in caplog.text
    assert "Authentication failure detected" in caplog.text
    assert state["fetches"] == 0
    assert notifier.student_calls == notifier.admin_calls == 0
    assert audit_repo.get_system_state("consecutive_watcher_failures") == "1"
    assert audit_repo.get_system_state("last_successful_watcher_run") is None


@pytest.mark.parametrize("method,scheduler_name", [
    ("run_forever", "scheduler"),
    ("run_pruning_loop", "prune_scheduler"),
])
@pytest.mark.asyncio
async def test_scheduler_exception_never_enters_raw_log_record(
        audit_repo, monkeypatch, caplog, method, scheduler_name):
    watcher, state = safe_watcher(audit_repo, monkeypatch, FakeNotifier(), [[]])
    scheduler = getattr(watcher, scheduler_name)
    scheduler.timezone_name = "Asia/Kolkata"

    def fail_schedule():
        raise RuntimeError("fictional-unstructured-scheduler-secret")

    scheduler.get_next_run = fail_schedule
    monkeypatch.setattr("app.monitoring.watcher.asyncio.sleep",
                        AsyncMock(side_effect=asyncio.CancelledError))
    caplog.set_level(logging.INFO, logger="app")
    with pytest.raises(asyncio.CancelledError):
        await getattr(watcher, method)()
    assert "fictional-unstructured-scheduler-secret" not in caplog.text
    assert "Unexpected error" in caplog.text
    assert state["fetches"] == 0
    assert audit_repo.get_system_state("consecutive_watcher_failures") is None
