"""Per-video and daily production limits. The monthly cap still applies to paid calls."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models import ProductionCost, ProductionJob


def _day_start(moment: datetime | None = None) -> datetime:
    current = moment or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_spend(session: Session, moment: datetime | None = None) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(ProductionCost.estimated_cost_usd), 0)).where(
            ProductionCost.cached.is_(False),
            ProductionCost.created_at >= _day_start(moment),
        )
    )
    return Decimal(str(total or 0))


def assert_production_budget(session: Session, job: ProductionJob, estimate: Decimal) -> None:
    if estimate <= 0:
        return
    settings = get_settings()
    video_cap = Decimal(str(settings.production_video_cap_usd))
    daily_cap = Decimal(str(settings.production_daily_cap_usd))
    spent_video = Decimal(str(job.cost_usd or 0))
    spent_day = daily_spend(session)
    if spent_video + estimate > video_cap or spent_day + estimate > daily_cap:
        raise AppError(
            "Production spend cap reached. "
            f"This video is at ${float(spent_video):.4f} of ${float(video_cap):.2f}. "
            f"Today is at ${float(spent_day):.4f} of ${float(daily_cap):.2f}. "
            f"The next call is estimated at ${float(estimate):.4f}."
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
