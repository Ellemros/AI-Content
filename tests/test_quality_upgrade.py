"""Quality upgrade. Provider calls are mocked. The film uses local sample clips."""

import base64
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from app.config import get_settings
from app.core.errors import AppError
from app.models import APIUsage, ContentOpportunity, ProductionCost, ProductionJob
from app.providers.elevenlabs import synthesize
from app.services.automation import create_brand
from app.services.probes import media_summary, run_ffmpeg
from app.services.production.budget import add_cost, assert_production_budget
from app.services.production.checks import assess_production
from app.services.production.extract import extract_article
from app.services.production.film import render_film, write_subtitles
from app.services.production.pexels import cache_path, choose_file, download_video, pick_video, search_videos
from app.services.production.scripting import narration_from_source, plan_scenes
from app.services.production.speech import speakable
from app.services.subtitles import parse_srt
from sqlalchemy import select

ARTICLE = """
<html><body><article>
<h1>Cold plates and battery cooling</h1>
<p>Cold plates pull heat away from the cells during a fast charge in the pack.</p>
<p>The pack then slows the current once the plates approach their temperature limit.</p>
<p>Engineers measure the plate temperature before they raise the charge rate again.</p>
<p>A failed sensor leaves the controller without a plate reading for that cycle.</p>
</article></body></html>
"""

NARRATION = (
    "Cold plates sit against the cells and carry heat out while the pack charges. "
    "The current drops when those plates get close to the temperature limit. "
    "Engineers check that plate temperature before they let the charge rate climb. "
    "A failed sensor means the controller has no plate reading for the cycle. "
    "The pack protects itself by watching the plates."
)


def _source() -> str:
    return extract_article(ARTICLE)


def _original(**_kwargs) -> dict:
    beats = [part.strip() for part in NARRATION.split(".") if part.strip()]
    return {
        "title": "Cold plates and battery cooling",
        "hook": beats[0],
        "narration": NARRATION,
        "beats": [beat + "." for beat in beats],
        "visual_queries": [
            "battery cold plate close up",
            "temperature gauge on machinery",
            "engineer measuring equipment",
            "failed sensor on a controller",
            "battery pack cooling",
        ],
        "claims": [
            {"text": "Cold plates carry heat away from the cells while the pack charges."},
            {"text": "The current drops as the plates approach the temperature limit."},
        ],
        "description": "How a cold plate limits a fast charge.",
        "hashtags": ["#battery"],
    }


def test_original_script_is_kept_when_it_is_grounded():
    draft = narration_from_source(
        title="Cold plates and battery cooling",
        extracted=_source(),
        url="https://example.com/plates",
        language="en",
        complete=_original,
    )
    assert draft["provider"] == "openai"
    assert draft["script_mode"] == "original"
    assert draft["rejected_model"] == ""
    assert "cold plates" in draft["narration"].casefold()
    assert all(claim["sources"] == ["https://example.com/plates"] for claim in draft["claims"])
    scenes = plan_scenes(draft["beats"], draft["visual_queries"])
    assert scenes[0]["visual_query"].startswith("battery cold plate")
    assert len({scene["visual_query"] for scene in scenes}) >= 4


def test_copied_sentences_invented_numbers_and_a_missing_model_stay_a_fallback():
    extracted = _source()
    copied = narration_from_source(
        title="Cold plates and battery cooling",
        extracted=extracted,
        url="https://example.com/plates",
        language="en",
        complete=lambda **_kwargs: {
            "narration": extracted,
            "beats": extracted.split(". "),
            "claims": [{"text": "Cold plates carry heat away from the cells during charging."}],
        },
    )
    assert copied["provider"] == "dossier"
    assert copied["script_mode"] == "fallback"
    assert "copies a source sentence" in copied["rejected_model"]

    numbered = narration_from_source(
        title="Cold plates and battery cooling",
        extracted=extracted,
        url="https://example.com/plates",
        language="en",
        complete=lambda **_kwargs: {
            **_original(),
            "narration": NARRATION + " A study of 99 packs confirmed it.",
        },
    )
    assert numbered["script_mode"] == "fallback"
    assert "number" in numbered["rejected_model"]

    offline = narration_from_source(
        title="Cold plates and battery cooling",
        extracted=extracted,
        url="https://example.com/plates",
        language="en",
        complete=None,
    )
    assert offline["provider"] == "dossier"
    assert offline["script_mode"] == "fallback"
    assert offline["rejected_model"] == ""


def test_speakable_expands_abbreviations_and_grouped_digits():
    spoken = speakable("Dr. Kim cited 1,200 cells, e.g. the pack at 20°C.")
    assert spoken.startswith("Doctor Kim")
    assert "1200" in spoken
    assert "for example" in spoken
    assert "degrees Celsius" in spoken


