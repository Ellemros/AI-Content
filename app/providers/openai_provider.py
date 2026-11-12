"""OpenAI chat completions. Used for topics, scripts, localization, and metadata."""

import json
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.retry import RetryableError, call_with_retry
from app.providers.cache import get_cached, make_cache_key, store_cached
from app.services.cost import assert_within_cap, record_usage


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
    return cleaned.strip()


class OpenAIProvider:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def available(self) -> bool:
        return self.settings.openai_enabled()

    def complete_json(self, *, operation: str, system: str, user: str) -> dict:
        if not self.available():
            raise AppError("OPENAI_API_KEY is empty. The offline draft path does not call OpenAI.")
        key = make_cache_key(
            {
                "provider": "openai",
                "model": self.settings.openai_model,
                "operation": operation,
                "system": system,
                "user": user,
            }
        )
        cached = get_cached(self.session, key)
        if cached is not None:
            record_usage(
                self.session,
                provider="openai",
                operation=operation,
                model=self.settings.openai_model,
                cached=True,
                cost=0,
            )
            return cached["json"]

        def attempt() -> dict:
            estimate = self._estimate(system, user)
            assert_within_cap(self.session, estimate)
            payload, usage = self._post(system, user)
            record_usage(
                self.session,
                provider="openai",
                operation=operation,
                model=self.settings.openai_model,
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                cost=usage["cost"],
            )
            try:
                parsed = json.loads(_strip_fences(payload))
            except json.JSONDecodeError as exc:
                raise RetryableError(f"Model did not return JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise RetryableError("Model JSON was not an object.")
            return parsed

        try:
            parsed = call_with_retry(
                attempt,
                retries=self.settings.provider_retries,
                base_delay=self.settings.provider_retry_base_seconds,
            )
        except RetryableError as exc:
            raise AppError(f"OpenAI did not return a usable script after retries: {exc}") from exc
        store_cached(
            self.session,
            key=key,
            provider="openai",
            operation=operation,
            response={"json": parsed},
        )
        return parsed

    def _estimate(self, system: str, user: str) -> Decimal:
        input_tokens = Decimal(len(system) + len(user)) / Decimal(4)
        output_tokens = Decimal(self.settings.openai_max_output_tokens)
        million = Decimal(1_000_000)
        cost = input_tokens / million * Decimal(str(self.settings.openai_input_cost_per_million))
        cost += output_tokens / million * Decimal(str(self.settings.openai_output_cost_per_million))
        return cost

    def _post(self, system: str, user: str) -> tuple[str, dict]:
        url = self.settings.openai_base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.settings.openai_model,
            "temperature": 0.4,
            "max_tokens": self.settings.openai_max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=60)
        except httpx.HTTPError as exc:
            raise RetryableError(str(exc)) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableError(f"OpenAI HTTP {response.status_code}")
        if response.status_code >= 400:
            raise AppError(f"OpenAI returned HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        if input_tokens == 0:
            input_tokens = int((len(system) + len(user)) / 4)
        million = Decimal(1_000_000)
        cost = Decimal(input_tokens) / million * Decimal(str(self.settings.openai_input_cost_per_million))
        cost += Decimal(output_tokens) / million * Decimal(str(self.settings.openai_output_cost_per_million))
        return content, {"input": input_tokens, "output": output_tokens, "cost": cost}
