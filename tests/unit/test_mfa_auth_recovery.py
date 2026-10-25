"""
Unit tests for Work Package 1: MFA-Aware Authentication and Non-Destructive Session Recovery.

All tests are 100% hermetic, isolated, and use deterministic fictional browser fixtures.
Zero live network requests to VIERP are permitted or performed.
"""

import os
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.auth.manager import (
    AuthManager,
    AuthState,
    AuthenticationError,
    AuthStateError,
    MFAEnrollmentRequiredError,
    MFAVerificationRequiredError,
    CredentialsRejectedError,
    AuthenticationTimeoutError,
    UnknownAuthenticationStateError,
    ReauthenticationRequiredError,
    get_state_file_path,
)
from app.monitoring.watcher import PlacementWatcher
from app.database.repository import DatabaseRepository
from app.tpo.client import TPOClient
from app.tpo.models import CompanyRecord


class FictionalLocator:
    """Deterministic mock for Playwright Locator."""

    def __init__(self, count=0):
        self._count = count

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._count > 0

    async def wait_for(self, *args, **kwargs):
        if self._count == 0:
            raise TimeoutError("Element not visible")

    async def fill(self, value):
        pass

    async def click(self, *args, **kwargs):
        pass

    @property
    def first(self):
        return self


class FictionalPage:
    """Deterministic mock for Playwright Page."""

    def __init__(self, url="https://tpo.vierp.in/home", selectors=None):
        self.url = url
        self._selectors = selectors or {}
        self._closed = False

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True

    async def goto(self, url, *args, **kwargs):
        self.url = url

    async def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        for pattern, loc in self._selectors.items():
            if pattern in selector:
                return loc
        return FictionalLocator(count=0)


# ==============================================================================
# 1. State Model and Exceptions Hierarchy
# ==============================================================================

def test_auth_state_enum_values():
    assert AuthState.AUTHENTICATED.value == "AUTHENTICATED"
    assert AuthState.MFA_ENROLLMENT_REQUIRED.value == "MFA_ENROLLMENT_REQUIRED"
    assert AuthState.MFA_VERIFICATION_REQUIRED.value == "MFA_VERIFICATION_REQUIRED"
    assert AuthState.REAUTHENTICATION_REQUIRED.value == "REAUTHENTICATION_REQUIRED"
    assert AuthState.CREDENTIALS_REJECTED.value == "CREDENTIALS_REJECTED"
    assert AuthState.AUTHENTICATION_TIMEOUT.value == "AUTHENTICATION_TIMEOUT"
    assert AuthState.UNKNOWN_AUTHENTICATION_STATE.value == "UNKNOWN_AUTHENTICATION_STATE"


def test_auth_exception_hierarchy():
    for exc_cls, expected_state in [
        (MFAEnrollmentRequiredError, AuthState.MFA_ENROLLMENT_REQUIRED),
        (MFAVerificationRequiredError, AuthState.MFA_VERIFICATION_REQUIRED),
        (ReauthenticationRequiredError, AuthState.REAUTHENTICATION_REQUIRED),
        (CredentialsRejectedError, AuthState.CREDENTIALS_REJECTED),
        (AuthenticationTimeoutError, AuthState.AUTHENTICATION_TIMEOUT),
        (UnknownAuthenticationStateError, AuthState.UNKNOWN_AUTHENTICATION_STATE),
    ]:
        err = exc_cls()
        assert isinstance(err, AuthStateError)
        assert isinstance(err, AuthenticationError)
        assert isinstance(err, Exception)
        assert err.state == expected_state


# ==============================================================================
# 2. Origin and Path Validation (Anti-Substring False Positives)
# ==============================================================================

def test_url_validation_rejects_subdomain_spoofing_and_substring_traps():
    manager = AuthManager()

    # Reject substring "home" in query parameters or foreign domains
    assert manager._parse_and_validate_url("https://attacker.com/home")[0] is False
    assert manager._parse_and_validate_url("https://attacker.com?tpo.vierp.in")[0] is False
    assert manager._parse_and_validate_url("https://tpo.vierp.in.attacker.com/home")[0] is False
    assert manager._parse_and_validate_url("")[0] is False

    # Valid origin and paths
    valid, path = manager._parse_and_validate_url("https://tpo.vierp.in/home")
    assert valid is True
    assert path == "/home"

    valid, path = manager._parse_and_validate_url("https://tpo.vierp.in/company-dashboard")
    assert valid is True
    assert path == "/company-dashboard"

    valid, path = manager._parse_and_validate_url("http://localhost:8000/home")
    assert valid is True
    assert path == "/home"


