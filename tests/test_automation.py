import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.core.database import get_sessionmaker
from app.core.seed import seed
from app.core.time import utc_now
from app.models import (
    Brand,
    ContentOpportunity,
    CycleRun,
    ProductionCost,
    ProductionJob,
    PublishingJob,
    Video,
)
from app.services.automation import (
    activate_brand,
    automation_enabled,
    create_brand,
    mark_automation_interrupted,
    pause_brand,
    run_brand_production,
    run_due_brands,
    set_automation_enabled,
)
from app.services.probes import media_summary
from app.services.production.pipeline import mark_production_interrupted, produce_next
from app.services.research.feeds import FetchResult, ParsedItem
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ARTICLE = """
<html><body>
<nav>Home menu</nav>
<article>
<h1>Cold plates and battery cooling</h1>
<p>Cold plates pull heat away from the cells during a fast charge in the pack.</p>
<p>The pack then slows the current once the plates approach their temperature limit.</p>
<p>Engineers measure the plate temperature before they raise the charge rate again.</p>
<p>A failed sensor leaves the controller without a plate reading for that cycle.</p>
</article>
</body></html>
"""

THIN = "<html><body><p>Too short.</p></body></html>"


def _enable(session) -> None:
    set_automation_enabled(session, True)
    session.commit()


def _feeds(url: str = "https://example.com/plates"):
    def discover(_source):
        return FetchResult(
            items=[
                ParsedItem(
                    external_id="plates",
                    title="Cold plates and battery cooling",
                    summary="A public feed lead about plates.",
                    url=url,
                    verified=True,
                    check_label="200",
                )
            ],
            body_sha256="plates-body",
        )

    def page(target: str):
        assert target == url
        return 200, ARTICLE

    return discover, page


def test_automated_cycle_renders_a_playable_mp4_and_skips_the_duplicate(db):
    _enable(db)
    brand = create_brand(
        db,
        name="Workshop notes",
        niche="How workshop hardware behaves",
        audience="People who repair battery packs",
        language="en",
        formats=["short"],
        interval_minutes=60,
        max_videos_per_day=2,
        max_spend_usd_per_day="5",
        max_attempts=2,
        terms="plate, heat",
        feed_url="https://example.com/workshop.xml",
        feed_name="Workshop feed",
        activate=True,
    )
    db.commit()
    discover, page = _feeds()
    first = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert first["status"] == "succeeded", first
    assert first["videos"] == 1
    job = db.get(ProductionJob, first["jobs"][0])
    assert job is not None
    assert job.status == "succeeded"
    assert job.report["published"] is False
    assert job.report["ready_for_publishing"] is True
    video = db.get(Video, job.video_id)
    assert video is not None
    assert video.status == "ready"
    assert video.approved is False
    path = Path(video.path)
    assert path.is_file() and path.stat().st_size > 0
    summary = media_summary(path)
    assert summary["video_codec"] == "h264"
    assert summary["audio_codec"] == "aac"
    assert (summary["width"], summary["height"]) == (1080, 1920)
    assert path.read_bytes()[4:8] == b"ftyp"
    costs = db.scalars(select(ProductionCost).where(ProductionCost.job_id == job.id)).all()
    assert costs
    assert {row.brand_id for row in costs} == {brand.id}
    assert {row.opportunity_id for row in costs} == {job.opportunity_id}
    assert db.scalar(select(func.count()).select_from(PublishingJob)) == 0

    again = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert again["videos"] == 0
    assert db.scalar(select(func.count()).select_from(Video)) == 1
    near = ContentOpportunity(
        brand_id=brand.id,
        title="Cold plates and battery cooling",
        partial_score=Decimal("40"),
        coverage=Decimal("0.2"),
        status="evaluated",
        production_status="blocked",
    )
    db.add(near)
    db.commit()
    third = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert third["videos"] == 0
    succeeded = db.scalar(
        select(func.count()).select_from(ProductionJob).where(ProductionJob.status == "succeeded")
    )
    assert succeeded == 1


