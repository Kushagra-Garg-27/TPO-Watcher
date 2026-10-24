# This must precede pytest fixtures and every application import.
import isolation_bootstrap as isolation
isolation.install()

import smtplib
import pytest


def pytest_configure(config):
    # Plain pytest also confines its output/fixture locations to this run's root.
    config.option.basetemp = str(isolation.ROOT / "pytest")
    config.option.log_file = str(isolation.ROOT / "pytest.log")
    config._inicache["cache_dir"] = str(isolation.ROOT / "cache")


@pytest.fixture(autouse=True)
def guard_suppressed_isolation_errors(request):
    # Real transports sometimes catch RuntimeError; swallowed denials still fail.
    isolation.assert_no_violations()
    if request.node.name == "test_uvicorn_live_server_header_absent":
        with isolation.local_server_test():
            yield
    elif request.node.name == "test_smtp_safety_guardrail_prevents_real_email_dispatch":
        # This unchanged legacy regression intentionally tries the denied SMTP
        # constructor and checks the provider's exception-to-False behavior.
        with isolation.expected_violation("real SMTP transport disabled"):
            yield
    else:
        yield
    isolation.assert_no_violations()

@pytest.fixture(autouse=True)
def guard_live_smtp(monkeypatch):
    """
    CRITICAL SAFETY GUARDRAIL:
    Prevents any automated test from initiating real SMTP network connections.
    If any test path attempts to instantiate smtplib.SMTP or smtplib.SMTP_SSL,
    this fixture intercepts it and immediately raises an error.
    """
    def _blocked_smtp(*args, **kwargs):
        isolation.blocked_smtp(*args, **kwargs)

    monkeypatch.setattr(smtplib, "SMTP", _blocked_smtp)
    if hasattr(smtplib, "SMTP_SSL"):
        monkeypatch.setattr(smtplib, "SMTP_SSL", _blocked_smtp)

@pytest.fixture(autouse=True)
def guard_live_database(tmp_path, monkeypatch):
    """
    CRITICAL SAFETY GUARDRAIL:
    Prevents any test from writing to or reading from the root watcher.sqlite database.
    Redirects the default DB_PATH to an isolated temporary test database.
    """
    test_db = str(tmp_path / "test_isolated_default.sqlite")
    # Legacy watcher retries use a relative state filename. A disposable cwd
    # prevents even metadata checks from resolving against private project data.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.database.repository.DB_PATH", test_db)
    monkeypatch.setattr("app.health.server.DB_PATH", test_db)
    monkeypatch.setattr("app.api.routes_opportunities.DB_PATH", test_db)
    # Bound default arguments were captured before fixtures; their path is also
    # inside the disposable root. Initialize the synthetic default for routes
    # which intentionally exercise the original un-overridden dependencies.
    from app.database.repository import DatabaseRepository
    DatabaseRepository(test_db)

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
