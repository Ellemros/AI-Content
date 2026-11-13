from datetime import date
from decimal import Decimal

import pytest
from app.config import get_settings
from app.core.errors import SpendCapExceeded
from app.core.time import utc_now
from app.models import APIUsage, Video
from app.schemas.content import TopicCreate
from app.services.cost import assert_within_cap, month_spend, record_usage, video_cost_rows
from app.services.scripts import current_version, generate_script
from app.services.topics import create_topic


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


def test_cost_is_attributed_to_the_video(db):
    topic = create_topic(db, TopicCreate(title="Tools change the work"))
    script = generate_script(db, topic, language="en", video_format="short")
    version = current_version(db, script)
    video = Video(
        script_id=script.id,
        script_version_id=version.id,
        language="en",
        format="short",
        profile="short_9_16",
        status="ready",
    )
    db.add(video)
    db.flush()
    record_usage(
        db,
        provider="openai",
        operation="script",
        model="gpt-4o-mini",
        cost="1.25",
        script_id=script.id,
    )
    record_usage(
        db,
        provider="elevenlabs",
        operation="voice",
        model="eleven_multilingual_v2",
        cost="0.50",
        video_id=video.id,
    )
    record_usage(db, provider="openai", operation="other", model="gpt-4o-mini", cost="0.10")
    rows, unassigned = video_cost_rows(db)
    assert rows[0]["video"].id == video.id
    assert rows[0]["cost"] == Decimal("1.75")
    assert unassigned == Decimal("0.10")
    assert month_spend(db) == Decimal("1.85")
