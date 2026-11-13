"""Scheduler, library, subtitles, and the topic-specific production monitor."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError, NotFoundError
from app.models import (
    Brand,
    ContentOpportunity,
    Job,
    ProductionCost,
    ProductionJob,
    ProductionSchedule,
    Script,
    Topic,
    Video,
)
from app.services.assets import attach_scene_asset, library_assets, register_library_asset
from app.services.cost import month_spend, video_cost_rows
from app.services.pipeline import enqueue, launch
from app.services.schedule import (
    create_schedule,
    launch_schedule,
    scheduler_enabled,
    set_scheduler_enabled,
)
from app.services.subtitles import parse_srt, render_srt, validate_cue_coverage
from app.web.views import _redirect, _render, _script, _video

router = APIRouter()


def _schedule(session: Session, schedule_id: str) -> ProductionSchedule:
    row = session.get(ProductionSchedule, schedule_id)
    if row is None:
        raise AppError("Schedule was not found.")
    return row


def _under_data(path: Path) -> Path:
    root = get_settings().data_dir.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise NotFoundError("That file is outside the data directory.")
    return resolved


@router.get("/production")
def production_page(request: Request, session: Session = Depends(get_db)):
    schedules = session.scalars(select(ProductionSchedule).order_by(ProductionSchedule.name)).all()
    topics = session.scalars(select(Topic).order_by(Topic.created_at.desc())).all()
    render_jobs = session.scalars(
        select(Job)
        .where(Job.status.in_(["running", "failed", "interrupted"]))
        .order_by(Job.updated_at.desc())
        .limit(30)
    ).all()
    video_costs, unassigned = video_cost_rows(session)
    brands = {row.id: row for row in session.scalars(select(Brand)).all()}
    opportunities = session.scalars(
        select(ContentOpportunity).order_by(ContentOpportunity.updated_at.desc())
    ).all()
    pipeline_jobs = session.scalars(select(ProductionJob).order_by(ProductionJob.started_at.desc())).all()
    script_ids = [job.script_id for job in pipeline_jobs if job.script_id]
    video_ids = [job.video_id for job in pipeline_jobs if job.video_id]
    scripts = {}
    videos = {}
    if script_ids:
        found = session.scalars(select(Script).where(Script.id.in_(script_ids))).all()
        scripts = {row.id: row for row in found}
    if video_ids:
        videos = {row.id: row for row in session.scalars(select(Video).where(Video.id.in_(video_ids))).all()}
    pipeline_costs = session.scalars(select(ProductionCost).order_by(ProductionCost.created_at.desc())).all()
    return _render(
        request,
        "production.html",
        schedules=schedules,
        topics=topics,
        render_jobs=render_jobs,
        video_costs=video_costs,
        unassigned=float(unassigned),
        spend=float(month_spend(session)),
        poller_on=scheduler_enabled(session),
        brands=brands,
        opportunities=opportunities[:40],
        pipeline_jobs=pipeline_jobs[:20],
        pipeline_costs=pipeline_costs[:40],
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


@router.post("/production/scheduler")
def production_scheduler(enabled: str = Form("false"), session: Session = Depends(get_db)):
    set_scheduler_enabled(session, enabled == "true")
    note = "Scheduler poller is on." if enabled == "true" else "Scheduler poller is off."
    return _redirect("/production", message=note + " Scheduled runs never approve or export.")


@router.post("/production/schedules")
def production_create(
    name: str = Form(...),
    interval_minutes: int = Form(1440),
    language: str = Form("en"),
    topic_id: str = Form(""),
    enabled: str = Form(""),
    session: Session = Depends(get_db),
):
    try:
        create_schedule(
            session,
            name=name,
            interval_minutes=interval_minutes,
            language=language,
            topic_id=topic_id or None,
            enabled=enabled == "yes",
        )
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    return _redirect("/production", message="Schedule saved. It stops before approval.")


@router.post("/production/schedules/{schedule_id}/run")
def production_run(schedule_id: str, session: Session = Depends(get_db)):
    try:
        _schedule(session, schedule_id)
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    launch_schedule(schedule_id)
    return _redirect("/production", message="Schedule started. It will stop for manual approval.")


@router.post("/production/schedules/{schedule_id}/toggle")
def production_toggle(schedule_id: str, session: Session = Depends(get_db)):
    try:
        row = _schedule(session, schedule_id)
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    row.enabled = not row.enabled
    return _redirect("/production", message=f"{row.name} is {'on' if row.enabled else 'off'}.")


@router.get("/library")
def library_page(request: Request, session: Session = Depends(get_db)):
    return _render(request, "library.html", assets=library_assets(session))


@router.post("/library")
async def library_create(
    name: str = Form(...),
    kind: str = Form(...),
    source: str = Form(...),
    license_name: str = Form(...),
    upload: UploadFile = File(...),
    session: Session = Depends(get_db),
):
    data = await upload.read()
    try:
        register_library_asset(
            session,
            name=name,
            kind=kind,
            source=source,
            license_name=license_name,
            filename=upload.filename or "",
            data=data,
        )
    except AppError as exc:
        return _redirect("/library", error=exc.message)
    return _redirect("/library", message="Asset registered. Attach it from a script.")


@router.post("/scripts/{script_id}/assets")
def attach_asset(
    script_id: str,
    scene_index: int = Form(...),
    asset_id: str = Form(""),
    slot: str = Form("visual"),
    session: Session = Depends(get_db),
):
    try:
        attach_scene_asset(
            session,
            _script(session, script_id),
            scene_index=scene_index,
            asset_id=asset_id,
            slot=slot,
        )
    except AppError as exc:
        return _redirect(f"/scripts/{script_id}", error=exc.message)
    return _redirect(f"/scripts/{script_id}", message="Scene attachment saved.")


def _queue_subtitles(session: Session, video: Video, *, edited: bool) -> str:
    if video.status == "exported":
        raise AppError("This export is frozen. Edit the script to make a new version.")
    script = session.get(Script, video.script_id)
    if script is None:
        raise AppError("Script was not found.")
    video.subtitle_edited = edited
    session.commit()
    video, started = enqueue(
        session,
        script,
        stability=video.stability,
        similarity=video.similarity,
        voice_id=video.voice_id,
        force=True,
        component="subtitles",
    )
    session.commit()
    if started:
        launch(video.id)
    return video.id


@router.post("/videos/{video_id}/subtitles")
def save_subtitles(
    video_id: str,
    srt: str = Form(...),
    session: Session = Depends(get_db),
):
    video = _video(session, video_id)
    path = Path(video.subtitle_path) if video.subtitle_path else None
    if path is None:
        return _redirect(f"/videos/{video_id}", error="Render the video once before editing subtitles.")
    try:
        cues = parse_srt(srt)
        if video.duration_seconds:
            validate_cue_coverage(cues, video.duration_seconds)
        path.write_text(render_srt(cues), encoding="utf-8")
        target = _queue_subtitles(session, video, edited=True)
    except (AppError, ValueError) as exc:
        detail = exc.message if isinstance(exc, AppError) else str(exc)
        return _redirect(f"/videos/{video_id}", error=detail)
    return _redirect(f"/videos/{target}", message="Subtitles saved. Re-burning captions only.")


@router.post("/videos/{video_id}/subtitles/rebuild")
def rebuild_subtitles(video_id: str, session: Session = Depends(get_db)):
    video = _video(session, video_id)
    try:
        target = _queue_subtitles(session, video, edited=False)
    except AppError as exc:
        return _redirect(f"/videos/{video_id}", error=exc.message)
    return _redirect(f"/videos/{target}", message="Rebuilding subtitle cues from the narration.")
