"""Runtime configuration. Values come from the environment and .env only."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Content Factory"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/app.db"
    data_dir: Path = Path("./data")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_input_cost_per_million: float = 0.15
    openai_output_cost_per_million: float = 0.60
    openai_max_output_tokens: int = 2500

    elevenlabs_api_key: str = ""
    elevenlabs_base_url: str = "https://api.elevenlabs.io/v1"
    elevenlabs_voice_id_en: str = ""
    elevenlabs_voice_id_cs: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_cost_per_1k_chars: float = 0.30

    tts_provider: str = "auto"
    edge_voice_en: str = "en-US-AriaNeural"
    edge_voice_cs: str = "cs-CZ-VlastaNeural"
    espeak_bin: str = "espeak-ng"
    espeak_voice_en: str = "en-us"
    espeak_voice_cs: str = "cs"

    monthly_spend_cap_usd: float = 25.0
    provider_retries: int = 3
    provider_retry_base_seconds: float = 0.5
    near_duplicate_threshold: float = 0.82
    font_path: str = ""

    # The poller starts with the process. It only produces when the dashboard
    # switch (seeded from this value) is on. Scheduled runs never approve or export.
    scheduler_enabled: bool = False
    scheduler_poll_seconds: float = 30.0

    video_fps: int = Field(default=30)

    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    def elevenlabs_enabled(self) -> bool:
        return bool(self.elevenlabs_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
