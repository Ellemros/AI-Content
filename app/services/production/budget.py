"""Per-video and daily production limits. The monthly cap still applies to paid calls."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models import Brand, ProductionCost, ProductionJob


def _day_start(moment: datetime | None = None) -> datetime:
    current = moment or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _spend(
    session: Session,
    *,
    since: datetime,
    brand_id: str | None = None,
) -> Decimal:
    query = select(func.coalesce(func.sum(ProductionCost.estimated_cost_usd), 0)).where(
        ProductionCost.cached.is_(False),
        ProductionCost.created_at >= since,
    )
    if brand_id:
        query = query.where(ProductionCost.brand_id == brand_id)
    total = session.scalar(query)
    return Decimal(str(total or 0))


def daily_spend(session: Session, moment: datetime | None = None, *, brand_id: str | None = None) -> Decimal:
    return _spend(session, since=_day_start(moment), brand_id=brand_id)


def monthly_spend(
    session: Session,
    moment: datetime | None = None,
    *,
    brand_id: str | None = None,
) -> Decimal:
    current = moment or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _spend(session, since=start, brand_id=brand_id)


def assert_production_budget(session: Session, job: ProductionJob, estimate: Decimal) -> None:
    if estimate <= 0:
        return
    settings = get_settings()
    video_cap = Decimal(str(settings.production_video_cap_usd))
    daily_cap = Decimal(str(settings.production_daily_cap_usd))
    spent_video = Decimal(str(job.cost_usd or 0))
    spent_day = daily_spend(session)
    brand = session.get(Brand, job.brand_id) if job.brand_id else None
    brand_video = None
    if brand is not None and brand.video_spend_cap_usd is not None:
        brand_video = Decimal(str(brand.video_spend_cap_usd))
    brand_day = None if brand is None else Decimal(str(brand.max_spend_usd_per_day or 0))
    spent_brand = daily_spend(session, brand_id=job.brand_id) if brand is not None else Decimal("0")
    video_limit = video_cap if brand_video is None else min(video_cap, brand_video)
    over_video = spent_video + estimate > video_limit
    over_day = spent_day + estimate > daily_cap
    over_brand = brand_day is not None and spent_brand + estimate > brand_day
    if over_video or over_day or over_brand:
        brand_text = ""
        if brand_day is not None:
            brand_text = f" This brand is at ${float(spent_brand):.4f} of ${float(brand_day):.2f} today."
        raise AppError(
            "Production spend cap reached. "
            f"This video is at ${float(spent_video):.4f} of ${float(video_limit):.2f}. "
            f"Today is at ${float(spent_day):.4f} of ${float(daily_cap):.2f}. "
            f"The next call is estimated at ${float(estimate):.4f}.{brand_text}"
        )


def add_cost(
    session: Session,
    job: ProductionJob,
    *,
    provider: str,
    operation: str,
    cost: Decimal | float,
    cached: bool = False,
) -> ProductionCost:
    amount = Decimal(str(cost))
    if not cached:
        assert_production_budget(session, job, amount)
    row = ProductionCost(
        job_id=job.id,
        brand_id=job.brand_id,
        opportunity_id=job.opportunity_id,
        video_id=job.video_id,
        provider=provider,
        operation=operation,
        estimated_cost_usd=amount,
        cached=cached,
        created_at=utc_now(),
    )
    session.add(row)
    if not cached:
        job.cost_usd = Decimal(str(job.cost_usd or 0)) + amount
    session.flush()
    return row
