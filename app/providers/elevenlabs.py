"""ElevenLabs speech. Local engines stay available when this key is empty."""

from decimal import Decimal
from pathlib import Path

import httpx

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.retry import RetryableError, call_with_retry
from app.services.cost import assert_within_cap, record_usage


def synthesize(
    session,
    *,
    text: str,
    voice_id: str,
    stability: float,
    similarity: float,
    out_path: Path,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    if not settings.elevenlabs_enabled():
        raise AppError("ELEVENLABS_API_KEY is empty.")
    if not voice_id.strip():
        raise AppError("Set ELEVENLABS_VOICE_ID_EN and ELEVENLABS_VOICE_ID_CS, or pass a voice id.")
    characters = len(text)
    estimate = Decimal(characters) / Decimal(1000) * Decimal(str(settings.elevenlabs_cost_per_1k_chars))

    def attempt() -> None:
        assert_within_cap(session, estimate)
        url = settings.elevenlabs_base_url.rstrip("/") + f"/text-to-speech/{voice_id.strip()}"
        body = {
            "text": text,
            "model_id": settings.elevenlabs_model,
            "voice_settings": {"stability": stability, "similarity_boost": similarity},
        }
        headers = {"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"}
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=90)
        except httpx.HTTPError as exc:
            raise RetryableError(str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableError(f"ElevenLabs HTTP {response.status_code}. Rate limit or outage.")
        if response.status_code >= 400:
            raise AppError(f"ElevenLabs returned HTTP {response.status_code}: {response.text[:300]}")
        out_path.write_bytes(response.content)
        record_usage(
            session,
            provider="elevenlabs",
            operation="text-to-speech",
            model=settings.elevenlabs_model,
            characters=characters,
            cost=estimate,
        )

    try:
        call_with_retry(
            attempt,
            retries=settings.provider_retries,
            base_delay=settings.provider_retry_base_seconds,
        )
    except RetryableError as exc:
        raise AppError(
            f"ElevenLabs did not finish after retries ({exc}). Wait and retry the voice step."
        ) from exc
