"""Script generation, editing, and approval."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.models import Script, ScriptVersion, Topic
from app.providers.openai_provider import OpenAIProvider
from app.schemas.content import FactualClaim, Scene, ScriptContent
from app.services.cost import usage_scope
from app.services.offline_scripts import build_offline_script
from app.services.scenes import scenes_from_narration
from app.services.textutil import split_sentences

_SYSTEM = (
    "You write spoken scripts for faceless videos about AI, technology, future trends, "
    "science, and interesting facts. Never invent factual claims, statistics, dates, "
    "quotations, or citations. If a specific fact is necessary, put it in factual_claims "
    "with a real http(s) source you can actually support. If you cannot source it, do not "
    "speak it. Return one JSON object with keys title, hook, narration, visual_instructions, "
    "music_mood, cta, description, hashtags, thumbnail_text, factual_claims. "
    "The hook is the opening sentence of narration and must take about 1 to 3 seconds. "
    "Short narration is 30 to 60 seconds of speech. Long narration is 5 to 10 minutes. "
    "hashtags is an array of 3 to 12 tags. factual_claims is an array of "
    "{text, sources, unverified, verified}. Do not include scenes."
)


def current_version(session: Session, script: Script) -> ScriptVersion:
    if not script.current_version_id:
        raise AppError("This script has no version yet.")
    version = session.get(ScriptVersion, script.current_version_id)
    if version is None:
        raise AppError("The current script version is missing.")
    return version


def content_from_version(script: Script, version: ScriptVersion) -> ScriptContent:
    return ScriptContent.model_validate(
        {
            "title": version.title,
            "hook": version.hook,
            "narration": version.narration,
            "scenes": version.scenes,
            "visual_instructions": version.visual_instructions,
            "music_mood": version.music_mood,
            "cta": version.cta,
            "description": version.description,
            "hashtags": version.hashtags,
            "thumbnail_text": version.thumbnail_text,
            "factual_claims": version.factual_claims,
            "offline_draft": version.offline_draft,
            "format": script.format,
            "language": script.language,
            "template_key": version.template_key,
            "provider": version.provider,
        }
    )


def _apply(version: ScriptVersion, content: ScriptContent) -> None:
    version.title = content.title
    version.hook = content.hook
    version.narration = content.narration
    version.scenes = [scene.model_dump() for scene in content.scenes]
    version.visual_instructions = content.visual_instructions
    version.music_mood = content.music_mood
    version.cta = content.cta
    version.description = content.description
    version.hashtags = list(content.hashtags)
    version.thumbnail_text = content.thumbnail_text
    version.factual_claims = [claim.model_dump() for claim in content.factual_claims]
    version.offline_draft = content.offline_draft
    version.provider = content.provider
    version.template_key = content.template_key


def save_script(
    session: Session,
    topic: Topic,
    content: ScriptContent,
    existing: Script | None = None,
) -> Script:
    if existing is None:
        script = Script(
            topic_id=topic.id,
            language=content.language,
            format=content.format,
            title=content.title,
            status="draft",
        )
        session.add(script)
        session.flush()
        number = 1
    else:
        script = existing
        number = (
            session.scalar(
                select(func.max(ScriptVersion.version_number)).where(ScriptVersion.script_id == script.id)
            )
            or 0
        ) + 1
        script.status = "draft"
        script.approved_at = None
        script.title = content.title
    version = ScriptVersion(script_id=script.id, version_number=number)
    _apply(version, content)
    session.add(version)
    session.flush()
    script.current_version_id = version.id
    if topic.status in {"Idea", "Researching"}:
        topic.status = "Ready"
    session.flush()
    return script


def _coerce(payload: dict, *, language: str, video_format: str, provider: str) -> ScriptContent:
    data = dict(payload)
    if isinstance(data.get("hashtags"), str):
        data["hashtags"] = [part.strip() for part in data["hashtags"].split(",") if part.strip()]
    narration = " ".join(split_sentences(str(data.get("narration") or "")))
    visual = str(data.get("visual_instructions") or "Original type cards. No stock footage.")
    data["narration"] = narration
    data["visual_instructions"] = visual
    data["scenes"] = [scene.model_dump() for scene in scenes_from_narration(narration, visual, video_format)]
    data["language"] = language
    data["format"] = video_format
    data["provider"] = provider
    data["offline_draft"] = False
    data.setdefault("factual_claims", [])
    return ScriptContent.model_validate(data)


def _from_openai(session: Session, topic: Topic, language: str, video_format: str) -> ScriptContent:
    language_name = "Czech" if language == "cs" else "English"
    voice_note = (
        "Write in natural spoken Czech for a native audience. Do not translate word for word."
        if language == "cs"
        else "Write in natural spoken English."
    )
    user = (
        f"Language: {language_name}. Format: {video_format}. {voice_note}\n"
        f"Topic title: {topic.title}\n"
        f"Description: {topic.description}\n"
        f"Category: {topic.category}\n"
        f"Audience: {topic.target_audience}\n"
        f"Platform: {topic.target_platform}\n"
        f"Known sources: {topic.source_references}\n"
        "Use a known source only if it is already listed. Do not invent URLs."
    )
    provider = OpenAIProvider(session)
    last_error = "The model returned an empty script."
    prompt = user
    for _ in range(2):
        payload = provider.complete_json(operation="script", system=_SYSTEM, user=prompt)
        try:
            return _coerce(payload, language=language, video_format=video_format, provider="openai")
        except Exception as exc:
            last_error = str(exc)
            prompt = user + "\n\nThe previous JSON failed validation:\n" + last_error[:1500]
    raise AppError(f"OpenAI returned a script that does not match the schema. {last_error}")


def generate_script(
    session: Session,
    topic: Topic,
    *,
    language: str | None = None,
    video_format: str = "short",
    force: bool = False,
) -> Script:
    language = language or topic.target_language
    existing = session.scalar(
        select(Script).where(
            Script.topic_id == topic.id,
            Script.language == language,
            Script.format == video_format,
        )
    )
    if existing is not None and existing.current_version_id and not force:
        return existing
    holder = existing
    if holder is None and get_settings().openai_enabled():
        holder = Script(
            topic_id=topic.id,
            language=language,
            format=video_format,
            title=topic.title[:200],
            status="draft",
        )
        session.add(holder)
        session.flush()
    with usage_scope(script_id=None if holder is None else holder.id):
        if get_settings().openai_enabled():
            content = _from_openai(session, topic, language, video_format)
        else:
            content = build_offline_script(
                topic.title,
                topic.category,
                language,
                video_format,
                topic.description,
            )
    return save_script(session, topic, content, holder)


def edit_script(session: Session, script: Script, content: ScriptContent) -> ScriptVersion:
    if content.language != script.language or content.format != script.format:
        raise AppError("Keep the script language and format. Localize to create the other language.")
    topic = session.get(Topic, script.topic_id)
    if topic is None:
        raise AppError("Topic is missing.")
    save_script(session, topic, content, script)
    version = current_version(session, script)
    return version


def build_edit(
    script: Script,
    *,
    title: str,
    hook: str,
    narration: str,
    visual_instructions: str,
    music_mood: str,
    cta: str,
    description: str,
    hashtags: list[str],
    thumbnail_text: str,
    factual_claims: list[FactualClaim],
    scenes: list[Scene] | None,
    rebuild_scenes: bool,
    offline_draft: bool,
    provider: str,
    template_key: str | None,
) -> ScriptContent:
    narration = " ".join(split_sentences(narration))
    if rebuild_scenes or not scenes:
        scene_models = scenes_from_narration(narration, visual_instructions, script.format)
    else:
        scene_models = scenes
    return ScriptContent(
        title=title.strip(),
        hook=hook.strip(),
        narration=narration,
        scenes=scene_models,
        visual_instructions=visual_instructions.strip(),
        music_mood=music_mood.strip(),
        cta=cta.strip(),
        description=description.strip(),
        hashtags=hashtags,
        thumbnail_text=thumbnail_text.strip(),
        factual_claims=factual_claims,
        offline_draft=offline_draft,
        format=script.format,
        language=script.language,
        template_key=template_key,
        provider=provider,
    )


def approve_script(session: Session, script: Script) -> Script:
    current_version(session, script)
    script.status = "approved"
    from app.core.time import utc_now

    script.approved_at = utc_now()
    session.flush()
    return script
