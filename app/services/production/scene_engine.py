"""Procedural stickman scenes directed by the script's storyboard.

Each scene is a composition, not a template: a layout (solo, duo, crowd, chart,
close-up, split, metaphor), a setting, props that carry the idea, a camera move,
an optional emphasis card, and a pose for every character. The storyboard comes
from the script writer; anything missing or invalid is filled deterministically
so a run never fails because of direction. Everything is drawn with Pillow and
encoded with FFmpeg. No external model or asset is used.
"""

from __future__ import annotations

import math
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.probes import MediaError

WIDTH = 1080
HEIGHT = 1920
FPS = 24
GROUND = 1330

LAYOUTS = ("solo", "duo", "crowd", "chart", "closeup", "split", "metaphor")
SETTINGS = ("room", "street", "office", "outdoors", "lab", "night", "board", "stage", "city", "forest")
CAMERAS = ("static", "push_in", "pull_out", "pan_left", "pan_right", "rise")
TRANSITIONS = ("fade", "slideleft", "slideup", "circleopen", "wipeleft", "smoothright", "fadeblack", "radial")
ACTIONS = (
    "stand",
    "walk",
    "run",
    "point",
    "think",
    "surprise",
    "celebrate",
    "explain",
    "shrug",
    "wave",
    "look",
    "slump",
    "push",
    "climb",
    "talk",
    "compare",
    "choose",
    "clock",
    "notice",
)
PROPS = (
    "lightbulb",
    "brain",
    "coins",
    "moneybag",
    "clock",
    "phone",
    "laptop",
    "book",
    "arrow_up",
    "arrow_down",
    "scale",
    "doors",
    "maze",
    "stairs",
    "bird",
    "cup",
    "globe",
    "magnifier",
    "calendar",
    "gear",
    "target",
    "question",
    "exclamation",
    "battery",
    "rocket",
    "house",
    "tree",
    "signpost",
    "trophy",
    "dice",
    "key",
    "lock",
    "chain",
    "cards",
    "screen",
    "dots",
    "speech",
    "heart",
    "plant",
    "cart",
)
# Old single-action names map onto the new vocabulary.
_ACTION_ALIASES = {"clock": "look", "notice": "look", "choose": "point", "compare": "compare"}


@dataclass(frozen=True)
class Style:
    name: str
    paper: tuple[int, int, int]
    ink: tuple[int, int, int]
    accent: tuple[int, int, int]
    accent2: tuple[int, int, int]
    line: tuple[int, int, int]
    soft: tuple[int, int, int]
    card: tuple[int, int, int]


STYLES = {
    "paper": Style(
        "paper",
        (244, 241, 234),
        (28, 28, 28),
        (31, 111, 106),
        (196, 122, 42),
        (214, 208, 198),
        (231, 226, 216),
        (255, 255, 255),
    ),
    "chalk": Style(
        "chalk",
        (36, 46, 44),
        (240, 238, 228),
        (120, 205, 190),
        (240, 186, 92),
        (78, 94, 90),
        (52, 64, 61),
        (60, 74, 70),
    ),
    "blueprint": Style(
        "blueprint",
        (22, 52, 92),
        (232, 241, 250),
        (255, 208, 92),
        (126, 216, 255),
        (60, 96, 140),
        (34, 70, 114),
        (40, 80, 128),
    ),
    "sunset": Style(
        "sunset",
        (252, 236, 222),
        (44, 30, 38),
        (214, 84, 72),
        (64, 116, 168),
        (232, 200, 184),
        (246, 220, 204),
        (255, 250, 246),
    ),
}

_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    from app.services.fonts import find_font

    if size not in _FONTS:
        path, _family = find_font(True)
        _FONTS[size] = ImageFont.truetype(str(path), size)
    return _FONTS[size]


def ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def pop(value: float) -> float:
    """0 → overshoot → 1. Used for things that appear."""
    value = max(0.0, min(1.0, value))
    return 1 + 2.7 * (value - 1) ** 3 + 1.7 * (value - 1) ** 2


# --------------------------------------------------------------------------- camera


@dataclass
class Camera:
    zoom: float = 1.0
    cx: float = WIDTH / 2
    cy: float = HEIGHT / 2

    def at(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.cx) * self.zoom + WIDTH / 2, (y - self.cy) * self.zoom + HEIGHT / 2)

    def w(self, width: float) -> int:
        return max(int(round(width * self.zoom)), 1)


def camera_for(move: str, t: float, focus: tuple[float, float] = (540, 1000)) -> Camera:
    k = ease(t)
    fx, fy = focus
    if move == "push_in":
        return Camera(1.0 + 0.22 * k, 540 + (fx - 540) * 0.5 * k, 960 + (fy - 960) * 0.5 * k)
    if move == "pull_out":
        return Camera(1.25 - 0.25 * k, fx + (540 - fx) * k, fy + (960 - fy) * k)
    if move == "pan_left":
        return Camera(1.12, 620 - 160 * k, 980)
    if move == "pan_right":
        return Camera(1.12, 460 + 160 * k, 980)
    if move == "rise":
        return Camera(1.08, 540, 1060 - 140 * k)
    if move == "close":
        return Camera(1.7, fx, fy)
    return Camera(1.0 + 0.03 * math.sin(t * math.pi), 540, 960)


class Painter:
    """ImageDraw with a camera. World coordinates in, screen pixels out."""

    def __init__(self, image: Image.Image, cam: Camera, style: Style):
        self.image = image
        self.d = ImageDraw.Draw(image)
        self.cam = cam
        self.s = style

    def _p(self, x, y):
        return self.cam.at(x, y)

    def line(self, pts, fill, width=8):
        flat = []
        for x, y in pts:
            flat.extend(self._p(x, y))
        self.d.line(flat, fill=fill, width=self.cam.w(width), joint="curve")

    def circle(self, x, y, r, outline=None, fill=None, width=8):
        cx, cy = self._p(x, y)
        rr = r * self.cam.zoom
        self.d.ellipse(
            (cx - rr, cy - rr, cx + rr, cy + rr), outline=outline, fill=fill, width=self.cam.w(width)
        )

    def rect(self, x0, y0, x1, y1, outline=None, fill=None, width=8, radius=0):
        a = self._p(x0, y0)
        b = self._p(x1, y1)
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        if radius:
            self.d.rounded_rectangle(
                box, radius=radius * self.cam.zoom, outline=outline, fill=fill, width=self.cam.w(width)
            )
        else:
            self.d.rectangle(box, outline=outline, fill=fill, width=self.cam.w(width))

    def poly(self, pts, fill=None, outline=None, width=6):
        screen = [self._p(x, y) for x, y in pts]
        self.d.polygon(screen, fill=fill, outline=outline, width=self.cam.w(width))

    def arc(self, x, y, r, start, end, fill, width=6):
        cx, cy = self._p(x, y)
        rr = r * self.cam.zoom
        self.d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start, end, fill=fill, width=self.cam.w(width))

    def text(self, x, y, text, size, fill, anchor="mm"):
        cx, cy = self._p(x, y)
        self.d.text((cx, cy), text, font=_font(max(int(size * self.cam.zoom), 10)), fill=fill, anchor=anchor)


