"""F08: fictional canary values must never reach shared operational logs.

Covers the real provider success/failure paths, the rate limiter boundary and
the auth-manager error paths that previously interpolated free-form values.
"""
import asyncio
import logging

import pytest

from app.api import rate_limiter
from app.auth.manager import AuthManager
from app.config import settings
from app.notifications.email import EmailNotificationProvider

CANARY_EMAIL = "canary-student@example.invalid"
CANARY_TOKEN = "PRIVATE-TOKEN-canary-9f1a"
CANARY_SMTP_DETAIL = "PRIVATE-SMTP-REJECTION canary-student@example.invalid"
CANARY_BROWSER_ERR = "PRIVATE-BROWSER-ERROR canary-student@example.invalid"


@pytest.fixture
def capture(caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    return caplog


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, msg):
        self.sent = msg


class RejectingSMTP(FakeSMTP):
    def starttls(self):
        raise RuntimeError(CANARY_SMTP_DETAIL)


def _provider_email_path(monkeypatch, smtp_cls):
    import smtplib as smtplib_module
    monkeypatch.setattr(smtplib_module, "SMTP", smtp_cls)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test.invalid")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "mailer@test.invalid")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "not-a-real-secret")
    monkeypatch.setattr(settings, "EMAIL_FROM", "mailer@test.invalid")
    provider = EmailNotificationProvider()
    return provider._send_email_to(CANARY_EMAIL, "Subject canary-student@example.invalid",
                                   "<p>body</p>")


def test_email_success_log_has_no_recipient_or_subject(capture, monkeypatch):
    assert _provider_email_path(monkeypatch, FakeSMTP) is True
    text = capture.text
    assert "accepted by provider" in text
    assert CANARY_EMAIL not in text


def test_email_failure_log_has_no_recipient_or_provider_detail(capture, monkeypatch):
    assert _provider_email_path(monkeypatch, RejectingSMTP) is False
    text = capture.text
    assert "dispatch failed" in text
    assert CANARY_EMAIL not in text
    assert CANARY_SMTP_DETAIL not in text


class _FakeRequest:
    class _Headers(dict):
        def get(self, item, default=None):
            return super().get(item.lower(), default)

    headers = _Headers()
    client = None


def test_rate_limit_log_uses_bounded_fingerprint(capture):
    key = f"signup:email:{CANARY_EMAIL}"
    for _ in range(5):
        rate_limiter.limiter.check(key, max_requests=5, window_seconds=900)
    with pytest.raises(Exception):
        rate_limiter.check_rate_limit(_FakeRequest(), key=key, max_requests=5, window_seconds=900)
    text = capture.text
    assert "Rate limit exceeded" in text
    assert "key_id=" in text
    assert CANARY_EMAIL not in text
    assert key not in text


class _BrokenPage:
    url = "https://fictional.example/somewhere?token=" + CANARY_TOKEN

    def is_closed(self):
        return False

    async def goto(self, *args, **kwargs):
        raise RuntimeError(CANARY_BROWSER_ERR)

    async def wait_for_timeout(self, ms):
        pass


def test_auth_status_and_login_errors_log_category_only(capture):
    manager = AuthManager()
    manager.page = _BrokenPage()

    asyncio.run(manager.is_authenticated())
    with pytest.raises(RuntimeError):
        asyncio.run(manager.login())

    text = capture.text
    assert "status check failed" in text
    assert "Login failed" in text
    assert CANARY_BROWSER_ERR not in text
    assert CANARY_TOKEN not in text
    assert CANARY_EMAIL not in text
