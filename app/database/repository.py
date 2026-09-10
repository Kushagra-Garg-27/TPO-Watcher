import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from app.tpo.models import CompanyRecord
import os

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "watcher.sqlite")

class DatabaseRepository:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

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

    def upsert_company(self, record: CompanyRecord, is_new: bool = False, notified_at: Optional[datetime] = None):
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc)
            raw_json = record.model_dump_json()
            
            cid = str(record.id)
            existing = self.get_company(cid)
            
            first_seen = now if existing is None else existing["first_seen_at"]
            last_notified = notified_at.isoformat() if notified_at else (existing["last_notified_at"] if existing else None)

            def to_str(val):
                if val is None:
                    return None
                if isinstance(val, list):
                    return ", ".join(str(v) for v in val if v is not None)
                return str(val)

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
                to_str(record.isactive),
                first_seen.isoformat() if isinstance(first_seen, datetime) else first_seen,
                now.isoformat(),
                last_notified,
                raw_json
            ))
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

    def add_pending_notification(self, company_id: str, notif_type: str, changes: List[str]):
        with self._get_conn() as conn:
            now = datetime.now(timezone.utc).isoformat()
            changes_json = json.dumps(changes)
            conn.execute("""
                INSERT INTO pending_notifications (company_id, notification_type, changes_json, created_at)
                VALUES (?, ?, ?, ?)
            """, (company_id, notif_type, changes_json, now))
            conn.commit()

    def get_pending_notifications(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM pending_notifications ORDER BY created_at ASC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def remove_pending_notification(self, notif_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM pending_notifications WHERE id = ?", (notif_id,))
            conn.commit()
