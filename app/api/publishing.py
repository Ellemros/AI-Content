from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import post_out
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import PublishedPost, PublishingJob, Video
from app.schemas.content import PublishRequest
from app.services.publisher import record_export, refuse_upload

router = APIRouter(prefix="/api/publishing", tags=["publishing"])


@router.get("")
def list_posts(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(PublishedPost).order_by(PublishedPost.recorded_at.desc())).all()
    payload = []
    for row in rows:
        job = session.get(PublishingJob, row.publishing_job_id)
        payload.append(post_out(row, job))
    return payload


@router.post("", status_code=201)
def post_export(body: PublishRequest, session: Session = Depends(get_db)) -> dict:
    video = session.get(Video, body.video_id)
    if video is None:
        raise NotFoundError("Video was not found.")
    if body.upload:
        refuse_upload(session, video, body.platform)
    post = record_export(session, video, body.platform)
    job = session.get(PublishingJob, post.publishing_job_id)
    return post_out(post, job) | {"upload_refused": True, "uploaded": False}
