"""A production run: find topics, pick the best distinct ones, make each video, package them for review.

One command does the whole thing (`python -m app.cli pilot`):

1. Discover. Fetch the brand's feeds (free) and add the brand's evergreen topic backlog
   (a JSON file of viewer questions with primary sources). Every topic passes the same
   medical and financial-advice holds as before.
2. Rank. One small model call scores every eligible topic on audience interest, retention
   potential, advertiser friendliness, research quality, and visual potential, and gives it
   a theme. The run picks the best topics with different themes and different story structures.
3. Produce. Each topic goes through the existing pipeline: multi-source dossier, script by the
   writer model, independent fact check, story editor, storyboard validation, Kokoro voice,
   Remotion render, FFmpeg mix, technical and visual QA. Nothing in this module bypasses a check.
   A topic that fails is replaced by the next ranked topic; it is not retried blindly.
4. Package. Every attempted video (passed or failed) gets a folder with its MP4, contact sheet,
   script, storyboard, sources, verification results, QA report, and costs, plus one run report.

A hard budget covers every paid call in the run, checked before each call against its worst case.
Nothing is published. Videos that pass are `ready` and still wait for a person to approve them.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError, RunBudgetExceeded, SpendCapExceeded
from app.core.time import utc_now
from app.models import (
    APIUsage,
    Brand,
    ContentOpportunity,
    DiscoveredItem,
    ProductionCost,
    ProductionJob,
    ResearchDossier,
    ScriptVersion,
    Video,
)
from app.services.cost import run_budget, run_remaining, run_spend
from app.services.textutil import jaccard

# Before starting a video the run must still have this much budget: the measured worst case of a
# video (three writer drafts with fact checks and story reviews) with a margin. A video is never
# started that the budget could not finish.
VIDEO_RESERVE_USD = Decimal("0.75")
RANK_LIMIT = 30


@dataclass
class Pick:
    opportunity_id: str
    title: str
    theme: str
    structure: str
    scores: dict
    why: str
    source: str  # backlog | feed


@dataclass
class RunResult:
    started_at: str
    finished_at: str = ""
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    ranking: list[dict] = field(default_factory=list)
    ranking_cost_usd: float = 0.0
    videos: list[dict] = field(default_factory=list)
    stopped_because: str = ""
    seconds: float = 0.0


# ---------------------------------------------------------------------------------------------- discover


def load_backlog(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    topics = data.get("topics") if isinstance(data, dict) else data
    return [topic for topic in topics or [] if isinstance(topic, dict) and topic.get("question")]


def seed_backlog(session: Session, brand: Brand, topics: list[dict]) -> dict[str, dict]:
    """Add backlog topics as opportunities (skipping ones already added). Returns {opportunity_id: topic}."""
    from app.services.strategy import add_topic

    by_id: dict[str, dict] = {}
    for topic in topics:
        title = str(topic.get("title") or topic["question"])[:300]
        existing = session.scalar(
            select(ContentOpportunity).where(
                ContentOpportunity.brand_id == brand.id, ContentOpportunity.title == title
            )
        )
        if existing is None:
            try:
                existing = add_topic(
                    session,
                    brand,
                    question=topic["question"],
                    urls=list(topic.get("sources") or []),
                    title=title,
                )
            except AppError:
                continue
        by_id[existing.id] = topic
    session.flush()
    return by_id


def discover(session: Session, brand: Brand) -> dict:
    """Fetch the brand's feeds and score new items. Free: no paid model is called here."""
    from app.services.business.cycle import run_brand_cycle

    try:
        run = run_brand_cycle(session, brand)
    except Exception as exc:  # noqa: BLE001 - a feed outage must not stop a run that has a backlog
        return {"status": "failed", "error": str(exc)[:300]}
    return {"status": run.status, "stats": run.stats or {}, "error": run.error or ""}


# ---------------------------------------------------------------------------------------------- rank

_CRITERIA = ("audience_interest", "retention", "advertiser_friendly", "research", "visual")


def _candidate_line(index: int, row: ContentOpportunity, item: DiscoveredItem | None, backlog: dict) -> str:
    urls = list(row.source_urls or []) or ([item.url] if item is not None and item.url else [])
    domains = ", ".join(sorted({re.sub(r"^https?://(www\.)?", "", url).split("/")[0] for url in urls}))
    kind = "curated question with primary sources" if row.id in backlog else "news feed item"
    question = row.question or (item.summary if item is not None else "") or ""
    return f"{index}. [{kind}] {row.title} | question: {question[:220]} | sources: {domains}"


