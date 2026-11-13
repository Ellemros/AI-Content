"""Pexels video search. Official API only. Illustrated cards stay a marked fallback."""

from pathlib import Path

import httpx

from app.config import get_settings
from app.core.errors import AppError

SEARCH_URL = "https://api.pexels.com/videos/search"
_LICENSE = "Pexels License. Free to use. Attribution is appreciated and not required."


def search_videos(query: str, *, client: httpx.Client, per_page: int = 8) -> list[dict]:
    key = get_settings().pexels_api_key.strip()
    if not key:
        raise AppError("PEXELS_API_KEY is empty.")
    response = client.get(
        SEARCH_URL,
        params={
            "query": query[:80],
            "orientation": "portrait",
            "size": "medium",
            "per_page": per_page,
        },
        headers={"Authorization": key},
        timeout=get_settings().research_timeout_seconds,
    )
    if response.status_code == 429:
        raise AppError("Pexels rate limit reached. No further footage was downloaded.")
    if response.status_code >= 400:
        raise AppError(f"Pexels returned HTTP {response.status_code}.")
    payload = response.json()
    videos = payload.get("videos") if isinstance(payload, dict) else None
    return [item for item in videos or [] if isinstance(item, dict)]


def choose_file(video: dict) -> dict | None:
    files = [item for item in video.get("video_files") or [] if isinstance(item, dict)]
    ranked = []
    for item in files:
        link = str(item.get("link") or "")
        if not link.startswith("https://") or "mp4" not in str(item.get("file_type") or "video/mp4"):
            if not link.startswith("https://"):
                continue
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if width < 480 or height < 480:
            continue
        portrait = 1 if height >= width else 0
        tall = 1 if height >= 1080 else 0
        ranked.append((portrait, tall, height * width, item))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[:3], reverse=True)
    return ranked[0][3]


def pick_video(videos: list[dict], *, used_ids: set[str], recent_ids: set[str]) -> dict | None:
    fresh = []
    spare = []
    for video in videos:
        identity = str(video.get("id") or "")
        if not identity or identity in used_ids:
            continue
        chosen = choose_file(video)
        if chosen is None:
            continue
        user = video.get("user") if isinstance(video.get("user"), dict) else {}
        packed = {
            "id": identity,
            "page": str(video.get("url") or ""),
            "duration": int(video.get("duration") or 0),
            "file": chosen,
            "author": str(user.get("name") or ""),
            "author_url": str(user.get("url") or ""),
            "license": _LICENSE,
        }
        if identity in recent_ids:
            spare.append(packed)
        else:
            fresh.append(packed)
    if fresh:
        return fresh[0]
    return spare[0] if spare else None


def cache_path(video_id: str) -> Path:
    directory = get_settings().data_dir / "cache" / "pexels"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{video_id}.mp4"


def download_video(url: str, dest: Path, *, client: httpx.Client) -> Path:
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    response = client.get(url, timeout=60, follow_redirects=True)
    if response.status_code >= 400:
        raise AppError(f"Pexels file returned HTTP {response.status_code}.")
    dest.write_bytes(response.content)
    return dest
