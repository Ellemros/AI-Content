"""Server-rendered dashboard."""

import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError, NotFoundError
from app.domain import (
    CATEGORIES,
    CATEGORY_LABELS,
    LANGUAGE_LABELS,
    LANGUAGES,
    PLATFORM_LABELS,
    PLATFORMS,
    TOPIC_STATUSES,
)
from app.models import (
    EditorialReview,
    Job,
    PublishedPost,
    PublishingJob,
    Script,
    ScriptVersion,
    Topic,
    Video,
)
from app.schemas.content import FactualClaim, Reference, Scene, TopicCreate, TopicUpdate
from app.services.analytics import placeholder
from app.services.cost import month_spend
from app.services.pipeline import approve_video, enqueue, launch
from app.services.publisher import record_export
from app.services.scripts import (
    approve_script,
    build_edit,
    current_version,
    edit_script,
    generate_script,
)
from app.services.textutil import estimate_seconds
from app.services.topics import create_topic, update_topic
from app.services.translator import localize

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message[:400]
    if error:
        params["error"] = error[:400]
    if params:
        path = f"{path}{'&' if '?' in path else '?'}{urlencode(params)}"
    return RedirectResponse(path, status_code=303)


def _render(request: Request, name: str, **context):
    settings = get_settings()
    context.update(
        {
            "app_name": settings.app_name,
            "openai_on": settings.openai_enabled(),
            "eleven_on": settings.elevenlabs_enabled(),
            "tts_provider": settings.tts_provider,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "category_labels": CATEGORY_LABELS,
            "language_labels": LANGUAGE_LABELS,
            "platform_labels": PLATFORM_LABELS,
            "categories": CATEGORIES,
            "languages": LANGUAGES,
            "platforms": PLATFORMS,
            "statuses": TOPIC_STATUSES,
            "spend": context.get("spend"),
            "cap": settings.monthly_spend_cap_usd,
        }
    )
    return templates.TemplateResponse(request, name, context)


def _refs(raw: str) -> list[Reference]:
    refs: list[Reference] = []
    for line in raw.splitlines():
        item = line.strip()
        if not item:
            continue
        if item.startswith(("http://", "https://")):
            refs.append(Reference(url=item, title=item))
        else:
            refs.append(Reference(title=item))
    return refs


def _topic(session: Session, topic_id: str) -> Topic:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic was not found.")
    return topic


def _script(session: Session, script_id: str) -> Script:
    script = session.get(Script, script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    return script


def _video(session: Session, video_id: str) -> Video:
    video = session.get(Video, video_id)
    if video is None:
        raise NotFoundError("Video was not found.")
    return video


def _job(session: Session, video_id: str) -> Job | None:
    return session.scalar(select(Job).where(Job.video_id == video_id).order_by(Job.created_at.desc()))


@router.get("/")
def overview(request: Request, session: Session = Depends(get_db)):
    def count(model, *criteria) -> int:
        stmt = select(func.count()).select_from(model)
        if criteria:
            stmt = stmt.where(*criteria)
        return int(session.scalar(stmt) or 0)

    by_status = {status: count(Topic, Topic.status == status) for status in TOPIC_STATUSES}
    return _render(
        request,
        "overview.html",
        counts={
            "topics": count(Topic),
            "scripts": count(Script),
            "videos": count(Video),
            "ready": count(Video, Video.status == "ready"),
            "approved": count(Video, Video.approved.is_(True)),
            "exports": count(PublishedPost),
        },
        by_status=by_status,
        spend=float(month_spend(session)),
        recent=session.scalars(select(Topic).order_by(Topic.created_at.desc()).limit(6)).all(),
    )


@router.get("/topics")
def topics_page(request: Request, session: Session = Depends(get_db)):
    rows = session.scalars(select(Topic).order_by(Topic.created_at.desc())).all()
    return _render(request, "topics.html", topics=rows)


@router.post("/topics")
def topics_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form("ai"),
    target_language: str = Form("en"),
    target_platform: str = Form("youtube_shorts"),
    target_audience: str = Form("curious adults"),
    priority: int = Form(3),
    estimated_content_potential: int = Form(5),
    source_references: str = Form(""),
    research_references: str = Form(""),
    session: Session = Depends(get_db),
):
    try:
        topic = create_topic(
            session,
            TopicCreate(
                title=title,
                description=description,
                category=category,
                target_language=target_language,
                target_platform=target_platform,
                target_audience=target_audience,
                priority=priority,
                estimated_content_potential=estimated_content_potential,
                source_references=_refs(source_references),
                research_references=_refs(research_references),
            ),
        )
    except (AppError, ValidationError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Check the topic fields."
        return _redirect("/topics", error=detail)
    return _redirect(f"/topics/{topic.id}", message="Topic saved.")


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: str, request: Request, session: Session = Depends(get_db)):
    topic = _topic(session, topic_id)
    scripts = session.scalars(select(Script).where(Script.topic_id == topic.id)).all()
    return _render(request, "topic_detail.html", topic=topic, scripts=scripts)


