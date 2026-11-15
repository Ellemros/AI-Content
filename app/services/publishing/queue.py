"""The publishing queue. Off unless PUBLISHING_ENABLED=true, and never for a video that is not ready.

Order of gates for every upload:
1. PUBLISHING_ENABLED is true (global; false in this phase).
2. The automation emergency switch is on.
3. The brand is not paused and its publishing_mode is approval or autonomous.
4. The video's quality status is ready. needs_review is never published.
5. approval mode: a person approved the video. autonomous: ready is enough.
6. The brand's daily_publish_limit for that platform is not reached.
7. The platform account is connected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.models import Brand, PlatformPost, ProductionJob, ScriptVersion, Video
from app.services.publishing import credentials, metadata
from app.services.publishing.connectors import PUBLISHERS, PublishError

DEFAULT_PLATFORMS = list(metadata.SHORT_PLATFORMS)


def needs_person(video: Video) -> bool:
    """Videos whose QA report asks for a human sign-off are never published unapproved, in any mode."""
    report = video.quality_report if isinstance(video.quality_report, dict) else {}
    return bool(report.get("human_review"))


def _now() -> datetime:
    return datetime.now(UTC)


def _note(post: PlatformPost, event: str, detail: str = "") -> None:
    post.history = [*(post.history or []), {"at": _now().isoformat(), "event": event, "detail": detail[:300]}]
    post.updated_at = _now()


def brand_platforms(brand: Brand, video_format: str = "short") -> list[str]:
    identity = brand.identity if isinstance(brand.identity, dict) else {}
    chosen = [item for item in identity.get("platforms") or DEFAULT_PLATFORMS if item in metadata.PLATFORMS]
    if video_format == "long":
        return [item for item in chosen if item == "youtube_long"] or ["youtube_long"]
    return [item for item in chosen if item != "youtube_long"]


def plan_posts(session: Session, video: Video, brand: Brand) -> list[PlatformPost]:
    """Write the per-platform title, description, and tags for review. Nothing is uploaded."""
    version = session.get(ScriptVersion, video.script_version_id)
    if version is None:
        raise AppError("The video has no script version, so no metadata can be written.")
    job = session.scalar(select(ProductionJob).where(ProductionJob.video_id == video.id))
    source = str(((job.report or {}).get("metadata") or {}).get("source_url") or "") if job else ""
    posts = []
    for platform in brand_platforms(brand, "long" if video.format == "long" else "short"):
        existing = session.scalar(
            select(PlatformPost).where(PlatformPost.video_id == video.id, PlatformPost.platform == platform)
        )
        if existing is not None:
            posts.append(existing)
            continue
        text = metadata.build(
            platform,
            title=version.title,
            description=version.description,
            hashtags=list(version.hashtags or []),
            sources=[source] if source else [],
        )
        post = PlatformPost(
            brand_id=brand.id,
            video_id=video.id,
            platform=platform,
            status="planned",
            title=text["title"],
            description=text["description"],
            tags=text["tags"],
            options={"language": video.language},
        )
        _note(post, "planned")
        session.add(post)
        posts.append(post)
    session.flush()
    return posts


def _next_slot(session: Session, brand: Brand, platform: str, after: datetime) -> datetime:
    """Spread posts: at most daily_publish_limit per platform per UTC day, at the brand's posting hour."""
    identity = brand.identity if isinstance(brand.identity, dict) else {}
    hour = int(identity.get("posting_hour_utc", 16))
    limit = max(int(brand.daily_publish_limit or 0), 1)
    day = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if day < after:
        day += timedelta(days=1)
    for _ in range(60):
        start, end = day.replace(hour=0), day.replace(hour=0) + timedelta(days=1)
        taken = session.scalar(
            select(func.count())
            .select_from(PlatformPost)
            .where(
                PlatformPost.brand_id == brand.id,
                PlatformPost.platform == platform,
                PlatformPost.status.in_(("queued", "uploading", "published", "needs_check")),
                PlatformPost.scheduled_at >= start,
                PlatformPost.scheduled_at < end,
            )
        )
        if int(taken or 0) < limit:
            return day
        day += timedelta(days=1)
    return day


