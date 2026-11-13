"""Select an opportunity, research the page, and render one local vertical video."""

import hashlib
import logging
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, DuplicateTopicError
from app.core.files import sha256_file
from app.core.time import utc_now
from app.domain import CATEGORIES, LANGUAGES
from app.models import (
    APIUsage,
    Brand,
    ContentOpportunity,
    DiscoveredItem,
    ProductionJob,
    ResearchDossier,
    Script,
    ScriptVersion,
    Topic,
    Video,
)
from app.providers.cache import get_cached, make_cache_key
from app.providers.openai_provider import OpenAIProvider
from app.schemas.content import Reference, TopicCreate
from app.services.assets import build_music, store_asset
from app.services.cost import assert_within_cap
from app.services.probes import media_summary
from app.services.production.budget import add_cost, assert_production_budget
from app.services.production.checks import assess_production
from app.services.production.extract import extract_article, quotations_from
from app.services.production.film import durations_for, render_film, write_subtitles
from app.services.production.illustrate import brand_palette, draw_scene
from app.services.production.scripting import narration_from_source, plan_scenes
from app.services.production.stock import search_commons
from app.services.topics import create_topic
from app.services.voice import ensure_audio

logger = logging.getLogger("app.production")
_READY = "Local file passed the production checks. It was not published."


def mark_production_interrupted(session: Session) -> int:
    rows = session.scalars(select(ProductionJob).where(ProductionJob.status == "running")).all()
    for row in rows:
        row.status = "interrupted"
        row.error = "The process stopped while this production was running."
        row.finished_at = utc_now()
    return len(rows)


def eligible_opportunities(session: Session, brand: Brand) -> list[ContentOpportunity]:
    done = set(
        session.scalars(
            select(ProductionJob.opportunity_id).where(
                ProductionJob.brand_id == brand.id,
                ProductionJob.status == "succeeded",
            )
        ).all()
    )
    rows = session.scalars(
        select(ContentOpportunity).where(
            ContentOpportunity.brand_id == brand.id,
            ContentOpportunity.status == "evaluated",
        )
    ).all()
    pending = [row for row in rows if row.id not in done and not (row.hold_reason or "").strip()]
    pending.sort(
        key=lambda row: (
            row.partial_score is None,
            -(float(row.partial_score or 0)),
            -(float(row.coverage or 0)),
            row.created_at.isoformat() if row.created_at else "",
        )
    )
    return pending


def produce_next(
    session: Session,
    brand: Brand,
    *,
    opportunity_id: str = "",
    fetch=None,
    synthesizer=None,
    stock_client=None,
) -> ProductionJob | None:
    if brand.status == "paused":
        raise AppError(f"{brand.name} is paused. No video was rendered.")
    if opportunity_id:
        opportunity = session.get(ContentOpportunity, opportunity_id)
        if opportunity is None or opportunity.brand_id != brand.id:
            raise AppError("That opportunity does not belong to this brand.")
        finished = session.scalar(
            select(ProductionJob).where(
                ProductionJob.opportunity_id == opportunity.id,
                ProductionJob.status == "succeeded",
            )
        )
        if finished is not None:
            return finished
    else:
        pending = eligible_opportunities(session, brand)
        if not pending:
            return None
        opportunity = pending[0]
    return _produce(
        session,
        brand,
        opportunity,
        fetch=fetch or _fetch_page,
        synthesizer=synthesizer,
        stock_client=stock_client,
    )


def _produce(
    session: Session,
    brand: Brand,
    opportunity: ContentOpportunity,
    *,
    fetch,
    synthesizer,
    stock_client,
) -> ProductionJob:
    job = ProductionJob(brand_id=brand.id, opportunity_id=opportunity.id, status="running", stage="select")
    session.add(job)
    session.flush()
    try:
        _run(
            session,
            job,
            brand,
            opportunity,
            fetch=fetch,
            synthesizer=synthesizer,
            stock_client=stock_client,
        )
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc)
        job.status = "failed"
        job.error = message[:500]
        job.finished_at = utc_now()
        opportunity.production_status = "failed"
        opportunity.production_block_reason = job.error
        if job.video_id:
            video = session.get(Video, job.video_id)
            if video is not None and video.status == "rendering":
                video.status = "failed"
                video.error = job.error
        logger.warning("production failed", extra={"job": job.id, "stage": job.stage, "error": job.error})
        session.flush()
    return job


