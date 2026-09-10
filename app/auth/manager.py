import os
import json
import logging
from typing import Optional, Dict
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
            logger.info(f"Found existing session state at {self.state_file}. Loading...")
            try:
                self.context = await self.browser.new_context(storage_state=self.state_file)
            except Exception as e:
                logger.warning(f"Failed to load storage state: {e}. Starting fresh context.")
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
        """
        if not self.context or not self.page or self.page.is_closed():
            await self.start()
            
        if not await self.is_authenticated():
            logger.info("Session not authenticated or expired. Performing login...")
            await self.login()
            
        return self.page

    async def is_authenticated(self) -> bool:
        """
        Checks if the current session is authenticated by verifying current URL or visiting home.
        """
        if not self.page or self.page.is_closed():
            return False
            
        current_url = self.page.url
        if "home" in current_url or "company-dashboard" in current_url:
            return True
            
        try:
            logger.info("Checking authentication by navigating to home page...")
            await self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)
            await self.page.wait_for_timeout(2000)
            if "home" in self.page.url or "company-dashboard" in self.page.url:
                return True
            return False
        except Exception as e:
            logger.warning(f"Error checking authentication status: {e}")
            return False

    async def login(self):
        """
        Performs the full login flow on https://tpo.vierp.in/.
        """
        if not self.page or self.page.is_closed():
            self.page = await self.context.new_page()
            
        page = self.page
        try:
            logger.info(f"Navigating to {LOGIN_URL} ...")
            try:
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=20000)
            except Exception:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            
            logger.info("Entering TPO credentials...")
            uname = page.locator("input[type='text'], input[name='username']")
            await uname.wait_for(state="visible", timeout=20000)
            await uname.fill(settings.TPO_USERNAME)
            pwd = page.locator("input[type='password'], input[name='password']")
            await pwd.fill(settings.TPO_PASSWORD)
            
            logger.info("Clicking Login button to trigger authentication and Altcha challenge...")
            login_btn = page.locator("button[type='submit']")
            await login_btn.wait_for(state="visible", timeout=15000)
            await login_btn.click()
            
            logger.info("Waiting for authentication completion...")
            await page.wait_for_url(lambda u: "home" in u or "dashboard" in u, timeout=45000)
            logger.info(f"Authentication successful! Landed at {page.url}")
            
            # Save storage state
            await self.context.storage_state(path=self.state_file)
            logger.info(f"Session state saved to storage at {self.state_file}.")
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            if os.path.exists(self.state_file):
                try:
                    os.remove(self.state_file)
                except Exception:
                    pass
            raise
