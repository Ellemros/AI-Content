"""Local licensed footage. Clips are generated samples, not downloads."""

from decimal import Decimal
from pathlib import Path

import pytest
from app.config import get_settings
from app.core.errors import AppError
from app.main import create_app
from app.models import Asset
from app.services.probes import media_summary, run_ffmpeg
from app.services.production.checks import assess_production
from app.services.production.extract import extract_article
from app.services.production.film import render_film, write_subtitles
from app.services.production.footage import choose_clip, pexels_enabled, register_stock_clip
from app.services.subtitles import parse_srt
from fastapi.testclient import TestClient
from sqlalchemy import func, select


def _clip(path: Path, color: str, size: str = "640x360") -> None:
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r=30:d=1",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ]
    )


def _register(db, path: Path, **kwargs):
    payload = {
        "name": path.stem,
        "source": "Fixture shelf",
        "license_name": "Fixture license",
        "filename": path.name,
        "data": path.read_bytes(),
        "tags": "battery plate",
        "category": "workshop",
        "creator": "Ada Frame",
        "source_url": "https://example.com/clip",
        "license_url": "https://example.com/license",
        "commercial_use": "permitted",
    }
    payload.update(kwargs)
    return register_stock_clip(db, **payload)


def _scene(query: str, index: int = 1) -> dict:
    return {
        "index": index,
        "visual_query": query,
        "motifs": query.split(),
        "narration": query,
        "on_screen_text": query,
    }


def test_license_is_required_and_a_duplicate_file_is_left_alone(db, tmp_path):
    path = tmp_path / "plate.mp4"
    _clip(path, "0xE07A5F")
    with pytest.raises(AppError, match="license"):
        _register(db, path, license_name="  ")
    assert db.scalar(select(func.count()).select_from(Asset)) == 0
    first = _register(db, path)
    assert first.metadata_json["commercial_use"] == "permitted"
    assert first.metadata_json["creator"] == "Ada Frame"
    assert Path(first.path).is_file()
    with pytest.raises(AppError, match="already registered"):
        _register(db, path, name="again")
    assert Path(first.path).is_file()
    assert db.scalar(select(func.count()).select_from(Asset)) == 1


def test_unpermitted_and_unrelated_clips_are_not_selected(db, tmp_path):
    kept = tmp_path / "kept.mp4"
    blocked = tmp_path / "blocked.mp4"
    _clip(kept, "0x3DB8C9")
    _clip(blocked, "0xF2C14E")
    _register(db, blocked, commercial_use="not_recorded", tags="battery plate")
    chosen = choose_clip(db, _scene("battery plate"), used=set(), recent=set())
    assert chosen is None
    asset = _register(db, kept, tags="battery plate")
    chosen = choose_clip(db, _scene("battery plate"), used=set(), recent=set())
    assert chosen is not None
    assert chosen.id == asset.id
    assert choose_clip(db, _scene("ocean waves"), used=set(), recent=set()) is None


def test_selection_skips_a_clip_already_used_and_prefers_a_fresher_match(db, tmp_path):
    first_path = tmp_path / "first.mp4"
    second_path = tmp_path / "second.mp4"
    _clip(first_path, "0x81B29A")
    _clip(second_path, "0xE07A5F")
    first = _register(db, first_path, tags="plate heat")
    second = _register(db, second_path, tags="plate")
    scene = _scene("plate")
    picked = choose_clip(db, scene, used=set(), recent={first.id})
    assert picked is not None
    assert picked.id == second.id
    again = choose_clip(db, scene, used={second.id}, recent=set())
    assert again is not None
    assert again.id == first.id
    assert choose_clip(db, scene, used={first.id, second.id}, recent=set()) is None


def test_pexels_stays_off_unless_footage_mode_asks_for_it(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "secret")
    monkeypatch.setenv("PRODUCTION_FOOTAGE", "local")
    get_settings.cache_clear()
    assert pexels_enabled() is False
    monkeypatch.setenv("PRODUCTION_FOOTAGE", "pexels")
    get_settings.cache_clear()
    assert pexels_enabled() is True
    monkeypatch.setenv("PRODUCTION_FOOTAGE", "local")
    get_settings.cache_clear()


