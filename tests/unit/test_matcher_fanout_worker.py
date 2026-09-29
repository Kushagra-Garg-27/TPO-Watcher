import pytest
import sqlite3
from unittest.mock import MagicMock
from app.tpo.models import CompanyRecord
from app.database.migrations import run_migrations
from app.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteTokenRepository,
    SQLiteDeliveryRepository
)
from app.subscribers.canonical import (
    CanonicalBranch,
    extract_eligible_canonical_branches
)
from app.subscribers.matcher import SubscriptionMatcher
from app.subscribers.fanout import FanoutEngine
from app.subscribers.worker import DeliveryWorker

@pytest.fixture
def temp_fanout_db(tmp_path):
    db_path = str(tmp_path / "test_fanout.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE companies (
                id TEXT PRIMARY KEY,
                company TEXT,
                company_code TEXT,
                placement_type TEXT,
                academic_year TEXT,
                raw_data_json TEXT
            )
        """)
        conn.execute("""
            INSERT INTO companies (id, company, company_code, placement_type, academic_year, raw_data_json)
            VALUES ('5610', 'Mastercard', 'MC1', 'Internship + Performance based PPO', '2026-27', '{}')
        """)
        conn.commit()
    run_migrations(db_path)
    return db_path

def test_canonical_branch_strict_matching_no_cross_branch_inference():
    # Real payload from Mastercard: CE & IT only
    tpoprogram = "VIT-BTech-Computer Engineering, VIT-BTech-Information Technology"
    programnew = "BTech-Computer Engineering, BTech-Information Technology"
    org = "VIT"

    branches = extract_eligible_canonical_branches(tpoprogram, programnew, org)
    assert CanonicalBranch.VIT_CE in branches
    assert CanonicalBranch.VIT_IT in branches

    # CRITICAL RULE: Under NO circumstances should CE be mapped to CS or AI
    assert CanonicalBranch.VIT_CSE_AI not in branches
    assert CanonicalBranch.VIT_CSE_AIML not in branches
    assert CanonicalBranch.VIT_CS_AI not in branches
    assert CanonicalBranch.VIT_AIDS not in branches
    assert CanonicalBranch.VIT_ENTC not in branches

def test_canonical_organization_filtering():
    # Only VU or VIIT, no VIT
    tpoprogram = "VU-Bachelor of Technology - Computer Science and Engineering"
    programnew = "Bachelor of Technology - Computer Science and Engineering"
    org = "VU"

    branches = extract_eligible_canonical_branches(tpoprogram, programnew, org)
    # Must be empty because VIT Pune is not an eligible organization
    assert len(branches) == 0

def test_opportunity_type_preferences():
    # 1. Internship + PPO
    co_ppo = CompanyRecord(
        id="1", company="A", placementtype="Internship + Performance based PPO"
    )
    # Matches internship pref
    assert SubscriptionMatcher.matches_opportunity_preferences(co_ppo, pref_internship=True, pref_placement=False, pref_ppo=False) is True
    # Matches PPO pref
    assert SubscriptionMatcher.matches_opportunity_preferences(co_ppo, pref_internship=False, pref_placement=False, pref_ppo=True) is True
    # If student wants placement only -> False
    assert SubscriptionMatcher.matches_opportunity_preferences(co_ppo, pref_internship=False, pref_placement=True, pref_ppo=False) is False

    # 2. Placement only
    co_place = CompanyRecord(
        id="2", company="B", placementtype="Placement"
    )
    assert SubscriptionMatcher.matches_opportunity_preferences(co_place, pref_internship=True, pref_placement=False, pref_ppo=False) is False
    assert SubscriptionMatcher.matches_opportunity_preferences(co_place, pref_internship=False, pref_placement=True, pref_ppo=False) is True

def test_fanout_engine_dispatch(temp_fanout_db):
    user_repo = SQLiteUserRepository(temp_fanout_db)
    delivery_repo = SQLiteDeliveryRepository(temp_fanout_db)

    # User 1: CE, verified, active, wants Internship + PPO
    u1 = user_repo.create_user("ce_student@vit.edu", 2028, "VIT_CE", pref_internship=True, pref_placement=False, pref_ppo=True)
    user_repo.set_verified(u1)

    # User 2: IT, verified, active, wants Placement only
    u2 = user_repo.create_user("it_placement@vit.edu", 2028, "VIT_IT", pref_internship=False, pref_placement=True, pref_ppo=False)
    user_repo.set_verified(u2)

    # User 3: CE, unverified (should not receive)
    u3 = user_repo.create_user("unverified@vit.edu", 2028, "VIT_CE")

    # User 4: ENTC, verified (company is CE & IT only -> should not receive)
    u4 = user_repo.create_user("entc_student@vit.edu", 2028, "VIT_ENTC")
    user_repo.set_verified(u4)

    fanout = FanoutEngine(user_repo, delivery_repo)
    co = CompanyRecord(
        id="5610",
        company="Mastercard",
        placementtype="Internship + Performance based PPO",
        tpoprogram="VIT-BTech-Computer Engineering, VIT-BTech-Information Technology",
        programnew="BTech-Computer Engineering, BTech-Information Technology",
        organization="VIT"
    )

    enqueued = fanout.dispatch_opportunity(co, "NEW")
    # Only u1 matches! (u2 wants placement only, u3 unverified, u4 wrong branch)
    assert enqueued == 1

    stats = delivery_repo.get_delivery_stats()
    assert stats["PENDING"] == 1

    # Re-dispatching same opportunity must be idempotent (0 new deliveries enqueued)
    enqueued_again = fanout.dispatch_opportunity(co, "NEW")
    assert enqueued_again == 0

@pytest.mark.asyncio
async def test_delivery_worker_processing(temp_fanout_db):
    user_repo = SQLiteUserRepository(temp_fanout_db)
    token_repo = SQLiteTokenRepository(temp_fanout_db)
    delivery_repo = SQLiteDeliveryRepository(temp_fanout_db)

    u1 = user_repo.create_user("worker_test@vit.edu", 2028, "VIT_CE")
    user_repo.set_verified(u1)

    delivery_repo.enqueue_deliveries([{
        "user_id": u1,
        "company_id": "5610",
        "notification_type": "NEW"
    }])

    mock_notifier = MagicMock()
    mock_notifier._send_email_to.return_value = True

    worker = DeliveryWorker(
        delivery_repo=delivery_repo,
        token_repo=token_repo,
        email_notifier=mock_notifier,
        batch_size=10,
        lease_seconds=300,
        send_interval_seconds=0.01
    )

    delivered = await worker.process_batch_once()
    assert delivered == 1

    stats = delivery_repo.get_delivery_stats()
    assert stats["SENT"] == 1
    assert stats["PENDING"] == 0
    assert mock_notifier._send_email_to.call_count == 1

    # Verify dedicated tokens were created for the user
    unsub_token = token_repo.get_valid_token(
        mock_notifier._send_email_to.call_args[0][2].split("unsubscribe?token=")[1].split('"')[0],
        "UNSUBSCRIBE"
    )
    # The URL in email has the raw token! Let's verify token hashing works.
    raw_token_in_email = mock_notifier._send_email_to.call_args[0][2].split("unsubscribe?token=")[1].split('"')[0]
    import hashlib
    h = hashlib.sha256(raw_token_in_email.encode("utf-8")).hexdigest()
    assert token_repo.get_valid_token(h, "UNSUBSCRIBE") is not None
