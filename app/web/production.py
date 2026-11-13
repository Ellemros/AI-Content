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
from app.services.automation import (
    activate_brand,
    attach_feed,
    automation_enabled,
    create_brand,
    launch_brand,
    opportunity_title,
    pause_brand,
    queue_length,
    recent_jobs,
    set_automation_enabled,
    update_brand_limits,
    videos_today,
)
from app.services.cost import month_spend, video_cost_rows
from app.services.pipeline import enqueue, launch
from app.services.production.budget import daily_spend, monthly_spend
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
    operations = []
    for brand in session.scalars(select(Brand).order_by(Brand.name)).all():
        operations.append(
            {
                "brand": brand,
                "queue": queue_length(session, brand),
                "videos_today": videos_today(session, brand.id),
                "spend_today": daily_spend(session, brand_id=brand.id),
                "spend_month": monthly_spend(session, brand_id=brand.id),
                "running": recent_jobs(session, brand.id, "running"),
                "failed": recent_jobs(session, brand.id, "failed"),
                "completed": recent_jobs(session, brand.id, "succeeded", limit=6),
            }
        )
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
        operations=operations,
        automation_on=automation_enabled(session),
        production_daily=daily_spend(session),
        production_month=monthly_spend(session),
        titles={
            job.opportunity_id: opportunity_title(session, job.opportunity_id)
            for job in pipeline_jobs
            if job.opportunity_id
        },
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


def _brand(session: Session, brand_id: str) -> Brand:
    brand = session.get(Brand, brand_id)
    if brand is None:
        raise NotFoundError("Brand was not found.")
    return brand


@router.post("/production/automation")
def production_automation(enabled: str = Form("false"), session: Session = Depends(get_db)):
    set_automation_enabled(session, enabled == "true")
    if enabled == "true":
        note = "Automation is on. Active brands produce on their schedule. Nothing is published."
    else:
        note = "Emergency stop is on. No new brand video will start."
    return _redirect("/production", message=note)


@router.post("/production/brands")
def production_create_brand(
    name: str = Form(...),
    slug: str = Form(""),
    niche: str = Form(""),
    audience: str = Form(""),
    language: str = Form("en"),
    interval_minutes: int = Form(1440),
    max_videos_per_day: int = Form(1),
    max_spend_usd_per_day: str = Form("5"),
    video_spend_cap_usd: str = Form(""),
    max_attempts: int = Form(2),
    terms: str = Form(""),
    editorial_minimum: str = Form(""),
    feed_url: str = Form(""),
    feed_name: str = Form(""),
    activate: str = Form(""),
    short_format: str = Form("yes"),
    long_format: str = Form(""),
    block_medical: str = Form(""),
    block_financial: str = Form(""),
    editorial_gate: str = Form(""),
    session: Session = Depends(get_db),
):
    formats = []
    if short_format == "yes":
        formats.append("short")
    if long_format == "yes":
        formats.append("long")
    try:
        brand = create_brand(
            session,
            name=name,
            slug=slug,
            niche=niche,
            audience=audience,
            language=language,
            formats=formats or ["short"],
            interval_minutes=interval_minutes,
            max_videos_per_day=max_videos_per_day,
            max_spend_usd_per_day=max_spend_usd_per_day,
            video_spend_cap_usd=video_spend_cap_usd,
            max_attempts=max_attempts,
            terms=terms,
            block_medical=block_medical == "yes",
            block_financial=block_financial == "yes",
            editorial_gate=editorial_gate == "yes",
            editorial_minimum=editorial_minimum,
            feed_url=feed_url,
            feed_name=feed_name,
            activate=activate == "yes",
        )
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    note = f"{brand.name} saved."
    if brand.automation_enabled:
        note += " It will run on the next poll. Nothing is published."
    return _redirect("/production", message=note)


@router.post("/production/brands/{brand_id}/limits")
def production_brand_limits(
    brand_id: str,
    niche: str = Form(""),
    audience: str = Form(""),
    language: str = Form("en"),
    interval_minutes: int = Form(1440),
    max_videos_per_day: int = Form(1),
    max_spend_usd_per_day: str = Form("5"),
    video_spend_cap_usd: str = Form(""),
    max_attempts: int = Form(2),
    editorial_minimum: str = Form(""),
    short_format: str = Form(""),
    long_format: str = Form(""),
    editorial_gate: str = Form(""),
    voice_id: str = Form(""),
    session: Session = Depends(get_db),
):
    formats = []
    if short_format == "yes":
        formats.append("short")
    if long_format == "yes":
        formats.append("long")
    try:
        brand = _brand(session, brand_id)
        update_brand_limits(
            session,
            brand,
            interval_minutes=interval_minutes,
            max_videos_per_day=max_videos_per_day,
            max_spend_usd_per_day=max_spend_usd_per_day,
            video_spend_cap_usd=video_spend_cap_usd,
            max_attempts=max_attempts,
            formats=formats,
            editorial_gate=editorial_gate == "yes",
            editorial_minimum=editorial_minimum,
            niche=niche,
            audience=audience,
            language=language,
            voice_id=voice_id,
        )
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    return _redirect("/production", message="Brand limits saved. Nothing is published.")


@router.post("/production/brands/{brand_id}/feed")
def production_brand_feed(
    brand_id: str,
    feed_url: str = Form(...),
    feed_name: str = Form(""),
    session: Session = Depends(get_db),
):
    try:
        brand = _brand(session, brand_id)
        attach_feed(session, brand, url=feed_url, name=feed_name)
    except AppError as exc:
        return _redirect("/production", error=exc.message)
    return _redirect("/production", message="Feed linked to this brand. No account was created.")


@router.post("/production/brands/{brand_id}/pause")
def production_brand_pause(brand_id: str, session: Session = Depends(get_db)):
    brand = _brand(session, brand_id)
    pause_brand(session, brand)
    return _redirect("/production", message=f"{brand.name} is paused. No new video will start.")


@router.post("/production/brands/{brand_id}/resume")
def production_brand_resume(brand_id: str, session: Session = Depends(get_db)):
    brand = _brand(session, brand_id)
    activate_brand(session, brand)
    note = f"{brand.name} is active. The next poll can produce. Nothing is published."
    return _redirect("/production", message=note)


@router.post("/production/brands/{brand_id}/run")
def production_brand_run(brand_id: str, session: Session = Depends(get_db)):
    brand = _brand(session, brand_id)
    if not automation_enabled(session):
        return _redirect(
            "/production",
            error="Emergency stop is on. Turn automation on before running a brand.",
        )
    if brand.status != "active" or not brand.automation_enabled:
        return _redirect("/production", error=f"{brand.name} is not active.")
    launch_brand(brand.id)
    note = f"{brand.name} started. It discovers and renders without publishing."
    return _redirect("/production", message=note)


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
