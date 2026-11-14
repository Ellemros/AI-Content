"""Motion-graphics storyboard, captions, research dossier, and the model-aware OpenAI client.

The render test at the bottom is real: Remotion draws the frames, FFmpeg mixes the audio,
and the visual QA inspects the MP4. It is skipped (not faked) when renderer/ is not installed.
"""

import json
import math
import struct
import wave

import httpx
import pytest
from app.core.errors import AppError
from app.services.production import motion
from app.services.research import dossier

SOURCE = (
    "Citation: Neural mechanisms of credit card spending. Scientific Reports, 2021-02-18.\n"
    "They spent more overall when using credit card (average basket = $87.41) rather than cash ($84.19).\n"
    "Credit card purchases were associated with strong activation in the striatum."
)
ALLOWED = {"87.41", "84.19", "2021", "02", "18"}


def test_unknown_template_and_objects_are_replaced_not_fatal():
    scene = motion.normalise({"template": "spaceship", "params": {"object": "rocket", "label": "x"}}, ALLOWED)
    assert scene["template"] == "object"
    assert scene["params"]["object"] == "lightbulb"


def test_an_unsourced_number_on_screen_demotes_the_scene():
    scene = motion.normalise(
        {
            "template": "bars",
            "text": "People spend more.",
            "params": {"bars": [{"label": "Card", "value": 99}, {"label": "Cash", "value": 50}]},
        },
        ALLOWED,
        SOURCE,
    )
    assert scene["template"] == "object"


def test_a_stat_needs_its_number_in_a_matching_source_sentence():
    good = motion.normalise(
        {
            "template": "stat",
            "text": "Average baskets were $87.41 by card.",
            "params": {"value": 87.41, "prefix": "$", "label": "Average basket by card"},
        },
        ALLOWED,
        SOURCE,
    )
    assert good["template"] == "stat"
    lonely = motion.normalise(
        {
            "template": "stat",
            "text": "Cards light up reward areas.",
            "params": {"value": 1, "label": "Reward areas"},
        },
        ALLOWED | {"1"},
        SOURCE,
    )
    assert lonely["template"] == "object"


def test_structural_kickers_are_hidden_and_negated_brain_is_not_lit():
    hook = motion.normalise({"template": "hook", "kicker": "Hook", "params": {"headline": "x"}}, ALLOWED)
    assert hook["kicker"] == ""
    brain = motion.normalise(
        {
            "template": "brain",
            "text": "The insula did not separate card from cash.",
            "params": {"tone": "pain"},
        },
        ALLOWED,
    )
    assert brain["params"]["tone"] == "neutral"


def test_takeaway_badge_follows_certainty():
    open_end = motion.normalise(
        {"template": "takeaway", "text": "The mechanism is still open.", "params": {"line": "Still open"}},
        ALLOWED,
    )
    settled = motion.normalise(
        {"template": "takeaway", "text": "Cards change the cue.", "params": {"line": "Cards change the cue"}},
        ALLOWED,
    )
    assert open_end["params"]["mark"] == "question"
    assert settled["params"]["mark"] == "check"


def _alignment(text: str, seconds_per_char: float = 0.06) -> dict:
    chars = list(text)
    starts = [i * seconds_per_char for i in range(len(chars))]
    return {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": [s + seconds_per_char for s in starts],
    }


def test_captions_are_short_never_cross_a_sentence_and_end_with_the_video():
    text = "Card or cash changes what you buy. The striatum lit at the card cue."
    alignment = _alignment(text)
    words = motion.words_from_alignment(alignment)
    captions = motion.captions_from_words(words)
    assert all(len(caption["words"]) <= 3 for caption in captions)
    assert not any(w["w"].endswith(".") for caption in captions for w in caption["words"][:-1])
    total = len(text) * 0.06
    props = motion.build_props(
        [
            {"template": "hook", "kicker": "", "params": {}},
            {"template": "object", "kicker": "", "params": {}},
        ],
        [total / 2, total / 2],
        alignment,
        brand="Test",
        theme="money-minds",
    )
    assert max(caption["to"] for caption in props["captions"]) <= props["durationInFrames"]
    assert props["scenes"][1]["words"][0]["f0"] >= 0


