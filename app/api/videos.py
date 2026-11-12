from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import video_out
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Job, Script, Video
from app.schemas.content import VideoGenerateRequest, VideoRegenerateRequest
from app.services.pipeline import approve_video, enqueue, launch
from app.services.probes import MediaError

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _video(session: Session, video_id: str) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise NotFoundError("Video was not found.")
    return video


def _job(session: Session, video_id: str) -> Job | None:
    return session.scalar(select(Job).where(Job.video_id == video_id).order_by(Job.created_at.desc()))


@router.get("")
def list_videos(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(Video).order_by(Video.created_at.desc())).all()
    return [video_out(row, _job(session, row.id)) for row in rows]


@router.post("/generate", status_code=202)
def post_generate(body: VideoGenerateRequest, session: Session = Depends(get_db)) -> dict:
    script = session.get(Script, body.script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    video, started = enqueue(
        session,
        script,
        stability=body.stability,
        similarity=body.similarity,
        voice_id=body.voice_id,
    )
    session.commit()
    if started:
        launch(video.id)
    return video_out(video, _job(session, video.id)) | {"started": started}


@router.get("/{video_id}")
def get_video(video_id: str, session: Session = Depends(get_db)) -> dict:
    video = _video(session, video_id)
    return video_out(video, _job(session, video.id))


@router.post("/{video_id}/approve")
def post_approve(video_id: str, session: Session = Depends(get_db)) -> dict:
    video = approve_video(session, _video(session, video_id))
    return video_out(video, _job(session, video.id))


@router.post("/{video_id}/retry", status_code=202)
def post_retry(video_id: str, session: Session = Depends(get_db)) -> dict:
    video = _video(session, video_id)
    script = session.get(Script, video.script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    video, started = enqueue(
        session,
        script,
        stability=video.stability,
        similarity=video.similarity,
        voice_id=video.voice_id,
        force=True,
        component="all",
    )
    session.commit()
    if started:
        launch(video.id)
    return video_out(video, _job(session, video.id)) | {"started": started}


@router.post("/{video_id}/regenerate", status_code=202)
def post_regenerate(
    video_id: str,
    body: VideoRegenerateRequest,
    session: Session = Depends(get_db),
) -> dict:
    if body.component not in {"audio", "subtitles", "visuals", "render", "all"}:
        raise NotFoundError("Component must be audio, subtitles, visuals, render, or all.")
    video = _video(session, video_id)
    script = session.get(Script, video.script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    video, started = enqueue(
        session,
        script,
        stability=video.stability,
        similarity=video.similarity,
        voice_id=video.voice_id,
        force=True,
        component=body.component,
    )
    session.commit()
    if started:
        launch(video.id)
    return video_out(video, _job(session, video.id)) | {"started": started}


@router.get("/{video_id}/file")
def get_file(video_id: str, session: Session = Depends(get_db)) -> FileResponse:
    from pathlib import Path

    from app.config import get_settings

    video = _video(session, video_id)
    if not video.path:
        raise NotFoundError("The video file is not ready.")
    path = Path(video.path).resolve()
    root = get_settings().data_dir.resolve()
    if root not in path.parents:
        raise NotFoundError("File is outside the data directory.")
    if not path.is_file():
        raise NotFoundError("The video file is not ready.")
    try:
        return FileResponse(path, media_type="video/mp4", filename=path.name)
    except MediaError as exc:
        raise NotFoundError(str(exc)) from exc
