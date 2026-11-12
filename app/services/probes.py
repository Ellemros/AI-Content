"""FFmpeg and ffprobe helpers."""

import json
import re
import subprocess
from pathlib import Path

_BLACK = re.compile(r"black_duration:\s*([0-9.]+)")
_SILENCE = re.compile(r"silence_duration:\s*([0-9.]+)")


class MediaError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot read or write media."""


def run_ffmpeg(args: list[str]) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-40:])
        raise MediaError(tail or "ffmpeg failed")
    return proc.stderr or ""


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError((proc.stderr or "ffprobe failed")[-400:])
    return json.loads(proc.stdout or "{}")


def media_summary(path: Path) -> dict:
    data = probe(path)
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    return {
        "duration": duration,
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "video_codec": (video or {}).get("codec_name") or "",
        "audio_codec": (audio or {}).get("codec_name") or "",
    }


def black_durations(path: Path, minimum: float) -> list[float]:
    stderr = run_ffmpeg(
        ["-i", str(path), "-vf", f"blackdetect=d={minimum}:pix_th=0.10", "-an", "-f", "null", "-"]
    )
    return [float(item) for item in _BLACK.findall(stderr)]


def silence_durations(path: Path, minimum: float) -> list[float]:
    stderr = run_ffmpeg(
        [
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise=-35dB:d={minimum}",
            "-f",
            "null",
            "-",
        ]
    )
    return [float(item) for item in _SILENCE.findall(stderr)]
