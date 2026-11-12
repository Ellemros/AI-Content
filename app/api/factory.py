"""Schedule and library API. Scheduled runs never approve or upload."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import ProductionSchedule
from app.services.assets import library_assets, register_library_asset
from app.services.cost import month_spend
from app.services.schedule import create_schedule, launch_schedule, scheduler_enabled

router = APIRouter(prefix="/api/production", tags=["production"])


def _schedule_out(row: ProductionSchedule) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "interval_minutes": row.interval_minutes,
        "language": row.language,
        "topic_id": row.topic_id,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "last_video_id": row.last_video_id,
        "uploads": False,
    }


@router.get("")
def snapshot(session: Session = Depends(get_db)) -> dict:
    rows = session.scalars(select(ProductionSchedule).order_by(ProductionSchedule.name)).all()
    return {
        "scheduler_enabled": scheduler_enabled(session),
        "month_spend_usd": float(month_spend(session)),
        "uploads": False,
        "schedules": [_schedule_out(row) for row in rows],
    }


@router.post("/schedules", status_code=201)
def add_schedule(
    name: str = Form(...),
    interval_minutes: int = Form(1440),
    language: str = Form("en"),
    topic_id: str = Form(""),
    enabled: bool = Form(False),
    session: Session = Depends(get_db),
) -> dict:
    row = create_schedule(
        session,
        name=name,
        interval_minutes=interval_minutes,
        language=language,
        topic_id=topic_id or None,
        enabled=enabled,
    )
    return _schedule_out(row)


@router.post("/schedules/{schedule_id}/run", status_code=202)
def run_schedule(schedule_id: str, session: Session = Depends(get_db)) -> dict:
    row = session.get(ProductionSchedule, schedule_id)
    if row is None:
        raise NotFoundError("Schedule was not found.")
    launch_schedule(schedule_id)
    return {"id": row.id, "started": True, "stops_before_approval": True, "uploads": False}


@router.get("/assets")
def list_assets(session: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "source": asset.source,
            "license": asset.license,
        }
        for asset in library_assets(session)
    ]


@router.post("/assets", status_code=201)
async def add_asset(
    name: str = Form(...),
    kind: str = Form(...),
    source: str = Form(...),
    license_name: str = Form(...),
    upload: UploadFile = File(...),
    session: Session = Depends(get_db),
) -> dict:
    asset = register_library_asset(
        session,
        name=name,
        kind=kind,
        source=source,
        license_name=license_name,
        filename=upload.filename or "",
        data=await upload.read(),
    )
    return {"id": asset.id, "name": asset.name, "kind": asset.kind, "license": asset.license}
