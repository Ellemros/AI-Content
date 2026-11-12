"""Natural localization. Offline mode uses a paired template, not a word-for-word swap."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.models import Script, Topic
from app.providers.openai_provider import OpenAIProvider
from app.services.offline_scripts import build_offline_script
from app.services.scripts import content_from_version, current_version, save_script

_SYSTEM = (
    "You localize spoken video scripts. Rewrite for a native audience. "
    "Do not translate word for word. Keep the same meaning and the same factual claims. "
    "Do not add facts, numbers, dates, quotations, or sources that are not in the input. "
    "If a claim is unverified, keep it unverified. Return one JSON object with keys "
    "title, hook, narration, visual_instructions, music_mood, cta, description, hashtags, "
    "thumbnail_text, factual_claims. The hook is the first sentence of the narration."
)


def localize(session: Session, script: Script, target_language: str) -> Script:
    if target_language == script.language:
        raise AppError("Choose the other language. This script is already in that language.")
    if target_language not in {"en", "cs"}:
        raise AppError("Language must be en or cs.")
    existing = session.scalar(
        select(Script).where(
            Script.topic_id == script.topic_id,
            Script.language == target_language,
            Script.format == script.format,
        )
    )
    if existing is not None:
        return existing
    topic = session.get(Topic, script.topic_id)
    if topic is None:
        raise AppError("Topic is missing.")
    source = content_from_version(script, current_version(session, script))
    if get_settings().openai_enabled():
        name = "Czech" if target_language == "cs" else "English"
        user = f"Rewrite this script in natural spoken {name}.\n{source.model_dump_json()}"
        provider = OpenAIProvider(session)
        payload = provider.complete_json(operation="localize", system=_SYSTEM, user=user)
        from app.services.scripts import _coerce

        content = _coerce(payload, language=target_language, video_format=script.format, provider="openai")
        content.template_key = source.template_key
    else:
        if not source.template_key:
            raise AppError(
                "Natural localization of a custom script needs OPENAI_API_KEY. "
                "Offline drafts exist only for built-in templates, and they are written "
                "separately for Czech and English rather than translated word for word."
            )
        content = build_offline_script(
            topic.title,
            topic.category,
            target_language,
            script.format,
            topic.description,
            template_key=source.template_key,
        )
    return save_script(session, topic, content, None)
