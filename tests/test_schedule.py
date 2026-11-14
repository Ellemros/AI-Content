from datetime import timedelta

import pytest
from app.core.time import utc_now
from app.models import PublishedPost, Video
from app.schemas.content import TopicCreate
from app.services.schedule import create_schedule, execute_schedule, run_due, set_scheduler_enabled
from app.services.topics import create_topic
from sqlalchemy import func, select


@pytest.mark.integration
def test_schedule_stops_before_approval_and_never_exports(db, monkeypatch):
    calls = {"n": 0}

    def fake_run(session, video_id):
        calls["n"] += 1
        video = session.get(Video, video_id)
        video.status = "ready"
        video.approved = False
        video.quality_report = {"passed": True, "checks": []}
        session.commit()
        return video

    monkeypatch.setattr("app.services.schedule.run_render", fake_run)
    topic = create_topic(db, TopicCreate(title="How machines learn from examples"))
    schedule = create_schedule(
        db,
        name="Morning short",
        interval_minutes=60,
        language="en",
        topic_id=topic.id,
        enabled=True,
    )
    db.commit()
    result = execute_schedule(db, schedule)
    assert result.last_status == "ready"
    assert "approval" in result.last_error.lower()
    video = db.get(Video, result.last_video_id)
    assert video is not None
    assert video.approved is False
    assert video.status == "ready"
    assert db.scalar(select(func.count()).select_from(PublishedPost)) == 0
    assert calls["n"] == 1

    again = execute_schedule(db, result)
    assert again.last_status == "skipped"
    assert calls["n"] == 1
    assert db.scalar(select(func.count()).select_from(PublishedPost)) == 0


def test_poller_runs_only_due_enabled_schedules(db, monkeypatch):
    seen: list[str] = []

    def fake_execute(session, schedule):
        seen.append(schedule.id)
        schedule.next_run_at = utc_now() + timedelta(hours=2)
        session.commit()
        return schedule

    monkeypatch.setattr("app.services.schedule.execute_schedule", fake_execute)
    topic = create_topic(db, TopicCreate(title="Start with the question"))
    waiting = create_schedule(
        db,
        name="Due",
        interval_minutes=30,
        language="en",
        topic_id=topic.id,
        enabled=True,
    )
    later = create_schedule(
        db,
        name="Later",
        interval_minutes=30,
        language="en",
        topic_id=topic.id,
        enabled=True,
    )
    later.next_run_at = utc_now() + timedelta(days=1)
    parked = create_schedule(
        db,
        name="Parked",
        interval_minutes=30,
        language="en",
        topic_id=topic.id,
        enabled=False,
    )
    db.commit()
    assert run_due(db) == []
    assert seen == []
    set_scheduler_enabled(db, True)
    db.commit()
    ran = run_due(db)
    assert ran == [waiting.id]
    assert seen == [waiting.id]
    assert parked.id not in ran
    assert run_due(db) == []
