"""Original stickman scenes for Everyday Decisions.

The figure, proportions, and teal chest mark stay the same in every scene.
Motion comes from the narration: a walk, a comparison, a choice, a clock, or a
notice. This is not a still card with a zoom, and it does not call another app.
"""

import math
import struct
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from app.config import get_settings
from app.services.probes import MediaError

WIDTH = 1080
HEIGHT = 1920
FPS = 12
PAPER = (244, 241, 234)
INK = (28, 28, 28)
TEAL = (31, 111, 106)
AMBER = (196, 122, 42)
LINE = (214, 208, 198)
HEAD_R = 58
TORSO = 176
UPPER = 122
LOWER = 108
THIGH = 142
SHIN = 132


def stickman_enabled() -> bool:
    mode = get_settings().production_footage.strip().lower()
    return mode in {"", "stickman"}


def choose_action(narration: str, index: int) -> str:
    text = narration.casefold()
    if any(word in text for word in ("walk", "route", "path", "home")):
        return "walk"
    if any(word in text for word in ("write", "alternative", "comparison", "compare")):
        return "compare"
    if any(word in text for word in ("notice", "attention", "check")):
        return "notice"
    if any(word in text for word in ("default", "option", "choice", "choose", "decision")):
        return "choose"
    if any(word in text for word in ("clock", "habit", "time")):
        return "clock"
    return ("walk", "compare", "choose", "clock", "notice")[(index - 1) % 5]


def render_stickman_scenes(
    scenes: list[dict], durations: list[float], work: Path
) -> tuple[list[Path], list[str], list[str], list[str]]:
    images: list[Path] = []
    asset_ids: list[str] = []
    kinds: list[str] = []
    clip_ids: list[str] = []
    for scene, duration in zip(scenes, durations, strict=True):
        index = int(scene.get("index") or len(images) + 1)
        action = choose_action(str(scene.get("narration") or ""), index)
        path = work / f"scene-{index}-stickman.mp4"
        render_scene(path, action, max(float(duration), 0.8) + 0.45, index)
        scene["visual"] = f"stickman {action}"
        scene["footage"] = {
            "status": "animated",
            "asset_id": "",
            "filename": path.name,
            "source": "stickman",
            "source_url": "",
            "creator": "",
            "license": "Original animation",
            "license_url": "",
            "tags": [action],
            "action": action,
            "eligible": True,
        }
        images.append(path)
        asset_ids.append("")
        kinds.append("stickman")
        clip_ids.append(f"stickman-{index}-{action}")
    return images, asset_ids, kinds, clip_ids


