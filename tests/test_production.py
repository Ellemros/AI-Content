from decimal import Decimal
from pathlib import Path

import pytest
from app.core.errors import AppError
from app.core.seed import seed
from app.models import (
    Brand,
    ContentOpportunity,
    DiscoveredItem,
    ProductionJob,
    PublishingJob,
    ResearchSource,
    Video,
)
from app.services.probes import media_summary, run_ffmpeg
from app.services.production.budget import add_cost, assert_production_budget
from app.services.production.checks import assess_production, scene_diversity
from app.services.production.extract import extract_article
from app.services.production.illustrate import brand_palette, draw_scene
from app.services.production.pipeline import eligible_opportunities, mark_production_interrupted, produce_next
from app.services.production.scripting import narration_from_source
from app.services.production.stock import license_allowed, parse_commons
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ARTICLE = """
<html><body>
<nav>Home menu</nav>
<article>
<h1>Cold plates and battery cooling</h1>
<p>Cold plates pull heat away from the cells during a fast charge in the pack.</p>
<p>The pack then slows the current once the plates approach their temperature limit.</p>
<p>Engineers measure the plate temperature before they raise the charge rate again.</p>
<p>A failed sensor leaves the controller without a plate reading for that cycle.</p>
</article>
</body></html>
"""


def test_extract_keeps_article_sentences_and_drops_navigation():
    text = extract_article(ARTICLE)
    assert "Cold plates pull heat" in text
    assert "Home menu" not in text


def test_compiler_stays_on_the_source_and_rejects_a_generic_model():
    extracted = extract_article(ARTICLE)
    draft = narration_from_source(
        title="Cold plates and battery cooling",
        extracted=extracted,
        url="https://example.com/plates",
        language="en",
        complete=lambda **_kwargs: {
            "narration": "Patterns are not minds. Think carefully about thinking in general.",
            "beats": ["Patterns are not minds."],
            "claims": [],
        },
    )
    assert draft["provider"] == "dossier"
    assert "not grounded" in draft["rejected_model"]
    assert "plates" in draft["narration"].casefold()
    assert "cells" in draft["narration"].casefold()
    assert all(claim["text"] in extracted for claim in draft["claims"])


def test_license_filter_refuses_noncommercial_stills():
    assert license_allowed("CC BY-SA 4.0")
    assert license_allowed("Public domain")
    assert license_allowed("CC0") is True
    assert license_allowed("CC BY-NC 4.0") is False
    assert license_allowed("CC BY-ND 4.0") is False
    picked = parse_commons(
        {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:Blocked.jpg",
                        "imageinfo": [
                            {
                                "thumburl": "https://upload.wikimedia.org/blocked.jpg",
                                "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}},
                            }
                        ],
                    },
                    "2": {
                        "title": "File:Open.jpg",
                        "imageinfo": [
                            {
                                "thumburl": "https://upload.wikimedia.org/open.jpg",
                                "extmetadata": {
                                    "LicenseShortName": {"value": "CC BY 4.0"},
                                    "Artist": {"value": "Ada <b>Lovelace</b>"},
                                },
                            }
                        ],
                    },
                }
            }
        }
    )
    assert picked["url"].endswith("open.jpg")
    assert picked["license"] == "CC BY 4.0"
    assert "Ada" in picked["artist"]
    assert "<b>" not in picked["artist"]


def _brand(session, slug: str, name: str) -> Brand:
    brand = Brand(
        slug=slug,
        name=name,
        niche=name,
        audience="People who use the thing",
        language="en",
        identity={"terms": ["plate", "lathe"], "palette": [[16, 28, 42], [48, 86, 102], [190, 122, 60]]},
        status="experimental",
        publishing_mode="test",
    )
    session.add(brand)
    session.flush()
    return brand


def _opportunity(session, brand, *, title, url, score, status="evaluated", hold="", category="science"):
    source = ResearchSource(name=title, kind="rss", url=f"{url}/feed", category=category, enabled=False)
    session.add(source)
    session.flush()
    item = DiscoveredItem(
        source_id=source.id,
        external_id=url,
        title=title,
        summary="A feed lead.",
        url=url,
        category=category,
        status="scored",
        fingerprint=url,
    )
    session.add(item)
    session.flush()
    row = ContentOpportunity(
        brand_id=brand.id,
        discovered_item_id=item.id,
        title=title,
        partial_score=Decimal(str(score)),
        coverage=Decimal("0.20"),
        weight_total=Decimal("1.00"),
        signals=[],
        status=status,
        hold_reason=hold,
        production_status="blocked",
        production_block_reason="Waiting for production.",
    )
    session.add(row)
    session.flush()
    return row


