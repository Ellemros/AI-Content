"""JSON for the brand cycle. Revenue fields stay null."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Brand, ContentOpportunity
from app.services.business.cycle import run_brand_cycle
from app.services.cost import month_spend

router = APIRouter(prefix="/api/business", tags=["business"])


def _opportunity_out(row: ContentOpportunity) -> dict:
    return {
        "id": row.id,
        "brand_id": row.brand_id,
        "title": row.title,
        "partial_score": None if row.partial_score is None else float(row.partial_score),
        "coverage": None if row.coverage is None else float(row.coverage),
        "weight_total": None if row.weight_total is None else float(row.weight_total),
        "signals": row.signals,
        "youtube_sample": row.youtube_sample,
        "status": row.status,
        "hold_reason": row.hold_reason,
        "production_status": row.production_status,
        "advertising_revenue": None,
    }


@router.get("/brands")
def list_brands(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(Brand).order_by(Brand.name)).all()
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "niche": row.niche,
            "language": row.language,
            "status": row.status,
            "publishing_mode": row.publishing_mode,
            "connected_platforms": [],
            "advertising_revenue": None,
        }
        for row in rows
    ]


@router.get("/opportunities")
def list_opportunities(session: Session = Depends(get_db)) -> dict:
    rows = session.scalars(select(ContentOpportunity).order_by(ContentOpportunity.updated_at.desc())).all()
    return {
        "opportunities": [_opportunity_out(row) for row in rows],
        "month_spend_usd": float(month_spend(session)),
        "advertising_revenue": None,
        "profit": None,
    }


@router.post("/cycle/{slug}")
def post_cycle(slug: str, session: Session = Depends(get_db)) -> dict:
    brand = session.scalar(select(Brand).where(Brand.slug == slug))
    if brand is None:
        raise NotFoundError("Brand was not found.")
    run = run_brand_cycle(session, brand)
    return {"status": run.status, "stats": run.stats, "error": run.error}
