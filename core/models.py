"""
core/models.py
Shared Pydantic models — the common language between all agents.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────────────────

class ApplicationStatus(str, Enum):
    DISCOVERED   = "discovered"
    SCORED       = "scored"
    SKIPPED      = "skipped"        # below relevance threshold
    TAILORING    = "tailoring"      # resume being customised
    APPLYING     = "applying"
    APPLIED      = "applied"
    OUTREACH_SENT = "outreach_sent"
    INTERVIEWING = "interviewing"
    REJECTED     = "rejected"
    OFFER        = "offer"

class ApplyChannel(str, Enum):
    LINKEDIN_EASY  = "linkedin_easy_apply"
    LINKEDIN_FORM  = "linkedin_external_form"
    ATS_PORTAL     = "ats_portal"
    EMAIL_OUTREACH = "email_outreach"

class ApplyMode(str, Enum):
    AUTO    = "auto"
    CONFIRM = "confirm"
    DRAFT   = "draft"


# ── Candidate profile (built once from resume + ATS data) ──────────────────

class CandidateProfile(BaseModel):
    full_name:       str
    email:           str
    phone:           Optional[str] = None
    location:        str
    linkedin_url:    Optional[str] = None
    summary:         str                        # 3–4 sentence bio
    years_experience: float
    current_title:   str
    skills:          list[str]                  # flat list, normalised
    industries:      list[str]
    education:       list[dict]                 # [{degree, institution, year}]
    experience:      list[dict]                 # [{title, company, start, end, bullets}]
    target_roles:    list[str]
    target_locations: list[str]
    salary_min:      Optional[int] = None
    salary_max:      Optional[int] = None
    preferred_channels: list[ApplyChannel] = [
        ApplyChannel.LINKEDIN_EASY,
        ApplyChannel.ATS_PORTAL,
        ApplyChannel.EMAIL_OUTREACH,
    ]
    # ATS-specific prefilled answers (common screening questions)
    ats_answers: dict[str, str] = Field(default_factory=dict)
    # e.g. {"authorised_to_work_sg": "Yes", "requires_sponsorship": "No",
    #        "notice_period": "30 days", "salary_expectation": "120000"}


# ── Raw job listing (from discovery) ───────────────────────────────────────

class JobListing(BaseModel):
    job_id:          str                        # platform-specific ID
    platform:        str                        # "linkedin", "indeed", etc.
    title:           str
    company:         str
    location:        str
    is_remote:       bool = False
    url:             str
    easy_apply:      bool = False               # LinkedIn Easy Apply available
    description:     str
    posted_at:       Optional[datetime] = None
    discovered_at:   datetime = Field(default_factory=datetime.utcnow)
    recruiter_name:  Optional[str] = None
    recruiter_email: Optional[str] = None
    recruiter_linkedin: Optional[str] = None
    ats_type:        Optional[str] = None       # "workday", "greenhouse", "lever", etc.
    apply_url:       Optional[str] = None       # direct apply link if different from url


# ── Scored job (after relevance agent) ─────────────────────────────────────

class ScoredJob(BaseModel):
    listing:         JobListing
    relevance_score: float                      # 0.0–1.0 (loosely thresholded)
    match_reasons:   list[str]                  # human-readable reasons
    gap_notes:       list[str]                  # missing skills / caveats
    tailored_summary: Optional[str] = None      # rewritten summary for this JD
    tailored_skills:  Optional[list[str]] = None


# ── Application record (persisted in tracker DB) ───────────────────────────

class ApplicationRecord(BaseModel):
    app_id:          str                        # uuid
    job:             ScoredJob
    status:          ApplicationStatus = ApplicationStatus.DISCOVERED
    channels_used:   list[ApplyChannel] = Field(default_factory=list)
    applied_at:      Optional[datetime] = None
    resume_version:  Optional[str] = None       # path to tailored resume file
    cover_note:      Optional[str] = None
    outreach_email:  Optional[str] = None
    followup_dates:  list[datetime] = Field(default_factory=list)
    notes:           str = ""
    last_updated:    datetime = Field(default_factory=datetime.utcnow)
