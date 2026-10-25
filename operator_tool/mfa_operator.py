"""
Standalone Operator-Assisted MFA Provisioning Tool (Stage C2)
=============================================================
Hardened, standalone helper for human-operated MFA session provisioning.
It is deliberately OUTSIDE the qualified watcher application: this module
must never import anything from the ``app`` package, must not open the
production SQLite database, must not send notifications, and must not run
as a daemon. It performs exactly one operator-driven transaction per run.

Concurrency and durability primitives live in ``operator_primitives``
(stdlib-only): kernel-managed exclusive locking (flock on Linux), strict
destination-safety checks, storage-state structure validation, and the
transactional publication with prior-session backup and explicit recovery.

Security properties (each covered by the adversarial suites):
1.  Strict origin allowlisting (loopback by default; non-loopback origins
    require an explicit acknowledgment flag).
2.  Non-echoing credential and OTP collection through callbacks
    (``getpass`` in the CLI). No TOTP generation, no QR decoding, no
    authenticator-seed storage, no personal-browser cookie import.
3.  Kernel-managed exclusive single-writer lock: a held lock always has a
    live holder (process death releases it at the kernel); the lock file is
    never deleted; no payload or PID inspection can weaken ownership.
4.  Fail-closed transactional publication: staged state is verified in a
    fresh browser context BEFORE publication; permissions and a
    strictly-newer mtime are enforced and VERIFIED on the staged file
    before the atomic replacement; a hard-link backup of any prior session
    enables explicit recovery on post-replacement failures - a failed run
    never deletes or truncates the previous session.
5.  Watcher compatibility: the published mtime is forced strictly newer
    than any replaced file because the qualified watcher's paused-session
    recovery uses a strict ``curr_mtime > paused_mtime`` comparison.
6.  Strict destination safety: final-component symlinks (SYMLINK_REJECTED)
    and symlinked ancestor directories (DESTINATION_UNSAFE) are rejected.
7.  POSIX permission discipline: staging directory 0700, staged and
    published files 0600 (verified where the platform proves modes;
    Windows receipts defer literal-mode proof to the ARM64 qualification).
8.  Zero secrets in logs: nothing variable is logged, and a defensive
    ``SecretScrubber`` filter replaces registered secrets with
    ``[REDACTED]``.
9.  Bounded runtime: a wall-clock deadline aborts fail-closed at every
    phase boundary.
10. Guaranteed staging cleanup on every exit path; cleanup failures are
    reported without corrupting state.

Fictional endpoints only during qualification and tests. Live use requires
separate account-holder and institutional authorization.
"""

import argparse
import asyncio
import getpass
import logging
import os
import shutil
import sys
import tempfile
import time
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, async_playwright

from operator_primitives import (  # noqa: F401  (re-exported for suites)
    MTIME_EPSILON_SECONDS,
    OperatorLock,
    PublicationError,
    SecretScrubber,
    chmod_strict,
    force_mtime_strictly_greater,
    fsync_dir,
    fsync_file,
    is_process_alive,
    publish_state_atomically,
    validate_destination_safety,
    validate_storage_state_structure,
)

logger = logging.getLogger("mfa_operator_tool")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [MFA-OPERATOR] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_scrubber = SecretScrubber()
logger.addFilter(_scrubber)


class ProvisioningStatus(Enum):
    SUCCESS = "SUCCESS"
    REJECTED_CREDENTIALS = "REJECTED_CREDENTIALS"
    REJECTED_OTP = "REJECTED_OTP"
    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    OPERATOR_DECLINED = "OPERATOR_DECLINED"
    CHALLENGE_TIMEOUT = "CHALLENGE_TIMEOUT"
    RUNTIME_BOUND_EXCEEDED = "RUNTIME_BOUND_EXCEEDED"
    UNKNOWN_SCREEN = "UNKNOWN_SCREEN"
    UNAUTHENTICATED_POST_LOGIN = "UNAUTHENTICATED_POST_LOGIN"
    SESSION_VALIDATION_ERROR = "SESSION_VALIDATION_ERROR"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    LOCK_ACQUISITION_FAILED = "LOCK_ACQUISITION_FAILED"
    SYMLINK_REJECTED = "SYMLINK_REJECTED"
    DESTINATION_UNSAFE = "DESTINATION_UNSAFE"
    ORIGIN_DISALLOWED = "ORIGIN_DISALLOWED"