def test_selection_skips_held_items_and_a_zero_cap_blocks_a_paid_estimate(db):
    brand = _brand(db, "workshop-notes", "Workshop notes")
    _opportunity(
        db,
        brand,
        title="New treatment for a racing heart",
        url="https://example.com/heart",
        score=90,
        status="held",
        hold="Held. The title looks like medical advice, which this brand does not automate.",
    )
    chosen = _opportunity(
        db,
        brand,
        title="Cold plates and battery cooling",
        url="https://example.com/plates",
        score=12,
    )
    other = _brand(db, "bench-notes", "Bench notes")
    _opportunity(
        db,
        other,
        title="How a lathe keeps an edge aligned",
        url="https://example.com/lathe",
        score=40,
    )

    def fetch(url: str):
        if url.endswith("/plates"):
            return 200, ARTICLE
        return 200, "<html><body><p>Too short.</p></body></html>"

    assert [row.id for row in eligible_opportunities(db, brand)] == [chosen.id]
    failed = produce_next(db, other, fetch=fetch)
    assert failed is not None
    assert failed.status == "failed"
    assert "enough sentences" in failed.error
    assert db.scalar(select(func.count()).select_from(Video)) == 0

    job = ProductionJob(brand_id=brand.id, opportunity_id=chosen.id, status="running")
    db.add(job)
    db.flush()
    from app.config import get_settings

    settings = get_settings()
    original_cap = settings.production_video_cap_usd
    settings.production_video_cap_usd = 0
    try:
        with pytest.raises(AppError, match="Production spend cap"):
            assert_production_budget(db, job, Decimal("0.10"))
        add_cost(db, job, provider="pillow", operation="illustrate", cost=0)
    finally:
        settings.production_video_cap_usd = original_cap
    assert job.cost_usd == 0
    assert mark_production_interrupted(db) >= 1
    assert job.status == "interrupted"
    assert "stopped" in job.error


def test_scene_drawings_change_with_the_brand_and_the_layout(db, tmp_path):
    left = _brand(db, "north-shop", "North shop")
    right = Brand(
        slug="south-shop",
        name="South shop",
        niche="Gardens",
        language="en",
        identity={"palette": [[40, 20, 20], [120, 50, 40], [220, 180, 80]]},
        status="experimental",
    )
    db.add(right)
    db.flush()
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    draw_scene(one, kind="orbit", motifs=["plate", "heat"], palette=brand_palette(left))
    draw_scene(two, kind="flow", motifs=["lathe", "edge"], palette=brand_palette(right))
    assert scene_diversity([one, two, one, two])[0] is False
    third = tmp_path / "three.png"
    fourth = tmp_path / "four.png"
    draw_scene(third, kind="split", motifs=["sensor", "cycle"], palette=brand_palette(left))
    draw_scene(fourth, kind="band", motifs=["current", "limit"], palette=brand_palette(left))
    assert scene_diversity([one, two, third, fourth])[0] is True


def test_gate_rejects_a_landscape_clip(tmp_path):
    video = tmp_path / "wide.mp4"
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nShort line\n", encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ]
    )
    scene = tmp_path / "scene.png"
    draw_scene(scene, kind="marker", motifs=["plate"], palette=[(10, 20, 30), (40, 50, 60), (70, 80, 90)])
    report = assess_production(
        video_path=video,
        subtitle_path=srt,
        scene_paths=[scene, scene, scene, scene],
        narration="Cold plates pull heat away from the cells during a fast charge.",
        extracted=extract_article(ARTICLE),
        title="Cold plates and battery cooling",
        claims=[
            {
                "text": "Cold plates pull heat away from the cells during a fast charge in the pack.",
                "sources": ["https://example.com"],
                "verified": True,
                "unverified": False,
            }
        ],
        cost=Decimal("0"),
        video_cap=Decimal("1"),
    )
    assert report["passed"] is False
    names = {item["name"]: item["passed"] for item in report["checks"]}
    assert names["resolution"] is False
    assert names["scene_diversity"] is False


@pytest.mark.integration
def test_produce_next_writes_a_vertical_mp4(db):
    seed(db)
    brand = _brand(db, "workshop-notes", "Workshop notes")
    _opportunity(
        db,
        brand,
        title="Cold plates and battery cooling",
        url="https://example.com/plates",
        score=20,
    )

    def fetch(url: str):
        assert url == "https://example.com/plates"
        return 200, ARTICLE

    job = produce_next(db, brand, fetch=fetch)
    assert job is not None
    assert job.status == "succeeded", job.error
    assert job.report["published"] is False
    assert job.report["ready_for_publishing"] is False
    assert job.report["script_mode"] == "fallback"
    assert job.report["quality_status"] == "needs_review"
    assert "script" in job.report["fallbacks"]
    assert "voice" not in job.report["fallbacks"]
    assert job.report["visual_provider"] == "stickman"
    assert "footage" not in job.report["fallbacks"]
    assert all(scene["footage"]["status"] == "animated" for scene in job.report["scenes"])
    assert "openai" not in job.report["providers"]
    assert "dossier" in job.report["providers"]
    assert "kokoro" in job.report["providers"]
    video = db.get(Video, job.video_id)
    assert video is not None
    assert video.approved is False
    assert video.status == "needs_review"
    path = Path(video.path)
    assert path.is_file() and path.stat().st_size > 0
    summary = media_summary(path)
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
    assert (summary["width"], summary["height"]) == (1080, 1920)
    assert summary["duration"] >= 4
    assert Path(video.subtitle_path).is_file()
    assert db.scalar(select(func.count()).select_from(PublishingJob)) == 0
    assert produce_next(db, brand, fetch=fetch) is None
    again = produce_next(db, brand, opportunity_id=job.opportunity_id, fetch=fetch)
    assert again.id == job.id
    db.commit()
    from app.main import create_app

    with TestClient(create_app()) as client:
        page = client.get("/production")
        assert page.status_code == 200
        assert "Cold plates and battery cooling" in page.text
        assert "does not publish" in page.text
        assert "Published: no." in page.text
        payload = client.get("/api/production/jobs").json()
        assert payload["published"] is False
        assert payload["jobs"][0]["status"] == "succeeded"
        media = client.get(f"/production/videos/{video.id}")
        assert media.status_code == 200
        assert media.headers["content-type"].startswith("video/mp4")
        assert media.content[4:8] == b"ftyp"
