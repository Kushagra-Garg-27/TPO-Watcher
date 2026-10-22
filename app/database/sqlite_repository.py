import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from app.database.migrations import run_migrations

logger = logging.getLogger(__name__)

class _LocalV2Unit:
    """Close a normal guarded connection after a bound local repository unit."""
    def __init__(self,connection):
        self.connection=connection
    def __enter__(self):
        try:
            from app.database.notification_migrator import atomic
            self.unit=atomic(self.connection)
            self.unit.__enter__()
        except BaseException:
            self.connection.close()
            raise
        return self.connection
    def __exit__(self, *args):
        try:
            return self.unit.__exit__(*args)
        finally:
            self.connection.close()

class _BorrowedUnit:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):return self.connection
    def __exit__(self,*args):return False

class SQLiteBaseRepository:
    @classmethod
    def open_existing(cls, db_path, *, connection=None):
        """Open an explicitly migrated account store without any schema writes."""
        from pathlib import Path
        from app.database.account_schema import verify_account_schema
        path = Path(db_path)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("Existing migrated account database required")
        repository = cls.__new__(cls)
        repository.db_path = str(path)
        repository._notification_v2 = None
        repository._unit_connection = None
        if connection is not None:
            from app.database.notification_migrator import active_production_authority
            authority = active_production_authority(connection)
            if authority is None or authority.path != path.resolve():
                raise ValueError("Exact admitted production connection required")
            verify_account_schema(connection)
        else:
            with repository._get_conn() as existing_connection:
                verify_account_schema(existing_connection)
        return repository

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._notification_v2 = None
        self._unit_connection = None
        run_migrations(self.db_path)

    def bind_notification_v2(self, pipeline):
        from app.monitoring.notification_pipeline import NotificationPipeline
        from pathlib import Path
        if not isinstance(pipeline, NotificationPipeline) or Path(self.db_path).resolve()!=pipeline.database_path:
            raise ValueError("explicit same-database disposable v2 binding required")
        self._notification_v2 = pipeline
        return self

    def _after_notification_mutation(self, conn, user_id, *, created=False):
        binding=self._consent_binding(conn)
        if binding is None:
            return
        repo = binding.consent_repository(conn)
        repo.sync_consent(user_id,created=created)

    def _consent_binding(self,conn):
        from app.database.notification_compatibility import consent_binding
        binding=consent_binding(conn)
        if self._notification_v2 is not None and binding is None:
            raise RuntimeError("bound notification consent runtime missing")
        return (self._notification_v2 or binding) if binding else None

    def _commit(self,conn):
        # Every _get_conn context owns its commit, including sqlite3's legacy
        # context manager. Borrowers never commit or revalidate a unit mid-write.
        return None

    def _cancel_legacy(self,conn,user_id):
        # D10 legacy disposition is unresolved. Preserve old rows in a v2
        # runtime; current consent and v2 terminal suppression gate new sends.
        if self._consent_binding(conn) is None:
            return conn.execute("DELETE FROM notification_deliveries WHERE user_id=? AND status IN ('PENDING','PROCESSING')",(user_id,)).rowcount
        return 0

    def _get_conn(self) -> sqlite3.Connection:
        if self._unit_connection is not None:return _BorrowedUnit(self._unit_connection)
        from app.monitoring.production_runtime import current_production_runtime
        runtime = current_production_runtime(self.db_path)
        if runtime is not None:
            return runtime.repository_connection()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            compatible=self._consent_binding(conn) is not None
            conn.execute("PRAGMA busy_timeout = 100;" if compatible else "PRAGMA busy_timeout = 5000;")
            return _LocalV2Unit(conn) if compatible else conn
        except BaseException:
            conn.close()
            raise

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
            self._after_notification_mutation(conn, user_id, created=True)
            self._commit(conn)
            return user_id

    def set_verified(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE users 
                SET is_verified = 1, verified_at = ?, updated_at = ?
                WHERE id = ?
            """, (now, now, user_id))
            self._after_notification_mutation(conn, user_id)
            self._commit(conn)

    def set_unsubscribed(self, user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE users 
                SET is_active = 0, updated_at = ?
                WHERE id = ?
            """, (now, user_id))
            # Immediately cancel any pending/processing deliveries for this user
            self._cancel_legacy(conn,user_id)
            self._after_notification_mutation(conn, user_id)
            self._commit(conn)

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
            self._after_notification_mutation(conn, user_id)
            self._commit(conn)

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
            self._after_notification_mutation(conn,user_id)
            self._commit(conn)
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
            self._commit(conn)
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
            if self._consent_binding(conn) is not None:
                target=conn.execute("SELECT u.* FROM action_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.token_type=? AND t.used_at IS NULL AND t.expires_at>?",(token_hash,token_type,now)).fetchone()
                if target is None:return None
                if token_type=="MANAGE_PREFS" and (not target["is_active"] or not target["is_verified"]):return None
                self._after_notification_mutation(conn,target["id"])
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
            self._commit(conn)
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
            self._after_notification_mutation(conn, user_id)
            self._commit(conn)
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
            self._cancel_legacy(conn,user_id)
            self._after_notification_mutation(conn, user_id)
            self._commit(conn)
            return user_id

    def invalidate_user_tokens(self, user_id: int, token_type: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            self._after_notification_mutation(conn,user_id)
            conn.execute("""
                UPDATE action_tokens 
                SET used_at = ? 
                WHERE user_id = ? AND token_type = ? AND used_at IS NULL
            """, (now, user_id, token_type))
            self._commit(conn)

    def prune_stale_tokens(self, retention_days: int = 30) -> int:
        """
        Prunes action tokens exceeding the retention period:
        - Used tokens whose used_at <= cutoff
        - Expired unused tokens whose expires_at <= cutoff
        Uses a single short transaction and bound parameters.
        Logs only aggregate count; never logs token hashes or identities.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat(timespec="microseconds")
        with self._get_conn() as conn:
            cursor = conn.execute("""
                DELETE FROM action_tokens 
                WHERE (used_at IS NOT NULL AND used_at <= ?)
                   OR (used_at IS NULL AND expires_at <= ?)
            """, (cutoff, cutoff))
            deleted_count = cursor.rowcount
            from app.database.account_schema import account_policy_enabled
            if account_policy_enabled(conn):
                from app.auth.accounts import AccountService
                deleted_count += AccountService(conn).prune_expired_credentials(retention_days=min(retention_days, 30))
            self._commit(conn)
            logger.info(
                "Pruned %d stale action tokens older than retention cutoff (retention_days=%d).",
                deleted_count,
                retention_days
            )
            return deleted_count

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
            self._commit(conn)
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
            if self._consent_binding(conn) is not None:
                # D10 disposition is unapproved; retained legacy obligations
                # cannot re-enter automatic delivery, even after opt-in.
                return []
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
            self._commit(conn)

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
            self._commit(conn)

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
            self._commit(conn)

    def recover_stale_leases(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE notification_deliveries
                SET status = 'PENDING', lease_expires_at = NULL
                WHERE status = 'PROCESSING' AND lease_expires_at < ?
            """, (now,))
            self._commit(conn)
            return cursor.rowcount

    def cancel_pending_deliveries_for_user(self, user_id: int) -> int:
        with self._get_conn() as conn:
            if self._consent_binding(conn) is not None:
                from app.database.event_repository import PolicyDenied
                raise PolicyDenied("legacy delivery disposition held pending D10")
            cursor = conn.execute("""
                DELETE FROM notification_deliveries 
                WHERE user_id = ? AND status IN ('PENDING', 'PROCESSING')
            """, (user_id,))
            self._commit(conn)
            return cursor.rowcount

    def get_delivery_stats(self) -> Dict[str, int]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT status, count(*) as cnt FROM notification_deliveries GROUP BY status")
            stats = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            for s in ["PENDING", "PROCESSING", "SENT", "FAILED"]:
                stats.setdefault(s, 0)
            return stats
