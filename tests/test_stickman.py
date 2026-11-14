from pathlib import Path

import pytest
from app.config import get_settings
from app.core.errors import AppError
from app.services.production.checks import motion_differs
from app.services.production.stickman import choose_action, render_scene, write_scene_sfx
from app.services.voice import plan_engine


def test_actions_follow_the_sentence():
    assert choose_action("People keep the option already selected on the route home.", 1) == "walk"
    assert choose_action("The same pull shows up when someone starts a daily habit.", 2) == "clock"
    assert choose_action("Writing the alternative down turns it into a comparison.", 3) == "compare"
    assert choose_action("Checking the default is a habit of attention.", 4) == "notice"
    assert choose_action("A quiet room and nothing else.", 2) == "compare"


def test_scene_moves_and_sfx_is_audible(tmp_path: Path):
    path = tmp_path / "choice.mp4"
    render_scene(path, "choose", 1.4, 1)
    moved, detail = motion_differs(path)
    assert moved, detail
    sfx = write_scene_sfx(tmp_path / "sfx.wav", [1.4])
    assert sfx.stat().st_size > 1000


def test_voicestudio_is_not_the_automatic_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "auto")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("VOICESTUDIO_URL", "http://127.0.0.1:3900")
    get_settings.cache_clear()
    assert plan_engine() != "voicestudio"
    monkeypatch.setenv("TTS_PROVIDER", "voicestudio")
    get_settings.cache_clear()
    with pytest.raises(AppError, match="CC-BY-NC"):
        plan_engine()
