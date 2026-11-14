"""Content strategy: which story structure and length the next video uses.

Formats are chosen per brand. Until a format has measured results, the brand
explores: the least recently used structure goes next, so consecutive videos are
built differently. Once published videos have analytics, formats that earned
more per view (or, without revenue, kept viewers longer) are picked more often,
while every format keeps a minimum share so the channel keeps learning.
"""

from __future__ import annotations

import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Brand, ProductionJob, VideoMetric

STRUCTURES: dict[str, str] = {
    "explainer": (
        "Explainer. Hook with the most surprising fact, then explain how it works in clear steps, "
        "then what it means."
    ),
    "myth_vs_fact": (
        "Myth versus fact. Open with what most people assume, show why the evidence disagrees, "
        "then state what is actually true and how sure we are."
    ),
    "mystery": (
        "Mystery. Open with a puzzling observation, withhold the answer for two beats while raising "
        "the stakes, then reveal it and explain why it makes sense."
    ),
    "experiment": (
        "Experiment story. Walk through what the researchers did, what they expected, what happened, "
        "and what it tells us, as a short story with a turn in the middle."
    ),
    "versus": (
        "Versus. Compare two things side by side (two groups, two choices, before and after), "
        "build the contrast, then land the reason for the difference."
    ),
    "three_points": (
        "Three points. Promise three things in the hook, deliver them in order with one beat each, "
        "and close with the one that matters most."
    ),
}
DEFAULT_STRATEGY = {
    "structures": list(STRUCTURES),
    "short_seconds": 40,
    "long_form": False,
    "exploration": 0.3,
}


def strategy_for(brand: Brand) -> dict:
    identity = brand.identity if isinstance(brand.identity, dict) else {}
    stored = identity.get("strategy") if isinstance(identity.get("strategy"), dict) else {}
    merged = {**DEFAULT_STRATEGY, **stored}
    merged["structures"] = [name for name in merged.get("structures") or [] if name in STRUCTURES] or list(
        STRUCTURES
    )
    return merged


def structure_brief(name: str) -> str:
    return STRUCTURES.get(name, STRUCTURES["explainer"])


def _recent_structures(session: Session, brand: Brand, limit: int = 20) -> list[str]:
    rows = session.scalars(
        select(ProductionJob)
        .where(ProductionJob.brand_id == brand.id, ProductionJob.status == "succeeded")
        .order_by(ProductionJob.started_at.desc())
        .limit(limit)
    ).all()
    return [str(((row.report or {}).get("format") or {}).get("structure") or "") for row in rows]


def format_performance(session: Session, brand: Brand) -> dict[str, dict]:
    """Average measured results per structure. Only real analytics rows count."""
    jobs = session.scalars(
        select(ProductionJob).where(ProductionJob.brand_id == brand.id, ProductionJob.video_id.is_not(None))
    ).all()
    by_video = {
        job.video_id: str(((job.report or {}).get("format") or {}).get("structure") or "") for job in jobs
    }
    if not by_video:
        return {}
    rows = session.scalars(select(VideoMetric).where(VideoMetric.video_id.in_(list(by_video)))).all()
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    videos: dict[str, set[str]] = defaultdict(set)
    percentages: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        structure = by_video.get(row.video_id or "")
        if not structure:
            continue
        totals[structure]["views"] += row.views
        if row.revenue_usd is not None:
            totals[structure]["revenue"] += float(row.revenue_usd)
            totals[structure]["revenue_reported"] = 1
        if row.average_view_percentage is not None:
            percentages[structure].append(float(row.average_view_percentage))
        videos[structure].add(row.video_id)
    result = {}
    for structure, metrics in totals.items():
        views = metrics.get("views", 0.0)
        reported = metrics.get("revenue_reported") and views
        result[structure] = {
            "videos": len(videos[structure]),
            "views": views,
            "revenue_per_1000_views": metrics["revenue"] / views * 1000 if reported else None,
            "average_view_percentage": sum(percentages[structure]) / len(percentages[structure])
            if percentages[structure]
            else 0.0,
        }
    return result


def choose_format(session: Session, brand: Brand, rng: random.Random | None = None) -> dict:
    strategy = strategy_for(brand)
    allowed = strategy["structures"]
    rng = rng or random.Random()
    performance = format_performance(session, brand)
    measured = {name: perf for name, perf in performance.items() if name in allowed and perf["videos"] >= 3}
    reason = "exploring: least recently used structure"
    if measured and rng.random() > float(strategy["exploration"]):

        def score(name: str) -> float:
            perf = measured[name]
            if perf["revenue_per_1000_views"] is not None:
                return perf["revenue_per_1000_views"]
            return perf["average_view_percentage"]

        structure = max(measured, key=score)
        reason = "exploiting: best measured structure"
    else:
        recent = _recent_structures(session, brand)
        unused = [name for name in allowed if name not in recent]
        if unused:
            structure = unused[0]
        else:
            structure = max(allowed, key=lambda name: recent.index(name) if name in recent else len(recent))
    return {
        "structure": structure,
        "target_seconds": int(strategy["short_seconds"]),
        "format": "short",
        "reason": reason,
    }


