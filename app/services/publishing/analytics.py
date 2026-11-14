"""Measured results and profit. Revenue is only what a platform reported; nothing is estimated here."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Brand, PlatformPost, ProductionCost, ProductionJob, VideoMetric
from app.services.publishing import credentials
from app.services.publishing.connectors import PublishError, youtube_metrics


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def fetch_youtube(session: Session, *, days: int = 28, client: httpx.Client | None = None) -> dict:
    """Pull daily YouTube Analytics for every published YouTube post. Other platforms are not wired yet."""
    end = date.today()
    start = end - timedelta(days=days)
    posts = session.scalars(
        select(PlatformPost).where(
            PlatformPost.status == "published", PlatformPost.platform.in_(("youtube_short", "youtube_long"))
        )
    ).all()
    owned = client is None
    client = client or httpx.Client(timeout=60)
    stored = 0
    errors: list[str] = []
    try:
        for post in posts:
            brand = session.get(Brand, post.brand_id)
            creds = credentials.load(brand.slug, post.platform) if brand else None
            if not creds:
                errors.append(f"{post.id}: YouTube is not connected.")
                continue
            try:
                rows, fresh = youtube_metrics(post, creds, client, start.isoformat(), end.isoformat())
            except PublishError as exc:
                errors.append(f"{post.id}: {exc.message}")
                continue
            credentials.save(brand.slug, post.platform, fresh)
            for row in rows:
                stored += _upsert(session, post, row)
    finally:
        if owned:
            client.close()
    session.flush()
    return {"posts": len(posts), "rows": stored, "errors": errors}


def _upsert(session: Session, post: PlatformPost, row: dict) -> int:
    day = str(row.get("day") or "")
    if not day:
        return 0
    metric = session.scalar(select(VideoMetric).where(VideoMetric.post_id == post.id, VideoMetric.day == day))
    if metric is None:
        metric = VideoMetric(
            post_id=post.id, video_id=post.video_id, brand_id=post.brand_id, platform=post.platform, day=day
        )
        session.add(metric)
    metric.views = int(row.get("views") or 0)
    metric.watch_minutes = _dec(row.get("estimatedMinutesWatched")) or Decimal("0")
    metric.average_view_seconds = _dec(row.get("averageViewDuration"))
    metric.average_view_percentage = _dec(row.get("averageViewPercentage"))
    metric.likes = int(row.get("likes") or 0)
    metric.comments = int(row.get("comments") or 0)
    metric.shares = int(row.get("shares") or 0)
    metric.subscribers_gained = int(row.get("subscribersGained") or 0)
    metric.revenue_usd = _dec(row.get("estimatedRevenue"))
    metric.raw = row
    metric.fetched_at = datetime.now(UTC)
    return 1


def profit_report(session: Session, brand: Brand) -> dict:
    """Per video, per platform, and per story structure: views, measured revenue, production cost, profit."""
    jobs = session.scalars(
        select(ProductionJob).where(ProductionJob.brand_id == brand.id, ProductionJob.video_id.is_not(None))
    ).all()
    structure = {
        job.video_id: str(((job.report or {}).get("format") or {}).get("structure") or "unknown")
        for job in jobs
    }
    costs = dict(
        session.execute(
            select(ProductionCost.video_id, func.sum(ProductionCost.cost_usd))
            .where(ProductionCost.brand_id == brand.id)
            .group_by(ProductionCost.video_id)
        ).all()
    )
    metrics = session.scalars(select(VideoMetric).where(VideoMetric.brand_id == brand.id)).all()
    videos: dict[str, dict] = defaultdict(
        lambda: {"views": 0, "revenue": Decimal("0"), "revenue_known": False}
    )
    platforms: dict[str, dict] = defaultdict(lambda: {"views": 0, "revenue": Decimal("0")})
    for row in metrics:
        entry = videos[row.video_id]
        entry["views"] += row.views
        platforms[row.platform]["views"] += row.views
        if row.revenue_usd is not None:
            entry["revenue"] += row.revenue_usd
            entry["revenue_known"] = True
            platforms[row.platform]["revenue"] += row.revenue_usd
    by_structure: dict[str, dict] = defaultdict(lambda: {"videos": 0, "views": 0, "revenue": Decimal("0")})
    out_videos = []
    for video_id in set(structure) | set(videos):
        entry = videos.get(video_id, {"views": 0, "revenue": Decimal("0"), "revenue_known": False})
        cost = Decimal(str(costs.get(video_id) or 0))
        profit = (entry["revenue"] - cost) if entry["revenue_known"] else None
        out_videos.append(
            {
                "video_id": video_id,
                "structure": structure.get(video_id, "unknown"),
                "views": entry["views"],
                "revenue_usd": float(entry["revenue"]) if entry["revenue_known"] else None,
                "cost_usd": float(cost),
                "profit_usd": float(profit) if profit is not None else None,
            }
        )
        bucket = by_structure[structure.get(video_id, "unknown")]
        bucket["videos"] += 1
        bucket["views"] += entry["views"]
        bucket["revenue"] += entry["revenue"]
    return {
        "videos": sorted(out_videos, key=lambda item: -item["views"]),
        "platforms": {
            key: {"views": v["views"], "revenue_usd": float(v["revenue"])} for key, v in platforms.items()
        },
        "structures": {
            key: {"videos": v["videos"], "views": v["views"], "revenue_usd": float(v["revenue"])}
            for key, v in by_structure.items()
        },
        "note": "Revenue is what platforms reported. A null revenue means none was reported, not zero.",
    }