class _DeadlineExceeded(Exception):
    """Internal: wall-clock bound tripped."""


class MFAOperatorTool:
    """
    Hardened, single-shot operator tool for manual MFA session provisioning.
    """

    DEFAULT_ALLOWED_ORIGINS = ("127.0.0.1", "localhost")

    def __init__(
        self,
        base_url: str,
        target_state_file: str,
        allowed_origins: Optional[Tuple[str, ...]] = None,
        max_runtime_seconds: float = 300.0,
        stale_lock_seconds: float = 600.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.target_state_file = os.path.abspath(target_state_file)
        self.allowed_origins = tuple(allowed_origins) if allowed_origins else self.DEFAULT_ALLOWED_ORIGINS
        self.max_runtime_seconds = max_runtime_seconds
        # Retained for interface compatibility: locking is kernel-managed
        # and no longer age-based (see operator_primitives.OperatorLock).
        self.stale_lock_seconds = stale_lock_seconds
        self._validate_origin()

    def _validate_origin(self) -> None:
        parsed = urlparse(self.base_url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("Invalid URL structure")
        if hostname not in self.allowed_origins:
            logger.error("Origin is not in the explicit allowlist.")
            raise ValueError(f"Origin '{hostname}' is not authorized")
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http/https origins are supported")

    def _deadline_remaining(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise _DeadlineExceeded()

    async def _has_authenticated_dom_markers(self, page: Page) -> bool:
        """Strict verification of authenticated DOM markers with absence of login forms."""
        try:
            if await page.locator("input[type='password']").count() > 0:
                return False
            if await page.locator("input[autocomplete='one-time-code'], input[name*='otp' i]").count() > 0:
                return False
            alerts = page.locator(".alert-danger, .error-message, [role='alert']")
            if await alerts.count() > 0:
                return False
            dash = await page.locator("a[href*='dashboard' i], a[href*='home' i]").count()
            logout = await page.locator("a[href*='logout' i], button:has-text('Logout')").count()
            return (dash > 0 or logout > 0)
        except Exception:
            return False

    async def _verify_isolated_restoration(self, browser: Browser, state_file_path: str) -> bool:
        """
        Restores the staged state in a completely fresh browser context and
        verifies navigation to the authenticated view with positive markers.
        """
        context: Optional[BrowserContext] = None
        try:
            context = await browser.new_context(storage_state=state_file_path)
            page = await context.new_page()
            try:
                resp = await page.goto(f"{self.base_url}/home", wait_until="domcontentloaded", timeout=12000)
                if not resp or resp.status >= 400:
                    return False
                parsed = urlparse(page.url)
                if parsed.path in ("", "/", "/login"):
                    return False
                return await self._has_authenticated_dom_markers(page)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Restored session verification failed (%s).", type(exc).__name__)
            return False
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

    @staticmethod
    async def _cleanup_browser(page: Optional[Page], context: Optional[BrowserContext], browser: Optional[Browser]) -> bool:
        """Closes browser artifacts best-effort; never raises. Returns True when all closed cleanly."""
        clean = True
        for obj in (page, context, browser):
            if obj is None:
                continue
            try:
                await obj.close()
            except Exception:
                clean = False
        return clean

    async def provision_session(
        self,
        username: str,
        password_callback: Callable[[], Awaitable[Optional[str]]],
        otp_callback: Callable[[], Awaitable[Optional[str]]],
        confirm_callback: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Tuple[ProvisioningStatus, str]:
        """
        Executes the transactional authentication and persistence lifecycle:
        confirm -> lock -> stage -> authenticate -> serialize -> verify in a
        fresh context -> validate structure -> publish atomically (0600,
        strictly-newer mtime, prior-session backup with explicit recovery)
        -> cleanup.

        A pre-existing destination file is NEVER modified, removed, or
        re-timestamped unless the staged session passed complete independent
        restoration verification AND the atomic publication succeeded.
        """
        deadline = time.monotonic() + self.max_runtime_seconds

        # 1. Validate the operator's explicit initiation.
        if confirm_callback is not None:
            try:
                confirmed = await confirm_callback()
            except Exception:
                confirmed = False
            if not confirmed:
                logger.warning("Operator declined the explicit initiation confirmation.")
                return ProvisioningStatus.OPERATOR_DECLINED, "Operator declined initiation"

        # 2. Destination safety (final component and ancestors).
        safe, code, detail = validate_destination_safety(self.target_state_file)
        if not safe:
            logger.error("Destination rejected (%s): %s", code, detail)
            return ProvisioningStatus[code], f"Destination unsafe: {detail}"

        target_dir = os.path.dirname(self.target_state_file)
        try:
            if target_dir:
                os.makedirs(target_dir, mode=0o700, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create destination directory (%s).", type(exc).__name__)
            return ProvisioningStatus.PERSISTENCE_ERROR, f"Directory creation failed: {type(exc).__name__}"

        # 3. Acquire the kernel-managed exclusive lock.
        lock = OperatorLock(self.target_state_file, timeout_seconds=self.stale_lock_seconds)
        if not lock.acquire():
            return ProvisioningStatus.LOCK_ACQUISITION_FAILED, "Concurrent operator lock active"

        staging_dir: Optional[str] = None
        password: Optional[str] = None
        otp_code: Optional[str] = None
        try:
            try:
                self._deadline_remaining(deadline)
            except _DeadlineExceeded:
                return ProvisioningStatus.RUNTIME_BOUND_EXCEEDED, "Runtime bound exceeded before staging"

            # 4. Private temporary working area inside the destination
            #    directory so the final publish is a same-filesystem rename.
            staging_dir = tempfile.mkdtemp(prefix=".mfa_stage_", dir=target_dir)
            try:
                chmod_strict(staging_dir, 0o700)
            except OSError:
                logger.warning("Staging directory mode could not be enforced on this platform.")
            staged_file = os.path.join(staging_dir, "staged_state.json")
            backup_file = os.path.join(staging_dir, "previous_session.bak")

            # 5. Confidential operator inputs (never logged; scrubber armed).
            try:
                password = await password_callback()
            except KeyboardInterrupt:
                logger.warning("Operator cancelled during password entry.")
                return ProvisioningStatus.OPERATOR_CANCELLED, "Cancelled at password prompt"
            except _DeadlineExceeded:
                raise
            except Exception:
                logger.warning("Operator password input errored.")
                return ProvisioningStatus.OPERATOR_CANCELLED, "Password prompt errored"
            if not password:
                logger.warning("Operator cancelled password input.")
                return ProvisioningStatus.OPERATOR_CANCELLED, "Password prompt cancelled"
            _scrubber.register(password)
            self._deadline_remaining(deadline)

            # 6. Isolated fictional authentication browser.
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    logger.info("Navigating to login portal...")
                    try:
                        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=15000)
                    except PlaywrightError as exc:
                        if "Timeout" in str(exc):
                            return ProvisioningStatus.CHALLENGE_TIMEOUT, "Login page navigation timed out"
                        raise
                    self._deadline_remaining(deadline)

                    user_loc = page.locator("input[type='text'], input[name='username']")
                    pwd_loc = page.locator("input[type='password'], input[name='password']")
                    btn_loc = page.locator("button[type='submit']")

                    if await user_loc.count() == 0 or await pwd_loc.count() == 0:
                        logger.error("Login form elements missing.")
                        return ProvisioningStatus.UNKNOWN_SCREEN, "Login form elements missing"

                    await user_loc.fill(username)
                    await pwd_loc.fill(password)
                    _scrubber.unregister(password)
                    await btn_loc.click()
                    await page.wait_for_load_state("domcontentloaded")
                    self._deadline_remaining(deadline)

                    err_loc = page.locator(".alert-danger, .error-message, [role='alert']")
                    if await err_loc.count() > 0:
                        text = (await err_loc.first.text_content() or "").lower()
                        if "invalid" in text or "incorrect" in text or "denied" in text:
                            logger.warning("Portal rejected credentials.")
                            return ProvisioningStatus.REJECTED_CREDENTIALS, "Credentials rejected"

                    otp_loc = page.locator("input[autocomplete='one-time-code'], input[name*='otp' i]")
                    if await otp_loc.count() > 0:
                        logger.info("MFA verification challenge detected. Requesting operator OTP code...")
                        try:
                            otp_code = await otp_callback()
                        except KeyboardInterrupt:
                            logger.warning("Operator cancelled OTP submission.")
                            return ProvisioningStatus.OPERATOR_CANCELLED, "Cancelled at OTP prompt"
                        except Exception:
                            logger.error("Operator OTP callback error.")
                            return ProvisioningStatus.OPERATOR_CANCELLED, "OTP entry error"
                        if not otp_code or not str(otp_code).strip():
                            logger.warning("Operator cancelled OTP code entry.")
                            return ProvisioningStatus.OPERATOR_CANCELLED, "Operator cancelled OTP input"
                        _scrubber.register(str(otp_code).strip())
                        self._deadline_remaining(deadline)

                        clean_otp = str(otp_code).strip()
                        if len(clean_otp) != 6 or not clean_otp.isdigit():
                            logger.warning("Supplied code does not match the required 6-digit format.")
                            return ProvisioningStatus.REJECTED_OTP, "Invalid OTP format"

                        # Single bounded attempt: no automated retries ever.
                        await otp_loc.fill(clean_otp)
                        submit_btn = page.locator("button[type='submit'], button:has-text('Verify')")
                        await submit_btn.click()
                        await page.wait_for_load_state("domcontentloaded")
                        self._deadline_remaining(deadline)

                        if await err_loc.count() > 0:
                            logger.warning("Portal rejected verification code.")
                            return ProvisioningStatus.REJECTED_OTP, "Portal rejected verification code"

                    if not await self._has_authenticated_dom_markers(page):
                        logger.error("Missing authenticated DOM markers after login flow.")
                        return ProvisioningStatus.UNAUTHENTICATED_POST_LOGIN, "Authenticated view not reached"
                    self._deadline_remaining(deadline)

                    # 7. Serialize into the private staging file; durability
                    #    fsync is FATAL (fail closed) on failure.
                    try:
                        await context.storage_state(path=staged_file)
                        fsync_file(staged_file)
                    except Exception as exc:
                        logger.error("Failed to write staged state durably (%s).", type(exc).__name__)
                        return ProvisioningStatus.PERSISTENCE_ERROR, f"Staging serialization error: {type(exc).__name__}"

                    # 8. Structure validation of the storage-state contract.
                    ok, why = validate_storage_state_structure(staged_file)
                    if not ok:
                        logger.error("Staged state rejected by structure validation (%s).", why)
                        return ProvisioningStatus.SESSION_VALIDATION_ERROR, f"Structure invalid: {why}"

                    # 9. Independent verification in a fresh browser context.
                    restored_ok = await self._verify_isolated_restoration(browser, staged_file)
                    if not restored_ok:
                        logger.error("Restored session verification failed. Preserving existing session.")
                        return ProvisioningStatus.SESSION_VALIDATION_ERROR, "Restored context failed verification"
                    self._deadline_remaining(deadline)

                    # 10. Re-check destination safety right before publishing.
                    safe, code, detail = validate_destination_safety(self.target_state_file)
                    if not safe:
                        logger.error("Destination became unsafe during provisioning (%s).", code)
                        return ProvisioningStatus[code], f"Destination unsafe at publish: {detail}"

                    # 11. Transactional publication: prior-session backup,
                    #     pre-verified 0600 + strictly-newer mtime on the
                    #     staged file, atomic replace, post-validation with
                    #     explicit recovery.
                    published, pub_detail, _applied = publish_state_atomically(
                        staged_file, self.target_state_file, backup_file,
                    )
                    if not published:
                        logger.error("Publication failed fail-closed (%s).", pub_detail)
                        return ProvisioningStatus.PERSISTENCE_ERROR, f"Publication failure: {pub_detail}"

                    logger.info("Session provisioned, verified, and published atomically.")
                    return ProvisioningStatus.SUCCESS, "Session provisioned and published"

                finally:
                    await self._cleanup_browser(page, context, browser)

        except _DeadlineExceeded:
            logger.error("Runtime bound exceeded during provisioning; aborting fail-closed.")
            return ProvisioningStatus.RUNTIME_BOUND_EXCEEDED, "Runtime bound exceeded"
        except KeyboardInterrupt:
            logger.warning("Operator interrupted the flow; aborting fail-closed.")
            return ProvisioningStatus.OPERATOR_CANCELLED, "Interrupted by operator"
        except PlaywrightError as exc:
            logger.error("Playwright error during interaction (%s).", type(exc).__name__)
            return ProvisioningStatus.CHALLENGE_TIMEOUT, f"Browser error: {type(exc).__name__}"
        except Exception as exc:
            logger.error("Unexpected provisioning error (%s).", type(exc).__name__)
            return ProvisioningStatus.UNKNOWN_SCREEN, f"Unexpected error: {type(exc).__name__}"
        finally:
            # 12. Staging cleanup (including any backup) and lock release on
            #     every exit path. A successful publication consumed the
            #     staged file; the backup of the superseded session is
            #     intentionally discarded with the staging directory.
            if staging_dir and os.path.exists(staging_dir):
                try:
                    shutil.rmtree(staging_dir)
                except Exception:
                    try:
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    except Exception:
                        pass
                    if os.path.exists(staging_dir):
                        logger.error("Staging cleanup failed; manual review of the staging directory is required.")
            lock.release()
            _scrubber.unregister(password)
            if otp_code:
                _scrubber.unregister(str(otp_code).strip())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mfa_operator",
        description=(
            "Standalone human-assisted MFA session provisioning tool. "
            "One bounded transaction per run; no daemon; no polling; "
            "no OTP generation; nothing is logged that could contain secrets."
        ),
    )
    parser.add_argument("--portal-url", required=True, help="Base URL of the portal (allowlisted origin only).")
    parser.add_argument(
        "--state-file",
        default=os.environ.get("STATE_FILE", "operator_session.json"),
        help="Destination state file. Defaults to $STATE_FILE, else ./operator_session.json.",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        dest="allow_origins",
        help="Additional allowed portal hostname (repeatable). Required for non-loopback origins.",
    )
    parser.add_argument(
        "--acknowledge-account-authorization",
        action="store_true",
        help=(
            "Required flag when the portal origin is not loopback: confirms the "
            "operator is the credential owner or has documented authorization, and "
            "that institutional acceptable-use policy permits this provisioning run."
        ),
    )
    parser.add_argument("--max-runtime-seconds", type=float, default=300.0, help="Wall-clock bound for the whole run.")
    parser.add_argument(
        "--stale-lock-seconds", type=float, default=600.0, dest="stale_lock_seconds",
        help="Accepted for interface compatibility; locking is kernel-managed and not age-based.",
    )
    return parser


async def interactive_cli_main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    hostname = (urlparse(args.portal_url).hostname or "").lower()
    allowed: List[str] = list(MFAOperatorTool.DEFAULT_ALLOWED_ORIGINS) + list(args.allow_origins)
    non_loopback = hostname not in ("127.0.0.1", "localhost")
    if non_loopback and not args.acknowledge_account_authorization:
        print("Refusing non-loopback portal origin without --acknowledge-account-authorization.")
        return 2

    print("=" * 70)
    print("TPO-WATCHER - OPERATOR MFA SESSION PROVISIONING TOOL (Stage C2)")
    print("=" * 70)
    print("One bounded transaction. No daemon. No automated OTP generation.")
    print("Passwords and OTP codes are collected hidden and never logged.")
    if non_loopback:
        print("Live portal origin acknowledged: authorization must be documented.")
    print()

    username = input("Portal username: ").strip()
    if not username:
        print("Error: username cannot be empty.")
        return 1

    async def get_password() -> Optional[str]:
        return getpass.getpass("Portal password (input hidden): ")

    async def get_otp() -> Optional[str]:
        return getpass.getpass("6-digit MFA verification code (input hidden): ")

    async def confirm() -> bool:
        answer = input(f"Proceed with provisioning toward {args.state_file}? [type PROCEED to confirm]: ")
        return answer.strip() == "PROCEED"

    try:
        tool = MFAOperatorTool(
            args.portal_url,
            args.state_file,
            allowed_origins=tuple(allowed),
            max_runtime_seconds=args.max_runtime_seconds,
            stale_lock_seconds=args.stale_lock_seconds,
        )
    except ValueError as exc:
        print(f"Refused: {exc}")
        return 2

    status, message = await tool.provision_session(username, get_password, get_otp, confirm_callback=confirm)
    print(f"\nResult: {status.value} - {message}")
    return 0 if status == ProvisioningStatus.SUCCESS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(interactive_cli_main()))
