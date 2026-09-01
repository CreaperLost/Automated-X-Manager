"""APScheduler bootstrap + scheduled-publish job handler.

Lives in the same process as the Streamlit app. Jobs are persisted in
data/scheduler.sqlite via SQLAlchemyJobStore so they survive an app
restart. On restart, the bootstrap function scans the `schedules`
table for any `pending` rows whose `fire_at` is in the past and
re-schedules them to fire immediately (with a 'fired_late' flag).

Concurrency: the BackgroundScheduler runs on a daemon thread. A
process-level lockfile (data/scheduler.lock) prevents double-start
when Streamlit reruns the script on every interaction.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from ..config import Settings
from ..store.repos import Database
from ..x.client import XApiError, XClient

LOCK_PATH_ATTR = "X_AUTO_SCHEDULER_LOCK"

_scheduler: BackgroundScheduler | None = None


def _lock_path(settings: Settings) -> Path:
    return settings.data_dir / "scheduler.lock"


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def start(settings: Settings, db: Database, x_client: XClient) -> BackgroundScheduler:
    """Idempotent bootstrap. Starts the scheduler exactly once per process."""
    global _scheduler
    if is_running():
        return _scheduler  # type: ignore[return-value]
    lock = _lock_path(settings)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # Process-level guard: only the first Streamlit rerun that runs in
    # this process should start the scheduler. The lock file is
    # best-effort on Windows (file locking semantics differ); APScheduler
    # itself is thread-safe so the bigger risk is double-scheduled jobs.
    if lock.exists():
        # Heuristic: if the lock is older than 60s, assume the previous
        # process is gone and take over.
        age = datetime.now().timestamp() - lock.stat().st_mtime
        if age < 60:
            _scheduler = _build_scheduler(settings, db, x_client)
            return _scheduler
    lock.write_text(str(os.getpid()))
    _scheduler = _build_scheduler(settings, db, x_client)
    return _scheduler


def _build_scheduler(
    settings: Settings, db: Database, x_client: XClient
) -> BackgroundScheduler:
    sched = BackgroundScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=f"sqlite:///{settings.data_dir / 'scheduler.sqlite'}"
            )
        },
        timezone="UTC",
    )
    sched.start()
    _reap_late_pending(settings, db, sched)
    return sched


def schedule_draft(
    settings: Settings,
    db: Database,
    x_client: XClient,
    draft_id: int,
    fire_at: datetime,
) -> int:
    """Add a one-shot job for the given draft. Returns the schedule id."""
    sched = start(settings, db, x_client)
    schedule_id = db.create_schedule(draft_id, fire_at)
    # If the fire_at is in the past (e.g. the user picked 'now' by
    # accident), fire immediately.
    if fire_at <= datetime.now():
        _reap_late_pending(settings, db, sched)
    else:
        sched.add_job(
            _fire_scheduled,
            "date",
            run_date=fire_at,
            args=[schedule_id, draft_id],
            id=f"schedule_{schedule_id}",
            misfire_grace_time=300,
            coalesce=True,
            replace_existing=True,
        )
    # Mark the draft as 'scheduled'.
    draft = db.get_draft(draft_id)
    if draft is not None:
        draft.status = "scheduled"
        draft.scheduled_at = fire_at
        db.update_draft(draft)
    return schedule_id


def reschedule_draft(
    settings: Settings, db: Database, x_client: XClient,
    schedule_id: int, draft_id: int, fire_at: datetime,
) -> None:
    """Move an existing pending schedule and its APScheduler job."""
    sched = start(settings, db, x_client)
    db.reschedule(schedule_id, fire_at)
    sched.add_job(
        _fire_scheduled, "date", run_date=fire_at,
        args=[schedule_id, draft_id], id=f"schedule_{schedule_id}",
        misfire_grace_time=300, coalesce=True, replace_existing=True,
    )
    draft = db.get_draft(draft_id)
    if draft:
        draft.status = "scheduled"
        draft.scheduled_at = fire_at
        db.update_draft(draft)


def cancel_scheduled_draft(
    settings: Settings, db: Database, x_client: XClient,
    schedule_id: int, draft_id: int,
) -> None:
    """Cancel a pending schedule and return its draft to the queue."""
    sched = start(settings, db, x_client)
    for job_id in (f"schedule_{schedule_id}", f"late_{schedule_id}"):
        job = sched.get_job(job_id)
        if job is not None:
            sched.remove_job(job_id)
    db.cancel_schedule(schedule_id)
    draft = db.get_draft(draft_id)
    if draft and draft.status == "scheduled":
        draft.status = "draft"
        draft.scheduled_at = None
        db.update_draft(draft)


def _reap_late_pending(
    settings: Settings, db: Database, sched: BackgroundScheduler
) -> None:
    """On startup, fire any pending schedule whose time has already passed."""
    now = datetime.now()
    for s in db.list_schedules(status="pending", limit=200):
        if s.fire_at <= now:
            sched.add_job(
                _fire_scheduled,
                "date",
                run_date=now,
                args=[s.id, s.draft_id],
                id=f"late_{s.id}",
                misfire_grace_time=300,
                coalesce=True,
                replace_existing=True,
            )
            db.mark_schedule_pending_late(s.id)


def _fire_scheduled(schedule_id: int, draft_id: int) -> None:
    """Job handler. Runs in the APScheduler worker thread."""
    settings = _settings_or_default()
    db = Database(settings.data_dir / "state.db")
    try:
        draft = db.get_draft(draft_id)
        if draft is None:
            db.mark_schedule_failed(schedule_id, "draft not found")
            return
        if draft.status in ("posted", "failed"):
            db.mark_schedule_fired(schedule_id)
            return
        # Build a fresh async client per fire. The shared publish
        # module does the upload → main → reply → update → log dance.
        async def _do() -> None:
            from ..x.publish import publish_draft

            async with XClient(settings) as x_client:
                await publish_draft(settings, db, x_client, draft)
            db.mark_schedule_fired(schedule_id)
        asyncio.run(_do())
    except XApiError as exc:
        db.mark_schedule_failed(schedule_id, f"{exc.status}: {exc.detail[:200]}")
        db.log_post(draft_id, "fire_scheduled", None, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001
        db.mark_schedule_failed(schedule_id, str(exc)[:200])
        db.log_post(draft_id, "fire_scheduled", None, "failed", str(exc))
    finally:
        db.close()


def _settings_or_default() -> Settings:
    from ..config import get_settings
    return get_settings()
