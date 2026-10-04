import asyncio
import json
import sqlite3
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.app import app, create_app, SecureFastAPI
from app.api.middleware import (
    RequestBodyLimitMiddleware,
    RequestBodyTooLargeException,
    SecurityHeadersMiddleware,
)
from app.config import settings
from tests.integration.test_security_headers import assert_required_security_headers


def test_normal_signup_unaffected():
    """Legitimate signup payload within limit is not blocked by body-size middleware."""
    client = TestClient(app)
    # Payload is ~200 bytes, well within 65536 limit
    res = client.post("/api/v1/auth/signup", json={
        "email": "test.student2028@vit.edu",
        "branch": "CS",
        "altcha": "mock-test"
    })
    # Body limit must not trigger 413 (it may fail Altcha or validation, but not 413)
    assert res.status_code != 413


def test_request_body_size_limits_exact_boundaries():
    """Verify limit boundary conditions: below limit, exactly at limit, limit + 1."""
    limit = 100
    test_app = create_app()

    # Create a test instance with small limit to test exact boundaries cleanly
    class BoundaryApp(SecureFastAPI):
        def build_middleware_stack(self):
            stack = super(SecureFastAPI, self).build_middleware_stack()
            stack = RequestBodyLimitMiddleware(stack, max_body_bytes=limit)
            stack = SecurityHeadersMiddleware(stack)
            return stack

    b_app = BoundaryApp()
    
    @b_app.exception_handler(RequestBodyTooLargeException)
    async def too_large_handler(request: Request, exc: RequestBodyTooLargeException):
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    business_executed = False

    @b_app.post("/test-boundary")
    async def boundary_endpoint(request: Request):
        nonlocal business_executed
        body = await request.body()
        business_executed = True
        return {"received": len(body)}

    client = TestClient(b_app)

    # 1. Just below limit (99 bytes)
    business_executed = False
    res_below = client.post("/test-boundary", content=b"x" * (limit - 1))
    assert res_below.status_code == 200
    assert res_below.json() == {"received": limit - 1}
    assert business_executed is True

    # 2. Exactly at limit (100 bytes)
    business_executed = False
    res_exact = client.post("/test-boundary", content=b"x" * limit)
    assert res_exact.status_code == 200
    assert res_exact.json() == {"received": limit}
    assert business_executed is True

    # 3. Limit + 1 (101 bytes) -> Rejected
    business_executed = False
    res_plus1 = client.post("/test-boundary", content=b"x" * (limit + 1))
    assert res_plus1.status_code == 413
    assert res_plus1.json() == {"detail": "Request body too large"}
    assert business_executed is False

    # 4. Multi-megabyte body -> Rejected
    business_executed = False
    res_huge = client.post("/test-boundary", content=b"x" * (1024 * 1024 * 2))
    assert res_huge.status_code == 413
    assert res_huge.json() == {"detail": "Request body too large"}
    assert business_executed is False


def test_oversized_with_content_length_rejected():
    """Verify Content-Length header is checked early and rejected."""
    client = TestClient(app)
    # Default limit is 65536; send 70000 bytes
    payload = b"A" * 70000
    res = client.post("/api/v1/auth/signup", content=payload, headers={"content-type": "application/json"})
    assert res.status_code == 413
    assert res.json() == {"detail": "Request body too large"}


def test_oversized_without_content_length_streaming():
    """Verify streaming / chunked request without Content-Length is rejected once limit exceeded."""
    limit = 50
    business_executed = False

    class StreamTestApp(SecureFastAPI):
        def build_middleware_stack(self):
            stack = super(SecureFastAPI, self).build_middleware_stack()
            stack = RequestBodyLimitMiddleware(stack, max_body_bytes=limit)
            stack = SecurityHeadersMiddleware(stack)
            return stack

    st_app = StreamTestApp()

    @st_app.exception_handler(RequestBodyTooLargeException)
    async def too_large_handler(request: Request, exc: RequestBodyTooLargeException):
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})

    @st_app.post("/test-stream")
    async def stream_route(request: Request):
        nonlocal business_executed
        body = await request.body()
        business_executed = True
        return {"received": len(body)}

    async def run_chunked_test():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/test-stream",
            "query_string": b"",
            "headers": [],  # NO content-length header
        }
        sent_messages = []
        async def fake_send(msg):
            sent_messages.append(msg)

        # Stream chunks totaling 80 bytes (exceeding limit of 50)
        chunks = [b"A" * 30, b"B" * 30, b"C" * 20]
        async def fake_receive():
            if chunks:
                c = chunks.pop(0)
                return {"type": "http.request", "body": c, "more_body": bool(chunks)}
            return {"type": "http.request", "body": b"", "more_body": False}

        await st_app(scope, fake_receive, fake_send)
        return sent_messages

    messages = asyncio.run(run_chunked_test())
    start_msg = next((m for m in messages if m["type"] == "http.response.start"), None)
    body_msg = next((m for m in messages if m["type"] == "http.response.body"), None)

    assert start_msg is not None
    assert start_msg["status"] == 413
    assert body_msg is not None
    assert json.loads(body_msg["body"].decode("utf-8")) == {"detail": "Request body too large"}
    assert business_executed is False


def test_security_headers_present_on_413_response():
    """Verify security headers wrapper is outermost and decorates 413 responses."""
    client = TestClient(app)
    payload = b"X" * (settings.MAX_REQUEST_BODY_BYTES + 500)
    res = client.post("/api/v1/auth/signup", content=payload, headers={"content-type": "application/json"})
    assert res.status_code == 413
    assert res.json() == {"detail": "Request body too large"}
    assert_required_security_headers(res.headers)


def test_health_check_database_error_sanitization(monkeypatch):
    """Verify health check returns generic JSON without leaking paths, SQL, or exception text."""
    secret_path = "/var/secret/database/credentials.sqlite3"
    leak_string = f"CRITICAL_INTERNAL_LEAK: unable to open file {secret_path}"

    def mock_connect(*args, **kwargs):
        raise sqlite3.OperationalError(leak_string)

    monkeypatch.setattr(sqlite3, "connect", mock_connect)

    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 503

    # Must match generic shape
    data = res.json()
    assert data == {
        "status": "unhealthy",
        "database": "unreachable"
    }

    # Strict check: the sensitive internal string or path must NOT appear in the response
    assert leak_string not in res.text
    assert secret_path not in res.text
    assert "CRITICAL_INTERNAL_LEAK" not in res.text
    assert "error" not in data
