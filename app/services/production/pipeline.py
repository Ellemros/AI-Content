"""Select an opportunity, research the page, and render one local vertical video."""

import hashlib
import json
import logging
import re
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
from app.services.production.stickman import (
    render_stickman_scenes,
    stickman_enabled,
    visual_variety,
    write_engine_sfx,
)
from app.services.research.editorial import priors, review_content, review_script
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
    retry: bool = False,
    structure: str = "",
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
        retry=retry and bool(opportunity_id),
        structure=structure,
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
    retry: bool = False,
    structure: str = "",
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
        if int(prior or 0) >= limit and not retry:
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
                structure=structure,
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
    structure: str = "",
) -> None:
    if opportunity.status == "held" or (opportunity.hold_reason or "").strip():
        job.stage = "select"
        raise AppError(opportunity.hold_reason or "This opportunity is held.")
    from app.services.business.signals import hold_reason

    late_hold = hold_reason(opportunity.title, "", brand)
    if late_hold:
        # The guardrail may be stricter than when this opportunity was scored. Hold it now; never release it.
        opportunity.status = "held"
        opportunity.hold_reason = late_hold
        opportunity.production_status = "blocked"
        job.stage = "select"
        raise AppError(late_hold)
    item = None
    if opportunity.discovered_item_id:
        item = session.get(DiscoveredItem, opportunity.discovered_item_id)
    if item is None or not (item.url or "").startswith("http"):
        job.stage = "retrieve"
        raise AppError("The opportunity has no source URL to retrieve.")
    job.stage = "retrieve"
    sources = []
    if opportunity.source_urls:
        from app.services.research import dossier as research

        sources = research.build(list(opportunity.source_urls), opportunity.question or opportunity.title)
        status, text = 200, research.combined_text(sources)
    else:
        status, html = fetch(item.url)
        text = extract_article(html)
    add_cost(session, job, provider="http", operation="retrieve-source", cost=0)
    quotes = quotations_from(text)
    if len(quotes) < 3:
        job.stage = "dossier"
        raise AppError("The source page does not contain enough sentences to write a topic-specific script.")
    job.stage = "dossier"
    dossier = _save_dossier(session, opportunity, item, status, text, quotes)
    language = brand.language if brand.language in LANGUAGES else "en"
    job.stage = "script"
    topic = _topic(session, brand, opportunity, item, language)
    draft = _write_script(
        session, job, brand, topic, opportunity, item, dossier, language, structure=structure
    )
    if len(draft["beats"]) < 4:
        raise AppError("The script did not produce enough scenes.")
    if sources:
        from app.services.research import dossier as research

        for claim in draft["claims"]:
            claim["sources"] = [research.source_for(claim.get("source", ""), sources)]
    scenes = plan_scenes(draft["beats"], draft.get("visual_queries"), draft.get("beat_directions"))
    add_cost(session, job, provider=draft["provider"], operation="script", cost=0)
    script, version = _script(session, topic, language, draft, scenes)
    job.script_id = script.id
    editorial_warning = _apply_editorial_gate(session, brand, script, required=draft["provider"] == "openai")
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
    durations = _spoken_durations(scenes, audio) or durations_for(scenes, audio.duration_seconds)
    sfx = None
    motion_plan = None
    if _renderer(brand) == "motion":
        from app.services.production import motion

        allowed = set(re.findall(r"\d+(?:\.\d+)?", dossier.extracted_text + " " + opportunity.title))
        directions = draft.get("beat_directions") or []
        motion_scenes = [
            motion.normalise(
                {**(directions[i].get("direction") or {}), "text": scene["narration"]},
                allowed,
                dossier.extracted_text,
            )
            if i < len(directions)
            else motion.normalise({"text": scene["narration"]}, allowed, dossier.extracted_text)
            for i, scene in enumerate(scenes)
        ]
        for scene, planned in zip(scenes, motion_scenes, strict=True):
            scene["motion"] = planned
            scene["visual"] = f"motion {planned['template']}"
            scene["footage"] = {
                "status": "animated",
                "source": "motion",
                "license": "Original motion graphics",
            }
        identity = brand.identity if isinstance(brand.identity, dict) else {}
        motion_plan = motion.build_props(
            motion_scenes,
            durations,
            (audio.metadata_json or {}).get("alignment") or {},
            brand=brand.name,
            theme=str(identity.get("motion_theme") or "money-minds"),
        )
        images, asset_ids = [], []
        kinds = ["motion"] * len(scenes)
        clip_ids = [f"motion-{i + 1}-{s['template']}" for i, s in enumerate(motion_scenes)]
        add_cost(session, job, provider="remotion", operation="animation", cost=0)
    elif stickman_enabled():
        identity = brand.identity if isinstance(brand.identity, dict) else {}
        images, asset_ids, kinds, clip_ids = render_stickman_scenes(
            scenes,
            durations,
            work,
            style=str(identity.get("visual_style") or "paper"),
            seed=int(hashlib.sha256(job.id.encode()).hexdigest()[:6], 16),
        )
        sfx = write_engine_sfx(work / "sfx.wav", durations, scenes)
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
    write_subtitles(
        speakable(version.narration),
        audio.duration_seconds,
        srt_path,
        alignment=alignment,
        background=_caption_background(brand, kinds),
    )
    music = build_music(
        session,
        mood=version.music_mood or "curious",
        duration=audio.duration_seconds,
        work_dir=work,
    )
    output = get_settings().data_dir / "videos" / f"{job.id}.mp4"
    if motion_plan is not None:
        from app.services.production import motion, visual_qa

        silent = motion.render(motion_plan, work, output)
        sfx = motion.sfx_track(work / "sfx.wav", motion_plan)
        motion.mix(silent, Path(audio.path), Path(music.path), sfx, output)
        motion_qa = visual_qa.inspect(output, work, motion_plan)
        motion_qa["checks"].extend(visual_qa.storyboard_checks([scene["motion"] for scene in scenes]))
        images = [Path(path) for path in motion_qa["scene_frames"]]
    else:
        motion_qa = None
    if motion_plan is None:
        render_film(
            images=images,
            durations=durations,
            voice=Path(audio.path),
            music=Path(music.path),
            srt=srt_path,
            output=output,
            kinds=kinds,
            sfx=sfx,
            transitions=[str(scene.get("transition") or "fade") for scene in scenes]
            if sfx is not None
            else None,
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
    visual_provider = _visual_provider(kinds, scenes)
    providers = [draft["provider"], audio.provider, visual_provider, "ffmpeg"]
    fallbacks = []
    if draft.get("script_mode") != "original":
        fallbacks.append("script")
    if audio.provider not in _PRODUCTION_VOICES:
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
    if motion_qa is not None:
        report = {
            **report,
            "checks": [*report["checks"], *motion_qa["checks"]],
            "contact_sheet": motion_qa["contact_sheet"],
            "human_review": motion_qa["human_review"],
        }
        failed_visual = [c["name"] for c in motion_qa["checks"] if c["required"] and not c["passed"]]
        soft_visual = [c["detail"] for c in motion_qa["checks"] if not c["required"] and not c["passed"]]
        if failed_visual:
            report["status"] = "failed"
        elif soft_visual and report["status"] != "failed":
            # A soft check flagged something a person should look at before approving.
            report["status"] = "needs_review"
        report["warnings"] = [
            *soft_visual,
            *(report.get("warnings") or []),
        ]
        report["story_review"] = draft.get("story_review") or {}
    quality_status = report["status"]
    warnings = _voice_warnings(audio, report.get("warnings") or [])
    if editorial_warning:
        if editorial_warning not in warnings:
            warnings.insert(0, editorial_warning)
        if quality_status == "ready":
            quality_status = "needs_review"
        report = {**report, "status": quality_status, "warnings": warnings}
    video.quality_report = report
    kept = {key: value for key, value in (job.report or {}).items() if key in {"hook_choice", "check_models"}}
    job.report = {
        **kept,
        "providers": providers,
        "rejected_model": draft.get("rejected_model") or "",
        "script_attempts": draft.get("attempts") or [],
        "format": draft.get("format") or {},
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
        "warnings": warnings,
        "semantic_visual": report.get("semantic_visual") or "unverified",
        "visual_variety": visual_variety(scenes) if sfx is not None else {},
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
    if quality_status == "ready" and motion_plan is not None:
        # Every automated check passed. A person still approves each video before any publish step.
        topic.status = "Ready"
        opportunity.production_status = "ready"
        opportunity.production_block_reason = (
            "Passed every automated check. Waiting for a person to approve it. It was not published."
        )
        metadata["awaiting_approval"] = True
        job.report = {**job.report, "metadata": metadata, "awaiting_approval": True, "published": False}
    elif quality_status == "ready":
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


def _renderer(brand: Brand) -> str:
    """motion (Remotion templates) or the older stickman/footage path. Brand identity wins over settings."""
    identity = brand.identity if isinstance(brand.identity, dict) else {}
    chosen = str(identity.get("renderer") or get_settings().production_footage or "").strip().lower()
    if chosen == "motion":
        from app.services.production import motion

        if not motion.available():
            raise AppError(
                "This brand renders motion graphics, but renderer/ is not installed (npm install)."
            )
        return "motion"
    return chosen


def _caption_background(brand: Brand, kinds: list[str]) -> str:
    """Dark ink captions on light paper; white captions on footage and dark styles."""
    if not kinds or not all(kind == "stickman" for kind in kinds):
        return "dark"
    from app.services.production.scene_engine import is_dark

    identity = brand.identity if isinstance(brand.identity, dict) else {}
    return "dark" if is_dark(str(identity.get("visual_style") or "paper")) else "light"


_PRODUCTION_VOICES = {"kokoro", "elevenlabs", "voicestudio"}


def _apply_editorial_gate(session: Session, brand: Brand, script: Script, *, required: bool = False) -> str:
    """Store an editorial review. A failing original script stops the job before narration.

    `required` is true for model scripts: they already had a revision round, so a
    failure here is final. A labelled dossier fallback is reviewed and kept at
    needs_review, as before, so an offline render still works.
    """
    settings = get_settings()
    editorial = brand.editorial if isinstance(brand.editorial, dict) else {}
    brand_gate = bool(editorial.get("gate"))
    minimum = editorial.get("minimum")
    review = review_script(session, script)
    threshold = review.threshold
    if minimum is not None:
        threshold = max(threshold, int(minimum))
    if review.passed and review.quality_score >= threshold:
        return ""
    kinds = ", ".join(str(issue.get("kind", "")) for issue in (review.issues or [])[:6]) or "below threshold"
    if required or settings.editorial_gate or brand_gate:
        raise AppError(
            f"Editorial score {review.quality_score} is below {threshold} ({kinds}). "
            "The video was not rendered."
        )
    return (
        f"Editorial score {review.quality_score} is below {threshold} ({kinds}). "
        "The video was rendered for review and is not ready for publishing."
    )


def _preview_review(session: Session, topic: Topic, draft: dict, audience: str) -> dict:
    """Run the editorial review on a draft before it is saved, so a revision can be requested."""
    existing = session.scalar(select(Script.id).where(Script.topic_id == topic.id))
    hooks, claims, endings = priors(session, exclude_script_id=existing or "")
    settings = get_settings()
    return review_content(
        title=draft["title"],
        hook=draft["hook"],
        narration=draft["narration"],
        scenes=list(draft["beats"]),
        claims=list(draft["claims"]),
        cta="",
        audience=audience,
        prior_hooks=hooks,
        prior_claims=claims,
        prior_endings=endings,
        threshold=settings.editorial_quality_minimum,
    )


def _write_script(
    session, job, brand, topic, opportunity, item, dossier, language: str, structure: str = ""
) -> dict:
    """Ask the model for a script, review it, and ask once more with the reviewer's issues.

    Without an OpenAI key the labelled dossier fallback is used (needs_review).
    With a key, a script that still fails after the revision stops the job:
    stitched source sentences are not rendered in place of a rejected script.
    """
    from app.services.strategy import STRUCTURES, choose_format

    settings = get_settings()
    plan = choose_format(session, brand)
    if structure in STRUCTURES:
        plan = {**plan, "structure": structure, "reason": "chosen for this topic by the topic ranking"}
    writer = _model_writer(session, job, plan["structure"], plan["target_seconds"])
    frame_language = brand.language if brand.language in _FRAMES_LANGS else language
    if writer is None:
        return narration_from_source(
            title=opportunity.title,
            extracted=dossier.extracted_text,
            url=item.url,
            language=frame_language,
        )
    audience = (brand.audience or "curious adults")[:160]
    feedback = ""
    attempts: list[dict] = []
    for _attempt in range(max(int(settings.script_attempts), 1)):
        try:
            draft = narration_from_source(
                title=opportunity.title,
                extracted=dossier.extracted_text,
                url=item.url,
                language=frame_language,
                complete=writer,
                feedback=feedback,
                allow_fallback=False,
                strict_hedges=not (settings.openai_factcheck_model and settings.openai_enabled()),
            )
        except AppError as exc:
            attempts.append({"passed": False, "issues": [exc.message]})
            feedback = f"The previous draft was rejected: {exc.message}"
            continue
        words = len(draft["narration"].split())
        floor = int(plan["target_seconds"] * 2.4 * 0.8)
        if words < floor:
            attempts.append(
                {"passed": False, "issues": [f"Too short: {words} words, needs at least {floor}."]}
            )
            feedback = (
                f"The previous draft was only {words} words. Write {floor} to "
                f"{int(plan['target_seconds'] * 2.8)} words: add one concrete example and the key caveat."
            )
            continue
        review = _preview_review(session, topic, draft, audience)
        details = [str(issue.get("detail") or issue.get("kind")) for issue in review["issues"]]
        fact_problems: list[str] = []
        story: dict = {}
        if review["passed"]:
            fact_problems = _fact_check(session, job, draft, dossier.extracted_text)
            if fact_problems:
                review = {**review, "passed": False}
                details = [f"Fact check: {problem}" for problem in fact_problems]
        if review["passed"]:
            story = _story_review(session, job, draft, opportunity)
            if story.get("blocking"):
                review = {**review, "passed": False}
                details = [f"Story editor: {problem}" for problem in story["blocking"]]
        attempts.append(
            {
                "passed": review["passed"],
                "score": review["quality_score"],
                "issues": details,
                "fact_check": "passed"
                if review["passed"] or story
                else ("failed" if fact_problems else "not run"),
                "story_review": story or None,
                "hook": draft.get("hook", ""),
            }
        )
        if review["passed"]:
            draft["attempts"] = attempts
            draft["format"] = plan
            draft["story_review"] = story
            return draft
        feedback = "The editorial reviewer rejected the previous draft:\n- " + "\n- ".join(details)
        feedback += f"\nPrevious opening (do not reuse): {draft['hook']}"
    last = attempts[-1]["issues"] if attempts else ["no draft"]
    # Keep every rejected draft's reasons on the job: a failed video can be reviewed and its cost explained.
    if job is not None:
        job.report = {**(job.report or {}), "script_attempts": attempts, "format": plan}
    raise AppError(f"The model script failed review after {len(attempts)} drafts: " + "; ".join(last)[:400])


def _spoken_durations(scenes: list[dict], audio) -> list[float] | None:
    """Scene lengths from the voice's own sentence timings, so a cut lands where the sentence ends."""
    alignment = (audio.metadata_json or {}).get("alignment") or {}
    segments = alignment.get("segments") if isinstance(alignment, dict) else None
    if not segments:
        return None
    from app.services.production.speech import speakable
    from app.services.textutil import split_sentences

    last_segment: list[int] = []
    cursor = 0
    for scene in scenes:
        count = len(split_sentences(speakable(str(scene.get("narration") or "")))) or 1
        cursor += count
        if cursor > len(segments):
            return None
        last_segment.append(cursor - 1)
    if cursor != len(segments):
        return None
    total = float(audio.duration_seconds)
    durations = []
    start = 0.0
    for index, seg in enumerate(last_segment):
        if index == len(last_segment) - 1:
            boundary = total
        else:
            # Cut in the middle of the pause after the scene's last sentence.
            boundary = (float(segments[seg]["end"]) + float(segments[seg + 1]["start"])) / 2
        durations.append(max(boundary - start, 0.4))
        start = boundary
    return durations


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


def _model_writer(
    session: Session, job: ProductionJob, structure: str = "explainer", target_seconds: int = 40
):
    settings = get_settings()
    if not settings.openai_enabled():
        return None

    beats_low = max(4, round(target_seconds / 8))
    beats_high = max(beats_low + 2, round(target_seconds / 5))

    def complete(*, title: str, extracted: str, url: str, language: str, feedback: str = "") -> dict:
        brand = session.get(Brand, job.brand_id) if job.brand_id else None
        audience = brand.audience if brand is not None else ""
        niche = brand.niche if brand is not None else ""
        recent_hooks, _claims, _endings = priors(session)
        avoid = "\n".join(f"- {hook}" for hook in recent_hooks[-8:] if hook) or "- (none yet)"
        from app.services.production import scene_engine
        from app.services.strategy import structure_brief

        brief = structure_brief(structure)
        system = (
            "You are the head writer and storyboard artist of a faceless short-form explainer channel "
            "animated with stickman figures. Write one original vertical video "
            f"(about {target_seconds} seconds spoken, "
            f"{int(target_seconds * 2.5)} to {int(target_seconds * 2.9)} words). "
            "The source page is your only evidence. Paraphrase it; "
            "never copy a run of 10 or more source words. "
            "Do not invent statistics, quotations, studies, names, products, or numbers. "
            "When the source is tentative (correlation, a single study, a model, an early result), say so in "
            "plain words: 'may', 'in one study', 'researchers think'. Never turn a correlation into a cause. "
            "Keep the conditions a result depends on (sample size, 'with clear signals', 'in mice'). "
            "No medical, financial, or legal advice "
            "and no instructions to the viewer about their health or money.\n"
            f"Story structure for this video: {brief}\n"
            "Beat 1 is the hook: concrete, surprising, specific, never a generic opener. "
            "The last beat lands a takeaway in fresh words; no 'so next time', no 'remember', "
            "no call to subscribe.\n"
            "Banned phrases: in today's world, let's dive in, have you ever wondered, "
            "buckle up, game changer, "
            "delve, it's important to note, research shows, studies show, in this video.\n"
            "Storyboard every beat so the picture shows the idea, not a caption of it. Fields per beat: "
            f"layout ({', '.join(scene_engine.LAYOUTS)}): duo for two people or two sides, "
            "crowd for 'most people', "
            "chart only when the source gives two to four comparable numbers, "
            "split for before/after or A vs B, "
            "closeup for a feeling or a thought, metaphor for an abstract idea shown as an object; "
            f"setting ({', '.join(scene_engine.SETTINGS)}); "
            f"action ({', '.join(scene_engine.ACTIONS)}); "
            f"props: zero to two from ({', '.join(scene_engine.PROPS)}), chosen as visual metaphors; "
            f"camera ({', '.join(scene_engine.CAMERAS)}); "
            "emphasis: optional 1 to 4 words or one number from the source shown big, at most three beats; "
            "chart: only for layout chart, [{label, value}] with values copied from the source; "
            "labels: only for layout split, [left, right]. "
            "Use at least four layouts and four settings, and never the same layout twice in a row.\n"
            'Return JSON: {"title": str (under 70 characters, curiosity without clickbait), '
            '"beats": [{"text": str, "layout": str, "setting": str, "action": str, "props": [str], '
            '"camera": str, "emphasis": str, "chart": [{"label": str, "value": number}], "labels": [str]}] '
            f"({beats_low} to {beats_high} beats, each one or two short spoken sentences), "
            '"claims": [{"text": str, "certainty": "established" or "hedged"}] '
            "(every factual statement the narration makes, paraphrased, "
            "hedged when the source is tentative), "
            '"description": str (2 sentences, no hashtags), "hashtags": [str] (3 to 5)}.'
        )
        motion_mode = brand is not None and _renderer(brand) == "motion"
        if motion_mode:
            system = _motion_system(brief, target_seconds)
        question = ""
        if job.opportunity_id:
            opportunity = session.get(ContentOpportunity, job.opportunity_id)
            question = (opportunity.question or "") if opportunity is not None else ""
        user = (
            f"Language: {language}\nAudience: {audience}\nNiche: {niche}\n"
            f"Working title: {title}\n"
            + (f"Viewer question: {question}\n" if question else "")
            + f"Source URL: {url}\n"
            f"Openings already used on this channel (write something different):\n{avoid}\n"
        )
        if feedback:
            user += f"\nRevise. {feedback}\n"
        user += f"\nSources:\n{extracted[:14000] if motion_mode else extracted[:6000]}"
        provider = OpenAIProvider(session, model=settings.openai_script_model or settings.openai_model)
        cache_key = make_cache_key(
            {
                "provider": "openai",
                "model": provider.model,
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
        if motion_mode:
            parsed = _choose_hook(session, job, parsed, question)
        return parsed

    return complete


def _fact_check(session: Session, job: ProductionJob, draft: dict, extracted: str) -> list[str]:
    """A second model reads every narration sentence against the sources. Returns problems, or []."""
    from app.services.production.llm import cheap_available, cheap_json

    if not cheap_available():
        return []
    system = (
        "You are a strict fact checker for an educational video. For each narration sentence, decide whether "
        "the sources support it as worded. Flag: unsupported (not in the sources), contradicted (the sources "
        "say otherwise), overstated (stated as certain or general when the sources hedge, "
        "use a small sample, "
        "a lab task, a price range, animals, or correlation), "
        "missing_caveat (a limitation without which a "
        "viewer would be misled), or misattributed (a finding credited to a publication, year, or "
        "author that the sources do not tie to that finding; a title in a reference list is not "
        "evidence of what that work found). Questions, the hook's framing of a question, and plainly "
        "labelled opinion "
        "are fine. Be exact; do not flag style. Return JSON: "
        '{"problems": [{"sentence": str, "kind": str, "why": str}]}. Empty list if all is supported.'
    )
    hints = draft.get("hedge_hints") or []
    cards = []
    for item in draft.get("beat_directions") or []:
        direction = item.get("direction") or {}
        params = direction.get("params") if isinstance(direction.get("params"), dict) else {}
        if direction.get("template") == "study":
            cards.append(
                f"- On-screen study card: {params.get('source', '')}: {params.get('finding', '')} "
                f"(caveat: {params.get('caveat', '')})"
            )
    user = (
        "Narration:\n"
        + "\n".join(f"- {beat}" for beat in draft["beats"])
        + ("\n" + "\n".join(cards) if cards else "")
        + (
            "\n\nA word-overlap check thinks these may drop a hedge the source uses. Judge them carefully:\n"
            + "\n".join(f"- {hint}" for hint in hints)
            if hints
            else ""
        )
        + f"\n\nSources:\n{extracted[:14000]}"
    )
    result = cheap_json(session, job, operation="fact-check", system=system, user=user)
    problems = result.get("problems") if isinstance(result, dict) else None
    out = []
    for item in problems or []:
        if isinstance(item, dict) and item.get("sentence"):
            sentence = str(item["sentence"])[:160]
            why = str(item.get("why", ""))[:200]
            out.append(f'{item.get("kind", "problem")}: "{sentence}" ({why})')
    return out[:6]


def _choose_hook(session: Session, job: ProductionJob, parsed: dict, question: str) -> dict:
    """A separate editor picks the strongest of the writer's three hooks; beat 1 becomes that hook.

    The writer favours its own first idea. A second reader, judging only the opening against the
    rest of the script, picks the line most likely to stop a scroll without overpromising.
    Any failure keeps the writer's own beat 1.
    """
    if not isinstance(parsed, dict):
        return parsed
    options = [
        option
        for option in parsed.get("hook_options") or []
        if isinstance(option, dict) and str(option.get("text") or "").strip()
    ][:3]
    beats = parsed.get("beats") if isinstance(parsed.get("beats"), list) else []
    if len(options) < 2 or not beats or not isinstance(beats[0], dict):
        return parsed
    rest = " ".join(str(beat.get("text", "")) for beat in beats[1:] if isinstance(beat, dict))[:2500]
    system = (
        "You choose the opening line of a vertical short. Viewers decide in one second whether to keep "
        "watching. Pick the option that is most concrete and most surprising, that a broad audience "
        "feels personally, and that the rest of the script fully pays off. Reject any option that "
        'promises more than the script shows. Return JSON: {"choice": 1-based int, "why": str}.'
    )
    user = (
        (f"Viewer question: {question}\n" if question else "")
        + "Options:\n"
        + "\n".join(f"{index + 1}. {option['text']}" for index, option in enumerate(options))
        + f"\n\nRest of the script:\n{rest}"
    )
    try:
        from app.services.production.llm import cheap_json

        result = cheap_json(session, job, operation="hook-choice", system=system, user=user)
        choice = int(result.get("choice")) - 1
    except (AppError, TypeError, ValueError):
        return parsed
    if not 0 <= choice < len(options):
        return parsed
    chosen = options[choice]
    first = dict(beats[0])
    first["text"] = str(chosen["text"]).strip()
    params = dict(first.get("params") or {}) if isinstance(first.get("params"), dict) else {}
    key = "question" if first.get("template") == "question" else "headline"
    params[key] = str(chosen.get("headline") or chosen["text"]).strip()
    first["params"] = params
    job.report = {
        **(job.report or {}),
        "hook_choice": {
            "options": [option["text"] for option in options],
            "chosen": choice + 1,
            "why": str(result.get("why") or "")[:300],
        },
    }
    return {**parsed, "beats": [first, *beats[1:]]}


def _story_review(session: Session, job: ProductionJob, draft: dict, opportunity) -> dict:
    """An editor model judges what heuristics cannot: does the hook deliver, is there a payoff,
    does each picture show its sentence. Blocking problems send the draft back for a rewrite.

    Returns {} when no review model is configured (the video then waits for a person anyway).
    """
    from app.services.production.llm import cheap_available, cheap_json

    if not cheap_available():
        return {}
    system = (
        "You are the senior editor of a short-form explainer channel about the psychology of money. "
        "You decide whether a script and storyboard are good enough to film. Judge strictly but fairly; "
        "do not ask for style preferences. Check:\n"
        "1. hook_delivers: does the first sentence create a specific question or tension that the video "
        "actually answers? A hook that promises more than the evidence shows is misleading.\n"
        "2. payoff: does the viewer learn something concrete and non-obvious by the end, and does the last "
        "beat land it in fresh words (not a generic moral, not advice, not 'it depends')?\n"
        "3. scenes: for each beat, does the planned picture (template and params) show the idea of its own "
        "sentence? A picture of a generic object for a specific claim is a mismatch.\n"
        "4. filler: beats that repeat an earlier point or add nothing.\n"
        "5. causation: wording that presents a correlation or a single lab result as a proven "
        "general cause.\n"
        "6. loop: does the last sentence call back to the hook so the video flows back into its start? "
        "(report it as loops: bool; a missing callback is advice, not a blocker)\n"
        'Return JSON: {"hook_delivers": bool, "misleading_hook": bool, "payoff": bool, '
        '"generic_ending": bool, "mismatched_scenes": [int] (1-based), "filler_beats": [int], '
        '"causal_overreach": [str], "notes": str (two sentences of concrete editing advice), '
        '"retention_risk": "low" or "medium" or "high"}.'
    )
    directions = draft.get("beat_directions") or []
    lines = []
    for index, beat in enumerate(draft["beats"]):
        direction = (directions[index].get("direction") if index < len(directions) else None) or {}
        picture = json.dumps(
            {"template": direction.get("template"), "params": direction.get("params")}, ensure_ascii=False
        )[:400]
        lines.append(f"{index + 1}. {beat}\n   picture: {picture}")
    question = getattr(opportunity, "question", "") or ""
    user = (
        f"Title: {draft.get('title', '')}\n"
        + (f"Viewer question the video answers: {question}\n" if question else "")
        + "Beats:\n"
        + "\n".join(lines)
    )
    result = cheap_json(session, job, operation="story-review", system=system, user=user)
    result = result if isinstance(result, dict) else {}
    blocking: list[str] = []
    if result.get("misleading_hook"):
        blocking.append("the hook promises more than the evidence shows")
    if result.get("hook_delivers") is False:
        blocking.append("the hook's question is never answered")
    if result.get("payoff") is False:
        blocking.append("no concrete payoff for the viewer")
    if result.get("generic_ending"):
        blocking.append("the ending is a generic moral; land the specific idea in fresh words")
    mismatched = [int(i) for i in result.get("mismatched_scenes") or [] if str(i).isdigit()]
    if len(mismatched) > 1:
        blocking.append(f"pictures do not show their sentences in beats {mismatched}")
    filler = [int(i) for i in result.get("filler_beats") or [] if str(i).isdigit()]
    if len(filler) > 1:
        blocking.append(f"beats {filler} repeat earlier points or add nothing")
    for claim in (result.get("causal_overreach") or [])[:3]:
        blocking.append(f"causal overreach: {str(claim)[:160]}")
    notes = str(result.get("notes") or "")[:400]
    if blocking and notes:
        blocking.append(f"editor notes: {notes}")
    used = ((job.report or {}).get("check_models") or {}).get("story-review", "")
    return {**result, "blocking": blocking, "model": used}


def _motion_system(structure_brief: str, target_seconds: int) -> str:
    from app.services.production import motion

    low, high = int(target_seconds * 2.4), int(target_seconds * 2.8)
    return (
        "You are the head writer and storyboard director of Money Minds, a channel that explains the "
        "psychology of money with clean motion graphics. You write one vertical short "
        f"(about {target_seconds} seconds spoken, {low} to {high} words) that a smart viewer would watch to "
        "the end and send to a friend.\n"
        "Evidence: the sources below, labelled [S1], [S2]. They are the only evidence. Paraphrase; "
        "never copy 10 or more words. Never invent numbers, studies, names, or quotes. "
        "Keep every limitation that changes the meaning: sample size, lab setting, price range, "
        "animal studies, correlation. If sources "
        "disagree, that disagreement is the story. Hedge exactly as the source does. Education only: no "
        "advice about what the viewer should buy, borrow, or invest.\n"
        f"Story structure: {structure_brief}\n"
        "Craft: the first sentence is the hook, under 12 words, concrete, a tension or a surprising contrast "
        "the viewer feels personally. Open a question early and pay it off late. Each beat adds one new "
        "idea; never restate a fact. Use one concrete everyday example. The last beat reframes the idea in "
        "fresh words and is not advice, not 'remember', not a call to subscribe. Plain spoken English, "
        "short sentences. Never start a sentence with a label such as 'Why:' or 'Twist:'. If the ending "
        "uses a concrete example (an object, a place, a person), introduce it earlier; never bring in a new "
        "example in the last beat. The last sentence calls back to the hook (its image, object, or question) "
        "so the end flows straight back into the start and the video loops.\n"
        "Hooks: write three different first sentences in hook_options, each under 12 words: one a "
        "surprising contrast, one a concrete scene the viewer has lived, one a sharp question. Each must be "
        "true to the sources and must not promise more than the video shows. Use your favourite as beat 1; "
        "an editor may swap in another, so beat 2 must follow from any of them.\n"
        "Banned: in today's world, let's dive in, have you ever wondered, buckle up, game changer, delve, "
        "it's important to note, research shows, studies show, in this video.\n"
        "Storyboard: 8 to 10 beats, so the picture changes every 3 to 5 seconds. Each beat is "
        "{text, template, kicker, params}. text is one short spoken sentence (at most 16 words; two only "
        "when both are very short). The picture must show the specific idea of its own sentence, not "
        "decorate it. "
        "First beat: hook or question. Last beat: takeaway. Introduce the evidence with a study scene that "
        "names the publication and year as they appear in the sources. Never use the same template twice in "
        "a row. kicker is an optional 1-3 word content label such as 'The study' or 'The twist', never a "
        "structural word like Hook, Question, or Takeaway. Numbers on screen must appear in the sources.\n"
        f"{motion.brief()}\n"
        'Return JSON: {"title": str (under 60 characters, curiosity, no clickbait), '
        '"hook_options": [{"text": str, "headline": str (max 9 words, on screen)}] (exactly 3), '
        '"beats": [{"text": str, "template": str, "kicker": str, "params": {}}], '
        '"claims": [{"text": str, "certainty": "established" or "hedged", "source": "S1"}] (every factual '
        'statement in the narration, paraphrased), "description": str (2 sentences, no hashtags), '
        '"hashtags": [str] (3 to 5)}.'
    )


def _reserve_voice_budget(session, job, narration: str) -> None:
    from app.services.voice import plan_engine

    settings = get_settings()
    if plan_engine(settings) != "elevenlabs":
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
    if kinds and all(kind == "motion" for kind in kinds):
        return "motion"
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
    if audio.provider not in _PRODUCTION_VOICES:
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


def rerender_motion(session: Session, job: ProductionJob) -> dict:
    """Render a motion job's picture again from its stored storyboard, script, and narration.

    For visual iteration: no model is called, so it costs nothing. The video stays needs_review.
    """
    from app.models import AudioFile
    from app.services.production import motion, visual_qa

    video = session.get(Video, job.video_id) if job.video_id else None
    scenes = list((job.report or {}).get("scenes") or [])
    if video is None or not scenes or not all(scene.get("motion") for scene in scenes):
        raise AppError("Only a finished motion-graphics job can be re-rendered.")
    brand = session.get(Brand, job.brand_id)
    audio = session.get(AudioFile, video.audio_file_id)
    dossier = session.scalar(
        select(ResearchDossier).where(ResearchDossier.opportunity_id == job.opportunity_id)
    )
    source_text = dossier.extracted_text if dossier is not None else ""
    allowed = set(re.findall(r"\d+(?:\.\d+)?", source_text))
    planned = [
        motion.normalise({**scene["motion"], "text": scene["narration"]}, allowed, source_text)
        for scene in scenes
    ]
    durations = _spoken_durations(scenes, audio) or durations_for(scenes, audio.duration_seconds)
    identity = brand.identity if isinstance(brand.identity, dict) else {}
    props = motion.build_props(
        planned,
        durations,
        (audio.metadata_json or {}).get("alignment") or {},
        brand=brand.name,
        theme=str(identity.get("motion_theme") or "money-minds"),
    )
    work = Path(video.work_dir or (get_settings().data_dir / "production" / job.id))
    output = Path(video.path)
    silent = motion.render(props, work, output)
    music = next(
        (Path(item) for item in work.glob("*.wav") if "bed" in item.name or "music" in item.name), None
    )
    if music is None:
        raise AppError("The music bed of this job is missing; produce the video again instead.")
    sfx = motion.sfx_track(work / "sfx.wav", props)
    motion.mix(silent, Path(audio.path), music, sfx, output)
    qa = visual_qa.inspect(output, work, props)
    summary = media_summary(output)
    report = dict(video.quality_report or {})
    kept = [
        check for check in report.get("checks", []) if check["name"] not in {c["name"] for c in qa["checks"]}
    ]
    failed = [check["name"] for check in qa["checks"] if check["required"] and not check["passed"]]
    report.update(
        {
            "checks": [*kept, *qa["checks"]],
            "contact_sheet": qa["contact_sheet"],
            "status": "failed" if failed else "needs_review",
            "warnings": ["Awaiting human visual review of the motion-graphics video."],
        }
    )
    video.quality_report = report
    video.status = "blocked" if failed else "needs_review"
    video.duration_seconds = summary["duration"]
    video.checksum = sha256_file(output)
    for scene, plan in zip(scenes, planned, strict=True):
        scene["motion"] = plan
    job.report = {
        **(job.report or {}),
        "scenes": scenes,
        "quality": report,
        "quality_status": report["status"],
    }
    session.flush()
    return {
        "video": str(output.resolve()),
        "status": video.status,
        "contact_sheet": qa["contact_sheet"],
        "failed": failed,
    }
