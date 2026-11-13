"""Content intelligence pages. Discovery never renders or uploads a video."""

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError, NotFoundError
from app.domain import CATEGORIES, CATEGORY_LABELS
from app.models import DiscoveredItem, ResearchSource
from app.services.research.discover import promote_item, run_discovery
from app.services.research.editorial import review_script
from app.web.views import _redirect, _render, _script

router = APIRouter()


def _source(session: Session, source_id: str) -> ResearchSource:
    source = session.get(ResearchSource, source_id)
    if source is None:
        raise NotFoundError("Source was not found.")
    return source


def _item(session: Session, item_id: str) -> DiscoveredItem:
    item = session.get(DiscoveredItem, item_id)
    if item is None:
        raise NotFoundError("Discovered item was not found.")
    return item


@router.get("/research")
def research_page(request: Request, session: Session = Depends(get_db)):
    sources = session.scalars(select(ResearchSource).order_by(ResearchSource.name)).all()
    items = session.scalars(select(DiscoveredItem).order_by(DiscoveredItem.created_at.desc())).all()
    return _render(
        request,
        "research.html",
        sources=sources,
        items=items[:80],
        categories=CATEGORIES,
        category_labels=CATEGORY_LABELS,
        minimum=get_settings().topic_score_minimum,
        editorial_minimum=get_settings().editorial_quality_minimum,
        gate=get_settings().editorial_gate,
    )


@router.post("/research/sources")
def add_source(
    name: str = Form(...),
    url: str = Form(...),
    category: str = Form("ai"),
    session: Session = Depends(get_db),
):
    cleaned = url.strip()
    if not cleaned.startswith("https://"):
        return _redirect("/research", error="Source URLs must start with https://.")
    if category not in CATEGORIES:
        return _redirect("/research", error="Unknown category.")
    exists = session.scalar(select(ResearchSource).where(ResearchSource.url == cleaned))
    if exists is not None:
        return _redirect("/research", error="That feed is already configured.")
    session.add(
        ResearchSource(
            name=name.strip()[:160] or "RSS feed",
            kind="rss",
            url=cleaned,
            category=category,
            enabled=True,
        )
    )
    return _redirect("/research", message="Source added. Discover when you want to fetch it.")


@router.post("/research/sources/{source_id}/toggle")
def toggle_source(source_id: str, session: Session = Depends(get_db)):
    source = _source(session, source_id)
    source.enabled = not source.enabled
    return _redirect("/research", message=f"{source.name} is {'on' if source.enabled else 'off'}.")


@router.post("/research/discover")
def discover_now(session: Session = Depends(get_db)):
    report = run_discovery(session)
    failed = len(report["failed_sources"])
    message = (
        f"Checked {report['sources']} sources. New items {report['created']}. "
        f"Promoted {report['promoted']}. Duplicates {report['duplicates']}."
    )
    if failed:
        message += f" Failed sources: {failed}."
    return _redirect("/research", message=message)


@router.post("/research/items/{item_id}/promote")
def promote_now(
    item_id: str,
    override: str = Form(""),
    session: Session = Depends(get_db),
):
    item = _item(session, item_id)
    try:
        topic = promote_item(session, item, override=override == "yes")
    except AppError as exc:
        return _redirect("/research", error=exc.message)
    return _redirect(f"/topics/{topic.id}", message="Topic created from the source. No video was rendered.")


@router.post("/scripts/{script_id}/review")
def review_now(script_id: str, session: Session = Depends(get_db)):
    script = _script(session, script_id)
    review = review_script(session, script)
    if review.passed:
        return _redirect(f"/scripts/{script.id}", message=f"Editorial score {review.quality_score}. Passed.")
    return _redirect(
        f"/scripts/{script.id}",
        error=f"Editorial score {review.quality_score} is below {review.threshold}.",
    )