# ==============================================================================
# 3. is_authenticated() Positive and Negative Determinations
# ==============================================================================

@pytest.mark.asyncio
async def test_is_authenticated_returns_true_for_authenticated_dashboard():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/company-dashboard",
        selectors={
            "company-dashboard": FictionalLocator(count=1),
            "logout": FictionalLocator(count=1),
            "password": FictionalLocator(count=0),
        }
    )
    assert await manager.is_authenticated() is True


@pytest.mark.asyncio
async def test_is_authenticated_returns_false_when_password_field_present():
    manager = AuthManager()
    # Even if URL is /home, a password field indicates an unauthenticated/login screen
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/home",
        selectors={
            "password": FictionalLocator(count=1),
            "logout": FictionalLocator(count=0),
        }
    )
    assert await manager.is_authenticated() is False


@pytest.mark.asyncio
async def test_is_authenticated_returns_false_when_redirected_to_mfa():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/auth/mfa-challenge",
        selectors={
            "authenticator": FictionalLocator(count=1),
            "otp": FictionalLocator(count=1),
            "password": FictionalLocator(count=0),
        }
    )
    assert await manager.is_authenticated() is False


# ==============================================================================
# 4. Screen Classification (MFA Enrollment, Verification, Rejection, Timeout)
# ==============================================================================

@pytest.mark.asyncio
async def test_classify_screen_mfa_enrollment():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/setup",
        selectors={
            "authenticator": FictionalLocator(count=1),
            "qr": FictionalLocator(count=1),
            "otp": FictionalLocator(count=1),
        }
    )
    assert await manager.classify_current_screen() == AuthState.MFA_ENROLLMENT_REQUIRED


@pytest.mark.asyncio
async def test_classify_screen_mfa_verification():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/verify",
        selectors={
            "verification": FictionalLocator(count=1),
            "otp": FictionalLocator(count=1),
            "password": FictionalLocator(count=0),
        }
    )
    assert await manager.classify_current_screen() == AuthState.MFA_VERIFICATION_REQUIRED


@pytest.mark.asyncio
async def test_classify_screen_credentials_rejected():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/login",
        selectors={
            "alert": FictionalLocator(count=1),
            "invalid": FictionalLocator(count=1),
            "password": FictionalLocator(count=1),
        }
    )
    assert await manager.classify_current_screen() == AuthState.CREDENTIALS_REJECTED


@pytest.mark.asyncio
async def test_classify_screen_unknown_state_fails_closed():
    manager = AuthManager()
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/some-strange-portal-path",
        selectors={}
    )
    assert await manager.classify_current_screen() == AuthState.UNKNOWN_AUTHENTICATION_STATE


# ==============================================================================
# 5. Non-Destructive Session Preservation
# ==============================================================================

@pytest.mark.asyncio
async def test_failed_login_never_deletes_existing_session_file(tmp_path):
    state_file = tmp_path / "playwright_state.json"
    original_bytes = b'{"cookies":[{"name":"auth","value":"secret-token"}]}'
    state_file.write_bytes(original_bytes)

    manager = AuthManager()
    manager.state_file = str(state_file)
    manager.context = MagicMock()
    manager.start = AsyncMock()

    # Simulate login failure due to MFA challenge
    broken_page = FictionalPage(
        url="https://tpo.vierp.in/login",
        selectors={
            "input[name='username']": FictionalLocator(count=1),
            "input[name='password']": FictionalLocator(count=1),
            "button[type='submit']": FictionalLocator(count=1),
            "authenticator": FictionalLocator(count=1),
            "qr": FictionalLocator(count=1),
        }
    )
    manager.page = broken_page

    result = await manager.login()
    assert result == AuthState.MFA_ENROLLMENT_REQUIRED

    # Verify session file was NOT deleted and bytes remain identical
    assert state_file.exists()
    assert state_file.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_save_storage_state_safe_uses_atomic_replacement(tmp_path):
    subdir = tmp_path / "protected_sessions"
    state_file = subdir / "playwright_state.json"

    manager = AuthManager()
    manager.state_file = str(state_file)

    mock_context = MagicMock()
    async def fake_storage_state(path):
        with open(path, "w") as f:
            f.write('{"cookies":[{"name":"fresh","value":"ok"}]}')
    mock_context.storage_state = fake_storage_state
    manager.context = mock_context

    await manager.save_storage_state_safe()

    assert state_file.exists()
    assert json.loads(state_file.read_text()) == {"cookies": [{"name": "fresh", "value": "ok"}]}
    assert subdir.exists()


