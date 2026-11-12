"""Discover topic candidates from configured sources. Promotion creates a topic, never a video."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, DuplicateTopicError
from app.core.time import utc_now
from app.models import DiscoveredItem, ResearchSource, Topic
from app.schemas.content import Reference, TopicCreate
from app.services.research.feeds import FetchResult, fetch_source
from app.services.research.scoring import score_topic
from app.services.textutil import fingerprint, jaccard, sequence_ratio
from app.services.topics import _conflict, create_topic

logger = logging.getLogger("app.research")
_SEQUENCE = 0.90


def _similar_item(session: Session, title: str) -> DiscoveredItem | None:
    exact = fingerprint(title)
    threshold = get_settings().near_duplicate_threshold
    for row in session.scalars(select(DiscoveredItem)).all():
        if row.fingerprint == exact:
            return row
        if jaccard(title, row.title) >= threshold or sequence_ratio(title, row.title) >= _SEQUENCE:
            return row
    return None


def _clip_title(title: str) -> str:
    return " ".join(title.split())[:200]


def _description(item: DiscoveredItem) -> str:
    summary = item.summary.strip() or "The feed did not include a summary."
    keywords = ", ".join(item.keywords) if item.keywords else "none extracted"
    lines = [
        "Feed summary, not a verified fact. Read the source before writing a script.",
        "",
        summary,
        "",
        f"Source: {item.url}",
        f"Horizon: {item.horizon}. This label is a keyword heuristic, not a traffic forecast.",
        f"Keywords: {keywords}.",
        item.monetization_note,
    ]
    if not item.source_verified:
        lines.append(f"Original URL check: {item.source_status or 'not confirmed'}.")
    return "\n".join(lines)


def promote_item(session: Session, item: DiscoveredItem, *, override: bool = False) -> Topic:
    if item.status == "promoted" and item.topic_id:
        existing = session.get(Topic, item.topic_id)
        if existing is not None:
            return existing
    if item.status == "duplicate":
        raise AppError("This item matches an existing topic or an earlier discovery. It was not promoted.")
    minimum = get_settings().topic_score_minimum
    if not override and (item.score is None or item.score < minimum):
        raise AppError(
            f"Score {item.score} is below {minimum}. "
            "Check the source and promote it yourself only if you still want the topic."
        )
    if not override and not item.source_verified:
        raise AppError("The original URL was not confirmed. Open it before promoting, or use the override.")
    title = _clip_title(item.title)
    try:
        topic = create_topic(
            session,
            TopicCreate(
                title=title,
                description=_description(item),
                category=item.category if item.category else "ai",
                target_language="en",
                target_platform="youtube_shorts",
                target_audience=item.audience or "curious adults",
                status="Researching",
                priority=4 if (item.score or 0) >= 80 else 3,
                estimated_content_potential=max(1, min(10, int(round((item.score or 50) / 10)))),
                source_references=[
                    Reference(
                        title=title,
                        url=item.url,
                        note="Original page. The feed summary is not a verified fact.",
                    )
                ],
                research_references=[
                    Reference(title="Feed item", url=item.url, note=item.summary_kind or "feed_summary")
                ],
            ),
        )
    except DuplicateTopicError as exc:
        item.status = "duplicate"
        item.topic_id = exc.topic_id if exc.topic_id != "existing" else item.topic_id
        item.rejection_reason = "Matches an existing topic."
        raise
    item.status = "promoted"
    item.topic_id = topic.id
    item.rejection_reason = ""
    session.flush()
    return topic


def _remember_cache(source: ResearchSource, result: FetchResult, status: str) -> None:
    source.last_fetched_at = utc_now()
    source.last_status = status
    source.last_error = ""
    if result.etag:
        source.etag = result.etag[:200]
    if result.last_modified:
        source.last_modified = result.last_modified[:120]
    if result.body_sha256:
        source.body_sha256 = result.body_sha256


def run_discovery(session: Session, *, fetch=None) -> dict:
    settings = get_settings()
    fetcher = fetch or (lambda source: fetch_source(source, settings))
    sources = session.scalars(select(ResearchSource).where(ResearchSource.enabled.is_(True))).all()
    created = 0
    duplicates = 0
    promoted = 0
    cached = 0
    failed: list[tuple[str, str]] = []
    for source in sources:
        try:
            result = fetcher(source)
        except Exception as exc:
            message = str(exc)[:500] or exc.__class__.__name__
            source.last_status = "failed"
            source.last_error = message
            source.last_fetched_at = utc_now()
            failed.append((source.name, message))
            logger.warning(
                "research source failed",
                extra={"source_id": source.id, "source_name": source.name, "error": message},
            )
            continue
        if result.not_modified:
            _remember_cache(source, result, "cached")
            cached += 1
            continue
        if result.body_sha256 and result.body_sha256 == source.body_sha256:
            known = session.scalar(
                select(func.count()).select_from(DiscoveredItem).where(DiscoveredItem.source_id == source.id)
            )
            if known:
                _remember_cache(source, result, "cached")
                cached += 1
                continue
        known_ids = set(
            session.scalars(
                select(DiscoveredItem.external_id).where(DiscoveredItem.source_id == source.id)
            ).all()
        )
        for parsed in result.items:
            if parsed.external_id in known_ids:
                continue
            title = _clip_title(parsed.title)
            if len(title) < 3:
                continue
            topic_conflict = _conflict(session, title)
            item_conflict = None if topic_conflict is not None else _similar_item(session, title)
            duplicate = topic_conflict is not None or item_conflict is not None
            scored = score_topic(
                title=title,
                summary=parsed.summary,
                url=parsed.url,
                source_category=source.category,
                verified=parsed.verified,
                duplicate=duplicate,
            )
            reason = ""
            status = "scored"
            topic_id = None
            duplicate_of = None
            if topic_conflict is not None:
                status = "duplicate"
                topic_id = topic_conflict[0].id
                reason = "Matches an existing topic."
                duplicates += 1
            elif item_conflict is not None:
                status = "duplicate"
                duplicate_of = item_conflict.id
                reason = "Matches an earlier discovery."
                duplicates += 1
            elif scored.clickbait:
                reason = "Clickbait phrasing is kept for review and is not promoted automatically."
            elif int(scored.breakdown["audience_fit"]["score"] or 0) < 62:
                reason = "The title does not overlap the AI & Future vocabulary, so it was not promoted."
            elif scored.score < settings.topic_score_minimum:
                reason = f"Score {scored.score} is below {settings.topic_score_minimum}."
            elif parsed.verified is not True:
                reason = "The original URL was not confirmed, so the item was not promoted."
            item = DiscoveredItem(
                source_id=source.id,
                external_id=parsed.external_id[:200],
                title=title,
                summary=parsed.summary,
                summary_kind="feed_summary",
                url=parsed.url,
                published_at=parsed.published_at,
                category=scored.category,
                keywords=scored.keywords,
                audience=scored.audience,
                horizon=scored.horizon,
                monetization_note=scored.monetization_note,
                source_verified=parsed.verified is True,
                source_status=(parsed.check_label or "")[:40],
                fingerprint=fingerprint(title),
                status=status,
                score=scored.score,
                score_breakdown=scored.breakdown,
                topic_id=topic_id,
                duplicate_of_item_id=duplicate_of,
                rejection_reason=reason,
            )
            session.add(item)
            session.flush()
            known_ids.add(item.external_id)
            created += 1
            if status == "scored" and not reason:
                try:
                    promote_item(session, item)
                    promoted += 1
                except (AppError, DuplicateTopicError) as exc:
                    item.rejection_reason = exc.message
                    logger.info(
                        "discovery item kept for review",
                        extra={"item_id": item.id, "score": item.score, "reason": exc.message},
                    )
        _remember_cache(source, result, "ok")
        logger.info(
            "research source fetched",
            extra={"source_id": source.id, "source_name": source.name, "items": len(result.items)},
        )
    return {
        "sources": len(sources),
        "created": created,
        "duplicates": duplicates,
        "promoted": promoted,
        "cached_sources": cached,
        "failed_sources": failed,
    }
