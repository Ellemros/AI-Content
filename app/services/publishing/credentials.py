"""Platform tokens live in data/secrets/<brand>/<platform>.json, outside Git and outside the database."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings


def path_for(brand_slug: str, platform: str) -> Path:
    account = (
        "youtube" if platform.startswith("youtube") else "meta" if platform.endswith("_reel") else platform
    )
    return Path(get_settings().secrets_dir) / brand_slug / f"{account}.json"


def load(brand_slug: str, platform: str) -> dict | None:
    path = path_for(brand_slug, platform)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def save(brand_slug: str, platform: str, data: dict) -> Path:
    path = path_for(brand_slug, platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
