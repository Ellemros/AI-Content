"""PostgreSQL-compatible tables. String ids and JSON keep SQLite and Postgres aligned."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.time import utc_now


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    default_language: Mapped[str] = mapped_column(String(8), default="en")
    default_platform: Mapped[str] = mapped_column(String(32), default="youtube_shorts")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32))
    target_language: Mapped[str] = mapped_column(String(8))
    target_platform: Mapped[str] = mapped_column(String(32))
    target_audience: Mapped[str] = mapped_column(String(160), default="curious adults")
    status: Mapped[str] = mapped_column(String(32), default="Idea")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    estimated_content_potential: Mapped[int] = mapped_column(Integer, default=5)
    source_references: Mapped[list] = mapped_column(JSON, default=list)
    research_references: Mapped[list] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Script(Base):
    __tablename__ = "scripts"
    __table_args__ = (UniqueConstraint("topic_id", "language", "format", name="uq_script_topic_lang_format"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), index=True)
    language: Mapped[str] = mapped_column(String(8))
    format: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("script_id", "version_number", name="uq_script_version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    hook: Mapped[str] = mapped_column(Text)
    narration: Mapped[str] = mapped_column(Text)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    visual_instructions: Mapped[str] = mapped_column(Text, default="")
    music_mood: Mapped[str] = mapped_column(String(40), default="curious")
    cta: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail_text: Mapped[str] = mapped_column(String(80), default="")
    factual_claims: Mapped[list] = mapped_column(JSON, default=list)
    offline_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32), default="offline")
    template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(160))
    license: Mapped[str] = mapped_column(String(80))
    checksum: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    library: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AudioFile(Base):
    __tablename__ = "audio_files"
    __table_args__ = (
        UniqueConstraint("script_version_id", "settings_hash", name="uq_audio_version_settings"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"), index=True)
    language: Mapped[str] = mapped_column(String(8))
    voice_id: Mapped[str] = mapped_column(String(120))
    stability: Mapped[float] = mapped_column(default=0.5)
    similarity: Mapped[float] = mapped_column(default=0.75)
    provider: Mapped[str] = mapped_column(String(32))
    planned_engine: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    settings_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"), index=True)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"), index=True)
    audio_file_id: Mapped[str | None] = mapped_column(ForeignKey("audio_files.id"), nullable=True)
    language: Mapped[str] = mapped_column(String(8))
    format: Mapped[str] = mapped_column(String(16))
    profile: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text, default="")
    subtitle_path: Mapped[str] = mapped_column(Text, default="")
    thumbnail_path: Mapped[str] = mapped_column(Text, default="")
    work_dir: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="rendering")
    width: Mapped[int] = mapped_column(Integer, default=1080)
    height: Mapped[int] = mapped_column(Integer, default=1920)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    voice_id: Mapped[str] = mapped_column(String(120), default="")
    stability: Mapped[float] = mapped_column(default=0.5)
    similarity: Mapped[float] = mapped_column(default=0.75)
    tts_provider: Mapped[str] = mapped_column(String(32), default="")
    settings_hash: Mapped[str] = mapped_column(String(64), default="")
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    subtitles_burned: Mapped[bool] = mapped_column(Boolean, default=False)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    master_path: Mapped[str] = mapped_column(Text, default="")
    subtitle_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    quality_report: Mapped[dict] = mapped_column(JSON, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PublishingJob(Base):
    __tablename__ = "publishing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    export_path: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublishedPost(Base):
    __tablename__ = "published_posts"
    __table_args__ = (UniqueConstraint("video_id", "platform", name="uq_published_video_platform"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    publishing_job_id: Mapped[str] = mapped_column(ForeignKey("publishing_jobs.id"))
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    local_export_path: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Analytics(Base):
    __tablename__ = "analytics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("videos.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="")
    metric: Mapped[str] = mapped_column(String(80), default="")
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    note: Mapped[str] = mapped_column(Text, default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class APIUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (Index("ix_api_usage_month", "month_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    month_key: Mapped[str] = mapped_column(String(7))
    video_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    script_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ContentTemplate(Base):
    __tablename__ = "content_templates"
    __table_args__ = (
        UniqueConstraint("template_key", "language", "format", name="uq_template_key_lang_format"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    template_key: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(8))
    format: Mapped[str] = mapped_column(String(16))
    niche: Mapped[str] = mapped_column(String(80), default="ai-tech-science")
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Job(Base):
    """Internal idempotency row. Interrupted renders are retried from this table."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default="running")
    video_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    component: Mapped[str] = mapped_column(String(32), default="all")
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ProductionSchedule(Base):
    """A repeating topic → script → voice → render. It never approves or exports."""

    __tablename__ = "production_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="en")
    video_format: Mapped[str] = mapped_column(String(16), default="short")
    stability: Mapped[float] = mapped_column(default=0.5)
    similarity: Mapped[float] = mapped_column(default=0.75)
    voice_id: Mapped[str] = mapped_column(String(120), default="")
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="idle")
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_video_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class JobEvent(Base):
    """One production step, failure, or retry. Shown on the video page."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job", "job_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeFlag(Base):
    """Dashboard switches. The scheduler poller reads scheduler_enabled from here."""

    __tablename__ = "runtime_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AIResponseCache(Base):
    __tablename__ = "ai_response_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