def rank(
    session: Session, brand: Brand, candidates: list[ContentOpportunity], backlog: dict[str, dict]
) -> list[dict]:
    """Score candidates with the small model; without a model, keep the backlog order. Returns rows."""
    from app.providers.openai_provider import OpenAIProvider
    from app.services.strategy import STRUCTURES

    settings = get_settings()
    rows = candidates[:RANK_LIMIT]
    if not rows:
        return []
    items = {
        row.id: session.get(DiscoveredItem, row.discovered_item_id) for row in rows if row.discovered_item_id
    }
    if not (settings.openai_enabled() and settings.openai_factcheck_model):
        out = []
        for row in rows:
            topic = backlog.get(row.id)
            if topic is None:
                continue
            out.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "theme": topic.get("theme") or row.title,
                    "structure": topic.get("structure") or "",
                    "scores": {},
                    "total": 0,
                    "qualified": True,
                    "why": "No ranking model configured: backlog order.",
                    "source": "backlog",
                }
            )
        return out
    made = _made_titles(session, brand)
    system = (
        "You are the programming editor of a faceless short-form channel. "
        f"Channel niche: {brand.niche}\nAudience: {brand.audience}\n"
        "Score each candidate topic from 1 to 5 on: niche_fit (squarely about money, prices, spending, "
        "saving, "
        "or economic decisions; 1 if it is about something else), audience_interest (a broad "
        "English-speaking "
        "audience would want to know), retention (a surprising turn or open question that keeps people "
        "watching to the end), advertiser_friendly (safe for brands: no health scares, violence, politics, "
        "or tragedy), research (backed by peer-reviewed primary research the sources actually contain; a "
        "news item scores lower than the paper itself), visual (can be shown with simple objects, numbers, "
        "comparisons, and diagrams). Give each topic a short theme label (2-4 words) so near-identical "
        "topics share a theme, the best story structure from "
        f"({', '.join(STRUCTURES)}), and one sentence why. Be strict: most feed items are off-niche.\n"
        'Return JSON: {"topics": [{"n": int, "niche_fit": int, "audience_interest": int, "retention": int, '
        '"advertiser_friendly": int, "research": int, "visual": int, "theme": str, "structure": str, '
        '"why": str}]}.'
    )
    user = (
        "Already made on this channel (do not repeat these themes):\n"
        + ("\n".join(f"- {title}" for title in made) or "- nothing yet")
        + "\n\nCandidates:\n"
        + "\n".join(
            _candidate_line(index + 1, row, items.get(row.id), backlog) for index, row in enumerate(rows)
        )
    )
    provider = OpenAIProvider(session, model=settings.openai_factcheck_model)
    result = provider.complete_json(operation="topic-ranking", system=system, user=user)
    scored = {}
    for entry in (result or {}).get("topics") or []:
        try:
            scored[int(entry.get("n"))] = entry
        except (TypeError, ValueError):
            continue
    out = []
    for index, row in enumerate(rows):
        entry = scored.get(index + 1) or {}
        scores = {key: _score(entry.get(key)) for key in ("niche_fit", *_CRITERIA)}
        qualified = (
            scores["niche_fit"] >= 4
            and scores["research"] >= 3
            and scores["advertiser_friendly"] >= 3
            and min(scores[key] for key in _CRITERIA) >= 2
        )
        structure = str(entry.get("structure") or "")
        out.append(
            {
                "id": row.id,
                "title": row.title,
                "theme": str(entry.get("theme") or row.title)[:60],
                "structure": structure if structure in STRUCTURES else "",
                "scores": scores,
                "total": sum(scores[key] for key in _CRITERIA),
                "qualified": qualified,
                "why": str(entry.get("why") or "")[:240],
                "source": "backlog" if row.id in backlog else "feed",
            }
        )
    out.sort(key=lambda entry: (not entry["qualified"], -entry["total"]))
    return out


def _score(value) -> int:
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 0


