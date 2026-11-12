"""Monthly spend cap for paid provider calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import SpendCapExceeded
from app.core.time import month_key, utc_now
from app.models import APIUsage, Video

_scope: ContextVar[dict[str, str | None] | None] = ContextVar("usage_scope", default=None)


def _current_scope() -> dict[str, str | None]:
    return dict(_scope.get() or {})


@contextmanager
def usage_scope(*, script_id: str | None = None, video_id: str | None = None):
    current = _current_scope()
    if script_id is not None:
        current["script_id"] = script_id
    if video_id is not None:
        current["video_id"] = video_id
    token = _scope.set(current)
    try:
        yield
    finally:
        _scope.reset(token)


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
    script_id: str | None = None,
    video_id: str | None = None,
) -> APIUsage:
    scope = _current_scope()
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
        script_id=script_id or scope.get("script_id"),
        video_id=video_id or scope.get("video_id"),
        created_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def video_cost_rows(session: Session, key: str | None = None) -> tuple[list[dict], Decimal]:
    """Month spend for each video. Script-level calls attach to the earliest video of that script.

    The month total used by the cap is `month_spend`, which counts each API row once.
    """
    current = key or month_key()
    videos = list(session.scalars(select(Video).order_by(Video.created_at, Video.id)))
    first_for_script: dict[str, str] = {}
    for video in videos:
        first_for_script.setdefault(video.script_id, video.id)
    totals = {video.id: Decimal("0") for video in videos}
    unassigned = Decimal("0")
    rows = session.scalars(
        select(APIUsage).where(APIUsage.month_key == current, APIUsage.cached.is_(False))
    )
    for row in rows:
        amount = Decimal(str(row.estimated_cost_usd or 0))
        if row.video_id and row.video_id in totals:
            totals[row.video_id] += amount
        elif row.script_id and row.script_id in first_for_script:
            totals[first_for_script[row.script_id]] += amount
        else:
            unassigned += amount
    listed = [
        {"video": video, "cost": totals[video.id]}
        for video in videos
        if totals[video.id] > 0 or (video.created_at and month_key(video.created_at) == current)
    ]
    return listed, unassigned
