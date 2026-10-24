"""Playwright browser verification suite for the Homepage Opportunities Visibility feature.

Tests all four rendering cases (A, B, C, D) directly against the built production frontend assets:
- Case B: Production historical fallback (5 stored records, unverified freshness)
- Case A: Fresh verified listings (active observation window, VERIFIED status)
- Case C: Empty database (0 historical, 0 verified)
- Case D: API failure (503 / network error)
- Mobile responsiveness (375x667)
- Console and hydration error detection
"""
import http.server
import json
import socketserver
import threading
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "app" / "static"
PREVIEWS_DIR = REPO_ROOT / "reports" / "previews"
ARTIFACTS_DIR = Path(r"C:\Users\kusha\.gemini\antigravity-ide\brain\ed0529c1-1961-47ab-b6a8-15c5bd2fbc31")
PORT = 8765


def save_screenshot(page, filename: str):
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(PREVIEWS_DIR / filename))
    if ARTIFACTS_DIR.exists():
        page.screenshot(path=str(ARTIFACTS_DIR / filename))

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        pass


def start_server():
    server = socketserver.TCPServer(("127.0.0.1", PORT), QuietHandler)
    server.allow_reuse_address = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)
    return server


PROD_HISTORICAL_OPPORTUNITIES = [
    {
        "id": "5988",
        "company": "Forbes Marshall",
        "max_package": "9.0",
        "min_package": "9.0",
        "placement_type": "Internship + Performance based PPO",
        "registration_end": "23-Oct-2026",
        "eligible_programs": "VIIT-BTech-Mechanical Engineering, VIT-BTech-Chemical Engineering",
        "company_type": "Regular",
        "is_active": "True",
        "first_seen_at": "2026-10-21T18:30:12.689575+00:00",
    },
    {
        "id": "5985",
        "company": "Valentius Kryptix",
        "max_package": "0.0",
        "min_package": "0.0",
        "placement_type": "Internship",
        "registration_end": "21-Oct-2026",
        "eligible_programs": "VIT-BTech-Computer Engineering, VIT-BTech-Information Technology",
        "company_type": "Regular",
        "is_active": "True",
        "first_seen_at": "2026-10-21T11:30:13.078989+00:00",
    },
    {
        "id": "5969",
        "company": "Eaton",
        "max_package": "10.0",
        "min_package": "10.0",
        "placement_type": "Internship + Performance based PPO",
        "registration_end": "16-Oct-2026",
        "eligible_programs": "VIT-BTech-Computer Engineering, VIT-BTech-Electronics",
        "company_type": "Regular",
        "is_active": "True",
        "first_seen_at": "2026-10-15T11:30:13.019717+00:00",
    },
    {
        "id": "5971",
        "company": "Cummins India",
        "max_package": "25000.0",
        "min_package": "25000.0",
        "placement_type": "Internship + Performance based PPO",
        "registration_end": None,
        "eligible_programs": "VIT-BTech-Computer Engineering, VIT-BTech-Mechanical",
        "company_type": "Regular",
        "is_active": "True",
        "first_seen_at": "2026-10-15T11:30:13.007845+00:00",
    },
    {
        "id": "5952",
        "company": "Cummins India",
        "max_package": "25000.0",
        "min_package": "25000.0",
        "placement_type": "Internship + Performance based PPO",
        "registration_end": "14-Oct-2026",
        "eligible_programs": "VIT-BTech-Computer Engineering, VIT-BTech-Mechanical",
        "company_type": "Regular",
        "is_active": "True",
        "first_seen_at": "2026-10-13T11:30:14.212607+00:00",
    },
]


