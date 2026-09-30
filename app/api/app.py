import logging
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes_auth import router as auth_router
from app.api.routes_preferences import router as pref_router
from app.api.web_views import router as views_router
from app.health.server import router as health_router

logger = logging.getLogger(__name__)

# Path to the React build output
STATIC_DIR = Path(__file__).parent.parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title="VIT TPO Watcher — Public V1",
        description="Autonomous TPO Opportunity Watcher for the VIT Pune Class of 2028.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None
    )

    # Note on CORS: Per Requirement 15, broad CORS is explicitly omitted.
    # The application serves same-origin forms and secure APIs.

    # Mount API routers first — these take priority over static files
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(pref_router)

    # Mount the built React assets under /assets (Vite output) BEFORE views_router catchall
    if STATIC_DIR.exists():
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # Serve specific static files at root level (favicon, manifest, etc.)
        @app.get("/favicon.svg", include_in_schema=False)
        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            fav = STATIC_DIR / "favicon.svg"
            if fav.exists():
                return FileResponse(str(fav), media_type="image/svg+xml")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/icons.svg", include_in_schema=False)
        async def icons_svg():
            f = STATIC_DIR / "icons.svg"
            if f.exists():
                return FileResponse(str(f), media_type="image/svg+xml")
            return JSONResponse({"error": "not found"}, status_code=404)

    # Mount SPA views & catchall last
    app.include_router(views_router)

    return app


app = create_app()
