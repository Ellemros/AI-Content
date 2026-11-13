"""One brand pass: score opportunities, then render while the brand's limits allow.

The existing scheduler calls this. It does not start another poller and it does not publish.
"""

import logging
import re
import threading
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.domain import LANGUAGES
from app.models import Brand, BrandSource, ContentOpportunity, ProductionJob, ResearchSource, RuntimeFlag
from app.services.business.cycle import run_brand_cycle
from app.services.production.budget import _day_start, daily_spend
from app.services.production.pipeline import eligible_opportunities, produce_next
from app.services.textutil import fold

logger = logging.getLogger("app.production")
_FLAG = "automation_enabled"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(brand_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(brand_id)
        if lock is None:
            lock = threading.Lock()
            _locks[brand_id] = lock
        return lock


def automation_enabled(session: Session) -> bool:
    row = session.get(RuntimeFlag, _FLAG)
    if row is None:
        return get_settings().automation_enabled
    return row.value == "true"


def set_automation_enabled(session: Session, enabled: bool) -> None:
    row = session.get(RuntimeFlag, _FLAG)
    value = "true" if enabled else "false"
    if row is None:
        session.add(RuntimeFlag(key=_FLAG, value=value))
    else:
        row.value = value
    session.flush()


def seed_automation_flag(session: Session) -> None:
    if session.get(RuntimeFlag, _FLAG) is None:
        set_automation_enabled(session, get_settings().automation_enabled)


def mark_automation_interrupted(session: Session) -> int:
    rows = session.scalars(select(Brand).where(Brand.last_automation_status == "running")).all()
    for row in rows:
        row.last_automation_status = "interrupted"
        row.last_automation_error = "The process stopped while this brand was producing."
    return len(rows)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", fold(value)).strip("-")
    return cleaned[:80]


def _money(value: str | Decimal | float | None, fallback: str) -> Decimal:
    text = fallback if value is None or str(value).strip() == "" else str(value).strip()
    try:
        amount = Decimal(text)
    except Exception as exc:
        raise AppError("Spend limits must be numbers.") from exc
    if amount < 0:
        raise AppError("Spend limits cannot be negative.")
    return amount


def create_brand(
    session: Session,
    *,
    name: str,
    slug: str = "",
    niche: str = "",
    audience: str = "",
    language: str = "en",
    formats: list[str] | None = None,
    interval_minutes: int = 1440,
    max_videos_per_day: int = 1,
    max_spend_usd_per_day: str = "5",
    video_spend_cap_usd: str = "",
    max_attempts: int = 2,
    terms: str = "",
    block_medical: bool = False,
    block_financial: bool = False,
    editorial_gate: bool = False,
    editorial_minimum: str = "",
    feed_url: str = "",
    feed_name: str = "",
    activate: bool = False,
) -> Brand:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise AppError("The brand needs a name.")
    slug_value = _slug(slug or cleaned)
    if not slug_value:
        raise AppError("The brand needs a slug.")
    if session.scalar(select(Brand).where(Brand.slug == slug_value)):
        raise AppError("A brand with that slug already exists.")
    if language not in LANGUAGES:
        raise AppError("Language must be en or cs.")
    chosen = [item for item in (formats or ["short"]) if item in {"short", "long"}]
    if not chosen:
        raise AppError("Choose at least one content format. Autonomous production renders a vertical short.")
    _check_limits(interval_minutes, max_videos_per_day, max_attempts)
    editorial = _editorial(editorial_gate, editorial_minimum)
    identity = _identity(terms, block_medical, block_financial)
    brand = Brand(
        slug=slug_value,
        name=cleaned[:160],
        niche=niche.strip(),
        audience=audience.strip(),
        language=language,
        identity=identity,
        status="active" if activate else "experimental",
        publishing_mode="test",
        daily_publish_limit=0,
        automation_enabled=activate,
        schedule_interval_minutes=interval_minutes,
        next_production_at=utc_now() if activate else None,
        max_videos_per_day=max_videos_per_day,
        max_spend_usd_per_day=_money(max_spend_usd_per_day, "5"),
        video_spend_cap_usd=None if not video_spend_cap_usd.strip() else _money(video_spend_cap_usd, "0"),
        max_attempts=max_attempts,
        content_formats=chosen,
        editorial=editorial,
        last_automation_status="idle",
    )
    session.add(brand)
    session.flush()
    if feed_url.strip():
        attach_feed(session, brand, url=feed_url, name=feed_name or cleaned)
    return brand


def update_brand_limits(
    session: Session,
    brand: Brand,
    *,
    interval_minutes: int,
    max_videos_per_day: int,
    max_spend_usd_per_day: str,
    video_spend_cap_usd: str,
    max_attempts: int,
    formats: list[str],
    editorial_gate: bool,
    editorial_minimum: str,
    niche: str,
    audience: str,
    language: str,
    voice_id: str = "",
) -> Brand:
    if language not in LANGUAGES:
        raise AppError("Language must be en or cs.")
    chosen = [item for item in formats if item in {"short", "long"}]
    if not chosen:
        raise AppError("Choose at least one content format.")
    _check_limits(interval_minutes, max_videos_per_day, max_attempts)
    brand.schedule_interval_minutes = interval_minutes
    brand.max_videos_per_day = max_videos_per_day
    brand.max_spend_usd_per_day = _money(max_spend_usd_per_day, "5")
    brand.video_spend_cap_usd = None if not video_spend_cap_usd.strip() else _money(video_spend_cap_usd, "0")
    brand.max_attempts = max_attempts
    brand.content_formats = chosen
    brand.editorial = _editorial(editorial_gate, editorial_minimum)
    brand.niche = niche.strip()
    brand.audience = audience.strip()
    brand.language = language
    identity = dict(brand.identity or {})
    identity["voice_id"] = voice_id.strip()
    brand.identity = identity
    session.flush()
    return brand


def attach_feed(session: Session, brand: Brand, *, url: str, name: str = "") -> ResearchSource:
    cleaned = url.strip()
    if not cleaned.startswith("https://"):
        raise AppError("Feed URLs must start with https://.")
    source = session.scalar(select(ResearchSource).where(ResearchSource.url == cleaned))
    if source is None:
        source = ResearchSource(
            name=(name.strip() or brand.name)[:160],
            kind="rss",
            url=cleaned,
            category="science",
            enabled=False,
        )
        session.add(source)
        session.flush()
    link = session.scalar(
        select(BrandSource).where(BrandSource.brand_id == brand.id, BrandSource.source_id == source.id)
    )
    if link is None:
        session.add(BrandSource(brand_id=brand.id, source_id=source.id))
        session.flush()
    return source


def activate_brand(session: Session, brand: Brand) -> None:
    brand.status = "active"
    brand.automation_enabled = True
    if not brand.content_formats:
        brand.content_formats = ["short"]
    brand.next_production_at = utc_now()
    brand.last_automation_status = "idle"
    brand.last_automation_error = ""
    session.flush()


def pause_brand(session: Session, brand: Brand) -> None:
    brand.status = "paused"
    brand.automation_enabled = False
    brand.last_automation_status = "paused"
    brand.last_automation_error = "Paused. No new video will be started."
    session.flush()


def videos_today(session: Session, brand_id: str, moment: datetime | None = None) -> int:
    start = _day_start(moment)
    total = session.scalar(
        select(func.count())
        .select_from(ProductionJob)
        .where(
            ProductionJob.brand_id == brand_id,
            ProductionJob.status == "succeeded",
            ProductionJob.finished_at.is_not(None),
            ProductionJob.finished_at >= start,
        )
    )
    return int(total or 0)


def run_due_brands(session: Session, now: datetime | None = None) -> list[str]:
    if not automation_enabled(session):
        return []
    moment = now or utc_now()
    due = list(
        session.scalars(
            select(Brand).where(
                Brand.status == "active",
                Brand.automation_enabled.is_(True),
                Brand.next_production_at.is_not(None),
                Brand.next_production_at <= moment,
            )
        )
    )
    finished: list[str] = []
    for brand in due:
        run_brand_production(session, brand, now=moment)
        finished.append(brand.id)
    return finished


def launch_brand(brand_id: str) -> None:
    """Run one brand off the request thread. Publishing is not called."""

    def _run() -> None:
        from app.core.database import session_scope

        try:
            with session_scope() as session:
                brand = session.get(Brand, brand_id)
                if brand is None:
                    return
                run_brand_production(session, brand)
        except Exception:
            logger.exception(
                "Background brand production failed.",
                extra={"stage": "automation", "brand_id": brand_id, "attempt": 1},
            )

    threading.Thread(target=_run, name=f"brand-{brand_id[:8]}", daemon=True).start()


def run_brand_production(
    session: Session,
    brand: Brand,
    *,
    discover_fetch=None,
    page_fetch=None,
    synthesizer=None,
    stock_client=None,
    now: datetime | None = None,
) -> dict:
    """Discover, select, and render until this brand's daily limits stop it."""
    moment = now or utc_now()
    brand_id = brand.id
    if not automation_enabled(session):
        brand.last_automation_status = "stopped"
        brand.last_automation_error = "Global automation stop is on. No new video was started."
        session.commit()
        return {"status": "stopped", "videos": 0, "jobs": [], "reason": brand.last_automation_error}
    if brand.status != "active" or not brand.automation_enabled:
        if brand.status != "active":
            reason = f"Brand status is {brand.status}. Activate it before production runs."
        else:
            reason = "Brand automation is paused."
        brand.last_automation_status = "skipped"
        brand.last_automation_error = reason
        session.commit()
        return {"status": "skipped", "videos": 0, "jobs": [], "reason": reason}
    lock = _lock(brand_id)
    if not lock.acquire(blocking=False):
        logger.info(
            "Brand production is already running.",
            extra={"stage": "automation", "brand_id": brand_id, "attempt": 1},
        )
        return {"status": "running", "videos": 0, "jobs": [], "reason": "This brand is already producing."}
    try:
        brand.last_automation_status = "running"
        brand.last_automation_error = ""
        session.commit()
        brand = session.get(Brand, brand_id)
        if brand is None:
            return {"status": "failed", "videos": 0, "jobs": [], "reason": "Brand was not found."}
        try:
            cycle = run_brand_cycle(session, brand, fetch=discover_fetch)
            session.commit()
            formats = list(brand.content_formats or []) or ["short"]
            if "short" not in formats:
                reason = "This brand has no short format. Autonomous production renders a vertical short."
                _finish(brand, moment, status="skipped", reason=reason)
                session.commit()
                return {"status": "skipped", "videos": 0, "jobs": [], "reason": reason}
            made: list[str] = []
            tried: set[str] = set()
            reason = ""
            succeeded = 0
            failed = 0
            status = "idle"
            for _ in range(25):
                if not automation_enabled(session):
                    reason = "Global automation stop is on. No further video was started."
                    status = "stopped"
                    break
                cap_videos = 1 if brand.max_videos_per_day is None else int(brand.max_videos_per_day)
                raw_spend = 5 if brand.max_spend_usd_per_day is None else brand.max_spend_usd_per_day
                spend_cap = Decimal(str(raw_spend))
                if videos_today(session, brand.id, moment) >= cap_videos:
                    reason = "Daily video limit reached. No further video was started."
                    break
                spent = daily_spend(session, moment, brand_id=brand.id)
                if spent >= spend_cap:
                    reason = (
                        f"Daily spend limit reached (${float(spent):.4f} of ${float(spend_cap):.2f}). "
                        "No further video was started."
                    )
                    break
                pending = [row for row in eligible_opportunities(session, brand) if row.id not in tried]
                if not pending:
                    if succeeded == 0 and failed == 0:
                        reason = reason or "No eligible opportunity."
                    break
                opportunity = pending[0]
                tried.add(opportunity.id)
                job = produce_next(
                    session,
                    brand,
                    opportunity_id=opportunity.id,
                    fetch=page_fetch,
                    synthesizer=synthesizer,
                    stock_client=stock_client,
                )
                if job is None:
                    break
                made.append(job.id)
                if job.status == "succeeded":
                    succeeded += 1
                    continue
                if job.status == "running":
                    reason = "Another worker is producing this opportunity."
                    break
                failed += 1
                if "spend cap" in (job.error or "").lower():
                    reason = job.error
                    break
                if not reason:
                    reason = job.error or "Production failed."
            else:
                reason = reason or "Stopped after the safety limit for one pass."
            if succeeded:
                status = "stopped" if status == "stopped" else "succeeded"
            elif status == "stopped":
                status = "stopped"
            elif failed:
                status = "failed"
            elif reason.startswith("Daily"):
                status = "skipped"
            else:
                status = "idle"
            if cycle.status == "failed" and succeeded == 0:
                status = "failed"
                reason = cycle.error or reason
            elif cycle.error and not reason:
                reason = cycle.error
            _finish(brand, moment, status=status, reason=reason)
            session.commit()
            logger.info(
                "Brand production pass finished.",
                extra={
                    "stage": "automation",
                    "brand_id": brand_id,
                    "status": status,
                    "videos": succeeded,
                    "attempt": 1,
                    "published": False,
                },
            )
            return {"status": status, "videos": succeeded, "jobs": made, "reason": reason}
        except Exception as exc:
            logger.exception(
                "Brand production failed.",
                extra={"stage": "automation", "brand_id": brand_id, "error": str(exc), "attempt": 1},
            )
            session.rollback()
            brand = session.get(Brand, brand_id)
            if brand is None:
                raise
            _finish(brand, moment, status="failed", reason=str(exc)[:500])
            session.commit()
            return {"status": "failed", "videos": 0, "jobs": [], "reason": str(exc)[:500]}
    finally:
        lock.release()


def queue_length(session: Session, brand: Brand) -> int:
    waiting = len(eligible_opportunities(session, brand))
    running = session.scalar(
        select(func.count())
        .select_from(ProductionJob)
        .where(ProductionJob.brand_id == brand.id, ProductionJob.status == "running")
    )
    return waiting + int(running or 0)


def _finish(brand: Brand, moment: datetime, *, status: str, reason: str) -> None:
    brand.last_production_at = moment
    brand.last_automation_status = status
    brand.last_automation_error = (reason or "")[:2000]
    interval = int(brand.schedule_interval_minutes or 1440)
    brand.next_production_at = moment + timedelta(minutes=max(interval, 1))


def _check_limits(interval_minutes: int, max_videos_per_day: int, max_attempts: int) -> None:
    if not 1 <= int(interval_minutes) <= 7 * 24 * 60:
        raise AppError("Interval must be between 1 minute and 7 days.")
    if not 0 <= int(max_videos_per_day) <= 50:
        raise AppError("Maximum videos per day must be between 0 and 50.")
    if not 1 <= int(max_attempts) <= 5:
        raise AppError("Attempts must be between 1 and 5.")


def _editorial(gate: bool, minimum: str) -> dict:
    text = minimum.strip()
    if not text:
        return {"gate": bool(gate)}
    try:
        score = int(text)
    except ValueError as exc:
        raise AppError("Editorial minimum must be a whole number.") from exc
    if not 0 <= score <= 100:
        raise AppError("Editorial minimum must be between 0 and 100.")
    return {"gate": bool(gate), "minimum": score}


def _identity(terms: str, block_medical: bool, block_financial: bool) -> dict:
    words = [item.strip() for item in terms.split(",") if item.strip()]
    blocked = []
    if block_medical:
        blocked.append("medical_advice")
    if block_financial:
        blocked.append("financial_advice")
    return {"terms": words[:40], "blocked_areas": blocked}


def recent_jobs(session: Session, brand_id: str, status: str, limit: int = 8) -> list[ProductionJob]:
    return list(
        session.scalars(
            select(ProductionJob)
            .where(ProductionJob.brand_id == brand_id, ProductionJob.status == status)
            .order_by(ProductionJob.started_at.desc())
            .limit(limit)
        ).all()
    )


def opportunity_title(session: Session, opportunity_id: str | None) -> str:
    if not opportunity_id:
        return ""
    row = session.get(ContentOpportunity, opportunity_id)
    return "" if row is None else row.title
