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


class AIResponseCache(Base):
    __tablename__ = "ai_response_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchSource(Base):
    """A configurable public feed. Discovery reads these. It does not render video."""

    __tablename__ = "research_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32), default="rss")
    url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="ai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    etag: Mapped[str] = mapped_column(String(200), default="")
    last_modified: Mapped[str] = mapped_column(String(120), default="")
    body_sha256: Mapped[str] = mapped_column(String(64), default="")
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="idle")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveredItem(Base):
    """A topic candidate. The feed summary is stored as a lead, not as a verified fact."""

    __tablename__ = "discovered_items"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_discovery_source_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("research_sources.id"))
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_kind: Mapped[str] = mapped_column(String(40), default="feed_summary")
    url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="ai")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    audience: Mapped[str] = mapped_column(String(160), default="")
    horizon: Mapped[str] = mapped_column(String(16), default="unknown")
    monetization_note: Mapped[str] = mapped_column(Text, default="")
    source_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_status: Mapped[str] = mapped_column(String(40), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    duplicate_of_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EditorialReview(Base):
    """Editorial plan and quality score for one script version."""

    __tablename__ = "editorial_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("scripts.id"), nullable=True, index=True)
    script_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audience: Mapped[str] = mapped_column(Text, default="")
    central_question: Mapped[str] = mapped_column(Text, default="")
    takeaway: Mapped[str] = mapped_column(Text, default="")
    opening: Mapped[str] = mapped_column(Text, default="")
    narrative: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    quality_score: Mapped[int] = mapped_column(Integer, default=0)
    originality_score: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    threshold: Mapped[int] = mapped_column(Integer, default=70)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Brand(Base):
    """An independent content brand. The niche lives on the row, not in application code."""

    __tablename__ = "brands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    niche: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    identity: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="experimental")
    publishing_mode: Mapped[str] = mapped_column(String(32), default="test")
    daily_publish_limit: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class BrandSource(Base):
    __tablename__ = "brand_sources"
    __table_args__ = (UniqueConstraint("brand_id", "source_id", name="uq_brand_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("research_sources.id"))


class ContentOpportunity(Base):
    """A scored idea. Missing factors stay null. The score is not rescaled to hide them."""

    __tablename__ = "content_opportunities"
    __table_args__ = (
        UniqueConstraint("brand_id", "discovered_item_id", name="uq_brand_discovered_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    discovered_item_id: Mapped[str | None] = mapped_column(ForeignKey("discovered_items.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    partial_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    weight_total: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    youtube_sample: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="evaluated")
    hold_reason: Mapped[str] = mapped_column(Text, default="")
    production_status: Mapped[str] = mapped_column(String(32), default="blocked")
    production_block_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AffiliateProgram(Base):
    __tablename__ = "affiliate_programs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(160), default="")
    locale: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    commission_type: Mapped[str] = mapped_column(String(40), default="")
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    terms: Mapped[str] = mapped_column(Text, default="")
    expires_on: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AffiliateProduct(Base):
    __tablename__ = "affiliate_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    program_id: Mapped[str] = mapped_column(ForeignKey("affiliate_programs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), default="")
    product_url: Mapped[str] = mapped_column(Text, default="")
    affiliate_url: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AffiliateClick(Base):
    __tablename__ = "affiliate_clicks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("affiliate_products.id"), index=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Sponsorship(Base):
    __tablename__ = "sponsorships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    sponsor_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="lead")
    rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CycleRun(Base):
    __tablename__ = "cycle_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
