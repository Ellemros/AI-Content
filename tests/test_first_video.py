"""Fixes behind the first real production run: local voice, honest hedging, revision, sync."""

from types import SimpleNamespace

import pytest
from app.config import get_settings
from app.core.errors import AppError
from app.services.production import pipeline as production_pipeline
from app.services.production.scripting import dropped_hedges, narration_from_source, plan_scenes
from app.services.research.editorial import review_content
from app.services.subtitles import cues_from_alignment, render_ass

SOURCE = (
    "Crows can make visual decisions twice as quickly as humans and monkeys, according to new research. "
    "In experiments involving two carrion crows, the birds needed an average of 235 milliseconds to "
    "decide which way a pattern of moving dots was traveling. "
    "Crows appear to make decisions using neural processes similar to those found in humans and other "
    "primates, even though their brains are organized very differently. "
    "The researchers recorded neural activity in a part of the bird brain called the nidopallium. "
    "With clear signals, the birds achieved an accuracy of nearly 100 percent."
)
TITLE = "Crows can make decisions twice as fast as humans"


def _review(claims, *, hook="Crows decide before you blink.", priors=None):
    priors = priors or {}
    return review_content(
        title=TITLE,
        hook=hook,
        narration=hook + " Crows decide quickly. The takeaway is that small brains can be fast.",
        scenes=[],
        claims=claims,
        cta="",
        audience="adults",
        prior_hooks=priors.get("hooks", []),
        prior_claims=priors.get("claims", []),
        prior_endings=priors.get("endings", []),
        threshold=70,
    )


def test_a_hedged_claim_labelled_hedged_is_not_speculation_as_fact():
    claim = {
        "text": "Crows may use processes similar to primates.",
        "sources": ["https://x.test/a"],
        "verified": True,
    }
    labelled = _review([{**claim, "certainty": "hedged"}])
    assert "speculation_as_fact" not in [issue["kind"] for issue in labelled["issues"]]
    assert labelled["evidence"][0]["kind"] == "speculation"
    unlabelled = _review([{**claim, "certainty": "established"}])
    assert "speculation_as_fact" in [issue["kind"] for issue in unlabelled["issues"]]


def test_a_flat_statement_of_a_hedged_source_sentence_is_found():
    flat = dropped_hedges(["Their brains use neural processes similar to humans and primates."], SOURCE)
    assert flat
    kept = dropped_hedges(["Crows appear to use neural processes similar to humans."], SOURCE)
    assert kept == []
    unrelated = dropped_hedges(["The birds needed 235 milliseconds to decide."], SOURCE)
    assert unrelated == []


def _storyboard(**overrides):
    beats = [
        {
            "text": "A crow can pick a direction faster than you can blink.",
            "action": "surprise",
            "label": "Blink",
        },
        {
            "text": "In one test, two carrion crows needed 235 milliseconds.",
            "action": "clock",
            "label": "235 ms",
        },
        {
            "text": "They judged which way a cloud of moving dots was traveling.",
            "action": "point",
            "label": "Dots",
        },
        {"text": "With clear signals, they were right nearly 100 percent of the time.", "action": "explain"},
        {
            "text": "Crows appear to decide with processes similar to primates.",
            "action": "think",
            "label": "Same",
        },
        {
            "text": "Speed and accuracy can come from a very differently organized brain.",
            "action": "celebrate",
        },
    ]
    draft = {
        "title": "How crows out-decide us",
        "beats": beats,
        "claims": [
            {
                "text": "Two carrion crows needed 235 milliseconds to choose a direction.",
                "certainty": "established",
            },
            {"text": "Crows appear to decide using processes similar to primates.", "certainty": "hedged"},
        ],
        "description": "Crows are fast deciders.",
        "hashtags": ["#crows"],
    }
    draft.update(overrides)
    return draft


def test_storyboard_beats_become_the_narration_and_direct_the_scenes():
    draft = narration_from_source(
        title=TITLE,
        extracted=SOURCE,
        url="https://x.test/crows",
        language="en",
        complete=lambda **_: _storyboard(),
    )
    assert draft["script_mode"] == "original"
    assert draft["narration"] == " ".join(beat["text"] for beat in _storyboard()["beats"])
    assert [claim["certainty"] for claim in draft["claims"]] == ["established", "hedged"]
    scenes = plan_scenes(draft["beats"], draft["visual_queries"], draft["beat_directions"])
    assert scenes[0]["action"] == "surprise"
    assert scenes[1]["on_screen_text"] == "235 ms"
    assert scenes[3]["on_screen_text"]  # label falls back to keywords


