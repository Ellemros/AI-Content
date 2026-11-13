"""Local licensed clips. A file on disk is not a license, and a weak tag match is not a scene."""

import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models import Asset, ProductionJob
from app.models.tables import new_id
from app.services.assets import store_asset
from app.services.fonts import find_font
from app.services.probes import MediaError, media_summary
from app.services.textutil import tokens

SOURCE = "local-stock"
CLIP_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_SLATE = (
    (186, 122, 74),
    (74, 132, 168),
    (92, 150, 112),
    (168, 96, 112),
    (132, 118, 168),
    (150, 138, 78),
)


def stock_root() -> Path:
    path = get_settings().data_dir / "assets" / "stock"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pexels_enabled() -> bool:
    settings = get_settings()
    configured = settings.production_footage.strip().lower() == "pexels"
    return configured and bool(settings.pexels_api_key.strip())


def _category(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return (cleaned or "general")[:40]


def _http_url(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        raise AppError(f"{label} must start with http:// or https://.")
    return cleaned[:300]


def tag_list(*parts: str) -> list[str]:
    found: list[str] = []
    for part in parts:
        for token in sorted(tokens(part)):
            if token not in found:
                found.append(token)
    return found


def scene_tags(scene: dict) -> set[str]:
    motifs = " ".join(str(item) for item in (scene.get("motifs") or []))
    return set(
        tag_list(
            str(scene.get("visual_query") or ""),
            motifs,
            str(scene.get("narration") or ""),
            str(scene.get("on_screen_text") or ""),
        )
    )


def register_stock_clip(
    session: Session,
    *,
    name: str,
    source: str,
    license_name: str,
    filename: str,
    data: bytes,
    tags: str = "",
    category: str = "",
    creator: str = "",
    source_url: str = "",
    license_url: str = "",
    commercial_use: str = "not_recorded",
) -> Asset:
    if not source.strip() or not license_name.strip():
        raise AppError("Source and license name are required. A local file is not a license.")
    if commercial_use not in {"permitted", "not_recorded"}:
        raise AppError("Commercial use must be permitted or not_recorded.")
    parsed_tags = tag_list(tags)
    if not parsed_tags:
        raise AppError("Add at least one tag so a scene can match this clip.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in CLIP_SUFFIXES:
        allowed = ", ".join(sorted(CLIP_SUFFIXES))
        raise AppError(f"A stock clip needs one of these extensions: {allowed}.")
    if not data:
        raise AppError("The file is empty.")
    if len(data) > 200 * 1024 * 1024:
        raise AppError("Stock clips must be under 200 MB.")
    digest = hashlib.sha256(data).hexdigest()
    existing = session.scalar(select(Asset).where(Asset.checksum == digest))
    if existing is not None:
        raise AppError("This file is already registered. The existing asset was left unchanged.")
    folder = _category(category)
    asset_id = new_id()
    path = stock_root() / folder / f"{asset_id}{suffix}"
    if path.exists():
        raise AppError("That asset path already exists. Nothing was overwritten.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        summary = media_summary(path)
    except MediaError as exc:
        path.unlink(missing_ok=True)
        raise AppError(f"ffprobe could not read that clip. {exc}") from exc
    if summary["width"] <= 0 or summary["height"] <= 0 or summary["duration"] <= 0:
        path.unlink(missing_ok=True)
        raise AppError("The file has no usable video stream.")
    if summary["height"] > summary["width"]:
        orientation = "portrait"
    elif summary["width"] > summary["height"]:
        orientation = "landscape"
    else:
        orientation = "square"
    permitted = commercial_use == "permitted"
    metadata = {
        "filename": Path(filename).name,
        "source": source.strip()[:160],
        "source_url": _http_url(source_url, "Source URL"),
        "creator": creator.strip()[:160],
        "license_name": license_name.strip()[:80],
        "license_url": _http_url(license_url, "License URL"),
        "downloaded_on": utc_now().date().isoformat(),
        "tags": parsed_tags,
        "category": folder,
        "duration": summary["duration"],
        "width": summary["width"],
        "height": summary["height"],
        "orientation": orientation,
        "commercial_use": commercial_use,
        "eligible": permitted,
        "use_count": 0,
    }
    label = name.strip() or Path(filename).stem
    return store_asset(
        session,
        kind="clip",
        path=path,
        source=SOURCE,
        license_name=license_name.strip()[:80],
        metadata=metadata,
        name=label[:160],
        library=True,
    )


def _file_ok(asset: Asset) -> bool:
    path = Path(asset.path)
    return path.is_file() and path.stat().st_size > 0


def eligible(asset: Asset) -> bool:
    if not _file_ok(asset) or asset.kind != "clip" or not (asset.license or "").strip():
        return False
    meta = asset.metadata_json or {}
    if asset.source == SOURCE:
        return meta.get("commercial_use") == "permitted" and bool(meta.get("eligible"))
    if asset.source == "pexels":
        return bool(meta.get("pexels_id"))
    return False


def list_stock(session: Session) -> list[Asset]:
    rows = session.scalars(
        select(Asset).where(Asset.source.in_([SOURCE, "pexels"])).order_by(Asset.created_at.desc())
    ).all()
    return [row for row in rows if row.kind == "clip"]


def _asset_tags(asset: Asset) -> set[str]:
    meta = asset.metadata_json or {}
    raw = " ".join(
        [
            " ".join(str(item) for item in (meta.get("tags") or [])),
            str(meta.get("query") or ""),
            str(meta.get("category") or ""),
            asset.name or "",
        ]
    )
    return set(tag_list(raw))


def recent_clip_ids(session: Session, limit: int = 30) -> set[str]:
    jobs = session.scalars(
        select(ProductionJob)
        .where(ProductionJob.status == "succeeded")
        .order_by(ProductionJob.finished_at.desc())
        .limit(limit)
    ).all()
    found: set[str] = set()
    for job in jobs:
        for scene in (job.report or {}).get("scenes") or []:
            footage = scene.get("footage") if isinstance(scene, dict) else None
            if isinstance(footage, dict) and footage.get("asset_id"):
                found.add(str(footage["asset_id"]))
    return found


def choose_clip(session: Session, scene: dict, *, used: set[str], recent: set[str]) -> Asset | None:
    needed = scene_tags(scene)
    if not needed:
        return None
    ranked: list[tuple[int, int, int, Asset]] = []
    for asset in list_stock(session):
        if asset.id in used or not eligible(asset):
            continue
        overlap = needed & _asset_tags(asset)
        if not overlap:
            continue
        meta = asset.metadata_json or {}
        uses = int(meta.get("use_count") or 0)
        height = int(meta.get("height") or 0)
        portrait = 3 if meta.get("orientation") == "portrait" else 0
        tall = 2 if height >= 1080 else (1 if height >= 720 else 0)
        penalty = 4 if asset.id in recent else 0
        score = len(overlap) * 10 + portrait + tall - uses - penalty
        ranked.append((score, -uses, height, asset))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[:3], reverse=True)
    return ranked[0][3]


def mark_used(session: Session, asset: Asset) -> None:
    meta = dict(asset.metadata_json or {})
    meta["use_count"] = int(meta.get("use_count") or 0) + 1
    meta["last_used_at"] = utc_now().isoformat()
    asset.metadata_json = meta
    flag_modified(asset, "metadata_json")
    session.flush()


def footage_record(asset: Asset) -> dict:
    meta = asset.metadata_json or {}
    return {
        "status": "matched",
        "asset_id": asset.id,
        "filename": meta.get("filename") or asset.name,
        "source": asset.source,
        "source_url": meta.get("source_url") or meta.get("page") or "",
        "creator": meta.get("creator") or meta.get("author") or "",
        "license": asset.license,
        "license_url": meta.get("license_url") or "",
        "tags": meta.get("tags") or [],
        "eligible": True,
    }


def missing_record() -> dict:
    return {
        "status": "missing",
        "asset_id": "",
        "filename": "",
        "source": "",
        "source_url": "",
        "creator": "",
        "license": "",
        "license_url": "",
        "tags": [],
        "eligible": False,
    }


def write_missing_slate(path: Path, index: int) -> None:
    color = _SLATE[(index - 1) % len(_SLATE)]
    image = Image.new("RGB", (1080, 1920), color)
    draw = ImageDraw.Draw(image)
    font_path, _family = find_font(False)
    try:
        font = ImageFont.truetype(str(font_path), 54)
    except OSError:
        font = ImageFont.load_default()
    draw.multiline_text(
        (80, 820),
        f"Missing licensed footage\nScene {index}",
        fill=(248, 244, 236),
        font=font,
        spacing=16,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
