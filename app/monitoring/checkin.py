"""
Optional Healthchecks.io check-in integration for TPO-Watcher.
Python stdlib urllib only. Timeout <= 5 seconds.
Monitoring failures are isolated and never impact application business results.
URLs, tokens, and query parameters are never logged.
"""

import logging
import urllib.request
from typing import Optional

logger = logging.getLogger("tpo-watcher")

def send_healthcheck_ping(url: Optional[str], success: bool, timeout: float = 5.0) -> bool:
    """
    Sends Healthchecks.io ping for success or failure.
    - success: GET to <url>
    - failure: GET to <url>/fail
    
    Security & Reliability Rules:
    - Never logs URL, tokens, or query parameters.
    - Catches all exceptions: monitoring failure never impacts application execution.
    - Timeout <= 5 seconds.
    """
    if not url or not url.strip():
        return False
    
    clean_url = url.strip()
    target = clean_url if success else f"{clean_url.rstrip('/')}/fail"
    try:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": "TPO-Watcher-Healthcheck/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 201, 204)
    except Exception as exc:
        logger.warning(
            "Healthcheck check-in ping failed (%s): %s",
            "success" if success else "fail",
            type(exc).__name__
        )
        return False
