import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from app.database.migrations import run_migrations

logger = logging.getLogger(__name__)

class SQLiteBaseRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        run_migrations(self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

class SQLiteUserRepository(SQLiteBaseRepository):
    def get_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        normalized = email.strip().lower()
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (normalized,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_user(
        self, 
        email: str, 
        graduation_year: int, 
        branch_canonical: str,
        pref_internship: bool = True,
        pref_placement: bool = True,
        pref_ppo: bool = True
    ) -> int:
        normalized = email.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO users (email, graduation_year, branch_canonical, is_verified, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 0, 1, ?, ?)
            """, (normalized, graduation_year, branch_canonical, now, now))
            user_id = cursor.lastrowid
            
            conn.execute("""
                INSERT INTO user_preferences (user_id, pref_internship, pref_placement, pref_ppo, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, 1 if pref_internship else 0, 1 if pref_placement else 0, 1 if pref_ppo else 0, now))
            conn.commit()
            return user_id

    def set_verified(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE users 
                SET is_verified = 1, verified_at = ?, updated_at = ?
                WHERE id = ?
            """, (now, now, user_id))
            conn.commit()

    def set_unsubscribed(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE users 
                SET is_active = 0, updated_at = ?
                WHERE id = ?
            """, (now, user_id))
            # Immediately cancel any pending/processing deliveries for this user
            conn.execute("""
                DELETE FROM notification_deliveries 
                WHERE user_id = ? AND status IN ('PENDING', 'PROCESSING')
            """, (user_id,))
            conn.commit()

    def get_active_subscribers_for_branch(
        self, 
        branch_canonical: str, 
        graduation_year: int = 2028
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT u.id, u.email, u.graduation_year, u.branch_canonical,
                       p.pref_internship, p.pref_placement, p.pref_ppo
                FROM users u
                JOIN user_preferences p ON u.id = p.user_id
                WHERE u.is_active = 1 
                  AND u.is_verified = 1 
                  AND u.graduation_year = ?
                  AND u.branch_canonical = ?
            """, (graduation_year, branch_canonical))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_preferences(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_preferences(
        self, 
        user_id: int, 
        pref_internship: bool, 
        pref_placement: bool, 
        pref_ppo: bool
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE user_preferences 
                SET pref_internship = ?, pref_placement = ?, pref_ppo = ?, updated_at = ?
                WHERE user_id = ?
            """, (
                1 if pref_internship else 0,
                1 if pref_placement else 0,
                1 if pref_ppo else 0,
                now,
                user_id
            ))
            conn.commit()

class SQLiteTokenRepository(SQLiteBaseRepository):
    def create_token(
        self, 
        user_id: int, 
        token_hash: str, 
        token_type: str, 
        expires_at: datetime
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        exp_iso = expires_at.isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO action_tokens (user_id, token_hash, token_type, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, token_hash, token_type, exp_iso, now))
            conn.commit()
            return cursor.lastrowid

    def get_valid_token(self, token_hash: str, token_type: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT * FROM action_tokens 
                WHERE token_hash = ? 
                  AND token_type = ? 
                  AND used_at IS NULL 
                  AND expires_at > ?
            """, (token_hash, token_type, now))
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_token_used(self, token_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE id = ? AND used_at IS NULL
            """, (now, token_id))
            conn.commit()
            return cursor.rowcount == 1

    def claim_action_token(
        self, 
        token_hash: str, 
        token_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically claims a single-use action token if valid, unexpired, and unused.
        Returns the token record dictionary if successfully claimed, or None if already used, expired, or invalid.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE token_hash = ? 
                  AND token_type = ? 
                  AND used_at IS NULL 
                  AND expires_at > ?
                RETURNING id, user_id, token_hash, token_type, expires_at, created_at, used_at
            """, (now, token_hash, token_type, now))
            row = cursor.fetchone()
            conn.commit()
            return dict(row) if row else None

    def claim_and_verify(self, token_hash: str) -> Optional[int]:
        """
        Atomically claims a valid SIGNUP_VERIFY token and marks the corresponding user verified
        within a single SQLite transaction.
        Returns user_id on success, or None if the token was already consumed, expired, or invalid.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE token_hash = ? 
                  AND token_type = 'SIGNUP_VERIFY' 
                  AND used_at IS NULL 
                  AND expires_at > ?
                RETURNING id, user_id
            """, (now, token_hash, now))
            row = cursor.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            conn.execute("""
                UPDATE users 
                SET is_verified = 1, verified_at = ?, updated_at = ?
                WHERE id = ?
            """, (now, now, user_id))
            conn.commit()
            return user_id

    def claim_and_unsubscribe(self, token_hash: str) -> Optional[int]:
        """
        Atomically claims a valid UNSUBSCRIBE token, deactivates the user, and cancels
        pending/processing deliveries within a single SQLite transaction.
        Returns user_id on success, or None if the token was already consumed, expired, or invalid.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE token_hash = ? 
                  AND token_type = 'UNSUBSCRIBE' 
                  AND used_at IS NULL 
                  AND expires_at > ?
                RETURNING id, user_id
            """, (now, token_hash, now))
            row = cursor.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            conn.execute("""
                UPDATE users 
                SET is_active = 0, updated_at = ?
                WHERE id = ?
            """, (now, user_id))
            conn.execute("""
                DELETE FROM notification_deliveries 
                WHERE user_id = ? AND status IN ('PENDING', 'PROCESSING')
            """, (user_id,))
            conn.commit()
            return user_id

    def invalidate_user_tokens(self, user_id: int, token_type: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE user_id = ? AND token_type = ? AND used_at IS NULL
            """, (now, user_id, token_type))
            conn.commit()

class SQLiteDeliveryRepository(SQLiteBaseRepository):
    def enqueue_deliveries(self, deliveries: List[Dict[str, Any]]) -> int:
        if not deliveries:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with self._get_conn() as conn:
            for d in deliveries:
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO notification_deliveries (
                        user_id, company_id, notification_type, status, created_at
                    ) VALUES (?, ?, ?, 'PENDING', ?)
                """, (d["user_id"], str(d["company_id"]), d["notification_type"], now))
                if cursor.rowcount > 0:
                    inserted += 1
            conn.commit()
        return inserted

    def claim_pending_batch(
        self, 
        batch_size: int = 25, 
        lease_seconds: int = 300
    ) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lease_exp = (now + timedelta(seconds=lease_seconds)).isoformat()

        with self._get_conn() as conn:
            # Select candidate deliveries
            cursor = conn.execute("""
                SELECT id FROM notification_deliveries
                WHERE (status = 'PENDING' AND attempt_count < max_attempts)
                   OR (status = 'PROCESSING' AND lease_expires_at < ?)
                ORDER BY created_at ASC
                LIMIT ?
            """, (now_iso, batch_size))
            candidate_ids = [row["id"] for row in cursor.fetchall()]

            if not candidate_ids:
                return []

            # Atomically update to PROCESSING
            placeholders = ",".join("?" for _ in candidate_ids)
            update_sql = f"""
                UPDATE notification_deliveries
                SET status = 'PROCESSING',
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?
                WHERE id IN ({placeholders})
            """
            conn.execute(update_sql, [lease_exp, now_iso] + candidate_ids)
            conn.commit()

            # Retrieve full records with user and company data
            select_sql = f"""
                SELECT d.*, u.email as user_email, u.branch_canonical as user_branch,
                       c.company as company_name, c.raw_data_json as company_raw_json
                FROM notification_deliveries d
                JOIN users u ON d.user_id = u.id
                JOIN companies c ON d.company_id = c.id
                WHERE d.id IN ({placeholders})
            """
            cursor = conn.execute(select_sql, candidate_ids)
            return [dict(r) for r in cursor.fetchall()]

    def mark_sent(self, delivery_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE notification_deliveries 
                SET status = 'SENT', sent_at = ?, lease_expires_at = NULL, error_message = NULL
                WHERE id = ?
            """, (now, delivery_id))
            conn.commit()

    def release_failed(
        self, 
        delivery_id: int, 
        error_message: str, 
        terminal: bool = False
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            if terminal:
                new_status = 'FAILED'
            else:
                # Check attempt count
                cursor = conn.execute("SELECT attempt_count, max_attempts FROM notification_deliveries WHERE id = ?", (delivery_id,))
                row = cursor.fetchone()
                if row and row["attempt_count"] >= row["max_attempts"]:
                    new_status = 'FAILED'
                else:
                    new_status = 'PENDING'

            conn.execute("""
                UPDATE notification_deliveries 
                SET status = ?, error_message = ?, lease_expires_at = NULL, last_attempt_at = ?
                WHERE id = ?
            """, (new_status, error_message, now, delivery_id))
            conn.commit()

    def recover_stale_leases(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE notification_deliveries
                SET status = 'PENDING', lease_expires_at = NULL
                WHERE status = 'PROCESSING' AND lease_expires_at < ?
            """, (now,))
            conn.commit()
            return cursor.rowcount

    def cancel_pending_deliveries_for_user(self, user_id: int) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute("""
                DELETE FROM notification_deliveries 
                WHERE user_id = ? AND status IN ('PENDING', 'PROCESSING')
            """, (user_id,))
            conn.commit()
            return cursor.rowcount

    def get_delivery_stats(self) -> Dict[str, int]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT status, count(*) as cnt FROM notification_deliveries GROUP BY status")
            stats = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            for s in ["PENDING", "PROCESSING", "SENT", "FAILED"]:
                stats.setdefault(s, 0)
            return stats
