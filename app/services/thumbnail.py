"""Thumbnail card. A small original graphic, not a designed brand system."""

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Asset
from app.services.assets import draw_card, store_asset


def build_thumbnail(
    session: Session,
    *,
    text: str,
    video_id: str,
    size: tuple[int, int],
    mood: str,
) -> Asset:
    path = Path(get_settings().data_dir) / "thumbnails" / f"{video_id}.png"
    draw_card(
        path,
        text=text,
        index=1,
        total=1,
        mood=mood,
        size=size,
        label="Thumbnail",
    )
    return store_asset(
        session,
        kind="thumbnail",
        path=path,
        source="pillow",
        license_name="original",
        metadata={"generator": "pillow", "role": "thumbnail", "original": True},
    )
