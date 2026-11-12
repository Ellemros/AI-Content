from datetime import date
from decimal import Decimal

import pytest
from app.config import get_settings
from app.core.errors import SpendCapExceeded
from app.core.time import utc_now
from app.models import APIUsage
from app.services.cost import assert_within_cap, month_spend


def _previous_month() -> str:
    today = date.today()
    if today.month == 1:
        return f"{today.year - 1}-12"
    return f"{today.year}-{today.month - 1:02d}"


def _row(month: str, cost: str, cached: bool = False) -> APIUsage:
    return APIUsage(
        provider="openai",
        operation="script",
        model="gpt-4o-mini",
        estimated_cost_usd=Decimal(cost),
        cached=cached,
        month_key=month,
        created_at=utc_now(),
    )


def test_cap_blocks_when_the_estimate_does_not_fit(db, monkeypatch):
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "10")
    get_settings.cache_clear()
    db.add(_row(date.today().strftime("%Y-%m"), "9.50"))
    db.flush()
    with pytest.raises(SpendCapExceeded) as caught:
        assert_within_cap(db, Decimal("0.60"))
    assert "9.50" in caught.value.message
    assert "10.00" in caught.value.message
    assert_within_cap(db, Decimal("0.40"))


def test_previous_month_and_cache_do_not_count(db, monkeypatch):
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "10")
    get_settings.cache_clear()
    db.add(_row(_previous_month(), "100"))
    db.add(_row(date.today().strftime("%Y-%m"), "100", cached=True))
    db.flush()
    assert month_spend(db) == Decimal("0")
    assert_within_cap(db, Decimal("1"))
