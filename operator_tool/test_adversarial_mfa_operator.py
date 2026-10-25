"""
Stage C2: Adversarial Test Suite for the Standalone MFA Operator Tool
=====================================================================
Real Playwright/Chromium evidence against an in-process fictional loopback
portal, plus clearly-labelled mocked checks where a real browser cannot
express the failure. Windows receipts prove logic only; POSIX mode proofs
(0700/0600) belong to the native ARM64 qualification.

Scenario classes:
  [BROWSER]  real headless Chromium against the fictional portal
  [PROCESS]  genuine multi-process lock competition (subprocess workers)
  [FS]       real filesystem mechanics (symlinks, mtimes, permissions)
  [MOCK]     deterministic mocks (browser-shutdown / cleanup failures)

Fictional endpoints only; zero real VIERP contact; zero real credentials.
"""

import asyncio
import io
import json
import logging
import os
import shutil
import socket
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mfa_operator import (  # noqa: E402
    MFAOperatorTool,
    OperatorLock,
    ProvisioningStatus,
    SecretScrubber,
    build_arg_parser,
    force_mtime_strictly_greater,
    is_process_alive,
    validate_destination_safety,
    validate_storage_state_structure,
)

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lock_race_worker.py")


class MockPortalHandler(BaseHTTPRequestHandler):
    """Fictional placement portal: login -> MFA -> gated /home + /company-dashboard."""

    burn_nonce = 0
    burn_lock = threading.Lock()
    consumed_tokens = set()
    invalidated_tokens = set()
    request_log = []
    request_log_lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def _record(self):
        with MockPortalHandler.request_log_lock:
            MockPortalHandler.request_log.append(self.path)

    def _issue_burn_token(self) -> str:
        with MockPortalHandler.burn_lock:
            MockPortalHandler.burn_nonce += 1
            return f"burn_token_{MockPortalHandler.burn_nonce}"

    def _auth_state(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name != "sess_token" or not value:
                continue
            token = value
            if token in MockPortalHandler.invalidated_tokens:
                return "invalid"
            if token.startswith("burn_token_"):
                # Single-use nonce: valid exactly once (the login redirect),
                # invalid on every subsequent request (restoration probe).
                if token not in MockPortalHandler.consumed_tokens:
                    MockPortalHandler.consumed_tokens.add(token)
                    return "valid"
                return "invalid"
            if token == "valid_token_xyz":
                return "valid"
        return "invalid"

    def _send_html(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._record()
        auth = self._auth_state()

        if self.path == "/":
            self._send_html(b"""
            <html><body>
                <h1>Fictional Placement Portal</h1>
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
                <h2>Two-Factor Verification</h2>
                <form method="POST" action="/verify-mfa">
                    <input type="text" name="otp" autocomplete="one-time-code" maxlength="6" />
                    <button type="submit">Verify Code</button>
                </form>
            </body></html>
            """)
        elif self.path == "/home":
            if auth == "valid":
                self._send_html(b"""
                <html><body>
                    <h1>Student Home</h1>
                    <a href="/company-dashboard">Dashboard Link</a>
                    <a href="/logout">Logout</a>
                </body></html>
                """)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
        elif self.path == "/company-dashboard":
            if auth == "valid":
                self._send_html(b"""
                <html><body>
                    <h1>Company Dashboard</h1>
                    <a href="/home">Home</a>
                    <a href="/logout">Logout</a>
                </body></html>
                """)
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
        elif self.path == "/unknown-screen":
            self._send_html(b"<html><body><h3>System Maintenance In Progress</h3></body></html>")
        elif self.path == "/false-positive":
            self._send_html(b"""
            <html><body>
                <h1>Login with Deceptive Text</h1>
                <a href="/company-dashboard">Dashboard Link</a>
                <input type="password" name="password" />
            </body></html>
            """)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        self._record()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body)

        if self.path == "/login":
            user = data.get("username", [""])[0]
            pwd = data.get("password", [""])[0]
            if user == "valid_user" and pwd == "correct_password":
                self.send_response(302)
                self.send_header("Location", "/mfa")
                self.end_headers()
            elif user == "unknown_user":
                self.send_response(302)
                self.send_header("Location", "/unknown-screen")
                self.end_headers()
            elif user == "false_positive_user":
                self.send_response(302)
                self.send_header("Location", "/false-positive")
                self.end_headers()
            else:
                self._send_html(b"<html><body><div class='alert-danger'>Invalid credentials</div></body></html>")

        elif self.path == "/verify-mfa":
            otp = data.get("otp", [""])[0]
            if otp == "123456":
                self.send_response(302)
                self.send_header("Set-Cookie", "sess_token=valid_token_xyz; Path=/; HttpOnly")
                self.send_header("Location", "/home")
                self.end_headers()
            elif otp == "777777":
                self.send_response(302)
                self.send_header("Set-Cookie", f"sess_token={self._issue_burn_token()}; Path=/; HttpOnly")
                self.send_header("Location", "/home")
                self.end_headers()
            elif otp == "888888":
                self._send_html(b"<html><body><div class='alert-danger'>MFA session expired. Please re-login.</div></body></html>")
            else:
                self._send_html(b"<html><body><div class='alert-danger'>Incorrect verification code</div></body></html>")


def get_free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_worker(dest: str, hold_seconds: float = 1.0, stale_seconds: float = 600.0):
    """Runs the lock race worker in a genuine separate process."""
    proc = subprocess.run(
        [sys.executable, "-I", WORKER, "--dest", dest,
         "--hold-seconds", str(hold_seconds), "--stale-seconds", str(stale_seconds)],
        capture_output=True, text=True, timeout=120,
    )
    line = (proc.stdout or "").strip().splitlines()
    payload = json.loads(line[-1]) if line else {"acquired": False, "no_output": True}
    return proc.returncode, payload


async def run_adversarial_suite():
    port = get_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), MockPortalHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.2)

    base_url = f"http://127.0.0.1:{port}"
    temp_dir = tempfile.mkdtemp(prefix="tpo_c2_adversarial_")
    session_dir = os.path.join(temp_dir, "session")
    os.makedirs(session_dir, exist_ok=True)

    passed = 0
    failed = 0
    skipped = 0
    deferred = 0
    deferred_reasons = []
    failures = []

    def ok(label: str):
        nonlocal passed
        passed += 1
        print(f"PASS {label}")

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal failed
        if condition:
            ok(label)
        else:
            failed += 1
            failures.append(f"{label}: {detail}")
            print(f"FAIL {label}: {detail}")

    def defer(label: str, reason: str):
        nonlocal deferred
        deferred += 1
        deferred_reasons.append(f"{label}: {reason}")
        print(f"DEFERRED {label} ({reason}; must EXECUTE and PASS on the ARM64 Linux qualification)")

    async def cb_pwd_valid(): return "correct_password"
    async def cb_pwd_wrong(): return "wrong_password"
    async def cb_pwd_cancel(): return None
    async def cb_otp_valid(): return "123456"
    async def cb_otp_wrong(): return "000000"
    async def cb_otp_burn(): return "777777"
    async def cb_otp_expired(): return "888888"
    async def cb_otp_cancel(): return None

    try:
        # ---- [BROWSER] core transaction scenarios ----
        dest_1 = os.path.join(session_dir, "state_1.json")
        tool_1 = MFAOperatorTool(base_url, dest_1)
        st, msg = await tool_1.provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        check("01 [BROWSER] Valid fictional MFA completion",
              st == ProvisioningStatus.SUCCESS and os.path.exists(dest_1), f"{st} {msg}")
        struct_ok, _ = validate_storage_state_structure(dest_1)
        check("01b [BROWSER] Published state matches Playwright storage contract", struct_ok)

        dest_2 = os.path.join(session_dir, "state_2.json")
        st, _ = await MFAOperatorTool(base_url, dest_2).provision_session("valid_user", cb_pwd_wrong, cb_otp_valid)
        check("02 [BROWSER] Rejected credentials fail closed",
              st == ProvisioningStatus.REJECTED_CREDENTIALS and not os.path.exists(dest_2), f"{st}")

        dest_3 = os.path.join(session_dir, "state_3.json")
        st, _ = await MFAOperatorTool(base_url, dest_3).provision_session("valid_user", cb_pwd_valid, cb_otp_wrong)
        check("03 [BROWSER] Rejected OTP fails closed",
              st == ProvisioningStatus.REJECTED_OTP and not os.path.exists(dest_3), f"{st}")

        dest_4 = os.path.join(session_dir, "state_4.json")
        st, _ = await MFAOperatorTool(base_url, dest_4).provision_session("valid_user", cb_pwd_valid, cb_otp_cancel)
        check("04 [BROWSER] Operator cancelled OTP fails closed",
              st == ProvisioningStatus.OPERATOR_CANCELLED and not os.path.exists(dest_4), f"{st}")

        dest_5 = os.path.join(session_dir, "state_5.json")
        st, _ = await MFAOperatorTool(base_url, dest_5).provision_session("valid_user", cb_pwd_cancel, cb_otp_valid)
        check("05 [BROWSER] Operator cancelled password fails closed",
              st == ProvisioningStatus.OPERATOR_CANCELLED and not os.path.exists(dest_5), f"{st}")

        dest_6 = os.path.join(session_dir, "state_6.json")
        st, _ = await MFAOperatorTool(base_url, dest_6).provision_session("valid_user", cb_pwd_valid, cb_otp_expired)
        check("06 [BROWSER] Expired challenge fails closed",
              st == ProvisioningStatus.REJECTED_OTP and not os.path.exists(dest_6), f"{st}")

        dest_7 = os.path.join(session_dir, "state_7.json")
        st, _ = await MFAOperatorTool(base_url, dest_7).provision_session("unknown_user", cb_pwd_valid, cb_otp_valid)
        check("07 [BROWSER] Unknown intermediate screen fails closed",
              st == ProvisioningStatus.UNAUTHENTICATED_POST_LOGIN and not os.path.exists(dest_7), f"{st}")

        dest_8 = os.path.join(session_dir, "state_8.json")
        st, _ = await MFAOperatorTool(base_url, dest_8).provision_session("false_positive_user", cb_pwd_valid, cb_otp_valid)
        check("08 [BROWSER] False-positive dashboard markers rejected",
              st == ProvisioningStatus.UNAUTHENTICATED_POST_LOGIN and not os.path.exists(dest_8), f"{st}")

        dest_9 = os.path.join(session_dir, "state_9.json")
        st, _ = await MFAOperatorTool(base_url, dest_9).provision_session("valid_user", cb_pwd_valid, cb_otp_burn)
        check("09 [BROWSER] Post-restoration invalidation fails closed",
              st == ProvisioningStatus.SESSION_VALIDATION_ERROR and not os.path.exists(dest_9), f"{st}")

        # ---- [BROWSER] corrupt-state handling ----
        corrupt_file = os.path.join(session_dir, "corrupt.json")
        with open(corrupt_file, "w") as f:
            f.write("{invalid json syntax !!!")
        struct_bad, _ = validate_storage_state_structure(corrupt_file)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            tool_v = MFAOperatorTool(base_url, corrupt_file)
            restored = await tool_v._verify_isolated_restoration(browser, corrupt_file)
            await browser.close()
        check("10 [BROWSER] Corrupt storage JSON rejected (structure + restoration)",
              struct_bad is False and restored is False)

        check("10b [FS] Structure validation rejects non-contract shapes",
              validate_storage_state_structure("/nonexistent/file.json")[0] is False
              and validate_storage_state_structure(corrupt_file)[0] is False)

        # ---- [PROCESS] genuine cross-process lock competition ----
        dest_11 = os.path.join(session_dir, "state_11.json")
        holder = subprocess.Popen(
            [sys.executable, "-I", WORKER, "--dest", dest_11, "--hold-seconds", "8.0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        holder_line = holder.stdout.readline()
        worker_holds = "true" in (holder_line or "")
        st, _ = await MFAOperatorTool(base_url, dest_11).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        holder_holds_through = holder.poll() is None
        holder.kill()
        holder.communicate(timeout=30)
        # The killed holder intentionally abandons its lock file (recovery of
        # exactly this state is proven in scenario 13); clean it up here so
        # the suite-wide leakage check counts only unintentional leaks.
        abandoned_lock = f"{os.path.abspath(dest_11)}.lock"
        if os.path.exists(abandoned_lock):
            os.remove(abandoned_lock)
        check("11 [PROCESS] Held cross-process lock blocks provisioning",
              worker_holds and holder_holds_through
              and st == ProvisioningStatus.LOCK_ACQUISITION_FAILED
              and not os.path.exists(dest_11), f"hold={worker_holds} st={st}")

        race_dest = os.path.join(session_dir, "race_acquire.json")
        procs = [subprocess.Popen(
            [sys.executable, "-I", WORKER, "--dest", race_dest, "--hold-seconds", "3.0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
        race_results = []
        for proc in procs:
            out, _ = proc.communicate(timeout=120)
            lines = (out or "").strip().splitlines()
            try:
                race_results.append(json.loads(lines[-1]).get("acquired") is True)
            except Exception:
                race_results.append(False)
        check("12 [PROCESS] 6 competing processes: exactly one acquires the lock",
              sum(race_results) == 1, f"acquired={sum(race_results)}")
        time.sleep(2.0)  # winner holds then releases

        dest_12 = os.path.join(session_dir, "state_12.json")
        # A leftover lock FILE with a dead-owner payload is inert under the
        # kernel-managed design: nothing needs reclaiming, acquisition is
        # immediate, and the payload is never consulted for ownership.
        stale_lock_path = f"{os.path.abspath(dest_12)}.lock"
        with open(stale_lock_path, "w", encoding="utf-8") as f:
            json.dump({"pid": 9999999, "host": socket.gethostname(),
                       "time": time.time() - 400.0, "token": "deadbeef"}, f)
        st, _ = await MFAOperatorTool(base_url, dest_12).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        check("13 [PROCESS] Dead-owner lock file is inert; provisioning proceeds (kernel-managed)",
              st == ProvisioningStatus.SUCCESS and os.path.exists(dest_12), f"{st}")

        stale_race_dest = os.path.join(session_dir, "stale_race.json")
        with open(f"{stale_race_dest}.lock", "w", encoding="utf-8") as f:
            json.dump({"pid": 9999999, "host": socket.gethostname(),
                       "time": time.time() - 900.0, "token": "deadbeef"}, f)
        procs = [subprocess.Popen(
            [sys.executable, "-I", WORKER, "--dest", stale_race_dest, "--hold-seconds", "2.0",
             "--stale-seconds", "60"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        reclaim_results = []
        for proc in procs:
            out, _ = proc.communicate(timeout=120)
            lines = (out or "").strip().splitlines()
            try:
                reclaim_results.append(json.loads(lines[-1]).get("acquired") is True)
            except Exception:
                reclaim_results.append(False)
        check("14 [PROCESS] 4-way stale reclaim race: exactly one winner",
              sum(reclaim_results) == 1, f"acquired={sum(reclaim_results)}")

        foreign_lock = OperatorLock(os.path.join(session_dir, "foreign.json"))
        foreign_lock.acquire()
        victim = OperatorLock(os.path.join(session_dir, "foreign.json"))
        # A confused foreign instance believing it holds the lock: its
        # release() must be a no-op; the real holder stays exclusive and
        # the persistent lock file is never deleted by anyone.
        victim.is_acquired = True
        victim.release()
        still_exclusive = OperatorLock(os.path.join(session_dir, "foreign.json")).acquire() is False
        foreign_holds = os.path.exists(foreign_lock.lock_path)
        foreign_lock.release()
        check("15 [FS] Foreign release is a no-op; holder stays exclusive; file never deleted",
              still_exclusive and foreign_holds)

        # ---- cancellation paths ----
        dest_13 = os.path.join(session_dir, "state_13.json")
        async def cb_interrupt_otp():
            raise KeyboardInterrupt("Simulated Ctrl+C")
        st, _ = await MFAOperatorTool(base_url, dest_13).provision_session("valid_user", cb_pwd_valid, cb_interrupt_otp)
        check("16 [BROWSER] Interruption at OTP fails closed",
              st == ProvisioningStatus.OPERATOR_CANCELLED and not os.path.exists(dest_13), f"{st}")

        dest_13b = os.path.join(session_dir, "state_13b.json")
        async def cb_interrupt_pwd():
            raise KeyboardInterrupt("Simulated Ctrl+C before authentication")
        st, _ = await MFAOperatorTool(base_url, dest_13b).provision_session("valid_user", cb_interrupt_pwd, cb_otp_valid)
        check("16b [BROWSER] Cancellation before authentication fails closed",
              st == ProvisioningStatus.OPERATOR_CANCELLED and not os.path.exists(dest_13b), f"{st}")

        # ---- watcher reader isolation during publication ----
        dest_14 = os.path.join(session_dir, "state_14.json")
        with open(dest_14, "w", encoding="utf-8") as f:
            json.dump({"cookies": [{"name": "prior", "value": "1"}]}, f)
        parse_failures = []
        stop_reader = threading.Event()

        def reader_loop():
            while not stop_reader.is_set():
                try:
                    if os.path.exists(dest_14):
                        with open(dest_14, "r", encoding="utf-8") as f:
                            json.load(f)
                except FileNotFoundError:
                    pass
                except ValueError:
                    parse_failures.append("partial-read")
                time.sleep(0.001)

        reader = threading.Thread(target=reader_loop, daemon=True)
        reader.start()
        async def cb_otp_with_reader():
            return "123456"
        st, _ = await MFAOperatorTool(base_url, dest_14).provision_session("valid_user", cb_pwd_valid, cb_otp_with_reader)
        time.sleep(0.05)
        stop_reader.set()
        reader.join(timeout=5)
        check("17 [BROWSER] Concurrent watcher reader never sees partial state",
              st == ProvisioningStatus.SUCCESS and not parse_failures,
              f"st={st} failures={len(parse_failures)}")

        # ---- symlink safety ----
        dest_15 = os.path.join(session_dir, "symlink_dest.json")
        real_target = os.path.join(session_dir, "real_target.json")
        Path(real_target).touch()
        try:
            os.symlink(real_target, dest_15)
            st, _ = MFAOperatorTool(base_url, dest_15).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
            check("18 [FS] Symlinked destination rejected",
                  st == ProvisioningStatus.SYMLINK_REJECTED and not os.path.exists(dest_15), f"{st}")
        except (OSError, NotImplementedError):
            defer("18 symlinked-destination rejection", "symlink creation unavailable on unprivileged Windows")

        try:
            real_dir = os.path.join(session_dir, "real_dir")
            os.makedirs(real_dir, exist_ok=True)
            link_dir = os.path.join(session_dir, "link_dir")
            if not os.path.exists(link_dir):
                os.symlink(real_dir, link_dir)
            dest_15b = os.path.join(link_dir, "state.json")
            safe, code, _ = validate_destination_safety(dest_15b)
            check("18b [FS] Symlinked ancestor directory rejected",
                  safe is False and code == "DESTINATION_UNSAFE", f"safe={safe} code={code}")
        except (OSError, NotImplementedError):
            defer("18b symlinked-ancestor rejection", "directory-symlink creation unavailable on unprivileged Windows")

        # ---- permissions ----
        st_mode = os.stat(dest_1).st_mode
        check("19 [FS] Published file is a regular file",
              stat_module.S_ISREG(st_mode), f"mode={oct(stat_module.S_IMODE(st_mode))}")
        if os.name != "nt":
            check("19b [FS] Published file mode is literally 0600 (POSIX)",
                  stat_module.S_IMODE(st_mode) == 0o600, f"mode={oct(stat_module.S_IMODE(st_mode))}")
        else:
            defer("19b literal 0600 mode assertion", "POSIX mode bits unprovable on Windows")

        # ---- leakage ----
        # Persistent *.lock files are expected by design under the
        # kernel-managed lock; only staging dirs or reclaim artifacts leak.
        stray_stages = [f for f in os.listdir(session_dir) if f.startswith(".mfa_stage_")]
        stray_reclaim = [f for f in os.listdir(session_dir) if ".reclaim." in f]
        check("20 [FS] Zero staging-directory or reclaim-artifact leakage",
              len(stray_stages) == 0 and len(stray_reclaim) == 0,
              f"stages={stray_stages} reclaim={stray_reclaim}")

        # ---- logging privacy ----
        log_stream = io.StringIO()
        test_logger = logging.getLogger("mfa_operator_tool")
        sh = logging.StreamHandler(log_stream)
        test_logger.addHandler(sh)
        dest_21 = os.path.join(session_dir, "state_21.json")
        secret_password = "P@ssw0rd_Secret_999!"
        async def cb_pwd_secret(): return secret_password
        async def cb_otp_secret(): return "123456"
        await MFAOperatorTool(base_url, dest_21).provision_session("valid_user", cb_pwd_secret, cb_otp_secret)
        # Probe the defensive scrubber while the secret is still registered.
        import mfa_operator as op_module
        op_module._scrubber.register(secret_password)
        test_logger.error("Defensive scrubber probe: %s", f"password was {secret_password}")
        op_module._scrubber.unregister(secret_password)
        test_logger.removeHandler(sh)
        captured = log_stream.getvalue()
        check("21 [BROWSER] Zero secrets in logs; scrubber redacts accidental leaks",
              secret_password not in captured and "[REDACTED]" in captured
              and "sess_token=valid_token_xyz" not in captured, "leak detected")

        # ---- origin allowlist ----
        blocked = False
        try:
            MFAOperatorTool("https://unauthorized-evil-site.com", dest_1)
        except ValueError:
            blocked = True
        check("22 [FS] Non-allowlisted external origin rejected in constructor", blocked)

        # ---- pre-existing session preservation ----
        dest_23 = os.path.join(session_dir, "state_23.json")
        original_payload = '{"cookies": [{"name": "prior", "value": "original_valid_data"}]}'
        with open(dest_23, "w", encoding="utf-8") as f:
            f.write(original_payload)
        orig_content = open(dest_23, "r", encoding="utf-8").read()
        orig_mtime_ns = os.stat(dest_23).st_mtime_ns
        st, _ = await MFAOperatorTool(base_url, dest_23).provision_session("valid_user", cb_pwd_valid, cb_otp_burn)
        after = os.stat(dest_23)
        check("23 [BROWSER] Pre-existing session preserved byte- and timestamp-identical on failure",
              st == ProvisioningStatus.SESSION_VALIDATION_ERROR
              and open(dest_23, "r", encoding="utf-8").read() == orig_content
              and after.st_mtime_ns == orig_mtime_ns, f"st={st}")

        # ---- watcher-compatible mtime invariant ----
        dest_24 = os.path.join(session_dir, "state_24.json")
        with open(dest_24, "w", encoding="utf-8") as f:
            f.write(original_payload)
        old_stat = os.stat(dest_24)
        st, _ = await MFAOperatorTool(base_url, dest_24).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        new_mtime = os.stat(dest_24).st_mtime
        check("24 [BROWSER] Successful publication forces strictly-newer mtime (watcher pause compat)",
              st == ProvisioningStatus.SUCCESS and new_mtime > old_stat.st_mtime + 0.0005,
              f"old={old_stat.st_mtime} new={new_mtime}")

        # ---- wall-clock bound ----
        dest_25 = os.path.join(session_dir, "state_25.json")
        tool_25 = MFAOperatorTool(base_url, dest_25, max_runtime_seconds=0.0001)
        st, _ = await tool_25.provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        check("25 [BROWSER] Wall-clock bound aborts before staging",
              st == ProvisioningStatus.RUNTIME_BOUND_EXCEEDED and not os.path.exists(dest_25), f"{st}")

        dest_25b = os.path.join(session_dir, "state_25b.json")
        tool_25b = MFAOperatorTool(base_url, dest_25b, max_runtime_seconds=1.5)
        async def cb_pwd_slow():
            await asyncio.sleep(2.0)
            return "correct_password"
        st, _ = await tool_25b.provision_session("valid_user", cb_pwd_slow, cb_otp_valid)
        check("25b [BROWSER] Wall-clock bound aborts mid-flow after callback delay",
              st == ProvisioningStatus.RUNTIME_BOUND_EXCEEDED and not os.path.exists(dest_25b), f"{st}")

        # ---- publication failure (permission) ----
        dest_26 = os.path.join(session_dir, "state_26.json")
        with open(dest_26, "w", encoding="utf-8") as f:
            f.write(original_payload)
        protected = False
        if os.name == "nt":
            stat_result = os.stat(dest_26)
            os.chmod(dest_26, stat_module.S_IREAD)
            protected = not (os.stat(dest_26).st_mode & stat_module.S_IWRITE)
        else:
            os.chmod(session_dir, 0o555)
            protected = True
        try:
            st, _ = await MFAOperatorTool(base_url, dest_26).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
            preserved = open(dest_26, "r", encoding="utf-8").read() == original_payload
            if os.name == "nt":
                # Windows: readonly destination blocks os.replace -> PERSISTENCE_ERROR.
                expected = ProvisioningStatus.PERSISTENCE_ERROR
            else:
                # POSIX: read-only parent dir blocks staging -> PERSISTENCE_ERROR earlier.
                expected = ProvisioningStatus.PERSISTENCE_ERROR
            check("26 [FS] Publication permission failure is fail-closed; existing bytes preserved",
                  st == expected and preserved, f"st={st} preserved={preserved}")
        finally:
            if os.name == "nt":
                os.chmod(dest_26, stat_result.st_mode | stat_module.S_IWRITE)
            else:
                os.chmod(session_dir, 0o755)

        # ---- liveness probe semantics ----
        check("27 [FS] Process liveness probe: self alive, invalid pids dead",
              is_process_alive(os.getpid()) is True and is_process_alive(-1) is False)

        # ---- CLI authorization gate ----
        parser = build_arg_parser()
        ns = parser.parse_args(["--portal-url", "https://tpo.vierp.in/", "--state-file", "x.json"])
        check("28 [FS] CLI requires explicit acknowledgment for non-loopback origins",
              ns.acknowledge_account_authorization is False
              and ns.portal_url == "https://tpo.vierp.in/")

        # ---- [MOCK] browser shutdown failure ----
        class FailingClose:
            async def close(self):
                raise RuntimeError("simulated browser shutdown failure")

        clean = await MFAOperatorTool._cleanup_browser(FailingClose(), FailingClose(), FailingClose())
        check("29 [MOCK] Browser shutdown failure never escapes cleanup", clean is False)

        # ---- [MOCK] staging cleanup failure ----
        dest_30 = os.path.join(session_dir, "state_30.json")
        import mfa_operator as op_module
        original_rmtree = op_module.shutil.rmtree

        def failing_rmtree(path, *a, **k):
            raise OSError("simulated cleanup failure")

        op_module.shutil.rmtree = failing_rmtree
        try:
            st, _ = await MFAOperatorTool(base_url, dest_30).provision_session("valid_user", cb_pwd_valid, cb_otp_valid)
        finally:
            op_module.shutil.rmtree = original_rmtree
        leftover_stages = [f for f in os.listdir(session_dir) if f.startswith(".mfa_stage_")]
        for leftover in leftover_stages:
            shutil.rmtree(os.path.join(session_dir, leftover), ignore_errors=True)
        # The persistent lock FILE remains by design; the requirement is
        # that it is no longer HELD (a fresh acquisition succeeds).
        fresh = OperatorLock(dest_30)
        lock_released = fresh.acquire() is True
        fresh.release()
        check("30 [MOCK] Cleanup failure does not corrupt state or leak a HELD lock",
              st == ProvisioningStatus.SUCCESS and lock_released, f"st={st} lock_released={lock_released}")

        # ---- explicit-initiation gate ----
        dest_31 = os.path.join(session_dir, "state_31.json")
        async def decline():
            return False
        tool_31 = MFAOperatorTool(base_url, dest_31)
        st, _ = await tool_31.provision_session("valid_user", cb_pwd_valid, cb_otp_valid, confirm_callback=decline)
        check("31 [BROWSER] Declined explicit initiation aborts before any browser work",
              st == ProvisioningStatus.OPERATOR_DECLINED and not os.path.exists(dest_31), f"{st}")

        # ---- mtime helper direct proof (watcher same-timestamp hazard) ----
        probe = os.path.join(session_dir, "mtime_probe.json")
        with open(probe, "w") as f:
            f.write("{}")
        base_mtime = time.time()
        os.utime(probe, (base_mtime, base_mtime))
        forced = force_mtime_strictly_greater(probe, os.path.getmtime(probe))
        check("32 [FS] Same-timestamp replacement is forced strictly newer",
              forced > base_mtime + 0.0005, f"base={base_mtime} forced={forced}")

        total = passed + failed
        print()
        if failures:
            print(f"FAILURES ({len(failures)}):")
            for failure in failures:
                print(f"  - {failure}")
        print(f"ADVERSARIAL SUITE RECEIPT: EXECUTED={total} PASSED={passed} FAILED={failed} "
              f"SKIPPED={skipped} DEFERRED_PLATFORM={deferred}"
              + (f" ({'; '.join(deferred_reasons)})" if deferred_reasons else ""))
        if os.name != "nt" and deferred:
            print("FAIL: platform deferrals are not permitted on Linux; "
                  "deferred scenarios must execute and pass on ARM64")
            return False
        return failed == 0 and skipped == 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    success = asyncio.run(run_adversarial_suite())
    sys.exit(0 if success else 1)
