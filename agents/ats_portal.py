"""
agents/ats_portal.py
Agent 6 — ATS Portal Agent

Fills and submits applications on external ATS portals.
Supported: Workday, Greenhouse, Lever, SmartRecruiters, Ashby
Falls back to a generic field-filler for unknown portals.

Requires Playwright (chromium). Each ATS has its own field-mapping strategy.
"""
from __future__ import annotations
import os, asyncio
from playwright.async_api import async_playwright, Page

from core.models import CandidateProfile, ScoredJob, ApplyChannel
from core import tracker

APPLY_MODE = os.getenv("APPLY_MODE", "confirm")


# ── Entry point ───────────────────────────────────────────────────────────────

async def apply_ats(
    scored_job: ScoredJob,
    profile: CandidateProfile,
    resume_path: str,
    app_id: str,
) -> bool:
    listing  = scored_job.listing
    apply_url = listing.apply_url or listing.url
    ats_type  = listing.ats_type or "generic"

    print(f"[ATS] Applying via {ats_type.upper()}: {listing.title} @ {listing.company}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page    = await browser.new_page()
        try:
            await page.goto(apply_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            handler = _get_handler(ats_type)
            success = await handler(page, profile, resume_path, listing.title, listing.company)

            if success:
                tracker.mark_applied(app_id, ApplyChannel.ATS_PORTAL, resume_path)

            await browser.close()
            return success
        except Exception as e:
            print(f"[ATS] ✗ Error: {e}")
            await browser.close()
            return False


def _get_handler(ats_type: str):
    return {
        "workday":        _workday,
        "greenhouse":     _greenhouse,
        "lever":          _lever,
        "smartrecruiters":_smartrecruiters,
        "ashby":          _ashby,
    }.get(ats_type, _generic)


# ── Workday ───────────────────────────────────────────────────────────────────

async def _workday(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    """Workday iFrame-based application flow."""
    await page.wait_for_selector("button:has-text('Apply')", timeout=10000)
    await page.click("button:has-text('Apply')")
    await page.wait_for_timeout(2000)

    # Upload resume
    file_input = page.locator("input[type='file']")
    if await file_input.count():
        await file_input.first.set_input_files(resume)
        await page.wait_for_timeout(2000)

    # Fill standard Workday fields
    await _try_fill(page, "[data-automation-id='legalNameSection_firstName']",  profile.full_name.split()[0])
    await _try_fill(page, "[data-automation-id='legalNameSection_lastName']",   profile.full_name.split()[-1])
    await _try_fill(page, "[data-automation-id='email']",                       profile.email)
    await _try_fill(page, "[data-automation-id='phone-number']",                profile.phone or "")
    await _try_fill(page, "[data-automation-id='addressSection_city']",         profile.location)

    return await _confirm_and_submit(page, title, company, "button[data-automation-id='bottom-navigation-next-button']", "button[data-automation-id='bottom-navigation-next-button']")


# ── Greenhouse ────────────────────────────────────────────────────────────────

async def _greenhouse(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    """Greenhouse application form."""
    await _try_fill(page, "#first_name",  profile.full_name.split()[0])
    await _try_fill(page, "#last_name",   profile.full_name.split()[-1])
    await _try_fill(page, "#email",       profile.email)
    await _try_fill(page, "#phone",       profile.phone or "")

    file_input = page.locator("input#resume")
    if await file_input.count():
        await file_input.set_input_files(resume)
        await page.wait_for_timeout(1500)

    await _fill_linkedin(page, profile)
    await _fill_ats_screening(page, profile)

    return await _confirm_and_submit(page, title, company, "#submit_app", "#submit_app")


# ── Lever ─────────────────────────────────────────────────────────────────────

async def _lever(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    """Lever application form."""
    await _try_fill(page, "input[name='name']",    profile.full_name)
    await _try_fill(page, "input[name='email']",   profile.email)
    await _try_fill(page, "input[name='phone']",   profile.phone or "")
    await _try_fill(page, "input[name='location']",profile.location)
    await _try_fill(page, "input[name='urls[LinkedIn]']", profile.linkedin_url or "")

    file_input = page.locator("input[type='file']")
    if await file_input.count():
        await file_input.first.set_input_files(resume)
        await page.wait_for_timeout(1500)

    return await _confirm_and_submit(page, title, company, "button[type='submit']", "button[type='submit']")


# ── SmartRecruiters ───────────────────────────────────────────────────────────

async def _smartrecruiters(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    await _try_fill(page, "input[id*='firstName'], input[name*='firstName']", profile.full_name.split()[0])
    await _try_fill(page, "input[id*='lastName'],  input[name*='lastName']",  profile.full_name.split()[-1])
    await _try_fill(page, "input[type='email']",   profile.email)
    await _try_fill(page, "input[type='tel']",     profile.phone or "")

    file_input = page.locator("input[type='file']")
    if await file_input.count():
        await file_input.first.set_input_files(resume)
        await page.wait_for_timeout(1500)

    return await _confirm_and_submit(page, title, company, "button[type='submit']", "button[type='submit']")


# ── Ashby ─────────────────────────────────────────────────────────────────────

async def _ashby(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    await _try_fill(page, "input[name='name']",   profile.full_name)
    await _try_fill(page, "input[name='email']",  profile.email)
    await _try_fill(page, "input[name='phone']",  profile.phone or "")

    file_input = page.locator("input[type='file']")
    if await file_input.count():
        await file_input.first.set_input_files(resume)
        await page.wait_for_timeout(1500)

    return await _confirm_and_submit(page, title, company, "button[type='submit']", "button[type='submit']")


# ── Generic fallback ──────────────────────────────────────────────────────────

async def _generic(page: Page, profile: CandidateProfile, resume: str, title: str, company: str) -> bool:
    """Best-effort fill for unrecognised ATS portals."""
    await _try_fill(page, "input[name*='first'],  input[id*='first']",  profile.full_name.split()[0])
    await _try_fill(page, "input[name*='last'],   input[id*='last']",   profile.full_name.split()[-1])
    await _try_fill(page, "input[name*='name']:not([name*='first']):not([name*='last'])", profile.full_name)
    await _try_fill(page, "input[type='email']",  profile.email)
    await _try_fill(page, "input[type='tel'], input[name*='phone']", profile.phone or "")

    file_input = page.locator("input[type='file']")
    if await file_input.count():
        await file_input.first.set_input_files(resume)
        await page.wait_for_timeout(1500)

    await _fill_linkedin(page, profile)
    await _fill_ats_screening(page, profile)

    return await _confirm_and_submit(page, title, company,
        "button[type='submit'], button:has-text('Submit'), input[type='submit']",
        "button:has-text('Next'), button:has-text('Continue')"
    )


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _try_fill(page: Page, selector: str, value: str) -> None:
    """Try multiple comma-separated selectors, fill the first that exists."""
    if not value:
        return
    for sel in [s.strip() for s in selector.split(",")]:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                current = await locator.input_value()
                if not current:
                    await locator.fill(value)
                return
        except Exception:
            continue


async def _fill_linkedin(page: Page, profile: CandidateProfile) -> None:
    if profile.linkedin_url:
        await _try_fill(page, "input[name*='linkedin'], input[id*='linkedin'], input[placeholder*='LinkedIn']", profile.linkedin_url)


async def _fill_ats_screening(page: Page, profile: CandidateProfile) -> None:
    """Fill common screening questions from ats_answers."""
    answers = profile.ats_answers
    if answers.get("notice_period"):
        await _try_fill(page, "input[name*='notice'], input[id*='notice']", answers["notice_period"])
    if answers.get("salary_expectation"):
        await _try_fill(page, "input[name*='salary'], input[id*='salary'], input[name*='compensation']", answers["salary_expectation"])


async def _confirm_and_submit(page: Page, title: str, company: str, submit_sel: str, next_sel: str) -> bool:
    """
    Walk through a multi-page form (clicking Next) until Submit is found.
    Respects APPLY_MODE: auto | confirm | draft
    """
    for _ in range(10):  # up to 10 pages
        await page.wait_for_timeout(800)

        submit = page.locator(submit_sel).first
        if await submit.count() > 0 and await submit.is_visible():
            if APPLY_MODE == "confirm":
                _human_confirm(title, company)
            if APPLY_MODE != "draft":
                await submit.click()
                await page.wait_for_timeout(2000)
                print(f"[ATS] ✓ Submitted: {title} @ {company}")
                return True
            else:
                print(f"[ATS] DRAFT — form filled, not submitted: {title} @ {company}")
                return True

        nxt = page.locator(next_sel).first
        if await nxt.count() > 0 and await nxt.is_visible():
            await nxt.click()
        else:
            break

    return False


def _human_confirm(title: str, company: str) -> None:
    print(f"\n{'='*60}")
    print(f"  About to SUBMIT via ATS:")
    print(f"  Role:    {title}")
    print(f"  Company: {company}")
    print(f"{'='*60}")
    input("  Press ENTER to submit, Ctrl+C to skip.\n")
