# Job Application Agent

End-to-end AI agent pipeline for automated job searching, scoring, applying, and tracking.

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/rajeshv-sg/job-agent.git
cd job-agent

# 2. Install dependencies
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# 3. Configure environment
cp .env.example .env
# → Open .env and fill in your API key + LinkedIn credentials

# 4. Add your resume and profile
cp your_resume.pdf data/resumes/resume.pdf
cp data/resumes/ats_profile.example.json data/resumes/ats_profile.json
# → Open ats_profile.json and fill in your target roles, salary, notice period etc.

# 5. Run
python orchestrator.py
```

## Architecture

```
Input (resume + ATS profile + keywords)
    │
    ├── [1] Profile Builder     → normalised CandidateProfile
    ├── [2] Job Discovery       → LinkedIn search, Easy Apply flagging
    ├── [3] Relevance Scorer    → loose semantic match (threshold 0.35)
    │
    └── For each accepted job:
        ├── [4] Resume Tailor       → per-JD customised DOCX
        ├── [5] LinkedIn Easy Apply → Playwright automation
        ├── [6] ATS Portal Agent    → Workday / Greenhouse / Lever / etc.
        └── [7] Outreach Writer     → personalised recruiter email
            │
           [Tracker DB]  →  dashboard.py
```

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | What to put |
|---|---|
| `OPENROUTER_API_KEY` | Recommended — get free credits at openrouter.ai |
| `ANTHROPIC_API_KEY` | Alternative — get key at console.anthropic.com |
| `LINKEDIN_EMAIL` | Your normal LinkedIn email |
| `LINKEDIN_PASSWORD` | Your normal LinkedIn password |
| `TARGET_ROLES` | e.g. `Project Manager,Program Manager` |
| `TARGET_LOCATIONS` | e.g. `Singapore,Remote` |
| `APPLY_MODE` | Start with `confirm` — it pauses before every submit |
| `SMTP_USER / SMTP_PASSWORD` | Only needed for outreach emails (Gmail App Password) |

No LinkedIn API key, no developer account, no registration needed.
The library logs in using your regular credentials and reuses cookies after the first run.

### 3. Add your resume

```bash
cp your_resume.pdf data/resumes/resume.pdf
cp data/resumes/ats_profile.example.json data/resumes/ats_profile.json
```

Then edit `data/resumes/ats_profile.json` — set your target roles, locations, notice period, and any ATS screening answers (right to work, salary expectation etc.).

### 4. Run

```bash
# Full pipeline: discover → score → tailor → apply → track
python orchestrator.py

# View live dashboard
python dashboard.py

# Export all applications to CSV
python dashboard.py --export

# Filter dashboard by status
python dashboard.py --status applied
```

## Apply modes explained

| Mode | Behaviour |
|---|---|
| `confirm` | Fills every form, then **pauses and asks you to press Enter** before submitting. Recommended for first runs. |
| `auto` | Fully automated — submits without asking. Use once you trust the pipeline. |
| `draft` | Fills forms and saves outreach emails but **never submits anything**. Safe for testing. |

## Relevance scoring

The scorer uses Claude to semantically match each job description to your profile.

- Score **≥ 0.35** → accepted and queued for application (the default threshold)
- Score **< 0.35** → skipped (saved in tracker as `skipped` for reference)

The threshold is intentionally **low** — borderline roles are included, not filtered.
You always get a final review in `confirm` mode before anything is submitted.

To make it even broader: set `RELEVANCE_THRESHOLD=0.2` in `.env`.

## Supported ATS portals

| Portal | Status |
|---|---|
| LinkedIn Easy Apply | ✓ Full support |
| Workday | ✓ Full support |
| Greenhouse | ✓ Full support |
| Lever | ✓ Full support |
| SmartRecruiters | ✓ Full support |
| Ashby | ✓ Full support |
| Other / unknown | ✓ Generic fallback |

## Data files

```
data/
  resumes/
    resume.pdf              ← your base resume
    ats_profile.json        ← target roles, preferences, ATS answers
    tailored/               ← auto-generated per-job resumes
  applications/
    tracker.json            ← all application state (TinyDB)
    export.csv              ← optional CSV export
  outreach/                 ← saved outreach email drafts
```

## Scheduling (optional)

Run daily with cron:

```bash
# Search and apply every morning at 8am
0 8 * * * cd /path/to/job_agent && python orchestrator.py >> logs/run.log 2>&1

# Check follow-ups every morning at 9am
0 9 * * * cd /path/to/job_agent && python -c "
from orchestrator import run_followups
from agents.profile_builder import build_profile
profile = build_profile('data/resumes/resume.pdf', 'data/resumes/ats_profile.json')
run_followups(profile)
" >> logs/followups.log 2>&1
```

## Extending the pipeline

**Add a new job board** — create a `search_indeed()` function in `agents/job_discovery.py`
and call it from `search_all()`.

**Add a new ATS** — add a handler function in `agents/ats_portal.py` and register it in `_get_handler()`.

**Custom scoring weights** — edit the scoring prompt in `agents/relevance_scorer.py`.

## Notes

- LinkedIn may occasionally require CAPTCHA or 2FA — the browser stays visible so you can complete it.
- Easy Apply forms vary by company; the agent handles most standard question types.
- All data stays local — nothing is sent anywhere except LinkedIn/ATS portals and your SMTP server.
