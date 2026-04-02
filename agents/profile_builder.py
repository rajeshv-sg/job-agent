"""
agents/profile_builder.py
Agent 1 — Profile Builder

Reads your resume (PDF or DOCX) + an optional ATS JSON profile,
and produces a normalised CandidateProfile used by every other agent.

Usage:
    profile = build_profile("data/resumes/resume.pdf",
                            "data/resumes/ats_profile.json")
"""
from __future__ import annotations
import json, os
from pathlib import Path

from core.llm import chat_json
from core.models import CandidateProfile


# ── Resume text extraction ────────────────────────────────────────────────────

def _extract_text_pdf(path: str) -> str:
    import PyPDF2
    text = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


def _extract_text_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def _extract_resume_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _extract_text_pdf(path)
    elif ext in (".docx", ".doc"):
        return _extract_text_docx(path)
    else:
        # plain text fallback
        return Path(path).read_text(encoding="utf-8")


# ── ATS profile loader ────────────────────────────────────────────────────────

def _load_ats_profile(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# ── LLM parsing ───────────────────────────────────────────────────────────────

SYSTEM = """You are a resume parser. Extract structured information from the resume text
and any supplementary ATS profile data provided. Be thorough and accurate.
Return a single JSON object that matches the CandidateProfile schema exactly."""

SCHEMA_HINT = """
Return JSON with these fields:
{
  "full_name": string,
  "email": string,
  "phone": string | null,
  "location": string,
  "linkedin_url": string | null,
  "summary": string,              // 3–4 sentence professional summary
  "years_experience": number,
  "current_title": string,
  "skills": [string],             // flat list, normalised lowercase
  "industries": [string],
  "education": [{"degree": str, "institution": str, "year": int|null}],
  "experience": [
    {"title": str, "company": str, "start": str, "end": str, "bullets": [str]}
  ],
  "target_roles": [string],       // infer from most recent roles if not stated
  "target_locations": [string],   // infer from current location if not stated
  "salary_min": number | null,
  "salary_max": number | null,
  "ats_answers": {
    "authorised_to_work": "Yes",
    "requires_sponsorship": "No",
    "notice_period": "30 days",
    "salary_expectation": ""
  }
}
"""


def build_profile(
    resume_path: str,
    ats_profile_path: str | None = None,
) -> CandidateProfile:
    """
    Parse a resume file + optional ATS JSON into a CandidateProfile.
    This is called once at startup; the result is passed to all other agents.
    """
    print(f"[ProfileBuilder] Reading resume: {resume_path}")
    resume_text = _extract_resume_text(resume_path)
    ats_data    = _load_ats_profile(ats_profile_path)

    user_prompt = f"""
RESUME TEXT:
{resume_text}

ATS PROFILE DATA (supplementary — may be empty):
{json.dumps(ats_data, indent=2) if ats_data else "(none provided)"}

{SCHEMA_HINT}
"""
    print("[ProfileBuilder] Sending to Claude for parsing...")
    parsed = chat_json(SYSTEM, user_prompt, max_tokens=3000)

    # Merge in any explicit overrides from the ATS profile
    for key in ("target_roles", "target_locations", "ats_answers"):
        if key in ats_data:
            parsed[key] = ats_data[key]

    profile = CandidateProfile(**parsed)
    print(f"[ProfileBuilder] ✓ Profile built for: {profile.full_name} ({profile.current_title})")
    print(f"[ProfileBuilder]   Skills: {', '.join(profile.skills[:8])}{'...' if len(profile.skills) > 8 else ''}")
    return profile
