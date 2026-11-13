"""Production status. Revenue and publishing stay empty."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ContentOpportunity, ProductionJob

router = APIRouter(prefix="/api/production", tags=["production"])


@router.get("/jobs")
def list_jobs(session: Session = Depends(get_db)) -> dict:
    jobs = session.scalars(select(ProductionJob).order_by(ProductionJob.started_at.desc())).all()
    opportunities = session.scalars(select(ContentOpportunity)).all()
    return {
        "published": False,
        "jobs": [
            {
                "id": job.id,
                "brand_id": job.brand_id,
                "opportunity_id": job.opportunity_id,
                "status": job.status,
                "stage": job.stage,
                "error": job.error,
                "cost_usd": float(job.cost_usd or 0),
                "video_id": job.video_id,
                "providers": (job.report or {}).get("providers") or [],
                "published": False,
            }
            for job in jobs
        ],
        "opportunities": [
            {
                "id": row.id,
                "brand_id": row.brand_id,
                "title": row.title,
                "status": row.status,
                "production_status": row.production_status,
                "hold_reason": row.hold_reason,
                "partial_score": None if row.partial_score is None else float(row.partial_score),
            }
            for row in opportunities
        ],
    }
