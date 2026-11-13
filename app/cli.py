"""Command line for renders, research, brand cycles, and schedules."""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.database import init_db, session_scope
from app.core.errors import AppError, DuplicateTopicError
from app.core.logging import configure_logging
from app.core.seed import seed
from app.domain import CATEGORIES, PLATFORMS
from app.models import Brand, ProductionSchedule, PublishedPost, Script, Video
from app.services.business.cycle import run_brand_cycle
from app.services.pipeline import produce_short
from app.services.probes import media_summary
from app.services.production import produce_next
from app.services.research import review_script, run_discovery
from app.services.schedule import execute_schedule


def _discover() -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        report = run_discovery(session)
    print(
        f"Sources checked: {report['sources']}. "
        f"New items: {report['created']}. "
        f"Promoted: {report['promoted']}. "
        f"Duplicates: {report['duplicates']}. "
        f"Cached: {report['cached_sources']}."
    )
    for name, error in report["failed_sources"]:
        print(f"Failed source: {name}: {error}")
    print("No video was rendered. Scores are editorial heuristics, not search volume.")
    if report["sources"] and len(report["failed_sources"]) == report["sources"]:
        return 1
    return 0


def _cycle(slug: str) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        query = select(Brand).where(Brand.status != "paused")
        if slug:
            query = select(Brand).where(Brand.slug == slug)
        brands = session.scalars(query).all()
        if not brands:
            print("No brand matched. Nothing was rendered or uploaded.")
            return 1
        for brand in brands:
            run = run_brand_cycle(session, brand)
            stats = run.stats or {}
            print(
                f"{brand.slug}: {run.status}. "
                f"Opportunities {stats.get('opportunities_written', 0)}. "
                f"Videos {stats.get('videos_rendered', 0)}. "
                f"Uploads {stats.get('uploads', 0)}. "
                f"Advertising revenue unavailable."
            )
            if run.error:
                print(run.error)
    return 0


def _produce_next(slug: str, opportunity_id: str) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        brand = session.scalar(select(Brand).where(Brand.slug == slug))
        if brand is None:
            print("Brand was not found. No video was rendered or published.")
            return 1
        try:
            job = produce_next(session, brand, opportunity_id=opportunity_id)
        except AppError as exc:
            print(exc.message)
            return 1
        if job is None:
            print("No eligible opportunity. Nothing was rendered or published.")
            return 1
        providers = ", ".join((job.report or {}).get("providers") or []) or "none"
        print(f"Job: {job.id}. Status: {job.status}. Stage: {job.stage}.")
        print(f"Providers: {providers}. Cost: ${float(job.cost_usd or 0):.4f}.")
        if job.error:
            print(job.error)
        if job.video_id:
            video = session.get(Video, job.video_id)
            if video is not None and video.path:
                print(f"Video: {video.path}")
                print(f"Video status: {video.status}")
        print("Published: no.")
        return 0 if job.status == "succeeded" else 1


def _review(script_id: str) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        script = session.get(Script, script_id)
        if script is None:
            print("Script was not found.")
            return 1
        review = review_script(session, script)
        passed = review.passed
        score = review.quality_score
        threshold = review.threshold
        originality = review.originality_score
        issues = list(review.issues or [])
    print(f"Editorial score: {score} / threshold {threshold}")
    print(f"Originality: {originality}")
    print("Passed." if passed else "Rejected by the editorial threshold.")
    for issue in issues:
        print(f"- {issue.get('kind')}: {issue.get('detail')}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render-short", help="Create a topic, script, voice, and vertical MP4.")
    render.add_argument("--language", choices=["en", "cs"], default="en")
    render.add_argument("--title", default="How machines learn from examples")
    render.add_argument("--category", choices=list(CATEGORIES), default="ai")
    render.add_argument("--description", default="")
    render.add_argument("--platform", choices=list(PLATFORMS), default="youtube_shorts")
    render.add_argument("--tts", choices=["auto", "espeak", "edge", "elevenlabs"], default=None)
    render.add_argument("--stability", type=float, default=0.5)
    render.add_argument("--similarity", type=float, default=0.75)
    render.add_argument("--voice-id", default="")
    render.add_argument("--approve", action=argparse.BooleanOptionalAction, default=True)
    render.add_argument("--export", action=argparse.BooleanOptionalAction, default=True)
    render.add_argument("--reuse", action="store_true")
    scheduled = sub.add_parser(
        "run-schedule",
        help="Run one named schedule once. Stops before approval and does not export.",
    )
    scheduled.add_argument("--name", required=True)
    sub.add_parser("discover", help="Fetch configured public feeds and score topics. No video is rendered.")
    cycle = sub.add_parser("run-cycle", help="Score one brand's feeds. Does not render or upload.")
    cycle.add_argument("--slug", default="")
    produce = sub.add_parser("produce-next", help="Render the next eligible opportunity. Does not publish.")
    produce.add_argument("--brand", required=True)
    produce.add_argument("--opportunity", default="")
    review = sub.add_parser("review-script", help="Score one script for originality and unsupported claims.")
    review.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    configure_logging(get_settings().log_level)
    if args.command == "discover":
        return _discover()
    if args.command == "run-cycle":
        return _cycle(args.slug)
    if args.command == "produce-next":
        return _produce_next(args.brand, args.opportunity)
    if args.command == "review-script":
        return _review(args.id)
    if args.command == "render-short" and args.export and not args.approve:
        print("Export only happens after approval. Use --approve or --no-export.")
        return 2
    if getattr(args, "tts", None):
        os.environ["TTS_PROVIDER"] = args.tts
        get_settings.cache_clear()
    init_db()
    try:
        with session_scope() as session:
            seed(session)
            if args.command == "run-schedule":
                schedule = session.scalar(
                    select(ProductionSchedule).where(ProductionSchedule.name == args.name)
                )
                if schedule is None:
                    print(f"No schedule named {args.name}.")
                    return 1
                schedule = execute_schedule(session, schedule)
                print(f"Schedule: {schedule.name}")
                print(f"Status: {schedule.last_status}")
                if schedule.last_error:
                    print(schedule.last_error)
                if schedule.last_video_id:
                    print(f"Video id: {schedule.last_video_id}")
                print("Approval: required before any export.")
                print("Upload: not called.")
                return 0 if schedule.last_status in {"ready", "skipped"} else 1
            video = produce_short(
                session,
                title=args.title,
                language=args.language,
                category=args.category,
                description=args.description,
                platform=args.platform,
                stability=args.stability,
                similarity=args.similarity,
                voice_id=args.voice_id,
                approve=args.approve,
                export=args.export,
                reuse=args.reuse,
            )
            summary = media_summary(Path(video.path)) if video.path else {}
            export_path = ""
            if video.status == "exported":
                post = session.scalar(select(PublishedPost).where(PublishedPost.video_id == video.id))
                export_path = "" if post is None else post.local_export_path
            print(f"Video: {video.path}")
            print(f"Status: {video.status}")
            print(
                f"Picture: {summary.get('width')}x{summary.get('height')} "
                f"{summary.get('video_codec')} / {summary.get('audio_codec')} "
                f"{summary.get('duration')}s"
            )
            if export_path:
                print(f"Export: {export_path}")
            print("Upload: refused. This command only stores a local export.")
    except DuplicateTopicError as exc:
        print(exc.message)
        return 2
    except AppError as exc:
        print(exc.message)
        return 1
    except Exception as exc:
        print(f"Render failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
