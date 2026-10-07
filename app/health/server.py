import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.database.repository import DB_PATH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Monitoring"])

@router.get("/health")
def health_check() -> Dict[str, Any]:
    """
    Public health check probe.
    Checks SQLite database connectivity and provides baseline, watcher freshness, & delivery statistics.
    Does NOT allow triggering TPO scraping or administrative state modification.
    """
    db_ok = False
    baseline_ok = False
    deliveries_stats = {}
    watcher_status = "unknown"

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT value FROM system_state WHERE key = 'baseline_initialized'")
            row = cursor.fetchone()
            baseline_ok = bool(row and row["value"] == "true")
            
            cursor = conn.execute("SELECT status, count(*) as cnt FROM notification_deliveries GROUP BY status")
            for r in cursor.fetchall():
                deliveries_stats[r["status"]] = r["cnt"]

            cursor = conn.execute("SELECT value FROM system_state WHERE key = 'last_successful_watcher_run'")
            hb_row = cursor.fetchone()
            if hb_row and hb_row["value"]:
                try:
                    last_run = datetime.fromisoformat(hb_row["value"])
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=timezone.utc)
                    age_seconds = (datetime.now(timezone.utc) - last_run).total_seconds()
                    if age_seconds <= 11 * 3600:
                        watcher_status = "ok"
                    else:
                        watcher_status = "stale"
                except Exception:
                    watcher_status = "unknown"

        db_ok = True
    except Exception as e:
        logger.exception("Health check failed database query: %s", e)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": "unreachable"
            }
        )

    return {
        "status": "healthy",
        "database": "connected",
        "baseline_initialized": baseline_ok,
        "watcher": watcher_status,
        "deliveries": deliveries_stats
    }
