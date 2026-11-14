"""Select an opportunity, research the page, and render one local vertical video."""

import hashlib
import logging
import threading
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    ProductionCost,
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
from app.services.production import pexels as pexels_api
from app.services.production.budget import add_cost, assert_production_budget
from app.services.production.checks import assess_production
from app.services.production.extract import extract_article, quotations_from
from app.services.production.film import durations_for, render_film, write_subtitles
from app.services.production.footage import (
    choose_clip,
    footage_record,
    mark_used,
    missing_record,
    pexels_enabled,
    recent_clip_ids,
    write_missing_slate,
)
from app.services.production.scripting import narration_from_source, plan_scenes
from app.services.production.speech import speakable
from app.services.production.stickman import render_stickman_scenes, stickman_enabled, write_scene_sfx
from app.services.research.editorial import review_script
from app.services.textutil import jaccard
from app.services.topics import create_topic
from app.services.voice import ensure_audio

logger = logging.getLogger("app.production")
_READY = (
    "Local file passed the production checks. It is ready for a future publish step. It was not published."
)
_REVIEW = (
    "The file plays, but a script, voice, or footage fallback was used. "
    "It is not marked ready for publishing."
)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _opportunity_lock(opportunity_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(opportunity_id)
        if lock is None:
            lock = threading.Lock()
            _locks[opportunity_id] = lock
        return lock


def mark_production_interrupted(session: Session) -> int:
    rows = session.scalars(select(ProductionJob).where(ProductionJob.status == "running")).all()
    for row in rows:
        row.status = "interrupted"
        row.error = "The process stopped while this production was running."
        row.finished_at = utc_now()
        opportunity = session.get(ContentOpportunity, row.opportunity_id)
        if opportunity is not None and opportunity.production_status == "producing":
            opportunity.production_status = "interrupted"
            opportunity.production_block_reason = row.error
    return len(rows)


def _attempt_counts(session: Session, brand_id: str) -> dict[str, int]:
    rows = session.execute(
        select(ProductionJob.opportunity_id, func.count())
        .where(ProductionJob.brand_id == brand_id)
        .group_by(ProductionJob.opportunity_id)
    ).all()
    return {opportunity_id: int(count) for opportunity_id, count in rows}


def _running_ids(session: Session, brand_id: str) -> set[str]:
    return set(
        session.scalars(
            select(ProductionJob.opportunity_id).where(
                ProductionJob.brand_id == brand_id,
                ProductionJob.status == "running",
            )
        ).all()
    )


def _produced_titles(session: Session, brand_id: str) -> list[str]:
    return list(
        session.scalars(
            select(ContentOpportunity.title)
            .join(ProductionJob, ProductionJob.opportunity_id == ContentOpportunity.id)
            .where(
                ProductionJob.brand_id == brand_id,
                ProductionJob.status.in_(("succeeded", "running")),
                ContentOpportunity.status != "test",
            )
        ).all()
    )


def _near_duplicate(title: str, existing: list[str]) -> bool:
    threshold = get_settings().near_duplicate_threshold
    return any(jaccard(title, other) >= threshold for other in existing)


def eligible_opportunities(session: Session, brand: Brand) -> list[ContentOpportunity]:
    done = set(
        session.scalars(
            select(ProductionJob.opportunity_id).where(
                ProductionJob.brand_id == brand.id,
                ProductionJob.status == "succeeded",
            )
        ).all()
    )
    attempts = _attempt_counts(session, brand.id)
    running = _running_ids(session, brand.id)
    produced = _produced_titles(session, brand.id)
    limit = int(brand.max_attempts or 2)
    rows = session.scalars(
        select(ContentOpportunity).where(
            ContentOpportunity.brand_id == brand.id,
            ContentOpportunity.status == "evaluated",
        )
    ).all()
    pending = []
    for row in rows:
        if row.id in done or row.id in running:
            continue
        if (row.hold_reason or "").strip():
            continue
        if attempts.get(row.id, 0) >= limit:
            continue
        if _near_duplicate(row.title, produced):
            continue
        pending.append(row)
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
    allow_placeholder: bool = True,
    test_run: bool = False,
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
        allow_placeholder=allow_placeholder,
        test_run=test_run,
    )


