#!/usr/bin/env python3
"""
TPO-Watcher SQLite Restore Validation Tool.
Validates database snapshots by checking:
1. PRAGMA integrity_check == 'ok'
2. Required tables exist
3. Expected columns exist
4. Representative SELECT queries execute successfully
5. Application repository layer can read the restored database
"""

import sys
import sqlite3
import argparse
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("restore-validator")

REQUIRED_TABLES = [
    "companies",
    "system_state",
    "pending_notifications",
    "users",
    "user_preferences",
    "action_tokens",
    "notification_deliveries",
]

REQUIRED_COLUMNS = {
    "users": ["id", "email", "is_verified"],
    "action_tokens": ["id", "user_id", "token_hash", "token_type", "expires_at", "used_at"],
    "companies": ["id", "company"],
    "notification_deliveries": ["id", "user_id", "company_id", "status"],
    "system_state": ["key", "value"],
}

def validate_snapshot(snapshot_path: str) -> bool:
    """
    Validates that a restored or backed-up SQLite database file is uncorrupted,
    conforms to the canonical application schema, and supports repository operations.
    """
    path = Path(snapshot_path).resolve()
    if not path.exists():
        logger.error("Snapshot file not found: %s", path)
        return False

    if path.stat().st_size <= 0:
        logger.error("Snapshot file is empty (0 bytes): %s", path)
        return False

    try:
        # 1. Connect read-only
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            # 2. PRAGMA integrity_check
            cursor = conn.execute("PRAGMA integrity_check;")
            integrity_rows = cursor.fetchall()
            if not integrity_rows or integrity_rows[0][0] != "ok":
                logger.error("PRAGMA integrity_check failed: %s", integrity_rows)
                return False

            # 3. Check required tables
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = {row[0] for row in cursor.fetchall()}
            for table in REQUIRED_TABLES:
                if table not in existing_tables:
                    logger.error("Required table '%s' missing from snapshot.", table)
                    return False

            # 4. Check expected columns
            for table, cols in REQUIRED_COLUMNS.items():
                cursor = conn.execute(f"PRAGMA table_info({table});")
                existing_cols = {row[1] for row in cursor.fetchall()}
                for col in cols:
                    if col not in existing_cols:
                        logger.error("Required column '%s' missing from table '%s'.", col, table)
                        return False

            # 5. Representative SELECTs
            conn.execute("SELECT count(*) FROM users;").fetchone()
            conn.execute("SELECT count(*) FROM companies;").fetchone()
            conn.execute("SELECT count(*) FROM system_state;").fetchone()
            conn.execute("SELECT count(*) FROM action_tokens;").fetchone()
            conn.execute("SELECT count(*) FROM notification_deliveries;").fetchone()

        finally:
            conn.close()

        # 6. Verify application DatabaseRepository can open and query it
        from app.database.repository import DatabaseRepository
        repo = DatabaseRepository(str(path))
        _ = repo.is_baseline_initialized()
        _ = repo.get_all_company_ids()

        logger.info("Snapshot restore validation passed successfully for: %s", path)
        return True

    except Exception as e:
        logger.error("Snapshot validation encountered an error: %s", e)
        return False

def main():
    parser = argparse.ArgumentParser(description="TPO-Watcher Restore Validation Tool")
    parser.add_argument("snapshot_path", help="Path to SQLite snapshot file to validate")

    args = parser.parse_args()
    success = validate_snapshot(args.snapshot_path)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
