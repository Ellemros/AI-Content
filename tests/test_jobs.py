from app.core.jobs import begin_job, mark_interrupted


def test_running_job_is_not_started_twice(db):
    first, started = begin_job(
        db,
        key="render:one",
        kind="render",
        video_id=None,
        component="all",
        payload={},
        force=False,
    )
    second, again = begin_job(
        db,
        key="render:one",
        kind="render",
        video_id=None,
        component="all",
        payload={},
        force=False,
    )
    assert started is True
    assert again is False
    assert first.id == second.id


def test_interrupted_job_can_be_retried(db):
    job, _started = begin_job(
        db,
        key="render:two",
        kind="render",
        video_id=None,
        component="all",
        payload={},
        force=False,
    )
    assert mark_interrupted(db) == 1
    assert job.status == "interrupted"
    retried, started = begin_job(
        db,
        key="render:two",
        kind="render",
        video_id=None,
        component="all",
        payload={},
        force=False,
    )
    assert started is True
    assert retried.attempts == 2
    assert retried.status == "running"
