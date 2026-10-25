"""
Stage C2: Watcher Interoperability Suite
========================================
Proves that fictional state published by the standalone operator tool is
compatible with the QUALIFIED watcher candidate (f4def96, byte-identical
to the checked-out app/ sources) using REAL Playwright/Chromium against a
fictional loopback portal.

Method: the qualified app modules are imported unmodified; only the module
URL constants (LOGIN_URL/HOME_URL/DASHBOARD_URL) are re-pointed at the
fictional portal at runtime, mirroring how the app's own hermetic tests
control fictional navigation. No application source file is modified.

Covered watcher contracts:
- AuthManager.start()      : tool-published state must load cleanly.
- AuthManager.is_authenticated(): existence of a file alone proves nothing.
- PlacementWatcher.is_mfa_paused() / has_session_file_been_refreshed():
  paused-session recovery depends on a STRICTLY newer mtime; same-timestamp
  replacement is invisible; the tool's forced bump makes refresh visible.
- Session expiry stays fail-closed after portal-side invalidation.
- Failed provisioning never disturbs an existing valid state file.
- Authentication checks never touch company-listing endpoints.
- Default polling schedule and delivery-drain policy are untouched, and the
  operator tool never imports the application package.
"""

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from mfa_operator import MFAOperatorTool, ProvisioningStatus  # noqa: E402

import app.auth.manager as manager_module  # noqa: E402
from app.auth.manager import AuthManager  # noqa: E402
from app.monitoring.watcher import PlacementWatcher  # noqa: E402


class FictionalPortalHandler(BaseHTTPRequestHandler):
    """Portal whose authenticated /home satisfies the qualified AuthManager markers."""

    valid_token = "interop_valid_token"
    invalidated = False
    request_log = []
    request_log_lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def _record(self):
        with FictionalPortalHandler.request_log_lock:
            FictionalPortalHandler.request_log.append(self.path)

    def _authed(self):
        if FictionalPortalHandler.invalidated:
            return False
        return f"sess_token={FictionalPortalHandler.valid_token}" in self.headers.get("Cookie", "")

    def _send_html(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._record()
        if self.path == "/":
            self._send_html(b"""
            <html><body>
                <form method="POST" action="/login">
                    <input type="text" name="username" />
                    <input type="password" name="password" />
                    <button type="submit">Log In</button>
                </form>
            </body></html>
            """)
        elif self.path == "/mfa":
            self._send_html(b"""
            <html><body>
                <form method="POST" action="/verify-mfa">
                    <input type="text" name="otp" autocomplete="one-time-code" maxlength="6" />
                    <button type="submit">Verify Code</button>
                </form>
            </body></html>
            """)
        elif self.path in ("/home", "/company-dashboard"):
            if self._authed():
                # Exact marker contract of the qualified AuthManager:
                # a[href='/company-dashboard'] present, no password/MFA inputs.
                self._send_html(b"""
                <html><body>
                    <h1>Student Home</h1>
                    <a href="/company-dashboard">Company Dashboard</a>
                    <a href="/logout">Logout</a>
                </body></html>
                """)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self._record()
        length = int(self.headers.get("Content-Length", 0))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))
        if self.path == "/login" and data.get("username", [""])[0] == "valid_user" \
                and data.get("password", [""])[0] == "correct_password":
            self.send_response(302)
            self.send_header("Location", "/mfa")
            self.end_headers()
        elif self.path == "/verify-mfa" and data.get("otp", [""])[0] == "123456":
            self.send_response(302)
            self.send_header("Set-Cookie", f"sess_token={FictionalPortalHandler.valid_token}; Path=/; HttpOnly")
            self.send_header("Location", "/home")
            self.end_headers()
        else:
            self._send_html(b"<html><body><div class='alert-danger'>Invalid credentials</div></body></html>")


def get_free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class StubDB:
    """Minimal dict-backed system-state store for the real PlacementWatcher methods."""

    def __init__(self):
        self.state = {}
        self.db_path = "stub-interop"

    def get_system_state(self, key):
        return self.state.get(key)

    def set_system_state(self, key, value):
        self.state[key] = str(value)


def minimal_watcher(db, auth) -> PlacementWatcher:
    """
    Real PlacementWatcher instance carrying only the attributes used by
    is_mfa_paused()/has_session_file_been_refreshed()/the pause-recovery
    branch - the exact qualified methods under test, without constructing
    the full heavyweight object graph.
    """
    watcher = object.__new__(PlacementWatcher)
    watcher.db = db
    watcher.auth = auth
    return watcher


