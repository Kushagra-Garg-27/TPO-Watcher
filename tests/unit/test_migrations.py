import sqlite3
import pytest
from unittest.mock import MagicMock

from app.database.migrations import run_migrations
from app.database.repository import DatabaseRepository


def test_migration_adds_column_on_unmigrated_schema(tmp_path):
    """Verify that run_migrations adds raw_data_snapshot on a fresh schema."""
    db_path = str(tmp_path / "unmigrated.sqlite")
    repo = DatabaseRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA table_info(notification_deliveries);")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "raw_data_snapshot" in columns
    assert columns["raw_data_snapshot"] == "TEXT"


def test_migration_is_idempotent_on_already_migrated_schema(tmp_path):
    """Verify that run_migrations can be executed repeatedly without error or data loss."""
    db_path = str(tmp_path / "idempotent.sqlite")
    repo = DatabaseRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO users (id, email, graduation_year, branch_canonical, is_verified, is_active, created_at)
            VALUES (1, 'test@vit.edu', 2028, 'VIT_CE', 1, 1, '2026-10-23T00:00:00Z')
        """)
        conn.execute("""
            INSERT INTO companies (id, company, is_active, first_seen_at, last_seen_at)
            VALUES ('100', 'Test Corp', 'True', '2026-10-23T00:00:00Z', '2026-10-23T00:00:00Z')
        """)
        conn.execute("""
            INSERT INTO notification_deliveries (id, user_id, company_id, notification_type, status, created_at, raw_data_snapshot)
            VALUES (1, 1, '100', 'NEW', 'SENT', '2026-10-23T00:00:00Z', '{"test": 123}')
        """)
        conn.commit()

    # Second migration run
    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT raw_data_snapshot FROM notification_deliveries WHERE id=1").fetchone()
        assert row[0] == '{"test": 123}'


def test_migration_preserves_existing_legacy_records(tmp_path):
    """Verify that existing delivery rows without raw_data_snapshot maintain NULL and remain queryable."""
    db_path = str(tmp_path / "legacy.sqlite")

    # Manually create pre-migration schema without raw_data_snapshot
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE companies (
                id TEXT PRIMARY KEY,
                company TEXT,
                is_active TEXT,
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                graduation_year INTEGER NOT NULL CHECK (graduation_year = 2028),
                branch_canonical TEXT NOT NULL,
                is_verified INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL,
                verified_at TIMESTAMP,
                updated_at TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company_id TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                lease_expires_at TIMESTAMP,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                last_attempt_at TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP NOT NULL,
                sent_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
                CONSTRAINT uq_delivery_user_company_type UNIQUE (user_id, company_id, notification_type)
            );
        """)
        conn.execute("""
            INSERT INTO users (id, email, graduation_year, branch_canonical, created_at)
            VALUES (1, 'student@vit.edu', 2028, 'VIT_CE', '2026-10-23T00:00:00Z');
        """)
        conn.execute("""
            INSERT INTO companies (id, company, is_active)
            VALUES ('500', 'Legacy Corp', 'True');
        """)
        conn.execute("""
            INSERT INTO notification_deliveries (id, user_id, company_id, notification_type, status, created_at)
            VALUES (1, 1, '500', 'NEW', 'SENT', '2026-10-23T00:00:00Z');
        """)
        conn.commit()

    # Run migration on legacy schema
    run_migrations(db_path)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA table_info(notification_deliveries);")
        cols = {r[1] for r in cursor.fetchall()}
        assert "raw_data_snapshot" in cols

        row = conn.execute("""
            SELECT id, user_id, company_id, notification_type, status, raw_data_snapshot
            FROM notification_deliveries WHERE id=1
        """).fetchone()

        assert row[0] == 1
        assert row[1] == 1
        assert row[2] == '500'
        assert row[3] == 'NEW'
        assert row[4] == 'SENT'
        assert row[5] is None  # Existing historical rows default to NULL safely


def test_unexpected_database_failure_surfaces_error(tmp_path, monkeypatch):
    """Verify that unexpected database errors are NOT silently swallowed by run_migrations."""
    db_path = str(tmp_path / "failure.sqlite")

    class FaultyConnection:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *args):
            if "ALTER TABLE notification_deliveries" in sql:
                raise sqlite3.OperationalError("disk I/O error: simulated hardware fault")
            return self._real.execute(sql, *args)

        def commit(self):
            return self._real.commit()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._real.__exit__(*args)

    real_connect = sqlite3.connect

    def faulty_connect(*args, **kwargs):
        real_conn = real_connect(*args, **kwargs)
        return FaultyConnection(real_conn)

    monkeypatch.setattr(sqlite3, "connect", faulty_connect)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        run_migrations(db_path)
