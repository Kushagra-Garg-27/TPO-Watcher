import logging
import json
from typing import List, Optional
from playwright.async_api import BrowserContext, Page, Error as PlaywrightError
from app.tpo.models import CompanyRecord, APIResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)

API_ENDPOINT = "https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies"

class AuthenticationError(Exception):
    pass

class TPOClient:
    def __init__(self, context: BrowserContext, page: Optional[Page] = None, *, listing_contract=None, clock=None):
        self.context = context
        self.page = page
        self.listing_contract=listing_contract
        self.clock=clock
        self._listing_body=None

    async def fetch_listing(self):
        from app.tpo.listing import ListingContract,ListingPage,decode_listing_pages
        if not isinstance(self.listing_contract,ListingContract) or not callable(self.clock):
            raise ValueError("approved listing scope and explicit clock required")
        await self.fetch_companies()
        # This browser integration observes one dashboard response. It has no
        # verified pagination/census contract and therefore cannot assert absence.
        return decode_listing_pages([ListingPage(self._listing_body,1,1,
            self.listing_contract.source_namespace,True)],contract=self.listing_contract,
            observed_ms=self.clock(),complete=False)

    async def fetch_companies(self) -> List[CompanyRecord]:
        """
        Fetches the company list using the authenticated browser session.
        Triggers dashboard navigation and captures the signed API response.
        Raises AuthenticationError if session expired.
        Raises Exception if the API call fails or schema is unexpected.
        """
        logger.info(f"Fetching companies via dashboard navigation from {API_ENDPOINT} ...")
        
        page = self.page
        if not page or page.is_closed():
            if self.context.pages:
                page = self.context.pages[0]
            else:
                page = await self.context.new_page()
                await page.goto("https://tpo.vierp.in/home", wait_until="domcontentloaded")
            self.page = page
            
        try:
            # Ensure we start from /home so navigating to /company-dashboard triggers fresh component mount and API call
            if "home" not in page.url:
                logger.info("Navigating to home view before dashboard access...")
                await page.goto("https://tpo.vierp.in/home", wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)

            logger.info("Initiating dashboard navigation and waiting for API response...")
            async with page.expect_response(
                lambda res: res.url.split("?",1)[0] == API_ENDPOINT,
                timeout=35000
            ) as resp_info:
                link = page.locator("a[href='/company-dashboard']").first
                if await link.is_visible():
                    await link.click()
                else:
                    await page.locator("a[href='/company-dashboard']").click(force=True)
                
            response = await resp_info.value
            
            if response.status in (401, 403):
                logger.warning("API returned 401/403. Session expired.")
                raise AuthenticationError("Session expired (HTTP 401/403)")
                
            if not response.ok:
                logger.error(f"API returned HTTP {response.status}")
                raise Exception(f"API request failed with status {response.status}")
                
            json_text = await response.text()
            if not json_text:
                raise Exception("API returned empty response.")
                
            try:
                data = json.loads(json_text)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON response from API.")
                raise Exception(f"Invalid JSON from API: {e}")
                
            if isinstance(data, dict) and data.get("error") and not data.get("company_list"):
                err_msg = data.get("msg") or data.get("error")
                logger.warning(f"API returned error payload: {err_msg}")
                raise AuthenticationError(f"API authentication error: {err_msg}")
                
            try:
                from app.tpo.listing import validate_listing_body
                validate_listing_body(data)
                api_response = APIResponse(**data)
            except ValidationError as e:
                logger.error("API schema validation failed.")
                raise Exception(f"Unexpected API schema: {e}")
                
            self._listing_body=data
            logger.info(f"Successfully fetched {len(api_response.company_list)} companies.")
            try:
                await page.goto("https://tpo.vierp.in/home", wait_until="domcontentloaded")
            except Exception:
                pass
            return api_response.company_list
            
        except PlaywrightError as e:
            if "login" in page.url or page.url.rstrip("/") == "https://tpo.vierp.in" or await page.locator("input[type='password']").count() > 0:
                logger.warning("Redirected to login page. Session expired.")
                raise AuthenticationError("Session expired (redirected to login)")
            logger.error(f"Playwright error in fetch_companies: {e}")
            raise
        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"Error in fetch_companies: {e}")
            raise