def _run(session, job, brand, opportunity, *, fetch, synthesizer, stock_client) -> None:
    if opportunity.status == "held" or (opportunity.hold_reason or "").strip():
        job.stage = "select"
        raise AppError(opportunity.hold_reason or "This opportunity is held.")
    item = None
    if opportunity.discovered_item_id:
        item = session.get(DiscoveredItem, opportunity.discovered_item_id)
    if item is None or not (item.url or "").startswith("http"):
        job.stage = "retrieve"
        raise AppError("The opportunity has no source URL to retrieve.")
    job.stage = "retrieve"
    status, html = fetch(item.url)
    add_cost(session, job, provider="http", operation="retrieve-source", cost=0)
    text = extract_article(html)
    quotes = quotations_from(text)
    if len(quotes) < 3:
        job.stage = "dossier"
        raise AppError("The source page does not contain enough sentences to write a topic-specific script.")
    job.stage = "dossier"
    dossier = _save_dossier(session, opportunity, item, status, text, quotes)
    language = brand.language if brand.language in LANGUAGES else "en"
    job.stage = "script"
    draft = narration_from_source(
        title=opportunity.title,
        extracted=dossier.extracted_text,
        url=item.url,
        language=brand.language if brand.language in _FRAMES_LANGS else language,
        complete=_model_writer(session, job),
    )
    if len(draft["beats"]) < 4:
        raise AppError("The script did not produce enough scenes.")
    scenes = plan_scenes(draft["beats"])
    add_cost(session, job, provider=draft["provider"], operation="script", cost=0)
    topic = _topic(session, brand, opportunity, item, language)
    script, version = _script(session, topic, language, draft, scenes)
    job.script_id = script.id
    job.stage = "narration"
    _reserve_voice_budget(session, job, draft["narration"])
    audio = ensure_audio(
        session,
        version,
        language=language,
        stability=0.5,
        similarity=0.75,
        synthesizer=synthesizer,
    )
    _record_voice_cost(session, job, audio.provider, draft["narration"])
    work = get_settings().data_dir / "production" / job.id
    work.mkdir(parents=True, exist_ok=True)
    job.stage = "visuals"
    images, asset_ids, stock_used = _visuals(session, job, brand, scenes, work, stock_client)
    job.stage = "render"
    durations = durations_for(scenes, audio.duration_seconds)
    srt_path = work / "captions.srt"
    write_subtitles(version.narration, audio.duration_seconds, srt_path)
    music = build_music(
        session,
        mood=version.music_mood or "curious",
        duration=audio.duration_seconds,
        work_dir=work,
    )
    output = get_settings().data_dir / "videos" / f"{job.id}.mp4"
    render_film(
        images=images,
        durations=durations,
        voice=Path(audio.path),
        music=Path(music.path),
        srt=srt_path,
        output=output,
    )
    summary = media_summary(output)
    video = Video(
        script_id=script.id,
        script_version_id=version.id,
        audio_file_id=audio.id,
        language=language,
        format="short",
        profile="short_9_16",
        path=str(output),
        subtitle_path=str(srt_path),
        work_dir=str(work),
        status="rendering",
        width=summary["width"],
        height=summary["height"],
        duration_seconds=summary["duration"],
        voice_id=audio.voice_id,
        tts_provider=audio.provider,
        asset_ids=asset_ids + [music.id],
        subtitles_burned=True,
        checksum=sha256_file(output),
        approved=False,
    )
    session.add(video)
    session.flush()
    job.video_id = video.id
    job.stage = "quality"
    settings = get_settings()
    report = assess_production(
        video_path=output,
        subtitle_path=srt_path,
        scene_paths=images,
        narration=version.narration,
        extracted=dossier.extracted_text,
        title=opportunity.title,
        claims=version.factual_claims,
        cost=Decimal(str(job.cost_usd or 0)),
        video_cap=Decimal(str(settings.production_video_cap_usd)),
    )
    video.quality_report = report
    providers = [draft["provider"], audio.provider, "pillow", "ffmpeg-lavfi"]
    if stock_used:
        providers.append("wikimedia-commons")
    metadata = {
        "title": version.title,
        "description": version.description,
        "hashtags": version.hashtags,
        "source_url": item.url,
        "published": False,
    }
    job.report = {
        "providers": providers,
        "rejected_model": draft.get("rejected_model") or "",
        "scenes": scenes,
        "metadata": metadata,
        "quality": report,
        "dossier_id": dossier.id,
        "published": False,
    }
    topic.status = "In production"
    if not report["passed"]:
        video.status = "blocked"
        failed = [item["name"] for item in report["checks"] if not item["passed"]]
        raise AppError("Production checks failed: " + ", ".join(failed))
    video.status = "ready"
    topic.status = "Ready"
    opportunity.production_status = "ready"
    opportunity.production_block_reason = _READY
    job.status = "succeeded"
    job.stage = "saved"
    job.finished_at = utc_now()
    session.flush()
    logger.info(
        "production finished",
        extra={"job": job.id, "video": video.id, "providers": providers, "published": False},
    )


