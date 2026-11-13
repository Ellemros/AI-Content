"""Original scene illustrations. The narration stays in the subtitles, not on a card."""

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models import Brand
from app.services.fonts import find_font


def brand_palette(brand: Brand) -> list[tuple[int, int, int]]:
    raw = (brand.identity or {}).get("palette")
    parsed = _parse_palette(raw)
    if parsed is not None:
        return parsed
    digest = hashlib.sha256((brand.slug or brand.name or "brand").encode()).digest()
    return [
        _tone(digest[0], digest[1], 28),
        _tone(digest[2], digest[3], 70),
        _tone(digest[4], digest[5], 150),
    ]


def _parse_palette(raw) -> list[tuple[int, int, int]] | None:
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    colors: list[tuple[int, int, int]] = []
    for item in raw[:3]:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            return None
        colors.append(tuple(max(0, min(255, int(channel))) for channel in item[:3]))
    return colors


def _tone(seed: int, mix: int, floor: int) -> tuple[int, int, int]:
    return (
        floor + (seed % 50),
        floor + ((seed * 3 + mix) % 70),
        floor + ((seed * 5 + mix) % 80),
    )


def _font(size: int):
    path, _family = find_font(True)
    return ImageFont.truetype(str(path), size)


def _shift(motifs: list[str]) -> int:
    return hashlib.sha256(" ".join(motifs).encode()).digest()[0]


def draw_scene(
    path: Path,
    *,
    kind: str,
    motifs: list[str],
    palette: list[tuple[int, int, int]],
    size: tuple[int, int] = (1080, 1920),
    photo: Image.Image | None = None,
    credit: str = "",
) -> None:
    width, height = size
    image = Image.new("RGB", size, palette[0])
    draw = ImageDraw.Draw(image)
    if photo is not None:
        image.paste(_cover(photo, size), (0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rectangle((0, height - 420, width, height), fill=(*palette[0], 210))
    else:
        _backdrop(draw, size, palette, kind, _shift(motifs))
    label = "  ".join(motifs[:2])[:28]
    font = _font(54)
    small = _font(28)
    if kind == "flow":
        _flow(draw, size, palette, motifs, font)
    elif kind == "split":
        _split(draw, size, palette, motifs, font)
    elif kind == "marker":
        _marker(draw, size, palette, motifs, font)
    elif kind == "band":
        _band(draw, size, palette, motifs, font)
    else:
        _orbit(draw, size, palette, motifs, font)
    if label:
        draw.text((80, 150), label, font=font, fill=(245, 244, 238))
    if credit:
        draw.text((80, height - 80), credit[:90], font=small, fill=(230, 230, 230))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG")


def _backdrop(draw, size, palette, kind: str, shift: int) -> None:
    width, height = size
    draw.rectangle((0, 0, width, height), fill=palette[0])
    offset = 80 + (shift % 220)
    if kind == "split":
        draw.polygon([(0, 0), (width, 0), (width, height), (0, int(height * 0.72))], fill=palette[1])
        return
    draw.ellipse((offset, 280, width - 40, 1100 + (shift % 180)), fill=palette[1])
    draw.rectangle((0, height - 360, width, height), fill=palette[2])


def _orbit(draw, size, palette, motifs, font) -> None:
    width, _height = size
    cx, cy, radius = width // 2, 860, 280
    draw.ellipse((cx - 90, cy - 90, cx + 90, cy + 90), fill=palette[2])
    for index in range(3):
        angle_x = cx + int((index - 1) * radius * 0.85)
        angle_y = cy - 220 + index * 180
        draw.line((cx, cy, angle_x, angle_y), fill=(245, 244, 238), width=8)
        draw.ellipse((angle_x - 70, angle_y - 70, angle_x + 70, angle_y + 70), fill=palette[2])
        word = motifs[index] if index < len(motifs) else ""
        if word:
            draw.text((angle_x - 60, angle_y + 80), word[:12], font=font, fill=(245, 244, 238))


def _flow(draw, size, palette, motifs, font) -> None:
    width, _height = size
    top = 460
    for index in range(3):
        box = (140, top + index * 340, width - 140, top + 220 + index * 340)
        draw.rounded_rectangle(box, radius=36, fill=palette[2] if index % 2 == 0 else palette[1])
        if index < 2:
            draw.polygon(
                [
                    (width // 2 - 24, box[3] + 20),
                    (width // 2 + 24, box[3] + 20),
                    (width // 2, box[3] + 70),
                ],
                fill=(245, 244, 238),
            )
        word = motifs[index] if index < len(motifs) else ""
        if word:
            draw.text((180, box[1] + 70), word[:16], font=font, fill=(245, 244, 238))


def _split(draw, size, palette, motifs, font) -> None:
    width, height = size
    draw.rectangle((0, 400, width // 2 - 16, height - 200), fill=palette[1])
    draw.rectangle((width // 2 + 16, 400, width, height - 200), fill=palette[2])
    left = motifs[0] if motifs else ""
    right = motifs[1] if len(motifs) > 1 else ""
    if left:
        draw.text((80, 760), left[:14], font=font, fill=(245, 244, 238))
    if right:
        draw.text((width // 2 + 80, 980), right[:14], font=font, fill=(245, 244, 238))


def _marker(draw, size, palette, motifs, font) -> None:
    width, _height = size
    shift = _shift(motifs)
    cx, cy = width // 2, 900
    if shift % 3 == 0:
        draw.polygon([(cx, 520), (cx - 280, 1180), (cx + 280, 1180)], fill=palette[2])
    elif shift % 3 == 1:
        draw.regular_polygon((cx, cy, 300), 6, fill=palette[2])
    else:
        draw.ellipse((cx - 280, cy - 280, cx + 280, cy + 280), fill=palette[2])
    word = motifs[0] if motifs else ""
    if word:
        draw.text((cx - 160, 1280), word[:16], font=font, fill=(245, 244, 238))


def _band(draw, size, palette, motifs, font) -> None:
    width, _height = size
    shift = _shift(motifs)
    for index in range(4):
        top = 420 + index * 280
        inset = 40 + ((shift + index * 30) % 120)
        color = palette[1] if index % 2 == 0 else palette[2]
        draw.rectangle((inset, top, width - inset, top + 160), fill=color)
        word = motifs[index] if index < len(motifs) else ""
        if word:
            draw.text((inset + 40, top + 48), word[:14], font=font, fill=(245, 244, 238))


def _cover(photo: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    frame = photo.convert("RGB")
    scale = max(width / frame.width, height / frame.height)
    resized = frame.resize((int(frame.width * scale), int(frame.height * scale)))
    left = max((resized.width - width) // 2, 0)
    top = max((resized.height - height) // 2, 0)
    return resized.crop((left, top, left + width, top + height))