def _running_job(session: Session, opportunity_id: str) -> ProductionJob | None:
    return session.scalar(
        select(ProductionJob).where(
            ProductionJob.opportunity_id == opportunity_id,
            ProductionJob.status == "running",
        )
    )


def _produce(
    session: Session,
    brand: Brand,
    opportunity: ContentOpportunity,
    *,
    fetch,
    synthesizer,
    stock_client,
    allow_placeholder: bool = True,
    test_run: bool = False,
) -> ProductionJob:
    lock = _opportunity_lock(opportunity.id)
    if not lock.acquire(blocking=False):
        current = _running_job(session, opportunity.id)
        if current is not None:
            return current
        raise AppError("This opportunity is already being produced.")
    try:
        current = _running_job(session, opportunity.id)
        if current is not None:
            return current
        finished = session.scalar(
            select(ProductionJob).where(
                ProductionJob.opportunity_id == opportunity.id,
                ProductionJob.status == "succeeded",
            )
        )
        if finished is not None:
            return finished
        prior = session.scalar(
            select(func.count())
            .select_from(ProductionJob)
            .where(ProductionJob.opportunity_id == opportunity.id)
        )
        limit = int(brand.max_attempts or 2)
        if int(prior or 0) >= limit:
            latest = session.scalar(
                select(ProductionJob)
                .where(ProductionJob.opportunity_id == opportunity.id)
                .order_by(ProductionJob.started_at.desc())
            )
            if latest is not None:
                return latest
        job = ProductionJob(
            brand_id=brand.id,
            opportunity_id=opportunity.id,
            status="running",
            stage="select",
            attempts=int(prior or 0) + 1,
        )
        session.add(job)
        opportunity.production_status = "producing"
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            current = _running_job(session, opportunity.id)
            if current is not None:
                return current
            raise
        session.commit()
        try:
            _run(
                session,
                job,
                brand,
                opportunity,
                fetch=fetch,
                synthesizer=synthesizer,
                stock_client=stock_client,
                allow_placeholder=allow_placeholder,
                test_run=test_run,
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
            logger.warning(
                "production failed",
                extra={"job": job.id, "stage": job.stage, "error": job.error, "attempt": job.attempts},
            )
        session.commit()
        return job
    finally:
        lock.release()


def _run(
    session,
    job,
    brand,
    opportunity,
    *,
    fetch,
    synthesizer,
    stock_client,
    allow_placeholder: bool = True,
    test_run: bool = False,
) -> None:
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
    scenes = plan_scenes(draft["beats"], draft.get("visual_queries"))
    add_cost(session, job, provider=draft["provider"], operation="script", cost=0)
    topic = _topic(session, brand, opportunity, item, language)
    script, version = _script(session, topic, language, draft, scenes)
    job.script_id = script.id
    _apply_editorial_gate(session, brand, script)
    job.stage = "narration"
    _reserve_voice_budget(session, job, draft["narration"])
    voice_id = _brand_voice(brand, language)
    audio = ensure_audio(
        session,
        version,
        language=language,
        stability=0.5,
        similarity=0.75,
        voice_id=voice_id,
        synthesizer=synthesizer,
    )
    _record_voice_cost(session, job, audio.provider, draft["narration"])
    work = get_settings().data_dir / "production" / job.id
    work.mkdir(parents=True, exist_ok=True)
    job.stage = "visuals"
    durations = durations_for(scenes, audio.duration_seconds)
    sfx = None
    if stickman_enabled():
        images, asset_ids, kinds, clip_ids = render_stickman_scenes(scenes, durations, work)
        sfx = write_scene_sfx(work / "sfx.wav", durations)
        add_cost(session, job, provider="stickman", operation="animation", cost=0)
    else:
        images, asset_ids, kinds, clip_ids = _visuals(
            session, job, scenes, work, allow_placeholder=allow_placeholder
        )
        if not allow_placeholder and any(kind != "clip" for kind in kinds):
            _finish_without_substitute(
                job,
                opportunity,
                scenes,
                kinds,
                clip_ids,
                draft,
                audio,
                dossier,
                test_run=test_run,
            )
            return
    job.stage = "render"
    srt_path = work / "captions.srt"
    alignment = (audio.metadata_json or {}).get("alignment")
    write_subtitles(speakable(version.narration), audio.duration_seconds, srt_path, alignment=alignment)
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
        kinds=kinds,
        sfx=sfx,
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
        script_mode=str(draft.get("script_mode") or "fallback"),
        voice_provider=audio.provider,
        visual_kinds=kinds,
        clip_ids=clip_ids,
    )
    video.quality_report = report
    visual_provider = _visual_provider(kinds, scenes)
    providers = [draft["provider"], audio.provider, visual_provider, "ffmpeg"]
    fallbacks = []
    if draft.get("script_mode") != "original":
        fallbacks.append("script")
    if audio.provider != "elevenlabs":
        fallbacks.append("voice")
    if any(kind not in {"clip", "stickman"} for kind in kinds):
        fallbacks.append("footage")
    metadata = {
        "title": version.title,
        "description": version.description,
        "hashtags": version.hashtags,
        "source_url": item.url,
        "published": False,
        "ready_for_publishing": False,
    }
    quality_status = report["status"]
    job.report = {
        "providers": providers,
        "rejected_model": draft.get("rejected_model") or "",
        "scenes": scenes,
        "metadata": metadata,
        "quality": report,
        "dossier_id": dossier.id,
        "published": False,
        "ready_for_publishing": False,
        "script_provider": draft["provider"],
        "script_mode": draft.get("script_mode") or "fallback",
        "voice_provider": audio.provider,
        "voice_name": audio.voice_id,
        "visual_provider": visual_provider,
        "unique_clips": len({item for item in clip_ids if item}),
        "clips": [scene.get("footage") for scene in scenes if isinstance(scene.get("footage"), dict)],
        "fallbacks": fallbacks,
        "quality_status": quality_status,
        "warnings": _voice_warnings(audio, report.get("warnings") or []),
        "semantic_visual": report.get("semantic_visual") or "unverified",
    }
    if test_run:
        job.report = {**job.report, "test_run": True, "published": False, "ready_for_publishing": False}
    topic.status = "In production"
    if quality_status == "failed":
        video.status = "blocked"
        failed = [
            item["name"] for item in report["checks"] if item.get("required", True) and not item["passed"]
        ]
        raise AppError("Production checks failed: " + ", ".join(failed))
    video.status = quality_status
    if quality_status == "ready":
        topic.status = "Ready"
        opportunity.production_status = "ready"
        opportunity.production_block_reason = _READY
        metadata["ready_for_publishing"] = True
        job.report = {**job.report, "metadata": metadata, "ready_for_publishing": True, "published": False}
    else:
        topic.status = "Needs review"
        opportunity.production_status = "needs_review"
        opportunity.production_block_reason = _REVIEW
    for cost in session.scalars(select(ProductionCost).where(ProductionCost.job_id == job.id)):
        cost.video_id = video.id
        cost.brand_id = job.brand_id
        cost.opportunity_id = job.opportunity_id
    job.status = "succeeded"
    job.stage = "saved"
    job.finished_at = utc_now()
    session.flush()
    logger.info(
        "production finished",
        extra={"job": job.id, "video": video.id, "providers": providers, "published": False},
    )


