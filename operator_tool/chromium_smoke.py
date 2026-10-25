"""
Stage C2 container gate: real Chromium launch under the runtime user.
=====================================================================
Run INSIDE the standalone operator image as the non-root `operator` user
(uid 1000). Launches actual headless Chromium through Playwright, opens a
page, and exits 0 only if the browser executable is genuinely reachable and
executable by the non-root account. No network access is required
(about:blank navigation only); safe under --network none.

Usage: python /opt/operator_tool/chromium_smoke.py
Exit 0 = Chromium launched and closed cleanly under the current (non-root) user.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright


async def main() -> int:
    uid = os.getuid() if hasattr(os, "getuid") else -1
    if uid == 0:
        print("CHROMIUM_SMOKE_REFUSED: must not run as root")
        return 2
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto("about:blank", timeout=15000)
            title = await page.title()
        finally:
            await page.close()
            await context.close()
            await browser.close()
    print(f"CHROMIUM_LAUNCH_OK uid={uid} title_len={len(title or '')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