def test_a_dropped_hedge_rejects_the_model_script_without_falling_back():
    beats = _storyboard()["beats"]
    beats[4] = {
        "text": "Crows decide with neural processes similar to primates and humans.",
        "action": "think",
    }
    with pytest.raises(AppError, match="only suggests"):
        narration_from_source(
            title=TITLE,
            extracted=SOURCE,
            url="https://x.test/crows",
            language="en",
            complete=lambda **_: _storyboard(beats=beats),
            allow_fallback=False,
        )


def test_the_writer_gets_the_rejection_and_revises(db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    calls = []
    bad = _storyboard()["beats"]
    bad[4] = {"text": "Crows decide with neural processes similar to primates and humans.", "action": "think"}

    def writer(**kwargs):
        calls.append(kwargs.get("feedback", ""))
        return _storyboard(beats=bad) if len(calls) == 1 else _storyboard()

    monkeypatch.setattr(production_pipeline, "_model_writer", lambda *_args: writer)
    monkeypatch.setattr(
        production_pipeline,
        "_preview_review",
        lambda *_args: {"passed": True, "issues": [], "quality_score": 100},
    )
    brand = SimpleNamespace(language="en", audience="adults", identity={}, id="brand-test")
    opportunity = SimpleNamespace(title=TITLE)
    item = SimpleNamespace(url="https://x.test/crows")
    dossier = SimpleNamespace(extracted_text=SOURCE)
    draft = production_pipeline._write_script(db, None, brand, None, opportunity, item, dossier, "en")
    assert len(calls) == 2
    assert calls[0] == ""
    assert "only suggests" in calls[1]
    assert draft["attempts"][0]["passed"] is False
    assert draft["attempts"][1]["passed"] is True


def test_a_script_that_never_passes_stops_the_job(db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr(production_pipeline, "_model_writer", lambda *_args: lambda **_kw: _storyboard())
    monkeypatch.setattr(
        production_pipeline,
        "_preview_review",
        lambda *_args: {
            "passed": False,
            "issues": [{"kind": "repetitive_opening", "detail": "Same opening."}],
            "quality_score": 40,
        },
    )
    with pytest.raises(AppError, match="failed review after 2 drafts"):
        production_pipeline._write_script(
            db,
            None,
            SimpleNamespace(language="en", audience="adults", identity={}, id="brand-test"),
            None,
            SimpleNamespace(title=TITLE),
            SimpleNamespace(url="https://x.test/crows"),
            SimpleNamespace(extracted_text=SOURCE),
            "en",
        )


def test_auto_voice_never_picks_a_paid_engine(monkeypatch):
    from app.services import voice

    monkeypatch.setenv("TTS_PROVIDER", "auto")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "set")
    get_settings.cache_clear()
    monkeypatch.setattr(voice, "kokoro_available", lambda _settings: True)
    try:
        assert voice.plan_engine(get_settings(), "en") == "kokoro"
        monkeypatch.setattr(voice, "kokoro_available", lambda _settings: False)
        assert voice.plan_engine(get_settings(), "en") != "elevenlabs"
    finally:
        get_settings.cache_clear()


def test_scene_cuts_follow_the_spoken_sentences():
    segments = [
        {"start": 0.0, "end": 2.0},
        {"start": 2.3, "end": 4.0},
        {"start": 4.3, "end": 7.0},
        {"start": 7.3, "end": 9.0},
    ]
    audio = SimpleNamespace(duration_seconds=9.0, metadata_json={"alignment": {"segments": segments}})
    scenes = [
        {"narration": "One sentence."},
        {"narration": "Two sentences here. And here."},
        {"narration": "Last one."},
    ]
    durations = production_pipeline._spoken_durations(scenes, audio)
    assert durations == pytest.approx([2.15, 5.0, 1.85])
    assert production_pipeline._spoken_durations(scenes[:2], audio) is None


def test_caption_cues_stop_at_the_end_of_a_sentence():
    text = "Crows decide fast. They were right almost every time when signals were clear."
    chars = list(text)
    starts = [index * 0.05 for index in range(len(chars))]
    ends = [start + 0.05 for start in starts]
    cues = cues_from_alignment(
        {"characters": chars, "character_start_times_seconds": starts, "character_end_times_seconds": ends},
        5.0,
    )
    assert cues[0].text.replace("\n", " ") == "Crows decide fast."
    assert all(not cue.text.startswith(" ") for cue in cues)


def test_light_paper_films_get_dark_captions():
    cues = cues_from_alignment(
        {
            "characters": list("Hello there."),
            "character_start_times_seconds": [0.1 * i for i in range(12)],
            "character_end_times_seconds": [0.1 * i + 0.1 for i in range(12)],
        },
        2.0,
    )
    assert "&H001C1C1C" in render_ass(cues, "light")
    assert "&H00FFFFFF" in render_ass(cues)
