"""Quality gate. A video can be approved only after every check passes."""

import re
from pathlib import Path

from app.core.files import sha256_file
from app.services.probes import MediaError, black_durations, media_summary, silence_durations

_SRT_END = re.compile(r"-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)")


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": passed, "detail": detail}


def _srt_span(path: Path) -> tuple[int, float]:
    matches = list(_SRT_END.finditer(path.read_text(encoding="utf-8")))
    if not matches:
        return 0, 0.0
    hours, minutes, seconds, millis = (int(part) for part in matches[-1].groups())
    end = hours * 3600 + minutes * 60 + seconds + millis / 1000
    return len(matches), end


def _claims_ok(claims: list[dict]) -> tuple[bool, str]:
    for claim in claims:
        sources = [item for item in (claim.get("sources") or []) if item]
        unverified = bool(claim.get("unverified"))
        verified = bool(claim.get("verified"))
        if verified and unverified:
            return False, "A claim is marked both verified and unverified."
        if verified and not sources:
            return False, "A verified claim is missing a source."
        if not sources and not unverified:
            return False, "A factual claim has no source and is not marked unverified."
    return True, "Every factual claim has a source or is marked unverified."


def evaluate(
    *,
    video_path: Path,
    subtitle_path: Path,
    asset_paths: list[Path],
    claims: list[dict],
    expected_size: tuple[int, int],
    exported_checksums: set[str] | None = None,
    duplicate_of: str | None = None,
    subtitles_burned: bool = True,
    min_duration: float = 1.0,
    black_seconds: float = 0.5,
    silence_seconds: float = 1.5,
) -> dict:
    checks: list[dict] = []
    checksum = sha256_file(video_path) if video_path.is_file() else ""
    if not video_path.is_file():
        checks.append(_check("audio_playable", False, "Video file is missing."))
        return {"passed": False, "checks": checks, "checksum": checksum}
    try:
        summary = media_summary(video_path)
    except MediaError as exc:
        checks.append(_check("audio_playable", False, str(exc)[:300]))
        return {"passed": False, "checks": checks, "checksum": checksum}

    playable = (
        summary["video_codec"] == "h264"
        and summary["audio_codec"] in {"aac", "mp3"}
        and summary["duration"] >= min_duration
    )
    checks.append(
        _check(
            "audio_playable",
            playable,
            (f"h264/{summary['audio_codec'] or 'no audio'}, {summary['duration']:.2f}s"),
        )
    )
    size_ok = (summary["width"], summary["height"]) == expected_size
    checks.append(
        _check(
            "resolution",
            size_ok,
            f"{summary['width']}x{summary['height']}, expected {expected_size[0]}x{expected_size[1]}",
        )
    )
    if not subtitle_path.is_file():
        checks.append(_check("subtitles", False, "Subtitle file is missing."))
    else:
        count, end = _srt_span(subtitle_path)
        covers = count >= 1 and (summary["duration"] - 1.5) <= end <= summary["duration"] + 1.0
        burned = subtitles_burned and covers
        checks.append(
            _check(
                "subtitles",
                burned,
                f"{count} cues ending at {end:.2f}s. Burned in: {subtitles_burned}.",
            )
        )
    missing = [str(path) for path in asset_paths if not path.is_file() or path.stat().st_size <= 0]
    assets_ok = bool(asset_paths) and not missing
    detail = "Assets are present." if assets_ok else f"Missing assets: {missing or 'none recorded'}"
    checks.append(_check("assets", assets_ok, detail))
    try:
        blacks = black_durations(video_path, black_seconds)
        checks.append(
            _check(
                "black_frames",
                not blacks,
                "No long black frames." if not blacks else f"Black segments (s): {blacks}",
            )
        )
    except MediaError as exc:
        checks.append(_check("black_frames", False, str(exc)[:200]))
    try:
        silences = silence_durations(video_path, silence_seconds)
        checks.append(
            _check(
                "silence",
                not silences,
                "No long silence." if not silences else f"Silence segments (s): {silences}",
            )
        )
    except MediaError as exc:
        checks.append(_check("silence", False, str(exc)[:200]))
    exported = exported_checksums or set()
    duplicate = checksum in exported or bool(duplicate_of)
    duplicate_detail = "No exported duplicate."
    if checksum in exported:
        duplicate_detail = "This file matches an already exported video."
    elif duplicate_of:
        duplicate_detail = f"Script version already exported as video {duplicate_of}."
    checks.append(_check("duplicate", not duplicate, duplicate_detail))
    claims_passed, claims_detail = _claims_ok(claims)
    checks.append(_check("factual_claims", claims_passed, claims_detail))
    return {"passed": all(item["passed"] for item in checks), "checks": checks, "checksum": checksum}
