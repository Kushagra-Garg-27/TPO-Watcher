"""Synthetic private provider details must not appear in application logs."""
from unittest.mock import AsyncMock, Mock
import pytest


@pytest.mark.asyncio
async def test_upstream_error_payload_is_not_logged(caplog):
    from app.tpo.client import TPOClient, AuthenticationError
    from tests.unit.test_tpo_listing_envelope_contract import FictionalPage, Response
    marker = 'fictional-private-upstream-token-123'
    page = FictionalPage([Response({'error': True, 'msg': marker})])
    with pytest.raises(AuthenticationError):
        await TPOClient(Mock(), page).fetch_companies()
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_browser_login_exception_is_not_logged(caplog, tmp_path):
    from app.auth.manager import AuthManager
    marker = 'fictional-private-browser-credential-123'
    manager = AuthManager()
    manager.state_file = str(tmp_path / 'session-state.json')
    manager.page = Mock(is_closed=lambda: False, goto=AsyncMock(side_effect=RuntimeError(marker)))
    with pytest.raises(RuntimeError):
        await manager.login()
    assert marker not in caplog.text
