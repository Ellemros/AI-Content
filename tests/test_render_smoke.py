import subprocess
from pathlib import Path

from app.services.fonts import find_font
from app.services.probes import media_summary
from app.services.video import render_mp4
from PIL import Image


def test_render_mp4_is_vertical_h264(tmp_path):
    images = []
    for index, color in enumerate(((22, 58, 68), (58, 92, 78)), start=1):
        path = tmp_path / f"s{index}.png"
        Image.new("RGB", (1080, 1920), color).save(path)
        images.append(path)
    voice = tmp_path / "voice.wav"
    music = tmp_path / "bed.wav"
    srt = tmp_path / "captions.srt"
    output = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", str(voice)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=196:sample_rate=48000:duration=2", str(music)],
        check=True,
        capture_output=True,
    )
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nStroje se učí.\n", encoding="utf-8")
    font, family = find_font(False)
    render_mp4(
        images=images,
        durations=[1.0, 1.0],
        voice=voice,
        music=music,
        srt=srt,
        output=output,
        size=(1080, 1920),
        font_family=family,
        fonts_dir=font.parent,
    )
    summary = media_summary(output)
    assert summary["width"] == 1080
    assert summary["height"] == 1920
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
    assert summary["duration"] > 1.5
    assert Path(output).stat().st_size > 1000
