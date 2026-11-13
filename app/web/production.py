"""Production monitor. Videos can be previewed here. Nothing on this page publishes."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Brand, ContentOpportunity, ProductionCost, ProductionJob, Script, Video
from app.web.views import _render

router = APIRouter()


def _under_data(path: Path) -> Path:
    root = get_settings().data_dir.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise NotFoundError("That file is outside the data directory.")
    return resolved


@router.get("/production")
def production_page(request: Request, session: Session = Depends(get_db)):
    brands = {row.id: row for row in session.scalars(select(Brand)).all()}
    opportunities = session.scalars(
        select(ContentOpportunity).order_by(ContentOpportunity.updated_at.desc())
    ).all()
    jobs = session.scalars(select(ProductionJob).order_by(ProductionJob.started_at.desc())).all()
    script_ids = [job.script_id for job in jobs if job.script_id]
    video_ids = [job.video_id for job in jobs if job.video_id]
    scripts = {}
    videos = {}
    if script_ids:
        found = session.scalars(select(Script).where(Script.id.in_(script_ids))).all()
        scripts = {row.id: row for row in found}
    if video_ids:
        videos = {row.id: row for row in session.scalars(select(Video).where(Video.id.in_(video_ids))).all()}
    costs = session.scalars(select(ProductionCost).order_by(ProductionCost.created_at.desc())).all()
    return _render(
        request,
        "production.html",
        brands=brands,
        opportunities=opportunities[:40],
        jobs=jobs[:20],
        costs=costs[:40],
        scripts=scripts,
        videos=videos,
        settings=get_settings(),
    )


@router.get("/production/videos/{video_id}")
def production_video(video_id: str, session: Session = Depends(get_db)):
    video = session.get(Video, video_id)
    if video is None or not video.path:
        raise NotFoundError("Video was not found.")
    path = _under_data(Path(video.path))
    if not path.is_file():
        raise NotFoundError("Video file is missing.")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
