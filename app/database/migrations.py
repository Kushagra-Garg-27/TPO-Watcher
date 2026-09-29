import sqlite3
import logging
import os

logger = logging.getLogger(__name__)

def run_migrations(db_path: str) -> None:
    """
    Applies Public V1 schema migrations safely to the SQLite database.
    Configures WAL mode, busy timeout, and foreign keys.
    Preserves all existing tables (companies, system_state, pending_notifications)
    and their historical data intact.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # 1. Users (subscribers restricted to 2028)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
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
            CREATE INDEX IF NOT EXISTS idx_users_lookup 
            ON users(is_active, is_verified, graduation_year, branch_canonical);
        """)

        # 2. User Preferences (V1: Internship, Placement, PPO)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                pref_internship INTEGER NOT NULL DEFAULT 1,
                pref_placement INTEGER NOT NULL DEFAULT 1,
                pref_ppo INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 3. Action Tokens (Signup verification, unsubscribe, manage prefs)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS action_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                token_type TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_action_tokens_lookup 
            ON action_tokens(token_hash, token_type, expires_at);
        """)

        # 4. Notification Deliveries (Recoverable lease queue with idempotency constraint)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_deliveries (
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
            CREATE INDEX IF NOT EXISTS idx_deliveries_claim 
            ON notification_deliveries(status, lease_expires_at);
        """)

        conn.commit()
    logger.info("Database migrations applied successfully.")
