"""Production gate. A finished encode is not enough."""

from decimal import Decimal
from pathlib import Path

from app.core.files import sha256_file
from app.services.probes import MediaError, media_summary, run_ffmpeg
from app.services.quality import evaluate
from app.services.textutil import tokens


def _check(name: str, passed: bool, detail: str, *, required: bool = True) -> dict:
    return {"name": name, "passed": passed, "detail": detail, "required": required}


def source_relevance(narration: str, extracted: str, title: str) -> tuple[bool, str]:
    shared = tokens(narration) & tokens(extracted)
    title_words = tokens(title)
    if len(shared) < 6:
        return False, f"Script shares {len(shared)} content words with the source."
    if title_words and not (title_words & tokens(narration)):
        return False, "Script does not use the topic title."
    return True, f"Script shares {len(shared)} content words with the source."


def scene_diversity(paths: list[Path]) -> tuple[bool, str]:
    present = [path for path in paths if path.is_file() and path.stat().st_size > 0]
    if len(present) < 4:
        return False, f"Only {len(present)} scene images were saved."
    hashes = {sha256_file(path) for path in present}
    if len(hashes) < 4:
        return False, f"{len(present)} images collapse to {len(hashes)} unique pictures."
    return True, f"{len(hashes)} distinct scene images."


def _subtitle_lines(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "Subtitle file is missing."
    long = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.isdigit() and "-->" not in line and len(line) > 42
    ]
    if long:
        return False, f"A subtitle line is longer than 42 characters: {long[0][:48]}"
    return True, "Subtitle lines fit a vertical frame."


def _frame_hash(video: Path, at: float) -> str:
    dest = video.with_name(f"sample-{int(at * 1000)}.png")
    run_ffmpeg(["-ss", f"{max(at, 0):.3f}", "-i", str(video), "-frames:v", "1", str(dest)])
    digest = sha256_file(dest)
    dest.unlink(missing_ok=True)
    return digest


def motion_differs(video: Path) -> tuple[bool, str]:
    try:
        summary = media_summary(video)
    except MediaError as exc:
        return False, str(exc)[:200]
    if summary["duration"] < 1:
        return False, "Video is too short to compare frames."
    early = _frame_hash(video, min(0.4, summary["duration"] / 4))
    later = _frame_hash(video, summary["duration"] * 0.62)
    if early == later:
        return False, "Frames from two points in the video are identical."
    return True, "Frames from two points in the video differ."