def test_citation_line_and_condense_keep_the_year():
    html = (
        '<meta name="dc.title" content="Neural mechanisms of credit card spending"/>'
        '<meta name="prism.publicationName" content="Scientific Reports"/>'
        '<meta name="prism.publicationDate" content="2021-02-18"/>'
    )
    line = dossier.citation_line(html)
    assert line == "Citation: Neural mechanisms of credit card spending. Scientific Reports, 2021-02-18."
    body = "\n".join(
        f"Paragraph {i} about credit card spending and the brain reward network today." for i in range(80)
    )
    condensed = dossier.condense(f"{line}\n{body}", "credit card spending brain", 800)
    assert condensed.startswith("Citation:") and len(condensed) <= 900


def test_wikipedia_goes_through_the_rest_api_and_refusals_are_skipped():
    assert dossier._fetch_url("https://en.wikipedia.org/wiki/Pain_of_paying").endswith(
        "/api/rest_v1/page/html/Pain_of_paying"
    )

    def fetch(url):
        if "blocked" in url:
            raise AppError(f"{url} refused the request (HTTP 403). It is not used.")
        return 200, "A long article sentence about credit cards and spending behaviour. " * 20

    sources = dossier.build(["https://blocked.test/a", "https://ok.test/b"], "credit cards", fetch=fetch)
    assert [source.url for source in sources] == ["https://ok.test/b"]
    assert sources[0].label == "S1"
    with pytest.raises(AppError):
        dossier.build(["https://blocked.test/a"], "credit cards", fetch=fetch)


def test_reasoning_models_get_their_own_request_shape(db, monkeypatch):
    from app.providers.openai_provider import OpenAIProvider

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.config import get_settings

    get_settings.cache_clear()
    sent = {}

    def fake_post(url, headers, json, timeout):  # noqa: A002
        sent.update(json)
        body = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 2000},
        }
        return httpx.Response(200, json=body)

    monkeypatch.setattr("app.providers.openai_provider.httpx.post", fake_post)
    provider = OpenAIProvider(db, model="gpt-5.4")
    assert provider.complete_json(operation="t", system="s", user="u") == {"ok": True}
    assert "max_completion_tokens" in sent and "temperature" not in sent
    cost = provider._post("s", "u")[1]["cost"]
    assert float(cost) == pytest.approx(1000 / 1e6 * 2.5 + 2000 / 1e6 * 15)
    get_settings.cache_clear()


def _tone_wav(path, seconds):
    rate = 24000
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(struct.pack("<h", int(6000 * math.sin(i / 9))) for i in range(int(seconds * rate)))
        )
    return path


@pytest.mark.skipif(not motion.available(), reason="renderer/ is not installed (npm install).")
def test_a_real_remotion_render_passes_visual_qa(tmp_path):
    from app.services.production import visual_qa

    text = "Same cart, different swipe. Average baskets were $87.41 by card."
    seconds = len(text) * 0.06
    plan = [
        {
            "template": "hook",
            "kicker": "",
            "params": {"headline": "Same cart, different swipe", "object": "cart", "object2": "card"},
        },
        {
            "template": "bars",
            "kicker": "The study",
            "params": {
                "title": "Average basket",
                "bars": [{"label": "Card", "value": 87.41}, {"label": "Cash", "value": 84.19}],
                "prefix": "$",
            },
        },
    ]
    props = motion.build_props(
        plan, [seconds / 2, seconds / 2], _alignment(text), brand="Money Minds", theme="money-minds"
    )
    out = tmp_path / "out.mp4"
    silent = motion.render(props, tmp_path, out)
    voice = _tone_wav(tmp_path / "voice.wav", seconds)
    music = _tone_wav(tmp_path / "music.wav", seconds)
    sfx = motion.sfx_track(tmp_path / "sfx.wav", props)
    motion.mix(silent, voice, music, sfx, out)
    report = visual_qa.inspect(out, tmp_path, props)
    failed = [check["name"] for check in report["checks"] if check["required"] and not check["passed"]]
    assert failed == [], json.dumps(report["checks"], indent=1)
    assert (tmp_path / "qa" / "contact-sheet.png").is_file()
