import logging
from fastapi import FastAPI
from app.api.routes_auth import router as auth_router
from app.api.routes_preferences import router as pref_router
from app.api.web_views import router as views_router
from app.health.server import router as health_router

logger = logging.getLogger(__name__)

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

    # Mount API routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(pref_router)
    app.include_router(views_router)

    return app

app = create_app()
