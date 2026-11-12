"""Seed the default channel and the built-in offline templates."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Channel, ContentTemplate
from app.services.offline_scripts import iter_template_records


def seed(session: Session) -> None:
    count = session.scalar(select(func.count()).select_from(Channel)) or 0
    if count == 0:
        session.add(
            Channel(
                name="AI Content Factory",
                slug="default",
                default_language="en",
                default_platform="youtube_shorts",
                description="Faceless explainers about AI, technology, science, and careful facts.",
            )
        )
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
