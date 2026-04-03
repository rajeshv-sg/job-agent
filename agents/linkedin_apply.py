"""
agents/linkedin_apply.py
Agent 5 — LinkedIn Apply

Handles two LinkedIn application paths:
  1. Easy Apply  → Playwright fills and submits the in-LinkedIn modal
  2. External    → Opens the company ATS URL, hands off to ats_portal.py

Apply modes (set via APPLY_MODE env var):
  auto    → submits immediately
  confirm → shows a summary, waits for your keypress before submitting
  draft   → fills the form but does NOT click submit; you finish manually

Browser session is shared across all jobs in a run (login once, apply many).
"""
from __future__ import annotations
import os, asyncio
from playwright.async_api import async_playwright, Page, Locator, Browser, BrowserContext

from core.models import CandidateProfile, ScoredJob, ApplyChannel
from core import tracker

APPLY_MODE  = os.getenv("APPLY_MODE", "confirm")
LI_EMAIL    = os.environ.get("LINKEDIN_EMAIL", "")
LI_PASSWORD = os.environ.get("LINKEDIN_PASSWORD", "")

# ── Shared browser session (login once per run) ───────────────────────────────

_pw       = None
_browser: Browser | None        = None
_context: BrowserContext | None = None
_page:    Page | None           = None


async def _get_page() -> Page:
    """Return the shared LinkedIn page, logging in if needed."""
    global _pw, _browser, _context, _page
    if _page is None:
        _pw      = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=False)
        _context = await _browser.new_context()
        _page    = await _context.new_page()
        await _login(_page)
    return _page


async def close_browser() -> None:
    """Call once after all jobs are processed."""
    global _pw, _browser, _context, _page
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()
    _pw = _browser = _context = _page = None


# ── Entry point ───────────────────────────────────────────────────────────────

async def apply_linkedin(
    scored_job: ScoredJob,
    profile: CandidateProfile,
    resume_path: str,
    app_id: str,
) -> bool:
    """
    Attempt to apply for the job via LinkedIn.
    Returns True if application was submitted (or drafted successfully).
    """
    listing = scored_job.listing
    if listing.platform != "linkedin":
        return False

    try:
        page = await _get_page()
        await page.goto(listing.url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2500)

        # Scroll to trigger lazy-loaded buttons
        await page.evaluate("window.scrollBy(0, 300)")
        await page.wait_for_timeout(1000)

        if listing.easy_apply:
            success = await _easy_apply(page, profile, resume_path, listing.title, listing.company)
            channel = ApplyChannel.LINKEDIN_EASY
        else:
            apply_btn = page.locator(
                "a.jobs-apply-button, "
                "a[data-tracking-control-name*='apply-link-offsite']"
            )
            if await apply_btn.count() > 0:
                url = await apply_btn.first.get_attribute("href")
                await page.goto(url or listing.apply_url or listing.url)
                await page.wait_for_timeout(2000)
            success = False
            channel = ApplyChannel.LINKEDIN_FORM

        if success:
            tracker.mark_applied(app_id, channel, resume_path)

        return success

    except Exception as e:
        print(f"[LinkedInApply] ✗ Error: {e}")
        # Reset page on error so next job gets a fresh tab
        global _page
        try:
            _page = await _context.new_page()
            await _login(_page)
        except Exception:
            pass
        return False


# ── Login ─────────────────────────────────────────────────────────────────────