def _end(origin, angle, length):
    return (origin[0] + math.cos(angle) * length, origin[1] + math.sin(angle) * length)


# --------------------------------------------------------------------------- figure


@dataclass
class Pose:
    # Angles in radians, 0 = pointing right, pi/2 = down. Upper/fore for each arm, thigh/shin for each leg.
    l_up: float = math.pi * 0.62
    l_fore: float = math.pi * 0.58
    r_up: float = math.pi * 0.38
    r_fore: float = math.pi * 0.42
    l_thigh: float = math.pi * 0.54
    l_shin: float = math.pi * 0.52
    r_thigh: float = math.pi * 0.46
    r_shin: float = math.pi * 0.48
    lean: float = 0.0
    lift: float = 0.0
    mood: str = "neutral"  # neutral, happy, sad, wow


def pose_for(action: str, t: float, seed: int = 0) -> Pose:
    action = _ACTION_ALIASES.get(action, action)
    s = math.sin(t * math.tau * 2 + seed)
    c = math.cos(t * math.tau * 2 + seed)
    p = Pose()
    if action in {"walk", "run"}:
        amp = 0.5 if action == "walk" else 0.85
        p.l_thigh = math.pi / 2 + amp * s
        p.r_thigh = math.pi / 2 - amp * s
        p.l_shin = p.l_thigh + 0.25 * max(0, -s)
        p.r_shin = p.r_thigh + 0.25 * max(0, s)
        p.l_up = math.pi / 2 - amp * s * 0.9
        p.r_up = math.pi / 2 + amp * s * 0.9
        p.l_fore = p.l_up - 0.3
        p.r_fore = p.r_up - 0.3
        p.lean = 0.12 if action == "run" else 0.04
        p.lift = abs(s) * (14 if action == "run" else 6)
    elif action == "point":
        p.r_up = -0.25 + 0.04 * s
        p.r_fore = -0.3 + 0.04 * s
        p.l_up = math.pi * 0.62
    elif action == "think":
        p.r_up = 0.95
        p.r_fore = -2.25 + 0.05 * s
        p.l_up = math.pi * 0.6
        p.l_fore = math.pi * 0.35
        p.lean = -0.04
    elif action == "surprise":
        k = abs(math.sin(min(t / 0.3, 1.0) * math.pi))
        p.l_up = math.pi + 0.9 + 0.3 * k
        p.l_fore = math.pi + 1.2
        p.r_up = -0.9 - 0.3 * k
        p.r_fore = -1.2
        p.lift = 70 * k
        p.mood = "wow"
    elif action == "celebrate":
        p.l_up = math.pi + 1.15 + 0.2 * s
        p.l_fore = math.pi + 1.35
        p.r_up = -1.15 + 0.2 * c
        p.r_fore = -1.35
        p.lift = 30 * abs(s)
        p.mood = "happy"
    elif action == "explain":
        p.r_up = 0.2 - 0.45 * abs(s)
        p.r_fore = -0.6 - 0.3 * abs(s)
        p.l_up = math.pi - 0.2 + 0.35 * abs(c)
        p.l_fore = math.pi + 0.5
    elif action == "shrug":
        k = abs(math.sin(t * math.pi * 2))
        p.l_up = math.pi * 0.75
        p.l_fore = math.pi + 0.7 * k
        p.r_up = math.pi * 0.25
        p.r_fore = -0.7 * k
    elif action == "wave":
        p.r_up = -0.9
        p.r_fore = -1.6 + 0.5 * s
        p.mood = "happy"
    elif action == "look":
        p.r_up = -0.5
        p.r_fore = math.pi + 0.9
        p.lean = 0.06 * s
    elif action == "slump":
        p.l_up = math.pi * 0.55
        p.r_up = math.pi * 0.45
        p.l_fore = math.pi * 0.52
        p.r_fore = math.pi * 0.48
        p.lean = 0.18
        p.mood = "sad"
    elif action == "push":
        p.r_up = -0.05
        p.r_fore = -0.02
        p.l_up = 0.1
        p.l_fore = 0.05
        p.lean = 0.3
        p.l_thigh = math.pi / 2 + 0.45 + 0.1 * s
        p.r_thigh = math.pi / 2 - 0.25
    elif action == "climb":
        p.l_thigh = math.pi / 2 - 0.6 * max(0, s)
        p.r_thigh = math.pi / 2 - 0.6 * max(0, -s)
        p.l_shin = math.pi / 2 + 0.2
        p.r_shin = math.pi / 2 + 0.2
        p.r_up = -0.6
        p.r_fore = -0.9
    elif action == "talk":
        p.r_up = 0.3 - 0.4 * abs(s)
        p.r_fore = -0.2 - 0.5 * abs(s)
        p.mood = "happy" if s > 0.6 else "neutral"
    elif action == "compare":
        p.l_up = math.pi - 0.15 + 0.1 * s
        p.l_fore = math.pi - 0.5
        p.r_up = 0.15 - 0.1 * s
        p.r_fore = 0.5
    else:  # stand
        p.r_up = math.pi * 0.38 + 0.05 * s
        p.l_up = math.pi * 0.62 - 0.05 * s
    return p


