"""Google Gemini (generateContent REST API) for the cheap model steps.

Used for topic ranking, hook choice, fact checking, and story review. The script writer stays
on OpenAI, so the fact checker is a different model family from the writer.

Free tier: Google's terms say prompts on the free tier may be used to improve Google's products,
and the free tier may not be used to serve users in the EU/EEA, UK, or Switzerland. Enabling
billing on the Google Cloud project removes both. Prices default to 0 (free tier); set
GEMINI_INPUT_COST_PER_MILLION / GEMINI_OUTPUT_COST_PER_MILLION when billing is on.

When the free quota runs out (HTTP 429), the provider marks itself exhausted for a while and
callers fall back to OpenAI. It never retries a quota error in a loop.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.providers.cache import get_cached, make_cache_key, store_cached
from app.services.cost import assert_within_cap, record_usage

API = "https://generativelanguage.googleapis.com/v1beta/models"
_exhausted_until = 0.0


class GeminiUnavailable(AppError):
    """Gemini cannot answer now (no key, quota exhausted, server error, unusable reply)."""


def exhausted() -> bool:
    return time.monotonic() < _exhausted_until


def _mark_exhausted(seconds: float) -> None:
    global _exhausted_until
    _exhausted_until = time.monotonic() + seconds


def reset_exhaustion() -> None:
    global _exhausted_until
    _exhausted_until = 0.0


class GeminiProvider:
    def __init__(self, session: Session, settings: Settings | None = None, model: str = "") -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.model = model or self.settings.gemini_model

    def available(self) -> bool:
        return bool(self.settings.gemini_api_key) and not exhausted()

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        million = Decimal(1_000_000)
        return Decimal(input_tokens) / million * Decimal(
            str(self.settings.gemini_input_cost_per_million)
        ) + Decimal(output_tokens) / million * Decimal(str(self.settings.gemini_output_cost_per_million))

    def _estimate(self, system: str, user: str) -> Decimal:
        return self._cost(int((len(system) + len(user)) / 4), 8192)

    def complete_json(self, *, operation: str, system: str, user: str) -> dict:
        if not self.settings.gemini_api_key:
            raise GeminiUnavailable("GEMINI_API_KEY is empty.")
        if exhausted():
            raise GeminiUnavailable("Gemini quota is exhausted for now.")
        key = make_cache_key(
            {
                "provider": "gemini",
                "model": self.model,
                "operation": operation,
                "system": system,
                "user": user,
            }
        )
        cached = get_cached(self.session, key)
        if cached is not None:
            record_usage(self.session, provider="gemini", operation=operation, model=self.model, cached=True)
            return cached["json"]
        assert_within_cap(self.session, self._estimate(system, user))
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 8192},
        }
        try:
            response = httpx.post(
                f"{API}/{self.model}:generateContent",
                headers={"x-goog-api-key": self.settings.gemini_api_key},
                json=body,
                timeout=90,
            )
        except httpx.HTTPError as exc:
            raise GeminiUnavailable(f"Gemini request failed: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            # Quota: stop using Gemini for a while instead of hammering it; callers use OpenAI meanwhile.
            _mark_exhausted(self.settings.gemini_cooldown_seconds)
            raise GeminiUnavailable("Gemini quota exhausted (HTTP 429). Falling back.")
        if response.status_code >= 400:
            raise GeminiUnavailable(f"Gemini returned HTTP {response.status_code}.")
        data = response.json()
        usage = data.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        record_usage(
            self.session,
            provider="gemini",
            operation=operation,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=self._cost(input_tokens, output_tokens),
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiUnavailable("Gemini did not return usable JSON.") from exc
        if not isinstance(parsed, dict):
            raise GeminiUnavailable("Gemini JSON was not an object.")
        store_cached(self.session, key=key, provider="gemini", operation=operation, response={"json": parsed})
        return parsed
