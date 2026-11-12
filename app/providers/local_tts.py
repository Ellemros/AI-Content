"""Local speech: Edge TTS when the network is up, espeak-ng when it is not."""

import asyncio
import subprocess
from pathlib import Path

from app.config import Settings
from app.core.errors import AppError


def espeak_available(settings: Settings) -> bool:
    try:
        proc = subprocess.run(
            [settings.espeak_bin, "--version"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def synthesize_espeak(
    text: str,
    *,
    voice: str,
    stability: float,
    similarity: float,
    out_path: Path,
    settings: Settings,
) -> None:
    speed = int(round(170 - min(max(stability, 0.0), 1.0) * 35))
    pitch = int(round(35 + min(max(similarity, 0.0), 1.0) * 40))
    text_path = out_path.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    command = [
        settings.espeak_bin,
        "-v",
        voice,
        "-s",
        str(speed),
        "-p",
        str(pitch),
        "-f",
        str(text_path),
        "-w",
        str(out_path),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AppError("espeak-ng is not installed. Install it, or set TTS_PROVIDER=edge.") from exc
    finally:
        text_path.unlink(missing_ok=True)
    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size < 44:
        detail = (proc.stderr or proc.stdout or "espeak-ng failed")[-300:]
        raise AppError(detail)


def synthesize_edge(
    text: str,
    *,
    voice: str,
    stability: float,
    similarity: float,
    out_path: Path,
) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise AppError("edge-tts is not installed.") from exc
    rate_delta = int(round((0.5 - min(max(stability, 0.0), 1.0)) * 20))
    pitch_delta = int(round((min(max(similarity, 0.0), 1.0) - 0.75) * 20))
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=f"{rate_delta:+d}%",
        pitch=f"{pitch_delta:+d}Hz",
    )

    async def _save() -> None:
        await communicate.save(str(out_path))

    try:
        asyncio.run(asyncio.wait_for(_save(), timeout=60))
    except Exception as exc:
        raise AppError(f"Edge TTS failed: {exc}") from exc
    if not out_path.is_file() or out_path.stat().st_size < 100:
        raise AppError("Edge TTS wrote no audio.")
