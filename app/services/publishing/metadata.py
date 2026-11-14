"""Platform-specific titles, descriptions, and tags from one script version.

Each platform gets text inside its own limits. Every description names the
source and says the narration is synthetic, because YouTube and Meta both ask
for disclosure of realistic synthetic media and it keeps the channel honest.
"""

from __future__ import annotations

import re

PLATFORMS = ("youtube_short", "youtube_long", "tiktok", "instagram_reel", "facebook_reel")
SHORT_PLATFORMS = ("youtube_short", "tiktok", "instagram_reel", "facebook_reel")

_LIMITS = {
    "youtube_short": {"title": 100, "description": 5000, "tags": 15},
    "youtube_long": {"title": 100, "description": 5000, "tags": 15},
    "tiktok": {"title": 2200, "description": 0, "tags": 5},
    "instagram_reel": {"title": 0, "description": 2200, "tags": 5},
    "facebook_reel": {"title": 0, "description": 2200, "tags": 3},
}
DISCLOSURE = "Narrated with a synthetic voice and animated by our own software. Sources are listed below."


def _hashtags(raw: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for item in raw or []:
        tag = re.sub(r"[^\w]", "", str(item).lstrip("#"))
        if tag and tag.lower() not in {existing.lower() for existing in seen}:
            seen.append(tag)
    return [f"#{tag}" for tag in seen[:limit]]


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def build(platform: str, *, title: str, description: str, hashtags: list[str], sources: list[str]) -> dict:
    if platform not in _LIMITS:
        raise ValueError(f"Unknown platform {platform}.")
    limits = _LIMITS[platform]
    tags = _hashtags(hashtags, limits["tags"])
    source_lines = "\n".join(f"Source: {url}" for url in sources if url)
    if platform in {"youtube_short", "youtube_long"}:
        shown_title = title
        if platform == "youtube_short" and "#shorts" not in title.lower():
            shown_title = _cut(title, limits["title"] - 8) + " #Shorts"
        body = "\n\n".join(part for part in (description, source_lines, DISCLOSURE, " ".join(tags)) if part)
        return {
            "title": _cut(shown_title, limits["title"]),
            "description": body[: limits["description"]],
            "tags": [tag.lstrip("#") for tag in tags],
        }
    if platform == "tiktok":
        caption = " ".join(
            part for part in (_cut(title, 150), _cut(description, 1500), " ".join(tags)) if part
        )
        return {
            "title": caption[: limits["title"]],
            "description": "",
            "tags": [tag.lstrip("#") for tag in tags],
        }
    caption = "\n\n".join(
        part for part in (title, _cut(description, 1600), source_lines, DISCLOSURE, " ".join(tags)) if part
    )
    return {
        "title": "",
        "description": caption[: limits["description"]],
        "tags": [tag.lstrip("#") for tag in tags],
    }