@router.post("/topics/{topic_id}/status")
def topic_status(
    topic_id: str,
    status: str = Form(...),
    session: Session = Depends(get_db),
):
    try:
        update_topic(session, _topic(session, topic_id), TopicUpdate(status=status))
    except AppError as exc:
        return _redirect(f"/topics/{topic_id}", error=exc.message)
    return _redirect(f"/topics/{topic_id}", message="Status updated.")


@router.get("/scripts")
def scripts_page(request: Request, session: Session = Depends(get_db)):
    rows = session.scalars(select(Script).order_by(Script.created_at.desc())).all()
    return _render(request, "scripts.html", scripts=rows, topics=session.scalars(select(Topic)).all())


@router.post("/scripts/generate")
def scripts_generate(
    topic_id: str = Form(...),
    language: str = Form("en"),
    video_format: str = Form("short"),
    force: str = Form(""),
    session: Session = Depends(get_db),
):
    topic = session.get(Topic, topic_id)
    if topic is None:
        return _redirect("/scripts", error="Topic was not found.")
    try:
        script = generate_script(
            session,
            topic,
            language=language,
            video_format=video_format,
            force=force == "yes",
        )
    except (AppError, ValidationError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "The script did not match the schema."
        return _redirect(f"/topics/{topic_id}", error=detail)
    return _redirect(f"/scripts/{script.id}", message="Script ready.")


@router.get("/scripts/{script_id}")
def script_detail(script_id: str, request: Request, session: Session = Depends(get_db)):
    script = _script(session, script_id)
    version = current_version(session, script)
    versions = session.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.script_id == script.id)
        .order_by(ScriptVersion.version_number.desc())
    ).all()
    review = session.scalar(
        select(EditorialReview)
        .where(EditorialReview.script_id == script.id)
        .order_by(EditorialReview.created_at.desc())
    )
    return _render(
        request,
        "script_detail.html",
        script=script,
        version=version,
        versions=versions,
        review=review,
        estimated=estimate_seconds(version.narration, script.language),
        claims_json=json.dumps(version.factual_claims, ensure_ascii=False, indent=2),
        scenes_json=json.dumps(version.scenes, ensure_ascii=False, indent=2),
    )


