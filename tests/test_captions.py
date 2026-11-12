import subprocess

import pytest
from app.services.fonts import find_font
from app.services.probes import media_summary
from app.services.subtitles import parse_srt, render_srt, validate_cue_coverage
from app.services.video import burn_subtitles


def test_edited_srt_round_trip():
    original = (
        "1\n00:00:00,000 --> 00:00:01,200\nMachines learn.\n\n"
        "2\n00:00:01,200 --> 00:00:02,000\nFrom examples.\n"
    )
    cues = parse_srt(original)
    assert cues[0].text == "Machines learn."
    assert cues[-1].end == 2
    validate_cue_coverage(cues, 2.0)
    again = parse_srt(render_srt(cues))
    assert again[1].text == "From examples."
    with pytest.raises(ValueError):
        validate_cue_coverage(cues, 10)


def test_reburn_keeps_picture_and_audio(tmp_path):
    master = tmp_path / "master.mp4"
    output = tmp_path / "video.mp4"
    srt = tmp_path / "captions.srt"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x243044:s=320x568:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(master),
        ],
        check=True,
        capture_output=True,
    )
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nStroje se učí.\n", encoding="utf-8")
    font, family = find_font(False)
    burn_subtitles(
        master=master,
        srt=srt,
        output=output,
        size=(320, 568),
        font_family=family,
        fonts_dir=font.parent,
    )
    summary = media_summary(output)
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
    assert summary["width"] == 320
    assert summary["height"] == 568
    assert summary["duration"] > 1
