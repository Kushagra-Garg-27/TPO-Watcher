import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any, Set
from app.tpo.models import CompanyRecord
import os

logger = logging.getLogger(__name__)

from app.database.migrations import run_migrations
from app.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteTokenRepository,
    SQLiteDeliveryRepository
)

DB_PATH = os.environ.get("DB_PATH", "watcher.sqlite")

class DatabaseRepository:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()
        run_migrations(self.db_path)
        self.users = SQLiteUserRepository(self.db_path)
        self.tokens = SQLiteTokenRepository(self.db_path)
        self.deliveries = SQLiteDeliveryRepository(self.db_path)

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id TEXT PRIMARY KEY,
                    company TEXT,
                    company_code TEXT,
                    registration_start TEXT,
                    registration_end TEXT,
                    max_package TEXT,
                    min_package TEXT,
                    placement_type TEXT,
                    academic_year TEXT,
                    company_type TEXT,
                    internship_type TEXT,
                    skills TEXT,
                    eligible_programs TEXT,
                    organizations TEXT,
                    is_active TEXT,
                    first_seen_at TIMESTAMP,
                    last_seen_at TIMESTAMP,
                    last_notified_at TIMESTAMP,
                    raw_data_json TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id TEXT,
                    notification_type TEXT,
                    changes_json TEXT,
                    created_at TIMESTAMP,
                    event_snapshot_json TEXT,
                    FOREIGN KEY(company_id) REFERENCES companies(id)
                )
            """)
            conn.commit()

    def get_all_company_ids(self) -> List[str]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT id FROM companies")
            return [row["id"] for row in cursor.fetchall()]

    def get_company(self, company_id: Any) -> Optional[dict]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM companies WHERE id = ?", (str(company_id),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_opportunities(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT
                    id,
                    company,
                    max_package,
                    min_package,
                    placement_type,
                    registration_end,
                    eligible_programs,
                    company_type,
                    is_active,
                    first_seen_at
                FROM companies
                WHERE is_active = 'True'
                ORDER BY first_seen_at DESC, id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def deactivate_missing_companies(self, current_active_ids: Set[str]) -> List[str]:
        """
        Reconciles database active records against an authoritative incoming feed.
        Marks records currently having is_active = 'True' as 'False' if their ID
        is absent from current_active_ids.

        Safety invariants:
        - Never deactivates any records if current_active_ids is empty.
        - Never touches records with is_active = 'False' or is_active IS NULL.
        - Never deletes records.
        - Preserves first_seen_at, last_seen_at, raw_data_json, and notification history.
        - Transactionally safe.

        Returns the list of deactivated company IDs.
        """
        if not current_active_ids:
            logger.warning(
                "deactivate_missing_companies called with empty current_active_ids. "
                "Safety guardrail triggered: skipping deactivation to prevent accidental database wipe."
            )
            return []

        active_id_strs = {str(cid).strip() for cid in current_active_ids if cid}
        if not active_id_strs:
            return []

        with self._get_conn() as conn:
            placeholders = ",".join("?" for _ in active_id_strs)
            cursor = conn.execute(
                f"""
                SELECT id, company 
                FROM companies 
                WHERE is_active = 'True' AND id NOT IN ({placeholders})
                """,
                tuple(active_id_strs)
            )
            stale_rows = cursor.fetchall()
            if not stale_rows:
                return []

            deactivated_ids = [str(r["id"]) for r in stale_rows]
            deact_placeholders = ",".join("?" for _ in deactivated_ids)
            conn.execute(
                f"""
                UPDATE companies 
                SET is_active = 'False' 
                WHERE id IN ({deact_placeholders})
                """,
                tuple(deactivated_ids)
            )
            conn.commit()

            for r in stale_rows:
                logger.info(
                    f"Feed reconciliation: company '{r['company']}' (ID: {r['id']}) "
                    f"absent from authoritative source feed. Marked is_active = 'False'."
                )

            return deactivated_ids

    def _upsert_company_conn(self, conn, record: CompanyRecord,
                             is_new: bool = False, notified_at: Optional[datetime] = None) -> bool:
        """Upsert within a caller-owned transaction; the caller commits.

        Returns whether the company already existed before this upsert, so
        callers can make introduction-obligation decisions inside the same
        serialized transaction."""
        now = datetime.now(timezone.utc)
        raw_json = record.model_dump_json()

        cid = str(record.id)
        existing_row = conn.execute("SELECT * FROM companies WHERE id = ?", (cid,)).fetchone()
        existing = dict(existing_row) if existing_row else None
        existed = existing is not None

        first_seen = now if existing is None else existing["first_seen_at"]
        last_notified = notified_at.isoformat() if notified_at else (existing["last_notified_at"] if existing else None)

        def to_str(val):
            if val is None:
                return None
            if isinstance(val, list):
                return ", ".join(str(v) for v in val if v is not None)
            return str(val)

        def normalize_active_flag(val) -> str:
            """G04: Always store a string sentinel ('True' or 'False') for is_active.
            Python None or the literal string 'None' must never be
            persisted as SQL NULL — that creates an unreconcilable orphan
            (the deactivate_missing_companies guard only matches 'True').

            If explicitly active ('true', '1', 'yes') -> 'True'.
            If explicitly inactive ('false', '0', 'no') -> 'False'.
            If absent or unknown:
              - If the company already exists in the database, preserve its existing is_active status.
              - If newly discovered in the active portal feed, default to 'True'.
            An absent or unknown status must never be treated as a confirmed inactive status.
            """
            if val is not None:
                s = str(val).strip().lower()
                if s in ("true", "1", "yes"):
                    return "True"
                if s in ("false", "0", "no"):
                    return "False"
            # Absent or unknown status from portal
            if existing is not None and existing.get("is_active"):
                return str(existing["is_active"])
            return "True"

        conn.execute("""
            INSERT INTO companies (
                id, company, company_code, registration_start, registration_end,
                max_package, min_package, placement_type, academic_year,
                company_type, internship_type, skills, eligible_programs,
                organizations, is_active, first_seen_at, last_seen_at,
                last_notified_at, raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                company=excluded.company,
                company_code=excluded.company_code,
                registration_start=excluded.registration_start,
                registration_end=excluded.registration_end,
                max_package=excluded.max_package,
                min_package=excluded.min_package,
                placement_type=excluded.placement_type,
                academic_year=excluded.academic_year,
                company_type=excluded.company_type,
                internship_type=excluded.internship_type,
                skills=excluded.skills,
                eligible_programs=excluded.eligible_programs,
                organizations=excluded.organizations,
                is_active=excluded.is_active,
                last_seen_at=excluded.last_seen_at,
                last_notified_at=excluded.last_notified_at,
                raw_data_json=excluded.raw_data_json
        """, (
            cid,
            to_str(record.company),
            to_str(record.company_code),
            to_str(record.regStartdate),
            to_str(record.regEnddate),
            to_str(record.maxPackage),
            to_str(record.minPackage),
            to_str(record.placementtype),
            to_str(record.academicyear),
            to_str(record.companytype),
            to_str(record.internshiptype),
            to_str(record.skill),
            to_str(record.tpoprogram or record.programnew),
            to_str(record.organization),
            normalize_active_flag(record.isactive),  # G04: never store NULL
            first_seen.isoformat() if isinstance(first_seen, datetime) else first_seen,
            now.isoformat(),
            last_notified,
            raw_json
        ))
        return existed

    def apply_company_event(self, record: CompanyRecord, *, is_new: bool = False,
                            notification_type: Optional[str] = None,
                            changes: Optional[List[str]] = None,
                            event_snapshot: Optional[str] = None):
        """F05: the company transition and its durable notification obligation
        commit in one transaction, or roll back together. `notification_type=None`
        records only the company transition (baseline ingest or no-change refresh)."""
        with self._get_conn() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            existed = self._upsert_company_conn(conn, record, is_new=is_new)
            if notification_type is not None:
                snapshot = event_snapshot if event_snapshot is not None else record.model_dump_json()
                # Concurrent-writer idempotency (F05): inside this serialized
                # write transaction, a NEW obligation is created only by the
                # transition that actually introduced the company row, and an
                # identical pending obligation (same event snapshot) is never
                # duplicated. Overlapping writers therefore converge on
                # exactly one obligation per distinct event.
                if notification_type == "NEW" and existed:
                    pass
                elif conn.execute(
                        """SELECT 1 FROM pending_notifications
                           WHERE company_id=? AND notification_type=? AND event_snapshot_json IS ?""",
                        (str(record.id), notification_type, snapshot)).fetchone() is None:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute("""
                        INSERT INTO pending_notifications
                            (company_id, notification_type, changes_json, created_at, event_snapshot_json)
                        VALUES (?, ?, ?, ?, ?)
                    """, (str(record.id), notification_type, json.dumps(changes or []),
                          now, snapshot))
            conn.commit()

    def upsert_company(self, record: CompanyRecord, is_new: bool = False, notified_at: Optional[datetime] = None):
        with self._get_conn() as conn:
            self._upsert_company_conn(conn, record, is_new=is_new, notified_at=notified_at)
            conn.commit()


    def mark_notified(self, company_id: str):
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE companies SET last_notified_at = ? WHERE id = ?", (now, company_id))
            conn.commit()

    def is_baseline_initialized(self) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT value FROM system_state WHERE key = 'baseline_initialized'")
            row = cursor.fetchone()
            return row is not None and row["value"] == "true"

    def set_baseline_initialized(self):
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at) 
                VALUES ('baseline_initialized', 'true', ?)
                ON CONFLICT(key) DO UPDATE SET value='true', updated_at=excluded.updated_at
            """, (now,))
            conn.commit()

    def get_system_state(self, key: str) -> Optional[str]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT value FROM system_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None

    def set_system_state(self, key: str, value: str) -> None:
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, value, now))
            conn.commit()

    def add_pending_notification(self, company_id: str, notif_type: str, changes: List[str],
                                 event_snapshot: Optional[str] = None):
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            changes_json = json.dumps(changes)
            conn.execute("""
                INSERT INTO pending_notifications
                    (company_id, notification_type, changes_json, created_at, event_snapshot_json)
                VALUES (?, ?, ?, ?, ?)
            """, (company_id, notif_type, changes_json, now, event_snapshot))
            conn.commit()

    def get_pending_notifications(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM pending_notifications ORDER BY created_at ASC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def remove_pending_notification(self, notif_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM pending_notifications WHERE id = ?", (notif_id,))
            # This marker is written only after durable recipient enqueue. Its
            # lifetime matches the admin obligation, including admin retries.
            conn.execute("DELETE FROM system_state WHERE key = ?", (f"notification:v1:fanout:{notif_id}",))
            conn.commit()