def test_clear_session_state_removes_file(tmp_path):
    state_file = tmp_path / "playwright_state.json"
    state_file.write_text("{}")

    manager = AuthManager()
    manager.state_file = str(state_file)

    assert state_file.exists()
    manager.clear_session_state()
    assert not state_file.exists()


# ==============================================================================
# 6. get_valid_page() Guarantee: Cannot Return Page on MFA or Auth Failure
# ==============================================================================

@pytest.mark.asyncio
async def test_get_valid_page_raises_on_mfa_enrollment():
    manager = AuthManager()
    manager.page = FictionalPage()
    manager.context = MagicMock()
    manager.start = AsyncMock()
    manager.is_authenticated = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=AuthState.MFA_ENROLLMENT_REQUIRED)

    with pytest.raises(MFAEnrollmentRequiredError) as exc_info:
        await manager.get_valid_page()
    assert exc_info.value.state == AuthState.MFA_ENROLLMENT_REQUIRED


@pytest.mark.asyncio
async def test_get_valid_page_raises_on_mfa_verification():
    manager = AuthManager()
    manager.page = FictionalPage()
    manager.context = MagicMock()
    manager.start = AsyncMock()
    manager.is_authenticated = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=AuthState.MFA_VERIFICATION_REQUIRED)

    with pytest.raises(MFAVerificationRequiredError) as exc_info:
        await manager.get_valid_page()
    assert exc_info.value.state == AuthState.MFA_VERIFICATION_REQUIRED


@pytest.mark.asyncio
async def test_get_valid_page_raises_on_credentials_rejected():
    manager = AuthManager()
    manager.page = FictionalPage()
    manager.context = MagicMock()
    manager.start = AsyncMock()
    manager.is_authenticated = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=AuthState.CREDENTIALS_REJECTED)

    with pytest.raises(CredentialsRejectedError) as exc_info:
        await manager.get_valid_page()
    assert exc_info.value.state == AuthState.CREDENTIALS_REJECTED


@pytest.mark.asyncio
async def test_get_valid_page_raises_on_timeout():
    manager = AuthManager()
    manager.page = FictionalPage()
    manager.context = MagicMock()
    manager.start = AsyncMock()
    manager.is_authenticated = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=AuthState.AUTHENTICATION_TIMEOUT)

    with pytest.raises(AuthenticationTimeoutError) as exc_info:
        await manager.get_valid_page()
    assert exc_info.value.state == AuthState.AUTHENTICATION_TIMEOUT


# ==============================================================================
# 7. PlacementWatcher: MFA Pause and Retry Suppression
# ==============================================================================

@pytest.mark.asyncio
async def test_watcher_pauses_on_mfa_without_credential_retry_storm(tmp_path):
    db_path = str(tmp_path / "test_mfa.sqlite")
    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()

    watcher = PlacementWatcher(db=repo)
    watcher.auth = AuthManager()
    watcher.auth.is_authenticated = AsyncMock(return_value=False)
    watcher.auth.get_valid_page = AsyncMock(side_effect=MFAVerificationRequiredError())
    watcher.auth.stop = AsyncMock()

    # First check: encounters MFA challenge
    success = await watcher.check_once()
    assert success is False

    # Assert state was recorded
    assert repo.get_system_state("mfa_pending") == "true"
    assert repo.get_system_state("auth_status") == AuthState.MFA_VERIFICATION_REQUIRED.value
    assert repo.get_system_state("consecutive_watcher_failures") == "1"

    # Subsequent check while paused: should NOT call get_valid_page or retry credentials
    watcher.auth.get_valid_page.reset_mock()
    second_check = await watcher.check_once()
    assert second_check is False
    # Verified: zero attempts to get_valid_page while paused
    assert watcher.auth.get_valid_page.await_count == 0
    assert repo.get_system_state("consecutive_watcher_failures") == "2"


