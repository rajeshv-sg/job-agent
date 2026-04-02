"""
agents/job_discovery.py
Agent 2 — Job Discovery  [Option B: unofficial linkedin-api]

Uses the unofficial linkedin-api library (reverse-engineered mobile API).
No API key needed — just your LinkedIn email + password in .env.

Safety measures built in:
  - Random sleep between requests (0.8-2.5s) to mimic human browsing
  - Session reuse (single login per run, not per search)
  - Per-combo caps to avoid hammering
  - Retries with exponential backoff on rate-limit errors
  - Skips already-tracked jobs before fetching full details
"""
from __future__ import annotations
import os, time, random, hashlib
from datetime import datetime

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

from core.models import CandidateProfile, JobListing
from core import tracker

load_dotenv()

# ── LinkedIn client singleton ─────────────────────────────────────────────────

_linkedin_client = None

def _get_client():
    global _linkedin_client
    if _linkedin_client is None:
        from linkedin_api import Linkedin
        email    = os.environ["LINKEDIN_EMAIL"]
        password = os.environ["LINKEDIN_PASSWORD"]
        print("[JobDiscovery] Logging in to LinkedIn...")
        _linkedin_client = Linkedin(email, password)
        print("[JobDiscovery] ✓ LinkedIn session established")
    return _linkedin_client


def _polite_sleep(min_s: float = 0.8, max_s: float = 2.5) -> None:
    """Random sleep to mimic human browsing pace."""
    time.sleep(random.uniform(min_s, max_s))


# ── Location → LinkedIn geo URN mapping ──────────────────────────────────────

GEO_IDS: dict[str, str] = {
    "Singapore":      "102454443",
    "Malaysia":       "101174742",
    "Indonesia":      "102478259",
    "Thailand":       "103365567",
    "Philippines":    "103121230",
    "Vietnam":        "104195383",
    "Hong Kong":      "104514075",
    "Australia":      "101452733",
    "New Zealand":    "105490917",
    "United Kingdom": "101165590",
    "United States":  "103644278",
    "Canada":         "101174742",
    "Germany":        "101282230",
    "India":          "102713980",
    "Remote":         None,
}

ATS_DOMAINS: dict[str, str] = {
    "myworkdayjobs.com":   "workday",
    "greenhouse.io":       "greenhouse",
    "lever.co":            "lever",
    "smartrecruiters.com": "smartrecruiters",
    "icims.com":           "icims",
    "taleo.net":           "taleo",
    "bamboohr.com":        "bamboohr",
    "jobvite.com":         "jobvite",
    "ashbyhq.com":         "ashby",
    "workable.com":        "workable",
    "recruitee.com":       "recruitee",
    "pinpointhq.com":      "pinpoint",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid(platform: str, raw_id: str) -> str:
    return hashlib.md5(f"{platform}:{raw_id}".encode()).hexdigest()[:16]


def _detect_ats(url: str) -> str | None:
    url = (url or "").lower()
    for domain, name in ATS_DOMAINS.items():
        if domain in url:
            return name
    return None


def _parse_ts(ts) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(ts) / 1000) if ts else None
    except Exception:
        return None


def _extract_recruiter(detail: dict) -> dict:
    try:
        hiring  = detail.get("hiringTeamMembersByRole", {})
        members = (
            hiring.get("recruiter", []) or
            hiring.get("hiringManager", []) or
            hiring.get("hr", [])
        )
        if members:
            m     = members[0]
            first = m.get("firstName", "")
            last  = m.get("lastName", "")
            pub   = m.get("publicIdentifier", "")
            return {
                "name":         f"{first} {last}".strip() or None,
                "linkedin_url": f"https://www.linkedin.com/in/{pub}/" if pub else None,
                "email":        m.get("email", None),
            }
    except Exception:
        pass
    return {}


def _is_easy_apply(apply_method: dict) -> bool:
    """
    LinkedIn Easy Apply = application stays inside LinkedIn.
    External = redirects to company ATS.
    Presence of 'OffSiteApply' means it's external.
    """
    method_str = str(apply_method)
    return "OffSiteApply" not in method_str and "offSiteApply" not in method_str


# ── Retrying search wrapper ───────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _search_role_location(client, role: str, location: str, limit: int) -> list[dict]:
    """Single role × location search with automatic retry."""
    kwargs: dict = dict(keywords=role, limit=limit)
    if location.lower() == "remote":
        kwargs["remote"] = ["2"]
    else:
        geo_id = GEO_IDS.get(location)
        if geo_id:
            kwargs["location_name"] = location
        else:
            kwargs["location_name"] = location   # try by name anyway
    return client.search_jobs(**kwargs)


def _fetch_job_detail(client, job_id: str) -> dict:
    _polite_sleep(0.8, 2.0)
    try:
        return client.get_job(job_id)
    except Exception as e:
        print(f"[JobDiscovery]   ⚠ Detail fetch failed for {job_id}: {e}")
        return {}


# ── Main search ───────────────────────────────────────────────────────────────

