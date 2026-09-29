import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from app.database.migrations import run_migrations
from app.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteTokenRepository,
    SQLiteDeliveryRepository
)

@pytest.fixture
def temp_v1_db(tmp_path):
    db_path = str(tmp_path / "test_v1.sqlite")
    # Seed companies table for foreign key reference
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE companies (
                id TEXT PRIMARY KEY,
                company TEXT,
                raw_data_json TEXT
            )
        """)
        conn.execute("INSERT INTO companies (id, company) VALUES ('101', 'Test Company')")
        conn.commit()
    run_migrations(db_path)
    return db_path

def test_user_creation_and_preferences(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    user_id = user_repo.create_user(
        email="student@vit.edu",
        graduation_year=2028,
        branch_canonical="VIT_CE",
        pref_internship=True,
        pref_placement=False,
        pref_ppo=True
    )
    assert user_id > 0

    user = user_repo.get_by_id(user_id)
    assert user is not None
    assert user["email"] == "student@vit.edu"
    assert user["graduation_year"] == 2028
    assert user["branch_canonical"] == "VIT_CE"
    assert user["is_verified"] == 0
    assert user["is_active"] == 1

    prefs = user_repo.get_preferences(user_id)
    assert prefs["pref_internship"] == 1
    assert prefs["pref_placement"] == 0
    assert prefs["pref_ppo"] == 1

def test_graduation_year_constraint(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    with pytest.raises(sqlite3.IntegrityError):
        user_repo.create_user(
            email="invalid_year@vit.edu",
            graduation_year=2027, # Rejection expected
            branch_canonical="VIT_CE"
        )

def test_verification_and_unsubscribe_lifecycle(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    user_id = user_repo.create_user("verify_test@vit.edu", 2028, "VIT_IT")
    assert user_repo.get_by_id(user_id)["is_verified"] == 0

    user_repo.set_verified(user_id)
    assert user_repo.get_by_id(user_id)["is_verified"] == 1

    # Active subscribers
    subs = user_repo.get_active_subscribers_for_branch("VIT_IT", 2028)
    assert len(subs) == 1
    assert subs[0]["email"] == "verify_test@vit.edu"

    # Unsubscribe
    user_repo.set_unsubscribed(user_id)
    assert user_repo.get_by_id(user_id)["is_active"] == 0

    subs_after = user_repo.get_active_subscribers_for_branch("VIT_IT", 2028)
    assert len(subs_after) == 0

def test_token_repository(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    token_repo = SQLiteTokenRepository(temp_v1_db)
    user_id = user_repo.create_user("token_test@vit.edu", 2028, "VIT_CE")

    # Valid token
    exp = datetime.now(timezone.utc) + timedelta(hours=24)
    token_id = token_repo.create_token(user_id, "hash123", "SIGNUP_VERIFY", exp)
    assert token_id > 0

    valid_token = token_repo.get_valid_token("hash123", "SIGNUP_VERIFY")
    assert valid_token is not None
    assert valid_token["user_id"] == user_id

    # Mark used
    token_repo.mark_token_used(token_id)
    assert token_repo.get_valid_token("hash123", "SIGNUP_VERIFY") is None

    # Expired token
    exp_past = datetime.now(timezone.utc) - timedelta(minutes=5)
    token_repo.create_token(user_id, "hash_expired", "MANAGE_PREFS", exp_past)
    assert token_repo.get_valid_token("hash_expired", "MANAGE_PREFS") is None

def test_delivery_idempotency_and_recoverable_lease(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    delivery_repo = SQLiteDeliveryRepository(temp_v1_db)

    user_id = user_repo.create_user("delivery_test@vit.edu", 2028, "VIT_CE")
    user_repo.set_verified(user_id)

    # 1. Enqueue delivery
    deliveries = [{
        "user_id": user_id,
        "company_id": "101",
        "notification_type": "NEW"
    }]
    count1 = delivery_repo.enqueue_deliveries(deliveries)
    assert count1 == 1

    # 2. Idempotency test: duplicate enqueue must be ignored
    count2 = delivery_repo.enqueue_deliveries(deliveries)
    assert count2 == 0

    stats = delivery_repo.get_delivery_stats()
    assert stats["PENDING"] == 1

    # 3. Claim batch with lease
    claimed = delivery_repo.claim_pending_batch(batch_size=10, lease_seconds=2)
    assert len(claimed) == 1
    assert claimed[0]["status"] == "PROCESSING"
    assert claimed[0]["attempt_count"] == 1
    assert claimed[0]["company_name"] == "Test Company"

    # Claiming again immediately returns nothing (active lease)
    assert len(delivery_repo.claim_pending_batch(batch_size=10, lease_seconds=2)) == 0

    # 4. Release failed with non-terminal
    delivery_repo.release_failed(claimed[0]["id"], "SMTP timeout", terminal=False)
    assert delivery_repo.get_delivery_stats()["PENDING"] == 1

    # Claim again
    claimed2 = delivery_repo.claim_pending_batch(batch_size=10, lease_seconds=1)
    assert len(claimed2) == 1
    assert claimed2[0]["attempt_count"] == 2

    # Mark sent
    delivery_repo.mark_sent(claimed2[0]["id"])
    assert delivery_repo.get_delivery_stats()["SENT"] == 1
    assert delivery_repo.get_delivery_stats()["PENDING"] == 0

def test_stale_lease_recovery(temp_v1_db):
    user_repo = SQLiteUserRepository(temp_v1_db)
    delivery_repo = SQLiteDeliveryRepository(temp_v1_db)

    user_id = user_repo.create_user("lease_test@vit.edu", 2028, "VIT_CE")
    delivery_repo.enqueue_deliveries([{
        "user_id": user_id,
        "company_id": "101",
        "notification_type": "NEW"
    }])

    # Claim with 0-second lease (already expired)
    claimed = delivery_repo.claim_pending_batch(batch_size=10, lease_seconds=-1)
    assert len(claimed) == 1

    # Recover stale lease
    recovered = delivery_repo.recover_stale_leases()
    assert recovered == 1
    assert delivery_repo.get_delivery_stats()["PENDING"] == 1
