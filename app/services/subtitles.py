"""Subtitle cues timed to the narration, written as SRT."""

import re
from dataclasses import dataclass

from app.services.textutil import split_sentences

_STAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


def caption_chunks(text: str, max_chars: int = 84) -> list[str]:
    chunks: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        current = ""
        for word in sentence.split():
            trial = word if not current else f"{current} {word}"
            if len(trial) <= max_chars:
                current = trial
            else:
                if current:
                    chunks.append(current)
                current = word
        if current:
            chunks.append(current)
    return chunks


def _durations(chunks: list[str], total: float, min_cue: float = 0.45) -> list[float]:
    weights = [max(len(chunk), 1) for chunk in chunks]
    raw = [total * weight / sum(weights) for weight in weights]
    raw[-1] = total - sum(raw[:-1])
    if min_cue * len(chunks) >= total:
        return raw
    lifted = [max(item, min_cue) for item in raw]
    overflow = sum(lifted) - total
    if overflow > 0:
        adjustable = [max(item - min_cue, 0) for item in lifted]
        pool = sum(adjustable)
        if pool <= 0:
            return raw
        lifted = [item - overflow * (extra / pool) for item, extra in zip(lifted, adjustable, strict=True)]
    lifted[-1] = total - sum(lifted[:-1])
    if min(lifted) <= 0:
        return raw
    return lifted


def format_timestamp(seconds: float) -> str:
    millis = max(int(round(seconds * 1000)), 0)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wrap(chunk: str, width: int = 42) -> str:
    if len(chunk) <= width:
        return chunk
    midpoint = len(chunk) // 2
    split_at = chunk.rfind(" ", 0, midpoint + 10)
    if split_at < 8:
        return chunk
    return f"{chunk[:split_at]}\n{chunk[split_at + 1 :]}"


def time_cues(text: str, total_seconds: float, max_chars: int = 84) -> list[Cue]:
    if total_seconds <= 0:
        raise ValueError("Duration must be positive.")
    chunks = caption_chunks(text, max_chars=max_chars)
    if not chunks:
        raise ValueError("Narration is empty.")
    durations = _durations(chunks, total_seconds)
    cues: list[Cue] = []
    cursor = 0.0
    last = len(chunks) - 1
    for index, (chunk, duration) in enumerate(zip(chunks, durations, strict=True)):
        start = cursor
        end = total_seconds if index == last else cursor + duration
        cues.append(Cue(index + 1, start, end, _wrap(chunk)))
        cursor = end
    return cues


def cues_from_alignment(alignment: dict, duration: float) -> list[Cue]:
    chars = list(alignment.get("characters") or [])
    starts = list(alignment.get("character_start_times_seconds") or [])
    ends = list(alignment.get("character_end_times_seconds") or [])
    if not chars or len(chars) != len(starts) or len(chars) != len(ends):
        raise ValueError("Alignment is incomplete.")
    phrases: list[tuple[float, float, str]] = []
    buffer = ""
    start = float(starts[0])
    end = float(ends[0])
    for char, raw_start, raw_end in zip(chars, starts, ends, strict=True):
        if buffer and len(buffer) >= 32 and char == " ":
            phrases.append((start, end, buffer.strip()))
            buffer = ""
            start = float(raw_start)
            continue
        if not buffer:
            if char == " ":
                continue
            start = float(raw_start)
        buffer += str(char)
        end = float(raw_end)
        # A cue never runs across the end of a sentence, and a long clause ends at its comma.
        stripped = buffer.strip()
        if (char in ".!?" and len(stripped) >= 6) or (char in ",;:" and len(stripped) >= 18):
            phrases.append((start, end, stripped))
            buffer = ""
    if buffer.strip():
        phrases.append((start, end, buffer.strip()))
    if not phrases:
        raise ValueError("Alignment produced no cues.")
    cues = [
        Cue(index, item_start, max(item_end, item_start + 0.2), _wrap(text, 32))
        for index, (item_start, item_end, text) in enumerate(phrases, start=1)
    ]
    last = cues[-1]
    cues[-1] = Cue(last.index, last.start, max(last.end, duration), last.text)
    return cues


def _ass_time(seconds: float) -> str:
    centi = max(int(round(seconds * 100)), 0)
    hours, rem = divmod(centi, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, hundredths = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{hundredths:02d}"


def _highlight(text: str, accent: str = "&H0060C8FF&", base: str = "&H00FFFFFF&") -> str:
    words = [word for word in text.replace("\n", " ").split() if len(word) >= 5]
    spoken = text.replace("\n", r"\N")
    if not words:
        return spoken
    pick = max(words, key=len)
    return spoken.replace(pick, "{\\c" + accent + "}" + pick + "{\\c" + base + "}", 1)


# Light-paper films (stickman) need dark ink captions; white text vanishes on the paper.
_STYLES = {
    "dark": (
        "Style: Main,DejaVu Sans,64,&H00FFFFFF,&H000000FF,&H00101820,&H64000000,"
        "0,0,0,0,100,100,0,0,1,4,0,2,90,90,320,1",
        "&H0060C8FF&",
        "&H00FFFFFF&",
    ),
    "light": (
        "Style: Main,DejaVu Sans,68,&H001C1C1C,&H000000FF,&H00EAF1F4,&H00EAF1F4,"
        "-1,0,0,0,100,100,0,0,1,6,0,2,90,90,320,1",
        "&H006A6F1F&",
        "&H001C1C1C&",
    ),
}


def render_ass(cues: list[Cue], background: str = "dark") -> str:
    style, accent, base = _STYLES.get(background, _STYLES["dark"])
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Main,,0,0,0,,"
            f"{_highlight(cue.text, accent, base)}"
        )
    return "\n".join(lines) + "\n"


def render_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{cue.index}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{cue.text}\n"
        for cue in cues
    ]
    return "\n".join(blocks)


def _parse_stamp(value: str) -> float:
    match = _STAMP.search(value.strip())
    if match is None:
        raise ValueError(f"Unreadable timestamp: {value.strip()[:40]}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(text: str) -> list[Cue]:
    body = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if body.startswith("\ufeff"):
        body = body.lstrip("\ufeff")
    if not body:
        raise ValueError("Subtitles are empty.")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue
        if "-->" in lines[0]:
            stamp, text_lines = lines[0], lines[1:]
        elif len(lines) >= 2 and "-->" in lines[1]:
            stamp, text_lines = lines[1], lines[2:]
        else:
            raise ValueError("A cue is missing a timestamp.")
        start_raw, end_raw = stamp.split("-->", 1)
        start = _parse_stamp(start_raw)
        end = _parse_stamp(end_raw)
        if end <= start:
            raise ValueError("A cue ends before it starts.")
        caption = "\n".join(line.strip() for line in text_lines).strip()
        if not caption:
            raise ValueError("A cue has no text.")
        cues.append(Cue(len(cues) + 1, start, end, caption))
    if not cues:
        raise ValueError("Subtitles are empty.")
    return cues


def validate_cue_coverage(cues: list[Cue], duration: float) -> None:
    end = cues[-1].end
    if end < duration - 1.5 or end > duration + 1.0:
        raise ValueError(
            f"The last cue ends at {end:.2f}s and the narration is {duration:.2f}s. "
            "Keep that cue within about a second and a half of the end so the burn stays in sync."
        )
