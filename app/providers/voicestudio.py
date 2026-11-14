"""Optional local VoiceStudio speech. This does not download models or change licenses.

VoiceStudio's application is AGPL-3.0. Its default OmniVoice weights are CC-BY-NC.
Production calls the operator's already running server only after they set
VOICESTUDIO_COMMERCIAL_USE=yes for a voice they are allowed to sell.
A failed call stays failed. It does not fall back to espeak.
"""

from pathlib import Path

import httpx

from app.config import Settings
from app.core.errors import AppError


def synthesize(text: str, *, voice: str, out_path: Path, settings: Settings) -> None:
    base = settings.voicestudio_url.strip().rstrip("/")
    if not base:
        raise AppError("VOICESTUDIO_URL is empty. Nothing was spoken.")
    try:
        response = httpx.post(
            f"{base}/v1/audio/speech",
            json={"model": "tts-1", "voice": voice, "input": text, "response_format": "wav"},
            timeout=180,
        )
    except httpx.HTTPError as exc:
        raise AppError(f"VoiceStudio did not respond. {exc}") from exc
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400 or "json" in content_type or not response.content:
        detail = response.text[:240].replace("\n", " ")
        raise AppError(f"VoiceStudio refused the line ({response.status_code}). {detail}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)
