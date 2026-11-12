"""Orchestrates a topic into an approved local export. Publishing never uploads."""

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, DuplicateTopicError, NotApprovedError, QualityGateError
from app.core.jobs import begin_job, log_event
from app.core.time import utc_now
from app.domain import PROFILE_FOR_FORMAT, PROFILE_SIZE
from app.models import Job, Script, Topic, Video
from app.schemas.content import TopicCreate
from app.services.cost import usage_scope
from app.services.publisher import record_export
from app.services.scripts import current_version, generate_script
from app.services.topics import create_topic
from app.services.video import render_video
from app.services.voice import default_voice, plan_engine, voice_hash

logger = logging.getLogger(__name__)
_inflight: set[str] = set()
_guard = threading.Lock()


def enqueue(
    session: Session,
    script: Script,
    *,
    stability: float = 0.5,
    similarity: float = 0.75,
    voice_id: str = "",
    force: bool = False,
    component: str = "all",
) -> tuple[Video, bool]:
    version = current_version(session, script)
    topic = session.get(Topic, script.topic_id)
    if topic is not None and topic.status == "Rejected":
        raise AppError("This topic is rejected. Change its status before producing a video.")
    settings = get_settings()
    engine = plan_engine(settings)
    voice = voice_id.strip() or default_voice(settings, engine, script.language)
    digest = voice_hash(engine, voice, script.language, stability, similarity)
    profile = PROFILE_FOR_FORMAT[script.format]
    width, height = PROFILE_SIZE[profile]
    key = f"render:{version.id}:{digest}:{profile}"
    existing_job = session.scalar(select(Job).where(Job.idempotency_key == key))
    existing_video = (
        session.get(Video, existing_job.video_id) if existing_job and existing_job.video_id else None
    )
    if existing_video is not None and existing_video.status == "exported" and (force or component != "all"):
        raise AppError(
            "This export is frozen. Edit the script to make a new version, then render that version."
        )
    if existing_job is not None and existing_job.status == "running" and existing_video is not None:
        return existing_video, False
    if (
        existing_job is not None
        and existing_job.status == "succeeded"
        and not force
        and component == "all"
        and existing_video is not None
    ):
        return existing_video, False
    if existing_video is None:
        video = Video(
            script_id=script.id,
            script_version_id=version.id,
            language=script.language,
            format=script.format,
            profile=profile,
            status="rendering",
            width=width,
            height=height,
            voice_id=voice,
            stability=stability,
            similarity=similarity,
            tts_provider=engine,
            settings_hash=digest,
        )
        session.add(video)
        session.flush()
        video.work_dir = str(settings.data_dir / "videos" / video.id)
    else:
        video = existing_video
        video.status = "rendering"
        video.approved = False
        video.approved_at = None
        video.error = ""
        video.script_version_id = version.id
        video.voice_id = voice
        video.stability = stability
        video.similarity = similarity
        video.tts_provider = engine
        video.settings_hash = digest
    job, started = begin_job(
        session,
        key=key,
        kind="render",
        video_id=video.id,
        component=component,
        payload={"script_id": script.id, "version_id": version.id, "profile": profile},
        force=force or component != "all",
    )
    job.video_id = video.id
    if started and job.attempts > 1:
        log_event(session, job, "retry", f"Retry attempt {job.attempts}.")
    if started and topic is not None and topic.status not in {"Rejected", "Published"}:
        topic.status = "In production"
    session.flush()
    return video, started


def run_render(session: Session, video_id: str) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise AppError(f"Video {video_id} was not found.")
    job = session.scalar(select(Job).where(Job.video_id == video_id).order_by(Job.created_at.desc()))
    try:
        with usage_scope(script_id=video.script_id, video_id=video.id):
            render_video(session, video, job)
        if job is not None:
            job.status = "succeeded"
            job.stage = "done"
            job.progress = 100
            job.error = "" if video.status == "ready" else video.error
        session.commit()
        return video
    except Exception as exc:
        session.rollback()
        video = session.get(Video, video_id)
        job = session.scalar(select(Job).where(Job.video_id == video_id).order_by(Job.created_at.desc()))
        message = str(exc)[:2000]
        if video is not None:
            video.status = "failed"
            video.error = message
        if job is not None:
            job.status = "failed"
            job.error = message
            log_event(session, job, "failed", message, level="error")
        session.commit()
        logger.exception("render failed", extra={"video_id": video_id})
        raise


def launch(video_id: str) -> None:
    with _guard:
        if video_id in _inflight:
            return
        _inflight.add(video_id)

    def _run() -> None:
        from app.core.database import get_sessionmaker

        session = get_sessionmaker()()
        try:
            run_render(session, video_id)
        except Exception:
            logger.exception("background render failed", extra={"video_id": video_id})
        finally:
            session.close()
            with _guard:
                _inflight.discard(video_id)

    threading.Thread(target=_run, name=f"render-{video_id[:8]}", daemon=True).start()


def approve_video(session: Session, video: Video) -> Video:
    report = video.quality_report or {}
    if video.status not in {"ready", "approved"} or not report.get("passed"):
        failed = [item["name"] for item in report.get("checks", []) if not item.get("passed")]
        detail = ", ".join(failed) if failed else video.status
        raise QualityGateError(f"Approve is available only after the quality gate passes ({detail}).")
    video.approved = True
    video.approved_at = utc_now()
    if video.status != "exported":
        video.status = "approved"
    session.flush()
    return video


def produce_short(
    session: Session,
    *,
    title: str,
    language: str,
    category: str,
    description: str = "",
    platform: str = "youtube_shorts",
    stability: float = 0.5,
    similarity: float = 0.75,
    voice_id: str = "",
    approve: bool = True,
    export: bool = True,
    reuse: bool = False,
) -> Video:
    try:
        topic = create_topic(
            session,
            TopicCreate(
                title=title,
                description=description,
                category=category,
                target_language=language,
                target_platform=platform,
            ),
        )
    except DuplicateTopicError as exc:
        if not (reuse and exc.exact):
            raise
        topic = session.get(Topic, exc.topic_id)
        if topic is None:
            raise
    script = generate_script(session, topic, language=language, video_format="short")
    video, started = enqueue(
        session,
        script,
        stability=stability,
        similarity=similarity,
        voice_id=voice_id,
    )
    session.commit()
    if started or video.status in {"rendering", "failed", "interrupted"}:
        video = run_render(session, video.id)
    if approve:
        if not (video.quality_report or {}).get("passed"):
            failed = [
                item["name"]
                for item in (video.quality_report or {}).get("checks", [])
                if not item.get("passed")
            ]
            raise QualityGateError("Quality gate failed, so the video was not approved: " + ", ".join(failed))
        approve_video(session, video)
    if export:
        if not video.approved:
            raise NotApprovedError("Export only happens after approval.")
        record_export(session, video, platform)
    session.commit()
    return video