def _made_titles(session: Session, brand: Brand) -> list[str]:
    rows = session.scalars(
        select(ContentOpportunity.title)
        .join(ProductionJob, ProductionJob.opportunity_id == ContentOpportunity.id)
        .where(ProductionJob.brand_id == brand.id, ProductionJob.status == "succeeded")
    ).all()
    return list(dict.fromkeys(rows))[:20]


def choose(ranked: list[dict], count: int, made: list[str], backlog: dict[str, dict]) -> list[Pick]:
    """Best qualified topics with different themes, near-duplicate titles skipped, structures varied."""
    from app.services.strategy import STRUCTURES

    picks: list[Pick] = []
    themes: set[str] = set()
    structures: set[str] = set()
    for entry in ranked:
        if not entry["qualified"]:
            continue
        theme = entry["theme"].strip().lower()
        if theme in themes:
            continue
        if any(jaccard(entry["title"], other) >= 0.5 for other in [*made, *(p.title for p in picks)]):
            continue
        hint = (backlog.get(entry["id"]) or {}).get("structure") or ""
        options = [entry["structure"], hint, *STRUCTURES]
        structure = next(
            (name for name in options if name and name not in structures), entry["structure"] or hint
        )
        picks.append(
            Pick(
                opportunity_id=entry["id"],
                title=entry["title"],
                theme=entry["theme"],
                structure=structure,
                scores=entry["scores"],
                why=entry["why"],
                source=entry["source"],
            )
        )
        themes.add(theme)
        structures.add(structure)
        if len(picks) >= count:
            break
    return picks


# ---------------------------------------------------------------------------------------------- run


def run(
    session: Session,
    brand: Brand,
    *,
    count: int = 5,
    budget_usd: float = 5.0,
    topics_path: Path | None = None,
    out_dir: Path,
    discover_feeds: bool = True,
    max_attempts: int | None = None,
    label: str = "",
) -> RunResult:
    from app.services.production.pipeline import eligible_opportunities, produce_next

    if brand.status == "paused":
        raise AppError(f"{brand.name} is paused. Nothing was produced.")
    clock = time.monotonic()
    label = label or out_dir.name
    ledger = load_ledger(out_dir)
    earlier = sum(Decimal(str(run.get("charged_usd", run.get("spent_usd", 0)))) for run in ledger)
    result = RunResult(started_at=utc_now().isoformat(), budget_usd=float(budget_usd))
    # The limit covers the whole pilot: earlier runs under this label (with any unrecorded spend) count.
    cap = Decimal(str(budget_usd)) - earlier
    attempts_left = max_attempts if max_attempts is not None else count + 3
    already = sum(1 for job in labelled_jobs(session, brand, label) if _passed(session, job))
    count = max(count - already, 0)
    if cap <= 0:
        result.stopped_because = f"The pilot budget of ${budget_usd:.2f} is already used by earlier runs."
    with run_budget(max(cap, Decimal("0"))) as since:
        if discover_feeds:
            result.ranking.append({"discovery": discover(session, brand)})
        backlog = seed_backlog(session, brand, load_backlog(topics_path) if topics_path else [])
        session.commit()
        candidates = eligible_opportunities(session, brand)
        try:
            ranked = rank(session, brand, candidates, backlog)
        except (RunBudgetExceeded, SpendCapExceeded) as exc:
            result.stopped_because = exc.message
            ranked = []
        session.commit()
        result.ranking_cost_usd = float(run_spend(session, since))
        result.ranking = [entry for entry in result.ranking if "discovery" in entry] + ranked
        made = _made_titles(session, brand)
        queue = choose(ranked, len(ranked), made, backlog)
        produced = 0
        if count == 0 and not result.stopped_because:
            result.stopped_because = f"Done: the pilot already has {already} videos."
        for pick in queue:
            if produced >= count or attempts_left <= 0 or result.stopped_because:
                break
            remaining = run_remaining(session)
            if remaining is not None and remaining < VIDEO_RESERVE_USD:
                result.stopped_because = (
                    f"Budget: ${float(remaining):.2f} left, below the "
                    f"${float(VIDEO_RESERVE_USD):.2f} a video "
                    "may need. No further video was started."
                )
                break
            attempts_left -= 1
            started = time.monotonic()
            try:
                job = produce_next(
                    session, brand, opportunity_id=pick.opportunity_id, structure=pick.structure
                )
            except (RunBudgetExceeded, SpendCapExceeded) as exc:
                result.stopped_because = exc.message
                session.commit()
                break
            if job is not None:
                job.report = {**(job.report or {}), "run": label, "pick": pick.__dict__}
            session.commit()
            if job is None:
                continue
            entry = {
                "pick": pick.__dict__,
                "job_id": job.id,
                "status": job.status,
                "seconds": round(time.monotonic() - started, 1),
            }
            if job.status == "failed" and "Run budget reached" in (job.error or ""):
                result.stopped_because = job.error
            if job.status == "succeeded":
                produced += 1
            result.videos.append(entry)
        result.spent_usd = float(run_spend(session, since))
    result.finished_at = utc_now().isoformat()
    result.seconds = round(time.monotonic() - clock, 1)
    if not result.stopped_because:
        if produced >= count:
            result.stopped_because = (
                f"Done: {produced} new video(s) passed; the pilot has {already + produced} that passed."
            )
        elif attempts_left <= 0:
            result.stopped_because = "Attempt limit reached."
        else:
            result.stopped_because = "No more qualified topics."
    ledger.append(
        {
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "spent_usd": result.spent_usd,
            "charged_usd": result.spent_usd,
            "ranking_cost_usd": result.ranking_cost_usd,
            "jobs": [entry["job_id"] for entry in result.videos],
            "stopped_because": result.stopped_because,
            "seconds": result.seconds,
            "ranking": result.ranking,
        }
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "runs.json", ledger)
    package(session, brand, label, out_dir, budget_usd=float(budget_usd))
    return result


