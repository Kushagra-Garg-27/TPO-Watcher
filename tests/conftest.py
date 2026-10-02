import smtplib
import pytest

@pytest.fixture(autouse=True)
def guard_live_smtp(monkeypatch):
    """
    CRITICAL SAFETY GUARDRAIL:
    Prevents any automated test from initiating real SMTP network connections.
    If any test path attempts to instantiate smtplib.SMTP or smtplib.SMTP_SSL,
    this fixture intercepts it and immediately raises an error.
    """
    def _blocked_smtp(*args, **kwargs):
        raise RuntimeError(
            "CRITICAL TEST GUARDRAIL TRIGGERED: Real smtplib.SMTP connection attempted during tests! "
            "All test paths must mock EmailService, EmailNotificationProvider, or SMTP transports."
        )

    monkeypatch.setattr(smtplib, "SMTP", _blocked_smtp)
    if hasattr(smtplib, "SMTP_SSL"):
        monkeypatch.setattr(smtplib, "SMTP_SSL", _blocked_smtp)

@pytest.fixture(autouse=True)
def reset_security_state():
    """
    Resets in-process rate limiter and session revocation state between test runs
    to guarantee test isolation and prevent cascading rate-limit triggers.
    """
    try:
        from app.api.rate_limiter import limiter
        from app.api.session import reset_session_revocations
        limiter.reset()
        reset_session_revocations()
    except ImportError:
        pass
    yield
    try:
        from app.api.rate_limiter import limiter
        from app.api.session import reset_session_revocations
        limiter.reset()
        reset_session_revocations()
    except ImportError:
        pass
