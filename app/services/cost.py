"""Monthly spend cap for paid provider calls."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import SpendCapExceeded
from app.core.time import month_key, utc_now
from app.models import APIUsage


def month_spend(session: Session, key: str | None = None) -> Decimal:
    current = key or month_key()
    total = session.scalar(
        select(func.coalesce(func.sum(APIUsage.estimated_cost_usd), 0)).where(
            APIUsage.month_key == current,
            APIUsage.cached.is_(False),
        )
    )
    return Decimal(str(total or 0))


def assert_within_cap(session: Session, estimate: Decimal) -> None:
    if estimate <= 0:
        return
    spent = month_spend(session)
    cap = Decimal(str(get_settings().monthly_spend_cap_usd))
    if spent + estimate > cap:
        raise SpendCapExceeded(spent, cap, estimate)


def record_usage(
    session: Session,
    *,
    provider: str,
    operation: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    characters: int = 0,
    cost: Decimal | float = 0,
    cached: bool = False,
) -> APIUsage:
    row = APIUsage(
        provider=provider,
        operation=operation,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        characters=characters,
        estimated_cost_usd=Decimal(str(cost)),
        cached=cached,
        month_key=month_key(),
        created_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row
