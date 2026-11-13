"""Subtitle cues timed to the narration, written as SRT."""

from dataclasses import dataclass

from app.services.textutil import split_sentences


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


def render_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{cue.index}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{cue.text}\n"
        for cue in cues
    ]
    return "\n".join(blocks)
