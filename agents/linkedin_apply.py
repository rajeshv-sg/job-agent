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

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile")

_pw:      object | None = None
_context: BrowserContext | None = None
_page:    Page | None           = None


async def _get_page() -> Page:
    """
    Return the shared LinkedIn page using a persistent browser profile.
    The profile saves cookies/session so LinkedIn doesn't re-challenge every run.
    On first run the browser opens and you log in manually — subsequent runs
    reuse the saved session automatically.
    """
    global _pw, _context, _page
    if _page is None:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        _pw      = await async_playwright().start()
        _context = await _pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        _page = _context.pages[0] if _context.pages else await _context.new_page()
        await _ensure_logged_in(_page)
    return _page


async def close_browser() -> None:
    """Call once after all jobs are processed."""
    global _pw, _context, _page
    if _context:
        await _context.close()
    if _pw:
        await _pw.stop()
    _pw = _context = _page = None


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

        # Navigate via the jobs search panel which properly renders the Easy Apply button.
        # Directly visiting /jobs/view/ID/ renders a different layout without the apply button.
        job_id = listing.url.rstrip("/").split("/")[-1]
        search_url = f"https://www.linkedin.com/jobs/search/?currentJobId={job_id}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        # Scroll to trigger lazy-loaded buttons
        await page.evaluate("window.scrollBy(0, 300)")
        await page.wait_for_timeout(1500)

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
            await _ensure_logged_in(_page)
        except Exception:
            pass
        return False


# ── Login ─────────────────────────────────────────────────────────────────────

async def _ensure_logged_in(page: Page) -> None:
    """
    Check if already logged in via saved profile. If not, fill credentials
    and wait for the user to complete any CAPTCHA/2FA in the browser.
    """
    def _is_logged_in(url: str) -> bool:
        return url.startswith("https://www.linkedin.com/feed") or (
            "/jobs" in url and "login" not in url and "session_redirect" not in url
        )

    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    # Already logged in — saved session worked
    if _is_logged_in(page.url):
        print("[LinkedInApply] ✓ LinkedIn session restored from saved profile", flush=True)
        return

    # Need to log in
    print("[LinkedInApply] Logging in to LinkedIn...", flush=True)
    await page.goto("https://www.linkedin.com/login", wait_until="networkidle", timeout=30_000)
    await page.wait_for_timeout(1000)

    try:
        await page.wait_for_selector("#username", timeout=10_000)
        await page.fill("#username", LI_EMAIL)
        await page.fill("#password", LI_PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_timeout(3000)
    except Exception:
        pass

    # Handle CAPTCHA / 2FA — wait up to 3 minutes for manual completion
    if not _is_logged_in(page.url):
        print("[LinkedInApply] ⚠ Complete any CAPTCHA/2FA in the browser window (3 min timeout).", flush=True)
        try:
            await page.wait_for_url("**/feed/**", timeout=180_000)
        except Exception:
            print("[LinkedInApply] ✗ Timed out waiting for login.", flush=True)
            raise

    print("[LinkedInApply] ✓ LinkedIn session established and saved", flush=True)


# ── Easy Apply flow ───────────────────────────────────────────────────────────

# Specific selectors for the actual Easy Apply button.
# NOTE: Do NOT use generic button:has-text('Easy Apply') — it matches the
# "Easy Apply filter" pill first, which is wrong.
_EASY_APPLY_BTN = (
    "button.jobs-apply-button:has-text('Easy Apply'), "
    "button[aria-label^='Easy Apply to']"
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
        print("[LinkedInApply] Easy Apply button not found", flush=True)
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
            print("[LinkedInApply] ⚠ Could not find Next/Submit button", flush=True)
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