_FRAMES_LANGS = {"en", "cs"}


def _apply_editorial_gate(session: Session, brand: Brand, script: Script) -> None:
    """Honor the global editorial gate and any stricter rule stored on the brand."""
    settings = get_settings()
    editorial = brand.editorial if isinstance(brand.editorial, dict) else {}
    brand_gate = bool(editorial.get("gate"))
    minimum = editorial.get("minimum")
    if not settings.editorial_gate and not brand_gate and minimum is None:
        return
    review = review_script(session, script)
    required = review.threshold
    if minimum is not None:
        required = max(required, int(minimum))
    if review.passed and review.quality_score >= required:
        return
    kinds = ", ".join(str(issue.get("kind", "")) for issue in (review.issues or [])[:6]) or "below threshold"
    raise AppError(
        f"Editorial score {review.quality_score} is below {required} ({kinds}). The video was not rendered."
    )


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
        brand = session.get(Brand, job.brand_id) if job.brand_id else None
        audience = brand.audience if brand is not None else ""
        niche = brand.niche if brand is not None else ""
        system = (
            "You write an original short-form narration for one vertical video. "
            "Use the source only as evidence. Paraphrase. "
            "Do not copy any source sentence of 10 or more words. "
            "Do not invent statistics, quotations, studies, products, or numbers. "
            "If the source is uncertain, keep the claim uncertain. "
            "Open with a concrete hook in the first sentence. Keep one idea. "
            "Use short spoken sentences, then a conclusion. "
            "Add a call to action only when the source supports it. "
            "Do not use phrases such as 'in today's world', 'let's dive in', or 'have you ever wondered'. "
            "Match the requested language, audience, and niche. "
            "Return JSON with title, hook, narration, beats (4 to 6 short sentences), "
            "visual_queries (one specific footage search query per beat), "
            "claims (paraphrases grounded in the source, each with text), description, and hashtags."
        )
        user = (
            f"Language: {language}\nAudience: {audience}\nNiche: {niche}\n"
            f"Title: {title}\nURL: {url}\n\nSource:\n{extracted[:6000]}"
        )
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


