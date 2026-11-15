"""Automated checks on the finished MP4, plus a contact sheet for a person.

Automated checks catch technical failures: decode errors, blank or frozen scenes,
scenes that look alike, drawing in the strips that platform buttons cover, captions
that miss speech or are hard to read, audio that drifts from the picture, bad loudness,
and storyboards that lost their pictures or repeat one template. They cannot judge whether
a video is good: a video that passes is `ready` for a person to approve, never approved.
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


def stream_durations(video: Path) -> dict[str, float]:
    """Duration of each stream ('video', 'audio') as the container reports it."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    out: dict[str, float] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and parts[0] in {"video", "audio"}:
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                continue
    return out


# Captions: at most 3 words / 22 characters on screen, each on screen long enough to read.
CAPTION_MAX_CHARS = 22
CAPTION_MIN_SECONDS = 0.3
CAPTION_MAX_WORDS_PER_SECOND = 4.5


def caption_check(props: dict, fps: int = 30) -> dict:
    captions = props.get("captions") or []
    long_ones, short_ones = [], []
    for index, caption in enumerate(captions):
        text = " ".join(word["w"] for word in caption["words"])
        if len(caption["words"]) > 3 or len(text) > CAPTION_MAX_CHARS:
            long_ones.append(text)
        if (caption["to"] - caption["from"]) / fps < CAPTION_MIN_SECONDS and index < len(captions) - 1:
            short_ones.append(text)
    words = sum(len(caption["words"]) for caption in captions)
    seconds = props.get("durationInFrames", 0) / fps
    rate = words / seconds if seconds else 0.0
    passed = (
        not long_ones
        and len(short_ones) <= max(1, len(captions) // 20)
        and rate <= CAPTION_MAX_WORDS_PER_SECOND
    )
    detail = (
        f"{len(captions)} captions, {rate:.1f} words/s; too long: {long_ones[:3] or 'none'}; "
        f"flashes under {CAPTION_MIN_SECONDS}s: {short_ones[:3] or 'none'}."
    )
    return _check("captions_readable", passed, detail)


def storyboard_checks(scenes: list[dict]) -> list[dict]:
    """Checks on the validated storyboard: variety, and how many pictures had to be replaced."""
    templates = [scene.get("template", "") for scene in scenes]
    repeats = [i + 1 for i in range(1, len(templates)) if templates[i] == templates[i - 1]]
    distinct = len(set(templates))
    demoted = [
        f"scene {i + 1}: {scene['demoted'].get('requested') or '?'} -> {scene.get('template')}"
        for i, scene in enumerate(scenes)
        if isinstance(scene.get("demoted"), dict)
        and (
            scene["demoted"].get("requested") != scene.get("template")
            or scene["demoted"].get("unknown_objects")
        )
    ]
    objects = sum(1 for template in templates if template == "object")
    return [
        _check(
            "storyboard_variety",
            not repeats and distinct >= min(4, len(templates)),
            f"{distinct} different templates over {len(templates)} scenes; repeated back to back: "
            f"{repeats or 'none'}.",
            required=False,
        ),
        _check(
            "illustrations_match_narration",
            len(demoted) <= 1 and objects <= max(2, len(templates) // 3),
            "Pictures the validator had to replace (unsourced number, unknown object, missing field): "
            f"{demoted or 'none'}; generic object scenes: {objects}.",
            required=False,
        ),
    ]


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
    streams = stream_durations(video)
    drift = abs(streams.get("video", 0.0) - streams.get("audio", -99.0))
    checks.append(
        _check(
            "audio_video_sync",
            drift <= 0.1,
            f"video stream {streams.get('video', 0):.2f}s, audio stream {streams.get('audio', 0):.2f}s "
            f"(captions and cuts are timed from the voice's own word timings).",
        )
    )
    checks.append(caption_check(props, fps))
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
        "human_review": "required: a person approves every video before it can be published.",
    }
