"""
orchestrator.py
The Orchestrator — coordinates all agents in the correct order.

Pipeline:
  1. Profile Builder   → CandidateProfile
  2. Job Discovery     → list[JobListing]
  3. Relevance Scorer  → list[ScoredJob]
  4. For each ScoredJob (in parallel batches):
       a. Resume Tailor       → tailored resume file
       b. LinkedIn Easy Apply → submit via Playwright (if easy_apply=True)
       c. ATS Portal Agent    → submit via ATS portal (if not easy_apply)
       d. Outreach Writer     → email to recruiter (if contact found)
  5. Tracker           → logged throughout

Run:  python orchestrator.py
"""
from __future__ import annotations
import os, asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()
console = Console()

from agents.profile_builder  import build_profile
from agents.job_discovery    import search_all
from agents.relevance_scorer import score_jobs
from agents.resume_tailor    import tailor_resume
from agents.outreach_writer  import send_outreach
from core import tracker
from core.models import ApplicationStatus, ApplyChannel, ScoredJob, CandidateProfile


RESUME_PATH     = os.getenv("RESUME_PATH",      "data/resumes/resume.pdf")
ATS_PROFILE     = os.getenv("ATS_PROFILE_PATH", "data/resumes/ats_profile.json")
EXCLUDE_COMPANIES = [
    c.strip().lower()
    for c in os.getenv("EXCLUDE_COMPANIES", "").split(",")
    if c.strip()
]


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline() -> None:
    console.rule("[bold]Job Application Agent — Starting Pipeline[/bold]")

    # ── Step 1: Build profile ────────────────────────────────────────────────
    console.print("\n[1/4] Building candidate profile...")
    profile = build_profile(RESUME_PATH, ATS_PROFILE)
    console.print(f"  ✓ Profile: [green]{profile.full_name}[/green] — {profile.current_title}")

    # ── Step 2: Discover jobs ────────────────────────────────────────────────
    console.print("\n[2/4] Discovering jobs...")
    all_listings = search_all(profile)

    # Filter excluded companies
    if EXCLUDE_COMPANIES:
        before = len(all_listings)
        all_listings = [j for j in all_listings if j.company.lower() not in EXCLUDE_COMPANIES]
        console.print(f"  Filtered {before - len(all_listings)} excluded companies")

    # Filter already-applied jobs
    fresh = [j for j in all_listings if not tracker.is_duplicate(j.job_id, j.platform)]
    console.print(f"  ✓ {len(fresh)} new jobs (skipped {len(all_listings)-len(fresh)} already in tracker)")

    if not fresh:
        console.print("[yellow]No new jobs found. Try different keywords or wait for new postings.[/yellow]")
        return

    # ── Step 3: Score jobs ───────────────────────────────────────────────────
    console.print("\n[3/4] Scoring relevance (loose threshold)...")
    accepted, skipped = score_jobs(fresh, profile)

    _print_scoring_summary(accepted, skipped)

    if not accepted:
        console.print("[yellow]No jobs passed the relevance threshold.[/yellow]")
        return

    # Create tracker records for all accepted jobs
    app_records = {
        scored.listing.job_id: tracker.create_application(scored)
        for scored in accepted
    }

    # ── Step 4: Execute applications ─────────────────────────────────────────
    console.print(f"\n[4/4] Applying to {len(accepted)} jobs...")
    try:
        await _execute_batch(accepted, profile, app_records)
    finally:
        # Close shared LinkedIn browser session
        from agents.linkedin_apply import close_browser
        await close_browser()

    # ── Summary ──────────────────────────────────────────────────────────────
    _print_final_summary()


# ── Execution batch ───────────────────────────────────────────────────────────

async def _execute_batch(
    scored_jobs: list[ScoredJob],
    profile: CandidateProfile,
    app_records: dict,
) -> None:
    for i, scored in enumerate(scored_jobs, 1):
        listing = scored.listing
        app_id  = app_records[listing.job_id].app_id
        console.print(f"\n  [{i}/{len(scored_jobs)}] {listing.title} @ {listing.company} (score={scored.relevance_score:.2f})")

        # 4a. Tailor resume
        try:
            resume_path = tailor_resume(profile, scored, RESUME_PATH)
            console.print(f"    ✓ Resume tailored")
        except Exception as e:
            console.print(f"    ⚠ Tailor failed: {e} — using base resume")
            resume_path = RESUME_PATH

        # 4b. Apply via LinkedIn Easy Apply
        if listing.easy_apply and listing.platform == "linkedin":
            try:
                from agents.linkedin_apply import apply_linkedin
                ok = await apply_linkedin(scored, profile, resume_path, app_id)
                if ok:
                    console.print("    ✓ LinkedIn Easy Apply submitted")
                    continue  # skip ATS if Easy Apply worked
            except Exception as e:
                console.print(f"    ⚠ Easy Apply failed: {e}")

        # 4c. Apply via ATS portal
        if listing.apply_url or listing.ats_type:
            try:
                from agents.ats_portal import apply_ats
                ok = await apply_ats(scored, profile, resume_path, app_id)
                if ok:
                    console.print(f"    ✓ ATS ({listing.ats_type or 'generic'}) submitted")
            except Exception as e:
                console.print(f"    ⚠ ATS portal failed: {e}")

        # 4d. Send outreach (regardless of application channel)
        if listing.recruiter_email or listing.recruiter_linkedin:
            try:
                send_outreach(scored, profile, app_id)
                console.print("    ✓ Outreach drafted/sent")
            except Exception as e:
                console.print(f"    ⚠ Outreach failed: {e}")


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_scoring_summary(accepted: list[ScoredJob], skipped: list[ScoredJob]) -> None:
    table = Table(title=f"Relevance Scores — {len(accepted)} accepted, {len(skipped)} skipped", show_lines=False)
    table.add_column("Score", style="cyan",  width=7)
    table.add_column("Title",               width=32)
    table.add_column("Company",             width=22)
    table.add_column("EasyApply", width=10)
    table.add_column("Top match reason",    width=35)

    for s in accepted[:20]:
        table.add_row(
            f"{s.relevance_score:.2f}",
            s.listing.title[:30],
            s.listing.company[:20],
            "✓" if s.listing.easy_apply else "—",
            s.match_reasons[0][:33] if s.match_reasons else "",
        )
    console.print(table)


def _print_final_summary() -> None:
    stats = tracker.stats()
    console.rule("[bold]Pipeline Complete[/bold]")
    console.print(f"\n  Total tracked applications: {stats['total']}")
    for status, count in stats["by_status"].items():
        console.print(f"  {status:20s}: {count}")

    followups = tracker.get_due_followups()
    if followups:
        console.print(f"\n  [yellow]⚑ {len(followups)} follow-ups due[/yellow]")
    console.print()


# ── Follow-up runner (run daily via cron or APScheduler) ─────────────────────

def run_followups(profile: CandidateProfile) -> None:
    """
    Check for due follow-ups and send reminder outreach.
    Call this separately on a schedule (e.g. daily cron).
    """
    from core.models import ApplicationRecord
    due = tracker.get_due_followups()
    if not due:
        console.print("[Followups] No follow-ups due today.")
        return

    console.print(f"[Followups] {len(due)} follow-ups due")
    for rec in due:
        job_data = rec.get("job", {})
        listing_data = job_data.get("listing", {})
        company = listing_data.get("company", "Unknown")
        title   = listing_data.get("title", "Unknown")
        console.print(f"  → {title} @ {company}")
        # You can extend this to auto-send a follow-up email


# ── CLI entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(run_pipeline())
