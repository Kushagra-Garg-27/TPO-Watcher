import socket
import re
import threading
import urllib.request
import pytest
from fastapi.testclient import TestClient
import uvicorn
from starlette.responses import JSONResponse

from app.api.app import app, create_app
from app.config import settings
from app.main import get_uvicorn_config


def assert_required_security_headers(headers):
    # 1. nosniff
    assert headers.get("x-content-type-options") == "nosniff"

    # 2. X-Frame-Options DENY
    assert headers.get("x-frame-options") == "DENY"

    # 3. Referrer-Policy no-referrer
    assert headers.get("referrer-policy") == "no-referrer"

    # 4. Strict-Transport-Security: max-age=<configured>, no includeSubDomains, no preload
    hsts = headers.get("strict-transport-security")
    assert hsts is not None
    assert f"max-age={settings.HSTS_MAX_AGE}" in hsts
    assert "includeSubDomains" not in hsts
    assert "preload" not in hsts

    # 5. Permissions-Policy
    perm = headers.get("permissions-policy")
    assert perm is not None
    assert "camera=()" in perm
    assert "microphone=()" in perm
    assert "geolocation=()" in perm
    assert "payment=()" in perm
    assert "usb=()" in perm

    # 6. Cross-Origin-Opener-Policy
    assert headers.get("cross-origin-opener-policy") == "same-origin"

    # 7. Content-Security-Policy-Report-Only
    csp = headers.get("content-security-policy-report-only")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-eval" not in csp
    assert "unsafe-inline" not in csp


def test_security_headers_on_root_spa_shell():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert_required_security_headers(res.headers)
    assert res.headers.get("cache-control") == "no-cache, must-revalidate"


def test_cache_control_spa_shell_policy():
    client = TestClient(app)

    # 1. Ordinary SPA shell routes must return no-cache, must-revalidate
    for path in ["/", "/signup", "/unknown-spa-client-route"]:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-cache, must-revalidate", f"Failed on {path}"

    # 2. Sensitive / token / account SPA shells must remain no-store
    for path in ["/verify", "/unsubscribe", "/preferences"]:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store", f"Failed on {path}"

    # 3. Hashed assets and static files must not have their cache policy overwritten
    stylesheet_paths = re.findall(r'href="(/assets/[^"\s]+\.css)"', client.get('/').text)
    assert stylesheet_paths, 'Built SPA must reference its actual hashed stylesheet'
    for asset_path in ["/favicon.svg", *stylesheet_paths]:
        res = client.get(asset_path)
        assert res.status_code == 200
        assert res.headers.get("cache-control") != "no-cache, must-revalidate", f"Failed on {asset_path}"
        assert res.headers.get("cache-control") != "no-store", f"Failed on {asset_path}"


def test_security_headers_on_api_opportunities():
    client = TestClient(app)
    res = client.get("/api/v1/opportunities")
    assert res.status_code == 200
    assert_required_security_headers(res.headers)


def test_account_and_operator_responses_are_never_cacheable():
    client = TestClient(app)
    for path in ('/api/v1/account', '/api/v1/admin/accounts/1/review', '/account/destination/confirm'):
        response = client.get(path)
        assert response.headers.get('cache-control') == 'no-store', path


def test_security_headers_on_static_asset():
    client = TestClient(app)
    res = client.get("/favicon.svg")
    assert res.status_code == 200
    assert_required_security_headers(res.headers)
    assert res.headers.get("cache-control") != "no-store"


def test_security_headers_on_api_404():
    client = TestClient(app)
    res = client.get("/api/v1/nonexistent-endpoint")
    assert res.status_code == 404
    assert_required_security_headers(res.headers)


def test_security_headers_on_api_405():
    client = TestClient(app)
    # POST to /api/v1/opportunities which only accepts GET
    res = client.post("/api/v1/opportunities")
    assert res.status_code == 405
    assert_required_security_headers(res.headers)


def test_security_headers_on_api_422():
    client = TestClient(app)
    # POST invalid JSON to /api/v1/auth/signup
    res = client.post("/api/v1/auth/signup", json={"bad_field": 123})
    assert res.status_code == 422
    assert_required_security_headers(res.headers)


def test_security_headers_on_forced_unhandled_500():
    test_app = create_app()

    async def crash(request):
        raise RuntimeError("Deliberate unhandled exception for security header test")

    from starlette.routing import Route
    test_app.router.routes.insert(0, Route("/test-forced-crash", crash))

    client = TestClient(test_app, raise_server_exceptions=False)
    res = client.get("/test-forced-crash")
    assert res.status_code == 500
    assert_required_security_headers(res.headers)


def test_cache_control_no_store_on_sensitive_routes():
    client = TestClient(app)

    # Auth routes
    res = client.get("/api/v1/auth/verify?token=invalid")
    assert res.headers.get("cache-control") == "no-store"

    # Preferences routes
    res = client.get("/api/v1/preferences/request?token=invalid")
    assert res.headers.get("cache-control") == "no-store"

    # Unsubscribe routes
    res = client.get("/api/v1/unsubscribe?token=invalid")
    assert res.headers.get("cache-control") == "no-store"

    # SPA confirmation pages
    for path in ["/verify", "/unsubscribe", "/preferences"]:
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"


def test_docs_gating_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DOCS", False)
    test_app = create_app()
    client = TestClient(test_app)

    # /docs must return 404 JSON, not 200 HTML
    res_docs = client.get("/docs")
    assert res_docs.status_code == 404
    assert res_docs.headers["content-type"].startswith("application/json")
    assert res_docs.json() == {"detail": "Not found"}

    # /openapi.json must return 404 JSON, not 200 HTML
    res_openapi = client.get("/openapi.json")
    assert res_openapi.status_code == 404
    assert res_openapi.headers["content-type"].startswith("application/json")
    assert res_openapi.json() == {"detail": "Not found"}


def test_docs_gating_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DOCS", True)
    test_app = create_app()
    client = TestClient(test_app)

    res_docs = client.get("/docs")
    assert res_docs.status_code == 200

    res_openapi = client.get("/openapi.json")
    assert res_openapi.status_code == 200
    assert "paths" in res_openapi.json()


def test_uvicorn_config_server_header_disabled():
    config = get_uvicorn_config()
    assert config.server_header is False


def test_uvicorn_live_server_header_absent():
    """Verify Server header is omitted over the wire via temporary localhost uvicorn."""
    test_app = create_app()

    ready = threading.Event()
    server_errors = []

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                ready.set()

    # Keep the exact owned socket alive through startup, HTTP, and shutdown.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        config = uvicorn.Config(
            app=test_app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=2,
        )
        server = ReadyServer(config)

        def run_server():
            try:
                server.run(sockets=[listener])
            except BaseException as exc:
                server_errors.append(exc)
            finally:
                ready.set()  # Also wake the bounded wait if startup fails.

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        try:
            assert ready.wait(timeout=5.0), "Localhost uvicorn startup timed out"
            if server_errors:
                raise server_errors[0]
            assert server.started and thread.is_alive(), "Localhost uvicorn failed to start"
            # This request uses only the owned endpoint, without ambient proxies.
            url = f"http://127.0.0.1:{port}/api/v1/opportunities"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=2.0) as resp:
                assert resp.status == 200
                headers = dict(resp.headers)
                server_hdr = headers.get("Server", headers.get("server"))
                assert server_hdr is None or "uvicorn" not in server_hdr.lower()
        finally:
            server.should_exit = True
            thread.join(timeout=5.0)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=2.0)
            assert not thread.is_alive(), "Localhost uvicorn thread failed to stop"
        if server_errors:
            raise server_errors[0]
