import logging
import json
from typing import List, Optional
from playwright.async_api import BrowserContext, Page, Error as PlaywrightError
from app.tpo.models import CompanyRecord, APIResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)

API_ENDPOINT = "https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies"

_UNDECODED = object()
# No rejection/authentication status tokens have independent source evidence.
_DOCUMENTED_REJECTION_MARKERS = frozenset()


def _json_type(value):
    """Fixed vocabulary only: never include response keys or values."""
    if value is _UNDECODED:
        return "unavailable"
    return {dict: "object", list: "array", str: "string", bool: "boolean",
            int: "integer", float: "number", type(None): "null"}.get(type(value), "other")


def _status_diagnostic_category(value):
    """Observe only reviewed whole tokens; never return source values."""
    if type(value) is not str:
        return _json_type(value)
    if value in ("success", "Success"):
        return "accepted_success_string"
    if value == "":
        return "empty_string"
    stripped = value.strip()
    if stripped in ("success", "Success"):
        return "success_whitespace_variant"
    if stripped.lower() == "success":
        return ("success_case_and_whitespace_variant" if stripped != value
                else "success_case_variant")
    if value in _DOCUMENTED_REJECTION_MARKERS:
        return "documented_rejection_marker"
    return "other_string"


def _log_rejected_response(response, category, body=_UNDECODED):
    # Content-Type parameters (and arbitrary header values) may contain secrets.
    media_type = getattr(response, "headers", {}).get("content-type", "").split(";", 1)[0].strip().lower()
    content_type = media_type if media_type in (
        "application/json", "text/json", "text/html", "text/plain") else "other_or_missing"
    fields = {key: (_json_type(body[key]) if key in body else "missing")
              for key in ("status", "error", "company_list", "data", "result", "companies", "msg", "message")} if isinstance(body, dict) else {}
    items = body.get("company_list", _UNDECODED) if isinstance(body, dict) else _UNDECODED
    logger.warning(
        "Rejected TPO response: category=%s http_status=%s content_type=%s "
        "json_type=%s fields=%s company_list_length=%s status_category=%s",
        category, response.status, content_type, _json_type(body), fields,
        len(items) if isinstance(items, list) else "unavailable",
        _status_diagnostic_category(body.get("status", _UNDECODED)
                                    if isinstance(body, dict) else _UNDECODED))


def _source_rejection_category(body):
    # Explain the existing guard; this helper does not decide acceptance.
    if not isinstance(body, dict):
        return "non_object"
    if body.get("error"):
        return "application_error"
    if body.get("status") not in (None, "success", "Success", True, 200):
        return "invalid_status"
    if "company_list" not in body:
        return "missing_company_list"
    if not isinstance(body["company_list"], list):
        return "invalid_company_list_type"
    if len(body["company_list"]) > 2000:
        return "oversized_company_list"
    return "invalid_company_identity"


from app.auth.manager import AuthenticationError

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
                _log_rejected_response(response, "session_expired_http")
                logger.warning("API returned 401/403. Session expired.")
                raise AuthenticationError("Session expired (HTTP 401/403)")
                
            if not response.ok:
                _log_rejected_response(response, "http_failure")
                logger.error(f"API returned HTTP {response.status}")
                raise Exception(f"API request failed with status {response.status}")
                
            json_text = await response.text()
            if not json_text:
                _log_rejected_response(response, "empty_response")
                raise Exception("API returned empty response.")
                
            try:
                data = json.loads(json_text)
            except json.JSONDecodeError:
                _log_rejected_response(response, "malformed_json")
                raise Exception("Invalid JSON from API") from None
                
            if isinstance(data, dict) and data.get("error") and not data.get("company_list"):
                _log_rejected_response(response, "application_error", data)
                raise AuthenticationError("API authentication error: application error payload")
                
            try:
                from app.tpo.listing import validate_listing_body, SourceEvidenceError
                validate_listing_body(data)
                api_response = APIResponse(**data)
            except SourceEvidenceError:
                _log_rejected_response(response, _source_rejection_category(data), data)
                raise
            except ValidationError:
                _log_rejected_response(response, "invalid_company_schema", data)
                raise Exception("Unexpected API schema") from None
                
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
            logger.error("Playwright error in fetch_companies.")
            raise
        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("Error in fetch_companies (%s).", type(e).__name__)
            raise
