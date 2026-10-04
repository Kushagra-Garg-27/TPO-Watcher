import logging
from typing import Optional, Set
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send
from app.config import settings

logger = logging.getLogger(__name__)

# Paths requiring Cache-Control: no-store
NO_STORE_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/preferences",
    "/api/v1/unsubscribe",
    "/verify",
    "/unsubscribe",
    "/preferences",
)

CSP_REPORT_ONLY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


class SecurityHeadersMiddleware:
    """
    Pure ASGI middleware injecting baseline security headers and cache-control directives.
    Operates at the outermost ASGI boundary so it decorates all responses including
    static files, API routes, 404, 405, 422, and unhandled 500 responses.
    """

    def __init__(
        self,
        app: ASGIApp,
        hsts_max_age: Optional[int] = None,
        csp_report_only: Optional[str] = None,
    ):
        self.app = app
        self.hsts_max_age = (
            hsts_max_age if hsts_max_age is not None else getattr(settings, "HSTS_MAX_AGE", 86400)
        )
        self.csp_report_only = csp_report_only or CSP_REPORT_ONLY_POLICY

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_security_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)

                # Standard security headers (do not override if route explicitly set them)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", "no-referrer")
                headers.setdefault(
                    "strict-transport-security",
                    f"max-age={self.hsts_max_age}",
                )
                headers.setdefault("permissions-policy", PERMISSIONS_POLICY)
                headers.setdefault("cross-origin-opener-policy", "same-origin")
                headers.setdefault("content-security-policy-report-only", self.csp_report_only)

                # Cache-Control: no-store on sensitive auth/pref/unsub routes and SPA shells
                if any(path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?") for prefix in NO_STORE_PREFIXES):
                    headers.setdefault("cache-control", "no-store")

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
