"""Shared vocabulary for topics, scripts, and renders."""

TOPIC_STATUSES = (
    "Idea",
    "Researching",
    "Ready",
    "In production",
    "Published",
    "Rejected",
)
CATEGORIES = ("ai", "technology", "future", "science", "facts")
LANGUAGES = ("en", "cs")
PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels")
FORMATS = ("short", "long")
PROFILE_FOR_FORMAT = {"short": "short_9_16", "long": "long_16_9"}
PROFILE_SIZE = {"short_9_16": (1080, 1920), "long_16_9": (1920, 1080)}
SCRIPT_STATUSES = ("draft", "approved")
VIDEO_STATUSES = (
    "rendering",
    "ready",
    "blocked",
    "failed",
    "interrupted",
    "approved",
    "exported",
)
JOB_STATUSES = ("running", "succeeded", "failed", "interrupted")
RENDER_COMPONENTS = ("all", "audio", "subtitles", "visuals", "render")
TTS_ENGINES = ("auto", "elevenlabs", "edge", "espeak")

CATEGORY_LABELS = {
    "ai": "AI",
    "technology": "Technology",
    "future": "Future trends",
    "science": "Science",
    "facts": "Interesting facts",
}
PLATFORM_LABELS = {
    "youtube_shorts": "YouTube Shorts",
    "tiktok": "TikTok",
    "instagram_reels": "Instagram Reels",
}
LANGUAGE_LABELS = {"en": "English", "cs": "Czech"}