def search_linkedin(
    profile: CandidateProfile,
    max_jobs: int | None = None,
) -> list[JobListing]:
    max_jobs = max_jobs or int(os.getenv("MAX_JOBS_PER_RUN", 50))
    client   = _get_client()
    seen_uids: set[str]        = set()
    listings: list[JobListing] = []

    # Spread budget evenly across role × location combos
    combos    = max(1, len(profile.target_roles) * len(profile.target_locations))
    per_combo = max(5, min(25, max_jobs // combos + 1))

    exclude = {
        c.strip().lower()
        for c in os.getenv("EXCLUDE_COMPANIES", "").split(",")
        if c.strip()
    }

    for role in profile.target_roles:
        for location in profile.target_locations:
            if len(listings) >= max_jobs:
                break

            print(f"[JobDiscovery] Searching: '{role}' in '{location}' (limit={per_combo})")
            _polite_sleep(1.0, 2.5)   # pause before each new search

            try:
                results = _search_role_location(client, role, location, per_combo)
            except Exception as e:
                print(f"[JobDiscovery] ✗ Failed '{role}/{location}': {e}")
                continue

            print(f"[JobDiscovery]   {len(results)} results returned")

            for r in results:
                if len(listings) >= max_jobs:
                    break

                # Extract raw job ID from entityUrn  e.g. "urn:li:fsd_jobPosting:1234"
                raw_urn = r.get("entityUrn", "") or r.get("dashEntityUrn", "")
                raw_id  = raw_urn.split(":")[-1] if raw_urn else str(r.get("id", ""))
                if not raw_id:
                    continue

                uid = _uid("linkedin", raw_id)

                # Skip within-run duplicates
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)

                # Skip already-tracked jobs (no need to fetch details)
                if tracker.is_duplicate(uid, "linkedin"):
                    continue

                # Company name from search result (try multiple paths)
                company_name = (
                    r.get("companyDetails", {})
                     .get("com.linkedin.voyager.deco.jobs.web.shared.WebJobPostingCompany", {})
                     .get("companyResolutionResult", {})
                     .get("name", "")
                    or r.get("companyName", "")
                    or ""
                )

                # Skip excluded companies before hitting detail endpoint
                if company_name.lower() in exclude:
                    continue

                title = r.get("title", "")

                # Fetch full job detail
                detail       = _fetch_job_detail(client, raw_id)
                desc_obj     = detail.get("description", {})
                description  = desc_obj.get("text", "") if isinstance(desc_obj, dict) else ""
                apply_method = detail.get("applyMethod", {})

                # Enrich company name from detail if search result didn't have it
                if not company_name:
                    cd = detail.get("companyDetails", {})
                    company_name = (
                        cd.get("com.linkedin.voyager.deco.jobs.web.shared.WebCompactJobPostingCompany", {})
                          .get("companyResolutionResult", {})
                          .get("name", "")
                        or cd.get("com.linkedin.voyager.deco.jobs.web.shared.WebJobPostingCompany", {})
                              .get("companyResolutionResult", {})
                              .get("name", "")
                        or detail.get("companyName", "")
                        or "Unknown"
                    )
                easy_apply   = _is_easy_apply(apply_method)

                # External ATS URL
                apply_url = (
                    apply_method
                        .get("com.linkedin.voyager.jobs.OffSiteApply", {})
                        .get("companyApplyUrl", "")
                    or apply_method.get("companyApplyUrl", "")
                    or ""
                )
                ats_type  = _detect_ats(apply_url)
                recruiter = _extract_recruiter(detail)
                loc_str   = (
                    detail.get("formattedLocation", "")
                    or r.get("formattedLocation", "")
                    or location
                )

                listings.append(JobListing(
                    job_id             = uid,
                    platform           = "linkedin",
                    title              = title,
                    company            = company_name,
                    location           = loc_str,
                    is_remote          = (
                        location.lower() == "remote"
                        or "remote" in loc_str.lower()
                        or bool(detail.get("workRemoteAllowed", False))
                    ),
                    url                = f"https://www.linkedin.com/jobs/view/{raw_id}/",
                    easy_apply         = easy_apply,
                    description        = description,
                    posted_at          = _parse_ts(r.get("listedAt")),
                    ats_type           = ats_type,
                    apply_url          = apply_url or None,
                    recruiter_name     = recruiter.get("name"),
                    recruiter_email    = recruiter.get("email"),
                    recruiter_linkedin = recruiter.get("linkedin_url"),
                ))

                ea_label = "EasyApply" if easy_apply else (ats_type or "external")
                print(f"[JobDiscovery]   + {title} @ {company_name} [{ea_label}]")

    print(f"\n[JobDiscovery] ✓ {len(listings)} new jobs found on LinkedIn")
    return listings


# ── Multi-source aggregator ───────────────────────────────────────────────────

def search_all(profile: CandidateProfile) -> list[JobListing]:
    """
    Central entry point. Currently LinkedIn only.
    To add Indeed / Glassdoor etc., define search_indeed(profile)
    and call it here alongside search_linkedin().
    """
    jobs: list[JobListing] = []

    try:
        jobs += search_linkedin(profile)
    except Exception as e:
        print(f"[JobDiscovery] ✗ LinkedIn search failed: {e}")
        raise

    # Dedup across sources by company+title
    seen: set[str] = set()
    unique: list[JobListing] = []
    for j in jobs:
        key = f"{j.company.lower().strip()}::{j.title.lower().strip()}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    removed = len(jobs) - len(unique)
    if removed:
        print(f"[JobDiscovery] Removed {removed} cross-source duplicates")

    print(f"[JobDiscovery] ✓ {len(unique)} unique jobs ready for scoring")
    return unique