def queue_ready(session: Session, brand: Brand) -> int:
    """Move planned posts to the queue when the brand's mode allows it. Uploads nothing."""
    mode = (brand.publishing_mode or "test").lower()
    if mode not in {"approval", "autonomous"}:
        return 0
    rows = session.scalars(
        select(PlatformPost).where(PlatformPost.brand_id == brand.id, PlatformPost.status == "planned")
    ).all()
    moved = 0
    for post in rows:
        video = session.get(Video, post.video_id)
        if video is None or video.status != "ready":
            continue
        if (mode == "approval" or needs_person(video)) and not video.approved:
            continue
        post.status = "queued"
        post.scheduled_at = _next_slot(session, brand, post.platform, _now())
        _note(post, "queued", f"mode={mode}")
        moved += 1
    session.flush()
    return moved


def publishing_blocked(session: Session) -> str:
    from app.services.automation import automation_enabled

    if not get_settings().publishing_enabled:
        return "Publishing is off (PUBLISHING_ENABLED=false). Nothing was uploaded."
    if not automation_enabled(session):
        return "The emergency stop is on. Nothing was uploaded."
    return ""


def publish_due(session: Session, *, client: httpx.Client | None = None, now: datetime | None = None) -> dict:
    blocked = publishing_blocked(session)
    if blocked:
        return {"status": "refused", "reason": blocked, "published": 0}
    now = now or _now()
    due = session.scalars(
        select(PlatformPost)
        .where(PlatformPost.status == "queued", PlatformPost.scheduled_at <= now)
        .order_by(PlatformPost.scheduled_at)
    ).all()
    owned = client is None
    client = client or httpx.Client(timeout=300, follow_redirects=True)
    published = 0
    try:
        for post in due:
            published += int(_publish_one(session, post, client))
            session.commit()
    finally:
        if owned:
            client.close()
    return {"status": "ran", "published": published, "due": len(due)}


def _publish_one(session: Session, post: PlatformPost, client: httpx.Client) -> bool:
    brand = session.get(Brand, post.brand_id)
    video = session.get(Video, post.video_id)
    if brand is None or video is None:
        post.status = "failed"
        _note(post, "failed", "Brand or video is missing.")
        return False
    if brand.status == "paused" or (brand.publishing_mode or "test") not in {"approval", "autonomous"}:
        _note(post, "skipped", "The brand is paused or not in a publishing mode.")
        return False
    if video.status != "ready" or (
        (brand.publishing_mode == "approval" or needs_person(video)) and not video.approved
    ):
        _note(post, "skipped", "The video is not ready, or not approved in approval mode.")
        return False
    creds = credentials.load(brand.slug, post.platform)
    if not creds:
        post.last_error = f"{post.platform} is not connected for {brand.slug}."
        _note(post, "waiting", post.last_error)
        return False
    path = Path(video.path)
    if not path.is_file():
        post.status = "failed"
        post.last_error = "The video file is missing."
        _note(post, "failed", post.last_error)
        return False
    post.status = "uploading"
    post.attempts = int(post.attempts or 0) + 1
    _note(post, "uploading", f"attempt {post.attempts}")
    session.commit()
    try:
        external_id, url, fresh = PUBLISHERS[post.platform](post, path, creds, client)
    except PublishError as exc:
        post.last_error = exc.message
        if exc.uncertain:
            post.status = "needs_check"
            _note(post, "needs_check", f"{exc.message} Check the platform before retrying.")
        elif exc.retryable and post.attempts < post.max_attempts:
            post.status = "queued"
            post.scheduled_at = _now() + timedelta(minutes=15 * 2 ** (post.attempts - 1))
            _note(post, "retry_scheduled", exc.message)
        else:
            post.status = "failed"
            _note(post, "failed", exc.message)
        return False
    credentials.save(brand.slug, post.platform, fresh)
    post.status = "published"
    post.external_id = external_id
    post.external_url = url
    post.published_at = _now()
    post.last_error = ""
    _note(post, "published", url)
    return True


def publishing_tick(session: Session) -> dict:
    """Run by the scheduler poller. Plans metadata for new videos, queues what the mode allows, then
    publishes what is due. With PUBLISHING_ENABLED=false the last step refuses and nothing is uploaded."""
    planned = queued = 0
    for brand in session.scalars(select(Brand)).all():
        jobs = session.scalars(
            select(ProductionJob).where(
                ProductionJob.brand_id == brand.id,
                ProductionJob.status == "succeeded",
                ProductionJob.video_id.is_not(None),
            )
        ).all()
        for job in jobs:
            video = session.get(Video, job.video_id)
            if video is None or video.status != "ready":
                continue
            has_post = session.scalar(select(PlatformPost.id).where(PlatformPost.video_id == video.id))
            if has_post is None:
                planned += len(plan_posts(session, video, brand))
        queued += queue_ready(session, brand)
    session.commit()
    result = publish_due(session)
    return {"planned": planned, "queued": queued, **result}
