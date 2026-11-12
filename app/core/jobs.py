"""Idempotent jobs. A second request does not start the same render twice."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Job, JobEvent, ProductionSchedule, Video

logger = logging.getLogger("app.production")


def begin_job(
    session: Session,
    *,
    key: str,
    kind: str,
    video_id: str | None,
    component: str,
    payload: dict,
    force: bool,
) -> tuple[Job, bool]:
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing is None:
        job = Job(
            idempotency_key=key,
            kind=kind,
            status="running",
            video_id=video_id,
            component=component,
            stage="queued",
            progress=0,
            attempts=1,
            payload=payload,
        )
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            existing = session.scalar(select(Job).where(Job.idempotency_key == key))
            if existing is None:
                raise
            return existing, False
        return job, True
    if existing.status == "running":
        return existing, False
    if existing.status == "succeeded" and not force:
        return existing, False
    existing.status = "running"
    existing.video_id = video_id or existing.video_id
    existing.component = component
    existing.stage = "queued"
    existing.progress = 0
    existing.attempts += 1
    existing.error = ""
    existing.payload = payload
    session.flush()
    return existing, True


def log_event(
    session: Session,
    job: Job | None,
    stage: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    extra: dict[str, object] = {"stage": stage, "attempt": job.attempts if job is not None else 1}
    if job is not None:
        extra["video_id"] = job.video_id
        extra["job_id"] = job.id
        session.add(
            JobEvent(
                job_id=job.id,
                attempt=job.attempts,
                stage=stage,
                level=level,
                message=message[:2000],
            )
        )
    log = logger.error if level == "error" else logger.info
    log(message, extra=extra)


def set_progress(session: Session, job: Job | None, stage: str, progress: int) -> None:
    if job is None:
        return
    job.stage = stage
    job.progress = progress
    log_event(session, job, stage, f"Step {stage} ({progress}%).")
    session.commit()


def mark_interrupted(session: Session) -> int:
    rows = list(session.scalars(select(Job).where(Job.status == "running")))
    for job in rows:
        job.status = "interrupted"
        job.error = "Process stopped before the job finished. Retry is safe."
        log_event(session, job, job.stage or "interrupted", job.error, level="error")
        if job.video_id:
            video = session.get(Video, job.video_id)
            if video is not None and video.status == "rendering":
                video.status = "interrupted"
                video.error = job.error
    for schedule in session.scalars(
        select(ProductionSchedule).where(ProductionSchedule.last_status == "running")
    ):
        schedule.last_status = "interrupted"
        schedule.last_error = "Process stopped before the scheduled run finished. Run it again."
    return len(rows)
