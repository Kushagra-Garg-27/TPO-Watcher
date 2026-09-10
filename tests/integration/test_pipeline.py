import os
import gc
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.database.repository import DatabaseRepository
from app.monitoring.detector import CompanyDetector
from app.monitoring.watcher import PlacementWatcher
from app.tpo.models import CompanyRecord
from app.tpo.client import TPOClient, AuthenticationError
from app.auth.manager import AuthManager

@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test_pipeline.sqlite")
    repo = DatabaseRepository(db_path)
    yield repo
    gc.collect()

def test_company_record_schema_coercion():
    record = CompanyRecord(
        id=5610,
        company="Mastercard",
        maxPackage=16.0,
        minPackage=0.0,
        isactive=True,
        tpoprogram=["VIT-BTech-CS", "VIT-BTech-IT"],
        organization=["VIT", "VIIT"]
    )
    assert record.id == "5610"
    assert record.maxPackage == "16.0"
    assert record.minPackage == "0.0"
    assert record.isactive == "True"
    assert "VIT-BTech-CS" in record.tpoprogram
    assert "VIT, VIIT" == record.organization

def test_baseline_and_duplicate_prevention(temp_db):
    detector = CompanyDetector(temp_db)
    companies = [
        CompanyRecord(id="1", company="A"),
        CompanyRecord(id="2", company="B")
    ]
    
    # Run 1: Baseline
    res1 = detector.process_fetched_companies(companies)
    assert temp_db.is_baseline_initialized()
    assert len(res1.new_companies) == 0
    assert len(temp_db.get_pending_notifications()) == 0
    
    # Run 2: Duplicate run
    res2 = detector.process_fetched_companies(companies)
    assert len(res2.new_companies) == 0
    assert len(temp_db.get_pending_notifications()) == 0

def test_simulated_new_company_and_no_duplicates(temp_db):
    detector = CompanyDetector(temp_db)
    base = [CompanyRecord(id="1", company="A")]
    detector.process_fetched_companies(base)
    
    # Cycle 1: Add new company
    cycle1 = base + [CompanyRecord(id="2", company="B")]
    res1 = detector.process_fetched_companies(cycle1)
    assert len(res1.new_companies) == 1
    assert res1.new_companies[0].id == "2"
    assert len(temp_db.get_pending_notifications()) == 1
    
    # Cycle 2: Same fixture
    res2 = detector.process_fetched_companies(cycle1)
    assert len(res2.new_companies) == 0
    assert len(temp_db.get_pending_notifications()) == 1

@pytest.mark.asyncio
async def test_notification_retry_lifecycle(temp_db):
    watcher = PlacementWatcher()
    watcher.db = temp_db
    watcher.detector = CompanyDetector(temp_db)
    
    base = [CompanyRecord(id="1", company="A")]
    watcher.detector.process_fetched_companies(base)
    
    # New company -> PENDING
    watcher.detector.process_fetched_companies(base + [CompanyRecord(id="2", company="B")])
    assert len(watcher.db.get_pending_notifications()) == 1
    
    # Failure -> remains PENDING
    mock_notifier = MagicMock()
    mock_notifier.send_new_company_notification.return_value = False
    watcher.notifier = mock_notifier
    await watcher._process_notifications()
    assert len(watcher.db.get_pending_notifications()) == 1
    
    # Retry -> succeeds -> removed from PENDING
    mock_notifier.send_new_company_notification.return_value = True
    await watcher._process_notifications()
    assert len(watcher.db.get_pending_notifications()) == 0
    assert watcher.db.get_company("2")["last_notified_at"] is not None
    
    # Later cycles do not resend
    mock_notifier.send_new_company_notification.reset_mock()
    watcher.detector.process_fetched_companies(base + [CompanyRecord(id="2", company="B")])
    await watcher._process_notifications()
    assert mock_notifier.send_new_company_notification.call_count == 0

@pytest.mark.asyncio
async def test_api_failure_safety_scenarios(temp_db):
    watcher = PlacementWatcher()
    watcher.db = temp_db
    watcher.detector = CompanyDetector(temp_db)
    
    base = [CompanyRecord(id="1", company="A")]
    watcher.detector.process_fetched_companies(base)
    initial_ids = watcher.db.get_all_company_ids()
    
    failures = [
        Exception("API request failed with status 500"),
        PlaywrightTimeoutError("Timeout 30000ms exceeded"),
        Exception("Invalid JSON from API"),
        Exception("Unexpected API schema")
    ]
    
    for exc in failures:
        with patch.object(TPOClient, "fetch_companies", side_effect=exc):
            with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
                await watcher.check_once()
        assert watcher.db.get_all_company_ids() == initial_ids
        assert len(watcher.db.get_pending_notifications()) == 0

@pytest.mark.asyncio
async def test_authentication_recovery_flow(temp_db):
    watcher = PlacementWatcher()
    watcher.db = temp_db
    watcher.detector = CompanyDetector(temp_db)
    
    expected = [CompanyRecord(id="1", company="A")]
    call_attempts = []
    
    async def mock_fetch(self):
        call_attempts.append(len(call_attempts) + 1)
        if len(call_attempts) == 1:
            raise AuthenticationError("Session expired (HTTP 401)")
        return expected
        
    async def mock_start(self):
        self.context = MagicMock()
        self.page = MagicMock()
        self.page.is_closed.return_value = False
        self.page.url = "https://tpo.vierp.in/home"
        
    with patch.object(TPOClient, "fetch_companies", mock_fetch):
        with patch.object(AuthManager, "start", mock_start):
            with patch.object(AuthManager, "is_authenticated", return_value=True):
                await watcher.check_once()
                
    assert len(call_attempts) == 2
    assert watcher.db.get_all_company_ids() == ["1"]
