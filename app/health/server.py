import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, status, Request
from fastapi.responses import JSONResponse

from app.database.repository import DB_PATH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Monitoring"])

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


def _missed_scheduled_start(started, now) -> Optional[bool]:
    """Exact per-schedule missed-start evaluation.

    Computes the first configured check instant strictly AFTER the last
    recorded start and reports missed only once now exceeds that instant by
    the 30-minute slack — evaluating the actual configured schedule (default
    00:00/10:00/17:00 IST), not a coarse maximum-gap estimate."""
    try:
        from zoneinfo import ZoneInfo
        from app.config import settings
        from app.monitoring.scheduler import parse_time_str
        tz = ZoneInfo(settings.TIMEZONE)
        times = sorted(parse_time_str(t) for t in settings.get_check_times_list())
        if not times:
            return None
    except Exception:
        return None
    local_start = started.astimezone(tz)
    for day_offset in (0, 1, 2):
        base = local_start.date() + timedelta(days=day_offset)
        for scheduled in times:
            candidate = datetime.combine(base, scheduled, tzinfo=tz)
            if candidate > started:
                return now > candidate + timedelta(seconds=1800)
    return None


@router.get("/health/details")
def health_details(request: Request) -> Dict[str, Any]:
    """F12: readiness detail behind the unchanged /health liveness contract.

    Distinguishes API/database reachability, source-ingestion freshness,
    scheduler start ledger, queue backlog/terminal failures and ambiguous
    provider acceptances. Read-only; never triggers a source poll, drain or
    other mutation, and never exposes subscriber data."""
    detail: Dict[str, Any] = {"database": "unreachable"}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            def state(key):
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
                return row["value"] if row else None

            def parse_when(value):
                if not value:
                    return None
                try:
                    parsed = datetime.fromisoformat(value)
                except Exception:
                    return None
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed

            now = datetime.now(timezone.utc)
            detail = {"database": "connected"}

            baseline = state("baseline_initialized")
            detail["baseline_initialized"] = baseline == "true"

            last_success = parse_when(state("last_successful_watcher_run"))
            detail["last_successful_watcher_run"] = last_success.isoformat() if last_success else None
            detail["watcher_fresh"] = bool(
                last_success and (now - last_success).total_seconds() <= 11 * 3600)

            last_failure = parse_when(state("last_failed_watcher_run"))
            detail["last_failed_watcher_run"] = last_failure.isoformat() if last_failure else None
            try:
                detail["consecutive_watcher_failures"] = int(state("consecutive_watcher_failures") or 0)
            except ValueError:
                detail["consecutive_watcher_failures"] = None

            started = parse_when(state("last_scheduled_check_started"))
            detail["last_scheduled_check_started"] = started.isoformat() if started else None
            missed = "unknown"
            if started is not None:
                missed = _missed_scheduled_start(started, now)
            detail["missed_scheduled_start"] = missed

            try:
                detail["last_observation_company_count"] = int(
                    state("last_observation_company_count"))
            except (TypeError, ValueError):
                detail["last_observation_company_count"] = None
            detail["last_admin_drain_complete"] = state("last_admin_drain_complete")

            detail["deliveries"] = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    "SELECT status, count(*) AS cnt FROM notification_deliveries GROUP BY status")}
            detail["admin_pending"] = conn.execute(
                "SELECT count(*) FROM pending_notifications").fetchone()[0]
            detail["ambiguous_provider_acceptances"] = conn.execute(
                "SELECT count(*) FROM system_state"
                " WHERE key LIKE 'notification:v1:ambiguous_acceptance:%'").fetchone()[0]
    except Exception:
        logger.exception("Health detail query failed.")
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=detail)

    detail["status"] = "healthy" if detail.get("watcher_fresh") else "degraded"
    return detail
