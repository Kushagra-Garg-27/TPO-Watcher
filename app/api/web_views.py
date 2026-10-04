"""
web_views.py — SPA shell serving for the React frontend.

All user-facing URL paths (/, /signup, /preferences, /verify, /unsubscribe, etc.)
are served as the React SPA index.html, which handles client-side routing.

The backend-served HTML pages have been replaced by the React frontend build.
API routes (/api/v1/*) remain completely unchanged and unaffected by this module.

IMPORTANT: This file intentionally does NOT:
- Modify any API endpoint behavior
- Return TPO credentials in any response
- Trigger TPO scraping
- Read another user's preferences
"""
import logging
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

# Path to the React build output index.html
_STATIC_DIR = Path(__file__).parent.parent / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"


def spa_response(status_code: int = 200, fallback_text: str = "") -> HTMLResponse:
    """Return the React SPA shell, with optional fallback/SSR text injected for accessibility & testing."""
    if _INDEX_HTML.exists():
        content = _INDEX_HTML.read_text(encoding="utf-8")
        if fallback_text and '<div id="root"></div>' in content:
            content = content.replace(
                '<div id="root"></div>',
                f'<div id="root"><noscript>{fallback_text}</noscript></div>'
            )
        return HTMLResponse(content=content, status_code=status_code)
    # Fallback: minimal page pointing to /docs if no build exists
    fallback_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>VIT TPO Watcher</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 80px auto; padding: 0 24px; }}
    h1 {{ color: #1e293b; }}
    p {{ color: #64748b; }}
    a {{ color: #4f46e5; }}
  </style>
</head>
<body>
  <h1>VIT TPO Watcher</h1>
  <div>{fallback_text or '<p>Frontend build not found. Run <code>cd frontend &amp;&amp; npm run build</code> first.</p>'}</div>
  <p><a href="/docs">API Documentation →</a></p>
</body>
</html>"""
    return HTMLResponse(content=fallback_content, status_code=status_code)


_spa_response = spa_response


# ── Frontend SPA routes ──────────────────────────────────────────────────────
# These routes serve the React app shell. Client-side routing handles the rest.

@router.get("/", response_class=HTMLResponse)
def root():
    return spa_response()


@router.get("/signup", response_class=HTMLResponse)
def signup_page():
    return spa_response()


@router.get("/preferences", response_class=HTMLResponse)
def preferences_page():
    return spa_response()


@router.get("/preferences/confirm", response_class=HTMLResponse)
def preferences_confirm_page():
    return spa_response()


@router.get("/verify", response_class=HTMLResponse)
def verify_page():
    return spa_response()


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page():
    return spa_response()


# Catch-all for any other frontend path not matched by API routes
# This must come LAST to not shadow API endpoints
@router.get("/{full_path:path}", response_class=HTMLResponse)
def spa_catchall(full_path: str):
    # Never intercept API, health, static asset, or documentation paths
    if (
        full_path.startswith("api/")
        or full_path.startswith("health")
        or full_path.startswith("assets/")
        or full_path in ("docs", "openapi.json", "redoc")
        or full_path.startswith("docs/")
    ):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return spa_response()