def test_browser_case_b_historical_fallback():
    """Validates Case B: Live production state with 5 historical records and 0 verified listings."""
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        def handle_route(route):
            url = route.request.url
            if "/api/v1/opportunity-listing" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"items": [], "total": 0, "shown": 0, "status": "UNVERIFIED", "observed_ms": None}),
                )
            elif "/api/v1/opportunities" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(PROD_HISTORICAL_OPPORTUNITIES),
                )
            else:
                route.continue_()

        page.route("**/api/v1/**", handle_route)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("text=RECENTLY DETECTED OPPORTUNITIES", timeout=8000)

        # 1. Header assertions
        assert page.locator("text=RECENTLY DETECTED OPPORTUNITIES").is_visible()
        assert page.locator("text=5 RECORDED DETECTIONS").is_visible()

        # 2. Context notice assertion
        notice = page.locator("text=Previously detected by TPO-WATCHER — current availability not verified")
        assert notice.is_visible()

        # 3. Card titles assertion
        assert page.locator("text=FORBES MARSHALL").first.is_visible()
        assert page.locator("text=VALENTIUS KRYPTIX").first.is_visible()
        assert page.locator("text=EATON").first.is_visible()
        assert page.locator("text=CUMMINS INDIA").first.is_visible()

        # 4. Compensation formatting (unit unverified, verbatim value)
        assert page.locator("text=9.0 (unit unverified)").first.is_visible()
        assert page.locator("text=10.0 (unit unverified)").first.is_visible()
        assert page.locator("text=25000.0 (unit unverified)").first.is_visible()

        # 5. Expand first card to check details
        first_card = page.locator('[aria-label*="Forbes Marshall"]').first
        first_card.click()
        page.wait_for_selector("text=SOURCE ID: 5988")
        assert page.locator("text=SOURCE ID: 5988").is_visible()
        assert page.locator("text=STATUS: HISTORICAL RECORD").is_visible()
        assert page.locator("text=CURRENT PORTAL AVAILABILITY UNCONFIRMED").is_visible()

        # 6. Negative assertions: NO false CURRENT or VERIFIED labels on historical records
        card_content = first_card.inner_text()
        assert "LISTING OBSERVED" not in card_content
        assert "LISTING STATUS: CURRENT" not in card_content

        # Expand second card (Valentius Kryptix) to verify expired deadline label
        second_card = page.locator('[aria-label*="Valentius Kryptix"]').first
        second_card.click()
        page.wait_for_selector("text=21-Oct-2026 (Passed)")
        assert page.locator("text=21-Oct-2026 (Passed)").is_visible()

        # Expand fourth card (Cummins India 5971) to verify null deadline
        cummins_card = page.locator('[aria-label*="5971"]').first
        cummins_card.click()
        page.wait_for_selector("text=Not reported in source")
        assert page.locator("text=Not reported in source").is_visible()

        # Save screenshot
        save_screenshot(page, "case_b_hero_historical_fallback.png")

        assert len(console_errors) == 0, f"Unexpected console errors: {console_errors}"
        browser.close()


def test_browser_case_a_verified_current_listings():
    """Validates Case A: Verified current listings exist with fresh observation evidence."""
    now_ms = int(time.time() * 1000)
    console_errors = []

    verified_items = [
        {
            "id": "7001",
            "company": "Alpha Robotics",
            "max_package": "15.0",
            "min_package": "15.0",
            "placement_type": "Placement",
            "registration_end": "30-Oct-2026",
            "eligible_programs": "VIT-BTech-Computer Engineering",
            "company_type": "Super Dream",
            "is_active": "True",
            "first_seen_at": "2026-10-23T10:00:00.000Z",
            "registration_start": "23-Oct-2026",
            "registration_start_time": "10:00",
            "registration_end_time": "18:00",
            "registration_start_alternate": None,
            "registration_end_alternate": None,
            "deadline_timezone": "Asia/Kolkata",
            "compensation_unit": "LPA",
            "organizations": "VIT",
            "job_title": "Software Engineer",
            "job_description": "Robotics perception engineering",
            "academic_year": "2026-27",
            "eligibility_status": "PROGRAMME_MATCH_ONLY",
            "listing_status": "CURRENT",
            "observed_ms": now_ms,
            "valid_until_ms": now_ms + 600000,
        }
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        def handle_route(route):
            url = route.request.url
            if "/api/v1/opportunity-listing" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"items": verified_items, "total": 1, "shown": 1, "status": "VERIFIED", "observed_ms": now_ms}),
                )
            elif "/api/v1/opportunities" in url:
                route.fulfill(status=200, content_type="application/json", body="[]")
            else:
                route.continue_()

        page.route("**/api/v1/**", handle_route)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("text=ALPHA ROBOTICS", timeout=8000)

        assert page.locator("text=TPO OPPORTUNITIES").first.is_visible()
        assert page.locator("text=1 SHOWN / 1 AT LAST CHECK").is_visible()
        assert page.locator("text=15.0 (LPA)").is_visible()

        page.locator('[aria-label*="Alpha Robotics"]').first.click()
        page.wait_for_selector("text=LISTING OBSERVED")
        assert page.locator("text=LISTING OBSERVED").is_visible()

        save_screenshot(page, "case_a_hero_verified_listing.png")

        assert len(console_errors) == 0
        browser.close()


