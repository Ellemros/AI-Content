"""Voice selection, local or hosted, with skip-if-unchanged."""

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.models import AudioFile, ScriptVersion
from app.providers import elevenlabs as elevenlabs_provider
from app.providers.local_tts import (
    edge_available,
    espeak_available,
    synthesize_edge,
    synthesize_espeak,
)
from app.services.probes import media_summary, run_ffmpeg

logger = logging.getLogger(__name__)


def plan_engine(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    choice = (settings.tts_provider or "auto").strip().lower()
    if choice == "auto":
        if settings.elevenlabs_enabled():
            return "elevenlabs"
        if edge_available():
            return "edge"
        if espeak_available(settings):
            return "espeak"
        raise AppError(
            "No speech engine is available. Install espeak-ng or edge-tts, or set ELEVENLABS_API_KEY."
        )
    if choice not in {"elevenlabs", "edge", "espeak", "voicestudio"}:
        raise AppError("TTS_PROVIDER must be auto, elevenlabs, edge, espeak, or voicestudio.")
    if choice == "elevenlabs" and not settings.elevenlabs_enabled():
        raise AppError("TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY.")
    if choice == "voicestudio":
        if settings.voicestudio_commercial_use.strip().lower() != "yes":
            raise AppError(
                "VoiceStudio stays off until VOICESTUDIO_COMMERCIAL_USE=yes. "
                "The default OmniVoice weights are CC-BY-NC and are not cleared for this brand."
            )
        if not settings.voicestudio_url.strip() or not settings.voicestudio_voice_id.strip():
            raise AppError("VoiceStudio needs VOICESTUDIO_URL and VOICESTUDIO_VOICE_ID.")
        return "voicestudio"
    if choice == "espeak" and not espeak_available(settings):
        raise AppError("espeak-ng is not installed.")
    if choice == "edge" and not edge_available():
        raise AppError("edge-tts is not installed.")
    return choice


def default_voice(settings: Settings, engine: str, language: str) -> str:
    if engine == "elevenlabs":
        if settings.elevenlabs_default_voice_id.strip():
            return settings.elevenlabs_default_voice_id.strip()
        voice = settings.elevenlabs_voice_id_cs if language == "cs" else settings.elevenlabs_voice_id_en
        if not voice.strip():
            raise AppError(
                "Set ELEVENLABS_DEFAULT_VOICE_ID, or ELEVENLABS_VOICE_ID_EN and ELEVENLABS_VOICE_ID_CS."
            )
        return voice.strip()
    if engine == "voicestudio":
        voice = settings.voicestudio_voice_id.strip()
        if not voice:
            raise AppError("Set VOICESTUDIO_VOICE_ID to a saved VoiceStudio profile.")
        return voice
    if engine == "edge":
        return settings.edge_voice_cs if language == "cs" else settings.edge_voice_en
    return settings.espeak_voice_cs if language == "cs" else settings.espeak_voice_en


def voice_hash(engine: str, voice: str, language: str, stability: float, similarity: float) -> str:
    payload = json.dumps(
        {
            "engine": engine,
            "voice": voice,
            "language": language,
            "stability": round(float(stability), 2),
            "similarity": round(float(similarity), 2),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trim(src: Path, dest: Path) -> None:
    trimmed = dest.with_name(dest.stem + "-trim.wav")
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-af",
            (
                "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB,"
                "areverse,silenceremove=start_periods=1:start_duration=0.05:start_threshold=-40dB,"
                "areverse"
            ),
            "-ar",
            "48000",
            "-ac",
            "1",
            str(trimmed),
        ]
    )
    trimmed.replace(dest)
    if src.resolve() != dest.resolve():
        src.unlink(missing_ok=True)


def _read_alignment(raw: Path) -> dict | None:
    path = raw.with_name(raw.stem + ".alignment.json")
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def speak(
    session: Session,
    text: str,
    *,
    engine: str,
    voice: str,
    language: str,
    stability: float,
    similarity: float,
    raw_path: Path,
    settings: Settings,
) -> str:
    """Synthesize into raw_path. Returns the engine that actually spoke."""
    from app.services.production.speech import speakable

    text = speakable(text)
    if engine == "elevenlabs":
        elevenlabs_provider.synthesize(
            session,
            text=text,
            voice_id=voice,
            stability=stability,
            similarity=similarity,
            out_path=raw_path,
            settings=settings,
        )
        return "elevenlabs"
    if engine == "voicestudio":
        from app.providers.voicestudio import synthesize as synthesize_voicestudio

        synthesize_voicestudio(text, voice=voice, out_path=raw_path, settings=settings)
        return "voicestudio"
    if engine == "edge":
        try:
            synthesize_edge(
                text,
                voice=voice,
                stability=stability,
                similarity=similarity,
                out_path=raw_path,
            )
            return "edge"
        except AppError as exc:
            if settings.tts_provider.strip().lower() != "auto":
                raise
            logger.warning("edge tts failed, using espeak-ng", extra={"error": str(exc)[:300]})
            wav = raw_path.with_suffix(".wav")
            synthesize_espeak(
                text,
                voice=default_voice(settings, "espeak", language),
                stability=stability,
                similarity=similarity,
                out_path=wav,
                settings=settings,
            )
            if wav != raw_path:
                wav.replace(raw_path)
            return "espeak"
    synthesize_espeak(
        text,
        voice=voice,
        stability=stability,
        similarity=similarity,
        out_path=raw_path,
        settings=settings,
    )
    return "espeak"


def ensure_audio(
    session: Session,
    version: ScriptVersion,
    *,
    language: str,
    stability: float,
    similarity: float,
    voice_id: str = "",
    engine: str | None = None,
    force: bool = False,
    synthesizer: Callable[..., dict] | None = None,
) -> AudioFile:
    settings = get_settings()
    planned = engine or plan_engine(settings)
    voice = voice_id.strip() or default_voice(settings, planned, language)
    digest = voice_hash(planned, voice, language, stability, similarity)
    existing = session.scalar(
        select(AudioFile).where(
            AudioFile.script_version_id == version.id,
            AudioFile.settings_hash == digest,
        )
    )
    if existing is not None and not force and Path(existing.path).is_file():
        return existing

    directory = settings.data_dir / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    final_path = directory / f"{version.id}-{digest[:12]}.wav"
    if synthesizer is not None:
        meta = synthesizer(
            version.narration,
            final_path,
            {"voice": voice, "stability": stability, "similarity": similarity, "engine": planned},
        )
        actual = str(meta.get("provider") or planned)
        duration = float(meta["duration"])
        alignment = meta.get("alignment") if isinstance(meta.get("alignment"), dict) else None
    else:
        raw = directory / f"{version.id}-{digest[:12]}-raw.mp3"
        if planned in {"espeak", "voicestudio"}:
            raw = raw.with_suffix(".wav")
        actual = speak(
            session,
            version.narration,
            engine=planned,
            voice=voice,
            language=language,
            stability=stability,
            similarity=similarity,
            raw_path=raw,
            settings=settings,
        )
        alignment = _read_alignment(raw)
        _trim(raw, final_path)
        duration = media_summary(final_path)["duration"]
    if duration < 0.8:
        raise AppError("Speech engine produced no usable audio.")
    metadata = {
        "planned_engine": planned,
        "actual_engine": actual,
        "stability": stability,
        "similarity": similarity,
        "similarity_effect": "elevenlabs" if actual == "elevenlabs" else "local-approximation",
        "subtitle_timing": "character" if alignment else "phrase",
    }
    if alignment:
        metadata["alignment"] = alignment
    if existing is None:
        existing = AudioFile(
            script_version_id=version.id,
            language=language,
            voice_id=voice,
            stability=stability,
            similarity=similarity,
            provider=actual,
            planned_engine=planned,
            path=str(final_path),
            duration_seconds=duration,
            settings_hash=digest,
            metadata_json=metadata,
        )
        session.add(existing)
    else:
        existing.provider = actual
        existing.path = str(final_path)
        existing.duration_seconds = duration
        existing.voice_id = voice
        existing.metadata_json = metadata
    session.flush()
    return existing
