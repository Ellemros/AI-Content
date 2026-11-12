"""Original graphics and a generated music bed. No stock media in this phase."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.core.files import sha256_file
from app.models import Asset
from app.services.fonts import find_font
from app.services.probes import run_ffmpeg

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
) -> Asset:
    asset = Asset(
        kind=kind,
        path=str(path),
        source=source,
        license=license_name,
        checksum=sha256_file(path),
        metadata_json=metadata,
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