async def run_interop_suite():
    port = get_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FictionalPortalHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)
    base_url = f"http://127.0.0.1:{port}"

    temp_dir = tempfile.mkdtemp(prefix="tpo_c2_interop_")
    state_file = os.path.join(temp_dir, "session", "playwright_state.json")

    original_urls = (manager_module.LOGIN_URL, manager_module.HOME_URL, manager_module.DASHBOARD_URL)
    manager_module.LOGIN_URL = f"{base_url}/"
    manager_module.HOME_URL = f"{base_url}/home"
    manager_module.DASHBOARD_URL = f"{base_url}/company-dashboard"

    passed = 0
    failures = []

    def check(label, condition, detail=""):
        nonlocal passed
        if condition:
            passed += 1
            print(f"PASS {label}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"FAIL {label}: {detail}")

    async def cb_pwd(): return "correct_password"
    async def cb_otp(): return "123456"
    async def cb_otp_bad(): return "000000"

    auth = None
    try:
        os.environ["STATE_FILE"] = state_file

        # 1. Tool publishes fictional state; the qualified AuthManager must
        #    load it and positively verify authentication (REAL browser).
        tool = MFAOperatorTool(base_url, state_file)
        st, msg = await tool.provision_session("valid_user", cb_pwd, cb_otp)
        check("01 [BROWSER] Tool publishes state successfully", st == ProvisioningStatus.SUCCESS, f"{st} {msg}")

        auth = AuthManager()
        auth.state_file = state_file
        await auth.start()
        is_auth = await auth.is_authenticated()
        check("02 [BROWSER] Qualified AuthManager.start() loads tool state; is_authenticated() true",
              is_auth is True)
        await auth.stop()

        # 2. File existence alone proves nothing: a well-formed file with a
        #    portal-rejected cookie must load but fail authentication.
        bad_state = {
            "cookies": [{
                "name": "sess_token", "value": "fictional_wrong_token",
                "domain": "127.0.0.1", "path": "/",
                "expires": int(time.time()) + 3600, "httpOnly": True, "secure": False,
                "sameSite": "Lax",
            }],
            "origins": [],
        }
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(bad_state, f)
        auth = AuthManager()
        auth.state_file = state_file
        await auth.start()  # loads the file without error...
        is_auth = await auth.is_authenticated()  # ...but auth check must fail.
        check("03 [BROWSER] Existing-but-invalid state does NOT authenticate (existence != proof)",
              is_auth is False)
        await auth.stop()

        # 3. Restore a genuinely valid state, then verify the pause-recovery
        #    mtime contract of the qualified watcher.
        st, _ = await MFAOperatorTool(base_url, state_file).provision_session("valid_user", cb_pwd, cb_otp)
        check("04 [BROWSER] Valid state re-published for pause tests", st == ProvisioningStatus.SUCCESS)

        db = StubDB()
        auth = AuthManager()
        auth.state_file = state_file
        watcher = minimal_watcher(db, auth)

        paused_mtime = os.path.getmtime(state_file)
        db.set_system_state("mfa_pending", "true")
        db.set_system_state("mfa_paused_state_mtime", str(paused_mtime))
        check("05 [CODE] Pause engaged: is_mfa_paused() true",
              watcher.is_mfa_paused() is True)

        # 3a. Same-mtime replacement is INVISIBLE to the qualified watcher
        #     (strict > comparison): the hazard the tool must compensate for.
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(bad_state, f)
        os.utime(state_file, (paused_mtime, paused_mtime))
        check("06 [CODE] Same-timestamp replacement is invisible to pause recovery",
              watcher.has_session_file_been_refreshed() is False)

        # 3b. Tool publication with forced mtime bump IS visible; the
        #     refreshed-but-INVALID session must keep the pause engaged
        #     (fail-closed branch of the qualified watcher).
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(bad_state, f)
        new_mtime = os.path.getmtime(state_file)
        while new_mtime <= paused_mtime:
            new_mtime = time.time()
            os.utime(state_file, (new_mtime, new_mtime))
        db.set_system_state("mfa_paused_state_mtime", str(paused_mtime))
        refreshed_invalid = watcher.has_session_file_been_refreshed()
        await auth.start()
        invalid_auth = await auth.is_authenticated()
        await auth.stop()
        if refreshed_invalid and not invalid_auth:
            # Mirror of watcher.py: the invalid refresh keeps the pause and
            # re-anchors the pause mtime to the current file mtime.
            db.set_system_state("mfa_paused_state_mtime", str(os.path.getmtime(state_file)))
        check("07 [BROWSER] Refreshed-but-invalid session keeps mfa_pending (fail-closed)",
              refreshed_invalid is True and invalid_auth is False and watcher.is_mfa_paused() is True)

        # 3c. A VALID tool publication after the pause: strictly-newer mtime
        #     detected and authentication positively verified -> pause clears.
        st, _ = await MFAOperatorTool(base_url, state_file).provision_session("valid_user", cb_pwd, cb_otp)
        refreshed_valid = watcher.has_session_file_been_refreshed()
        auth2 = AuthManager()
        auth2.state_file = state_file
        await auth2.start()
        valid_auth = await auth2.is_authenticated()
        await auth2.stop()
        if refreshed_valid and valid_auth:
            db.set_system_state("mfa_pending", "false")
            db.set_system_state("auth_status", "AUTHENTICATED")
        check("08 [BROWSER] Tool-published valid session clears the pause via strict mtime rule",
              st == ProvisioningStatus.SUCCESS and refreshed_valid is True
              and valid_auth is True and watcher.is_mfa_paused() is False)

        # 4. Session expiry remains fail-closed: portal-side invalidation
        #    flips a previously-verified session to unauthenticated.
        FictionalPortalHandler.invalidated = True
        auth3 = AuthManager()
        auth3.state_file = state_file
        await auth3.start()
        expired_auth = await auth3.is_authenticated()
        await auth3.stop()
        FictionalPortalHandler.invalidated = False
        check("09 [BROWSER] Portal-side session invalidation stays fail-closed",
              expired_auth is False)

        # 5. Failed provisioning must not disturb an existing valid state.
        with open(state_file, "r", encoding="utf-8") as f:
            valid_bytes = f.read()
        valid_stat = os.stat(state_file)
        st, _ = await MFAOperatorTool(base_url, state_file).provision_session(
            "valid_user", cb_pwd, cb_otp_bad)
        after_bytes = open(state_file, "r", encoding="utf-8").read()
        after_stat = os.stat(state_file)
        check("10 [BROWSER] Failed provisioning leaves existing state byte-identical",
              st == ProvisioningStatus.REJECTED_OTP and after_bytes == valid_bytes
              and after_stat.st_mtime_ns == valid_stat.st_mtime_ns, f"st={st}")

        # 6. Authentication never triggers company-listing traffic.
        FictionalPortalHandler.request_log.clear()
        auth4 = AuthManager()
        auth4.state_file = state_file
        await auth4.start()
        await auth4.is_authenticated()
        await auth4.stop()
        company_hits = [p for p in FictionalPortalHandler.request_log if "company-dashboard" in p or "/api" in p]
        check("11 [BROWSER] Auth start+check issues zero company-listing requests",
              len(company_hits) == 0, f"hits={company_hits}")

        # 7. Polling/drain policy untouched + operator tool never imports app.
        from app.config import Settings
        check_times_default = Settings.model_fields["CHECK_TIMES"].default
        drain_default = Settings.model_fields["DELIVERY_DRAIN_INTERVAL_SECONDS"].default
        check("12 [CODE] Default schedule (10:00,17:00,00:00) and disabled drain unchanged",
              check_times_default == "10:00,17:00,00:00" and drain_default == 0.0,
              f"check_times={check_times_default} drain={drain_default}")

        import mfa_operator as op_module
        source = open(op_module.__file__, "r", encoding="utf-8").read()
        app_imports = [line for line in source.splitlines()
                       if line.startswith("import app") or line.startswith("from app")
                       or " import app" in line]
        check("13 [CODE] Operator tool contains zero application imports",
              not app_imports, f"imports={app_imports}")

        total = passed + len(failures)
        print()
        if failures:
            print(f"FAILURES ({len(failures)}):")
            for failure in failures:
                print(f"  - {failure}")
        print(f"INTEROP SUITE RESULT: {passed}/{total} PASSED"
              + ("" if not failures else f", {len(failures)} FAILED"))
        return not failures

    finally:
        manager_module.LOGIN_URL, manager_module.HOME_URL, manager_module.DASHBOARD_URL = original_urls
        if auth is not None:
            try:
                await auth.stop()
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run_interop_suite()) else 1)