_FRAMES_LANGS = {"en", "cs"}


def _fetch_page(url: str) -> tuple[int, str]:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "AIContentFactory/production (article text)"},
            timeout=get_settings().research_timeout_seconds,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise AppError(f"Could not retrieve the source page: {exc}") from exc
    if response.status_code >= 400:
        raise AppError(f"Source page returned HTTP {response.status_code}.")
    return response.status_code, response.text


def _save_dossier(session, opportunity, item, status: int, text: str, quotes: list[str]) -> ResearchDossier:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = session.scalar(select(ResearchDossier).where(ResearchDossier.opportunity_id == opportunity.id))
    if row is None:
        row = ResearchDossier(opportunity_id=opportunity.id)
        session.add(row)
    row.source_url = item.url
    row.http_status = status
    row.title = opportunity.title[:300]
    row.extracted_text = text
    row.quotations = quotes
    row.content_hash = digest
    row.retrieved_at = utc_now()
    session.flush()
    return row


def _topic(session, brand, opportunity, item, language: str) -> Topic:
    category = item.category if item.category in CATEGORIES else "science"
    description = (
        f"Retrieved from {item.url}. Quotations in the script are copied from that page. "
        "The feed summary is not the script."
    )
    try:
        return create_topic(
            session,
            TopicCreate(
                title=opportunity.title[:200],
                description=description,
                category=category,
                target_language=language,
                target_platform="youtube_shorts",
                target_audience=(brand.audience or "curious adults")[:160],
                status="In production",
                source_references=[Reference(title=item.title[:200], url=item.url, note="retrieved page")],
            ),
        )
    except DuplicateTopicError as exc:
        topic = session.get(Topic, exc.topic_id)
        if topic is None:
            raise AppError("A topic with this title already exists and could not be reused.") from exc
        return topic


def _script(session, topic, language: str, draft: dict, scenes: list[dict]) -> tuple[Script, ScriptVersion]:
    script = session.scalar(
        select(Script).where(
            Script.topic_id == topic.id,
            Script.language == language,
            Script.format == "short",
        )
    )
    if script is None:
        script = Script(
            topic_id=topic.id,
            language=language,
            format="short",
            title=draft["title"],
            status="draft",
        )
        session.add(script)
        session.flush()
        number = 1
    else:
        existing = session.scalars(select(ScriptVersion).where(ScriptVersion.script_id == script.id)).all()
        number = 1 + len(existing)
    version = ScriptVersion(
        script_id=script.id,
        version_number=number,
        title=draft["title"][:200],
        hook=draft["hook"],
        narration=draft["narration"],
        scenes=scenes,
        visual_instructions="\n".join(
            f"{scene['index']}. {scene['visual']}: {', '.join(scene['motifs'])}" for scene in scenes
        ),
        music_mood="curious",
        description=draft["description"],
        hashtags=draft["hashtags"],
        thumbnail_text=scenes[0]["on_screen_text"][:80],
        factual_claims=draft["claims"],
        offline_draft=draft["provider"] != "openai",
        provider=draft["provider"],
    )
    session.add(version)
    session.flush()
    script.current_version_id = version.id
    script.title = version.title
    session.flush()
    return script, version


