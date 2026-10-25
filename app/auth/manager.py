import os
import json
import time
import logging
from enum import Enum
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse
from playwright.async_api import async_playwright, BrowserContext, Page, Error as PlaywrightError
from app.config import settings

logger = logging.getLogger(__name__)


def get_state_file_path() -> str:
    env_path = os.environ.get("STATE_FILE")
    if env_path:
        return env_path
    if os.path.exists("/app/data"):
        return "/app/data/playwright_state.json"
    return "playwright_state.json"


STATE_FILE = get_state_file_path()
LOGIN_URL = "https://tpo.vierp.in/"
HOME_URL = "https://tpo.vierp.in/home"
DASHBOARD_URL = "https://tpo.vierp.in/company-dashboard"


class AuthState(str, Enum):
    """Explicit, bounded authentication states."""
    AUTHENTICATED = "AUTHENTICATED"
    MFA_ENROLLMENT_REQUIRED = "MFA_ENROLLMENT_REQUIRED"
    MFA_VERIFICATION_REQUIRED = "MFA_VERIFICATION_REQUIRED"
    REAUTHENTICATION_REQUIRED = "REAUTHENTICATION_REQUIRED"
    CREDENTIALS_REJECTED = "CREDENTIALS_REJECTED"
    AUTHENTICATION_TIMEOUT = "AUTHENTICATION_TIMEOUT"
    UNKNOWN_AUTHENTICATION_STATE = "UNKNOWN_AUTHENTICATION_STATE"


class AuthenticationError(Exception):
    """Base exception for authentication failures."""
    pass


class AuthStateError(AuthenticationError):
    """Base exception for explicit typed authentication failures."""
    def __init__(self, state: AuthState, message: str = ""):
        self.state = state
        super().__init__(message or state.value)


class MFAEnrollmentRequiredError(AuthStateError):
    def __init__(self, message: str = "MFA enrollment required on portal"):
        super().__init__(AuthState.MFA_ENROLLMENT_REQUIRED, message)


class MFAVerificationRequiredError(AuthStateError):
    def __init__(self, message: str = "MFA TOTP verification code required on portal"):
        super().__init__(AuthState.MFA_VERIFICATION_REQUIRED, message)


class ReauthenticationRequiredError(AuthStateError):
    def __init__(self, message: str = "Re-authentication required: session expired"):
        super().__init__(AuthState.REAUTHENTICATION_REQUIRED, message)


class CredentialsRejectedError(AuthStateError):
    def __init__(self, message: str = "Portal rejected supplied credentials"):
        super().__init__(AuthState.CREDENTIALS_REJECTED, message)


class AuthenticationTimeoutError(AuthStateError):
    def __init__(self, message: str = "Authentication timed out"):
        super().__init__(AuthState.AUTHENTICATION_TIMEOUT, message)


class UnknownAuthenticationStateError(AuthStateError):
    def __init__(self, message: str = "Unrecognized intermediate authentication state"):
        super().__init__(AuthState.UNKNOWN_AUTHENTICATION_STATE, message)


class AuthManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.state_file = get_state_file_path()

    async def start(self):
        logger.info("Starting Playwright Chromium browser...")
        self.playwright = await async_playwright().start()
        
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=launch_args
        )
        
        if os.path.exists(self.state_file):
            logger.info("Found existing session state at %s. Loading...", self.state_file)
            try:
                self.context = await self.browser.new_context(storage_state=self.state_file)
            except Exception as e:
                logger.warning("Stored session state could not be loaded (%s); starting fresh context.",
                               type(e).__name__)
                self.context = await self.browser.new_context()
        else:
            logger.info("No existing session state found. Starting fresh context...")
            self.context = await self.browser.new_context()
            
        self.context.set_default_timeout(30000)
        self.page = await self.context.new_page()

    async def stop(self):
        if self.page and not self.page.is_closed():
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        logger.info("Playwright stopped cleanly.")

    async def get_valid_context(self) -> BrowserContext:
        """Returns valid authenticated context."""
        await self.get_valid_page()
        return self.context

    async def get_valid_page(self) -> Page:
        """
        Returns a valid authenticated Page.
        If current session is not authenticated, performs login.
        Guarantees that a page is returned ONLY if authentication is established;
        otherwise raises the appropriate AuthStateError.
        """
        if not self.page or self.page.is_closed():
            if self.context:
                self.page = await self.context.new_page()
            else:
                await self.start()
            
        if not await self.is_authenticated():
            logger.info("Session not authenticated or expired. Performing login...")
            state = await self.login()
            if state != AuthState.AUTHENTICATED:
                self._raise_for_state(state)

        if not await self.is_authenticated():
            raise ReauthenticationRequiredError("Authentication could not be verified after login attempt")
            
        return self.page

    @staticmethod
    def _raise_for_state(state: AuthState):
        """Raises specific AuthStateError for non-authenticated states."""
        if state == AuthState.MFA_ENROLLMENT_REQUIRED:
            raise MFAEnrollmentRequiredError()
        elif state == AuthState.MFA_VERIFICATION_REQUIRED:
            raise MFAVerificationRequiredError()
        elif state == AuthState.CREDENTIALS_REJECTED:
            raise CredentialsRejectedError()
        elif state == AuthState.AUTHENTICATION_TIMEOUT:
            raise AuthenticationTimeoutError()
        elif state == AuthState.REAUTHENTICATION_REQUIRED:
            raise ReauthenticationRequiredError()
        elif state == AuthState.UNKNOWN_AUTHENTICATION_STATE:
            raise UnknownAuthenticationStateError()
        else:
            raise UnknownAuthenticationStateError(f"Unexpected authentication state: {state}")

    @staticmethod
    def _parse_and_validate_url(url: str) -> Tuple[bool, str]:
        """
        Validates URL origin and extracts clean path.
        Returns (is_valid_origin, clean_path).
        """
        if not url:
            return False, ""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            # Allow tpo.vierp.in or loopbacks with optional ports for testing
            is_valid_origin = (
                host == "tpo.vierp.in"
                or host == "localhost"
                or host == "127.0.0.1"
                or host.startswith("localhost:")
                or host.startswith("127.0.0.1:")
            )
            path = parsed.path.rstrip("/")
            return is_valid_origin, path
        except Exception:
            return False, ""

    async def _detect_mfa_enrollment(self) -> bool:
        """
        Detects MFA enrollment / authenticator setup challenge.
        Requires high-confidence contextual pairing:
        Text indicating authenticator setup AND (QR element OR TOTP input).
        Avoids broad rules like matching any canvas or generic image.
        """
        if not self.page or self.page.is_closed():
            return False
        try:
            setup_text_loc = self.page.locator(
                "text=/authenticator\\s+setup/i, text=/set\\s*up\\s+authenticator/i, "
                "text=/scan\\s+(the\\s+)?qr/i, text=/configure\\s+authenticator/i, "
                "[data-testid*='mfa-setup' i], [id*='mfa-setup' i]"
            )
            has_setup_text = await setup_text_loc.count() > 0
            if not has_setup_text:
                return False

            qr_or_input_loc = self.page.locator(
                "img[alt*='qr' i], img[src*='qr' i], canvas.qr-code, [data-testid*='qr' i], "
                "input[name*='otp' i], input[name*='totp' i], input[name*='code' i], "
                "input[id*='otp' i], input[id*='totp' i]"
            )
            return await qr_or_input_loc.count() > 0
        except Exception:
            return False

    async def _detect_mfa_verification(self) -> bool:
        """
        Detects MFA verification challenge (TOTP 6-digit code entry).
        Requires specific MFA prompt text OR OTP input without password fields.
        """
        if not self.page or self.page.is_closed():
            return False
        try:
            if await self._detect_mfa_enrollment():
                return False

            verify_text_loc = self.page.locator(
                "text=/enter\\s+(the\\s+)?(verification|authenticator|6-digit|totp)\\s+code/i, "
                "text=/two-factor\\s+authentication/i, text=/2-step\\s+verification/i, "
                "text=/authenticator\\s+code/i, [data-testid*='mfa-verify' i]"
            )
            has_verify_text = await verify_text_loc.count() > 0

            otp_input_loc = self.page.locator(
                "input[autocomplete='one-time-code'], input[name*='otp' i], "
                "input[name*='totp' i], input[name*='code' i], "
                "input[id*='otp' i], input[id*='totp' i], [data-testid*='otp' i]"
            )
            has_otp_input = await otp_input_loc.count() > 0

            pwd_count = await self.page.locator("input[type='password']").count()
            return has_verify_text or (has_otp_input and pwd_count == 0)
        except Exception:
            return False

    async def _detect_credentials_rejected(self) -> bool:
        """
        Detects explicit rejection of credentials by the portal.
        """
        if not self.page or self.page.is_closed():
            return False
        try:
            err_loc = self.page.locator(
                ".alert-danger, .error-message, [role='alert'], "
                "text=/invalid\\s+(username|password|credentials)/i, "
                "text=/incorrect\\s+(username|password|credentials)/i, "
                "text=/user\\s+not\\s+found/i, text=/account\\s+locked/i"
            )
            return await err_loc.count() > 0
        except Exception:
            return False

    async def _has_authenticated_dom_markers(self) -> bool:
        """
        Inspects page DOM for positive proof of an authenticated session
        and absence of login or challenge forms.
        """
        if not self.page or self.page.is_closed():
            return False
        try:
            # 1. A login password field must NOT be present
            if await self.page.locator("input[type='password']").count() > 0:
                return False
                
            # 2. MFA input or setup text must NOT be present
            if await self._detect_mfa_enrollment() or await self._detect_mfa_verification():
                return False
                
            # 3. Must have at least one recognized authenticated element (dashboard link or logout button):
            dashboard_link = await self.page.locator("a[href='/company-dashboard']").count()
            logout_elem = await self.page.locator("a[href*='logout' i], button:has-text('Logout'), a:has-text('Logout')").count()
            
            return (dashboard_link > 0 or logout_elem > 0)
        except Exception:
            return False

    async def classify_current_screen(self) -> AuthState:
        """
        Classifies current screen state into an explicit AuthState.
        """
        if not self.page or self.page.is_closed():
            return AuthState.UNKNOWN_AUTHENTICATION_STATE

        valid_origin, path = self._parse_and_validate_url(self.page.url)

        # 1. Check positive authenticated state
        if valid_origin and path in ("/home", "/company-dashboard"):
            if await self._has_authenticated_dom_markers():
                return AuthState.AUTHENTICATED

        # 2. Check credentials rejection
        if await self._detect_credentials_rejected():
            return AuthState.CREDENTIALS_REJECTED

        # 3. Check MFA challenges
        if await self._detect_mfa_enrollment():
            return AuthState.MFA_ENROLLMENT_REQUIRED
        if await self._detect_mfa_verification():
            return AuthState.MFA_VERIFICATION_REQUIRED

        # 4. Check login / reauthentication screen
        if valid_origin and path in ("", "/", "/login"):
            if await self.page.locator("input[type='password']").count() > 0:
                return AuthState.REAUTHENTICATION_REQUIRED

        return AuthState.UNKNOWN_AUTHENTICATION_STATE

    async def is_authenticated(self) -> bool:
        """
        Checks if current session is authenticated by verifying exact URL path,
        valid origin, and authenticated DOM markers without password/MFA fields.
        """
        if not self.page or self.page.is_closed():
            return False
            
        valid_origin, path = self._parse_and_validate_url(self.page.url)
        if valid_origin and path in ("/home", "/company-dashboard"):
            if await self._has_authenticated_dom_markers():
                return True
                
        try:
            logger.info("Checking authentication by navigating to home page...")
            await self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)
            await self.page.wait_for_timeout(1000)
            
            valid_origin, path = self._parse_and_validate_url(self.page.url)
            if not valid_origin:
                return False
            if path in ("", "/", "/login"):
                return False
            if path in ("/home", "/company-dashboard"):
                return await self._has_authenticated_dom_markers()
            return False
        except Exception as e:
            logger.warning("Authentication status check failed (%s).", type(e).__name__)
            return False

    async def save_storage_state_safe(self):
        """
        Saves Playwright storage state using atomic replacement and protected file permissions.
        Ensures restrictive parent directory permissions (0700) and file permissions (0600)
        without symlink vulnerability or world-readable write windows.
        """
        if not self.context:
            return
        state_dir = os.path.dirname(os.path.abspath(self.state_file))
        if state_dir and not os.path.exists(state_dir):
            os.makedirs(state_dir, mode=0o700, exist_ok=True)
            
        tmp_file = f"{self.state_file}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
        try:
            await self.context.storage_state(path=tmp_file)
            if hasattr(os, "chmod"):
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass
            os.replace(tmp_file, self.state_file)
            if hasattr(os, "chmod"):
                try:
                    os.chmod(self.state_file, 0o600)
                except OSError:
                    pass
            logger.info("Session state saved to storage at %s.", self.state_file)
        except Exception as e:
            logger.error("Failed to save session state safely (%s).", type(e).__name__)
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            raise

    def clear_session_state(self):
        """
        Explicitly removes the persisted session state file.
        Only called on verified session invalidation (HTTP 401/403) or explicit reset.
        """
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
                logger.info("Cleared session state file at %s.", self.state_file)
            except Exception as e:
                logger.warning("Could not remove session state file (%s).", type(e).__name__)

    async def login(self) -> AuthState:
        """
        Performs the login flow on https://tpo.vierp.in/.
        Returns the resulting AuthState.
        Never deletes existing session file on MFA, timeout, or challenge.
        Only saves session state when AUTHENTICATED is confirmed.
        """
        if not self.page or self.page.is_closed():
            if self.context:
                self.page = await self.context.new_page()
            else:
                await self.start()
            
        page = self.page
        try:
            logger.info("Navigating to %s ...", LOGIN_URL)
            try:
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=20000)
            except Exception:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            
            logger.info("Entering TPO credentials...")
            uname = page.locator("input[type='text'], input[name='username']")
            await uname.wait_for(state="visible", timeout=15000)
            await uname.fill(settings.TPO_USERNAME)
            pwd = page.locator("input[type='password'], input[name='password']")
            await pwd.fill(settings.TPO_PASSWORD)
            
            logger.info("Clicking Login button to trigger authentication and Altcha challenge...")
            login_btn = page.locator("button[type='submit']")
            await login_btn.wait_for(state="visible", timeout=10000)
            await login_btn.click()
            
            logger.info("Waiting for authentication completion...")
            final_state = AuthState.UNKNOWN_AUTHENTICATION_STATE
            for _ in range(30):
                await page.wait_for_timeout(500)
                state = await self.classify_current_screen()
                if state == AuthState.AUTHENTICATED:
                    final_state = AuthState.AUTHENTICATED
                    break
                elif state in (
                    AuthState.MFA_ENROLLMENT_REQUIRED,
                    AuthState.MFA_VERIFICATION_REQUIRED,
                    AuthState.CREDENTIALS_REJECTED,
                ):
                    final_state = state
                    break
            else:
                final_state = await self.classify_current_screen()
                if final_state == AuthState.UNKNOWN_AUTHENTICATION_STATE:
                    final_state = AuthState.AUTHENTICATION_TIMEOUT

            if final_state == AuthState.AUTHENTICATED:
                logger.info("Authentication successful; authenticated view reached.")
                await self.save_storage_state_safe()
            elif final_state == AuthState.MFA_ENROLLMENT_REQUIRED:
                logger.warning("MFA enrollment required on portal (authenticator setup detected).")
            elif final_state == AuthState.MFA_VERIFICATION_REQUIRED:
                logger.warning("MFA verification required on portal (TOTP code prompt detected).")
            elif final_state == AuthState.CREDENTIALS_REJECTED:
                logger.error("Portal rejected credentials.")
            elif final_state == AuthState.AUTHENTICATION_TIMEOUT:
                logger.error("Authentication timed out waiting for post-login view.")
            else:
                logger.warning("Unrecognized authentication state post-login.")

            return final_state

        except Exception as e:
            logger.error("Login failed (%s).", type(e).__name__)
            # CRITICAL SAFETY: DO NOT delete self.state_file on exception!
            # Preserves previously persisted authenticated state.
            raise

