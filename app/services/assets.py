"""Original graphics, a generated music bed, and the reusable asset library."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import AppError
from app.core.files import sha256_file
from app.models import Asset, Script
from app.models.tables import new_id
from app.services.fonts import find_font
from app.services.probes import MediaError, media_summary, run_ffmpeg

LIBRARY_KINDS = ("image", "clip", "music")
_SUFFIXES = {
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "clip": {".mp4", ".mov", ".webm"},
    "music": {".wav", ".mp3", ".m4a", ".aac"},
}

MOOD_TONES = {
    "calm": (174.61, 220.00),
    "curious": (196.00, 246.94),
    "focused": (164.81, 207.65),
    "hopeful": (220.00, 277.18),
}


def store_asset(
    session: Session,
    *,
    kind: str,
    path: Path,
    source: str,
    license_name: str,
    metadata: dict,
    name: str = "",
    library: bool = False,
) -> Asset:
    asset = Asset(
        kind=kind,
        path=str(path),
        source=source,
        license=license_name,
        checksum=sha256_file(path),
        metadata_json=metadata,
        name=name,
        library=library,
    )
    session.add(asset)
    session.flush()
    return asset


def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    path, _family = find_font(bold)
    return ImageFont.truetype(str(path), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = word if not current else f"{current} {word}"
        if draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def draw_card(
    path: Path,
    *,
    text: str,
    index: int,
    total: int,
    mood: str,
    size: tuple[int, int],
    label: str,
) -> None:
    width, height = size
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    top = (22, 58, 68)
    bottom = (58, 92, 78)
    for y in range(height):
        blend = y / max(height - 1, 1)
        pixels[0, y] = tuple(int(a + (b - a) * blend) for a, b in zip(top, bottom, strict=True))
    image = column.resize((width, height))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 28, height), fill=(224, 164, 90))
    margin = int(width * 0.1)
    label_font = _font(max(28, width // 36), bold=False)
    chip_font = _font(max(24, width // 42), bold=False)
    draw.text((margin, int(height * 0.08)), label.upper(), font=label_font, fill=(232, 220, 196))
    draw.text(
        (margin, int(height * 0.08) + 48),
        f"{index:02d}  /  {total:02d}",
        font=chip_font,
        fill=(224, 164, 90),
    )
    max_width = width - margin * 2
    body_size = 78 if height > width else 64
    lines: list[str] = []
    body_font = _font(body_size, bold=True)
    for candidate in (body_size, 64, 52, 42):
        body_font = _font(candidate, bold=True)
        lines = _wrap(draw, text, body_font, max_width)
        if len(lines) <= 5:
            break
    line_height = body_size + 16
    origin_y = int(height * (0.30 if height > width else 0.28))
    for offset, line in enumerate(lines):
        draw.text((margin, origin_y + offset * line_height), line, font=body_font, fill=(247, 244, 236))
    footer_font = _font(28, bold=False)
    footer = f"original graphic  ·  {mood}"
    draw.text((margin, height - int(height * 0.16)), footer, font=footer_font, fill=(214, 206, 190))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def build_graphics(
    session: Session,
    *,
    scenes: list[dict],
    mood: str,
    label: str,
    size: tuple[int, int],
    work_dir: Path,
) -> list[Asset]:
    assets: list[Asset] = []
    total = len(scenes)
    for scene in scenes:
        path = work_dir / f"scene-{int(scene['index']):02d}.png"
        draw_card(
            path,
            text=str(scene.get("on_screen_text") or " "),
            index=int(scene["index"]),
            total=total,
            mood=mood,
            size=size,
            label=label,
        )
        assets.append(
            store_asset(
                session,
                kind="graphic",
                path=path,
                source="pillow",
                license_name="original",
                metadata={"generator": "pillow", "scene": int(scene["index"]), "original": True},
            )
        )
    return assets


def build_music(session: Session, *, mood: str, duration: float, work_dir: Path) -> Asset:
    low, high = MOOD_TONES.get(mood, MOOD_TONES["curious"])
    path = work_dir / "bed.wav"
    length = max(duration + 0.5, 1.0)
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={low}:sample_rate=48000:duration={length:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={high}:sample_rate=48000:duration={length:.3f}",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2,lowpass=f=700,volume=0.4",
            "-t",
            f"{length:.3f}",
            str(path),
        ]
    )
    return store_asset(
        session,
        kind="music",
        path=path,
        source="ffmpeg-lavfi",
        license_name="original",
        metadata={"generator": "ffmpeg-lavfi", "mood": mood, "original": True},
    )


def library_assets(session: Session) -> list[Asset]:
    return list(
        session.scalars(select(Asset).where(Asset.library.is_(True)).order_by(Asset.created_at.desc()))
    )


def _usable(asset: Asset | None, kinds: set[str]) -> Asset | None:
    if asset is None or not asset.library or asset.kind not in kinds:
        return None
    path = Path(asset.path)
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    return asset


def resolve_visuals(
    session: Session,
    *,
    scenes: list[dict],
    mood: str,
    label: str,
    size: tuple[int, int],
    work_dir: Path,
) -> tuple[list[Asset], list[str]]:
    """Library image or clip when a scene names one. Otherwise a new Pillow card."""
    assets: list[Asset] = []
    kinds: list[str] = []
    total = len(scenes)
    for scene in scenes:
        asset_id = scene.get("asset_id")
        attached = _usable(session.get(Asset, asset_id), {"image", "clip"}) if asset_id else None
        if attached is not None:
            assets.append(attached)
            kinds.append("clip" if attached.kind == "clip" else "image")
            continue
        path = work_dir / f"scene-{int(scene['index']):02d}.png"
        draw_card(
            path,
            text=str(scene.get("on_screen_text") or " "),
            index=int(scene["index"]),
            total=total,
            mood=mood,
            size=size,
            label=label,
        )
        assets.append(
            store_asset(
                session,
                kind="graphic",
                path=path,
                source="pillow",
                license_name="original",
                metadata={"generator": "pillow", "scene": int(scene["index"]), "original": True},
            )
        )
        kinds.append("image")
    return assets, kinds


def resolve_music(
    session: Session,
    *,
    scenes: list[dict],
    mood: str,
    duration: float,
    work_dir: Path,
) -> tuple[Asset, bool]:
    """A library track is looped under the voice. Otherwise the generated bed is used."""
    for scene in scenes:
        music_id = scene.get("music_asset_id")
        attached = _usable(session.get(Asset, music_id), {"music"}) if music_id else None
        if attached is not None:
            return attached, True
    return build_music(session, mood=mood, duration=duration, work_dir=work_dir), False


def register_library_asset(
    session: Session,
    *,
    name: str,
    kind: str,
    source: str,
    license_name: str,
    filename: str,
    data: bytes,
) -> Asset:
    if kind not in LIBRARY_KINDS:
        raise AppError("Library assets are images, clips, or music.")
    if not name.strip() or not source.strip() or not license_name.strip():
        raise AppError("Name, source, and license are required.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _SUFFIXES[kind]:
        allowed = ", ".join(sorted(_SUFFIXES[kind]))
        raise AppError(f"A {kind} file needs one of these extensions: {allowed}.")
    if not data:
        raise AppError("The file is empty.")
    asset_id = new_id()
    path = get_settings().data_dir / "library" / f"{asset_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        _verify_media(kind, path)
    except AppError:
        path.unlink(missing_ok=True)
        raise
    return store_asset(
        session,
        kind=kind,
        path=path,
        source=source.strip()[:160],
        license_name=license_name.strip()[:80],
        metadata={"library": True, "filename": Path(filename).name},
        name=name.strip()[:160],
        library=True,
    )


def attach_scene_asset(
    session: Session,
    script: Script,
    *,
    scene_index: int,
    asset_id: str,
    slot: str,
) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.scripts import current_version

    if slot not in {"visual", "music"}:
        raise AppError("Attach a visual or a music bed.")
    version = current_version(session, script)
    scenes = [dict(item) for item in version.scenes]
    target = next((scene for scene in scenes if int(scene.get("index") or 0) == scene_index), None)
    if target is None:
        raise AppError("That scene was not found.")
    chosen = asset_id.strip()
    if chosen:
        asset = _usable(
            session.get(Asset, chosen),
            {"music"} if slot == "music" else {"image", "clip"},
        )
        if asset is None:
            label = "music" if slot == "music" else "image or clip"
            raise AppError(f"Pick a library {label} that is still on disk.")
    key = "music_asset_id" if slot == "music" else "asset_id"
    target[key] = chosen or None
    version.scenes = scenes
    flag_modified(version, "scenes")
    session.flush()


def _verify_media(kind: str, path: Path) -> None:
    if kind == "image":
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise AppError(f"That file is not a readable image. {exc}") from exc
        return
    try:
        summary = media_summary(path)
    except MediaError as exc:
        raise AppError(f"ffprobe could not read that file. {exc}") from exc
    if kind == "clip" and summary["video_codec"] == "":
        raise AppError("That clip has no video stream.")
    if kind == "music" and summary["audio_codec"] == "":
        raise AppError("That music file has no audio stream.")
    if summary["duration"] <= 0:
        raise AppError("That file has no duration.")
