from pathlib import Path

import pytest
from app.cli import main
from app.core.errors import AppError
from app.models import (
    BrandSource,
    ContentOpportunity,
    DiscoveredItem,
    ProductionJob,
    PublishedPost,
    PublishingJob,
    ResearchSource,
    Video,
)
from app.services.automation import create_brand, videos_today
from app.services.business.signals import hold_reason
from app.services.probes import media_summary
from app.services.production.pipeline import eligible_opportunities
from app.services.production.test_run import TEST_TITLE, run_test_production, topic_hold
from sqlalchemy import func, select

HELD = "Held. The title looks like medical advice, which this brand does not automate."


def _brand(session):
    return create_brand(
        session,
        name="Everyday decisions",
        slug="everyday-decisions",
        niche="How people remember, choose, and build ordinary habits.",
        audience="Curious adults",
        language="en",
        block_medical=True,
        block_financial=True,
    )


def _held_opportunity(session, brand) -> ContentOpportunity:
    source = ResearchSource(
        name="Mind feed",
        kind="rss",
        url="https://example.com/mind.xml",
        category="science",
        enabled=True,
    )
    session.add(source)
    session.flush()
    session.add(BrandSource(brand_id=brand.id, source_id=source.id))
    item = DiscoveredItem(
        source_id=source.id,
        external_id="medical-item",
        title="A new diagnosis and therapy",
        summary="The item discusses a symptom and a disease.",
        url="https://example.com/medical",
        category="science",
        fingerprint="medical-item",
        status="held",
    )
    session.add(item)
    session.flush()
    row = ContentOpportunity(
        brand_id=brand.id,
        discovered_item_id=item.id,
        title=item.title,
        status="held",
        hold_reason=HELD,
        production_status="blocked",
        signals=[],
    )
    session.add(row)
    session.flush()
    return row


def test_health_hold_still_blocks_medical_text_and_stops_the_test(db, monkeypatch):
    brand = _brand(db)
    held = _held_opportunity(db, brand)
    assert "medical_advice" in (brand.identity or {}).get("blocked_areas", [])
    assert hold_reason("A new cancer therapy", "The diagnosis lists a symptom.", brand) == HELD
    assert topic_hold(brand) == ""
    before = (held.status, held.hold_reason)
    monkeypatch.setattr(
        "app.services.production.test_run.hold_reason",
        lambda *_args, **_kwargs: HELD,
    )
    with pytest.raises(AppError, match="medical advice"):
        run_test_production(db, brand, require_live_providers=False)
    assert (held.status, held.hold_reason) == before
    assert db.scalar(select(func.count()).select_from(ProductionJob)) == 0
    assert db.scalar(select(func.count()).select_from(PublishingJob)) == 0


def test_test_run_does_not_publish_or_change_existing_opportunities(db):
    brand = _brand(db)
    held = _held_opportunity(db, brand)
    before = (held.id, held.status, held.hold_reason)
    blocked = list((brand.identity or {}).get("blocked_areas") or [])

    job = run_test_production(db, brand, require_live_providers=False)

    assert job is not None
    assert job.status == "succeeded", job.error
    report = job.report
    assert report["published"] is False
    assert report["ready_for_publishing"] is False
    assert report["quality_status"] == "needs_review"
    assert report["test_run"] is True
    assert report["visual_provider"] == "stickman"
    assert job.video_id is not None
    db.refresh(held)
    assert (held.id, held.status, held.hold_reason) == before
    test_row = db.get(ContentOpportunity, job.opportunity_id)
    assert test_row.status == "test"
    assert test_row.hold_reason == ""
    assert test_row.id not in {row.id for row in eligible_opportunities(db, brand)}
    assert held not in eligible_opportunities(db, brand)
    assert videos_today(db, brand.id) == 0
    assert db.scalar(select(func.count()).select_from(PublishingJob)) == 0
    assert db.scalar(select(func.count()).select_from(PublishedPost)) == 0
    assert (brand.identity or {}).get("blocked_areas") == blocked
    video = db.get(Video, job.video_id)
    path = Path(video.path)
    assert path.is_file() and path.stat().st_size > 0
    summary = media_summary(path)
    assert (summary["width"], summary["height"]) == (1080, 1920)
    source = db.scalar(select(ResearchSource).where(ResearchSource.url.contains("production-test.invalid")))
    assert source is not None and source.enabled is False
    linked = db.scalar(select(BrandSource).where(BrandSource.source_id == source.id))
    assert linked is None


def test_command_refuses_without_providers_and_prints_no_upload(db, capsys):
    brand = _brand(db)
    held = _held_opportunity(db, brand)
    before = (held.status, held.hold_reason)
    db.commit()
    code = main(["test-production", "--slug", brand.slug])
    output = capsys.readouterr().out
    assert code == 1
    assert "was not started" in output
    assert "Published: no." in output
    assert "Uploaded: no." in output
    db.expire_all()
    row = db.get(ContentOpportunity, held.id)
    assert (row.status, row.hold_reason) == before
    assert db.scalar(select(func.count()).select_from(ProductionJob)) == 0
    assert db.scalar(select(func.count()).select_from(PublishingJob)) == 0
    assert TEST_TITLE not in output
