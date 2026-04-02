"""
agents/relevance_scorer.py
Agent 3 — Relevance Scorer

Scores each job listing against the candidate profile.
Intentionally LOOSE — the threshold is low (default 0.35) so borderline
roles are included rather than missed. The candidate can always skip in
the confirm step.

Scoring philosophy:
  - Any overlap in skills, title, or industry = positive signal
  - Missing requirements are noted but don't kill the score
  - Level mismatches get a small penalty, not a hard exclude
  - "Stretch" roles (one level up) are flagged but kept
"""
from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.llm import chat_json
from core.models import CandidateProfile, JobListing, ScoredJob


THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.35"))


SYSTEM = """You are a job relevance scorer helping a candidate decide which jobs to apply for.
Your job is to score how relevant a job posting is to the candidate's background.

Be GENEROUS and INCLUSIVE in your scoring. The candidate wants a wide net.
- If the role is even loosely related to their experience → score ≥ 0.4
- Don't penalise for missing "nice to have" skills
- Don't penalise for being slightly over or under-qualified
- Only give scores < 0.35 for genuinely irrelevant roles (totally different industry/function)
Return JSON only."""


def _score_one(listing: JobListing, profile: CandidateProfile) -> ScoredJob:
    prompt = f"""
CANDIDATE:
Name: {profile.full_name}
Current title: {profile.current_title}
Years experience: {profile.years_experience}
Skills: {', '.join(profile.skills[:30])}
Industries: {', '.join(profile.industries)}
Target roles: {', '.join(profile.target_roles)}

JOB POSTING:
Title: {listing.title}
Company: {listing.company}
Location: {listing.location}
Description (first 1200 chars):
{listing.description[:1200]}

Score this job's relevance to the candidate.

Return JSON:
{{
  "relevance_score": <float 0.0-1.0>,
  "match_reasons": ["reason 1", "reason 2"],   // up to 4 bullets
  "gap_notes": ["gap 1"],                       // skills/exp missing, if any
  "tailored_summary": "<2-sentence rewrite of candidate summary, using language from this JD>"
}}

Guidelines:
- 0.8–1.0 = strong match (title, skills, industry all align)
- 0.5–0.8 = good match (2 out of 3)
- 0.35–0.5 = loose match (some relevant overlap, worth a shot)
- < 0.35   = poor match (genuinely unrelated)
"""
    result = chat_json(SYSTEM, prompt, max_tokens=600, temperature=0.2)
    return ScoredJob(
        listing          = listing,
        relevance_score  = float(result.get("relevance_score", 0.5)),
        match_reasons    = result.get("match_reasons", []),
        gap_notes        = result.get("gap_notes", []),
        tailored_summary = result.get("tailored_summary"),
    )


def score_jobs(
    listings: list[JobListing],
    profile: CandidateProfile,
    threshold: float | None = None,
    max_workers: int = 5,
) -> tuple[list[ScoredJob], list[ScoredJob]]:
    """
    Score all listings in parallel.

    Returns:
        (accepted, skipped) — both lists of ScoredJob.
        accepted = score >= threshold
        skipped  = below threshold (saved for reference, not applied)
    """
    threshold = threshold if threshold is not None else THRESHOLD
    accepted: list[ScoredJob] = []
    skipped:  list[ScoredJob] = []

    print(f"[Scorer] Scoring {len(listings)} jobs (threshold={threshold}, workers={max_workers})")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_score_one, j, profile): j for j in listings}
        for i, future in enumerate(as_completed(futures), 1):
            listing = futures[future]
            try:
                scored = future.result()
                bucket = accepted if scored.relevance_score >= threshold else skipped
                bucket.append(scored)
                status = "✓" if scored.relevance_score >= threshold else "✗"
                print(
                    f"[Scorer] {status} [{i}/{len(listings)}] "
                    f"{listing.title} @ {listing.company} "
                    f"— score={scored.relevance_score:.2f}"
                )
            except Exception as e:
                print(f"[Scorer] ⚠ Error scoring {listing.title}: {e}")
                # On error, keep the job with a default mid score
                accepted.append(ScoredJob(
                    listing=listing,
                    relevance_score=0.5,
                    match_reasons=["scoring error — included by default"],
                    gap_notes=[],
                ))

    accepted.sort(key=lambda s: s.relevance_score, reverse=True)
    print(f"[Scorer] ✓ Accepted: {len(accepted)}  Skipped: {len(skipped)}")
    return accepted, skipped