class _Response:
    def __init__(self, status, payload, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = json.dumps(payload) if isinstance(payload, dict) else ""

    def json(self):
        return self._payload


def test_elevenlabs_uses_timestamps_and_records_cost(db, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "25")
    get_settings.cache_clear()
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json
        audio = base64.b64encode(b"fake-audio-bytes").decode()
        alignment = {
            "characters": list("Cold plates."),
            "character_start_times_seconds": [index * 0.1 for index in range(12)],
            "character_end_times_seconds": [index * 0.1 + 0.1 for index in range(12)],
        }
        return _Response(200, {"audio_base64": audio, "alignment": alignment})

    monkeypatch.setattr(httpx, "post", post)
    dest = Path(get_settings().data_dir) / "voice.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    synthesize(
        db,
        text="Cold plates.",
        voice_id="voice-1",
        stability=0.5,
        similarity=0.75,
        out_path=dest,
    )
    assert seen["url"].endswith("/text-to-speech/voice-1/with-timestamps")
    assert seen["headers"]["xi-api-key"] == "test-key"
    assert seen["body"]["model_id"] == "eleven_multilingual_v2"
    assert dest.read_bytes() == b"fake-audio-bytes"
    alignment = json.loads(dest.with_name("voice.alignment.json").read_text(encoding="utf-8"))
    assert alignment["characters"][0] == "C"
    usage = db.scalar(select(APIUsage).where(APIUsage.provider == "elevenlabs"))
    assert usage is not None
    assert usage.operation == "text-to-speech"
    assert Decimal(str(usage.estimated_cost_usd)) > 0


def test_elevenlabs_binary_endpoint_is_used_when_timestamps_are_refused(db, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "25")
    get_settings.cache_clear()
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if url.endswith("/with-timestamps"):
            return _Response(404, {"detail": "missing"})
        return _Response(200, {}, content=b"mpeg-bytes")

    monkeypatch.setattr(httpx, "post", post)
    dest = Path(get_settings().data_dir) / "plain.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    synthesize(
        db,
        text="Cold plates.",
        voice_id="voice-1",
        stability=0.4,
        similarity=0.8,
        out_path=dest,
    )
    assert calls[0].endswith("/with-timestamps")
    assert calls[1].endswith("/text-to-speech/voice-1")
    assert dest.read_bytes() == b"mpeg-bytes"
    assert not dest.with_name("plain.alignment.json").exists()


def _video(identity: str, width: int, height: int) -> dict:
    return {
        "id": identity,
        "url": f"https://www.pexels.com/video/{identity}/",
        "duration": 12,
        "user": {"name": "Ada Frame", "url": "https://www.pexels.com/@ada"},
        "video_files": [
            {
                "file_type": "video/mp4",
                "width": width,
                "height": height,
                "link": f"https://images.pexels.com/videos/{identity}.mp4",
            }
        ],
    }


def test_pexels_search_prefers_a_new_portrait_file_and_caches_the_download(db, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test")
    get_settings.cache_clear()
    downloads = {"count": 0}

    class Client:
        def get(self, url, params=None, headers=None, timeout=None, follow_redirects=None):
            assert headers["Authorization"] == "pexels-test"
            assert params["orientation"] == "portrait"
            return _Response(
                200,
                {
                    "videos": [
                        _video("old", 1080, 1920),
                        _video("fresh", 1080, 1920),
                        _video("wide", 1920, 1080),
                    ]
                },
            )

    videos = search_videos("battery cold plate", client=Client())
    chosen = choose_file(videos[2])
    assert chosen["height"] == 1080
    first = pick_video(videos, used_ids=set(), recent_ids={"old"})
    assert first["id"] == "fresh"
    assert first["author"] == "Ada Frame"
    assert "Pexels License" in first["license"]
    again = pick_video(videos, used_ids={"fresh", "wide"}, recent_ids={"old"})
    assert again["id"] == "old"

    class Downloader:
        def get(self, url, timeout=None, follow_redirects=None):
            downloads["count"] += 1
            return _Response(200, {}, content=b"x" * 2000)

    dest = cache_path("fresh")
    download_video("https://images.pexels.com/videos/fresh.mp4", dest, client=Downloader())
    download_video("https://images.pexels.com/videos/fresh.mp4", dest, client=Downloader())
    assert downloads["count"] == 1
    assert dest.is_file() and dest.stat().st_size > 1000


def _media(path: Path, source: str, duration: float) -> None:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ]
    )


def _tone(path: Path, duration: float) -> None:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