# The recommended first channel. See docs/strategy.md for the reasoning and the sources.
RECOMMENDED_BRAND = {
    "name": "Money Minds",
    "slug": "money-minds",
    "niche": (
        "The psychology of money and everyday economic behaviour: why people spend, save, tip, price, "
        "and decide the way they do, explained from published research. Education, never advice."
    ),
    "audience": "Adults 20 to 45 who like learning why people behave the way they do with money",
    "feeds": [
        ("https://www.sciencedaily.com/rss/science_society/economics.xml", "ScienceDaily Economics"),
        (
            "https://www.sciencedaily.com/rss/mind_brain/consumer_behavior.xml",
            "ScienceDaily Consumer Behavior",
        ),
        ("https://www.sciencedaily.com/rss/mind_brain/psychology.xml", "ScienceDaily Psychology"),
    ],
    "terms": (
        "money, price, spend, save, tip, buy, cost, budget, decision, choice, habit, economy, market, wage"
    ),
    "visual_style": "blueprint",
}


def create_recommended_brand(session: Session) -> Brand:
    """Create the recommended channel, paused, with publishing off and both advice guardrails on."""
    from app.services.automation import attach_feed, create_brand

    existing = session.scalar(select(Brand).where(Brand.slug == RECOMMENDED_BRAND["slug"]))
    if existing is not None:
        return existing
    brand = create_brand(
        session,
        name=RECOMMENDED_BRAND["name"],
        slug=RECOMMENDED_BRAND["slug"],
        niche=RECOMMENDED_BRAND["niche"],
        audience=RECOMMENDED_BRAND["audience"],
        terms=RECOMMENDED_BRAND["terms"],
        block_medical=True,
        block_financial=True,
        editorial_gate=True,
        activate=False,
        max_videos_per_day=1,
    )
    for url, name in RECOMMENDED_BRAND["feeds"]:
        attach_feed(session, brand, url=url, name=name)
    identity = dict(brand.identity or {})
    identity["visual_style"] = RECOMMENDED_BRAND["visual_style"]
    identity["strategy"] = dict(DEFAULT_STRATEGY)
    brand.identity = identity
    session.flush()
    return brand


def add_topic(session: Session, brand: Brand, *, question: str, urls: list[str], title: str = ""):
    """A topic chosen by a person (or a future research agent): a viewer question plus its sources.

    It goes through the same holds as discovered topics and the same production gates.
    """
    from app.core.errors import AppError
    from app.models import ContentOpportunity, DiscoveredItem, ResearchSource
    from app.services.business.signals import hold_reason
    from app.services.textutil import sha256_text

    urls = [url.strip() for url in urls if url.strip().startswith(("http://", "https://"))]
    if not question.strip() or not urls:
        raise AppError("A topic needs a question and at least one http(s) source.")
    source = session.scalar(select(ResearchSource).where(ResearchSource.url == "manual://topics"))
    if source is None:
        source = ResearchSource(
            name="Chosen topics", kind="manual", url="manual://topics", category="facts", enabled=False
        )
        session.add(source)
        session.flush()
    headline = (title or question).strip()[:300]
    external_id = sha256_text(headline + question + urls[0])[:40]
    duplicate = session.scalar(
        select(DiscoveredItem).where(
            DiscoveredItem.source_id == source.id, DiscoveredItem.external_id == external_id
        )
    )
    if duplicate is not None:
        raise AppError("This topic, with this title and first source, was already added.")
    item = DiscoveredItem(
        source_id=source.id,
        external_id=external_id,
        title=headline,
        summary=question,
        summary_kind="question",
        url=urls[0],
        fingerprint=sha256_text(headline.lower()),
        status="new",
    )
    session.add(item)
    session.flush()
    reason = hold_reason(headline, question, brand)
    row = ContentOpportunity(
        brand_id=brand.id,
        discovered_item_id=item.id,
        title=headline,
        question=question.strip(),
        source_urls=urls[:4],
        status="held" if reason else "evaluated",
        hold_reason=reason,
        signals=[],
    )
    session.add(row)
    session.flush()
    return row