def draw_figure(
    pt: Painter,
    x: float,
    ground: float,
    pose: Pose,
    *,
    scale: float = 1.0,
    color=None,
    facing: int = 1,
    mark=None,
    head_fill=None,
) -> tuple[float, float]:
    """Draw one figure. Returns the head centre (world)."""
    ink = color or pt.s.ink
    head_r = 58 * scale
    torso = 176 * scale
    upper, lower = 122 * scale, 108 * scale
    thigh, shin = 142 * scale, 132 * scale
    width = 10 * scale

    def a(angle):  # mirror horizontally when facing left
        return angle if facing > 0 else math.pi - angle

    hip = (x, ground - thigh - shin - pose.lift * scale)
    neck = (hip[0] + math.sin(pose.lean * facing) * torso, hip[1] - math.cos(pose.lean) * torso)
    head = (neck[0] + math.sin(pose.lean * facing) * (head_r + 10 * scale), neck[1] - head_r - 10 * scale)
    lk = _end(hip, a(pose.l_thigh), thigh)
    lf = _end(lk, a(pose.l_shin), shin)
    rk = _end(hip, a(pose.r_thigh), thigh)
    rf = _end(rk, a(pose.r_shin), shin)
    le = _end(neck, a(pose.l_up), upper)
    lh = _end(le, a(pose.l_fore), lower)
    re = _end(neck, a(pose.r_up), upper)
    rh = _end(re, a(pose.r_fore), lower)
    for seg in ((hip, neck), (hip, lk, lf), (hip, rk, rf), (neck, le, lh), (neck, re, rh)):
        pt.line(seg, ink, width)
    pt.circle(*head, head_r, outline=ink, fill=head_fill or pt.s.paper, width=width)
    for hand in (lh, rh):
        pt.circle(*hand, 11 * scale, fill=ink, width=1)
    if mark is not None:
        my = neck[1] + 42 * scale
        pt.line(((neck[0] - 18 * scale, my), (neck[0] + 18 * scale, my)), mark, 8 * scale)
    # Face: two dots and a mouth that follows the mood.
    ex = 18 * scale * facing
    pt.circle(head[0] + ex - 8 * scale, head[1] - 8 * scale, 5 * scale, fill=ink, width=1)
    pt.circle(head[0] + ex + 14 * scale, head[1] - 8 * scale, 5 * scale, fill=ink, width=1)
    mx, my = head[0] + ex * 0.6, head[1] + 20 * scale
    if pose.mood == "happy":
        pt.arc(mx, my - 8 * scale, 16 * scale, 20, 160, ink, 5 * scale)
    elif pose.mood == "sad":
        pt.arc(mx, my + 10 * scale, 14 * scale, 200, 340, ink, 5 * scale)
    elif pose.mood == "wow":
        pt.circle(mx, my, 9 * scale, outline=ink, width=5 * scale)
    else:
        pt.line(((mx - 12 * scale, my), (mx + 12 * scale, my)), ink, 5 * scale)
    return head


# --------------------------------------------------------------------------- settings


def draw_setting(pt: Painter, setting: str, t: float, seed: int) -> None:
    s = pt.s
    rng = random.Random(seed)
    pt.line(((-200, GROUND), (WIDTH + 200, GROUND)), s.line, 6)
    if setting == "room":
        wx = rng.choice((110, 760))
        pt.rect(wx, 560, wx + 220, 800, outline=s.line, width=8, radius=16)
        pt.line(((wx + 110, 560), (wx + 110, 800)), s.line, 6)
        pt.line(((wx, 680), (wx + 220, 680)), s.line, 6)
        pt.rect(880 - wx * 0.6, 1180, 980 - wx * 0.6, 1330, outline=s.line, width=6)
    elif setting == "street":
        for i, (bx, h) in enumerate(((-60, 520), (180, 380), (520, 600), (800, 440))):
            pt.rect(bx, GROUND - h, bx + 230, GROUND, outline=s.line, fill=s.soft, width=6)
            for row in range(3):
                for col in range(2):
                    pt.rect(
                        bx + 40 + col * 90,
                        GROUND - h + 60 + row * 110,
                        bx + 90 + col * 90,
                        GROUND - h + 110 + row * 110,
                        outline=s.line,
                        width=4,
                    )
            del i
        pt.line(((960, GROUND), (960, 1020)), s.ink, 6)
        pt.circle(960, 1010, 18, fill=s.accent2, width=1)
    elif setting == "office":
        pt.rect(560, 1150, 1000, 1180, fill=s.line, width=1)
        pt.line(((600, 1180), (600, GROUND)), s.line, 8)
        pt.line(((960, 1180), (960, GROUND)), s.line, 8)
        pt.rect(680, 980, 900, 1130, outline=s.ink, fill=s.card, width=6, radius=8)
        pt.line(((790, 1130), (790, 1150)), s.ink, 6)
        pt.rect(140, 520, 420, 700, outline=s.line, width=6, radius=8)
    elif setting == "outdoors":
        sun_y = 640 - 30 * ease(t)
        pt.circle(860, sun_y, 70, fill=s.accent2, width=1)
        for i in range(3):
            cx = (120 + i * 330 + t * 60) % (WIDTH + 200) - 100
            pt.circle(cx, 520 + i * 40, 36, fill=s.soft, width=1)
            pt.circle(cx + 44, 506 + i * 40, 46, fill=s.soft, width=1)
            pt.circle(cx + 96, 520 + i * 40, 34, fill=s.soft, width=1)
        pt.arc(250, GROUND + 260, 520, 200, 340, s.line, 6)
    elif setting == "lab":
        for i, (fx, h) in enumerate(((760, 120), (860, 180), (960, 140))):
            pt.rect(fx - 30, GROUND - 160 - h, fx + 30, GROUND - 160, outline=s.ink, width=5, radius=10)
            level = 0.5 + 0.2 * math.sin(t * math.tau + i)
            pt.rect(
                fx - 26, GROUND - 160 - h * level, fx + 26, GROUND - 164, fill=s.accent, width=1, radius=8
            )
        pt.rect(700, GROUND - 160, 1020, GROUND - 140, fill=s.line, width=1)
    elif setting == "night":
        for _ in range(26):
            x, y = rng.randint(0, WIDTH), rng.randint(420, 1000)
            twinkle = 4 + 3 * math.sin(t * math.tau * 2 + x)
            pt.circle(x, y, twinkle, fill=s.line, width=1)
        pt.circle(880, 560, 64, fill=s.card, width=1)
        pt.circle(910, 540, 58, fill=s.paper, width=1)
    elif setting == "board":
        pt.rect(90, 470, 990, 1100, outline=s.ink, fill=s.card, width=10, radius=14)
        pt.line(((140, 1100), (140, GROUND)), s.ink, 8)
        pt.line(((940, 1100), (940, GROUND)), s.ink, 8)
    elif setting == "stage":
        pt.poly(((540, 380), (180, GROUND), (900, GROUND)), fill=s.soft)
        pt.rect(0, GROUND, WIDTH, GROUND + 40, fill=s.line, width=1)
    elif setting == "city":
        x = -40
        while x < WIDTH + 40:
            h = rng.randint(260, 640)
            w = rng.randint(90, 170)
            pt.rect(x, GROUND - h, x + w, GROUND, fill=s.soft, outline=s.line, width=4)
            x += w + 10
    elif setting == "forest":
        for i in range(6):
            tx = 60 + i * 190 + rng.randint(-30, 30)
            h = rng.randint(260, 420)
            sway = 6 * math.sin(t * math.tau + i)
            pt.line(((tx, GROUND), (tx, GROUND - h)), s.line, 10)
            pt.circle(tx + sway, GROUND - h - 60, 90, fill=s.soft, outline=s.line, width=5)