def assess_production(
    *,
    video_path: Path,
    subtitle_path: Path,
    scene_paths: list[Path],
    narration: str,
    extracted: str,
    title: str,
    claims: list[dict],
    cost: Decimal,
    video_cap: Decimal,
    script_mode: str = "dossier",
    voice_provider: str = "",
    visual_kinds: list[str] | None = None,
    clip_ids: list[str] | None = None,
) -> dict:
    technical = evaluate(
        video_path=video_path,
        subtitle_path=subtitle_path,
        asset_paths=scene_paths,
        claims=claims,
        expected_size=(1080, 1920),
        subtitles_burned=True,
        min_duration=4.0,
    )
    checks = list(technical["checks"])
    diverse, diverse_detail = scene_diversity(scene_paths)
    checks.append(_check("scene_diversity", diverse, diverse_detail))
    readable, readable_detail = _subtitle_lines(subtitle_path)
    checks.append(_check("subtitle_readability", readable, readable_detail))
    related, related_detail = source_relevance(narration, extracted, title)
    checks.append(_check("source_relevance", related, related_detail))
    within = cost <= video_cap
    checks.append(
        _check(
            "cost_limit",
            within,
            f"Video cost ${float(cost):.4f} of ${float(video_cap):.2f}.",
        )
    )
    moved, moved_detail = (False, "Video file is missing.")
    if video_path.is_file():
        try:
            summary = media_summary(video_path)
            ratio = summary["width"] / summary["height"] if summary["height"] else 0
            portrait = abs(ratio - (9 / 16)) < 0.02 and summary["height"] > summary["width"]
            checks.append(_check("aspect_ratio", portrait, f"{summary['width']}x{summary['height']}"))
            moved, moved_detail = motion_differs(video_path)
        except MediaError as exc:
            checks.append(_check("aspect_ratio", False, str(exc)[:200]))
    checks.append(_check("visual_motion", moved, moved_detail))
    kinds = visual_kinds or []
    motion_graphics = bool(kinds) and all(kind == "motion" for kind in kinds)
    animated = bool(kinds) and all(kind == "stickman" for kind in kinds)
    clip_count = sum(1 for kind in kinds if kind == "clip")
    missing = sum(1 for kind in kinds if kind not in {"clip", "stickman"})
    unique_ids = {item for item in (clip_ids or []) if item}
    if animated:
        moved_scenes = []
        for path in scene_paths:
            if not path.is_file():
                moved_scenes.append(False)
                continue
            scene_moved, _detail = motion_differs(path)
            moved_scenes.append(scene_moved)
        stickman_ok = bool(moved_scenes) and all(moved_scenes)
        checks.append(
            _check(
                "stickman_motion",
                stickman_ok,
                f"{sum(moved_scenes)} of {len(kinds)} stickman scenes move.",
            )
        )
        footage_ok = stickman_ok and len(kinds) >= 4
        if stickman_ok:
            footage_detail = (
                f"{len(kinds)} stickman scenes were animated from the narration. No stock clip was required."
            )
        else:
            footage_detail = "A stickman scene did not move, so the animation is incomplete."
    else:
        footage_ok = (
            bool(kinds)
            and clip_count == len(kinds)
            and len(unique_ids) == clip_count
            and clip_count >= min(4, len(kinds))
        )
    if motion_graphics:
        footage_ok = True
        footage_detail = f"{len(kinds)} motion-graphics scenes from the storyboard."
    if animated or motion_graphics:
        pass
    elif not kinds:
        footage_detail = "Visual kind was not recorded."
    elif missing:
        footage_detail = (
            f"{clip_count} of {len(kinds)} scenes have a licensed clip. "
            f"{missing} scenes have no relevant licensed footage. "
            "No unrelated clip was inserted."
        )
    elif len(unique_ids) != clip_count:
        footage_detail = "A licensed clip was repeated. That is not full coverage."
    else:
        footage_detail = f"{len(unique_ids)} distinct licensed clips."
    checks.append(_check("footage_coverage", footage_ok, footage_detail, required=False))
    original = script_mode == "original"
    checks.append(
        _check(
            "script_mode",
            original,
            "Original model script." if original else "Fallback script assembled from source sentences.",
            required=False,
        )
    )
    natural = voice_provider in {"kokoro", "elevenlabs", "voicestudio"}
    voice_detail = f"Voice provider: {voice_provider or 'unknown'}."
    if voice_provider == "kokoro":
        voice_detail = "Kokoro-82M neural voice, local, Apache-2.0 weights."
    if voice_provider == "espeak":
        voice_detail = "espeak-ng is a robotic fallback. This video is not production-ready."
    elif voice_provider == "edge":
        voice_detail = "Edge TTS is Microsoft's online read-aloud voice. Its commercial terms are unclear."
    elif voice_provider == "voicestudio":
        voice_detail = "VoiceStudio spoke with a voice the operator marked as commercially usable."
    checks.append(_check("voice_provider", natural, voice_detail, required=False))
    ass_path = subtitle_path.with_suffix(".ass")
    ass_text = ass_path.read_text(encoding="utf-8") if ass_path.is_file() else ""
    safe = "MarginV, Encoding" in ass_text and ",320,1" in ass_text
    checks.append(
        _check(
            "subtitle_safe_area",
            safe,
            "Captions use a 320px bottom margin on a 1920px frame."
            if safe
            else "Caption safe-area style was not found.",
            required=False,
        )
    )
    required = [item for item in checks if item.get("required", True)]
    limits = [item for item in checks if not item.get("required", True)]
    if not all(item["passed"] for item in required):
        status = "failed"
    elif any(not item["passed"] for item in limits):
        status = "needs_review"
    else:
        status = "ready"
    warnings = [item["detail"] for item in checks if not item["passed"]]
    return {
        "passed": status != "failed",
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "semantic_visual": "directed-from-narration" if animated else "unverified",
        "checksum": technical.get("checksum", ""),
    }
