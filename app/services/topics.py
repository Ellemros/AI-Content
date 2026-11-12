"""Topics, including near-duplicate rejection against every past topic."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import DuplicateTopicError
from app.models import Channel, Topic
from app.schemas.content import TopicCreate, TopicUpdate
from app.services.textutil import fingerprint, jaccard, sequence_ratio

_SEQUENCE_THRESHOLD = 0.90


def _conflict(session: Session, title: str, ignore_id: str | None = None) -> tuple[Topic, bool] | None:
    exact = session.scalar(select(Topic).where(Topic.fingerprint == fingerprint(title)))
    if exact is not None and exact.id != ignore_id:
        return exact, True
    threshold = get_settings().near_duplicate_threshold
    for row in session.scalars(select(Topic)).all():
        if row.id == ignore_id:
            continue
        if jaccard(title, row.title) >= threshold or sequence_ratio(title, row.title) >= _SEQUENCE_THRESHOLD:
            return row, False
    return None


def _default_channel_id(session: Session) -> str | None:
    channel = session.scalar(select(Channel).order_by(Channel.created_at))
    return None if channel is None else channel.id


def create_topic(session: Session, data: TopicCreate) -> Topic:
    conflict = _conflict(session, data.title)
    if conflict is not None:
        topic, exact = conflict
        raise DuplicateTopicError(topic.title, topic.id, exact)
    topic = Topic(
        channel_id=data.channel_id or _default_channel_id(session),
        title=data.title.strip(),
        description=data.description.strip(),
        category=data.category,
        target_language=data.target_language,
        target_platform=data.target_platform,
        target_audience=data.target_audience.strip() or "curious adults",
        status=data.status,
        priority=data.priority,
        estimated_content_potential=data.estimated_content_potential,
        source_references=[item.model_dump() for item in data.source_references],
        research_references=[item.model_dump() for item in data.research_references],
        fingerprint=fingerprint(data.title),
    )
    session.add(topic)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise DuplicateTopicError(data.title, "existing", True) from exc
    return topic


def update_topic(session: Session, topic: Topic, data: TopicUpdate) -> Topic:
    changes = data.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"]:
        conflict = _conflict(session, changes["title"], ignore_id=topic.id)
        if conflict is not None:
            other, exact = conflict
            raise DuplicateTopicError(other.title, other.id, exact)
        topic.title = changes["title"].strip()
        topic.fingerprint = fingerprint(topic.title)
    for field in (
        "description",
        "category",
        "target_language",
        "target_platform",
        "target_audience",
        "status",
        "priority",
        "estimated_content_potential",
    ):
        if field in changes and changes[field] is not None:
            setattr(topic, field, changes[field])
    if "source_references" in changes and changes["source_references"] is not None:
        topic.source_references = [item.model_dump() for item in data.source_references or []]
    if "research_references" in changes and changes["research_references"] is not None:
        topic.research_references = [item.model_dump() for item in data.research_references or []]
    session.flush()
    return topic
