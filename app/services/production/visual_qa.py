"""Automated checks on the finished MP4, plus a contact sheet for a person.

Automated checks catch technical failures: decode errors, blank or frozen scenes,
scenes that look alike, drawing in the strips that platform buttons cover, captions
that miss speech, bad loudness. They cannot judge whether a video is good. A video
from a new visual direction therefore stays `needs_review` until a person signs it off.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from app.services.probes import media_summary

# Strips that YouTube Shorts / TikTok / Reels overlay with buttons and captions.
UNSAFE_BOTTOM = 1640
UNSAFE_RIGHT = 1000


def _check(name: str, passed: bool, detail: str, required: bool = True) -> dict:
    return {"name": name, "passed": passed, "detail": detail, "required": required}


def _frame(video: Path, at: float, dest: Path) -> Image.Image:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(at, 0):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
    )
    return Image.open(dest).convert("RGB")


def decode_errors(video: Path) -> int:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True, text=True
    )
    return len([line for line in proc.stderr.splitlines() if line.strip()])


def loudness(video: Path) -> tuple[float, float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", proc.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+) dB", proc.stderr)
    return (float(mean.group(1)) if mean else -99.0, float(peak.group(1)) if peak else -99.0)


def _difference(a: Image.Image, b: Image.Image) -> float:
    small_a = a.resize((54, 96)).convert("L")
    small_b = b.resize((54, 96)).convert("L")
    return ImageStat.Stat(ImageChops.difference(small_a, small_b)).mean[0]


def _busy(region: Image.Image) -> float:
    return ImageStat.Stat(region.convert("L")).stddev[0]


def inspect(video: Path, work: Path, props: dict, *, fps: int = 30) -> dict:
    checks: list[dict] = []
    frames_dir = work / "qa"
    frames_dir.mkdir(parents=True, exist_ok=True)
    summary = media_summary(video)
    duration = float(summary["duration"])
    errors = decode_errors(video)
    checks.append(_check("full_decode", errors == 0, f"{errors} decoder messages over {duration:.2f}s."))
    checks.append(
        _check(
            "format",
            (summary["width"], summary["height"]) == (1080, 1920) and summary.get("video_codec") == "h264",
            f"{summary['width']}x{summary['height']} "
            f"{summary.get('video_codec')}/{summary.get('audio_codec')}",
        )
    )
    expected = props["durationInFrames"] / fps
    checks.append(
        _check(
            "duration_matches_voice",
            abs(duration - expected) < 0.25,
            f"{duration:.2f}s video, {expected:.2f}s plan.",
        )
    )

    # One frame from the middle of each scene, plus the opening and the last second.
    mids: list[Image.Image] = []
    cursor = 0
    for index, scene in enumerate(props["scenes"]):
        mid = (cursor + scene["durationInFrames"] * 0.6) / fps
        mids.append(_frame(video, mid, frames_dir / f"scene-{index + 1}.png"))
        cursor += scene["durationInFrames"]
    opening = _frame(video, 0.4, frames_dir / "opening.png")
    ending = _frame(video, max(duration - 0.5, 0), frames_dir / "ending.png")

    blank = [i + 1 for i, frame in enumerate(mids) if _busy(frame) < 6]
    checks.append(_check("no_empty_scenes", not blank, f"Near-empty scenes: {blank or 'none'}."))
    alike = [i + 1 for i in range(1, len(mids)) if _difference(mids[i - 1], mids[i]) < 4]
    checks.append(
        _check(
            "scenes_differ",
            not alike,
            f"Scenes that look like the one before: {alike or 'none'}.",
            required=False,
        )
    )
    unsafe = []
    for i, frame in enumerate([opening, *mids, ending]):
        bottom = frame.crop((0, UNSAFE_BOTTOM, 1080, 1920))
        right = frame.crop((UNSAFE_RIGHT, 300, 1080, UNSAFE_BOTTOM))
        if _busy(bottom) > 14 or _busy(right) > 20:
            unsafe.append(i)
    checks.append(
        _check(
            "safe_area",
            not unsafe,
            "Nothing drawn under the platform buttons."
            if not unsafe
            else f"Content in unsafe strips in frames {unsafe}.",
            required=False,
        )
    )
    words = sum(len(caption["words"]) for caption in props["captions"])
    spoken = len(" ".join(" ".join(w["w"] for w in c["words"]) for c in props["captions"]).split())
    last = max((caption["to"] for caption in props["captions"]), default=0) / fps
    checks.append(
        _check(
            "captions_cover_speech",
            words > 0 and words == spoken and last <= duration + 0.2,
            f"{words} caption words, last caption ends at {last:.2f}s.",
        )
    )
    mean, peak = loudness(video)
    checks.append(
        _check("loudness", -20 <= mean <= -12 and peak <= -0.5, f"mean {mean:.1f} dB, peak {peak:.1f} dB.")
    )

    sheet = frames_dir / "contact-sheet.png"
    tiles = [opening, *mids, ending]
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * 270, rows * 480), "white")
    for i, tile in enumerate(tiles):
        canvas.paste(tile.resize((270, 480)), ((i % cols) * 270, (i // cols) * 480))
    canvas.save(sheet)
    required_ok = all(check["passed"] for check in checks if check["required"])
    return {
        "checks": checks,
        "passed": required_ok,
        "contact_sheet": str(sheet),
        "scene_frames": [str(frames_dir / f"scene-{i + 1}.png") for i in range(len(mids))],
        "human_review": "required: automated checks cannot judge whether the visuals tell the story well.",
    }
