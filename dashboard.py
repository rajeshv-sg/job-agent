"""
dashboard.py
Terminal dashboard — shows your application pipeline status at a glance.

Run:  python dashboard.py
      python dashboard.py --export   # exports to CSV
"""
from __future__ import annotations
import argparse, csv, sys
from datetime import datetime
from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich.columns import Columns
from rich          import box

from core import tracker
from core.models import ApplicationStatus

console = Console()

STATUS_COLORS = {
    ApplicationStatus.DISCOVERED:    "dim",
    ApplicationStatus.SCORED:        "dim",
    ApplicationStatus.SKIPPED:       "red",
    ApplicationStatus.TAILORING:     "yellow",
    ApplicationStatus.APPLYING:      "yellow",
    ApplicationStatus.APPLIED:       "green",
    ApplicationStatus.OUTREACH_SENT: "cyan",
    ApplicationStatus.INTERVIEWING:  "bold green",
    ApplicationStatus.REJECTED:      "red",
    ApplicationStatus.OFFER:         "bold magenta",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true", help="Export to CSV")
    parser.add_argument("--status", help="Filter by status")
    args = parser.parse_args()

    records = tracker.get_all()
    if args.status:
        records = [r for r in records if r.get("status") == args.status]

    if args.export:
        _export_csv(records)
        return

    _print_dashboard(records)


def _print_dashboard(records: list[dict]) -> None:
    stats = tracker.stats()

    # ── Stat cards ──────────────────────────────────────────────────────────
    applied      = stats["by_status"].get(ApplicationStatus.APPLIED, 0)
    outreach     = stats["by_status"].get(ApplicationStatus.OUTREACH_SENT, 0)
    interviewing = stats["by_status"].get(ApplicationStatus.INTERVIEWING, 0)
    offers       = stats["by_status"].get(ApplicationStatus.OFFER, 0)
    rejected     = stats["by_status"].get(ApplicationStatus.REJECTED, 0)

    panels = [
        Panel(f"[bold green]{applied}[/bold green]\n[dim]Applied[/dim]",       expand=True),
        Panel(f"[bold cyan]{outreach}[/bold cyan]\n[dim]Outreach sent[/dim]",  expand=True),
        Panel(f"[bold yellow]{interviewing}[/bold yellow]\n[dim]Interviewing[/dim]", expand=True),
        Panel(f"[bold magenta]{offers}[/bold magenta]\n[dim]Offers[/dim]",     expand=True),
        Panel(f"[bold red]{rejected}[/bold red]\n[dim]Rejected[/dim]",          expand=True),
    ]
    console.print()
    console.print(Columns(panels))

    # ── Applications table ───────────────────────────────────────────────────
    table = Table(title=f"Applications ({len(records)} total)", box=box.SIMPLE, show_lines=False)
    table.add_column("Status",   width=16)
    table.add_column("Company",  width=22)
    table.add_column("Role",     width=30)
    table.add_column("Score",    width=7)
    table.add_column("Channels", width=20)
    table.add_column("Applied",  width=12)
    table.add_column("Follow-up",width=12)

    # Sort: active first, then by applied_at desc
    priority = {
        ApplicationStatus.INTERVIEWING: 0,
        ApplicationStatus.APPLIED: 1,
        ApplicationStatus.OUTREACH_SENT: 2,
        ApplicationStatus.OFFER: 3,
    }
    records_sorted = sorted(
        records,
        key=lambda r: (priority.get(r.get("status", ""), 9), r.get("applied_at", "") or ""),
        reverse=False,
    )

    for r in records_sorted:
        status  = r.get("status", "")
        color   = STATUS_COLORS.get(status, "white")
        job     = r.get("job", {})
        listing = job.get("listing", {})
        score   = job.get("relevance_score", 0)
        channels = ", ".join(
            c.replace("linkedin_easy_apply","LI-Easy")
             .replace("ats_portal","ATS")
             .replace("email_outreach","Email")
             .replace("linkedin_external_form","LI-Form")
            for c in r.get("channels_used", [])
        ) or "—"
        applied_at = (r.get("applied_at") or "")[:10] or "—"

        # Next follow-up
        followup_dates = r.get("followup_dates", [])
        now = datetime.utcnow().isoformat()
        due = next((d[:10] for d in followup_dates if d >= now), "—")

        table.add_row(
            f"[{color}]{status}[/{color}]",
            listing.get("company", "")[:20],
            listing.get("title",   "")[:28],
            f"{score:.2f}",
            channels[:18],
            applied_at,
            due,
        )

    console.print(table)

    # ── Follow-ups due ───────────────────────────────────────────────────────
    due_list = tracker.get_due_followups()
    if due_list:
        console.print(f"\n  [bold yellow]⚑ {len(due_list)} follow-up(s) due today[/bold yellow]")
        for rec in due_list:
            listing = rec.get("job", {}).get("listing", {})
            console.print(f"    • {listing.get('title','')} @ {listing.get('company','')}")


def _export_csv(records: list[dict]) -> None:
    path = "data/applications/export.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company","title","status","relevance_score",
            "channels","applied_at","easy_apply","location","url"
        ])
        writer.writeheader()
        for r in records:
            listing = r.get("job", {}).get("listing", {})
            writer.writerow({
                "company":         listing.get("company",""),
                "title":           listing.get("title",""),
                "status":          r.get("status",""),
                "relevance_score": r.get("job",{}).get("relevance_score",""),
                "channels":        "|".join(r.get("channels_used",[])),
                "applied_at":      (r.get("applied_at") or "")[:10],
                "easy_apply":      listing.get("easy_apply",""),
                "location":        listing.get("location",""),
                "url":             listing.get("url",""),
            })
    console.print(f"[green]Exported {len(records)} records to {path}[/green]")


if __name__ == "__main__":
    main()
