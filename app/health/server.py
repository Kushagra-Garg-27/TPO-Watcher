import logging
import sqlite3
import os
from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, status, Request
from fastapi.responses import JSONResponse

DB_PATH=os.environ.get('DB_PATH','watcher.sqlite')

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Monitoring"])


def inert_health(application,policy,index,assets):
    from app.monitoring.deployment_policy import public_listing,PolicyError
    available=False
    try:
        if getattr(application.state,'notification_runtime',None) is not None:
            raise PolicyError('Unexpected runtime activation')
        public_listing(policy)
        available=index.is_file() and assets.is_dir()
    except (PolicyError,OSError):pass
    return dict(mode='INERT_REHEARSAL',live=True,read_only_ready=available,
        notification_ready=False,polling=False,sending=False,consent_processing=False)

@router.get("/health")
def health_check(request: Request) -> Dict[str, Any]:
    """
    Public health check probe.
    Checks SQLite database connectivity and provides baseline, watcher freshness, & delivery statistics.
    Does NOT allow triggering TPO scraping or administrative state modification.
    """
    runtime=getattr(request.app.state,"notification_runtime",None)
    if runtime is not None:
        diagnostic=runtime.diagnostics()
        running=diagnostic["runtime_available"]
        return JSONResponse(status_code=200 if running else 503,
            content={"status":"guarded" if running else "held","notification":diagnostic})
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
