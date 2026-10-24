import sqlite3
import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from app.database.migrations import run_migrations

logger = logging.getLogger(__name__)

class _LocalV2Unit:
    """Close a normal guarded connection after a bound local repository unit."""
    def __init__(self,connection):
        self.connection=connection
    def __enter__(self):
        self.prepared=False
        try:
            from app.database.protected_payloads import require_binding
            codec=require_binding(self.connection)
            self.codec=codec
            if codec:codec.begin_unit(self.connection)
            self.prepared=bool(codec)
            self.connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            if self.prepared and not self.connection.in_transaction:
                self.codec.finish_unit(self.connection)
            self.connection.close()
            raise
        return self.connection
    def __exit__(self, *args):
        try:
            result=self.connection.__exit__(*args)
            if self.codec:self.codec.finish_unit(self.connection)
            return result
        finally:
            self.connection.close()

class _BorrowedUnit:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):return self.connection
    def __exit__(self,*args):return False

class SQLiteBaseRepository:
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
        if conn is not self._unit_connection:conn.commit()

    def _cancel_legacy(self,conn,user_id):
        # D10 legacy disposition is unresolved. Preserve old rows in a v2
        # runtime; current consent and v2 terminal suppression gate new sends.
        if self._consent_binding(conn) is None:
            return conn.execute("DELETE FROM notification_deliveries WHERE user_id=? AND status IN ('PENDING','PROCESSING')",(user_id,)).rowcount
        return 0

    def _get_conn(self) -> sqlite3.Connection:
        if self._unit_connection is not None:return _BorrowedUnit(self._unit_connection)
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
        pref_ppo: bool,
        branch_canonical: "Optional[str]" = None,
    ) -> None:
        from typing import Optional as _Opt  # noqa: F401 — used in signature above
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
            # F07: persist branch correction when explicitly supplied
            if branch_canonical is not None:
                conn.execute(
                    "UPDATE users SET branch_canonical = ?, updated_at = ? WHERE id = ?",
                    (branch_canonical, now, user_id),
                )
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
            self._commit(conn)
            logger.info(
                "Pruned %d stale action tokens older than retention cutoff (retention_days=%d).",
                deleted_count,
                retention_days
            )
            return deleted_count

class _ClaimedDeliveryId(int):
    """Local claim authority. Numeric serialization deliberately drops authority.

    The durable token lives in system_state, not a process-local owner map.
    Its representation stays a numeric ID so logs never expose the token.
    """
    def __new__(cls, value, token):
        instance = int.__new__(cls, value)
        instance._claim_token = token
        return instance


class DeliveryFanoutDeferred(RuntimeError):
    """A distinct UPDATE must retain its durable obligation until prior work ends."""


