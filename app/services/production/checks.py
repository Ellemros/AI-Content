"""Production gate. A finished encode is not enough."""

from decimal import Decimal
from pathlib import Path

from app.core.files import sha256_file
from app.services.probes import MediaError, media_summary, run_ffmpeg
from app.services.quality import evaluate
from app.services.textutil import tokens


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": passed, "detail": detail}


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
    passed = all(item["passed"] for item in checks)
    return {"passed": passed, "checks": checks, "checksum": technical.get("checksum", "")}
