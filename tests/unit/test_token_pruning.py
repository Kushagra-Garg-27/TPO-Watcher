import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from app.database.repository import DatabaseRepository
from app.monitoring.watcher import PlacementWatcher

@pytest.fixture
def repo_bundle(tmp_path):
    db_path = str(tmp_path / "test_prune.sqlite")
    db_repo = DatabaseRepository(db_path)
    db_repo.set_baseline_initialized()
    user_id = db_repo.users.create_user("student_prune@vit.edu", 2028, "VIT_CSE")
    return db_repo.tokens, user_id, db_path

def _insert_token(db_path, user_id, token_hash, token_type, expires_at_iso, created_at_iso, used_at_iso=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO action_tokens (user_id, token_hash, token_type, expires_at, created_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, token_hash, token_type, expires_at_iso, created_at_iso, used_at_iso))
        conn.commit()

def test_prune_old_used_removed(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    old_used_at = (now - timedelta(days=35)).isoformat(timespec="microseconds")
    old_created_at = (now - timedelta(days=40)).isoformat(timespec="microseconds")
    old_expires_at = (now + timedelta(days=5)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_old_used", "SIGNUP_VERIFY", old_expires_at, old_created_at, old_used_at)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_old_used'").fetchone()
        assert row is None

def test_prune_old_expired_unused_removed(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    old_created_at = (now - timedelta(days=40)).isoformat(timespec="microseconds")
    old_expires_at = (now - timedelta(days=35)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_old_expired", "MANAGE_PREFS", old_expires_at, old_created_at, None)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 1

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_old_expired'").fetchone()
        assert row is None

def test_prune_recent_used_retained(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    recent_used_at = (now - timedelta(days=10)).isoformat(timespec="microseconds")
    recent_created_at = (now - timedelta(days=12)).isoformat(timespec="microseconds")
    recent_expires_at = (now + timedelta(days=2)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_recent_used", "SIGNUP_VERIFY", recent_expires_at, recent_created_at, recent_used_at)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 0

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_recent_used'").fetchone()
        assert row is not None

def test_prune_recent_expired_unused_retained(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(days=10)).isoformat(timespec="microseconds")
    expired_at = (now - timedelta(days=5)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_recent_expired", "MANAGE_PREFS", expired_at, created_at, None)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 0

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_recent_expired'").fetchone()
        assert row is not None

def test_prune_valid_unused_retained(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(hours=2)).isoformat(timespec="microseconds")
    expires_at = (now + timedelta(hours=22)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_valid_unused", "SIGNUP_VERIFY", expires_at, created_at, None)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 0

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_valid_unused'").fetchone()
        assert row is not None

def test_prune_future_expiry_retained(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    created_at = now.isoformat(timespec="microseconds")
    expires_at = (now + timedelta(days=15)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_future_expiry", "UNSUBSCRIBE", expires_at, created_at, None)

    deleted = token_repo.prune_stale_tokens(retention_days=30)
    assert deleted == 0

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT * FROM action_tokens WHERE token_hash = 'hash_future_expiry'").fetchone()
        assert row is not None

def test_prune_idempotent(repo_bundle):
    token_repo, user_id, db_path = repo_bundle
    now = datetime.now(timezone.utc)
    old_time = (now - timedelta(days=35)).isoformat(timespec="microseconds")

    _insert_token(db_path, user_id, "hash_idem_1", "SIGNUP_VERIFY", old_time, old_time, old_time)
    _insert_token(db_path, user_id, "hash_idem_2", "MANAGE_PREFS", old_time, old_time, None)

    first_run = token_repo.prune_stale_tokens(retention_days=30)
    assert first_run == 2

    second_run = token_repo.prune_stale_tokens(retention_days=30)
    assert second_run == 0

def test_normal_token_claim_behavior_unchanged(repo_bundle):
    token_repo, user_id, _ = repo_bundle
    now = datetime.now(timezone.utc)
    token_repo.create_token(
        user_id=user_id,
        token_hash="hash_normal_claim",
        token_type="SIGNUP_VERIFY",
        expires_at=now + timedelta(hours=24)
    )

    claimed = token_repo.claim_action_token("hash_normal_claim", "SIGNUP_VERIFY")
    assert claimed is not None
    assert claimed["token_hash"] == "hash_normal_claim"

    # Second claim must fail
    second = token_repo.claim_action_token("hash_normal_claim", "SIGNUP_VERIFY")
    assert second is None

def test_normal_verify_behavior_unchanged(repo_bundle):
    token_repo, user_id, _ = repo_bundle
    now = datetime.now(timezone.utc)
    token_repo.create_token(
        user_id=user_id,
        token_hash="hash_normal_verify",
        token_type="SIGNUP_VERIFY",
        expires_at=now + timedelta(hours=24)
    )

    verified_user_id = token_repo.claim_and_verify("hash_normal_verify")
    assert verified_user_id == user_id

def test_pruning_job_registered_for_0100_ist(tmp_path):
    db_path = str(tmp_path / "test_sched_reg.sqlite")
    watcher = PlacementWatcher(db_path=db_path)
    assert watcher.prune_scheduler.timezone_name == "Asia/Kolkata"
    registered_times = [t.strftime("%H:%M") for t in watcher.prune_scheduler.check_times]
    assert registered_times == ["01:00"]
    # Separate from watcher checks
    watcher_times = [t.strftime("%H:%M") for t in watcher.scheduler.check_times]
    assert "01:00" not in watcher_times
