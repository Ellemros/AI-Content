"""Draw the architecture and user-flow diagrams for the YouTube API audit form (PNG, 1600x900)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("docs/youtube-audit")
INK, PAPER, GREEN, GOLD, CORAL, SOFT = "#14213D", "#F4EEE3", "#18A06B", "#F2B134", "#E4573D", "#5B6478"
FONT = "C:/Windows/Fonts/arialbd.ttf"
BODY = "C:/Windows/Fonts/arial.ttf"


def font(size, bold=True):
    return ImageFont.truetype(FONT if bold else BODY, size)


def box(draw, xy, title, lines, fill="#FFFFFF", edge=INK):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=18, fill=fill, outline=edge, width=4)
    draw.text(((x0 + x1) / 2, y0 + 30), title, fill=INK, font=font(26), anchor="mm")
    for i, line in enumerate(lines):
        draw.text(((x0 + x1) / 2, y0 + 70 + i * 30), line, fill=SOFT, font=font(19, False), anchor="mm")


def arrow(draw, a, b, label="", color=INK):
    draw.line([a, b], fill=color, width=5)
    (x0, y0), (x1, y1) = a, b
    import math

    angle = math.atan2(y1 - y0, x1 - x0)
    for side in (-0.45, 0.45):
        draw.line(
            [(x1, y1), (x1 - 22 * math.cos(angle + side), y1 - 22 * math.sin(angle + side))],
            fill=color,
            width=5,
        )
    if label:
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        draw.text((mx, my - 18), label, fill=color, font=font(18, False), anchor="mm")


def architecture():
    img = Image.new("RGB", (1600, 900), PAPER)
    d = ImageDraw.Draw(img)
    d.text((800, 50), "Money Minds Content Tool: architecture", fill=INK, font=font(40), anchor="mm")
    d.text(
        (800, 95),
        "Internal tool on the channel owner's own computer. One user: the channel owner.",
        fill=SOFT,
        font=font(22, False),
        anchor="mm",
    )
    d.rounded_rectangle((40, 140, 1060, 860), radius=26, outline=SOFT, width=3)
    d.text((70, 165), "Owner's computer (local)", fill=SOFT, font=font(22), anchor="lm")
    box(d, (80, 200, 400, 360), "Research", ["public study pages", "Wikipedia, OpenAlex"])
    box(d, (450, 200, 770, 360), "Script + checks", ["writer model,", "independent fact check"])
    box(d, (80, 420, 400, 580), "Narration", ["local text-to-speech", "(Kokoro)"])
    box(d, (450, 420, 770, 580), "Render + QA", ["Remotion animation,", "FFmpeg, automated QA"])
    box(
        d,
        (80, 660, 400, 820),
        "Human review",
        ["owner watches and", "approves each video"],
        fill="#FFF6DF",
        edge=GOLD,
    )
    box(
        d,
        (450, 660, 770, 820),
        "Upload module",
        ["scripts/youtube_upload.py", "videos.insert (resumable)"],
        fill="#E5F6EE",
        edge=GREEN,
    )
    box(d, (820, 420, 1020, 580), "Local store", ["SQLite DB,", "OAuth tokens"])
    arrow(d, (400, 280), (450, 280))
    arrow(d, (610, 360), (240, 420))
    arrow(d, (400, 500), (450, 500))
    arrow(d, (610, 580), (240, 660))
    arrow(d, (400, 740), (450, 740), "approved")
    arrow(d, (770, 680), (840, 580))
    box(
        d,
        (1150, 380, 1540, 560),
        "Google OAuth 2.0",
        ["owner signs in once", "refresh token kept locally"],
        edge=SOFT,
    )
    box(
        d,
        (1150, 620, 1540, 820),
        "YouTube Data API v3",
        ["videos.insert (upload)", "channels/videos.list (read)", "YouTube Analytics (own channel)"],
        fill="#E5F6EE",
        edge=GREEN,
    )
    arrow(d, (770, 700), (1150, 700), "upload own video", GREEN)
    arrow(d, (1150, 790), (990, 580), "own analytics")
    arrow(d, (1020, 470), (1150, 470), "token refresh")
    d.text(
        (1345, 250),
        "No other users.\nNo public sign-up.\nNo third-party data.",
        fill=INK,
        font=font(24),
        anchor="mm",
        align="center",
    )
    img.save(OUT / "architecture-diagram.png")


def flow():
    img = Image.new("RGB", (1600, 900), PAPER)
    d = ImageDraw.Draw(img)
    d.text(
        (800, 50),
        "User flow: from topic to an uploaded video on our own channel",
        fill=INK,
        font=font(38),
        anchor="mm",
    )
    steps = [
        ("1. Connect once", ["owner runs", "connect-youtube,", "signs in with Google"]),
        ("2. Produce", ["tool researches,", "writes, narrates,", "renders a short"]),
        ("3. Automated QA", ["fact check, captions,", "audio, safe area"]),
        ("4. Owner review", ["owner watches the MP4,", "approves or rejects"]),
        ("5. Upload", ["videos.insert,", "1 video per day,", "owner's channel only"]),
        ("6. Analytics", ["views, watch time,", "revenue of own videos"]),
    ]
    x = 40
    for i, (title, lines) in enumerate(steps):
        edge = GOLD if i == 3 else GREEN if i == 4 else INK
        fill = "#FFF6DF" if i == 3 else "#E5F6EE" if i == 4 else "#FFFFFF"
        box(d, (x, 300, x + 225, 520), title, lines, fill=fill, edge=edge)
        if i < len(steps) - 1:
            arrow(d, (x + 225, 410), (x + 262, 410))
        x += 262
    d.line(
        [(40 + 3 * 262 + 112, 520), (40 + 3 * 262 + 112, 640), (40 + 1 * 262 + 112, 640)], fill=CORAL, width=5
    )
    arrow(d, (40 + 1 * 262 + 112, 640), (40 + 1 * 262 + 112, 525), color=CORAL)
    d.text(
        (40 + 2 * 262 + 112, 670),
        "rejected: fixed and produced again, never uploaded",
        fill=CORAL,
        font=font(22, False),
        anchor="mm",
    )
    d.text(
        (800, 790),
        "Nothing is uploaded without the owner's approval. Uploads go only to the owner's own channel.",
        fill=INK,
        font=font(26),
        anchor="mm",
    )
    img.save(OUT / "user-flow-diagram.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    flow()
    print("ok")
