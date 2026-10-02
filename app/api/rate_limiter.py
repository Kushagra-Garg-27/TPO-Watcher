import time
import threading
import logging
import ipaddress
from typing import Dict, List, Tuple, Optional
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

class InMemoryRateLimiter:
    """
    Thread-safe, in-process sliding-window rate limiter.
    
    Limitations:
    - In-process / process-local: limits are tracked per application process.
      In a multi-worker or multi-container deployment without sticky sessions,
      counters are not shared across processes.
    - Ephemeral: state is stored in memory and reset upon application restart.
    - Memory: expired entries are cleaned up periodically during rate-limit checks.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._records: Dict[str, List[float]] = {}
        self._last_cleanup = time.monotonic()

    def check(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
        """
        Checks whether a request is allowed under the rate limit.
        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        now = time.monotonic()
        with self._lock:
            # Periodic cleanup every 60 seconds
            if now - self._last_cleanup > 60:
                self._cleanup(now)

            timestamps = self._records.get(key, [])
            cutoff = now - window_seconds
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= max_requests:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now))
                self._records[key] = timestamps
                return False, retry_after

            timestamps.append(now)
            self._records[key] = timestamps
            return True, 0

    def _cleanup(self, now: float) -> None:
        """Purge empty or completely expired keys to prevent memory leak."""
        keys_to_delete = []
        for k, ts in self._records.items():
            if not ts or (now - ts[-1] > 3600):
                keys_to_delete.append(k)
        for k in keys_to_delete:
            del self._records[k]
        self._last_cleanup = now

    def reset(self) -> None:
        """Reset all rate limiter state. Used primarily for test isolation."""
        with self._lock:
            self._records.clear()
            self._last_cleanup = time.monotonic()

limiter = InMemoryRateLimiter()

def get_client_ip(request: Request) -> str:
    """
    Extracts and validates the client IP address.
    
    Deployment Topology:
        Internet (Client) -> Caddy (Trusted Reverse Proxy) -> Watcher Container
        
    Security Rationale:
        When an external client sends an untrusted X-Forwarded-For header, Caddy appends
        the client's actual remote IP to the end of the comma-separated list.
        Taking the leftmost IP would allow an attacker to spoof arbitrary client IPs and
        bypass IP-based rate limiting.
        In this single-proxy topology, the true client IP recorded by Caddy is the
        rightmost valid IP.
    """
    # 1. Check X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        # Under single reverse proxy (Caddy), the proxy appends the real remote IP at the end.
        for candidate in reversed(parts):
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue

    # 2. Check X-Real-IP
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        candidate = real_ip.strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass

    # 3. Direct client connection host (handles local dev and testclient)
    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"

def check_rate_limit(
    request: Request,
    key: str,
    max_requests: int,
    window_seconds: int,
    detail: Optional[str] = None
) -> None:
    """
    Enforces a rate limit for an explicit key (e.g. per-IP or per-email).
    Raises HTTPException(429) if exceeded.
    """
    allowed, retry_after = limiter.check(key, max_requests, window_seconds)
    if not allowed:
        client_ip = get_client_ip(request)
        logger.warning(f"Rate limit exceeded for key '{key}' from IP '{client_ip}'. Retry after {retry_after}s.")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail or f"Rate limit exceeded. Please retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )
