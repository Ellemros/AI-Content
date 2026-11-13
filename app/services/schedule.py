"""Scheduled production. A run stops when the video is ready for a person to approve."""

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models import ProductionSchedule, RuntimeFlag, Script, Topic, Video
from app.services.pipeline import enqueue, run_render
from app.services.scripts import generate_script

logger = logging.getLogger("app.production")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(schedule_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(schedule_id)
        if lock is None:
            lock = threading.Lock()
            _locks[schedule_id] = lock
        return lock


def scheduler_enabled(session: Session) -> bool:
    row = session.get(RuntimeFlag, "scheduler_enabled")
    if row is None:
        return get_settings().scheduler_enabled
    return row.value == "true"


def set_scheduler_enabled(session: Session, enabled: bool) -> None:
    row = session.get(RuntimeFlag, "scheduler_enabled")
    value = "true" if enabled else "false"
    if row is None:
        session.add(RuntimeFlag(key="scheduler_enabled", value=value))
    else:
        row.value = value
    session.flush()


def seed_scheduler_flag(session: Session) -> None:
    if session.get(RuntimeFlag, "scheduler_enabled") is None:
        set_scheduler_enabled(session, get_settings().scheduler_enabled)


def create_schedule(
    session: Session,
    *,
    name: str,
    interval_minutes: int,
    language: str,
    topic_id: str | None,
    enabled: bool,
    video_format: str = "short",
) -> ProductionSchedule:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise AppError("The schedule needs a name.")
    if not 1 <= interval_minutes <= 7 * 24 * 60:
        raise AppError("Interval must be between 1 minute and 7 days.")
    if language not in {"en", "cs"}:
        raise AppError("Language must be en or cs.")
    if video_format not in {"short", "long"}:
        raise AppError("Format must be short or long.")
    if topic_id:
        if session.get(Topic, topic_id) is None:
            raise AppError("That topic was not found.")
    else:
        topic_id = None
    if session.scalar(select(ProductionSchedule).where(ProductionSchedule.name == cleaned)):
        raise AppError("A schedule with that name already exists.")
    row = ProductionSchedule(
        name=cleaned[:120],
        enabled=enabled,
        interval_minutes=interval_minutes,
        topic_id=topic_id,
        language=language,
        video_format=video_format,
        next_run_at=utc_now(),
        last_status="idle",
    )
    session.add(row)
    session.flush()
    return row


def _advance(schedule: ProductionSchedule) -> None:
    schedule.last_run_at = utc_now()
    schedule.next_run_at = utc_now() + timedelta(minutes=schedule.interval_minutes)


def _waiting_topic(session: Session, schedule: ProductionSchedule) -> Topic | None:
    if schedule.topic_id:
        topic = session.get(Topic, schedule.topic_id)
        if topic is None:
            raise AppError("The scheduled topic was not found.")
        return topic
    topics = session.scalars(
        select(Topic)
        .where(Topic.status.in_(["Idea", "Ready"]))
        .order_by(Topic.priority.desc(), Topic.created_at)
    )
    for topic in topics:
        script = session.scalar(
            select(Script).where(
                Script.topic_id == topic.id,
                Script.language == schedule.language,
                Script.format == schedule.video_format,
            )
        )
        if script is None:
            return topic
        video = session.scalar(
            select(Video).where(
                Video.script_id == script.id,
                Video.status.in_(["rendering", "ready", "approved", "exported", "blocked"]),
            )
        )
        if video is None:
            return topic
    return None


def execute_schedule(session: Session, schedule: ProductionSchedule) -> ProductionSchedule:
    """Produce one video and stop. Approval and export stay manual."""
    schedule_id = schedule.id
    lock = _lock(schedule_id)
    if not lock.acquire(blocking=False):
        logger.info(
            "Schedule is already running.",
            extra={"stage": "schedule", "schedule_id": schedule_id, "attempt": 1},
        )
        return schedule
    try:
        schedule.last_status = "running"
        schedule.last_error = ""
        session.commit()
        try:
            topic = _waiting_topic(session, schedule)
            if topic is None:
                schedule.last_status = "skipped"
                schedule.last_error = "No topic is waiting."
                _advance(schedule)
                session.commit()
                logger.info(
                    "Schedule skipped. No topic is waiting.",
                    extra={"stage": "topic", "schedule_id": schedule_id, "attempt": 1},
                )
                return schedule
            logger.info(
                "Schedule using topic.",
                extra={"stage": "topic", "schedule_id": schedule_id, "topic_id": topic.id, "attempt": 1},
            )
            script = generate_script(
                session,
                topic,
                language=schedule.language,
                video_format=schedule.video_format,
            )
            video, started = enqueue(
                session,
                script,
                stability=schedule.stability,
                similarity=schedule.similarity,
                voice_id=schedule.voice_id,
            )
            session.commit()
            if video.status == "rendering" and not started:
                schedule.last_status = "skipped"
                schedule.last_error = "A render is already running."
                schedule.last_video_id = video.id
                _advance(schedule)
                session.commit()
                return schedule
            if started or video.status in {"failed", "interrupted"}:
                video = run_render(session, video.id)
            elif video.status in {"ready", "approved", "exported", "blocked"}:
                schedule.last_status = "skipped"
                schedule.last_video_id = video.id
                schedule.last_error = (
                    "Already rendered. It is waiting for manual approval."
                    if video.status == "ready"
                    else f"Existing video is {video.status}. Nothing was published."
                )
                _advance(schedule)
                session.commit()
                return schedule
            schedule.last_status = video.status
            schedule.last_video_id = video.id
            if video.status == "ready":
                schedule.last_error = "Stopped for manual approval. Nothing was exported."
            else:
                schedule.last_error = video.error or ""
            logger.info(
                "Schedule stopped before approval.",
                extra={
                    "stage": "approval",
                    "schedule_id": schedule_id,
                    "video_id": video.id,
                    "attempt": 1,
                },
            )
        except Exception as exc:
            logger.exception(
                "Schedule failed.",
                extra={"stage": "schedule", "schedule_id": schedule_id, "error": str(exc), "attempt": 1},
            )
            session.rollback()
            schedule = session.get(ProductionSchedule, schedule_id)
            if schedule is None:
                raise
            schedule.last_status = "failed"
            schedule.last_error = str(exc)[:2000]
        _advance(schedule)
        session.commit()
        return schedule
    finally:
        lock.release()


def launch_schedule(schedule_id: str) -> None:
    """Run one schedule off the request thread. It still stops before approval."""

    def _run() -> None:
        from app.core.database import session_scope

        try:
            with session_scope() as session:
                schedule = session.get(ProductionSchedule, schedule_id)
                if schedule is None:
                    return
                execute_schedule(session, schedule)
        except Exception:
            logger.exception(
                "Background schedule failed.",
                extra={"stage": "schedule", "schedule_id": schedule_id, "attempt": 1},
            )

    threading.Thread(target=_run, name=f"schedule-{schedule_id[:8]}", daemon=True).start()


def run_due(session: Session, now: datetime | None = None) -> list[str]:
    if not scheduler_enabled(session):
        return []
    moment = now or utc_now()
    due = list(
        session.scalars(
            select(ProductionSchedule).where(
                ProductionSchedule.enabled.is_(True),
                ProductionSchedule.next_run_at <= moment,
            )
        )
    )
    finished: list[str] = []
    for schedule in due:
        execute_schedule(session, schedule)
        finished.append(schedule.id)
    return finished