def test_local_sample_footage_renders_a_vertical_film_with_safe_captions(tmp_path):
    clips = []
    for index, color in enumerate(("0xF2C14E", "0x3DB8C9", "0xE07A5F", "0x81B29A")):
        path = tmp_path / f"clip-{index}.mp4"
        _media(path, f"color=c={color}:s=720x1280:r=30:d=2", 2)
        clips.append(path)
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    _tone(voice, 6)
    _tone(music, 6)
    narration = (
        "Cold plates carry heat away from the cells. "
        "The current drops near the temperature limit. "
        "Engineers check the plate before the rate climbs. "
        "A failed sensor leaves the controller without a reading."
    )
    srt = tmp_path / "captions.srt"
    write_subtitles(narration, 5.0, srt)
    cues = parse_srt(srt.read_text(encoding="utf-8"))
    assert cues[-1].end <= 5.2
    assert all(cue.end > cue.start for cue in cues)
    for earlier, later in zip(cues, cues[1:], strict=False):
        assert later.start >= earlier.start
    assert all(len(line) <= 42 for cue in cues for line in cue.text.splitlines())
    ass = srt.with_suffix(".ass").read_text(encoding="utf-8")
    assert "MarginV, Encoding" in ass
    assert ",320,1" in ass
    assert "PlayResY: 1920" in ass
    output = tmp_path / "film.mp4"
    render_film(
        images=clips,
        durations=[1.25, 1.25, 1.25, 1.25],
        voice=voice,
        music=music,
        srt=srt,
        output=output,
        kinds=["clip", "clip", "clip", "clip"],
    )
    summary = media_summary(output)
    assert (summary["width"], summary["height"]) == (1080, 1920)
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
    assert 4.5 <= summary["duration"] <= 5.5
    report = assess_production(
        video_path=output,
        subtitle_path=srt,
        scene_paths=clips,
        narration=narration,
        extracted=_source(),
        title="Cold plates and battery cooling",
        claims=[
            {
                "text": "Cold plates carry heat away from the cells.",
                "sources": ["https://example.com/plates"],
                "verified": True,
                "unverified": False,
            }
        ],
        cost=Decimal("0"),
        video_cap=Decimal("1"),
        script_mode="original",
        voice_provider="elevenlabs",
        visual_kinds=["clip", "clip", "clip", "clip"],
        clip_ids=["a", "b", "c", "d"],
    )
    assert report["status"] == "ready", report["warnings"]
    assert report["semantic_visual"] == "unverified"
    limited = assess_production(
        video_path=output,
        subtitle_path=srt,
        scene_paths=clips,
        narration=narration,
        extracted=_source(),
        title="Cold plates and battery cooling",
        claims=[
            {
                "text": "Cold plates carry heat away from the cells.",
                "sources": ["https://example.com/plates"],
                "verified": True,
                "unverified": False,
            }
        ],
        cost=Decimal("0"),
        video_cap=Decimal("1"),
        script_mode="fallback",
        voice_provider="espeak",
        visual_kinds=["image", "image", "image", "image"],
        clip_ids=[],
    )
    assert limited["status"] == "needs_review"
    assert limited["passed"] is True
    landscape = tmp_path / "wide.mp4"
    _media(landscape, "color=c=gray:s=1280x720:r=30:d=2", 2)
    failed = assess_production(
        video_path=landscape,
        subtitle_path=srt,
        scene_paths=clips,
        narration=narration,
        extracted=_source(),
        title="Cold plates and battery cooling",
        claims=[],
        cost=Decimal("0"),
        video_cap=Decimal("1"),
        script_mode="original",
        voice_provider="elevenlabs",
        visual_kinds=["clip"] * 4,
        clip_ids=["a", "b", "c", "d"],
    )
    assert failed["status"] == "failed"
    assert failed["passed"] is False


def test_repeated_clip_ids_are_not_treated_as_full_coverage(tmp_path):
    # The status comes from the clip ids, not from a second render.
    video = tmp_path / "missing.mp4"
    srt = tmp_path / "captions.srt"
    srt.write_text("", encoding="utf-8")
    report = assess_production(
        video_path=video,
        subtitle_path=srt,
        scene_paths=[],
        narration="Cold plates.",
        extracted=_source(),
        title="Cold plates and battery cooling",
        claims=[],
        cost=Decimal("0"),
        video_cap=Decimal("1"),
        script_mode="original",
        voice_provider="elevenlabs",
        visual_kinds=["clip", "clip", "clip", "clip"],
        clip_ids=["same", "same", "same", "same"],
    )
    names = {item["name"]: item for item in report["checks"]}
    assert names["footage_coverage"]["passed"] is False
    assert report["status"] == "failed"


def test_paid_estimates_stop_at_the_video_cap_and_costs_stay_separate(db):
    brand = create_brand(
        db,
        name="Cost notes",
        niche="Hardware",
        audience="Repair",
        language="en",
        formats=["short"],
    )
    opportunity = ContentOpportunity(brand_id=brand.id, title="Cold plates", status="evaluated")
    db.add(opportunity)
    db.flush()
    job = ProductionJob(brand_id=brand.id, opportunity_id=opportunity.id, status="running")
    db.add(job)
    db.flush()
    add_cost(db, job, provider="openai", operation="production-script", cost=Decimal("0.20"))
    add_cost(db, job, provider="elevenlabs", operation="narration", cost=Decimal("0.30"))
    add_cost(db, job, provider="pexels", operation="footage", cost=0)
    rows = db.scalars(select(ProductionCost).where(ProductionCost.job_id == job.id)).all()
    assert {row.provider: Decimal(str(row.estimated_cost_usd)) for row in rows} == {
        "openai": Decimal("0.20"),
        "elevenlabs": Decimal("0.30"),
        "pexels": Decimal("0"),
    }
    with pytest.raises(AppError, match="Production spend cap"):
        assert_production_budget(db, job, Decimal("0.60"))