def flag_video(session: Session, job_id: str, reason: str) -> Video:
    """A person's review finding: the video goes to needs_review with the reason kept in its QA report."""
    job = session.get(ProductionJob, job_id)
    video = session.get(Video, job.video_id) if job is not None and job.video_id else None
    if video is None:
        raise AppError("No rendered video for that job.")
    report = dict(video.quality_report or {})
    findings = list(report.get("human_findings") or [])
    findings.append({"at": utc_now().isoformat(), "finding": reason.strip()})
    report["human_findings"] = findings
    report["warnings"] = [f"Reviewer: {reason.strip()}", *(report.get("warnings") or [])]
    report["status"] = "needs_review"
    video.quality_report = report
    video.status = "needs_review"
    video.approved = False
    opportunity = session.get(ContentOpportunity, job.opportunity_id)
    if opportunity is not None:
        opportunity.production_status = "needs_review"
        opportunity.production_block_reason = f"Reviewer finding: {reason.strip()}"[:500]
    job.report = {**(job.report or {}), "quality_status": "needs_review"}
    session.flush()
    return video


def _passed(session: Session, job: ProductionJob) -> bool:
    video = session.get(Video, job.video_id) if job.video_id else None
    return job.status == "succeeded" and video is not None and video.status in {"ready", "approved"}


def load_ledger(out_dir: Path) -> list[dict]:
    path = out_dir / "runs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def labelled_jobs(session: Session, brand: Brand, label: str) -> list[ProductionJob]:
    jobs = session.scalars(
        select(ProductionJob).where(ProductionJob.brand_id == brand.id).order_by(ProductionJob.started_at)
    ).all()
    return [job for job in jobs if (job.report or {}).get("run") == label]


# ---------------------------------------------------------------------------------------------- package


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