def _model_writer(session: Session, job: ProductionJob):
    settings = get_settings()
    if not settings.openai_enabled():
        return None

    def complete(*, title: str, extracted: str, url: str, language: str) -> dict:
        system = (
            "Write a short vertical video script using only the source sentences. "
            "Do not add studies, numbers, products, or advice that the page does not state. "
            "Return JSON with title, hook, narration, beats, claims, description, and hashtags. "
            "Each claim text must be copied from the source."
        )
        user = f"Language: {language}\nTitle: {title}\nURL: {url}\n\nSource:\n{extracted[:6000]}"
        provider = OpenAIProvider(session)
        cache_key = make_cache_key(
            {
                "provider": "openai",
                "model": settings.openai_model,
                "operation": "production-script",
                "system": system,
                "user": user,
            }
        )
        if get_cached(session, cache_key) is None:
            assert_production_budget(session, job, provider._estimate(system, user))
            assert_within_cap(session, provider._estimate(system, user))
        parsed = provider.complete_json(operation="production-script", system=system, user=user)
        usage = session.scalar(
            select(APIUsage)
            .where(APIUsage.operation == "production-script")
            .order_by(APIUsage.created_at.desc())
        )
        if usage is not None:
            add_cost(
                session,
                job,
                provider="openai",
                operation="production-script",
                cost=usage.estimated_cost_usd,
                cached=usage.cached,
            )
        return parsed

    return complete


def _reserve_voice_budget(session, job, narration: str) -> None:
    settings = get_settings()
    if not settings.elevenlabs_enabled():
        return
    estimate = Decimal(len(narration)) / Decimal(1000) * Decimal(str(settings.elevenlabs_cost_per_1k_chars))
    assert_production_budget(session, job, estimate)


def _record_voice_cost(session, job, provider: str, narration: str) -> None:
    if provider == "elevenlabs":
        settings = get_settings()
        cost = Decimal(len(narration)) / Decimal(1000) * Decimal(str(settings.elevenlabs_cost_per_1k_chars))
    else:
        cost = Decimal("0")
    add_cost(session, job, provider=provider, operation="narration", cost=cost)


def _visuals(session, job, brand, scenes, work: Path, stock_client):
    palette = brand_palette(brand)
    photo = None
    credit = ""
    stock_used = False
    if stock_client is not None and get_settings().production_stock.strip().lower() == "wikimedia":
        query = " ".join(scenes[0].get("motifs") or [])
        try:
            found = search_commons(query, client=stock_client)
        except httpx.HTTPError:
            found = None
        if found is not None:
            image_bytes = stock_client.get(found["url"])
            image_bytes.raise_for_status()
            photo_path = work / "stock.jpg"
            photo_path.write_bytes(image_bytes.content)
            from PIL import Image

            photo = Image.open(photo_path)
            credit = f"{found['license']} · {found['artist']}"[:90]
            stock_used = True
            store_asset(
                session,
                kind="still",
                path=photo_path,
                source="wikimedia-commons",
                license_name=found["license"][:80],
                metadata={"title": found["title"], "page": found["url"], "artist": found["artist"]},
            )
    images: list[Path] = []
    asset_ids: list[str] = []
    for scene in scenes:
        path = work / f"scene-{scene['index']}.png"
        draw_scene(
            path,
            kind=scene["visual"],
            motifs=scene["motifs"],
            palette=palette,
            photo=photo if scene["index"] == 1 else None,
            credit=credit if scene["index"] == 1 else "",
        )
        asset = store_asset(
            session,
            kind="scene",
            path=path,
            source="pillow",
            license_name="original",
            metadata={"visual": scene["visual"], "motifs": scene["motifs"], "original": True},
        )
        images.append(path)
        asset_ids.append(asset.id)
    add_cost(session, job, provider="pillow", operation="illustrate", cost=0)
    return images, asset_ids, stock_used
