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
    elevenlabs_default_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_model_id: str = ""
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
    # Emergency switch for brand automation. False until a new database is seeded
    # from this value, or until the Production page turns it on. The poller above
    # is the only worker. It does not publish.
    automation_enabled: bool = False

    # Content intelligence. Scores are heuristics. They are not search volume.
    topic_score_minimum: int = 55
    editorial_quality_minimum: int = 70
    editorial_gate: bool = False
    research_timeout_seconds: float = 8.0
    research_max_items: int = 8
    research_verify_limit: int = 8

    # 0 keeps the brand cycle off unless a command or a server sets a poll interval.
    brand_cycle_poll_seconds: int = 0
    youtube_api_key: str = ""
    opportunity_weight_monetization: float = 0.25
    opportunity_weight_demand: float = 0.20
    opportunity_weight_competition: float = 0.15
    opportunity_weight_scalability: float = 0.15
    opportunity_weight_affiliate: float = 0.10
    opportunity_weight_cost: float = 0.10
    opportunity_weight_evergreen: float = 0.05

    # Local production. A zero cap blocks paid calls and still allows the local path.
    production_video_cap_usd: float = 1.0
    production_daily_cap_usd: float = 5.0
    production_stock: str = "none"
    # local uses the licensed library. pexels stays off unless this is set to pexels and a key exists.
    production_footage: str = "local"
    pexels_api_key: str = ""

    video_fps: int = Field(default=30)

    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    def elevenlabs_enabled(self) -> bool:
        return bool(self.elevenlabs_api_key.strip())

    def elevenlabs_model_name(self) -> str:
        chosen = self.elevenlabs_model_id.strip() or self.elevenlabs_model.strip()
        return chosen or "eleven_multilingual_v2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
