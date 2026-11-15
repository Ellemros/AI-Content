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
from app.models import Brand, ProductionJob, ProductionSchedule, PublishedPost, Script, Video
from app.services.automation import run_brand_production
from app.services.business.cycle import run_brand_cycle
from app.services.pipeline import produce_short
from app.services.probes import media_summary
from app.services.production import produce_next
from app.services.production.test_run import TEST_TITLE, run_test_production
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


def _automate(slug: str) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        brand = session.scalar(select(Brand).where(Brand.slug == slug))
        if brand is None:
            print("Brand was not found. No video was rendered or published.")
            return 1
        result = run_brand_production(session, brand)
    print(f"{slug}: {result['status']}. Videos {result['videos']}.")
    if result["reason"]:
        print(result["reason"])
    print("Published: no.")
    return 0 if result["status"] in {"succeeded", "idle", "skipped"} else 1


def _test_production(slug: str) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        brand = session.scalar(select(Brand).where(Brand.slug == slug))
        if brand is None:
            print("Brand was not found. No video was rendered or published.")
            return 1
        try:
            job = run_test_production(session, brand, require_live_providers=True)
        except AppError as exc:
            print(exc.message)
            print("Published: no.")
            print("Uploaded: no.")
            return 1
        report = dict(job.report or {})
        path = ""
        if job.video_id:
            video = session.get(Video, job.video_id)
            path = video.path if video is not None else ""
        quality = str(report.get("quality_status") or "")
        explanation = str(report.get("explanation") or "")
        warnings = list(report.get("warnings") or [])
        error = job.error or ""
        status = job.status
    print(f"Topic: {TEST_TITLE}")
    print(f"{slug}: {status}. Quality: {quality or 'unknown'}.")
    if error:
        print(error)
    if explanation:
        print(explanation)
    for warning in warnings:
        print(warning)
    print("Published: no.")
    print("Uploaded: no.")
    if path:
        print(f"MP4: {Path(path).resolve()}")
    else:
        print("MP4 was not written.")
    return 0 if status == "succeeded" else 1


_KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _install_voice() -> int:
    """Download Kokoro-82M (Apache-2.0) for local narration. About 350 MB, once."""
    import httpx

    settings = get_settings()
    targets = [
        (Path(settings.kokoro_model_path), _KOKORO_RELEASE + "kokoro-v1.0.onnx", 300_000_000),
        (Path(settings.kokoro_voices_path), _KOKORO_RELEASE + "voices-v1.0.bin", 25_000_000),
    ]
    for path, url, minimum in targets:
        if path.is_file() and path.stat().st_size >= minimum:
            print(f"Present: {path.resolve()}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        print(f"Downloading {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            if response.status_code != 200:
                print(f"Download failed: HTTP {response.status_code}.")
                return 1
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
        if partial.stat().st_size < minimum:
            print(f"Download is too small: {partial.stat().st_size} bytes.")
            return 1
        partial.replace(path)
        print(f"Saved: {path.resolve()}")
    print(f"Kokoro is ready. Voice: {settings.kokoro_voice}. TTS_PROVIDER=auto now uses it for English.")
    return 0


def _produce_next(slug: str, opportunity_id: str, retry: bool = False) -> int:
    init_db()
    with session_scope() as session:
        seed(session)
        brand = session.scalar(select(Brand).where(Brand.slug == slug))
        if brand is None:
            print("Brand was not found. No video was rendered or published.")
            return 1
        try:
            job = produce_next(session, brand, opportunity_id=opportunity_id, retry=retry)
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
                print(f"Video: {Path(video.path).resolve()}")
                print(f"Video status: {video.status}")
                print(f"Dashboard: http://127.0.0.1:8000/production (job {job.id})")
        print("Published: no.")
        return 0 if job.status == "succeeded" else 1


def _brand_or_none(session, slug: str):
    brand = session.scalar(select(Brand).where(Brand.slug == slug))
    if brand is None:
        print(f"No brand with slug {slug}.")
    return brand


def _publishing_command(args) -> int:
    import json as _json
    import os as _os

    from app.models import PlatformPost
    from app.services.publishing import credentials
    from app.services.publishing.analytics import fetch_youtube, profit_report
    from app.services.publishing.queue import plan_posts, publish_due, publishing_blocked, queue_ready

    init_db()
    with session_scope() as session:
        seed(session)
        if args.command == "plan-posts":
            video = session.get(Video, args.video)
            if video is None:
                print("Video was not found.")
                return 1
            job_brand = session.scalar(
                select(Brand)
                .join(ProductionJob, ProductionJob.brand_id == Brand.id)
                .where(ProductionJob.video_id == video.id)
            )
            if job_brand is None:
                print("This video was not made by a brand, so it has no publishing plan.")
                return 1
            for post in plan_posts(session, video, job_brand):
                print(f"[{post.platform}] {post.status}")
                print(f"  title: {post.title}")
                print("  description: " + post.description.replace(chr(10), " | ")[:400])
            print("Planned only. Nothing was uploaded.")
            return 0
        if args.command == "posts":
            rows = session.scalars(
                select(PlatformPost).order_by(PlatformPost.created_at.desc()).limit(50)
            ).all()
            for post in rows:
                print(
                    f"{post.platform:15} {post.status:12} {str(post.scheduled_at or '')[:16]:16} "
                    f"{post.video_id[:8]} {post.external_url or post.last_error[:60]}"
                )
            print(publishing_blocked(session) or "Publishing is enabled.")
            return 0
        if args.command == "publish-due":
            brands = session.scalars(select(Brand)).all()
            for brand in brands:
                queue_ready(session, brand)
            session.commit()
            result = publish_due(session)
            print(result.get("reason") or f"Published {result['published']} of {result['due']} due posts.")
            return 0 if result["status"] != "refused" else 2
        if args.command == "set-publishing":
            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            brand.publishing_mode = args.mode
            brand.daily_publish_limit = args.daily_limit
            print(f"{brand.slug}: publishing_mode={args.mode}, daily_publish_limit={args.daily_limit}.")
            print(
                publishing_blocked(session)
                or "PUBLISHING_ENABLED is true: queued posts will upload when due."
            )
            return 0
        if args.command == "connect-youtube":
            from app.services.publishing.connectors import PublishError, youtube_authorize

            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            try:
                token = youtube_authorize()
            except PublishError as exc:
                print(exc.message)
                return 1
            path = credentials.save(brand.slug, "youtube_short", token)
            print(f"YouTube connected for {brand.slug}. Token saved to {path.resolve()} (not in Git).")
            return 0
        if args.command == "connect-tiktok":
            from app.services.publishing.connectors import tiktok_exchange_code

            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            token = tiktok_exchange_code(args.code, args.redirect_uri)
            path = credentials.save(brand.slug, "tiktok", token)
            print(f"TikTok connected for {brand.slug}. Token saved to {path.resolve()} (not in Git).")
            return 0
        if args.command == "connect-meta":
            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            token = _os.environ.get("META_PAGE_TOKEN", "").strip()
            if not token:
                print("Set META_PAGE_TOKEN in this terminal first (a long-lived Page access token).")
                return 1
            data = {"page_id": args.page_id, "ig_user_id": args.ig_user_id, "page_access_token": token}
            path = credentials.save(brand.slug, "instagram_reel", data)
            print(f"Meta connected for {brand.slug}. Token saved to {path.resolve()} (not in Git).")
            return 0
        if args.command == "fetch-analytics":
            result = fetch_youtube(session, days=args.days)
            print(f"YouTube posts: {result['posts']}. Metric rows stored: {result['rows']}.")
            for error in result["errors"]:
                print(error)
            return 0
        if args.command == "profit-report":
            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            print(_json.dumps(profit_report(session, brand), indent=2, default=str))
            return 0
    return 1


def _cleanup_work(days: int, apply: bool) -> int:
    """Delete per-job work folders (scene clips, audio stems) of finished jobs. Final MP4s are kept."""
    import shutil
    from datetime import timedelta

    from app.core.time import utc_now

    init_db()
    root = get_settings().data_dir / "production"
    freed = 0
    removed = 0
    with session_scope() as session:
        cutoff = utc_now() - timedelta(days=days)
        for folder in sorted(root.glob("*")) if root.is_dir() else []:
            job = session.get(ProductionJob, folder.name)
            if job is None or job.status == "running":
                continue
            finished = job.finished_at
            if finished is not None and finished.tzinfo is None:
                finished = finished.replace(tzinfo=cutoff.tzinfo)
            if finished is None or finished > cutoff:
                continue
            size = sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())
            freed += size
            removed += 1
            if apply:
                shutil.rmtree(folder, ignore_errors=True)
    verb = "Removed" if apply else "Would remove"
    print(f"{verb} {removed} work folders, {freed / 1_048_576:.1f} MB. Final videos are kept.")
    if not apply:
        print("Add --apply to delete.")
    return 0


def _pilot(args) -> int:
    from app.services.production import pilot

    topics = Path(args.topics or f"topics/{args.brand}.json")
    out = Path(args.out or f"output/review/{args.brand}-pilot")
    # Running again with the same folder continues the same pilot: earlier videos and spend are kept
    # and count against --budget. A new folder (--out) starts a new pilot with its own budget.
    init_db()
    with session_scope() as session:
        seed(session)
        brand = session.scalar(select(Brand).where(Brand.slug == args.brand))
        if brand is None:
            print("Brand was not found. Nothing was produced.")
            return 1
        print(f"Run budget: ${args.budget:.2f}. Videos wanted: {args.videos}. Publishing: off.")
        try:
            result = pilot.run(
                session,
                brand,
                count=args.videos,
                budget_usd=args.budget,
                topics_path=topics,
                out_dir=out,
                discover_feeds=not args.no_discover,
            )
        except AppError as exc:
            print(exc.message)
            return 1
        made = [video for video in result.videos if video["status"] == "succeeded"]
        print(f"Attempted: {len(result.videos)}. Rendered: {len(made)}. Spent: ${result.spent_usd:.4f}.")
        print(f"Stopped because: {result.stopped_because}")
        print(f"Review package: {out.resolve()}")
        print("Published: no.")
        return 0 if made else 1


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
    auto = sub.add_parser(
        "run-automation",
        help="Discover and produce for one active brand. Does not publish.",
    )
    auto.add_argument("--slug", required=True)
    test_run = sub.add_parser(
        "test-production",
        help="Render one fixed non-medical topic. Does not discover, publish, or change other opportunities.",
    )
    test_run.add_argument("--slug", required=True)
    produce = sub.add_parser("produce-next", help="Render the next eligible opportunity. Does not publish.")
    produce.add_argument("--brand", required=True)
    produce.add_argument("--opportunity", default="")
    produce.add_argument(
        "--retry",
        action="store_true",
        help="Allow one more attempt for --opportunity after the attempt limit. Holds still apply.",
    )
    sub.add_parser("install-voice", help="Download the local Kokoro voice model (about 350 MB, once).")
    sub.add_parser(
        "create-recommended-brand",
        help="Create the recommended Money Minds channel, paused, publishing off. Changes no other brand.",
    )
    pp = sub.add_parser("plan-posts", help="Write per-platform titles and descriptions. Uploads nothing.")
    pp.add_argument("--video", required=True)
    sub.add_parser("posts", help="List the publishing queue and history.")
    at = sub.add_parser(
        "add-topic", help="Add a viewer question with its sources to a brand. Renders nothing."
    )
    at.add_argument("--brand", required=True)
    at.add_argument("--question", required=True)
    at.add_argument("--title", default="")
    at.add_argument("--url", action="append", required=True, help="Source page; repeat for more sources.")
    pilot = sub.add_parser(
        "pilot",
        help="Discover, rank, and produce several videos under a hard API budget, then write a review "
        "package. Does not publish.",
    )
    pilot.add_argument("--brand", default="money-minds")
    pilot.add_argument("--videos", type=int, default=5)
    pilot.add_argument(
        "--budget", type=float, default=5.0, help="Hard API spending limit for this run, in USD."
    )
    pilot.add_argument("--topics", default="", help="Topic backlog JSON (default: topics/<brand>.json).")
    pilot.add_argument(
        "--out",
        default="",
        help="Review folder (default: output/review/<brand>-pilot). The same folder continues the same "
        "pilot: its earlier videos and spend count against --budget.",
    )
    pilot.add_argument("--no-discover", action="store_true", help="Skip fetching the brand's feeds.")
    fv = sub.add_parser(
        "flag-video",
        help="Record a reviewer's finding on a produced video and set it to needs_review. Deletes nothing.",
    )
    fv.add_argument("--job", required=True)
    fv.add_argument("--reason", required=True)
    rr = sub.add_parser("rerender", help="Render a motion job's picture again. No model call, no cost.")
    rr.add_argument("--job", required=True)
    sr = sub.add_parser("set-renderer", help="Choose a brand's renderer: motion or stickman.")
    sr.add_argument("--brand", required=True)
    sr.add_argument("--renderer", required=True, choices=["motion", "stickman"])
    sub.add_parser("publish-due", help="Upload due posts. Refuses while PUBLISHING_ENABLED=false.")
    sp = sub.add_parser("set-publishing", help="Set a brand's publishing mode. Does not upload anything.")
    sp.add_argument("--brand", required=True)
    sp.add_argument("--mode", required=True, choices=["test", "approval", "autonomous"])
    sp.add_argument("--daily-limit", type=int, default=1)
    cy = sub.add_parser("connect-youtube", help="One-time Google sign-in for a brand's YouTube channel.")
    cy.add_argument("--brand", required=True)
    ct = sub.add_parser("connect-tiktok", help="Exchange a TikTok authorization code for a brand.")
    ct.add_argument("--brand", required=True)
    ct.add_argument("--code", required=True)
    ct.add_argument("--redirect-uri", required=True)
    cm = sub.add_parser(
        "connect-meta", help="Store a Page token (from META_PAGE_TOKEN) for Instagram and Facebook."
    )
    cm.add_argument("--brand", required=True)
    cm.add_argument("--page-id", required=True)
    cm.add_argument("--ig-user-id", required=True)
    fa = sub.add_parser("fetch-analytics", help="Pull YouTube Analytics for published posts.")
    fa.add_argument("--days", type=int, default=28)
    pr = sub.add_parser(
        "profit-report", help="Views, reported revenue, cost, and profit by video and format."
    )
    pr.add_argument("--brand", required=True)
    cw = sub.add_parser("cleanup-work", help="Delete old per-job work folders. Keeps final videos.")
    cw.add_argument("--days", type=int, default=14)
    cw.add_argument("--apply", action="store_true")
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
    if args.command == "run-automation":
        return _automate(args.slug)
    if args.command == "test-production":
        return _test_production(args.slug)
    if args.command == "produce-next":
        if args.retry and not args.opportunity:
            print("--retry needs --opportunity.")
            return 2
        return _produce_next(args.brand, args.opportunity, args.retry)
    if args.command == "install-voice":
        return _install_voice()
    if args.command == "create-recommended-brand":
        from app.services.strategy import create_recommended_brand

        init_db()
        with session_scope() as session:
            seed(session)
            brand = create_recommended_brand(session)
            print(f"Brand: {brand.name} ({brand.slug}). Status: {brand.status}.")
            print(f"Publishing: {brand.publishing_mode}. Automation on: {brand.automation_enabled}.")
        return 0
    if args.command in {
        "plan-posts",
        "posts",
        "publish-due",
        "set-publishing",
        "connect-youtube",
        "connect-tiktok",
        "connect-meta",
        "fetch-analytics",
        "profit-report",
    }:
        return _publishing_command(args)
    if args.command == "pilot":
        return _pilot(args)
    if args.command == "flag-video":
        from app.services.production.pilot import flag_video

        init_db()
        with session_scope() as session:
            try:
                video = flag_video(session, args.job, args.reason)
            except AppError as exc:
                print(exc.message)
                return 1
            print(f"Video {video.id} is now {video.status}. Reason recorded. Published: no.")
        return 0
    if args.command == "rerender":
        from app.services.production.pipeline import rerender_motion

        init_db()
        with session_scope() as session:
            job = session.get(ProductionJob, args.job)
            if job is None:
                print("Job was not found.")
                return 1
            try:
                result = rerender_motion(session, job)
            except AppError as exc:
                print(exc.message)
                return 1
            print(f"Video: {result['video']}")
            print(f"Status: {result['status']}. Contact sheet: {result['contact_sheet']}")
            if result["failed"]:
                print("Failed checks: " + ", ".join(result["failed"]))
            print("Published: no.")
        return 0
    if args.command in {"add-topic", "set-renderer"}:
        from app.services.strategy import add_topic

        init_db()
        with session_scope() as session:
            seed(session)
            brand = _brand_or_none(session, args.brand)
            if brand is None:
                return 1
            if args.command == "set-renderer":
                brand.identity = {**(brand.identity or {}), "renderer": args.renderer}
                print(f"{brand.slug} renders with {args.renderer}.")
                return 0
            try:
                row = add_topic(session, brand, question=args.question, urls=args.url, title=args.title)
            except AppError as exc:
                print(exc.message)
                return 1
            print(f"Opportunity: {row.id}. Status: {row.status}. Sources: {len(row.source_urls)}.")
            if row.hold_reason:
                print(row.hold_reason)
        return 0
    if args.command == "cleanup-work":
        return _cleanup_work(args.days, args.apply)
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
