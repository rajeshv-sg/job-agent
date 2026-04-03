"""
debug_easyapply.py
Opens a LinkedIn job page and prints all button details so we can find
the correct Easy Apply selector.
"""
import asyncio, os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

# Paste any LinkedIn job URL here that shows Easy Apply
TEST_URL = "https://www.linkedin.com/jobs/search/?keywords=Program+Manager&location=Singapore"

async def main():
    async with async_playwright() as pw:
        profile_dir = os.path.join(os.path.dirname(__file__), "data", "browser_profile")
        os.makedirs(profile_dir, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        # Check / restore session
        print("Checking LinkedIn session...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        def is_logged_in(url: str) -> bool:
            return url.startswith("https://www.linkedin.com/feed") or "/mynetwork" in url or "/jobs" in url and "login" not in url

        if not is_logged_in(page.url):
            print(f"Not logged in (URL: {page.url})")
            print("Filling credentials...")
            await page.goto("https://www.linkedin.com/login", wait_until="networkidle", timeout=30000)
            try:
                await page.wait_for_selector("#username", timeout=10000)
                await page.fill("#username", os.environ["LINKEDIN_EMAIL"])
                await page.fill("#password", os.environ["LINKEDIN_PASSWORD"])
                await page.click("button[type='submit']")
                await page.wait_for_timeout(5000)
            except Exception:
                pass
            if not is_logged_in(page.url):
                print("⚠ Complete any CAPTCHA/2FA in the browser window — waiting 3 minutes...")
                await page.wait_for_url("**/feed/**", timeout=180000)

        print(f"✓ Logged in. URL: {page.url}")

        # Go to job search
        print(f"\nNavigating to job search...")
        await page.goto(TEST_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Click first job in the list
        first_job = page.locator(".job-card-container, .jobs-search-results__list-item").first
        if await first_job.count():
            await first_job.click()
            await page.wait_for_timeout(2000)

        print(f"\nPage URL: {page.url}")
        print("\n=== ALL BUTTONS ON PAGE ===")
        buttons = await page.locator("button").all()
        for i, btn in enumerate(buttons):
            try:
                text    = (await btn.inner_text()).strip()[:60]
                cls     = await btn.get_attribute("class") or ""
                aria    = await btn.get_attribute("aria-label") or ""
                visible = await btn.is_visible()
                print(f"[{i}] visible={visible} text='{text}' aria='{aria[:40]}' class='{cls[:60]}'")
            except Exception as e:
                print(f"[{i}] error: {e}")

        print("\n=== LOOKING FOR APPLY BUTTONS ===")
        for selector in [
            "button:has-text('Easy Apply')",
            "button:has-text('Apply')",
            ".jobs-apply-button",
            "[data-control-name*='apply']",
            "button[aria-label*='Apply']",
        ]:
            count = await page.locator(selector).count()
            print(f"  '{selector}' → {count} match(es)")

        print("\nKeeping browser open for 30 seconds so you can inspect...")
        await page.wait_for_timeout(30000)
        await context.close()

asyncio.run(main())