def test_limits_pause_stop_and_two_brands(db, monkeypatch):
    seed(db)
    _enable(db)
    calls: list[str] = []

    def fake_cycle(session, brand, **_kwargs):
        run = CycleRun(brand_id=brand.id, status="succeeded", stats={"videos_rendered": 0, "uploads": 0})
        session.add(run)
        session.flush()
        return run

    def fake_produce(session, brand, **_kwargs):
        calls.append(brand.slug)
        job = ProductionJob(
            brand_id=brand.id,
            opportunity_id=_opp(session, brand, f"{brand.slug}-topic").id,
            status="succeeded",
            stage="saved",
            attempts=1,
            finished_at=utc_now(),
            report={"published": False, "ready_for_publishing": True},
        )
        session.add(job)
        session.commit()
        return job

    monkeypatch.setattr("app.services.automation.run_brand_cycle", fake_cycle)
    monkeypatch.setattr("app.services.automation.produce_next", fake_produce)
    def pending(_session, brand):
        row = ContentOpportunity(id=f"pending-{brand.slug}", brand_id=brand.id, title=brand.slug)
        return [row]

    monkeypatch.setattr("app.services.automation.eligible_opportunities", pending)

    workshop = create_brand(
        db,
        name="Workshop notes",
        niche="Hardware",
        audience="Repair technicians",
        max_videos_per_day=1,
        max_spend_usd_per_day="5",
        activate=True,
    )
    garden = create_brand(
        db,
        name="Garden notes",
        niche="Soil and seasons",
        audience="Home gardeners",
        language="cs",
        max_videos_per_day=1,
        activate=True,
    )
    experiment = db.scalar(select(Brand).where(Brand.slug == "everyday-decisions"))
    db.commit()

    paused = run_brand_production(db, pause_then(db, workshop))
    assert paused["status"] == "skipped"
    assert calls == []
    activate_brand(db, workshop)
    db.commit()
    first = run_brand_production(db, workshop)
    assert first["videos"] == 1
    second = run_brand_production(db, workshop)
    assert second["videos"] == 0
    assert "video limit" in second["reason"].lower()
    assert calls == ["workshop-notes"]

    garden.max_spend_usd_per_day = Decimal("0")
    db.commit()
    blocked = run_brand_production(db, garden)
    assert blocked["videos"] == 0
    assert "spend" in blocked["reason"].lower()
    assert "garden-notes" not in calls

    set_automation_enabled(db, False)
    db.commit()
    stopped = run_brand_production(db, workshop)
    assert stopped["status"] == "stopped"
    assert not automation_enabled(db)
    assert experiment.status == "experimental"
    assert experiment.automation_enabled is False
    assert workshop.niche != garden.niche
    assert workshop.audience != garden.audience


def pause_then(session, brand):
    pause_brand(session, brand)
    session.commit()
    return brand


def _opp(session, brand, title) -> ContentOpportunity:
    row = ContentOpportunity(
        brand_id=brand.id,
        title=title,
        partial_score=Decimal("10"),
        status="evaluated",
        production_status="blocked",
    )
    session.add(row)
    session.flush()
    return row


def test_failure_stays_visible_and_retries_stop(db, monkeypatch):
    _enable(db)
    brand = create_brand(
        db,
        name="Bench notes",
        niche="Benches",
        audience="Makers",
        max_videos_per_day=3,
        max_attempts=2,
        feed_url="https://example.com/bench.xml",
        activate=True,
    )
    db.commit()

    def discover(_source):
        return FetchResult(
            items=[
                ParsedItem(
                    external_id="thin",
                    title="A thin page about lathes",
                    summary="Lead only.",
                    url="https://example.com/thin",
                    verified=True,
                    check_label="200",
                )
            ],
            body_sha256="thin-body",
        )

    def page(_url: str):
        return 200, THIN

    first = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert first["status"] == "failed"
    assert db.scalar(select(func.count()).select_from(ProductionJob)) == 1
    failed = db.scalars(select(ProductionJob)).one()
    assert failed.status == "failed"
    assert "enough sentences" in failed.error

    run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert db.scalar(select(func.count()).select_from(ProductionJob)) == 2
    third = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert third["videos"] == 0
    assert db.scalar(select(func.count()).select_from(ProductionJob)) == 2
    assert all(row.status == "failed" and row.error for row in db.scalars(select(ProductionJob)))
    monkeypatch.setattr("app.services.automation.produce_next", lambda *_args, **_kwargs: None)


def test_restart_retries_an_interrupted_job(db, monkeypatch):
    _enable(db)
    brand = create_brand(
        db,
        name="Night shift",
        niche="Shifts",
        audience="Operators",
        activate=True,
        max_attempts=3,
    )
    opportunity = _opp(db, brand, "How a night shift hands off a checklist")
    running = ProductionJob(
        brand_id=brand.id,
        opportunity_id=opportunity.id,
        status="running",
        stage="render",
        attempts=1,
    )
    db.add(running)
    brand.last_automation_status = "running"
    db.commit()
    assert mark_production_interrupted(db) == 1
    assert mark_automation_interrupted(db) == 1
    assert running.status == "interrupted"
    assert brand.last_automation_status == "interrupted"
    assert "stopped" in running.error

    def fake_produce(session, _brand, **_kwargs):
        job = ProductionJob(
            brand_id=_brand.id,
            opportunity_id=opportunity.id,
            status="succeeded",
            stage="saved",
            attempts=2,
            finished_at=utc_now(),
            report={"published": False, "ready_for_publishing": True},
        )
        session.add(job)
        session.commit()
        return job

    monkeypatch.setattr("app.services.automation.produce_next", fake_produce)
    monkeypatch.setattr("app.services.automation.run_brand_cycle", _quiet_cycle)
    brand.next_production_at = utc_now() - timedelta(minutes=1)
    db.commit()
    ran = run_due_brands(db)
    assert ran == [brand.id]
    statuses = list(db.scalars(select(ProductionJob.status)))
    assert "interrupted" in statuses
    assert "succeeded" in statuses
    assert run_due_brands(db) == []