def test_dashboard_registers_and_previews_a_clip(db, tmp_path):
    path = tmp_path / "hands.mp4"
    _clip(path, "0xF2C14E")
    with path.open("rb") as handle, TestClient(create_app()) as client:
        response = client.post(
            "/production/stock",
            data={
                "name": "Hands",
                "source": "Fixture shelf",
                "license_name": "Fixture license",
                "tags": "hands battery",
                "category": "workshop",
                "creator": "Ada Frame",
                "source_url": "https://example.com/clip",
                "license_url": "https://example.com/license",
                "commercial_use": "permitted",
            },
            files={"upload": ("hands.mp4", handle, "video/mp4")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    db.expire_all()
    asset = db.scalar(select(Asset).where(Asset.source == "local-stock"))
    assert asset is not None
    assert asset.metadata_json["eligible"] is True
    with TestClient(create_app()) as client:
        preview = client.get(f"/production/stock/{asset.id}")
        page = client.get("/production")
    assert preview.status_code == 200
    assert preview.content[:8] != b""
    assert "Fixture license" in page.text
    assert "Eligible" in page.text


def test_sample_licensed_clips_render_a_vertical_film(db, tmp_path):
    colors = ("0xF2C14E", "0x3DB8C9", "0xE07A5F", "0x81B29A")
    words = ("alpha", "beta", "gamma", "delta")
    assets = []
    for color, word in zip(colors, words, strict=True):
        path = tmp_path / f"{word}.mp4"
        _clip(path, color, size="720x1280")
        assets.append(_register(db, path, tags=f"{word} battery", name=word))
    chosen = []
    used: set[str] = set()
    for index, word in enumerate(words, start=1):
        asset = choose_clip(db, _scene(f"{word} battery", index), used=used, recent=set())
        assert asset is not None
        assert asset.id not in used
        used.add(asset.id)
        chosen.append(asset)
    assert len({item.id for item in chosen}) == 4
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    for dest in (voice, music):
        run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:sample_rate=48000:duration=6",
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
    narration = (
        "Cold plates carry heat away from the cells. "
        "The current drops near the temperature limit. "
        "Engineers check the plate before the rate climbs. "
        "A failed sensor leaves the controller without a reading."
    )
    srt = tmp_path / "captions.srt"
    write_subtitles(narration, 5.0, srt)
    assert parse_srt(srt.read_text(encoding="utf-8"))
    output = tmp_path / "film.mp4"
    render_film(
        images=[Path(asset.path) for asset in chosen],
        durations=[1.25, 1.25, 1.25, 1.25],
        voice=voice,
        music=music,
        srt=srt,
        output=output,
        kinds=["clip", "clip", "clip", "clip"],
    )
    summary = media_summary(output)
    assert (summary["width"], summary["height"]) == (1080, 1920)
    extracted = extract_article(
        "<article><p>Cold plates pull heat away from the cells during a fast charge in the pack. "
        "The pack then slows the current once the plates approach their temperature limit. "
        "Engineers measure the plate temperature before they raise the charge rate again. "
        "A failed sensor leaves the controller without a plate reading for that cycle.</p></article>"
    )
    report = assess_production(
        video_path=output,
        subtitle_path=srt,
        scene_paths=[Path(asset.path) for asset in chosen],
        narration=narration,
        extracted=extracted,
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
        clip_ids=[asset.id for asset in chosen],
    )
    assert report["status"] == "ready", report["warnings"]
    assert report["semantic_visual"] == "unverified"
    gapped = assess_production(
        video_path=output,
        subtitle_path=srt,
        scene_paths=[Path(asset.path) for asset in chosen],
        narration=narration,
        extracted=extracted,
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
        visual_kinds=["clip", "clip", "missing", "clip"],
        clip_ids=[assets[0].id, assets[1].id, "", assets[3].id],
    )
    assert gapped["status"] == "needs_review"
    assert "No unrelated clip" in " ".join(gapped["warnings"])