def test_browser_case_c_empty_state():
    """Validates Case C: Empty database displays genuine empty state."""
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        def handle_route(route):
            url = route.request.url
            if "/api/v1/opportunity-listing" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"items": [], "total": 0, "shown": 0, "status": "UNVERIFIED", "observed_ms": None}),
                )
            elif "/api/v1/opportunities" in url:
                route.fulfill(status=200, content_type="application/json", body="[]")
            else:
                route.continue_()

        page.route("**/api/v1/**", handle_route)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("text=NO RECORDED OPPORTUNITIES", timeout=8000)

        assert page.locator("text=NO RECORDED OPPORTUNITIES").is_visible()
        assert page.locator("text=LATEST PORTAL CHECK HAS NO MATCHING ACTIVE LISTINGS").is_visible()

        page.wait_for_timeout(1400)
        save_screenshot(page, "case_c_hero_empty_state.png")

        assert len(console_errors) == 0
        browser.close()


def test_browser_case_d_api_error_state():
    """Validates Case D: 503 error surfaces clean error state instead of silent corruption."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def handle_route(route):
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Service Unavailable"}))

        page.route("**/api/v1/**", handle_route)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("text=LIVE DATA UNAVAILABLE", timeout=8000)

        assert page.locator("text=LIVE DATA UNAVAILABLE").is_visible()
        assert page.locator("text=UNABLE TO REACH TPO SERVICE").is_visible()

        page.wait_for_timeout(1400)
        save_screenshot(page, "case_d_hero_api_error.png")

        browser.close()


def test_browser_mobile_viewport_rendering():
    """Validates mobile 375px viewport rendering without horizontal overflow."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 375, "height": 667})

        def handle_route(route):
            url = route.request.url
            if "/api/v1/opportunity-listing" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": [], "total": 0, "shown": 0, "status": "UNVERIFIED", "observed_ms": None}))
            elif "/api/v1/opportunities" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(PROD_HISTORICAL_OPPORTUNITIES[:3]))
            else:
                route.continue_()

        page.route("**/api/v1/**", handle_route)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_selector("text=RECENTLY DETECTED OPPORTUNITIES", timeout=8000)

        # Check scroll width doesn't exceed viewport width significantly
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, f"Horizontal overflow detected: scrollWidth {scroll_width} > clientWidth {client_width}"

        page.wait_for_timeout(1400)
        page.locator("text=RECENTLY DETECTED OPPORTUNITIES").scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        save_screenshot(page, "case_b_mobile_rendering.png")

        browser.close()


if __name__ == "__main__":
    print(f"Starting static test server on http://127.0.0.1:{PORT}...")
    server = start_server()
    print("Running browser verification scenarios against built assets...")
    test_browser_case_b_historical_fallback()
    print("PASS: Case B Historical Fallback (Hero + Archive)")
    test_browser_case_a_verified_current_listings()
    print("PASS: Case A Verified Current Listings")
    test_browser_case_c_empty_state()
    print("PASS: Case C Empty State")
    test_browser_case_d_api_error_state()
    print("PASS: Case D API Error State")
    test_browser_mobile_viewport_rendering()
    print("PASS: Mobile Viewport Rendering (375x667)")
    print("\nALL 5 BROWSER SCENARIOS PASSED 100%!")
