import pytest
import asyncio
from unittest.mock import MagicMock, patch
from app.tpo.models import CompanyRecord
from app.tpo.client import TPOClient
from app.monitoring.detector import CompanyDetector
from app.monitoring.watcher import PlacementWatcher
from app.database.repository import DatabaseRepository

@pytest.fixture
def temp_watcher_db(tmp_path):
    db_path = str(tmp_path / "test_watcher_pipeline.sqlite")
    repo = DatabaseRepository(db_path)
    return repo

@pytest.mark.asyncio
async def test_end_to_end_watcher_with_v1_fanout_and_admin_notification(temp_watcher_db):
    watcher = PlacementWatcher(db=temp_watcher_db)
    watcher.db = temp_watcher_db
    watcher.detector = CompanyDetector(temp_watcher_db)
    # Hook repos
    watcher.fanout.user_repo = temp_watcher_db.users
    watcher.fanout.delivery_repo = temp_watcher_db.deliveries
    watcher.delivery_worker.delivery_repo = temp_watcher_db.deliveries
    watcher.delivery_worker.token_repo = temp_watcher_db.tokens

    # Register subscribers:
    # 1. CE student 2028 (Verified, active)
    u_ce = temp_watcher_db.users.create_user("ce2028@vit.edu", 2028, "VIT_CE")
    temp_watcher_db.users.set_verified(u_ce)

    # 2. IT student 2028 (Verified, active)
    u_it = temp_watcher_db.users.create_user("it2028@vit.edu", 2028, "VIT_IT")
    temp_watcher_db.users.set_verified(u_it)

    # 3. ENTC student 2028 (Verified, active - but company is CE only)
    u_entc = temp_watcher_db.users.create_user("entc2028@vit.edu", 2028, "VIT_ENTC")
    temp_watcher_db.users.set_verified(u_entc)

    # 4. CE student 2028 (Unverified)
    u_unverified = temp_watcher_db.users.create_user("unverified2028@vit.edu", 2028, "VIT_CE")

    # Mocks
    mock_notifier = MagicMock()
    mock_notifier.send_new_company_notification.return_value = True
    mock_notifier._send_email_to.return_value = True
    watcher.notifier = mock_notifier
    watcher.delivery_worker.notifier = mock_notifier

    # Baseline run
    base_company = CompanyRecord(
        id="100",
        company="Old Corp",
        placementtype="Placement",
        tpoprogram="VIT-BTech-Computer Engineering",
        organization="VIT"
    )
    with patch.object(TPOClient, "fetch_companies", return_value=[base_company]):
        with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
            with patch.object(watcher.auth, "is_authenticated", return_value=True):
                await watcher.check_once()

    # Baseline should not trigger any notifications
    assert mock_notifier.send_new_company_notification.call_count == 0
    assert mock_notifier._send_email_to.call_count == 0

    # New cycle: New company "Mastercard" (CE & IT eligible, Internship + PPO)
    new_company = CompanyRecord(
        id="5610",
        company="Mastercard",
        placementtype="Internship + Performance based PPO",
        tpoprogram="VIT-BTech-Computer Engineering, VIT-BTech-Information Technology",
        programnew="BTech-Computer Engineering, BTech-Information Technology",
        organization="VIT"
    )

    with patch.object(TPOClient, "fetch_companies", return_value=[base_company, new_company]):
        with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
            with patch.object(watcher.auth, "is_authenticated", return_value=True):
                await watcher.check_once()

    # Verification:
    # 1. Independent Admin safety channel fired:
    assert mock_notifier.send_new_company_notification.call_count == 1

    # 2. Student Subscriber deliveries:
    # u_ce and u_it matched and received emails
    # u_entc (branch mismatch) and u_unverified (unverified) did NOT receive emails
    assert mock_notifier._send_email_to.call_count == 2
    recipient_emails = [call[0][0] for call in mock_notifier._send_email_to.call_args_list]
    assert "ce2028@vit.edu" in recipient_emails
    assert "it2028@vit.edu" in recipient_emails
    assert "entc2028@vit.edu" not in recipient_emails
    assert "unverified2028@vit.edu" not in recipient_emails

    # 3. Deliveries marked SENT in DB
    stats = temp_watcher_db.deliveries.get_delivery_stats()
    assert stats["SENT"] == 2
    assert stats["PENDING"] == 0

    # 4. Cycle 3 (Same companies): Idempotency check -> 0 new notifications
    mock_notifier.send_new_company_notification.reset_mock()
    mock_notifier._send_email_to.reset_mock()

    with patch.object(TPOClient, "fetch_companies", return_value=[base_company, new_company]):
        with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
            with patch.object(watcher.auth, "is_authenticated", return_value=True):
                await watcher.check_once()

    assert mock_notifier.send_new_company_notification.call_count == 0
    assert mock_notifier._send_email_to.call_count == 0
