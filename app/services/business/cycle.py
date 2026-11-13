"""One brand cycle: fetch linked feeds, score what we know, render nothing, upload nothing."""

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.time import utc_now
from app.models import (
    AffiliateProgram,
    Brand,
    BrandSource,
    ContentOpportunity,
    CycleRun,
    DiscoveredItem,
)
from app.services.business.score import configured_weights, score_signals
from app.services.business.signals import build_signals, hold_reason
from app.services.business.youtube_sample import sample_search
from app.services.cost import month_spend
from app.services.research.discover import run_discovery

logger = logging.getLogger("app.business")
_BLOCK = "This build evaluates opportunities. It does not render a video or upload one."


def mark_cycles_interrupted(session: Session) -> int:
    rows = session.scalars(select(CycleRun).where(CycleRun.status == "running")).all()
    for row in rows:
        row.status = "interrupted"
        row.error = "The process stopped while this cycle was running."
        row.finished_at = utc_now()
    return len(rows)


def run_brand_cycle(session: Session, brand: Brand, *, fetch=None, youtube=None) -> CycleRun:
    run = CycleRun(brand_id=brand.id, status="running", stats={})
    session.add(run)
    session.flush()
    if brand.status == "paused":
        run.status = "skipped"
        run.error = "Brand is paused. No feeds were fetched and nothing was published."
        run.finished_at = utc_now()
        run.stats = {"videos_rendered": 0, "uploads": 0}
        return run
    try:
        source_ids = list(
            session.scalars(select(BrandSource.source_id).where(BrandSource.brand_id == brand.id)).all()
        )
        discovery = {"sources": 0, "created": 0, "promoted": 0}
        if source_ids:
            discovery = run_discovery(session, fetch=fetch, source_ids=source_ids, promote=False)
        programs = session.scalars(
            select(AffiliateProgram).where(AffiliateProgram.brand_id == brand.id)
        ).all()
        created = _score_items(session, brand, programs, source_ids, youtube=youtube)
        run.status = "succeeded"
        run.stats = {
            "sources": discovery.get("sources", 0),
            "feed_items": discovery.get("created", 0),
            "opportunities_written": created,
            "topics_promoted": discovery.get("promoted", 0),
            "videos_rendered": 0,
            "uploads": 0,
            "advertising_revenue": None,
            "month_spend_usd": str(month_spend(session)),
            "publishing_mode": brand.publishing_mode,
            "autonomous_upload": False,
        }
        logger.info(
            "brand cycle finished",
            extra={"brand": brand.slug, "opportunities": created, "uploads": 0},
        )
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)[:500]
        logger.warning("brand cycle failed", extra={"brand": brand.slug, "error": run.error})
    run.finished_at = utc_now()
    session.flush()
    return run


def _score_items(
    session: Session,
    brand: Brand,
    programs: list[AffiliateProgram],
    source_ids: list[str],
    *,
    youtube,
) -> int:
    if not source_ids:
        return 0
    items = session.scalars(
        select(DiscoveredItem).where(DiscoveredItem.source_id.in_(source_ids))
    ).all()
    weights = configured_weights()
    written = 0
    sample_fn = youtube if youtube is not None else _live_sample
    for item in items:
        existing = session.scalar(
            select(ContentOpportunity).where(
                ContentOpportunity.brand_id == brand.id,
                ContentOpportunity.discovered_item_id == item.id,
            )
        )
        signals = build_signals(
            title=item.title,
            summary=item.summary or "",
            brand=brand,
            programs=list(programs),
        )
        scored = score_signals(signals, weights)
        reason = hold_reason(item.title, item.summary or "", brand)
        sample = None
        if existing is None or existing.youtube_sample is None:
            sample = sample_fn(item.title)
        row = existing or ContentOpportunity(
            brand_id=brand.id,
            discovered_item_id=item.id,
            title=item.title[:300],
        )
        row.title = item.title[:300]
        row.partial_score = scored["partial_score"]
        row.coverage = scored["coverage"]
        row.weight_total = scored["weight_total"]
        row.signals = [{"factor": key, **value} for key, value in signals.items()]
        if sample is not None:
            row.youtube_sample = sample
        row.status = "held" if reason else "evaluated"
        row.hold_reason = reason
        row.production_status = "blocked"
        row.production_block_reason = _BLOCK
        if existing is None:
            session.add(row)
        written += 1
    session.flush()
    return written


def _live_sample(query: str) -> dict | None:
    key = get_settings().youtube_api_key.strip()
    if not key:
        return None
    import httpx

    with httpx.Client(timeout=get_settings().research_timeout_seconds) as client:
        return sample_search(key, query, client=client)


def disclosure_for(program: AffiliateProgram) -> str:
    if program.commission_rate is None:
        rate = "No commission rate is stored."
    else:
        kind = program.commission_type or "rate"
        rate = f"The stored {kind} is {Decimal(str(program.commission_rate))}."
    return (
        f"If you use a link from {program.name}, the brand may earn a commission. "
        f"{rate} This is not a measured conversion."
    )
