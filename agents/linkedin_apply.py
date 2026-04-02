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
"""
from __future__ import annotations
import os, time, asyncio
from playwright.async_api import async_playwright, Page, Locator

from core.models import CandidateProfile, ScoredJob, ApplyChannel
from core import tracker

APPLY_MODE = os.getenv("APPLY_MODE", "confirm")
LI_EMAIL   = os.environ.get("LINKEDIN_EMAIL", "")
LI_PASSWORD= os.environ.get("LINKEDIN_PASSWORD", "")


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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # headless=True for prod
        context = await browser.new_context()
        page    = await context.new_page()

        try:
            await _login(page)
            await page.goto(listing.url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            if listing.easy_apply:
                success = await _easy_apply(page, profile, resume_path, listing.title, listing.company)
                channel = ApplyChannel.LINKEDIN_EASY
            else:
                # Open external ATS link
                apply_btn = page.locator("a.jobs-apply-button, a[data-tracking-control-name='public_jobs_apply-link-offsite_sign-in-modal']")
                if await apply_btn.count() > 0:
                    url = await apply_btn.first.get_attribute("href")
                    await page.goto(url or listing.apply_url or listing.url)
                    await page.wait_for_timeout(2000)
                success = False  # hand off to ATS agent
                channel = ApplyChannel.LINKEDIN_FORM

            if success:
                tracker.mark_applied(app_id, channel, resume_path)

            await browser.close()
            return success

        except Exception as e:
            print(f"[LinkedInApply] ✗ Error: {e}")
            await browser.close()
            return False


# ── Login ─────────────────────────────────────────────────────────────────────

async def _login(page: Page) -> None:
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await page.fill("#username", LI_EMAIL)
    await page.fill("#password", LI_PASSWORD)
    await page.click("button[type='submit']")
    await page.wait_for_timeout(3000)
    # Handle potential 2FA / CAPTCHA — pause for human if needed
    if "checkpoint" in page.url or "captcha" in page.url:
        print("[LinkedInApply] ⚠ CAPTCHA / 2FA detected. Complete it in the browser window.")
        await page.wait_for_url("**/feed/**", timeout=120_000)


# ── Easy Apply flow ───────────────────────────────────────────────────────────

async def _easy_apply(
    page: Page,
    profile: CandidateProfile,
    resume_path: str,
    job_title: str,
    company: str,
) -> bool:
    """Walk through the LinkedIn Easy Apply multi-step modal."""

    # Click the Easy Apply button
    btn = page.locator("button.jobs-apply-button:has-text('Easy Apply')")
    if not await btn.count():
        print("[LinkedInApply] Easy Apply button not found")
        return False
    await btn.first.click()
    await page.wait_for_timeout(1500)

    step = 0
    max_steps = 12  # safety cap

    while step < max_steps:
        step += 1
        modal = page.locator(".jobs-easy-apply-modal")
        if not await modal.count():
            break

        # Upload resume on first step if file input appears
        file_input = modal.locator("input[type='file']")
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            await page.wait_for_timeout(1000)

        # Fill any visible text/select fields using profile data
        await _fill_form_fields(modal, profile)

        # Check for submit vs next
        submit_btn = modal.locator("button:has-text('Submit application')")
        next_btn   = modal.locator("button:has-text('Next'), button:has-text('Continue'), button:has-text('Review')")

        if await submit_btn.count() > 0:
            if APPLY_MODE == "confirm":
                _confirm_prompt(job_title, company)
            if APPLY_MODE != "draft":
                await submit_btn.first.click()
                await page.wait_for_timeout(2000)
                print(f"[LinkedInApply] ✓ Submitted Easy Apply: {job_title} @ {company}")
                return True
            else:
                print(f"[LinkedInApply] DRAFT mode — form filled, not submitted: {job_title} @ {company}")
                return True

        elif await next_btn.count() > 0:
            await next_btn.first.click()
            await page.wait_for_timeout(1200)
        else:
            # No recognisable button — bail
            print("[LinkedInApply] ⚠ Could not find Next/Submit button")
            break

    return False


# ── Form field filler ─────────────────────────────────────────────────────────

FIELD_MAP = {
    # label text fragment → profile attribute or ats_answers key
    "phone":             lambda p: p.phone or "",
    "mobile":            lambda p: p.phone or "",
    "city":              lambda p: p.location,
    "location":          lambda p: p.location,
    "linkedin":          lambda p: p.linkedin_url or "",
    "salary":            lambda p: str(p.salary_min or ""),
    "notice":            lambda p: p.ats_answers.get("notice_period", "30 days"),
    "sponsorship":       lambda p: p.ats_answers.get("requires_sponsorship", "No"),
    "authoris":          lambda p: p.ats_answers.get("authorised_to_work", "Yes"),
    "years of experience": lambda p: str(int(p.years_experience)),
}

async def _fill_form_fields(modal: Locator, profile: CandidateProfile) -> None:
    """Best-effort fill of text inputs and selects in the Easy Apply modal."""
    inputs = await modal.locator("input[type='text'], input[type='tel'], input[type='number'], textarea").all()
    for inp in inputs:
        try:
            label_text = ""
            label = inp.locator("xpath=preceding-sibling::label | ../label | ../../label")
            if await label.count():
                label_text = (await label.first.inner_text()).lower()

            for key, value_fn in FIELD_MAP.items():
                if key in label_text:
                    current_val = await inp.input_value()
                    if not current_val:
                        await inp.fill(value_fn(profile))
                    break
        except Exception:
            pass

    # Handle Yes/No radio buttons
    yes_radios = await modal.locator("input[type='radio'][value='Yes'], label:has-text('Yes')").all()
    for radio in yes_radios:
        try:
            parent_text = await radio.locator("xpath=../../..").inner_text()
            parent_text = parent_text.lower()
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
    input("  Press ENTER to submit, or Ctrl+C to skip this application.\n")
