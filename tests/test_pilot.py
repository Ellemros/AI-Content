"""The production run (pilot): hard budget, topic choice, extra QA checks, approval guard, review package.

No paid call is made here. The run-budget test drives the real spend check with recorded
usage rows; the end-to-end test replaces produce_next with a stub, because the real pipeline
is covered by tests/test_motion.py and tests/test_first_video.py.
"""

import json
from decimal import Decimal

import pytest
from app.core.errors import RunBudgetExceeded
from app.models import ContentOpportunity, ProductionJob
from app.services.automation import create_brand
from app.services.cost import assert_within_cap, record_usage, run_budget, run_remaining
from app.services.production import motion, pilot, visual_qa
from app.services.publishing import queue as publishing
from app.services.research import dossier
from tests.test_publishing import _brand, _video


def test_the_run_budget_stops_a_call_that_could_go_over(db):
    with run_budget(1.0):
        record_usage(db, provider="openai", operation="t", model="gpt-5.4", cost=Decimal("0.70"))
        db.flush()
        assert run_remaining(db) == Decimal("0.30")
        assert_within_cap(db, Decimal("0.29"))  # fits
        with pytest.raises(RunBudgetExceeded):
            assert_within_cap(db, Decimal("0.31"))  # the worst case would cross the limit
    assert run_remaining(db) is None  # outside a run only the monthly cap applies
    assert_within_cap(db, Decimal("0.31"))


def test_cached_calls_do_not_count_against_the_run(db):
    with run_budget(0.5):
        record_usage(db, provider="openai", operation="t", model="m", cost=Decimal("0"), cached=True)
        record_usage(db, provider="openai", operation="t", model="m", cost=Decimal("0.2"))
        db.flush()
        assert run_remaining(db) == Decimal("0.3")


def _ranked(title, theme, *, total=20, qualified=True, structure="explainer", source="backlog", id_=None):
    return {
        "id": id_ or title,
        "title": title,
        "theme": theme,
        "structure": structure,
        "scores": {},
        "total": total,
        "qualified": qualified,
        "why": "",
        "source": source,
    }


def test_choose_takes_distinct_themes_and_varies_structures():
    ranked = [
        _ranked("Why the expensive wine tastes better", "price and taste", total=24, structure="mystery"),
        _ranked("Price labels change how wine tastes", "Price and taste", total=23, structure="mystery"),
        _ranked("Crows decide fast", "animal cognition", total=25, qualified=False),
        _ranked("How a tip screen changes tips", "defaults", total=22, structure="mystery"),
        _ranked("The jam study", "choice overload", total=21, structure="myth_vs_fact"),
    ]
    picks = pilot.choose(ranked, 5, made=["Why paying by card feels easier"], backlog={})
    assert [pick.title for pick in picks] == [
        "Why the expensive wine tastes better",
        "How a tip screen changes tips",
        "The jam study",
    ]
    assert len({pick.structure for pick in picks}) == 3  # mystery is used once, then another structure


def test_choose_skips_a_topic_too_close_to_one_already_made():
    ranked = [_ranked("Why paying by card makes spending feel easier", "card vs cash")]
    assert (
        pilot.choose(ranked, 5, made=["Why paying by card makes spending feel different"], backlog={}) == []
    )


def test_caption_and_storyboard_checks():
    words = [{"w": w, "f0": i * 10, "f1": i * 10 + 9} for i, w in enumerate("one two three".split())]
    good = {"durationInFrames": 300, "captions": [{"from": 0, "to": 30, "words": words}]}
    assert visual_qa.caption_check(good)["passed"]
    crowded = {
        "durationInFrames": 30,
        "captions": [{"from": 0, "to": 30, "words": words + [{"w": "four", "f0": 0, "f1": 1}]}],
    }
    assert not visual_qa.caption_check(crowded)["passed"]

    varied = [{"template": t} for t in ("hook", "study", "bars", "takeaway")]
    variety, relevance = visual_qa.storyboard_checks(varied)
    assert variety["passed"] and relevance["passed"]
    flat = [
        {"template": "object", "demoted": {"requested": "stat", "unknown_objects": []}},
        {"template": "object", "demoted": {"requested": "bars", "unknown_objects": []}},
        {"template": "object"},
        {"template": "takeaway"},
    ]
    variety, relevance = visual_qa.storyboard_checks(flat)
    assert not variety["passed"] and not relevance["passed"]
    assert not variety["required"] and not relevance["required"]  # flagged for a person, not hidden


