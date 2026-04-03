"""
core/tracker.py
Persistent application tracker backed by TinyDB (a lightweight JSON store).
No external DB needed — everything lives in data/applications/tracker.json.
"""
from __future__ import annotations
import os, uuid
from datetime import datetime, timedelta
from typing import Optional
from tinydb import TinyDB, Query
from tinydb.storages import JSONStorage

from core.models import ApplicationRecord, ApplicationStatus, ScoredJob, ApplyChannel


DB_PATH = os.getenv("DB_PATH", "data/applications/tracker.json")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_db: TinyDB | None = None

def _get_db() -> TinyDB:
    global _db
    if _db is None:
        _db = TinyDB(DB_PATH, storage=JSONStorage)
    return _db


# ── Write ────────────────────────────────────────────────────────────────────

def upsert_application(record: ApplicationRecord) -> ApplicationRecord:
    """Insert or update a record by app_id."""
    db = _get_db()
    App = Query()
    data = record.model_dump(mode="json")
    if db.contains(App.app_id == record.app_id):
        db.update(data, App.app_id == record.app_id)
    else:
        db.insert(data)
    return record


def create_application(job: ScoredJob) -> ApplicationRecord:
    """Create a fresh ApplicationRecord for a scored job."""
    followup_day1 = int(os.getenv("FOLLOWUP_DAY_1", 5))
    followup_day2 = int(os.getenv("FOLLOWUP_DAY_2", 14))
    now = datetime.utcnow()
    record = ApplicationRecord(
        app_id=str(uuid.uuid4()),
        job=job,
        followup_dates=[
            now + timedelta(days=followup_day1),
            now + timedelta(days=followup_day2),
        ],
    )
    return upsert_application(record)


def mark_applied(app_id: str, channel: ApplyChannel, resume_version: str | None = None) -> None:
    db = _get_db()
    App = Query()
    record = db.get(App.app_id == app_id)
    if not record:
        return
    channels = record.get("channels_used", [])
    if channel not in channels:
        channels.append(channel)
    db.update({
        "status": ApplicationStatus.APPLIED,
        "channels_used": channels,
        "applied_at": datetime.utcnow().isoformat(),
        "resume_version": resume_version,
        "last_updated": datetime.utcnow().isoformat(),
    }, App.app_id == app_id)


def mark_outreach_sent(app_id: str, email_body: str) -> None:
    db = _get_db()
    App = Query()
    db.update({
        "status": ApplicationStatus.OUTREACH_SENT,
        "outreach_email": email_body,
        "last_updated": datetime.utcnow().isoformat(),
    }, App.app_id == app_id)


def update_status(app_id: str, status: ApplicationStatus) -> None:
    db = _get_db()
    App = Query()
    db.update({
        "status": status,
        "last_updated": datetime.utcnow().isoformat(),
    }, App.app_id == app_id)


# ── Read ─────────────────────────────────────────────────────────────────────

def is_duplicate(job_id: str, platform: str) -> bool:
    """Returns True if we already have a record for this job."""
    db = _get_db()
    App = Query()
    return db.contains(
        (App.job.listing.job_id == job_id) &
        (App.job.listing.platform == platform)
    )


def get_all() -> list[dict]:
    return _get_db().all()


def get_by_status(status: ApplicationStatus) -> list[dict]:
    db = _get_db()
    App = Query()
    return db.search(App.status == status)


def get_due_followups() -> list[dict]:
    """Return applications whose next follow-up date is today or overdue."""
    now = datetime.utcnow().isoformat()
    db = _get_db()
    results = []
    for record in db.all():
        dates = record.get("followup_dates", [])
        status = record.get("status", "")
        if status in (ApplicationStatus.APPLIED, ApplicationStatus.OUTREACH_SENT):
            for d in dates:
                if d <= now:
                    results.append(record)
                    break
    return results


def stats() -> dict:
    all_records = get_all()
    from collections import Counter
    counts = Counter(r.get("status") for r in all_records)
    return {
        "total": len(all_records),
        "by_status": dict(counts),
    }