def render_scene(path: Path, action: str, duration: float, index: int) -> None:
    frames = max(int(duration * FPS), 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{WIDTH}x{HEIGHT}",
            "-r",
            str(FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for frame in range(frames):
            proc.stdin.write(_frame(action, frame / (frames - 1), index))
    except BrokenPipeError:
        pass
    proc.stdin.close()
    error = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    code = proc.wait()
    if code != 0 or not path.is_file() or path.stat().st_size == 0:
        raise MediaError(error[-800:] or "Stickman encode failed.")


def write_scene_sfx(path: Path, durations: list[float]) -> Path:
    rate = 44100
    total = max(sum(durations), 0.5)
    count = int(total * rate)
    samples = [0.0] * count
    cursor = 0.0
    for duration in durations:
        start = int(cursor * rate)
        length = int(0.09 * rate)
        for offset in range(length):
            pos = start + offset
            if pos >= count:
                break
            envelope = math.exp(-offset / (0.018 * rate))
            samples[pos] += 0.32 * math.sin(2 * math.pi * 740 * offset / rate) * envelope
            samples[pos] += 0.08 * math.sin(2 * math.pi * 180 * offset / rate) * envelope
        cursor += duration
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(_pcm(sample) for sample in samples))
    return path


def _pcm(sample: float) -> bytes:
    clipped = max(-1.0, min(1.0, sample))
    return struct.pack("<h", int(clipped * 32767))


def _frame(action: str, progress: float, index: int) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    _room(draw)
    _props(draw, action, progress)
    _figure(draw, action, progress, index)
    return image.tobytes()


def _room(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((760, 280, 980, 620), radius=18, outline=LINE, width=8)
    draw.line((870, 280, 870, 620), fill=LINE, width=6)
    draw.line((760, 450, 980, 450), fill=LINE, width=6)
    draw.line((140, 1320, 940, 1320), fill=LINE, width=6)


def _props(draw: ImageDraw.ImageDraw, action: str, progress: float) -> None:
    if action in {"choose", "compare", "notice"}:
        lift = int(48 * progress) if action == "choose" else int(12 * math.sin(progress * math.tau))
        _card(draw, 150, 690 - lift, TEAL, progress > 0.45 and action == "choose")
        _card(draw, 700, 730, AMBER, False)
        if action == "notice":
            radius = 18 + int(14 * abs(math.sin(progress * math.tau)))
            draw.ellipse((250 - radius, 620 - radius, 250 + radius, 620 + radius), outline=TEAL, width=8)
    elif action == "clock":
        center = (760, 760)
        box = (center[0] - 110, center[1] - 110, center[0] + 110, center[1] + 110)
        draw.ellipse(box, outline=INK, width=8)
        angle = -math.pi / 2 + progress * math.tau
        hand = _end(center, angle, 78)
        draw.line((*center, *hand), fill=TEAL, width=8)
    elif action == "walk":
        draw.ellipse((180, 1260, 230, 1310), outline=TEAL, width=8)
        draw.ellipse((820, 1260, 870, 1310), outline=AMBER, width=8)


def _card(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int], chosen: bool) -> None:
    width = 10 if chosen else 6
    draw.rounded_rectangle((x, y, x + 230, y + 300), radius=22, outline=color, width=width)
    draw.line((x + 36, y + 70, x + 190, y + 70), fill=color, width=6)
    draw.line((x + 36, y + 120, x + 160, y + 120), fill=LINE, width=6)


def _figure(draw: ImageDraw.ImageDraw, action: str, progress: float, index: int) -> None:
    swing = math.sin(progress * math.tau * (2 if action == "walk" else 1) + index)
    if action == "walk":
        root_x = 240 + int(560 * progress)
        leg = 0.55 * swing
        left_arm = 0.7 * swing
        right_arm = -0.7 * swing
    elif action == "choose":
        root_x = 620 - int(180 * progress)
        leg = 0.12 * swing
        left_arm = -0.9 - 0.7 * progress
        right_arm = 0.35
    elif action == "compare":
        root_x = 540
        leg = 0.08 * swing
        left_arm = -0.85 + 0.15 * swing
        right_arm = 0.85 - 0.15 * swing
    elif action == "clock":
        root_x = 460
        leg = 0.05 * swing
        left_arm = -1.15
        right_arm = 0.2 * swing
    else:
        root_x = 560 + int(24 * swing)
        leg = 0.1 * swing
        left_arm = -0.45 - 0.25 * abs(swing)
        right_arm = 0.25
    hip = (root_x, 1080)
    neck = (hip[0], hip[1] - TORSO)
    head = (neck[0], neck[1] - HEAD_R - 10)
    left_elbow = _end(neck, math.pi + left_arm, UPPER)
    left_hand = _end(left_elbow, math.pi + left_arm * 0.8, LOWER)
    right_elbow = _end(neck, right_arm, UPPER)
    right_hand = _end(right_elbow, right_arm * 0.8, LOWER)
    left_knee = _end(hip, math.pi / 2 + leg, THIGH)
    left_foot = _end(left_knee, math.pi / 2 + leg * 0.35, SHIN)
    right_knee = _end(hip, math.pi / 2 - leg, THIGH)
    right_foot = _end(right_knee, math.pi / 2 - leg * 0.35, SHIN)
    draw.line((*hip, *neck), fill=INK, width=10)
    draw.line((*neck, *left_elbow), fill=INK, width=10)
    draw.line((*left_elbow, *left_hand), fill=INK, width=10)
    draw.line((*neck, *right_elbow), fill=INK, width=10)
    draw.line((*right_elbow, *right_hand), fill=INK, width=10)
    draw.line((*hip, *left_knee), fill=INK, width=10)
    draw.line((*left_knee, *left_foot), fill=INK, width=10)
    draw.line((*hip, *right_knee), fill=INK, width=10)
    draw.line((*right_knee, *right_foot), fill=INK, width=10)
    draw.ellipse(
        (head[0] - HEAD_R, head[1] - HEAD_R, head[0] + HEAD_R, head[1] + HEAD_R),
        outline=INK,
        width=10,
    )
    mark_y = neck[1] + 42
    draw.line((neck[0] - 18, mark_y, neck[0] + 18, mark_y), fill=TEAL, width=8)
    for point in (left_hand, right_hand):
        draw.ellipse((point[0] - 11, point[1] - 11, point[0] + 11, point[1] + 11), fill=INK)
    smile = progress > 0.62 and action == "choose"
    box = (head[0] - 16, head[1] + 8, head[0] + 16, head[1] + 28)
    draw.arc(box, 20 if smile else 200, 160 if smile else 340, fill=INK, width=4)


def _end(origin: tuple[int, int], angle: float, length: int) -> tuple[int, int]:
    return (int(origin[0] + math.cos(angle) * length), int(origin[1] + math.sin(angle) * length))
