from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import topic_out
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Topic
from app.schemas.content import TopicCreate, TopicUpdate
from app.services.topics import create_topic, update_topic

router = APIRouter(prefix="/api/topics", tags=["topics"])


def _topic(session: Session, topic_id: str) -> Topic:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic was not found.")
    return topic


@router.get("")
def list_topics(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(Topic).order_by(Topic.created_at.desc())).all()
    return [topic_out(row) for row in rows]


@router.post("", status_code=201)
def post_topic(body: TopicCreate, session: Session = Depends(get_db)) -> dict:
    return topic_out(create_topic(session, body))


@router.get("/{topic_id}")
def get_topic(topic_id: str, session: Session = Depends(get_db)) -> dict:
    return topic_out(_topic(session, topic_id))


@router.patch("/{topic_id}")
def patch_topic(topic_id: str, body: TopicUpdate, session: Session = Depends(get_db)) -> dict:
    return topic_out(update_topic(session, _topic(session, topic_id), body))