def _quiet_cycle(session, brand, **_kwargs):
    run = CycleRun(brand_id=brand.id, status="succeeded", stats={"uploads": 0, "videos_rendered": 0})
    session.add(run)
    session.flush()
    return run


def test_editorial_gate_blocks_autonomous_render(db, monkeypatch):
    _enable(db)
    brand = create_brand(
        db,
        name="Gate notes",
        niche="Checks",
        audience="Editors",
        editorial_gate=True,
        feed_url="https://example.com/gate.xml",
        activate=True,
    )
    db.commit()

    class FakeReview:
        passed = False
        quality_score = 12
        threshold = 70
        issues = [{"kind": "unsupported_claim"}]

    monkeypatch.setattr(
        "app.services.production.pipeline.review_script",
        lambda *_args, **_kwargs: FakeReview(),
    )
    discover, page = _feeds("https://example.com/plates")
    result = run_brand_production(db, brand, discover_fetch=discover, page_fetch=page)
    assert result["status"] == "failed"
    job = db.scalars(select(ProductionJob)).one()
    assert job.status == "failed"
    assert "Editorial" in job.error
    assert db.scalar(select(func.count()).select_from(Video)) == 0


def test_one_opportunity_cannot_be_produced_twice_at_once(db, monkeypatch):
    brand = create_brand(db, name="Lock notes", niche="Locks", audience="Operators", activate=True)
    opportunity = _opp(db, brand, "How a latch holds")
    db.commit()
    started = threading.Event()
    release = threading.Event()

    def slow_run(session, job, *_args, **_kwargs):
        job.stage = "render"
        session.commit()
        started.set()
        assert release.wait(timeout=5)
        job.status = "succeeded"
        job.stage = "saved"
        job.finished_at = utc_now()
        job.report = {"published": False, "ready_for_publishing": True}

    monkeypatch.setattr("app.services.production.pipeline._run", slow_run)
    errors: list[str] = []
    found: list[str] = []

    def go():
        session = get_sessionmaker()()
        try:
            current = session.get(Brand, brand.id)
            job = produce_next(session, current, opportunity_id=opportunity.id)
            session.commit()
            if job is not None:
                found.append(job.id)
        except Exception as exc:
            errors.append(str(exc))
        finally:
            session.close()

    first = threading.Thread(target=go)
    first.start()
    assert started.wait(timeout=5)
    second = threading.Thread(target=go)
    second.start()
    second.join(timeout=5)
    release.set()
    first.join(timeout=5)
    jobs = db.scalars(select(ProductionJob)).all()
    assert len(jobs) == 1
    assert jobs[0].status == "succeeded"
    assert not errors
    assert found
    assert set(found) == {jobs[0].id}


def test_scheduler_tick_runs_a_due_brand(db, monkeypatch):
    _enable(db)
    brand = create_brand(
        db,
        name="Due notes",
        niche="Time",
        audience="Operators",
        interval_minutes=30,
        activate=True,
    )
    brand.next_production_at = utc_now() - timedelta(minutes=5)
    later = create_brand(db, name="Later notes", niche="Time", audience="Operators", activate=True)
    later.next_production_at = utc_now() + timedelta(days=1)
    db.commit()
    seen: list[str] = []

    def fake_run(session, current, **_kwargs):
        seen.append(current.slug)
        current.next_production_at = utc_now() + timedelta(hours=2)
        current.last_automation_status = "idle"
        session.commit()
        return {"status": "idle", "videos": 0, "jobs": [], "reason": ""}

    monkeypatch.setattr("app.services.automation.run_brand_production", fake_run)
    from app.core.scheduler import scheduler

    scheduler.tick()
    assert seen == ["due-notes"]
    assert later.id not in seen


def test_production_page_can_add_and_pause_a_brand(db):
    db.commit()
    from app.main import create_app

    with TestClient(create_app()) as client:
        page = client.get("/production")
        assert page.status_code == 200
        assert "Emergency stop" in page.text or "Turn automation on" in page.text
        assert "does not publish" in page.text
        created = client.post(
            "/production/brands",
            data={
                "name": "Harbor notes",
                "niche": "Tides and ropes",
                "audience": "People who work on boats",
                "language": "en",
                "interval_minutes": "120",
                "max_videos_per_day": "2",
                "max_spend_usd_per_day": "3",
                "max_attempts": "2",
                "short_format": "yes",
                "feed_url": "https://example.com/harbor.xml",
                "activate": "yes",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        shown = client.get("/production")
        assert "Harbor notes" in shown.text
        assert "Tides and ropes" in shown.text
        brand = db.scalar(select(Brand).where(Brand.slug == "harbor-notes"))
        assert brand is not None
        assert brand.automation_enabled is True
        paused = client.post(f"/production/brands/{brand.id}/pause", follow_redirects=False)
        assert paused.status_code == 303
    db.expire_all()
    stored = db.get(Brand, brand.id)
    assert stored.status == "paused"
    assert stored.automation_enabled is False
