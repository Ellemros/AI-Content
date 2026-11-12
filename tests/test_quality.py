import subprocess
from pathlib import Path

from app.services.quality import evaluate


def _media(path: Path, *, color: str, size: str, duration: float, silent: bool) -> None:
    audio = (
        f"anullsrc=r=48000:cl=mono:d={duration}"
        if silent
        else f"sine=frequency=440:sample_rate=48000:duration={duration}"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:d={duration}:r=30",
            "-f",
            "lavfi",
            "-i",
            audio,
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _srt(path: Path, end: str = "00:00:02,000") -> None:
    path.write_text(f"1\n00:00:00,000 --> {end}\nHello from the test.\n", encoding="utf-8")


def _asset(path: Path) -> None:
    path.write_bytes(b"asset")


def _evaluate(tmp: Path, **kwargs):
    video = tmp / "video.mp4"
    srt = tmp / "captions.srt"
    asset = tmp / "card.png"
    if not video.exists():
        _media(video, color="0x243044", size="1080x1920", duration=2, silent=False)
    if not srt.exists():
        _srt(srt)
    if not asset.exists():
        _asset(asset)
    defaults = {
        "video_path": video,
        "subtitle_path": srt,
        "asset_paths": [asset],
        "claims": [{"text": "Unverified draft.", "sources": [], "unverified": True, "verified": False}],
        "expected_size": (1080, 1920),
        "subtitles_burned": True,
    }
    defaults.update(kwargs)
    return evaluate(**defaults)


def test_good_short_passes(tmp_path):
    report = _evaluate(tmp_path)
    failed = [item["name"] for item in report["checks"] if not item["passed"]]
    assert failed == []
    assert report["passed"] is True


def test_long_black_frames_fail(tmp_path):
    _media(tmp_path / "video.mp4", color="black", size="1080x1920", duration=2, silent=False)
    report = _evaluate(tmp_path)
    black = next(item for item in report["checks"] if item["name"] == "black_frames")
    assert black["passed"] is False
    assert report["passed"] is False


def test_long_silence_fails(tmp_path):
    _media(tmp_path / "video.mp4", color="0x243044", size="1080x1920", duration=2, silent=True)
    report = _evaluate(tmp_path)
    silence = next(item for item in report["checks"] if item["name"] == "silence")
    assert silence["passed"] is False


def test_missing_asset_fails(tmp_path):
    report = _evaluate(tmp_path, asset_paths=[tmp_path / "missing.png"])
    assets = next(item for item in report["checks"] if item["name"] == "assets")
    assert assets["passed"] is False


def test_duplicate_export_fails(tmp_path):
    report = _evaluate(tmp_path, duplicate_of="already-exported")
    duplicate = next(item for item in report["checks"] if item["name"] == "duplicate")
    assert duplicate["passed"] is False


def test_unsourced_claim_fails_the_gate(tmp_path):
    report = _evaluate(
        tmp_path,
        claims=[{"text": "A made-up fact.", "sources": [], "unverified": False, "verified": False}],
    )
    claims = next(item for item in report["checks"] if item["name"] == "factual_claims")
    assert claims["passed"] is False


def test_wrong_resolution_fails(tmp_path):
    report = _evaluate(tmp_path, expected_size=(1920, 1080))
    resolution = next(item for item in report["checks"] if item["name"] == "resolution")
    assert resolution["passed"] is False
