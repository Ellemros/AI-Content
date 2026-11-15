"""Gemini with OpenAI fallback, the hook editor, the topic agent's checks, and approved videos.

MOCKED: Gemini, OpenAI, Wikipedia, and OpenAlex calls are replaced here. These tests check the
fallback order, that the agent never adds a topic without a usable source, and the queue rules.
"""

import httpx
import pytest
from app.config import get_settings
from app.models import ContentOpportunity, ProductionJob
from app.providers import gemini_provider
from app.services.automation import create_brand
from app.services.production import llm
from app.services.publishing import queue as publishing
from app.services.research import topic_agent
from tests.test_publishing import _brand, _video


@pytest.fixture()
def keys(monkeypatch, db):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("CHEAP_MODEL_PROVIDER", "gemini")
    get_settings.cache_clear()
    gemini_provider.reset_exhaustion()
    yield
    gemini_provider.reset_exhaustion()
    get_settings.cache_clear()


def _openai_reply(json, usage=None):  # noqa: A002
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json}}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 10},
        },
    )


def test_gemini_answers_first_and_costs_nothing_on_the_free_tier(db, keys, monkeypatch):
    sent = []

    def post(url, headers, json, timeout):  # noqa: A002
        sent.append(url)
        assert "generativelanguage" in url
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3},
            },
        )

    monkeypatch.setattr("app.providers.gemini_provider.httpx.post", post)
    assert llm.cheap_json(db, None, operation="t", system="s", user="u") == {"ok": True}
    assert len(sent) == 1


def test_a_quota_error_falls_back_to_openai_and_stops_asking_gemini(db, keys, monkeypatch):
    calls = {"gemini": 0, "openai": 0}

    def post(url, headers, json, timeout):  # noqa: A002 - both providers share the httpx module
        if "generativelanguage" in url:
            calls["gemini"] += 1
            return httpx.Response(429, json={"error": {"code": 429}})
        calls["openai"] += 1
        return _openai_reply('{"ok": "openai"}')

    monkeypatch.setattr("app.providers.gemini_provider.httpx.post", post)
    assert llm.cheap_json(db, None, operation="a", system="s", user="1") == {"ok": "openai"}
    assert llm.cheap_json(db, None, operation="b", system="s", user="2") == {"ok": "openai"}
    assert calls == {"gemini": 1, "openai": 2}  # after a 429 Gemini rests instead of being retried


def test_the_hook_editor_swaps_in_the_chosen_hook(db, keys, monkeypatch):
    from app.services.production.pipeline import _choose_hook

    brand = create_brand(db, name="Hook Test", slug="hook-test", niche="money", audience="adults")
    opportunity = ContentOpportunity(brand_id=brand.id, title="Wine price")
    db.add(opportunity)
    db.flush()
    job = ProductionJob(brand_id=brand.id, opportunity_id=opportunity.id, status="running")
    db.add(job)
    db.flush()
    monkeypatch.setattr(
        "app.services.production.llm.cheap_json", lambda *a, **k: {"choice": 2, "why": "more concrete"}
    )
    parsed = {
        "hook_options": [
            {"text": "Prices trick you.", "headline": "Prices trick you"},
            {"text": "Same wine. The $90 label tasted better.", "headline": "The $90 label tasted better"},
            {"text": "Is price taste?", "headline": "Is price taste?"},
        ],
        "beats": [
            {"text": "Prices trick you.", "template": "hook", "params": {"headline": "Prices trick you"}},
            {"text": "Researchers relabelled one wine.", "template": "study", "params": {}},
        ],
    }
    out = _choose_hook(db, job, parsed, "Does price change taste?")
    assert out["beats"][0]["text"] == "Same wine. The $90 label tasted better."
    assert out["beats"][0]["params"]["headline"] == "The $90 label tasted better"
    assert job.report["hook_choice"]["chosen"] == 2


def test_the_topic_agent_adds_only_topics_with_a_usable_source(db, monkeypatch, tmp_path):
    brand = create_brand(
        db, name="Agent Test", slug="agent-test", niche="money psychology", audience="adults"
    )
    monkeypatch.setattr(
        "app.services.production.llm.cheap_json",
        lambda *a, **k: {
            "topics": [
                {
                    "title": "Why $4.99 feels cheaper",
                    "question": "Why does one cent change how a price feels?",
                    "concept": "Left-digit bias",
                    "wikipedia": "Psychological pricing",
                    "paper_query": "left digit bias",
                },
                {
                    "title": "Why nobody can source this",
                    "question": "Is this real?",
                    "concept": "Imaginary effect",
                    "wikipedia": "Imaginary effect",
                    "paper_query": "imaginary effect",
                },
            ]
        },
    )
    monkeypatch.setattr(
        topic_agent,
        "wikipedia_url",
        lambda title: (
            "https://en.wikipedia.org/wiki/Psychological_pricing" if "pricing" in title.lower() else ""
        ),
    )
    monkeypatch.setattr(topic_agent, "open_papers", lambda query, concept: [])
    monkeypatch.setattr(topic_agent, "_usable", lambda url, question, concept="": True)
    path = tmp_path / "topics.json"
    found = topic_agent.find_topics(db, brand, path, count=2)
    assert [entry["title"] for entry in found["added"]] == ["Why $4.99 feels cheaper"]
    assert found["rejected"] == [{"title": "Why nobody can source this", "why": "no fetchable source"}]
    assert "Secondary source only" in " ".join(found["added"][0]["checks"])


def test_an_approved_video_can_be_queued(db, tmp_path):
    brand = _brand(db, mode="approval")
    video = _video(db, tmp_path, status="approved", approved=True)
    video.quality_report = {"human_review": "required"}
    publishing.plan_posts(db, video, brand)
    assert publishing.queue_ready(db, brand) > 0
