import logging
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Path to the React build output
STATIC_DIR = Path(__file__).parent.parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


class SecureFastAPI(FastAPI):
    """
    Custom FastAPI subclass ensuring pure ASGI security headers and request body
    limiting middlewares wrap the outermost ASGI boundary, including Starlette's ServerErrorMiddleware.
    """
    def build_middleware_stack(self) -> ASGIApp:
        from app.config import settings
        from app.api.middleware import SecurityHeadersMiddleware,RequestBodyLimitMiddleware
        stack = super().build_middleware_stack()
        stack = RequestBodyLimitMiddleware(stack, max_body_bytes=settings.MAX_REQUEST_BODY_BYTES)
        stack = SecurityHeadersMiddleware(stack)
        return stack


def create_app() -> FastAPI:
    from app.config import settings
    from app.api.middleware import RequestBodyTooLargeException
    from app.api.routes_auth import router as auth_router
    from app.api.routes_preferences import router as pref_router
    from app.api.routes_account import router as account_router
    from app.api.routes_opportunities import router as opps_router
    from app.api.web_views import router as views_router
    from app.health.server import router as health_router
    docs_url = "/docs" if settings.ENABLE_DOCS else None
    openapi_url = "/openapi.json" if settings.ENABLE_DOCS else None

    app = SecureFastAPI(
        title="VIT TPO Watcher — Public V1",
        description="Autonomous TPO Opportunity Watcher for the VIT Pune Class of 2028.",
        version="1.0.0",
        docs_url=docs_url,
        openapi_url=openapi_url,
        redoc_url=None
    )

    @app.exception_handler(RequestBodyTooLargeException)
    async def request_body_too_large_handler(request: Request, exc: RequestBodyTooLargeException):
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"}
        )

    # Note on CORS: Per Requirement 15, broad CORS is explicitly omitted.
    # The application serves same-origin forms and secure APIs.

    # Mount API routers first — these take priority over static files
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(pref_router)
    app.include_router(account_router)
    app.include_router(opps_router)

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


def __getattr__(name):
    # Preserve the default legacy app on explicit access, without constructing it
    # merely because the inert factory is imported.
    if name=='app':
        value=create_app();globals()['app']=value;return value
    raise AttributeError(name)


def create_inert_app(policy):
    from contextlib import asynccontextmanager
    from app.monitoring.deployment_policy import InertPolicy,PolicyError,public_listing
    from app.health.server import inert_health
    if type(policy) is not InertPolicy:raise PolicyError('Validated inert policy required')
    public_listing(policy)
    if not INDEX_HTML.is_file() or not (STATIC_DIR/'assets').is_dir():raise PolicyError('Approved static build unavailable')
    @asynccontextmanager
    async def lifespan(application):
        if application.state.inert_active:raise PolicyError('Inert lifecycle already active')
        application.state.inert_active=True
        try:yield
        finally:application.state.inert_active=False
    application=FastAPI(title='TPO Watcher read-only maintenance',docs_url=None,redoc_url=None,
        openapi_url=None,lifespan=lifespan)
    application.state.inert_active=False
    application.state.inert_policy=policy
    safe={'/health','/health/live','/health/ready','/api/v1/opportunities','/api/v1/opportunity-listing'}
    @application.middleware('http')
    async def maintenance(request,call_next):
        path=request.url.path
        if (request.method not in ('GET','HEAD') or path.startswith('/api/') and path not in safe
            or path.startswith('/health') and path not in safe):
            response=JSONResponse({'detail':'Read-only maintenance; operation unavailable'},status_code=503)
        else:response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Referrer-Policy':'no-referrer','Cache-Control':'no-store','X-TPO-Mode':'INERT_REHEARSAL',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'"})
        return response
    def ready():return inert_health(application,policy,INDEX_HTML,STATIC_DIR/'assets')
    @application.get('/health/live')
    def live():return {'live':True,'mode':'INERT_REHEARSAL'}
    @application.get('/health')
    @application.get('/health/ready')
    def health():
        value=ready()
        return JSONResponse(value,status_code=200 if value['read_only_ready'] else 503)
    def listing():
        if not ready()['read_only_ready']:raise PolicyError('Read-only service unavailable')
        return public_listing(policy)
    @application.exception_handler(PolicyError)
    async def unavailable(request,error):return JSONResponse({'detail':'Read-only service unavailable'},status_code=503)
    @application.get('/api/v1/opportunity-listing')
    def opportunities():return listing()
    @application.get('/api/v1/opportunities')
    def compatibility():return listing()['items']
    application.mount('/assets',StaticFiles(directory=str(STATIC_DIR/'assets'),follow_symlink=False),name='assets')
    @application.get('/favicon.svg')
    @application.get('/favicon.ico')
    def favicon():return FileResponse(STATIC_DIR/'favicon.svg')
    @application.get('/icons.svg')
    def icons():return FileResponse(STATIC_DIR/'icons.svg')
    @application.get('/{path:path}')
    def shell(path:str):
        if path.startswith(('api/','health','assets/')):return JSONResponse({'detail':'Not found'},status_code=404)
        return FileResponse(INDEX_HTML)
    return application
