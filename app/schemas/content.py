"""Pydantic models for scripts, topics, and API payloads."""

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain import CATEGORIES, FORMATS, LANGUAGES, PLATFORMS, TOPIC_STATUSES
from app.services.textutil import estimate_seconds, words

_HASHTAG = re.compile(r"^#[\w]+$", re.UNICODE)


class Reference(BaseModel):
    title: str = ""
    url: str = ""
    note: str = ""

    @field_validator("url")
    @classmethod
    def url_is_http(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Reference URLs must start with http:// or https://.")
        return value


class FactualClaim(BaseModel):
    text: str = Field(min_length=1)
    sources: list[str] = Field(default_factory=list)
    unverified: bool = False
    verified: bool = False

    @model_validator(mode="after")
    def sourced_or_explicitly_unverified(self) -> "FactualClaim":
        if self.verified and self.unverified:
            raise ValueError("A claim cannot be both verified and unverified.")
        if self.verified and not self.sources:
            raise ValueError("A verified claim needs at least one source.")
        if not self.sources and not self.unverified:
            raise ValueError("A factual claim needs a source or an explicit unverified mark.")
        for source in self.sources:
            if not source.startswith(("http://", "https://")):
                raise ValueError("Claim sources must be http(s) URLs.")
        return self


class Scene(BaseModel):
    index: int = Field(ge=1)
    narration: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    on_screen_text: str = Field(min_length=1, max_length=80)
    asset_id: str | None = None
    music_asset_id: str | None = None


class ScriptContent(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    hook: str = Field(min_length=1)
    narration: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    visual_instructions: str = Field(min_length=1)
    music_mood: str = Field(min_length=1, max_length=40)
    cta: str = Field(min_length=8)
    description: str = Field(min_length=1, max_length=500)
    hashtags: list[str] = Field(min_length=3, max_length=12)
    thumbnail_text: str = Field(min_length=1, max_length=40)
    factual_claims: list[FactualClaim] = Field(default_factory=list)
    offline_draft: bool = False
    format: str
    language: str
    template_key: str | None = None
    provider: str = "offline"

    @field_validator("format")
    @classmethod
    def known_format(cls, value: str) -> str:
        if value not in FORMATS:
            raise ValueError(f"Format must be one of {FORMATS}.")
        return value

    @field_validator("language")
    @classmethod
    def known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError("Language must be en or cs.")
        return value

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in value:
            item = tag.strip().replace(" ", "")
            if not item.startswith("#"):
                item = f"#{item}"
            if not _HASHTAG.match(item):
                raise ValueError(f"Hashtag is not usable: {tag}")
            cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def structure_matches_format(self) -> "ScriptContent":
        if not self.narration.startswith(self.hook):
            raise ValueError("Narration must open with the hook.")
        hook_seconds = estimate_seconds(self.hook, self.language)
        if not 1 <= hook_seconds <= 3:
            raise ValueError(
                f"Hook must land between 1 and 3 seconds. Estimate is {hook_seconds:.2f}s "
                f"({len(words(self.hook))} words)."
            )
        narration_seconds = estimate_seconds(self.narration, self.language)
        low, high = (30, 60) if self.format == "short" else (5 * 60, 10 * 60)
        if not low <= narration_seconds <= high:
            raise ValueError(
                f"{self.format} narration estimate is {narration_seconds:.0f}s. Expected {low}-{high}s."
            )
        minimum_scenes = 3 if self.format == "short" else 6
        if len(self.scenes) < minimum_scenes:
            raise ValueError(f"{self.format} scripts need at least {minimum_scenes} scenes.")
        joined = " ".join(scene.narration.strip() for scene in self.scenes)
        if " ".join(joined.split()) != " ".join(self.narration.split()):
            raise ValueError("Scene narrations must add up to the full narration.")
        return self


class TopicCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = ""
    category: str = "ai"
    target_language: str = "en"
    target_platform: str = "youtube_shorts"
    target_audience: str = "curious adults"
    status: str = "Idea"
    priority: int = Field(default=3, ge=1, le=5)
    estimated_content_potential: int = Field(default=5, ge=1, le=10)
    source_references: list[Reference] = Field(default_factory=list)
    research_references: list[Reference] = Field(default_factory=list)
    channel_id: str | None = None

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"Category must be one of {CATEGORIES}.")
        return value

    @field_validator("target_language")
    @classmethod
    def known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError("Language must be en or cs.")
        return value

    @field_validator("target_platform")
    @classmethod
    def known_platform(cls, value: str) -> str:
        if value not in PLATFORMS:
            raise ValueError(f"Platform must be one of {PLATFORMS}.")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in TOPIC_STATUSES:
            raise ValueError(f"Status must be one of {TOPIC_STATUSES}.")
        return value


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = None
    category: str | None = None
    target_language: str | None = None
    target_platform: str | None = None
    target_audience: str | None = None
    status: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    estimated_content_potential: int | None = Field(default=None, ge=1, le=10)
    source_references: list[Reference] | None = None
    research_references: list[Reference] | None = None


class ScriptGenerateRequest(BaseModel):
    topic_id: str
    language: str | None = None
    format: str = "short"


class ScriptEditRequest(BaseModel):
    title: str
    hook: str
    narration: str
    visual_instructions: str
    music_mood: str
    cta: str
    description: str
    hashtags: list[str]
    thumbnail_text: str
    factual_claims: list[FactualClaim] = Field(default_factory=list)
    scenes: list[Scene] | None = None


class VoiceRequest(BaseModel):
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity: float = Field(default=0.75, ge=0, le=1)
    voice_id: str = ""


class VideoGenerateRequest(VoiceRequest):
    script_id: str


class VideoRegenerateRequest(BaseModel):
    component: str = "all"


class PublishRequest(BaseModel):
    video_id: str
    platform: str = "youtube_shorts"
    upload: bool = False
