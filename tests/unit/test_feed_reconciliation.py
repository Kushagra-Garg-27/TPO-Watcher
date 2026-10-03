import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from app.tpo.models import CompanyRecord
from app.monitoring.detector import CompanyDetector
from app.monitoring.watcher import PlacementWatcher
from app.database.repository import DatabaseRepository
from app.tpo.client import TPOClient, AuthenticationError

@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test_reconciliation.sqlite")
    return DatabaseRepository(db_path)

@pytest.fixture
def detector(repo):
    return CompanyDetector(repo)

def _create_record(cid: str, company: str, is_active: str = "True") -> CompanyRecord:
    return CompanyRecord(
        id=cid,
        company=company,
        isactive=is_active,
        placementtype="Placement",
        tpoprogram="VIT-BTech-Computer Engineering",
        organization="VIT"
    )

def test_reconciliation_test_a_deactivate_missing_active_company(repo, detector):
    """
    TEST A:
    Existing active records: A (id: 1), B (id: 2), C (id: 3)
    Incoming authoritative feed: A (id: 1), B (id: 2)
    Expected:
        A = True
        B = True
        C = False
    """
    comp_a = _create_record("1", "Company A", is_active="True")
    comp_b = _create_record("2", "Company B", is_active="True")
    comp_c = _create_record("3", "Company C", is_active="True")

    # Seed baseline with A, B, C
    detector.process_fetched_companies([comp_a, comp_b, comp_c])
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"
    assert repo.get_company("3")["is_active"] == "True"

    # Next cycle: only A and B in feed (C has disappeared)
    result = detector.process_fetched_companies([comp_a, comp_b])

    assert "3" in result.deactivated_companies
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"
    assert repo.get_company("3")["is_active"] == "False"

def test_reconciliation_test_b_insert_new_record_alongside_existing(repo, detector):
    """
    TEST B:
    Existing active records: A (id: 1), B (id: 2)
    Incoming feed contains a new record: A (id: 1), B (id: 2), D (id: 4)
    Expected:
        D is inserted/upserted as True.
        A = True, B = True.
    """
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    detector.process_fetched_companies([comp_a, comp_b])

    comp_d = _create_record("4", "Company D")
    result = detector.process_fetched_companies([comp_a, comp_b, comp_d])

    assert len(result.new_companies) == 1
    assert result.new_companies[0].id == "4"
    assert repo.get_company("4")["is_active"] == "True"
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"
    assert len(result.deactivated_companies) == 0

def test_reconciliation_test_c_identical_feed_no_state_change(repo, detector):
    """
    TEST C:
    Incoming feed is identical to database: A (id: 1), B (id: 2), C (id: 3)
    Expected:
        No unnecessary state changes.
        0 deactivated companies.
    """
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    comp_c = _create_record("3", "Company C")
    detector.process_fetched_companies([comp_a, comp_b, comp_c])

    # Re-run identical feed
    result = detector.process_fetched_companies([comp_a, comp_b, comp_c])

    assert len(result.deactivated_companies) == 0
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"
    assert repo.get_company("3")["is_active"] == "True"

@pytest.mark.asyncio
async def test_reconciliation_test_d_fetch_failure_preserves_active_state(repo):
    """
    TEST D:
    Fetch fails.
    Expected:
        NO active records are deactivated.
    """
    detector = CompanyDetector(repo)
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    detector.process_fetched_companies([comp_a, comp_b])

    watcher = PlacementWatcher(db=repo)
    with patch.object(TPOClient, "fetch_companies", side_effect=AuthenticationError("Session expired (HTTP 401)")):
        with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
            with patch.object(watcher.auth, "is_authenticated", return_value=True):
                with patch.object(watcher.auth, "login", return_value=None):
                    await watcher.check_once()

    # Active companies remain untouched
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"

@pytest.mark.asyncio
async def test_reconciliation_test_e_malformed_response_preserves_active_state(repo):
    """
    TEST E:
    Malformed / invalid response.
    Expected:
        NO active records are deactivated.
    """
    detector = CompanyDetector(repo)
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    detector.process_fetched_companies([comp_a, comp_b])

    watcher = PlacementWatcher(db=repo)
    with patch.object(TPOClient, "fetch_companies", side_effect=Exception("Invalid JSON from API")):
        with patch.object(watcher.auth, "get_valid_page", return_value=MagicMock()):
            with patch.object(watcher.auth, "is_authenticated", return_value=True):
                with patch.object(watcher.auth, "login", return_value=None):
                    await watcher.check_once()

    # Active companies remain untouched
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"

def test_reconciliation_test_f_empty_feed_safety_preserves_active_state(repo, detector):
    """
    TEST F:
    Empty feed.
    Expected:
        Safety guardrail prevents deactivation: preserve state.
        NO active records are deactivated.
    """
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    detector.process_fetched_companies([comp_a, comp_b])

    # Next cycle receives an empty feed (e.g. portal glitch / unauthenticated blank)
    result = detector.process_fetched_companies([])

    assert len(result.deactivated_companies) == 0
    assert repo.get_company("1")["is_active"] == "True"
    assert repo.get_company("2")["is_active"] == "True"

def test_reconciliation_test_g_inactive_historical_records_remain_inactive_and_not_deleted(repo, detector):
    """
    TEST G:
    Existing inactive historical records remain inactive and are not deleted.
    """
    comp_a = _create_record("1", "Company A")
    comp_b = _create_record("2", "Company B")
    comp_hist = _create_record("99", "Historical Corp", is_active="False")

    detector.process_fetched_companies([comp_a, comp_b, comp_hist])
    assert repo.get_company("99")["is_active"] == "False"
    first_seen = repo.get_company("99")["first_seen_at"]
    last_seen = repo.get_company("99")["last_seen_at"]

    # Next cycle contains only A and B
    result = detector.process_fetched_companies([comp_a, comp_b])

    # Record 99 was already False, so it is not reported as newly deactivated
    assert "99" not in result.deactivated_companies
    # Record 99 still exists in database (not deleted)
    record_99 = repo.get_company("99")
    assert record_99 is not None
    assert record_99["is_active"] == "False"
    assert record_99["first_seen_at"] == first_seen
    assert record_99["last_seen_at"] == last_seen

def test_reconciliation_test_h_null_records_not_activated(repo, detector):
    """
    TEST H:
    Existing NULL/test records are not accidentally activated by reconciliation.
    """
    comp_a = _create_record("1", "Company A")
    # Manually insert a record with NULL is_active (simulating historical/unverified record like Mastercard)
    with repo._get_conn() as conn:
        conn.execute("""
            INSERT INTO companies (id, company, is_active, first_seen_at, last_seen_at)
            VALUES ('5610', 'Mastercard', NULL, '2026-09-09', '2026-09-09')
        """)
        conn.commit()

    assert repo.get_company("5610")["is_active"] is None

    # Run cycle with comp_a
    result = detector.process_fetched_companies([comp_a])

    record = repo.get_company("5610")
    assert record is not None
    assert record["is_active"] is None
    assert "5610" not in result.deactivated_companies
    assert repo.get_company("1")["is_active"] == "True"
