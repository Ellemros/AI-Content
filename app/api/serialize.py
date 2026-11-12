"""JSON shapes for the API."""

from app.models import Job, PublishedPost, PublishingJob, Script, ScriptVersion, Topic, Video


def topic_out(topic: Topic) -> dict:
    return {
        "id": topic.id,
        "channel_id": topic.channel_id,
        "title": topic.title,
        "description": topic.description,
        "category": topic.category,
        "target_language": topic.target_language,
        "target_platform": topic.target_platform,
        "target_audience": topic.target_audience,
        "status": topic.status,
        "priority": topic.priority,
        "estimated_content_potential": topic.estimated_content_potential,
        "source_references": topic.source_references,
        "research_references": topic.research_references,
        "created_at": topic.created_at.isoformat(),
    }


def version_out(version: ScriptVersion) -> dict:
    return {
        "id": version.id,
        "version_number": version.version_number,
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
        "provider": version.provider,
        "template_key": version.template_key,
        "created_at": version.created_at.isoformat(),
    }


def script_out(script: Script, version: ScriptVersion | None = None) -> dict:
    payload = {
        "id": script.id,
        "topic_id": script.topic_id,
        "language": script.language,
        "format": script.format,
        "title": script.title,
        "status": script.status,
        "current_version_id": script.current_version_id,
        "approved_at": script.approved_at.isoformat() if script.approved_at else None,
    }
    if version is not None:
        payload["version"] = version_out(version)
    return payload


def video_out(video: Video, job: Job | None = None) -> dict:
    payload = {
        "id": video.id,
        "script_id": video.script_id,
        "script_version_id": video.script_version_id,
        "audio_file_id": video.audio_file_id,
        "language": video.language,
        "format": video.format,
        "profile": video.profile,
        "status": video.status,
        "width": video.width,
        "height": video.height,
        "duration_seconds": video.duration_seconds,
        "path": video.path,
        "subtitle_path": video.subtitle_path,
        "approved": video.approved,
        "quality_report": video.quality_report,
        "error": video.error,
        "voice_id": video.voice_id,
        "stability": video.stability,
        "similarity": video.similarity,
        "tts_provider": video.tts_provider,
    }
    if job is not None:
        payload["job"] = {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "attempts": job.attempts,
            "component": job.component,
            "error": job.error,
        }
    return payload


def post_out(post: PublishedPost, job: PublishingJob | None = None) -> dict:
    return {
        "id": post.id,
        "video_id": post.video_id,
        "platform": post.platform,
        "local_export_path": post.local_export_path,
        "note": post.note,
        "recorded_at": post.recorded_at.isoformat(),
        "upload": False,
        "job_status": None if job is None else job.status,
    }
