from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialize import script_out
from app.core.database import get_db
from app.core.errors import NotFoundError
from app.models import Script, Topic
from app.schemas.content import ScriptEditRequest, ScriptGenerateRequest
from app.services.scripts import (
    approve_script,
    build_edit,
    content_from_version,
    current_version,
    edit_script,
    generate_script,
)
from app.services.translator import localize

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


def _script(session: Session, script_id: str) -> Script:
    script = session.get(Script, script_id)
    if script is None:
        raise NotFoundError("Script was not found.")
    return script


@router.get("")
def list_scripts(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(Script).order_by(Script.created_at.desc())).all()
    return [script_out(row) for row in rows]


@router.post("/generate", status_code=201)
def post_generate(body: ScriptGenerateRequest, session: Session = Depends(get_db)) -> dict:
    topic = session.get(Topic, body.topic_id)
    if topic is None:
        raise NotFoundError("Topic was not found.")
    script = generate_script(
        session,
        topic,
        language=body.language,
        video_format=body.format,
    )
    return script_out(script, current_version(session, script))


@router.get("/{script_id}")
def get_script(script_id: str, session: Session = Depends(get_db)) -> dict:
    script = _script(session, script_id)
    return script_out(script, current_version(session, script))


@router.post("/{script_id}/versions")
def post_version(
    script_id: str,
    body: ScriptEditRequest,
    session: Session = Depends(get_db),
) -> dict:
    script = _script(session, script_id)
    previous = current_version(session, script)
    content = build_edit(
        script,
        title=body.title,
        hook=body.hook,
        narration=body.narration,
        visual_instructions=body.visual_instructions,
        music_mood=body.music_mood,
        cta=body.cta,
        description=body.description,
        hashtags=body.hashtags,
        thumbnail_text=body.thumbnail_text,
        factual_claims=body.factual_claims,
        scenes=body.scenes,
        rebuild_scenes=body.scenes is None,
        offline_draft=previous.offline_draft,
        provider="manual",
        template_key=previous.template_key,
    )
    edit_script(session, script, content)
    return script_out(script, current_version(session, script))


@router.post("/{script_id}/approve")
def post_approve(script_id: str, session: Session = Depends(get_db)) -> dict:
    script = approve_script(session, _script(session, script_id))
    return script_out(script, current_version(session, script))


@router.post("/{script_id}/localize")
def post_localize(
    script_id: str,
    language: str,
    session: Session = Depends(get_db),
) -> dict:
    script = localize(session, _script(session, script_id), language)
    return script_out(script, current_version(session, script))


@router.get("/compare/{topic_id}")
def compare(topic_id: str, video_format: str = "short", session: Session = Depends(get_db)) -> dict:
    rows = session.scalars(
        select(Script).where(Script.topic_id == topic_id, Script.format == video_format)
    ).all()
    payload = {}
    for script in rows:
        version = current_version(session, script)
        payload[script.language] = script_out(script, version)
        payload[script.language]["preview"] = content_from_version(script, version).model_dump()
    return payload
