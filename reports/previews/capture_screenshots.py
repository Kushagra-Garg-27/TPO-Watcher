"""
Capture crisp desktop and mobile layout screenshots of TPO-WATCHER email notifications.
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

import os

PREVIEWS_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = Path(os.environ.get("TPO_SCREENSHOTS_DIR", str(PREVIEWS_DIR / "screenshots")))
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

targets = [
    ("cummins_5952_preview.html", "cummins_5952"),
    ("cummins_5971_preview.html", "cummins_5971"),
    ("cummins_update_preview.html", "cummins_update"),
]

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for html_file, name in targets:
            file_url = (PREVIEWS_DIR / html_file).as_uri()

            # Desktop (1000x850 viewport)
            desktop_page = await browser.new_page(viewport={"width": 1000, "height": 850})
            await desktop_page.goto(file_url)
            await desktop_page.wait_for_timeout(500)
            desktop_path = SCREENSHOTS_DIR / f"{name}_desktop.png"
            await desktop_page.screenshot(path=str(desktop_path), full_page=True)
            print(f"Captured desktop: {desktop_path}")
            await desktop_page.close()

            # Mobile (390x844 viewport, iPhone width)
            mobile_page = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
            await mobile_page.goto(file_url)
            await mobile_page.wait_for_timeout(500)
            mobile_path = SCREENSHOTS_DIR / f"{name}_mobile.png"
            await mobile_page.screenshot(path=str(mobile_path), full_page=True)
            print(f"Captured mobile: {mobile_path}")
            await mobile_page.close()

        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture())