def _usage_for(session: Session, job: ProductionJob) -> list[dict]:
    """Model calls made while this job ran (jobs in a run are sequential)."""
    end = job.finished_at or utc_now()
    rows = session.scalars(
        select(APIUsage)
        .where(APIUsage.created_at >= job.started_at, APIUsage.created_at <= end)
        .order_by(APIUsage.created_at)
    ).all()
    return [
        {
            "operation": row.operation,
            "model": row.model,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cost_usd": float(row.estimated_cost_usd or 0),
            "cached": row.cached,
            "at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]


def _write(path: Path, data) -> None:
    path.write_text(
        data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def export_job(session: Session, job: ProductionJob, dest: Path, pick: dict | None = None) -> dict:
    """Write one video's review folder. Works for failed jobs too (whatever exists is exported)."""
    dest.mkdir(parents=True, exist_ok=True)
    report = job.report or {}
    opportunity = session.get(ContentOpportunity, job.opportunity_id)
    video = session.get(Video, job.video_id) if job.video_id else None
    version = (
        session.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.script_id == job.script_id)
            .order_by(ScriptVersion.version_number.desc())
        )
        if job.script_id
        else None
    )
    files: dict[str, str] = {}
    if video is not None and video.path and Path(video.path).is_file():
        shutil.copy2(video.path, dest / "video.mp4")
        files["video"] = "video.mp4"
    quality = (video.quality_report if video is not None else None) or report.get("quality") or {}
    sheet = quality.get("contact_sheet")
    if sheet and Path(sheet).is_file():
        shutil.copy2(sheet, dest / "contact-sheet.png")
        files["contact_sheet"] = "contact-sheet.png"
    scenes = report.get("scenes") or []
    if version is not None:
        lines = [f"# {version.title}", "", f"**Hook:** {version.hook}", ""]
        for index, scene in enumerate(scenes or [{"narration": version.narration}]):
            motion = scene.get("motion") or {}
            lines.append(f"## Beat {index + 1}: {motion.get('template', '')}")
            if motion.get("kicker"):
                lines.append(f"*Kicker:* {motion['kicker']}")
            lines.append("")
            lines.append(str(scene.get("narration") or ""))
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(motion.get("params") or {}, ensure_ascii=False, indent=1))
            lines.append("```")
            if motion.get("demoted"):
                lines.append(f"*Validator replaced the planned picture:* {motion['demoted']}")
            lines.append("")
        lines += ["## Claims", ""]
        for claim in version.factual_claims or []:
            if isinstance(claim, dict):
                where = ", ".join(claim.get("sources") or []) or claim.get("source", "")
                lines.append(f"- ({claim.get('certainty', '')}) {claim.get('text', '')} [{where}]")
        lines += [
            "",
            "## Metadata (not published)",
            "",
            f"**Title:** {version.title}",
            "",
            f"**Description:** {version.description}",
            "",
            f"**Hashtags:** {' '.join(version.hashtags or [])}",
        ]
        _write(dest / "script.md", "\n".join(lines) + "\n")
        files["script"] = "script.md"
    _write(dest / "storyboard.json", [scene.get("motion") or {} for scene in scenes])
    dossier = session.get(ResearchDossier, report.get("dossier_id")) if report.get("dossier_id") else None
    urls = list((opportunity.source_urls if opportunity is not None else None) or [])
    _write(
        dest / "sources.json",
        {
            "question": opportunity.question if opportunity is not None else "",
            "urls": urls,
            "retrieved_text": dossier.extracted_text if dossier is not None else "",
        },
    )
    _write(
        dest / "verification.json",
        {
            "drafts": report.get("script_attempts") or [],
            "final_story_review": quality.get("story_review") or {},
            "claims": (version.factual_claims if version is not None else []) or [],
            "note": "Each draft lists the editorial review, the independent fact check, "
            "and the story editor. "
            "A draft only passes when all three pass.",
        },
    )
    _write(dest / "qa.json", quality)
    costs = session.scalars(select(ProductionCost).where(ProductionCost.job_id == job.id)).all()
    usage = _usage_for(session, job)
    cost = {
        "job_total_usd": float(job.cost_usd or 0),
        "model_calls": usage,
        "by_step": [
            {
                "provider": row.provider,
                "operation": row.operation,
                "cost_usd": float(row.estimated_cost_usd or 0),
            }
            for row in costs
        ],
        "rendering_usd": 0.0,
        "voice_usd": 0.0,
        "basis": "Model costs are the token counts each API response reported, multiplied by the per-token "
        "prices configured in app/providers/openai_provider.py. They are not reconciled with an OpenAI "
        "invoice. Kokoro (voice), Remotion (render), and FFmpeg run locally at no API cost.",
    }
    _write(dest / "cost.json", cost)
    checks = quality.get("checks") or []
    summary = {
        "job_id": job.id,
        "title": version.title if version is not None else (opportunity.title if opportunity else ""),
        "topic": opportunity.title if opportunity is not None else "",
        "pick": pick or {},
        "job_status": job.status,
        "video_status": video.status if video is not None else "not rendered",
        "error": job.error or "",
        "stage": job.stage,
        "duration_seconds": float(video.duration_seconds or 0) if video is not None else 0.0,
        "drafts": len(report.get("script_attempts") or []),
        "failed_checks": [c["name"] for c in checks if not c.get("passed") and c.get("required", True)],
        "flagged_checks": [c["name"] for c in checks if not c.get("passed") and not c.get("required", True)],
        "warnings": quality.get("warnings") or report.get("warnings") or [],
        "reviewer_findings": [item.get("finding") for item in quality.get("human_findings") or []],
        "cost_usd": float(job.cost_usd or 0),
        "models": sorted({row["model"] for row in usage if row["model"]}),
        "input_tokens": sum(row["input_tokens"] for row in usage),
        "output_tokens": sum(row["output_tokens"] for row in usage),
        "seconds": (job.finished_at - job.started_at).total_seconds()
        if job.finished_at and job.started_at
        else None,
        "files": files,
        "published": False,
    }
    _write(dest / "summary.json", summary)
    return summary


