from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.errors import AppError
from app.core.seed import seed
from app.models import AffiliateClick, Brand, ContentOpportunity, CycleRun, ResearchSource, Topic, Video
from app.services.business.cycle import mark_cycles_interrupted, run_brand_cycle
from app.services.business.records import save_product, save_program, save_sponsorship
from app.services.business.score import score_signals
from app.services.research.discover import run_discovery
from app.services.research.feeds import FetchResult, ParsedItem
from fastapi.testclient import TestClient
from sqlalchemy import func, select


def test_partial_score_does_not_rescale_a_missing_factor():
    scored = score_signals(
        {
            "evergreen": {"value": 80, "kind": "assumption", "note": "keyword"},
            "audience_demand": {"value": None, "kind": "unknown", "note": "no source"},
        },
        {"evergreen": Decimal("0.05"), "audience_demand": Decimal("0.20")},
    )
    assert scored["partial_score"] == Decimal("4.00")
    assert scored["coverage"] == Decimal("0.0500")
    assert scored["weight_total"] == Decimal("0.2500")
    assert scored["complete"] is False


def _item(external_id: str, title: str) -> ParsedItem:
    return ParsedItem(
        external_id=external_id,
        title=title,
        summary="A public feed lead.",
        url=f"https://example.com/{external_id}",
        published_at=datetime(2026, 11, 1, tzinfo=UTC),
        verified=True,
        check_label="200",
    )


def test_brand_cycle_scores_without_rendering_or_promoting(db, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    seed(db)
    brand = db.scalar(select(Brand).where(Brand.slug == "everyday-decisions"))
    save_program(
        db,
        brand,
        name="Sleep notes",
        provider="Example",
        locale="US",
        status="active",
        commission_type="percent",
        commission_rate="4.5",
        terms="Stored by the operator.",
        expires_on="",
    )
    seen: list[str] = []

    def fetch(source: ResearchSource):
        seen.append(source.url)
        if "mind_brain" not in source.url:
            return FetchResult(items=[], body_sha256="other")
        return FetchResult(
            items=[
                _item("sleep", "How sleep and habit shape a decision"),
                _item("clinic", "New Alzheimer treatment reported"),
            ],
            body_sha256="brain",
        )

    global_seen: list[str] = []

    def global_fetch(source: ResearchSource):
        global_seen.append(source.url)
        return FetchResult(items=[], body_sha256=source.url)

    run_discovery(db, fetch=global_fetch)
    assert global_seen
    assert all("mind_brain" not in url for url in global_seen)

    def sample(_query: str):
        return {"median_views": 900_000, "sample_size": 5, "note": "fixture"}

    run = run_brand_cycle(db, brand, fetch=fetch, youtube=sample)
    assert run.status == "succeeded"
    assert run.stats["videos_rendered"] == 0
    assert run.stats["uploads"] == 0
    assert run.stats["topics_promoted"] == 0
    assert run.stats["advertising_revenue"] is None
    assert seen == ["https://www.sciencedaily.com/rss/mind_brain.xml"]
    assert db.scalar(select(func.count()).select_from(Video)) == 0
    assert db.scalar(select(func.count()).select_from(Topic)) == 0
    rows = db.scalars(select(ContentOpportunity).order_by(ContentOpportunity.title)).all()
    assert len(rows) == 2
    held = next(row for row in rows if "Alzheimer" in row.title)
    useful = next(row for row in rows if "sleep" in row.title)
    assert held.status == "held"
    assert "medical" in held.hold_reason
    assert useful.status == "evaluated"
    assert useful.partial_score == Decimal("14.50")
    assert useful.coverage < useful.weight_total
    assert useful.production_status == "blocked"
    assert useful.youtube_sample["median_views"] == 900_000
    factors = {item["factor"]: item for item in useful.signals}
    assert factors["audience_demand"]["value"] is None
    assert factors["monetization_potential"]["value"] is None
    assert factors["competition_opportunity"]["value"] is None
    assert factors["affiliate_sponsorship"]["value"] is None
    assert "4.5" in factors["affiliate_sponsorship"]["note"]
    assert factors["evergreen"]["kind"] == "assumption"

    brand.status = "paused"
    called = {"n": 0}

    def refuse(_source):
        called["n"] += 1
        raise AssertionError("paused brand must not fetch")

    skipped = run_brand_cycle(db, brand, fetch=refuse)
    assert skipped.status == "skipped"
    assert called["n"] == 0


def test_rates_are_required_when_someone_marks_them_real(db):
    seed(db)
    brand = db.scalar(select(Brand).where(Brand.slug == "everyday-decisions"))
    with pytest.raises(AppError, match="commission rate"):
        save_program(
            db,
            brand,
            name="Books",
            provider="Example",
            locale="US",
            status="active",
            commission_type="percent",
            commission_rate="",
            terms="",
            expires_on="",
        )
    with pytest.raises(AppError, match="agreement"):
        save_sponsorship(db, brand, sponsor_name="Example Co", status="agreed", rate="", notes="")
    lead = save_sponsorship(db, brand, sponsor_name="Example Co", status="lead", rate="", notes="No reply.")
    assert lead.rate is None
    assert lead.status == "lead"


def test_business_page_and_click_do_not_invent_revenue(db):
    seed(db)
    brand = db.scalar(select(Brand).where(Brand.slug == "everyday-decisions"))
    program = save_program(
        db,
        brand,
        name="Notes",
        provider="Example",
        locale="US",
        status="active",
        commission_type="percent",
        commission_rate="4.5",
        terms="",
        expires_on="",
    )
    product = save_product(
        db,
        program,
        name="A notebook",
        category="books",
        product_url="https://example.com/notebook",
        affiliate_url="https://example.com/notebook?tag=stored",
    )
    running = CycleRun(brand_id=brand.id, status="running", stats={})
    db.add(running)
    db.commit()
    from app.main import create_app

    with TestClient(create_app()) as client:
        page = client.get("/business")
        assert page.status_code == 200
        assert "Everyday decisions" in page.text
        assert "Advertising revenue is unavailable" in page.text
        assert "does not render a video" in page.text
        brands = client.get("/api/business/brands").json()
        assert brands[0]["advertising_revenue"] is None
        assert brands[0]["connected_platforms"] == []
        redirect = client.get(f"/go/{product.id}", follow_redirects=False)
        assert redirect.status_code == 302
        assert redirect.headers["location"] == "https://example.com/notebook?tag=stored"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AffiliateClick)) == 1
    stored = db.get(CycleRun, running.id)
    assert stored.status == "interrupted"


def test_mark_cycles_interrupted_is_explicit(db):
    seed(db)
    brand = db.scalar(select(Brand).where(Brand.slug == "everyday-decisions"))
    row = CycleRun(brand_id=brand.id, status="running", stats={})
    db.add(row)
    db.flush()
    assert mark_cycles_interrupted(db) == 1
    assert row.status == "interrupted"
    assert "stopped" in row.error
