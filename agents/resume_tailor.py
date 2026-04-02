"""
agents/resume_tailor.py
Agent 4 — Resume Tailor

For each accepted job, produces a lightly customised version of the resume:
  1. Rewrites the professional summary to mirror the JD's language
  2. Reorders / highlights the most relevant skills
  3. Optionally tweaks the top job's bullet points to echo the JD keywords

Saves each tailored resume as a DOCX in data/resumes/tailored/.
Returns the file path for the tracker.
"""
from __future__ import annotations
import os, re
from pathlib import Path
from docx import Document
from docx.shared import Pt

from core.llm import chat_json, chat
from core.models import CandidateProfile, ScoredJob


OUTPUT_DIR = Path("data/resumes/tailored")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


SYSTEM_TAILOR = """You are an expert resume writer helping a candidate tailor their resume
for a specific job posting. Make targeted edits — don't over-rewrite.
Use keywords from the JD naturally. Keep it honest and accurate.
Return JSON only."""


def _safe_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", text)[:40]


def tailor_resume(
    profile: CandidateProfile,
    scored_job: ScoredJob,
    base_resume_path: str,
) -> str:
    """
    Produce a tailored DOCX for the given job.
    Returns the path to the saved file.
    """
    jd = scored_job.listing
    filename = f"{_safe_filename(jd.company)}_{_safe_filename(jd.title)}.docx"
    output_path = OUTPUT_DIR / filename

    # Skip re-tailoring if file already exists
    if output_path.exists():
        return str(output_path)

    print(f"[ResumeTailor] Tailoring for {jd.title} @ {jd.company}")

    prompt = f"""
CANDIDATE PROFILE:
Summary: {profile.summary}
Skills: {', '.join(profile.skills)}
Most recent role bullets:
{chr(10).join('- ' + b for b in (profile.experience[0]['bullets'] if profile.experience else [])[:5])}

JOB DESCRIPTION (first 1500 chars):
{jd.description[:1500]}

Match reasons: {', '.join(scored_job.match_reasons)}
Gap notes: {', '.join(scored_job.gap_notes)}

Produce tailored content:
{{
  "summary": "<rewritten 3-sentence summary using JD language and keywords>",
  "skills_ordered": ["skill1", "skill2", ...],  // reorder to put most JD-relevant first (max 16)
  "top_role_bullets": ["bullet1", "bullet2", ...]  // optionally tweak top 3 bullets to echo JD
}}

Rules:
- Keep everything factually accurate — don't add skills the candidate doesn't have
- Mirror JD terminology naturally (e.g. if JD says "stakeholder alignment", use that phrase)
- Summary should be 3 sentences max
"""
    result = chat_json(SYSTEM_TAILOR, prompt, max_tokens=1000)

    # Build the DOCX
    try:
        doc = Document(base_resume_path)
    except Exception:
        doc = Document()

    _apply_tailoring(doc, profile, result)
    doc.save(str(output_path))
    print(f"[ResumeTailor] ✓ Saved: {output_path}")
    return str(output_path)


def _apply_tailoring(doc: Document, profile: CandidateProfile, tailoring: dict) -> None:
    """
    Walk the document and replace the summary paragraph and skills section.
    This is a best-effort DOCX edit — structure varies by template.
    """
    new_summary = tailoring.get("summary", "")
    new_skills  = tailoring.get("skills_ordered", [])

    summary_replaced = False
    skills_replaced  = False

    for para in doc.paragraphs:
        text = para.text.strip()

        # Replace summary: first substantial paragraph after the name header
        if not summary_replaced and new_summary and len(text) > 60 and _looks_like_summary(text, profile):
            for run in para.runs:
                run.text = ""
            if para.runs:
                para.runs[0].text = new_summary
            else:
                para.add_run(new_summary)
            summary_replaced = True

        # Replace skills section: paragraph that contains most of the current skills
        if not skills_replaced and new_skills and _looks_like_skills(text, profile):
            for run in para.runs:
                run.text = ""
            if para.runs:
                para.runs[0].text = " • ".join(new_skills)
            else:
                para.add_run(" • ".join(new_skills))
            skills_replaced = True

    # If we couldn't find the sections, append a note at the end
    if not summary_replaced or not skills_replaced:
        doc.add_paragraph("")
        if not summary_replaced and new_summary:
            p = doc.add_paragraph(f"[TAILORED SUMMARY]: {new_summary}")
            p.runs[0].font.size = Pt(10)
        if not skills_replaced and new_skills:
            p = doc.add_paragraph(f"[TAILORED SKILLS]: {' • '.join(new_skills)}")
            p.runs[0].font.size = Pt(10)


def _looks_like_summary(text: str, profile: CandidateProfile) -> bool:
    """Heuristic: is this paragraph the professional summary?"""
    words = set(text.lower().split())
    name_parts = set(profile.full_name.lower().split())
    # Avoid replacing the name line
    if name_parts & words and len(text) < 60:
        return False
    return len(text) > 80


def _looks_like_skills(text: str, profile: CandidateProfile) -> bool:
    """Heuristic: does this paragraph look like a skills list?"""
    skill_hits = sum(1 for s in profile.skills if s.lower() in text.lower())
    return skill_hits >= 3 or ("•" in text and skill_hits >= 1)