def test_normalise_records_a_replaced_picture():
    scene = motion.normalise({"template": "stat", "text": "x", "params": {"value": 99, "label": "x"}}, set())
    assert scene["template"] == "object" and scene["demoted"]["requested"] == "stat"
    kept = motion.normalise({"template": "hook", "params": {"headline": "x", "object": "card"}}, set())
    assert "demoted" not in kept


def test_a_video_that_asks_for_a_person_is_never_queued_unapproved(db, tmp_path):
    brand = _brand(db, mode="autonomous")
    video = _video(db, tmp_path, status="ready", approved=False)
    video.quality_report = {"human_review": "required: a person approves every video."}
    publishing.plan_posts(db, video, brand)
    assert publishing.queue_ready(db, brand) == 0  # autonomous mode, ready status: still waits
    video.approved = True
    assert publishing.queue_ready(db, brand) > 0


def test_site_furniture_is_dropped_from_sources():
    text = (
        "Citation: A paper. Journal, 2023.\n"
        "Official websites use .gov A .gov website belongs to an official government organization.\n"
        "We found that the flattening pattern exists only among the least happy fifth of people studied."
    )
    condensed = dossier.condense(text, "happiness income", 5000)
    assert ".gov" not in condensed and "least happy" in condensed


def test_a_run_packages_every_attempt_and_reports_failures(db, tmp_path, monkeypatch):
    brand = create_brand(db, name="Pilot Test", slug="pilot-test", niche="money", audience="adults")
    brand.status = "experimental"
    topics = tmp_path / "topics.json"
    topics.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "title": "Why the expensive wine tastes better",
                        "question": "Does price change how wine tastes?",
                        "structure": "mystery",
                        "theme": "price and taste",
                        "sources": ["https://example.org/wine"],
                    },
                    {
                        "title": "How a tip screen changes what you tip",
                        "question": "Do tip buttons change tips?",
                        "structure": "versus",
                        "theme": "tipping defaults",
                        "sources": ["https://example.org/tips"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_produce(session, brand, *, opportunity_id, structure):
        calls.append(structure)
        failed = len(calls) == 1
        job = ProductionJob(
            brand_id=brand.id,
            opportunity_id=opportunity_id,
            status="failed" if failed else "succeeded",
            stage="script" if failed else "saved",
            error="The model script failed review after 3 drafts: Fact check: overstated" if failed else "",
            report={"script_attempts": [{"passed": False, "issues": ["overstated"]}] * 3},
        )
        session.add(job)
        session.flush()
        return job

    monkeypatch.setattr("app.services.production.pipeline.produce_next", fake_produce)
    out = tmp_path / "review"
    result = pilot.run(
        db, brand, count=2, budget_usd=1.0, topics_path=topics, out_dir=out, discover_feeds=False
    )
    assert calls == ["mystery", "versus"]  # backlog order and structures without a ranking model
    assert [video["status"] for video in result.videos] == ["failed", "succeeded"]
    report = json.loads((out / "pilot-report.json").read_text(encoding="utf-8"))
    assert report["published"] is False and len(report["videos"]) == 2
    failed = report["videos"][0]
    assert failed["job_status"] == "failed" and failed["drafts"] == 3 and "Fact check" in failed["error"]
    markdown = (out / "pilot-report.md").read_text(encoding="utf-8")
    assert "Videos attempted: 2" in markdown and "Failed (not rendered or blocked): 1" in markdown
    assert (out / f"01-{pilot._slug('Why the expensive wine tastes better')}" / "verification.json").is_file()
    opportunities = db.query(ContentOpportunity).filter_by(brand_id=brand.id).count()
    pilot.seed_backlog(db, brand, pilot.load_backlog(topics))
    assert db.query(ContentOpportunity).filter_by(brand_id=brand.id).count() == opportunities  # no duplicates


def test_a_continued_pilot_counts_earlier_spend_against_the_same_limit(db, tmp_path, monkeypatch):
    brand = create_brand(db, name="Pilot Test", slug="pilot-test", niche="money", audience="adults")
    brand.status = "experimental"
    out = tmp_path / "review"
    out.mkdir()
    (out / "runs.json").write_text(
        json.dumps(
            [{"started_at": "x", "finished_at": "y", "spent_usd": 0.6, "charged_usd": 0.9, "jobs": []}]
        ),
        encoding="utf-8",
    )
    topics = tmp_path / "topics.json"
    topics.write_text(
        json.dumps(
            {
                "topics": [
                    {"title": "Tips", "question": "Do tip buttons change tips?", "sources": ["https://e.org"]}
                ]
            }
        ),
        encoding="utf-8",
    )

    def never(*args, **kwargs):  # pragma: no cover - the budget must stop the run first
        raise AssertionError("a video was started without enough budget")

    monkeypatch.setattr("app.services.production.pipeline.produce_next", never)
    result = pilot.run(
        db, brand, count=1, budget_usd=1.0, topics_path=topics, out_dir=out, discover_feeds=False
    )
    assert "Budget" in result.stopped_because  # $0.10 left of $1.00, below the reserve for one video
    assert len(pilot.load_ledger(out)) == 2


def test_pmc_goes_through_the_official_api_and_jats_is_read():
    url = dossier._fetch_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC10013834/")
    assert url.startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=10013834")
    xml = (
        "<pmc-articleset><article><front><journal-meta><journal-title-group><journal-title>PNAS"
        "</journal-title></journal-title-group></journal-meta><article-meta><title-group><article-title>"
        "Income and emotional well-being</article-title></title-group><pub-date><year>2023</year></pub-date>"
        "<abstract><p>Happiness rose with log income for most people.</p></abstract></article-meta></front>"
        "<body><sec><title>Results</title><p>The flattening appeared only in the least happy group.</p></sec>"
        "</body></article></pmc-articleset>"
    )
    text = dossier.jats_text(xml)
    assert text.startswith("Citation: Income and emotional well-being. PNAS, 2023.")
    assert "least happy group" in text


def test_reference_list_entries_are_not_evidence():
    text = (
        "The endowment effect is the finding that owners value an object more than buyers would pay for it.\n"
        'Hanemann, W. Michael (1991). "Willingness To Pay and Willingness To Accept". American Economic '
        "Review. 81 (3): 635-647. doi:10.1257/000282803321455449. JSTOR 2006525.\n"
        "1 2 3 Kahneman, Daniel; Knetsch, Jack L.; Thaler, Richard H. (1990). Experimental Tests."
    )
    condensed = dossier.condense(text, "endowment effect owners buyers", 5000)
    assert "owners value" in condensed and "American Economic Review" not in condensed


def test_a_replaced_picture_never_shows_half_a_sentence():
    scene = motion.normalise(
        {
            "template": "gauge",
            "text": "Then came the twist. Higher suggested amounts also made riders leave no tip.",
            "params": {"label": "x", "from": 7, "to": 9},
        },
        set(),
    )
    assert scene["template"] == "object"
    assert "…" not in scene["params"]["label"]


def test_a_reviewer_finding_sends_a_video_back_to_review(db, tmp_path):
    from app.models import Video

    video = _video(db, tmp_path, status="ready", approved=False)
    brand = create_brand(db, name="Pilot Test", slug="pilot-test", niche="money", audience="adults")
    opportunity = ContentOpportunity(brand_id=brand.id, title="t")
    db.add(opportunity)
    db.flush()
    job = ProductionJob(
        brand_id=brand.id, opportunity_id=opportunity.id, status="succeeded", video_id=video.id
    )
    db.add(job)
    db.flush()
    pilot.flag_video(db, job.id, "Misattributed citation.")
    video = db.get(Video, video.id)
    assert video.status == "needs_review" and not video.approved
    assert video.quality_report["human_findings"][0]["finding"] == "Misattributed citation."