@pytest.mark.asyncio
async def test_watcher_resumes_after_session_file_refreshed(tmp_path):
    db_path = str(tmp_path / "test_mfa_refresh.sqlite")
    state_file = tmp_path / "playwright_state.json"
    state_file.write_text('{"cookies":[{"name":"auth","value":"valid"}]}')

    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()
    repo.set_system_state("mfa_pending", "true")
    # Record paused mtime as in the past
    repo.set_system_state("mfa_paused_state_mtime", str(time.time() - 100))

    watcher = PlacementWatcher(db=repo)
    watcher.auth.state_file = str(state_file)
    watcher.auth.is_authenticated = AsyncMock(return_value=True)
    watcher.auth.get_valid_page = AsyncMock(return_value=FictionalPage())
    watcher.auth.stop = AsyncMock()

    # Touch the file to give it a newer mtime
    os.utime(str(state_file), None)

    # 1. Initially is_mfa_paused is still True (pause not cleared on mtime alone)
    assert watcher.is_mfa_paused() is True
    assert watcher.has_session_file_been_refreshed() is True

    # 2. Check execution: verifies the refreshed session, clears pause, and succeeds
    with patch("app.monitoring.watcher.TPOClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_companies = AsyncMock(return_value=[])
        mock_client_cls.return_value = mock_client
        result = await watcher.check_once()

    assert result is True
    assert repo.get_system_state("mfa_pending") == "false"
    assert repo.get_system_state("auth_status") == AuthState.AUTHENTICATED.value


@pytest.mark.asyncio
async def test_watcher_reset_auth_pause_method(tmp_path):
    db_path = str(tmp_path / "test_mfa_reset.sqlite")
    repo = DatabaseRepository(db_path)
    repo.set_system_state("mfa_pending", "true")

    watcher = PlacementWatcher(db=repo)
    watcher.reset_auth_pause()

    assert repo.get_system_state("mfa_pending") == "false"
    assert repo.get_system_state("auth_status") == "RESET_REQUESTED"


# ==============================================================================
# 8. Downstream Safety: Zero Writes and Zero Notifications on Auth Failure
# ==============================================================================

@pytest.mark.asyncio
async def test_auth_failure_preserves_company_records_and_sends_no_notifications(tmp_path):
    db_path = str(tmp_path / "test_safety.sqlite")
    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()

    # Pre-populate one active company
    rec = CompanyRecord(
        id="101",
        company="Cummins India",
        isactive="True",
    )
    repo.upsert_company(rec, is_new=True)
    assert repo.get_company("101")["is_active"] == "True"

    watcher = PlacementWatcher(db=repo)
    watcher.auth = AuthManager()
    watcher.auth.is_authenticated = AsyncMock(return_value=False)
    watcher.auth.get_valid_page = AsyncMock(side_effect=MFAEnrollmentRequiredError())
    watcher.auth.stop = AsyncMock()

    # Run check under MFA failure
    result = await watcher.check_once()
    assert result is False

    # Safety assertions:
    # 1. Active company was NOT deactivated or altered
    assert repo.get_company("101")["is_active"] == "True"
    # 2. No pending notifications were created
    assert len(repo.get_pending_notifications()) == 0
    # 3. No deliveries were recorded
    with repo._get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM notification_deliveries WHERE company_id = ?", ("101",)).fetchone()[0]
    assert count == 0
    # 4. Heartbeat failure recorded accurately
    assert int(repo.get_system_state("consecutive_watcher_failures")) >= 1


# ==============================================================================
# 9. Adversarial Review Hardening Regressions
# ==============================================================================

@pytest.mark.asyncio
async def test_mfa_paused_state_does_not_clear_or_retry_credentials_on_invalid_session_touch(tmp_path):
    """
    Risk 1 & 2: Touching session file must NOT clear mfa_pending or trigger
    unattended credential retries if the replacement session does not authenticate.
    """
    db_path = str(tmp_path / "test_mfa_touch.sqlite")
    state_file = tmp_path / "playwright_state.json"
    state_file.write_text('{"cookies":[]}')

    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()
    repo.set_system_state("mfa_pending", "true")
    repo.set_system_state("auth_status", AuthState.MFA_VERIFICATION_REQUIRED.value)
    # Pause timestamp in the past
    past_mtime = time.time() - 200
    repo.set_system_state("mfa_paused_state_mtime", str(past_mtime))

    # Touch file so curr_mtime > past_mtime
    os.utime(str(state_file), None)

    watcher = PlacementWatcher(db=repo)
    watcher.auth = AuthManager()
    watcher.auth.state_file = str(state_file)
    watcher.auth.context = MagicMock()
    watcher.auth.start = AsyncMock()
    watcher.auth.stop = AsyncMock()

    # The replacement session fails authentication (e.g. invalid/expired cookies)
    watcher.auth.is_authenticated = AsyncMock(return_value=False)
    watcher.auth.login = AsyncMock()

    # Run check_once
    result = await watcher.check_once()
    assert result is False

    # CRITICAL: login() must NEVER be called with credentials
    assert watcher.auth.login.await_count == 0
    # CRITICAL: mfa_pending must remain true
    assert repo.get_system_state("mfa_pending") == "true"


@pytest.mark.asyncio
async def test_http_403_does_not_destroy_reusable_session_file(tmp_path):
    """
    Risk 3: Ambiguous HTTP 403 authorization failures must not delete the session file
    during watcher retry or recovery attempts.
    """
    db_path = str(tmp_path / "test_403.sqlite")
    state_file = tmp_path / "playwright_state.json"
    original_bytes = b'{"cookies":[{"name":"auth","value":"valid-token"}]}'
    state_file.write_bytes(original_bytes)

    repo = DatabaseRepository(db_path)
    repo.set_baseline_initialized()

    watcher = PlacementWatcher(db=repo)
    watcher.auth = AuthManager()
    watcher.auth.state_file = str(state_file)
    watcher.auth.is_authenticated = AsyncMock(return_value=True)
    watcher.auth.get_valid_page = AsyncMock(return_value=FictionalPage())
    watcher.auth.stop = AsyncMock()

    # Simulate fetch_companies raising AuthenticationError (HTTP 401/403)
    mock_fresh_auth = MagicMock()
    mock_fresh_auth.state_file = str(state_file)
    mock_fresh_auth.is_authenticated = AsyncMock(return_value=False)
    mock_fresh_auth.get_valid_page = AsyncMock(side_effect=ReauthenticationRequiredError())
    mock_fresh_auth.stop = AsyncMock()

    with patch("app.monitoring.watcher.TPOClient") as mock_client_cls, \
         patch("app.monitoring.watcher.AuthManager", return_value=mock_fresh_auth):
        mock_client = MagicMock()
        mock_client.fetch_companies = AsyncMock(side_effect=AuthenticationError("Session expired (HTTP 401/403)"))
        mock_client_cls.return_value = mock_client

        # Run check_once
        result = await watcher.check_once()

    assert result is False

    # CRITICAL: Session file must NOT be deleted!
    assert state_file.exists()
    assert state_file.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_has_authenticated_dom_markers_requires_specific_post_login_elements():
    """
    Risk 8: Generic /home link must NOT falsely satisfy authenticated DOM markers.
    """
    manager = AuthManager()
    # Page with only a generic /home link (e.g. brand logo) but no logout or dashboard
    manager.page = FictionalPage(
        url="https://tpo.vierp.in/home",
        selectors={
            "a[href='/home']": FictionalLocator(count=1),
            "a[href='/company-dashboard']": FictionalLocator(count=0),
            "logout": FictionalLocator(count=0),
            "password": FictionalLocator(count=0),
        }
    )
    # Must return False because specific authenticated elements (dashboard/logout) are absent
    assert await manager._has_authenticated_dom_markers() is False

