"""Contiguous scene cards built from narration."""

from app.core.errors import AppError
from app.schemas.content import Scene
from app.services.textutil import card_line, split_sentences


def scenes_from_narration(narration: str, visual: str, video_format: str) -> list[Scene]:
    sentences = split_sentences(narration)
    if not sentences:
        raise AppError("Narration has no sentences.")
    target = 8 if video_format == "long" else 5
    target = min(target, len(sentences))
    groups: list[list[str]] = []
    count = len(sentences)
    for index in range(target):
        start = round(index * count / target)
        end = round((index + 1) * count / target)
        chunk = sentences[start:end]
        if chunk:
            groups.append(chunk)
    scenes: list[Scene] = []
    total = len(groups)
    for index, group in enumerate(groups, start=1):
        text = " ".join(group)
        scenes.append(
            Scene(
                index=index,
                narration=text,
                visual=f"{visual} Scene {index} of {total}.",
                on_screen_text=card_line(group[0]),
            )
        )
    return scenes
