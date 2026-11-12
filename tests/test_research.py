from datetime import UTC, datetime

import httpx
import pytest
from app.config import get_settings
from app.core.errors import QualityGateError
from app.models import DiscoveredItem, ResearchSource, Topic
from app.schemas.content import TopicCreate
from app.services.offline_scripts import build_offline_script
from app.services.research.discover import run_discovery
from app.services.research.editorial import review_content
from app.services.research.feeds import FetchResult, ParsedItem, check_url, parse_feed
from app.services.research.scoring import score_topic
from app.services.topics import create_topic

RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>How models learn</title>
      <link>https://arxiv.org/abs/1234.5678</link>
      <guid>urn:1</guid>
      <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
      <description>A &lt;b&gt;note&lt;/b&gt; about learning.</description>
    </item>
    <item>
      <title>No link here</title>
      <description>Skip me</description>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>A cited paper</title>
    <id>urn:atom:1</id>
    <updated>2026-11-01T12:00:00Z</updated>
    <summary>Public abstract.</summary>
    <link href="https://example.com/paper"/>
  </entry>
</feed>
"""


def _source(session, url="https://example.com/feed.xml"):
    source = ResearchSource(name="Fixture", kind="rss", url=url, category="ai", enabled=True)
    session.add(source)
    session.flush()
    return source


def _paper():
    return ParsedItem(
        external_id="paper-1",
        title="How retrieval models learn from cited papers",
        summary="A public note about research habits. No numbers.",
        url="https://arxiv.org/abs/0000.00000",
        published_at=datetime(2024, 5, 1, tzinfo=UTC),
        verified=True,
        check_label="200",
    )


def _chair():
    return ParsedItem(
        external_id="chair-1",
        title="Office chair catalog",
        summary="A list of chairs.",
        url="https://example.com/chairs",
        published_at=datetime(2026, 11, 1, tzinfo=UTC),
        verified=False,
        check_label="404",
    )


def test_parse_rss_and_atom_keep_the_original_url():
    rss_items = parse_feed(RSS, limit=8)
    assert len(rss_items) == 1
    assert rss_items[0].url == "https://arxiv.org/abs/1234.5678"
    assert rss_items[0].summary == "A note about learning."
    assert rss_items[0].published_at is not None
    atom_items = parse_feed(ATOM, limit=8)
    assert atom_items[0].external_id == "urn:atom:1"
    assert atom_items[0].url == "https://example.com/paper"
    with pytest.raises(ValueError):
        parse_feed("<html>nope</html>", limit=5)


def test_check_url_uses_the_status_and_does_not_invent_a_summary():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ok, label = check_url(client, "https://example.com/paper")
    assert ok is True
    assert label == "204"


def test_score_hides_search_volume_and_prefers_a_sourced_explainer():
    strong = score_topic(
        title="How retrieval models learn from cited papers",
        summary="A public note about research habits.",
        url="https://arxiv.org/abs/0000.00000",
        source_category="science",
        verified=True,
        duplicate=False,
    )
    weak = score_topic(
        title="Office chair catalog",
        summary="A list of chairs.",
        url="https://example.com/chairs",
        source_category="facts",
        verified=False,
        duplicate=False,
    )
    duplicate = score_topic(
        title="How retrieval models learn from cited papers",
        summary="A public note about research habits.",
        url="https://arxiv.org/abs/0000.00000",
        source_category="science",
        verified=True,
        duplicate=True,
    )
    assert 0 <= strong.score <= 100
    assert strong.score > weak.score
    assert strong.score >= 55
    assert weak.score < 55
    assert duplicate.score <= 20
    assert strong.breakdown["search_potential"]["score"] is None
    assert strong.breakdown["competition"]["score"] is None
    assert "search-volume" in strong.breakdown["search_potential"]["note"]
    assert "product" in strong.monetization_note


def test_discovery_promotes_only_the_strong_item_and_caches_the_feed(db):
    _source(db)

    def fetch(_source_row):
        return FetchResult(items=[_paper(), _chair()], body_sha256="feed-hash")

    first = run_discovery(db, fetch=fetch)
    assert first["created"] == 2
    assert first["promoted"] == 1
    promoted = db.query(DiscoveredItem).filter(DiscoveredItem.status == "promoted").one()
    topic = db.get(Topic, promoted.topic_id)
    assert topic is not None
    assert topic.status == "Researching"
    assert "https://arxiv.org/abs/0000.00000" in topic.description
    assert "not a verified fact" in topic.description
    assert topic.source_references[0]["url"] == "https://arxiv.org/abs/0000.00000"
    held = db.query(DiscoveredItem).filter(DiscoveredItem.title == "Office chair catalog").one()
    assert held.status == "scored"
    assert held.score < 55
    second = run_discovery(db, fetch=fetch)
    assert second["created"] == 0
    assert second["cached_sources"] == 1
    assert db.query(DiscoveredItem).count() == 2


def test_discovery_marks_near_duplicates_and_clickbait(db):
    create_topic(db, TopicCreate(title="How machines learn from examples", category="ai"))
    _source(db)

    def fetch(_source_row):
        return FetchResult(
            items=[
                ParsedItem(
                    external_id="near",
                    title="How machines learn from example",
                    summary="A feed lead.",
                    url="https://arxiv.org/abs/9999.1",
                    verified=True,
                    check_label="200",
                ),
                ParsedItem(
                    external_id="bait",
                    title="You won't believe this model",
                    summary="A launch note.",
                    url="https://openai.com/index/example",
                    verified=True,
                    check_label="200",
                ),
            ]
        )

    report = run_discovery(db, fetch=fetch)
    assert report["promoted"] == 0
    assert report["duplicates"] == 1
    rows = {row.title: row for row in db.query(DiscoveredItem).all()}
    assert rows["How machines learn from example"].status == "duplicate"
    assert "Clickbait" in rows["You won't believe this model"].rejection_reason


def test_off_niche_page_from_a_trusted_host_stays_in_review(db):
    _source(db, url="https://blog.google/feed.xml")

    def fetch(_source_row):
        return FetchResult(
            items=[
                ParsedItem(
                    external_id="arts",
                    title="Experience the art and legacy of a painter",
                    summary="A museum note with no product claim.",
                    url="https://blog.google/arts/example",
                    verified=True,
                    check_label="200",
                )
            ]
        )

    report = run_discovery(db, fetch=fetch)
    assert report["promoted"] == 0
    item = db.query(DiscoveredItem).one()
    assert item.status == "scored"
    assert "vocabulary" in item.rejection_reason


def test_failed_source_does_not_raise(db):
    _source(db)

    def fetch(_source_row):
        raise ValueError("Feed is not RSS or Atom.")

    report = run_discovery(db, fetch=fetch)
    assert report["created"] == 0
    assert report["failed_sources"][0][0] == "Fixture"
    assert db.get(ResearchSource, db.query(ResearchSource).one().id).last_status == "failed"


def test_offline_scripts_pass_and_slop_fails():
    for language in ("en", "cs"):
        content = build_offline_script("How machines learn", "ai", language, "short")
        result = review_content(
            title=content.title,
            hook=content.hook,
            narration=content.narration,
            scenes=[scene.narration for scene in content.scenes],
            claims=[claim.model_dump() for claim in content.factual_claims],
            cta=content.cta,
            audience="students, professionals, and founders learning practical AI",
            prior_hooks=[],
            prior_claims=[],
            prior_endings=[],
            threshold=70,
        )
        assert result["passed"], (language, result["quality_score"], result["issues"])
        assert result["central_question"]
        assert result["evidence"][0]["kind"] == "unverified"
    slop = review_content(
        title="AI update",
        hook="In today's fast-paced world, let's dive in.",
        narration="In today's fast-paced world, let's dive in. A 2024 study shows 87 percent gains. Buy now.",
        scenes=["In today's fast-paced world, let's dive in."],
        claims=[
            {
                "text": "A 2024 study shows 87 percent gains.",
                "sources": [],
                "verified": False,
                "unverified": True,
            }
        ],
        cta="Buy now",
        audience="everyone",
        prior_hooks=[],
        prior_claims=[],
        prior_endings=[],
        threshold=70,
    )
    assert slop["passed"] is False
    kinds = {issue["kind"] for issue in slop["issues"]}
    assert "generic_phrase" in kinds
    assert "unsupported_claim" in kinds
    assert "promo_language" in kinds
    repeated = review_content(
        title="How models learn from examples",
        hook="A model is a pattern, not a mind.",
        narration=(
            "A model is a pattern, not a mind. "
            "The useful habit is to check the source before you repeat it."
        ),
        scenes=["Check the source."],
        claims=[{"text": "Models can be wrong.", "sources": [], "verified": False, "unverified": True}],
        cta="Check the source.",
        audience="students",
        prior_hooks=["A model is a pattern, not a mind."],
        prior_claims=[],
        prior_endings=[],
        threshold=70,
    )
    assert any(issue["kind"] == "repetitive_opening" for issue in repeated["issues"])


def test_editorial_gate_blocks_a_new_render(db, monkeypatch):
    monkeypatch.setenv("EDITORIAL_GATE", "true")
    get_settings.cache_clear()

    class FakeReview:
        passed = False
        quality_score = 10
        threshold = 70
        issues = [{"kind": "unsupported_claim", "detail": "No source."}]

    monkeypatch.setattr("app.services.pipeline.review_script", lambda _session, _script: FakeReview())
    from app.services.pipeline import produce_short

    with pytest.raises(QualityGateError):
        produce_short(
            db,
            title="Gate check topic",
            language="en",
            category="ai",
            approve=False,
            export=False,
        )


def test_research_page_states_the_limit(db):
    db.commit()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        page = client.get("/research")
        assert page.status_code == 200
        assert "not search volume" in page.text
        assert "arXiv cs.AI" in page.text
        assert "not a verified fact" in page.text