def package(session: Session, brand: Brand, label: str, out_dir: Path, *, budget_usd: float) -> Path:
    """Write the review folder for every job of this pilot (all runs under the label) and the report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = load_ledger(out_dir)
    summaries = []
    for index, job in enumerate(labelled_jobs(session, brand, label), start=1):
        pick = (job.report or {}).get("pick") or {}
        opportunity = session.get(ContentOpportunity, job.opportunity_id)
        title = pick.get("title") or (opportunity.title if opportunity is not None else job.id)
        folder = out_dir / f"{index:02d}-{_slug(title)}"
        summary = export_job(session, job, folder, pick)
        summary["folder"] = folder.name
        summaries.append(summary)
    ranking = next((run["ranking"] for run in reversed(ledger) if run.get("ranking")), [])
    data = {
        "brand": brand.slug,
        "label": label,
        "started_at": ledger[0]["started_at"] if ledger else "",
        "finished_at": ledger[-1]["finished_at"] if ledger else "",
        "seconds": sum(float(run.get("seconds") or 0) for run in ledger),
        "budget_usd": budget_usd,
        "spent_usd": sum(float(run.get("spent_usd") or 0) for run in ledger),
        "charged_usd": sum(float(run.get("charged_usd", run.get("spent_usd")) or 0) for run in ledger),
        "ranking_cost_usd": sum(float(run.get("ranking_cost_usd") or 0) for run in ledger),
        "stopped_because": ledger[-1]["stopped_because"] if ledger else "",
        "runs": [{key: value for key, value in run.items() if key != "ranking"} for run in ledger],
        "ranking": ranking,
        "videos": summaries,
        "published": False,
    }
    _write(out_dir / "pilot-report.json", data)
    _write(out_dir / "pilot-report.md", render_report(data))
    return out_dir


def render_report(data: dict) -> str:
    videos = data["videos"]
    ready = [v for v in videos if v["video_status"] == "ready"]
    review = [v for v in videos if v["video_status"] == "needs_review"]
    failed = [v for v in videos if v["job_status"] not in {"succeeded", "interrupted"}]
    interrupted = [v for v in videos if v["job_status"] == "interrupted"]
    made = [v for v in videos if v["job_status"] == "succeeded"]
    average = (data["spent_usd"] / len(made)) if made else 0.0
    unrecorded = data.get("charged_usd", data["spent_usd"]) - data["spent_usd"]
    lines = [
        f"# Production run report: {data['brand']}",
        "",
        f"First run started {data['started_at']}, last run finished {data['finished_at']}. "
        f"Time spent running: {data['seconds'] / 60:.1f} minutes over {len(data.get('runs') or [])} run(s).",
        "",
        "## Summary",
        "",
        f"- Videos attempted: {len(videos)}",
        f"- Passed every automated check (`ready`, awaiting your approval): {len(ready)}",
        f"- Rendered but flagged for a person (`needs_review`): {len(review)}",
        f"- Failed (not rendered or blocked): {len(failed)}",
        f"- Interrupted (stopped by the operator, not a pipeline failure): {len(interrupted)}",
        f"- API spend, whole run: ${data['spent_usd']:.4f} of the ${data['budget_usd']:.2f} limit "
        f"(topic ranking ${data['ranking_cost_usd']:.4f}; failed attempts included)",
        f"- Average recorded spend per rendered video: ${average:.4f}",
        f"- Cost per video that passed (all spend, failures included, divided by passed videos): "
        f"${(data['spent_usd'] / len(ready)) if ready else 0:.4f} recorded, "
        f"${(data.get('charged_usd', data['spent_usd']) / len(ready)) if ready else 0:.4f} counted",
        *(
            [
                f"- Counted against the limit but not recorded by the app: up to ${unrecorded:.4f} "
                "(see the run notes below)"
            ]
            if unrecorded > 0
            else []
        ),
        f"- Stopped because: {data['stopped_because']}",
        "- Published: nothing. Publishing is disabled.",
        "",
        "Costs are computed from the token counts the API returned and the configured per-token prices; "
        "they are not reconciled with an invoice. Voice (Kokoro), rendering (Remotion), and mixing (FFmpeg) "
        "run locally and cost $0 in API fees.",
        "",
        "## Videos",
        "",
        "| # | Topic | Status | Length | Drafts | Cost | Flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, video in enumerate(videos, start=1):
        status = (
            video["video_status"]
            if video["job_status"] == "succeeded"
            else "interrupted"
            if video["job_status"] == "interrupted"
            else f"failed at {video['stage']}"
        )
        flags = ", ".join(video["failed_checks"] + video["flagged_checks"]) or "none"
        lines.append(
            f"| {index} | {video['title']} | {status} | {video['duration_seconds']:.1f}s | "
            f"{video['drafts']} | "
            f"${video['cost_usd']:.4f} | {flags} |"
        )
    for index, video in enumerate(videos, start=1):
        pick = video.get("pick") or {}
        lines += [
            "",
            f"### {index}. {video['title']}",
            "",
            f"- Folder: `{video.get('folder', '')}`",
            f"- Topic: {video['topic']} ({pick.get('source', '')}); theme: {pick.get('theme', '')}; "
            f"structure: {pick.get('structure', '')}",
            f"- Why chosen: {pick.get('why', '')} Scores: {pick.get('scores', {})}",
            f"- Status: {video['video_status']}" + (f"; error: {video['error']}" if video["error"] else ""),
            f"- Models: {', '.join(video['models']) or 'none'}; tokens in/out: "
            f"{video['input_tokens']}/{video['output_tokens']}; cost ${video['cost_usd']:.4f}",
            f"- Production time: {video['seconds'] or 0:.0f}s",
        ]
        if video.get("reviewer_findings"):
            lines.append("- Reviewer findings: " + "; ".join(video["reviewer_findings"]))
        if video["warnings"]:
            lines.append("- Warnings: " + "; ".join(str(w) for w in video["warnings"][:6]))
    lines += [
        "",
        "## Topic ranking",
        "",
        "| Topic | Source | Qualified | Total | Theme | Why |",
        "|---|---|---|---|---|---|",
    ]
    for entry in data["ranking"]:
        if "discovery" in entry:
            continue
        lines.append(
            f"| {entry['title'][:70]} | {entry['source']} | {'yes' if entry['qualified'] else 'no'} | "
            f"{entry['total']} | {entry['theme']} | {entry['why'][:120]} |"
        )
    discovery = next((entry["discovery"] for entry in data["ranking"] if "discovery" in entry), None)
    if discovery is not None:
        lines += ["", f"Feed discovery: {discovery}"]
    lines += ["", "## Runs", ""]
    for number, run in enumerate(data.get("runs") or [], start=1):
        lines.append(
            f"{number}. {run.get('started_at', '')}: recorded ${float(run.get('spent_usd') or 0):.4f}, "
            f"counted ${float(run.get('charged_usd', run.get('spent_usd')) or 0):.4f}, "
            f"{len(run.get('jobs') or [])} job(s). {run.get('stopped_because', '')}"
            + (f" Note: {run['note']}" if run.get("note") else "")
        )
    lines += [
        "",
        "## Remaining manual steps",
        "",
        "1. Watch each `ready` and `needs_review` video and read its `script.md` and `verification.json`.",
        "2. Approve the ones you would publish (approval is a person's decision; "
        "nothing is approved automatically).",
        "3. Publishing stays disabled until you decide to enable it in a later phase.",
        "",
    ]
    return "\n".join(lines)
