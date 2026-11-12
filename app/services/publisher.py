"""Local export records. This version never uploads to a platform."""

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, NotApprovedError, UploadDisabledError
from app.domain import PLATFORMS
from app.models import PublishedPost, PublishingJob, Script, Topic, Video

_REFUSAL = (
    "Automatic upload is disabled. This version only records an approved local export. "
    "YouTube, TikTok, and Instagram are not called."
)


def record_export(session: Session, video: Video, platform: str) -> PublishedPost:
    if platform not in PLATFORMS:
        raise AppError(f"Platform must be one of {', '.join(PLATFORMS)}.")
    if not video.approved:
        raise NotApprovedError("Export is allowed only after the video is approved.")
    source = Path(video.path)
    if not source.is_file():
        raise AppError("The rendered file is missing.")
    existing = session.scalar(
        select(PublishedPost).where(
            PublishedPost.video_id == video.id,
            PublishedPost.platform == platform,
        )
    )
    if existing is not None:
        return existing
    destination = get_settings().data_dir / "exports" / f"{video.id}.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    job = PublishingJob(
        video_id=video.id,
        platform=platform,
        status="recorded",
        export_path=str(destination),
        detail="Local export only. Nothing was uploaded.",
    )
    session.add(job)
    session.flush()
    post = PublishedPost(
        publishing_job_id=job.id,
        video_id=video.id,
        platform=platform,
        local_export_path=str(destination),
        note="Local export only. Nothing was uploaded.",
    )
    session.add(post)
    video.status = "exported"
    script = session.get(Script, video.script_id)
    if script is not None:
        topic = session.get(Topic, script.topic_id)
        if topic is not None:
            topic.status = "Published"
    session.flush()
    return post


def refuse_upload(session: Session, video: Video, platform: str) -> None:
    session.add(
        PublishingJob(
            video_id=video.id,
            platform=platform,
            status="refused",
            detail=_REFUSAL,
        )
    )
    session.commit()
    raise UploadDisabledError(_REFUSAL)