class SQLiteDeliveryRepository(SQLiteBaseRepository):
    _CLAIM_PREFIX = "notification:v1:claim:"

    @classmethod
    def _claim_key(cls, delivery_id):
        return cls._CLAIM_PREFIX + str(int(delivery_id))

    # Flattened repository columns the meaningful-change comparison reads.
    _EVENT_COLUMNS = (
        ("maxPackage", "max_package"), ("minPackage", "min_package"),
        ("placementtype", "placement_type"), ("internshiptype", "internship_type"),
    )

    @classmethod
    def _snapshot_event_distinct(cls, existing_snapshot, new_snapshot):
        """F07: event identity follows the approved meaningful-change semantics,
        not bytewise snapshot equality. Equivalent representations (e.g. zero
        seconds in a time field) are replays of the same event; only a
        meaningful change starts a new one."""
        if existing_snapshot == new_snapshot:
            return False
        from app.monitoring.detector import CompanyDetector
        from app.tpo.models import CompanyRecord
        try:
            if existing_snapshot is None or new_snapshot is None:
                return True
            old_record = CompanyRecord(**json.loads(existing_snapshot))
            new_record = CompanyRecord(**json.loads(new_snapshot))
        except (TypeError, ValueError):
            # Unparseable evidence can never prove equivalence; bytewise
            # difference stands.
            return existing_snapshot != new_snapshot
        existing_dict = {column: getattr(old_record, field, None) for field, column in cls._EVENT_COLUMNS}
        existing_dict["is_active"] = old_record.isactive
        existing_dict["raw_data_json"] = existing_snapshot
        return bool(CompanyDetector.meaningful_changes(existing_dict, new_record))

    def enqueue_deliveries(self, deliveries: List[Dict[str, Any]]) -> int:
        if not deliveries:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with self._get_conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            for d in deliveries:
                notif_type = d["notification_type"]
                snapshot = d.get("raw_data_snapshot")
                existing = conn.execute(
                    """SELECT id, status, raw_data_snapshot, error_message FROM notification_deliveries
                       WHERE user_id=? AND company_id=? AND notification_type=?""",
                    (d["user_id"], str(d["company_id"]), notif_type),
                ).fetchone()
                if (existing and notif_type == 'UPDATE'
                        and existing['status'] in ('PENDING', 'PROCESSING')
                        and snapshot is not None
                        and self._snapshot_event_distinct(existing['raw_data_snapshot'], snapshot)):
                    # Roll back the whole recipient enqueue unit. The persisted
                    # admin/fanout obligation (with its own event snapshot)
                    # remains retryable rather than being acknowledged while a
                    # distinct UPDATE is silently lost.
                    raise DeliveryFanoutDeferred('prior_update_still_pending')

                if existing is None:
                    # No prior row — insert fresh.
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO notification_deliveries
                               (user_id, company_id, notification_type, status, created_at, raw_data_snapshot)
                           VALUES (?, ?, ?, 'PENDING', ?, ?)""",
                        (d["user_id"], str(d["company_id"]), notif_type, now, snapshot),
                    )
                    if cursor.rowcount > 0:
                        inserted += 1
                elif notif_type == 'NEW':
                    # NEW deliveries remain strictly one-shot per company/user
                    # pair for the lifetime of the row.
                    continue
                elif existing['status'] in ('SENT', 'FAILED'):
                    # A distinct event of this type has arrived (e.g. a later
                    # genuine deadline change). F06: a terminal FAILED row may
                    # accept it too — terminality applies to the prior attempt,
                    # whose outcome is preserved in the append-only outcome log,
                    # never to all future distinct events.
                    if snapshot is not None and not self._snapshot_event_distinct(
                            existing['raw_data_snapshot'], snapshot):
                        # A replay after durable enqueue but before fanout
                        # receipt, or an equivalent representation of the
                        # already-delivered event: never resend.
                        continue
                    if existing['status'] == 'FAILED':
                        conn.execute("""
                            INSERT INTO notification_delivery_outcome_log
                                (delivery_id, user_id, company_id, notification_type,
                                 prior_status, event_snapshot, outcome_note, recorded_at)
                            VALUES (?, ?, ?, ?, 'FAILED', ?, ?, ?)
                        """, (existing["id"], d["user_id"], str(d["company_id"]), notif_type,
                              existing["raw_data_snapshot"], existing["error_message"], now))
                    conn.execute(
                        """UPDATE notification_deliveries
                           SET status='PENDING', created_at=?, attempt_count=0,
                               lease_expires_at=NULL, last_attempt_at=NULL,
                               error_message=NULL, sent_at=NULL,
                               raw_data_snapshot=?
                           WHERE id=?""",
                        (now, snapshot, existing["id"]),
                    )
                    inserted += 1
                # else: PENDING/PROCESSING same event — true duplicate; skip.
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
            if self._consent_binding(conn) is not None:
                # D10 disposition is unapproved; retained legacy obligations
                # cannot re-enter automatic delivery, even after opt-in.
                return []
            # Python's sqlite3 context manager does not start a transaction for
            # SELECT. Reserve the writer before reading candidates, and retain
            # it through hydration so no second connection can claim them.
            conn.execute("BEGIN IMMEDIATE")
            self._recover_stale(conn, now_iso)
            # Select candidate deliveries
            cursor = conn.execute("""
                SELECT id FROM notification_deliveries
                WHERE status = 'PENDING' AND attempt_count < max_attempts
                ORDER BY created_at ASC, id ASC
                LIMIT ?
            """, (batch_size,))
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
            tokens = {}
            for delivery_id in candidate_ids:
                token = secrets.token_hex(32)
                tokens[delivery_id] = token
                conn.execute("""INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                    (self._claim_key(delivery_id), token, now_iso))

            # Retrieve full records with user and company data.
            # F11: prefer raw_data_snapshot (event-time state) over current DB raw_data_json.
            select_sql = f"""
                SELECT d.*, u.email as user_email, u.branch_canonical as user_branch,
                       c.company as company_name,
                       COALESCE(d.raw_data_snapshot, c.raw_data_json) as company_raw_json
                FROM notification_deliveries d
                JOIN users u ON d.user_id = u.id
                JOIN companies c ON d.company_id = c.id
                WHERE d.id IN ({placeholders})
            """
            cursor = conn.execute(select_sql, candidate_ids)
            records = [dict(r) for r in cursor.fetchall()]
            for record in records:
                record["id"] = _ClaimedDeliveryId(record["id"], tokens[record["id"]])
            conn.commit()
            return records

    def mark_sent(self, delivery_id: int) -> bool:
        """Record provider acceptance only for the still-current claim."""
        if not isinstance(delivery_id, _ClaimedDeliveryId):
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE notification_deliveries 
                SET status = 'SENT', sent_at = ?, lease_expires_at = NULL, error_message = NULL
                WHERE id = ? AND status = 'PROCESSING' AND lease_expires_at > ?
                  AND EXISTS(SELECT 1 FROM system_state WHERE key=? AND value=?)
            """, (now, delivery_id, now, self._claim_key(delivery_id), delivery_id._claim_token))
            if cursor.rowcount:
                conn.execute("DELETE FROM system_state WHERE key=? AND value=?",
                    (self._claim_key(delivery_id), delivery_id._claim_token))
            conn.commit()
            return cursor.rowcount == 1

    def release_failed(
        self, 
        delivery_id: int, 
        error_message: str, 
        terminal: bool = False
    ) -> bool:
        if not isinstance(delivery_id, _ClaimedDeliveryId):
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("""
                UPDATE notification_deliveries 
                SET status = CASE WHEN ? OR attempt_count >= max_attempts THEN 'FAILED' ELSE 'PENDING' END,
                    error_message = ?, lease_expires_at = NULL, last_attempt_at = ?
                WHERE id = ? AND status = 'PROCESSING' AND lease_expires_at > ?
                  AND EXISTS(SELECT 1 FROM system_state WHERE key=? AND value=?)
            """, (terminal, error_message, now, delivery_id, now,
                  self._claim_key(delivery_id), delivery_id._claim_token))
            if cursor.rowcount:
                conn.execute("DELETE FROM system_state WHERE key=? AND value=?",
                    (self._claim_key(delivery_id), delivery_id._claim_token))
            conn.commit()
            return cursor.rowcount == 1

    def record_ambiguous_acceptance(self, delivery_id: int) -> None:
        """F04: durable, bounded marker that a provider accepted a message the
        database could not acknowledge. Operational signal only — never a
        resend trigger."""
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                ("notification:v1:ambiguous_acceptance:" + str(int(delivery_id)), now, now))
            conn.commit()

    def current_delivery_context(self, delivery_id: int) -> Optional[Dict[str, Any]]:
        """Check authority and current consent/source state just before transport.

        This read fences workers that already lost their lease. It cannot revoke
        an SMTP call in flight or make SMTP and SQLite a single transaction.
        """
        if not isinstance(delivery_id, _ClaimedDeliveryId):
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(companies)')}
            # Minimal legacy repository schemas have no normalized status.
            # Missing status is unknown, never an inferred deactivation.
            active_column = 'c.is_active' if 'is_active' in columns else 'NULL'
            row = conn.execute(f"""SELECT u.is_active, u.is_verified, u.graduation_year,
                    u.branch_canonical, p.pref_internship,p.pref_placement,p.pref_ppo,
                    {active_column} AS company_is_active,c.raw_data_json AS current_company_json
                FROM notification_deliveries d
                JOIN users u ON u.id=d.user_id
                JOIN user_preferences p ON p.user_id=u.id
                JOIN companies c ON c.id=d.company_id
                WHERE d.id=? AND d.status='PROCESSING' AND d.lease_expires_at>?
                  AND EXISTS(SELECT 1 FROM system_state WHERE key=? AND value=?)""",
                (delivery_id, now, self._claim_key(delivery_id), delivery_id._claim_token)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['current_routing_available'] = {'organizations', 'eligible_programs'}.issubset(columns)
            return result

    def _recover_stale(self, conn, now):
        cursor = conn.execute("""UPDATE notification_deliveries
            SET status=CASE WHEN attempt_count>=max_attempts THEN 'FAILED' ELSE 'PENDING' END,
                lease_expires_at=NULL,
                error_message=CASE WHEN attempt_count>=max_attempts
                    THEN 'Retry attempts exhausted after expired lease' ELSE error_message END
            WHERE status='PROCESSING' AND lease_expires_at<=?""", (now,))
        recovered = cursor.rowcount
        # Includes crash leftovers and rows removed by unsubscribe/cancellation.
        # A live owner's key always has a matching PROCESSING row and survives.
        conn.execute("""DELETE FROM system_state WHERE key LIKE ?
            AND NOT EXISTS(SELECT 1 FROM notification_deliveries d
                WHERE system_state.key=? || d.id AND d.status='PROCESSING')""",
            (self._CLAIM_PREFIX + "%", self._CLAIM_PREFIX))
        conn.execute("""UPDATE notification_deliveries SET status='FAILED',
            error_message='Retry attempts exhausted' WHERE status='PENDING' AND attempt_count>=max_attempts""")
        return recovered

    def recover_stale_leases(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            recovered = self._recover_stale(conn, now)
            conn.commit()
            return recovered

    def cancel_pending_deliveries_for_user(self, user_id: int) -> int:
        with self._get_conn() as conn:
            if self._consent_binding(conn) is not None:
                from app.database.event_repository import PolicyDenied
                raise PolicyDenied("legacy delivery disposition held pending D10")
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
