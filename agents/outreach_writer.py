"""
agents/outreach_writer.py
Agent 7 — Outreach Writer

For each job with a known recruiter / TA contact, generates and (optionally)
sends a personalised cold outreach email.

Strategy:
  1. Short, specific subject line referencing the role
  2. 3-paragraph body: connection → value prop → call to action
  3. No generic "I came across your posting" openers
  4. Mirrors JD language to show you've actually read it

Email is sent via SMTP (Gmail by default). Uses APPLY_MODE for gating:
  auto    → sends immediately
  confirm → shows draft, waits for keypress
  draft   → saves to data/outreach/, does not send
"""
from __future__ import annotations
import os, smtplib, json
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from core.llm import chat_json
from core.models import CandidateProfile, ScoredJob, ApplyChannel
from core import tracker

APPLY_MODE = os.getenv("APPLY_MODE", "confirm")
OUTREACH_DIR = Path("data/outreach")
OUTREACH_DIR.mkdir(parents=True, exist_ok=True)


SYSTEM = """You are a career coach writing short, genuine cold outreach emails for a job seeker.
Write in first person from the candidate's perspective.
Be direct, specific, and human — not corporate.
No generic openers. Reference something specific about the role or company.
Return JSON only."""


# ── Generate email ────────────────────────────────────────────────────────────

def generate_outreach(
    scored_job: ScoredJob,
    profile: CandidateProfile,
) -> dict[str, str]:
    """
    Returns {"subject": ..., "body": ..., "to_name": ..., "to_email": ...}
    """
    listing = scored_job.listing
    recruiter_name  = listing.recruiter_name  or "Hiring Team"
    recruiter_email = listing.recruiter_email or ""

    prompt = f"""
CANDIDATE:
Name: {profile.full_name}
Current title: {profile.current_title}
Years experience: {profile.years_experience}
Key skills: {', '.join(profile.skills[:12])}
Summary: {profile.summary}

ROLE:
Title: {listing.title}
Company: {listing.company}
Location: {listing.location}
JD excerpt (first 800 chars): {listing.description[:800]}

RECRUITER / TA CONTACT:
Name: {recruiter_name}
LinkedIn: {listing.recruiter_linkedin or "(unknown)"}

Match reasons for this candidate: {', '.join(scored_job.match_reasons)}

Write a cold outreach email FROM the candidate TO the recruiter.

{{
  "subject": "<specific, <10 words, references the role title>",
  "body": "<email body — 3 short paragraphs, ~150 words total. Plain text.
            Para 1: direct intro + role reference.
            Para 2: 2–3 concrete reasons why this candidate fits.
            Para 3: simple CTA — open to a call.
            Sign off with candidate's name.>",
  "to_name": "{recruiter_name}"
}}

Rules:
- Do NOT start with "I hope this email finds you well" or similar
- Do NOT say "I came across your job posting on LinkedIn"
- Be specific about the role and company — show you did research
- Keep it under 160 words
"""
    result = chat_json(SYSTEM, prompt, max_tokens=600, temperature=0.5)
    result["to_email"] = recruiter_email
    result["from_name"] = profile.full_name
    result["from_email"] = profile.email
    return result


# ── Send or save ──────────────────────────────────────────────────────────────

def send_outreach(
    scored_job: ScoredJob,
    profile: CandidateProfile,
    app_id: str,
) -> bool:
    """
    Generate and send (or draft) outreach for a job.
    Returns True if sent or drafted successfully.
    """
    listing = scored_job.listing

    if not listing.recruiter_email and APPLY_MODE != "draft":
        print(f"[Outreach] No recruiter email for {listing.company} — skipping outreach")
        return False

    email_data = generate_outreach(scored_job, profile)

    # Always save a local copy
    out_file = OUTREACH_DIR / f"{listing.company}_{listing.title}_{datetime.utcnow().strftime('%Y%m%d')}.txt".replace(" ", "_")
    out_file.write_text(
        f"To: {email_data.get('to_name')} <{email_data.get('to_email','')}>\n"
        f"From: {email_data['from_name']} <{email_data['from_email']}>\n"
        f"Subject: {email_data['subject']}\n\n"
        f"{email_data['body']}\n",
        encoding="utf-8"
    )
    print(f"[Outreach] Draft saved: {out_file}")

    if APPLY_MODE == "draft":
        print("[Outreach] DRAFT mode — email not sent")
        return True

    if APPLY_MODE == "confirm":
        _print_preview(email_data)

    if APPLY_MODE in ("auto", "confirm"):
        if not email_data.get("to_email"):
            print("[Outreach] ⚠ No email address — cannot send. Draft saved.")
            return True
        sent = _send_email(email_data)
        if sent:
            tracker.mark_outreach_sent(app_id, email_data["body"])
        return sent

    return False


def _send_email(email_data: dict) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        print("[Outreach] ✗ SMTP credentials not configured")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = email_data["subject"]
        msg["From"]    = f"{email_data['from_name']} <{smtp_user}>"
        msg["To"]      = email_data["to_email"]
        msg.attach(MIMEText(email_data["body"], "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        print(f"[Outreach] ✓ Sent to {email_data['to_email']}")
        return True

    except Exception as e:
        print(f"[Outreach] ✗ Send failed: {e}")
        return False


def _print_preview(email_data: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  OUTREACH EMAIL PREVIEW")
    print(f"  To:      {email_data.get('to_name')} <{email_data.get('to_email','no email')}>")
    print(f"  Subject: {email_data['subject']}")
    print(f"  ---")
    for line in email_data["body"].split("\n"):
        print(f"  {line}")
    print(f"{'='*60}")
    input("  Press ENTER to send, Ctrl+C to skip.\n")
