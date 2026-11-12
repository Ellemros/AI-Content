from pathlib import Path

import pytest
from app.core.errors import NotApprovedError, UploadDisabledError
from app.models import PublishingJob, Video
from app.schemas.content import TopicCreate
from app.services.publisher import record_export, refuse_upload
from app.services.scripts import current_version, generate_script
from app.services.topics import create_topic
from sqlalchemy import select


def _video(db, tmp_path: Path, *, approved: bool, title: str) -> Video:
    source = tmp_path / f"{title}.mp4"
    source.write_bytes(b"fake-mp4")
    topic = create_topic(db, TopicCreate(title=title))
    script = generate_script(db, topic, language="en", video_format="short")
    version = current_version(db, script)
    video = Video(
        script_id=script.id,
        script_version_id=version.id,
        language="en",
        format="short",
        profile="short_9_16",
        path=str(source),
        status="approved" if approved else "ready",
        approved=approved,
    )
    db.add(video)
    db.flush()
    return video


def test_export_requires_approval(db, tmp_path):
    video = _video(db, tmp_path, approved=False, title="Needs approval first")
    with pytest.raises(NotApprovedError):
        record_export(db, video, "youtube_shorts")


def test_approved_export_is_local_only(db, tmp_path):
    video = _video(db, tmp_path, approved=True, title="Approved local export")
    post = record_export(db, video, "tiktok")
    assert Path(post.local_export_path).is_file()
    assert post.note.startswith("Local export only")
    assert video.status == "exported"
    again = record_export(db, video, "tiktok")
    assert again.id == post.id


def test_upload_flag_is_refused(db, tmp_path):
    video = _video(db, tmp_path, approved=True, title="Upload must be refused")
    with pytest.raises(UploadDisabledError) as caught:
        refuse_upload(db, video, "instagram_reels")
    assert "disabled" in caught.value.message.lower()
    job = db.scalar(select(PublishingJob).where(PublishingJob.video_id == video.id))
    assert job is not None
    assert job.status == "refused"