@router.post("/scripts/{script_id}/edit")
def script_edit(
    script_id: str,
    title: str = Form(...),
    hook: str = Form(...),
    narration: str = Form(...),
    visual_instructions: str = Form(...),
    music_mood: str = Form(...),
    cta: str = Form(...),
    description: str = Form(...),
    hashtags: str = Form(...),
    thumbnail_text: str = Form(...),
    factual_claims: str = Form("[]"),
    scenes_json: str = Form(""),
    rebuild_scenes: str = Form(""),
    session: Session = Depends(get_db),
):
    script = _script(session, script_id)
    previous = current_version(session, script)
    try:
        claims = [FactualClaim.model_validate(item) for item in json.loads(factual_claims or "[]")]
        scenes = None
        if rebuild_scenes != "yes" and scenes_json.strip():
            scenes = [Scene.model_validate(item) for item in json.loads(scenes_json)]
        tags = [part.strip() for part in hashtags.replace("\n", ",").split(",") if part.strip()]
        content = build_edit(
            script,
            title=title,
            hook=hook,
            narration=narration,
            visual_instructions=visual_instructions,
            music_mood=music_mood,
            cta=cta,
            description=description,
            hashtags=tags,
            thumbnail_text=thumbnail_text,
            factual_claims=claims,
            scenes=scenes,
            rebuild_scenes=rebuild_scenes == "yes" or scenes is None,
            offline_draft=previous.offline_draft,
            provider="manual",
            template_key=previous.template_key,
        )
        edit_script(session, script, content)
    except (AppError, ValidationError, json.JSONDecodeError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "The edit did not match the script schema."
        if isinstance(exc, ValidationError):
            detail = exc.errors()[0]["msg"]
        return _redirect(f"/scripts/{script_id}", error=detail)
    return _redirect(f"/scripts/{script_id}", message="Saved a new version.")


@router.post("/scripts/{script_id}/approve")
def script_approve(script_id: str, session: Session = Depends(get_db)):
    try:
        approve_script(session, _script(session, script_id))
    except AppError as exc:
        return _redirect(f"/scripts/{script_id}", error=exc.message)
    return _redirect(f"/scripts/{script_id}", message="Script approved.")


@router.post("/scripts/{script_id}/localize")
def script_localize(
    script_id: str,
    language: str = Form(...),
    session: Session = Depends(get_db),
):
    try:
        localized = localize(session, _script(session, script_id), language)
    except (AppError, ValidationError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Localization did not match the schema."
        return _redirect(f"/scripts/{script_id}", error=detail)
    return _redirect(f"/scripts/{localized.id}", message="Localized script ready.")


@router.get("/topics/{topic_id}/compare")
def compare(topic_id: str, request: Request, video_format: str = "short", session: Session = Depends(get_db)):
    topic = _topic(session, topic_id)
    rows = session.scalars(
        select(Script).where(Script.topic_id == topic.id, Script.format == video_format)
    ).all()
    pair = {}
    for script in rows:
        version = current_version(session, script)
        pair[script.language] = {"script": script, "version": version}
    return _render(request, "compare.html", topic=topic, pair=pair, video_format=video_format)


@router.get("/videos")
def videos_page(request: Request, session: Session = Depends(get_db)):
    rows = session.scalars(select(Video).order_by(Video.created_at.desc())).all()
    return _render(request, "videos.html", videos=rows)


@router.post("/videos/generate")
def videos_generate(
    script_id: str = Form(...),
    stability: float = Form(0.5),
    similarity: float = Form(0.75),
    voice_id: str = Form(""),
    session: Session = Depends(get_db),
):
    script = session.get(Script, script_id)
    if script is None:
        return _redirect("/videos", error="Script was not found.")
    try:
        video, started = enqueue(
            session,
            script,
            stability=stability,
            similarity=similarity,
            voice_id=voice_id,
        )
        session.commit()
        if started:
            launch(video.id)
    except AppError as exc:
        return _redirect(f"/scripts/{script_id}", error=exc.message)
    note = "Render started." if started else "Existing render returned. Nothing was started twice."
    return _redirect(f"/videos/{video.id}", message=note)


@router.get("/videos/{video_id}")
def video_detail(video_id: str, request: Request, session: Session = Depends(get_db)):
    video = _video(session, video_id)
    script = session.get(Script, video.script_id)
    return _render(
        request,
        "video_detail.html",
        video=video,
        script=script,
        job=_job(session, video.id),
    )


@router.post("/videos/{video_id}/approve")
def video_approve(video_id: str, session: Session = Depends(get_db)):
    try:
        approve_video(session, _video(session, video_id))
    except AppError as exc:
        return _redirect(f"/videos/{video_id}", error=exc.message)
    return _redirect(f"/videos/{video_id}", message="Video approved. You can record a local export.")


@router.post("/videos/{video_id}/retry")
def video_retry(video_id: str, session: Session = Depends(get_db)):
    video = _video(session, video_id)
    script = session.get(Script, video.script_id)
    if script is None:
        return _redirect(f"/videos/{video_id}", error="Script was not found.")
    try:
        video, started = enqueue(
            session,
            script,
            stability=video.stability,
            similarity=video.similarity,
            voice_id=video.voice_id,
            force=True,
        )
        session.commit()
        if started:
            launch(video.id)
    except AppError as exc:
        return _redirect(f"/videos/{video_id}", error=exc.message)
    return _redirect(f"/videos/{video.id}", message="Retry started.")


@router.post("/videos/{video_id}/regenerate")
def video_regenerate(
    video_id: str,
    component: str = Form("render"),
    session: Session = Depends(get_db),
):
    video = _video(session, video_id)
    script = session.get(Script, video.script_id)
    if script is None:
        return _redirect(f"/videos/{video_id}", error="Script was not found.")
    try:
        video, started = enqueue(
            session,
            script,
            stability=video.stability,
            similarity=video.similarity,
            voice_id=video.voice_id,
            force=True,
            component=component,
        )
        session.commit()
        if started:
            launch(video.id)
    except AppError as exc:
        return _redirect(f"/videos/{video_id}", error=exc.message)
    return _redirect(f"/videos/{video.id}", message=f"Regenerating {component}.")


@router.get("/videos/{video_id}/file")
def video_file(video_id: str, session: Session = Depends(get_db)):
    video = _video(session, video_id)
    if not video.path:
        raise NotFoundError("The video file is not ready.")
    path = Path(video.path).resolve()
    root = get_settings().data_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise NotFoundError("The video file is not ready.")
    return FileResponse(path, media_type="video/mp4", filename=f"{video.id}.mp4")


@router.get("/publishing")
def publishing_page(request: Request, session: Session = Depends(get_db)):
    posts = session.scalars(select(PublishedPost).order_by(PublishedPost.recorded_at.desc())).all()
    jobs = session.scalars(select(PublishingJob).order_by(PublishingJob.created_at.desc())).all()
    approved = session.scalars(select(Video).where(Video.approved.is_(True))).all()
    return _render(request, "publishing.html", posts=posts, jobs=jobs, approved=approved)


@router.post("/publishing/record")
def publishing_record(
    video_id: str = Form(...),
    platform: str = Form("youtube_shorts"),
    session: Session = Depends(get_db),
):
    video = session.get(Video, video_id)
    if video is None:
        return _redirect("/publishing", error="Video was not found.")
    try:
        record_export(session, video, platform)
    except AppError as exc:
        return _redirect(f"/videos/{video_id}", error=exc.message)
    return _redirect("/publishing", message="Local export recorded. Nothing was uploaded.")


@router.get("/analytics")
def analytics_page(request: Request):
    return _render(request, "analytics.html", analytics=placeholder())