def _voice_warnings(audio, quality_warnings: list) -> list[str]:
    stored = str((audio.metadata_json or {}).get("voice_warning") or "").strip()
    warnings = [str(item) for item in quality_warnings if str(item).strip()]
    if stored and stored not in warnings:
        return [stored, *warnings]
    return warnings


def _brand_voice(brand: Brand, language: str) -> str:
    from app.services.voice import plan_engine

    identity = brand.identity if isinstance(brand.identity, dict) else {}
    chosen = str(identity.get("voice_id") or "").strip()
    settings = get_settings()
    planned = plan_engine(settings)
    if planned == "voicestudio":
        return chosen or settings.voicestudio_voice_id.strip()
    if planned != "elevenlabs":
        return ""
    if chosen:
        return chosen
    return settings.elevenlabs_default_voice_id.strip()


def _visual_provider(kinds: list[str], scenes: list[dict]) -> str:
    sources = {
        str((scene.get("footage") or {}).get("source") or "")
        for scene in scenes
        if (scene.get("footage") or {}).get("status") == "matched"
    }
    sources.discard("")
    if kinds and all(kind == "stickman" for kind in kinds):
        return "stickman"
    if kinds and all(kind == "clip" for kind in kinds):
        if sources == {"local-stock"}:
            return "local-stock"
        if sources == {"pexels"}:
            return "pexels"
        if sources:
            return "+".join(sorted(sources))
        return "local-stock"
    if "clip" in kinds:
        return "local-stock+missing-footage"
    return "missing-footage"


def _try_pexels_clip(session, job, scene, client, used_ids: set[str]):
    queries = []
    primary = str(scene.get("visual_query") or "").strip()
    if primary:
        queries.append(primary)
    motifs = " ".join(scene.get("motifs") or [])[:80].strip()
    if motifs and motifs not in queries:
        queries.append(motifs)
    for query in queries:
        try:
            videos = pexels_api.search_videos(query, client=client)
        except (AppError, httpx.HTTPError):
            continue
        picked = pexels_api.pick_video(videos, used_ids=used_ids, recent_ids=set())
        if picked is None:
            continue
        link = str((picked.get("file") or {}).get("link") or "")
        dest = pexels_api.cache_path(picked["id"])
        try:
            pexels_api.download_video(link, dest, client=client)
        except (AppError, httpx.HTTPError):
            continue
        asset = store_asset(
            session,
            kind="clip",
            path=dest,
            source="pexels",
            license_name="Pexels License",
            metadata={
                "pexels_id": picked["id"],
                "page": picked.get("page") or "",
                "author": picked.get("author") or "",
                "author_url": picked.get("author_url") or "",
                "license": picked.get("license") or "",
                "file_url": link,
                "query": query,
                "tags": query.split(),
            },
            name=f"pexels-{picked['id']}",
        )
        add_cost(session, job, provider="pexels", operation="footage", cost=0)
        used_ids.add(picked["id"])
        return asset
    return None


def _remember_clip(scene, asset, images, asset_ids, kinds, clip_ids) -> None:
    scene["footage"] = footage_record(asset)
    images.append(Path(asset.path))
    asset_ids.append(asset.id)
    kinds.append("clip")
    clip_ids.append(asset.id)