# --------------------------------------------------------------------------- props


def draw_prop(pt: Painter, name: str, x: float, y: float, size: float, t: float) -> None:
    s = pt.s
    k = pop(t / 0.35) if t < 0.35 else 1.0
    r = size * max(k, 0.05)
    ink, acc, acc2 = s.ink, s.accent, s.accent2
    w = 7
    if name == "lightbulb":
        glow = 0.5 + 0.5 * math.sin(t * math.tau * 2)
        pt.circle(x, y, r * (1.25 + 0.1 * glow), fill=s.soft, width=1)
        pt.circle(x, y, r, outline=ink, fill=acc2, width=w)
        pt.rect(x - r * 0.35, y + r * 0.85, x + r * 0.35, y + r * 1.25, outline=ink, fill=s.line, width=w)
    elif name == "brain":
        for dx, dy in ((-0.35, -0.2), (0.35, -0.2), (-0.4, 0.25), (0.4, 0.25), (0, -0.45)):
            pt.circle(x + dx * r, y + dy * r, r * 0.45, outline=ink, fill=s.card, width=w)
        pt.line(((x, y - r * 0.7), (x, y + r * 0.6)), ink, 5)
    elif name == "coins":
        for i in range(5):
            cy = y + r * 0.6 - i * r * 0.28 * min(1, t * 3)
            pt.rect(
                x - r * 0.6,
                cy - r * 0.14,
                x + r * 0.6,
                cy + r * 0.14,
                outline=ink,
                fill=acc2,
                width=5,
                radius=10,
            )
    elif name == "moneybag":
        pt.circle(x, y + r * 0.2, r * 0.8, outline=ink, fill=acc2, width=w)
        pt.poly(
            ((x - r * 0.3, y - r * 0.55), (x + r * 0.3, y - r * 0.55), (x, y - r * 0.85)),
            fill=acc2,
            outline=ink,
        )
        pt.text(x, y + r * 0.25, "$", int(r * 0.9), ink)
    elif name == "clock":
        pt.circle(x, y, r, outline=ink, fill=s.card, width=w)
        pt.line(((x, y), _end((x, y), -math.pi / 2 + t * math.tau, r * 0.75)), acc, 7)
        pt.line(((x, y), _end((x, y), -math.pi / 2 + t * math.pi / 3, r * 0.5)), ink, 7)
    elif name in {"phone", "screen", "laptop"}:
        if name == "phone":
            pt.rect(
                x - r * 0.45,
                y - r * 0.8,
                x + r * 0.45,
                y + r * 0.8,
                outline=ink,
                fill=s.card,
                width=w,
                radius=14,
            )
            for i in range(3):
                pt.line(
                    (
                        (x - r * 0.3, y - r * 0.4 + i * r * 0.3),
                        (x + r * 0.25 * ease(t * 3 - i), y - r * 0.4 + i * r * 0.3),
                    ),
                    acc if i == 0 else s.line,
                    8,
                )
        elif name == "laptop":
            pt.rect(x - r, y - r * 0.6, x + r, y + r * 0.3, outline=ink, fill=s.card, width=w, radius=8)
            pt.poly(
                (
                    (x - r * 1.2, y + r * 0.3),
                    (x + r * 1.2, y + r * 0.3),
                    (x + r * 1.35, y + r * 0.45),
                    (x - r * 1.35, y + r * 0.45),
                ),
                fill=s.line,
                outline=ink,
            )
            pt.line(((x - r * 0.7, y - r * 0.2), (x - r * 0.7 + r * 1.2 * ease(t * 2), y - r * 0.2)), acc, 10)
        else:
            pt.rect(
                x - r * 1.2,
                y - r * 0.8,
                x + r * 1.2,
                y + r * 0.8,
                outline=ink,
                fill=s.card,
                width=w,
                radius=10,
            )
            for i in range(3):
                pt.line(
                    (
                        (x - r, y - r * 0.4 + i * r * 0.4),
                        (x - r + 2 * r * ease(t * 2.5 - i * 0.4), y - r * 0.4 + i * r * 0.4),
                    ),
                    acc if i == 0 else s.line,
                    12,
                )
    elif name == "book":
        pt.poly(
            ((x, y - r * 0.5), (x - r, y - r * 0.7), (x - r, y + r * 0.5), (x, y + r * 0.7)),
            fill=s.card,
            outline=ink,
            width=w,
        )
        pt.poly(
            ((x, y - r * 0.5), (x + r, y - r * 0.7), (x + r, y + r * 0.5), (x, y + r * 0.7)),
            fill=s.card,
            outline=ink,
            width=w,
        )
    elif name in {"arrow_up", "arrow_down"}:
        up = name == "arrow_up"
        grow = ease(t * 2)
        y0 = y + r if up else y - r
        y1 = y0 - 2 * r * grow if up else y0 + 2 * r * grow
        pt.line(((x - r * 0.6, y0), (x, (y0 + y1) / 2), (x + r * 0.2, y1)), acc if up else acc2, 14)
        head = -1 if up else 1
        pt.poly(
            ((x + r * 0.2, y1 + head * r * 0.35), (x - r * 0.15, y1 - head * 0.05 * r), (x + r * 0.55, y1)),
            fill=acc if up else acc2,
        )
    elif name == "scale":
        tilt = 0.25 * math.sin(t * math.pi * 1.2)
        pt.line(((x, y + r), (x, y - r * 0.6)), ink, w)
        pt.rect(x - r * 0.5, y + r, x + r * 0.5, y + r * 1.1, fill=ink, width=1)
        left = (x - r * math.cos(tilt), y - r * 0.6 - r * math.sin(tilt))
        right = (x + r * math.cos(tilt), y - r * 0.6 + r * math.sin(tilt))
        pt.line((left, right), ink, w)
        for (px, py), col in ((left, acc), (right, acc2)):
            pt.line(((px, py), (px - r * 0.3, py + r * 0.5), (px + r * 0.3, py + r * 0.5), (px, py)), ink, 4)
            pt.circle(px, py + r * 0.4, r * 0.18, fill=col, width=1)
    elif name == "doors":
        for i, col in enumerate((acc, acc2)):
            dx = x - r * 1.1 + i * r * 1.3
            pt.rect(dx, y - r, dx + r * 0.9, y + r, outline=ink, fill=s.card, width=w, radius=6)
            pt.circle(dx + r * 0.75, y, 8, fill=col, width=1)
            pt.text(dx + r * 0.45, y - r * 0.55, "AB"[i], int(r * 0.4), col)
    elif name == "maze":
        pt.rect(x - r, y - r, x + r, y + r, outline=ink, width=w)
        for i in range(1, 4):
            o = r * (1 - i * 0.25)
            pt.line(((x - o, y - o), (x + o, y - o), (x + o, y + o * 0.5)), ink, 5)
        dot = _end((x, y), t * math.tau, r * 0.5)
        pt.circle(*dot, 12, fill=acc, width=1)
    elif name == "stairs":
        for i in range(5):
            pt.line(
                (
                    (x - r + i * r * 0.45, y + r - i * r * 0.4),
                    (x - r + (i + 1) * r * 0.45, y + r - i * r * 0.4),
                    (x - r + (i + 1) * r * 0.45, y + r - (i + 1) * r * 0.4),
                ),
                ink,
                w,
            )
        pt.circle(x + r * 1.3, y - r * 1.1, 14, fill=acc2, width=1)
    elif name == "bird":
        flap = math.sin(t * math.tau * 4) * 0.5
        bx = x + 40 * math.sin(t * math.tau)
        pt.circle(bx, y, r * 0.42, fill=ink, width=1)
        pt.circle(bx + r * 0.42, y - r * 0.25, r * 0.22, fill=ink, width=1)
        pt.poly(
            ((bx + r * 0.6, y - r * 0.28), (bx + r * 0.85, y - r * 0.2), (bx + r * 0.6, y - r * 0.15)),
            fill=acc2,
        )
        pt.line(((bx - r * 0.1, y - r * 0.1), (bx - r * 0.6, y - r * (0.5 + flap))), ink, 12)
        pt.circle(bx + r * 0.48, y - r * 0.3, 4, fill=s.paper, width=1)
    elif name == "cup":
        pt.poly(
            (
                (x - r * 0.6, y - r * 0.5),
                (x + r * 0.6, y - r * 0.5),
                (x + r * 0.45, y + r * 0.6),
                (x - r * 0.45, y + r * 0.6),
            ),
            fill=s.card,
            outline=ink,
            width=w,
        )
        pt.arc(x + r * 0.62, y, r * 0.28, 270, 90, ink, w)
        for i in range(3):
            sy = y - r * 0.7 - ((t * 120 + i * 40) % 120)
            pt.arc(x - r * 0.3 + i * r * 0.3, sy, r * 0.12, 180, 360, s.line, 5)
    elif name == "globe":
        pt.circle(x, y, r, outline=ink, fill=s.card, width=w)
        spin = math.sin(t * math.tau) * r * 0.4
        pt.arc(x + spin * 0.2, y, r * 0.6, 90, 270, acc, 5)
        pt.line(((x - r, y), (x + r, y)), acc, 5)
        pt.line(((x + spin, y - r * 0.9), (x + spin, y + r * 0.9)), acc, 5)
    elif name == "magnifier":
        mx = x + r * 0.3 * math.sin(t * math.tau)
        pt.circle(mx, y, r * 0.6, outline=ink, fill=s.soft, width=w + 2)
        pt.line(((mx + r * 0.42, y + r * 0.42), (mx + r, y + r)), ink, 16)
    elif name == "calendar":
        pt.rect(x - r, y - r * 0.8, x + r, y + r, outline=ink, fill=s.card, width=w, radius=10)
        pt.rect(x - r, y - r * 0.8, x + r, y - r * 0.4, fill=acc, width=1)
        n = int(1 + t * 6)
        for i in range(min(n, 9)):
            cx = x - r * 0.6 + (i % 3) * r * 0.6
            cy = y - r * 0.1 + (i // 3) * r * 0.35
            pt.line(((cx - 12, cy), (cx - 2, cy + 10), (cx + 14, cy - 10)), acc2, 5)
    elif name == "gear":
        ang = t * math.tau * 0.5
        for i in range(8):
            a = ang + i * math.tau / 8
            pt.line((_end((x, y), a, r * 0.7), _end((x, y), a, r)), ink, 18)
        pt.circle(x, y, r * 0.72, outline=ink, fill=s.card, width=w)
        pt.circle(x, y, r * 0.25, outline=ink, width=w)
    elif name == "target":
        for i, col in enumerate((acc2, s.card, acc2, s.card)):
            pt.circle(x, y, r * (1 - i * 0.25), fill=col, outline=ink, width=4)
        hit = ease(t * 2)
        ax = x + r * 1.8 * (1 - hit)
        pt.line(((ax, y - r * 0.05), (ax + r * 0.9, y - r * 0.3)), ink, 6)
    elif name in {"question", "exclamation"}:
        pt.circle(x, y, r, outline=ink, fill=s.card, width=w)
        pt.text(x, y, "?" if name == "question" else "!", int(r * 1.2), acc if name == "question" else acc2)
    elif name == "battery":
        level = ease(t * 1.5)
        pt.rect(x - r * 0.5, y - r, x + r * 0.5, y + r, outline=ink, width=w, radius=10)
        pt.rect(x - r * 0.2, y - r * 1.12, x + r * 0.2, y - r, fill=ink, width=1)
        pt.rect(x - r * 0.38, y + r * 0.88 - r * 1.76 * level, x + r * 0.38, y + r * 0.88, fill=acc, width=1)
    elif name == "rocket":
        ry = y - 120 * ease(t)
        pt.poly(
            ((x, ry - r), (x - r * 0.35, ry + r * 0.4), (x + r * 0.35, ry + r * 0.4)),
            fill=s.card,
            outline=ink,
            width=w,
        )
        pt.circle(x, ry - r * 0.2, r * 0.13, fill=acc, width=1)
        flame = r * (0.4 + 0.2 * math.sin(t * math.tau * 6))
        pt.poly(
            ((x - r * 0.2, ry + r * 0.4), (x + r * 0.2, ry + r * 0.4), (x, ry + r * 0.4 + flame)), fill=acc2
        )
    elif name == "house":
        pt.rect(x - r * 0.8, y - r * 0.2, x + r * 0.8, y + r, outline=ink, fill=s.card, width=w)
        pt.poly(((x - r, y - r * 0.15), (x, y - r), (x + r, y - r * 0.15)), fill=acc2, outline=ink, width=w)
        pt.rect(x - r * 0.2, y + r * 0.4, x + r * 0.2, y + r, fill=acc, width=1)
    elif name in {"tree", "plant"}:
        grow = ease(t * 1.5) if name == "plant" else 1
        pt.line(((x, y + r), (x, y + r - r * 1.4 * grow)), ink, w + 2)
        pt.circle(
            x, y + r - r * 1.4 * grow - r * 0.3, r * 0.6 * max(grow, 0.1), fill=acc, outline=ink, width=5
        )
    elif name == "signpost":
        pt.line(((x, y + r * 1.2), (x, y - r)), ink, w + 2)
        pt.poly(
            (
                (x, y - r * 0.9),
                (x + r, y - r * 0.9),
                (x + r * 1.2, y - r * 0.7),
                (x + r, y - r * 0.5),
                (x, y - r * 0.5),
            ),
            fill=acc,
            outline=ink,
        )
        pt.poly(
            (
                (x, y - r * 0.3),
                (x - r, y - r * 0.3),
                (x - r * 1.2, y - r * 0.1),
                (x - r, y + r * 0.1),
                (x, y + r * 0.1),
            ),
            fill=acc2,
            outline=ink,
        )
    elif name == "trophy":
        pt.poly(
            ((x - r * 0.6, y - r * 0.8), (x + r * 0.6, y - r * 0.8), (x + r * 0.4, y), (x - r * 0.4, y)),
            fill=acc2,
            outline=ink,
            width=w,
        )
        pt.line(((x, y), (x, y + r * 0.6)), ink, w + 4)
        pt.rect(x - r * 0.45, y + r * 0.6, x + r * 0.45, y + r * 0.8, fill=ink, width=1)
    elif name == "dice":
        rot = math.sin(t * math.tau) * 0.2
        pts = [_end((x, y), rot + math.pi / 4 + i * math.pi / 2, r * 0.9) for i in range(4)]
        pt.poly(pts, fill=s.card, outline=ink, width=w)
        for dx, dy in ((-0.3, -0.3), (0, 0), (0.3, 0.3)):
            pt.circle(x + dx * r, y + dy * r, r * 0.1, fill=ink, width=1)
    elif name in {"key", "lock"}:
        if name == "key":
            pt.circle(x - r * 0.5, y, r * 0.35, outline=acc2, width=w + 2)
            pt.line(((x - r * 0.15, y), (x + r, y), (x + r, y + r * 0.25)), acc2, w + 2)
        else:
            open_ = ease(t * 2 - 0.5)
            pt.arc(x, y - r * 0.3 - open_ * r * 0.3, r * 0.45, 180, 360, ink, w + 2)
            pt.rect(
                x - r * 0.6, y - r * 0.3, x + r * 0.6, y + r * 0.6, fill=acc, outline=ink, width=w, radius=10
            )
    elif name == "chain":
        for i in range(4):
            pt.circle(x - r + i * r * 0.65, y, r * 0.35, outline=ink, width=w)
    elif name == "cards":
        for i, col in enumerate((acc, acc2)):
            lift = 40 * ease(t * 2) if i == 0 else 0
            cx = x - r * 0.7 + i * r * 1.4
            pt.rect(
                cx - r * 0.55,
                y - r * 0.8 - lift,
                cx + r * 0.55,
                y + r * 0.8 - lift,
                outline=col,
                fill=s.card,
                width=w + (3 if i == 0 else 0),
                radius=14,
            )
            pt.line(((cx - r * 0.35, y - r * 0.4 - lift), (cx + r * 0.35, y - r * 0.4 - lift)), col, 6)
    elif name == "dots":
        rng = random.Random(7)
        for _ in range(18):
            px = x + rng.uniform(-r, r) + t * r * 0.8
            py = y + rng.uniform(-r * 0.6, r * 0.6)
            pt.circle(px - (px - x + r) // (2 * r) * 2 * r if px > x + r else px, py, 9, fill=acc, width=1)
        pt.rect(x - r * 1.1, y - r * 0.8, x + r * 1.1, y + r * 0.8, outline=ink, width=5, radius=10)
    elif name == "speech":
        pt.rect(x - r, y - r * 0.6, x + r, y + r * 0.5, outline=ink, fill=s.card, width=w, radius=24)
        pt.poly(
            ((x - r * 0.4, y + r * 0.5), (x - r * 0.1, y + r * 0.5), (x - r * 0.55, y + r * 0.9)),
            fill=s.card,
            outline=ink,
        )
        for i in range(3):
            dot_on = (t * 3 + i * 0.3) % 1 > 0.4
            pt.circle(x - r * 0.4 + i * r * 0.4, y - r * 0.05, 12, fill=acc if dot_on else s.line, width=1)
    elif name == "heart":
        beat = 1 + 0.1 * math.sin(t * math.tau * 3)
        rr = r * beat
        pt.circle(x - rr * 0.35, y - rr * 0.2, rr * 0.45, fill=acc2, width=1)
        pt.circle(x + rr * 0.35, y - rr * 0.2, rr * 0.45, fill=acc2, width=1)
        pt.poly(((x - rr * 0.78, y), (x + rr * 0.78, y), (x, y + rr * 0.8)), fill=acc2)
    elif name == "cart":
        pt.line(
            (
                (x - r * 1.2, y - r * 0.8),
                (x - r * 0.9, y - r * 0.8),
                (x - r * 0.6, y + r * 0.4),
                (x + r * 0.9, y + r * 0.4),
                (x + r * 1.1, y - r * 0.4),
                (x - r * 0.75, y - r * 0.4),
            ),
            ink,
            w,
        )
        pt.circle(x - r * 0.4, y + r * 0.7, r * 0.15, fill=ink, width=1)
        pt.circle(x + r * 0.7, y + r * 0.7, r * 0.15, fill=ink, width=1)


# --------------------------------------------------------------------------- overlays


def _wrap(text: str, size: int, limit: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    font = _font(size)
    for word in words:
        trial = f"{current} {word}".strip()
        if font.getlength(trial) <= limit or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_emphasis(image: Image.Image, text: str, seconds: float, style: Style, *, big: bool = False) -> None:
    """A card that pops in, screen space, above the action. Big is for a number or the hook."""
    if not text:
        return
    d = ImageDraw.Draw(image)
    k = pop(seconds / 0.35)
    size = 150 if big and len(text) <= 8 else (104 if big else 84)
    lines = _wrap(text.upper(), size, WIDTH - 200)
    while len(lines) > 2 and size > 50:
        size -= 10
        lines = _wrap(text.upper(), size, WIDTH - 200)
    font = _font(size)
    heights = size + 18
    total = heights * len(lines)
    top = 330 - total / 2 + (1 - min(k, 1)) * 60
    width = max(font.getlength(line) for line in lines) + 90
    scale = max(k, 0.05)
    box_w = width * scale
    box_h = (total + 50) * scale
    d.rounded_rectangle(
        (
            540 - box_w / 2,
            top + total / 2 + 10 - box_h / 2,
            540 + box_w / 2,
            top + total / 2 + 10 + box_h / 2,
        ),
        radius=28,
        fill=style.card,
        outline=style.ink,
        width=8,
    )
    if k > 0.5:
        for i, line in enumerate(lines):
            d.text(
                (540, top + 22 + i * heights + size / 2),
                line,
                font=font,
                fill=style.accent if big else style.ink,
                anchor="mm",
            )


# --------------------------------------------------------------------------- scene spec


@dataclass
class SceneSpec:
    layout: str = "solo"
    setting: str = "room"
    action: str = "stand"
    props: tuple[str, ...] = ()
    camera: str = "static"
    emphasis: str = ""
    chart: tuple[tuple[str, float], ...] = ()
    other_action: str = "talk"
    transition: str = "fade"
    labels: tuple[str, ...] = ()
    seed: int = 0
    style: str = "paper"


def normalise(direction: dict, index: int, video_seed: int, style: str = "paper") -> SceneSpec:
    """Accept whatever the storyboard says, keep what is valid, fill the rest."""
    rng = random.Random(video_seed * 131 + index)
    layout = str(direction.get("layout") or "").lower()
    if layout not in LAYOUTS:
        layout = LAYOUTS[(video_seed + index) % len(LAYOUTS)]
    setting = str(direction.get("setting") or "").lower()
    if setting not in SETTINGS:
        setting = SETTINGS[(video_seed * 3 + index * 7) % len(SETTINGS)]
    action = str(direction.get("action") or "").lower()
    if action not in ACTIONS:
        action = rng.choice(("point", "think", "explain", "walk", "look", "talk", "shrug"))
    props = tuple(p for p in (str(item).lower() for item in direction.get("props") or []) if p in PROPS)[:2]
    camera = str(direction.get("camera") or "").lower()
    if camera not in CAMERAS:
        camera = CAMERAS[(video_seed + index * 2) % len(CAMERAS)]
    chart: list[tuple[str, float]] = []
    for item in direction.get("chart") or []:
        if isinstance(item, dict):
            try:
                chart.append((str(item.get("label") or "")[:14], float(item.get("value"))))
            except (TypeError, ValueError):
                continue
    if layout == "chart" and len(chart) < 2:
        layout = "metaphor" if props else "solo"
    return SceneSpec(
        layout=layout,
        setting=setting,
        action=action,
        props=props,
        camera=camera,
        emphasis=str(direction.get("emphasis") or "").strip()[:40],
        chart=tuple(chart[:4]),
        labels=tuple(str(item)[:16] for item in (direction.get("labels") or [])[:2]),
        other_action=rng.choice(("talk", "shrug", "look", "think")),
        transition=TRANSITIONS[(video_seed + index * 3) % len(TRANSITIONS)],
        seed=video_seed * 1000 + index,
        style=style if style in STYLES else "paper",
    )


def is_dark(style: str) -> bool:
    paper = STYLES.get(style, STYLES["paper"]).paper
    return (0.2126 * paper[0] + 0.7152 * paper[1] + 0.0722 * paper[2]) < 128


def diversify(specs: list[SceneSpec], max_emphasis: int = 3) -> list[SceneSpec]:
    """No two neighbouring scenes share a layout, a setting, or a camera move.

    At most three emphasis cards per video, numbers first, then the hook, so a card still means something.
    """
    with_card = [i for i, spec in enumerate(specs) if spec.emphasis]
    ranked = sorted(with_card, key=lambda i: (not any(ch.isdigit() for ch in specs[i].emphasis), i != 0, i))
    for i in ranked[max_emphasis:]:
        specs[i].emphasis = ""
    for i in range(1, len(specs)):
        prev, cur = specs[i - 1], specs[i]
        if cur.layout == prev.layout:
            choices = [item for item in LAYOUTS if item not in {prev.layout, "chart"}]
            cur.layout = choices[(cur.seed + i) % len(choices)]
        if cur.setting == prev.setting:
            choices = [item for item in SETTINGS if item != prev.setting]
            cur.setting = choices[(cur.seed * 7 + i) % len(choices)]
        if cur.camera == prev.camera:
            choices = [item for item in CAMERAS if item != prev.camera]
            cur.camera = choices[(cur.seed + i) % len(choices)]
        if cur.transition == prev.transition:
            choices = [item for item in TRANSITIONS if item != prev.transition]
            cur.transition = choices[(cur.seed + i) % len(choices)]
    return specs


# --------------------------------------------------------------------------- composition


def compose(spec: SceneSpec, t: float, seconds: float) -> Image.Image:
    style = STYLES[spec.style]
    image = Image.new("RGB", (WIDTH, HEIGHT), style.paper)
    focus = {"solo": (420, 1000), "duo": (540, 1000), "closeup": (460, 840), "split": (540, 1000)}.get(
        spec.layout, (540, 960)
    )
    move = "close" if spec.layout == "closeup" else spec.camera
    cam = camera_for(move, t, focus)
    if spec.layout == "closeup":
        cam.zoom = 1.55 + 0.12 * ease(t)
    pt = Painter(image, cam, style)
    if spec.layout != "split":
        draw_setting(pt, spec.setting, t, spec.seed)
    pose = pose_for(spec.action, t, spec.seed)
    layout = spec.layout
    props = list(spec.props)
    if layout == "solo":
        x = 360 + (300 * t if spec.action in {"walk", "run"} else 0)
        draw_figure(pt, x, GROUND, pose, mark=style.accent)
        if props:
            draw_prop(pt, props[0], 790, 900, 120, t)
        if len(props) > 1:
            draw_prop(pt, props[1], 820, 1180, 80, max(t - 0.2, 0))
    elif layout == "duo":
        draw_figure(pt, 330, GROUND, pose, mark=style.accent)
        draw_figure(
            pt, 760, GROUND, pose_for(spec.other_action, t, spec.seed + 3), color=style.accent2, facing=-1
        )
        if props:
            draw_prop(pt, props[0], 545, 760, 95, t)
    elif layout == "crowd":
        for i, cx in enumerate((150, 330, 750, 930)):
            p = pose_for(("stand", "look", "talk", "shrug")[i], t, spec.seed + i)
            draw_figure(pt, cx, GROUND + 40, p, scale=0.62, color=style.line)
        draw_figure(pt, 540, GROUND, pose, mark=style.accent)
        if props:
            draw_prop(pt, props[0], 540, 620, 90, t)
    elif layout == "closeup":
        draw_figure(pt, 460, GROUND, pose, mark=style.accent)
        draw_prop(pt, props[0] if props else "question", 640, 660, 80, t)
    elif layout == "split":
        _split(pt, image, spec, t, pose)
    elif layout == "chart":
        _chart(pt, spec, t)
        draw_figure(
            pt,
            200,
            GROUND,
            pose_for("point" if spec.action == "stand" else spec.action, t, spec.seed),
            scale=0.8,
            mark=style.accent,
        )
    elif layout == "metaphor":
        name = props[0] if props else ("stairs" if spec.action == "climb" else "signpost")
        draw_prop(pt, name, 680, 1000, 230, t)
        draw_figure(pt, 300, GROUND, pose, scale=0.9, mark=style.accent)
    draw_emphasis(
        image,
        spec.emphasis,
        seconds,
        style,
        big=bool(spec.emphasis) and any(ch.isdigit() for ch in spec.emphasis),
    )
    return image


def _split(pt: Painter, image: Image.Image, spec: SceneSpec, t: float, pose: Pose) -> None:
    s = pt.s
    d = ImageDraw.Draw(image)
    reveal = ease(t * 3)
    d.rectangle((0, 0, WIDTH // 2, HEIGHT), fill=s.soft)
    d.line((WIDTH // 2, 520, WIDTH // 2, 520 + 900 * reveal), fill=s.ink, width=8)
    left = Pose(mood="sad", lean=0.12, l_up=math.pi * 0.55, r_up=math.pi * 0.45)
    draw_figure(pt, 270, GROUND, left, scale=0.8, color=s.ink)
    right = pose_for(spec.action if spec.action != "stand" else "celebrate", t, spec.seed)
    right.mood = "happy"
    draw_figure(pt, 810, GROUND, right, scale=0.8, mark=s.accent)
    if spec.props:
        draw_prop(pt, spec.props[0], 810, 760, 80, t)
    if len(spec.props) > 1:
        draw_prop(pt, spec.props[1], 270, 760, 80, t)
    if len(spec.labels) == 2:
        for x, label, col in ((270, spec.labels[0], s.ink), (810, spec.labels[1], s.accent)):
            pt.text(x, 640, label.upper(), 60, col)


def _chart(pt: Painter, spec: SceneSpec, t: float) -> None:
    s = pt.s
    values = [value for _label, value in spec.chart]
    top = max(max(values), 1e-6)
    count = len(spec.chart)
    left, right, base, height = 360, 1000, 1180, 560
    pt.line(((left - 20, base), (right, base)), s.ink, 8)
    slot = (right - left) / count
    for i, (label, value) in enumerate(spec.chart):
        grow = ease(t * 2.2 - i * 0.25)
        h = height * (value / top) * grow
        x0 = left + i * slot + slot * 0.18
        x1 = left + (i + 1) * slot - slot * 0.18
        col = s.accent if value == top else s.line
        pt.rect(x0, base - h, x1, base, fill=col, outline=s.ink, width=6)
        if grow > 0.6:
            shown = f"{value:g}"
            pt.text((x0 + x1) / 2, base - h - 40, shown, 52, s.ink)
        pt.text((x0 + x1) / 2, base + 50, label, 40, s.ink)


# --------------------------------------------------------------------------- encode


def render(path: Path, spec: SceneSpec, duration: float) -> None:
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
            "20",
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
            proc.stdin.write(compose(spec, frame / (frames - 1), frame / FPS).tobytes())
    except BrokenPipeError:
        pass
    proc.stdin.close()
    error = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    code = proc.wait()
    if code != 0 or not path.is_file() or path.stat().st_size == 0:
        raise MediaError(error[-800:] or "Scene encode failed.")


# --------------------------------------------------------------------------- sound


def _noise_sweep(rate: int, length: float, rising: bool):
    import numpy as np

    n = int(rate * length)
    noise = np.random.default_rng(3).standard_normal(n)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    # One-pole low-pass whose cutoff sweeps: a whoosh.
    out = np.zeros(n)
    acc = 0.0
    for i in range(n):
        frac = i / n if rising else 1 - i / n
        alpha = 0.02 + 0.25 * frac
        acc += alpha * (noise[i] - acc)
        out[i] = acc
    return out * env * 0.9


def _tone(rate: int, freq: float, length: float, decay: float = 0.05):
    import numpy as np

    t = np.arange(int(rate * length)) / rate
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)


def write_sfx(path: Path, durations: list[float], specs: list[SceneSpec]) -> Path:
    """Whoosh on each cut, a pop when an emphasis card lands, a soft chime on a celebration."""
    import numpy as np
    import soundfile

    rate = 44100
    total = max(sum(durations), 0.5)
    track = np.zeros(int(total * rate) + rate)
    cursor = 0.0
    for index, (duration, spec) in enumerate(zip(durations, specs, strict=True)):
        start = int(cursor * rate)
        if index:
            sweep = _noise_sweep(rate, 0.32, rising=index % 2 == 0) * 0.22
            at = max(start - int(0.16 * rate), 0)
            track[at : at + len(sweep)] += sweep[: len(track) - at]
        if spec.emphasis:
            p = (_tone(rate, 880, 0.18, 0.04) + 0.5 * _tone(rate, 1320, 0.18, 0.03)) * 0.18
            at = start + int(0.12 * rate)
            track[at : at + len(p)] += p[: max(len(track) - at, 0)]
        if spec.action == "celebrate" or spec.layout == "split":
            chime = sum(_tone(rate, f, 0.6, 0.25) for f in (523.25, 659.25, 783.99)) * 0.06
            at = start + int(0.25 * rate)
            track[at : at + len(chime)] += chime[: max(len(track) - at, 0)]
        if "clock" in spec.props:
            for k in range(int(duration * 2)):
                tick = _tone(rate, 2400, 0.03, 0.006) * 0.12
                at = start + int(k * 0.5 * rate)
                track[at : at + len(tick)] += tick[: max(len(track) - at, 0)]
        cursor += duration
    track = np.clip(track[: int(total * rate)], -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(path), track.astype("float32"), rate, subtype="PCM_16")
    return path
