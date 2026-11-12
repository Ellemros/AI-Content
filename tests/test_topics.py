import pytest
from app.core.errors import DuplicateTopicError
from app.schemas.content import TopicCreate, TopicUpdate
from app.services.topics import create_topic, update_topic


def test_exact_duplicate_is_rejected(db):
    create_topic(db, TopicCreate(title="How machines learn"))
    with pytest.raises(DuplicateTopicError) as caught:
        create_topic(db, TopicCreate(title="  how   machines learn "))
    assert caught.value.exact is True


def test_near_duplicate_is_rejected(db):
    create_topic(db, TopicCreate(title="How machines learn from examples"))
    with pytest.raises(DuplicateTopicError) as caught:
        create_topic(db, TopicCreate(title="How machines learn from example"))
    assert caught.value.exact is False


def test_distinct_topic_is_kept(db):
    create_topic(db, TopicCreate(title="How machines learn from examples"))
    other = create_topic(db, TopicCreate(title="Start with the question"))
    assert other.title == "Start with the question"


def test_rejected_topic_still_blocks_a_duplicate(db):
    topic = create_topic(db, TopicCreate(title="Jak se stroje učí"))
    update_topic(db, topic, TopicUpdate(status="Rejected"))
    with pytest.raises(DuplicateTopicError):
        create_topic(db, TopicCreate(title="Jak se stroje uci"))


def test_past_topics_remain(db):
    from app.models import Topic
    from sqlalchemy import func, select

    create_topic(db, TopicCreate(title="Tools change the work"))
    assert db.scalar(select(func.count()).select_from(Topic)) == 1
