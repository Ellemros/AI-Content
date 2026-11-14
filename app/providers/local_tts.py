"""Local speech: Kokoro on this machine, Edge TTS over the network, espeak-ng as the last resort."""

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


# Kokoro-82M: Apache-2.0 weights, local CPU inference through kokoro-onnx (MIT).
# Each sentence is synthesized on its own, so sentence boundaries have exact times.
_KOKORO = None
_KOKORO_PAUSE = 0.32
_KOKORO_RATE = 24000


def kokoro_available(settings: Settings) -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return Path(settings.kokoro_model_path).is_file() and Path(settings.kokoro_voices_path).is_file()


def _kokoro(settings: Settings):
    global _KOKORO
    if _KOKORO is None:
        from kokoro_onnx import Kokoro

        _KOKORO = Kokoro(str(settings.kokoro_model_path), str(settings.kokoro_voices_path))
    return _KOKORO


def _trim_silence(samples, rate: int, threshold: float = 0.012, pad: float = 0.04):
    import numpy as np

    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples[:0]
    start = max(int(loud[0] - pad * rate), 0)
    end = min(int(loud[-1] + pad * rate), len(samples))
    return samples[start:end]


def synthesize_kokoro(text: str, *, voice: str, out_path: Path, settings: Settings) -> dict:
    """Write a 24 kHz mono WAV and a character alignment next to it. Returns the alignment."""
    import json
    import re

    import numpy as np
    import soundfile

    try:
        engine = _kokoro(settings)
    except Exception as exc:  # model file missing or corrupt
        raise AppError(f"Kokoro could not load: {exc}") from exc
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if part.strip()]
    if not sentences:
        raise AppError("Narration is empty.")
    pause = np.zeros(int(_KOKORO_PAUSE * _KOKORO_RATE), dtype=np.float32)
    pieces = []
    chars: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    segments: list[dict] = []
    cursor = 0.0
    for index, sentence in enumerate(sentences):
        try:
            samples, rate = engine.create(
                sentence, voice=voice, speed=float(settings.kokoro_speed), lang="en-us"
            )
        except Exception as exc:
            raise AppError(f"Kokoro failed on a sentence: {exc}") from exc
        if rate != _KOKORO_RATE:
            raise AppError(f"Kokoro returned an unexpected sample rate {rate}.")
        samples = _trim_silence(np.asarray(samples, dtype=np.float32), rate)
        length = len(samples) / rate
        if length < 0.2:
            raise AppError(f"Kokoro produced no audio for: {sentence[:80]}")
        if index:
            pieces.append(pause)
            chars.append(" ")
            starts.append(cursor)
            ends.append(cursor + _KOKORO_PAUSE)
            cursor += _KOKORO_PAUSE
        pieces.append(samples)
        # Spread characters across the sentence by word length. Sentence edges are exact.
        weights = [1.0 if ch != " " else 0.6 for ch in sentence]
        total = sum(weights)
        position = cursor
        for ch, weight in zip(sentence, weights, strict=True):
            step = length * weight / total
            chars.append(ch)
            starts.append(round(position, 4))
            ends.append(round(position + step, 4))
            position += step
        segments.append({"text": sentence, "start": round(cursor, 4), "end": round(cursor + length, 4)})
        cursor += length
    audio = np.concatenate(pieces)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(out_path), audio, _KOKORO_RATE, subtype="PCM_16")
    alignment = {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
        "segments": segments,
        "timing": "sentence-exact",
    }
    out_path.with_name(out_path.stem + ".alignment.json").write_text(json.dumps(alignment), encoding="utf-8")
    return alignment
