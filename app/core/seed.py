"""Seed the default channel and the built-in offline templates."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Brand, BrandSource, Channel, ContentTemplate, ResearchSource
from app.services.offline_scripts import iter_template_records
from app.services.research.brand import BRAND_DESCRIPTION, BRAND_NAME, DEFAULT_FEEDS

_EXPERIMENT = {
    "slug": "everyday-decisions",
    "name": "Everyday decisions",
    "niche": "How people remember, decide, and misread ordinary situations",
    "audience": "Adults who want a clear explanation of ordinary behavior",
    "language": "en",
    "identity": {
        "terms": [
            "memory",
            "remember",
            "decision",
            "behave",
            "habit",
            "attention",
            "bias",
            "sleep",
            "emotion",
        ],
        "blocked_areas": ["medical_advice", "financial_advice"],
    },
}
_EXPERIMENT_FEEDS = (
    ("ScienceDaily Mind & Brain", "https://www.sciencedaily.com/rss/mind_brain.xml", "science"),
)


def seed(session: Session) -> None:
    channel = session.scalar(select(Channel).where(Channel.slug == "default"))
    if channel is None and (session.scalar(select(func.count()).select_from(Channel)) or 0) == 0:
        session.add(
            Channel(
                name=BRAND_NAME,
                slug="default",
                default_language="en",
                default_platform="youtube_shorts",
                description=BRAND_DESCRIPTION,
            )
        )
    elif channel is not None and channel.name == "AI Content Factory":
        channel.name = BRAND_NAME
        channel.description = BRAND_DESCRIPTION
    for name, url, category in DEFAULT_FEEDS:
        exists = session.scalar(select(ResearchSource).where(ResearchSource.url == url))
        if exists is None:
            session.add(ResearchSource(name=name, kind="rss", url=url, category=category, enabled=True))
    _seed_experiment(session)
    for record in iter_template_records():
        exists = session.scalar(
            select(ContentTemplate).where(
                ContentTemplate.template_key == record["template_key"],
                ContentTemplate.language == record["language"],
                ContentTemplate.format == record["format"],
            )
        )
        if exists is None:
            session.add(
                ContentTemplate(
                    name=record["name"],
                    template_key=record["template_key"],
                    language=record["language"],
                    format=record["format"],
                    niche="ai-tech-science",
                    body={
                        "categories": record["categories"],
                        "hook": record["hook"],
                        "music_mood": record["music_mood"],
                        "thumbnail_text": record["thumbnail_text"],
                        "offline_draft": True,
                    },
                )
            )
    session.flush()


def _seed_experiment(session: Session) -> None:
    brand = session.scalar(select(Brand).where(Brand.slug == _EXPERIMENT["slug"]))
    if brand is None:
        brand = Brand(
            slug=_EXPERIMENT["slug"],
            name=_EXPERIMENT["name"],
            niche=_EXPERIMENT["niche"],
            audience=_EXPERIMENT["audience"],
            language=_EXPERIMENT["language"],
            identity=_EXPERIMENT["identity"],
            status="experimental",
            publishing_mode="test",
            daily_publish_limit=0,
        )
        session.add(brand)
        session.flush()
    for name, url, category in _EXPERIMENT_FEEDS:
        source = session.scalar(select(ResearchSource).where(ResearchSource.url == url))
        if source is None:
            source = ResearchSource(name=name, kind="rss", url=url, category=category, enabled=False)
            session.add(source)
            session.flush()
        link = session.scalar(
            select(BrandSource).where(BrandSource.brand_id == brand.id, BrandSource.source_id == source.id)
        )
        if link is None:
            session.add(BrandSource(brand_id=brand.id, source_id=source.id))