def _remember_gap(scene, work: Path, images, asset_ids, kinds, clip_ids, session) -> None:
    scene["footage"] = missing_record()
    path = work / f"scene-{scene['index']}-missing.png"
    write_missing_slate(path, int(scene["index"]))
    asset = store_asset(
        session,
        kind="scene",
        path=path,
        source="missing-footage",
        license_name="none",
        metadata={"fallback": "missing-footage", "scene": scene["index"]},
    )
    images.append(path)
    asset_ids.append(asset.id)
    kinds.append("missing")
    clip_ids.append("")


def _note_gap(scene, images, asset_ids, kinds, clip_ids) -> None:
    scene["footage"] = missing_record()
    images.append(None)
    asset_ids.append("")
    kinds.append("missing")
    clip_ids.append("")


def _missing_footage_lines(scenes: list[dict], kinds: list[str]) -> list[str]:
    lines = []
    for scene, kind in zip(scenes, kinds, strict=False):
        if kind == "clip":
            continue
        query = str(scene.get("visual_query") or scene.get("narration") or "this scene").strip()
        lines.append(f'Scene {scene.get("index")}: no eligible licensed clip matched "{query[:120]}".')
    return lines


def _finish_without_substitute(
    job,
    opportunity,
    scenes,
    kinds,
    clip_ids,
    draft,
    audio,
    dossier,
    *,
    test_run: bool,
) -> None:
    """Stop before render when a test has no licensed clip. Do not draw a substitute card."""
    lines = _missing_footage_lines(scenes, kinds)
    explanation = (
        "Licensed footage is missing, so no MP4 was written. "
        "No illustrated card was substituted. " + " ".join(lines)
    )
    visual = "missing-footage" if "clip" not in kinds else "local-stock+missing-footage"
    fallbacks = []
    if draft.get("script_mode") != "original":
        fallbacks.append("script")
    if audio.provider != "elevenlabs":
        fallbacks.append("voice")
    fallbacks.append("footage")
    job.report = {
        "test_run": test_run,
        "published": False,
        "ready_for_publishing": False,
        "quality_status": "needs_review",
        "warnings": lines,
        "missing_footage": lines,
        "explanation": explanation,
        "video_path": "",
        "providers": [draft["provider"], audio.provider, visual],
        "script_provider": draft["provider"],
        "script_mode": draft.get("script_mode") or "fallback",
        "voice_provider": audio.provider,
        "voice_name": audio.voice_id,
        "visual_provider": visual,
        "unique_clips": len({item for item in clip_ids if item}),
        "clips": [scene.get("footage") for scene in scenes if isinstance(scene.get("footage"), dict)],
        "fallbacks": fallbacks,
        "scenes": scenes,
        "dossier_id": dossier.id,
        "semantic_visual": "unverified",
    }
    opportunity.production_status = "needs_review"
    opportunity.production_block_reason = explanation[:2000]
    job.status = "succeeded"
    job.stage = "saved"
    job.error = ""
    job.finished_at = utc_now()


def _visuals(session, job, scenes, work: Path, *, allow_placeholder: bool = True):
    images: list[Path] = []
    asset_ids: list[str] = []
    kinds: list[str] = []
    clip_ids: list[str] = []
    used: set[str] = set()
    recent = recent_clip_ids(session)
    pexels_ids: set[str] = set()
    owned = None
    client = None
    if pexels_enabled():
        owned = httpx.Client(timeout=30, follow_redirects=True)
        client = owned
    local_used = False
    try:
        for scene in scenes:
            asset = choose_clip(session, scene, used=used, recent=recent)
            if asset is not None:
                mark_used(session, asset)
                used.add(asset.id)
                local_used = True
                _remember_clip(scene, asset, images, asset_ids, kinds, clip_ids)
                continue
            if client is not None:
                fetched = _try_pexels_clip(session, job, scene, client, pexels_ids)
                if fetched is not None:
                    used.add(fetched.id)
                    _remember_clip(scene, fetched, images, asset_ids, kinds, clip_ids)
                    continue
            if allow_placeholder:
                _remember_gap(scene, work, images, asset_ids, kinds, clip_ids, session)
            else:
                _note_gap(scene, images, asset_ids, kinds, clip_ids)
    finally:
        if owned is not None:
            owned.close()
    if local_used:
        add_cost(session, job, provider="local-stock", operation="footage", cost=0)
    return images, asset_ids, kinds, clip_ids
