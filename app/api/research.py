"""HTTP API for discovery and editorial review. Neither route uploads a video."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import DiscoveredItem, EditorialReview, Script
from app.services.research.discover import promote_item, run_discovery
from app.services.research.editorial import review_script

router = APIRouter(prefix="/api/research", tags=["research"])


class PromoteBody(BaseModel):
    override: bool = False


def _item_out(item: DiscoveredItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "summary": item.summary,
        "summary_kind": item.summary_kind,
        "url": item.url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "category": item.category,
        "keywords": item.keywords,
        "audience": item.audience,
        "horizon": item.horizon,
        "monetization_note": item.monetization_note,
        "source_verified": item.source_verified,
        "source_status": item.source_status,
        "status": item.status,
        "score": item.score,
        "score_breakdown": item.score_breakdown,
        "topic_id": item.topic_id,
        "rejection_reason": item.rejection_reason,
    }


def _review_out(review: EditorialReview) -> dict:
    return {
        "id": review.id,
        "script_id": review.script_id,
        "quality_score": review.quality_score,
        "originality_score": review.originality_score,
        "passed": review.passed,
        "threshold": review.threshold,
        "audience": review.audience,
        "central_question": review.central_question,
        "takeaway": review.takeaway,
        "opening": review.opening,
        "narrative": review.narrative,
        "evidence": review.evidence,
        "conclusion": review.conclusion,
        "issues": review.issues,
    }


@router.get("/items")
def list_items(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(DiscoveredItem).order_by(DiscoveredItem.created_at.desc())).all()
    return [_item_out(row) for row in rows]


@router.post("/discover")
def post_discover(session: Session = Depends(get_db)) -> dict:
    report = run_discovery(session)
    report["failed_sources"] = [{"name": name, "error": error} for name, error in report["failed_sources"]]
    return report


@router.post("/items/{item_id}/promote")
def post_promote(item_id: str, body: PromoteBody, session: Session = Depends(get_db)) -> dict:
    item = session.get(DiscoveredItem, item_id)
    if item is None:
        raise NotFoundError("Discovered item was not found.")
    topic = promote_item(session, item, override=body.override)
    return {"topic_id": topic.id, "item_id": item.id, "status": item.status}


@router.post("/scripts/{script_id}/review")
def post_review(script_id: str, session: Session = Depends(get_db)) -> dict:
    script = session.get(Script, script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    return _review_out(review_script(session, script))
