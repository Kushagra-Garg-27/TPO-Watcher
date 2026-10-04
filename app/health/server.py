import logging
import sqlite3
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
    Checks SQLite database connectivity and provides baseline & delivery statistics.
    Does NOT allow triggering TPO scraping or administrative state modification.
    """
    db_ok = False
    baseline_ok = False
    deliveries_stats = {}

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT value FROM system_state WHERE key = 'baseline_initialized'")
            row = cursor.fetchone()
            baseline_ok = bool(row and row["value"] == "true")
            
            cursor = conn.execute("SELECT status, count(*) as cnt FROM notification_deliveries GROUP BY status")
            for r in cursor.fetchall():
                deliveries_stats[r["status"]] = r["cnt"]

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
        "deliveries": deliveries_stats
    }
