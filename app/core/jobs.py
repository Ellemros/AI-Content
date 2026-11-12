"""Idempotent jobs. A second request does not start the same render twice."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Job, Video


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


def set_progress(session: Session, job: Job | None, stage: str, progress: int) -> None:
    if job is None:
        return
    job.stage = stage
    job.progress = progress
    session.commit()


def mark_interrupted(session: Session) -> int:
    rows = list(session.scalars(select(Job).where(Job.status == "running")))
    for job in rows:
        job.status = "interrupted"
        job.error = "Process stopped before the job finished. Retry is safe."
        if job.video_id:
            video = session.get(Video, job.video_id)
            if video is not None and video.status == "rendering":
                video.status = "interrupted"
                video.error = job.error
    return len(rows)