async def _login(page: Page) -> None:
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)

    # Already logged in?
    if "feed" in page.url or "mynetwork" in page.url:
        return

    await page.fill("#username", LI_EMAIL)
    await page.fill("#password", LI_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_timeout(3000)

    # Handle CAPTCHA / 2FA — give user 2 minutes to complete
    if "checkpoint" in page.url or "captcha" in page.url or "challenge" in page.url:
        print("[LinkedInApply] ⚠ CAPTCHA / 2FA detected. Complete it in the browser window (2 min timeout).")
        try:
            await page.wait_for_url("**/feed/**", timeout=120_000)
        except Exception:
            print("[LinkedInApply] ✗ Timed out waiting for CAPTCHA resolution.")
            raise


# ── Easy Apply flow ───────────────────────────────────────────────────────────

# Multiple selector fallbacks for the Easy Apply button
_EASY_APPLY_BTN = (
    "button.jobs-apply-button:has-text('Easy Apply'), "
    "button[aria-label*='Easy Apply'], "
    "button[data-control-name*='apply']:has-text('Easy Apply'), "
    ".jobs-s-apply button:has-text('Easy Apply'), "
    "button:has-text('Easy Apply')"
)


async def _easy_apply(
    page: Page,
    profile: CandidateProfile,
    resume_path: str,
    job_title: str,
    company: str,
) -> bool:
    """Walk through the LinkedIn Easy Apply multi-step modal."""

    # Wait up to 5s for button to appear
    btn = page.locator(_EASY_APPLY_BTN)
    try:
        await btn.first.wait_for(timeout=5_000)
    except Exception:
        print("[LinkedInApply] Easy Apply button not found")
        return False

    await btn.first.click()
    await page.wait_for_timeout(1500)

    step = 0
    max_steps = 12

    while step < max_steps:
        step += 1
        modal = page.locator(".jobs-easy-apply-modal, [data-test-modal]")
        if not await modal.count():
            break

        # Upload resume if file input appears
        file_input = modal.locator("input[type='file']")
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            await page.wait_for_timeout(1000)

        # Fill visible text/select fields
        await _fill_form_fields(modal, profile)

        submit_btn = modal.locator(
            "button:has-text('Submit application'), "
            "button[aria-label*='Submit']"
        )
        next_btn = modal.locator(
            "button:has-text('Next'), "
            "button:has-text('Continue'), "
            "button:has-text('Review')"
        )

        if await submit_btn.count() > 0:
            if APPLY_MODE == "confirm":
                _confirm_prompt(job_title, company)
            if APPLY_MODE != "draft":
                await submit_btn.first.click()
                await page.wait_for_timeout(2000)
                print(f"[LinkedInApply] ✓ Submitted: {job_title} @ {company}")
                return True
            else:
                print(f"[LinkedInApply] DRAFT — form filled, not submitted: {job_title} @ {company}")
                return True

        elif await next_btn.count() > 0:
            await next_btn.first.click()
            await page.wait_for_timeout(1200)
        else:
            print("[LinkedInApply] ⚠ Could not find Next/Submit button")
            break

    return False


# ── Form field filler ─────────────────────────────────────────────────────────

FIELD_MAP = {
    "phone":               lambda p: p.phone or "",
    "mobile":              lambda p: p.phone or "",
    "city":                lambda p: p.location,
    "location":            lambda p: p.location,
    "linkedin":            lambda p: p.linkedin_url or "",
    "salary":              lambda p: str(p.salary_min or ""),
    "notice":              lambda p: p.ats_answers.get("notice_period", "30 days"),
    "sponsorship":         lambda p: p.ats_answers.get("requires_sponsorship", "No"),
    "authoris":            lambda p: p.ats_answers.get("authorised_to_work", "Yes"),
    "years of experience": lambda p: str(int(p.years_experience)),
}

async def _fill_form_fields(modal: Locator, profile: CandidateProfile) -> None:
    inputs = await modal.locator(
        "input[type='text'], input[type='tel'], input[type='number'], textarea"
    ).all()
    for inp in inputs:
        try:
            label_text = ""
            label = inp.locator("xpath=preceding-sibling::label | ../label | ../../label")
            if await label.count():
                label_text = (await label.first.inner_text()).lower()
            for key, value_fn in FIELD_MAP.items():
                if key in label_text:
                    if not await inp.input_value():
                        await inp.fill(value_fn(profile))
                    break
        except Exception:
            pass

    # Yes/No radio buttons for work authorisation
    yes_radios = await modal.locator(
        "input[type='radio'][value='Yes'], label:has-text('Yes')"
    ).all()
    for radio in yes_radios:
        try:
            parent_text = (await radio.locator("xpath=../../..").inner_text()).lower()
            if "authoris" in parent_text or "eligible" in parent_text or "right to work" in parent_text:
                await radio.click()
        except Exception:
            pass


# ── Human confirm prompt ──────────────────────────────────────────────────────

def _confirm_prompt(job_title: str, company: str) -> None:
    print(f"\n{'='*60}")
    print(f"  About to SUBMIT Easy Apply:")
    print(f"  Role:    {job_title}")
    print(f"  Company: {company}")
    print(f"{'='*60}")
    input("  Press ENTER to submit, or Ctrl+C to skip.\n")
