"""Original narration grounded in a retrieved page, with an explicit dossier fallback.

The dossier compiler stitches source sentences. It is not an original script.
A model script is kept only when it is spoken language, grounded, and not a copy.
"""

import re

from app.core.errors import AppError
from app.services.textutil import split_sentences, tokens

_BANNED = (
    "in today's world",
    "let's dive in",
    "in this video",
    "have you ever wondered",
    "buckle up",
    "game changer",
    "delve",
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_FRAMES = {
    "en": (
        "{title} starts with a detail that is easy to miss. {sentence}",
        "The source supports this, and nothing beyond it. {sentence}",
    ),
    "cs": (
        "{title} začíná detailem, který je snadné minout. {sentence}",
        "Zdroj podporuje tohle a nic dalšího. {sentence}",
    ),
}
_KINDS = ("orbit", "flow", "split", "marker", "band")


def _usable(text: str) -> list[str]:
    return [sentence for sentence in split_sentences(text) if len(sentence.split()) >= 8]


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def _grounded(narration: str, extracted: str, title: str) -> None:
    shared = tokens(narration) & tokens(extracted)
    if len(shared) < 6:
        raise AppError("Model narration is not grounded in the source page.")
    if tokens(title) and not (tokens(title) & tokens(narration)):
        raise AppError("Model narration does not mention the topic.")
    invented = _numbers(narration) - _numbers(extracted) - _numbers(title)
    if invented:
        raise AppError("The script adds a number that is not in the source.")
    folded = narration.casefold()
    for sentence in split_sentences(extracted):
        if len(sentence.split()) >= 10 and sentence.casefold() in folded:
            raise AppError("The script copies a source sentence instead of writing an original narration.")
    for phrase in _BANNED:
        if phrase in folded:
            raise AppError(f"The script uses a generic phrase ({phrase}).")


def compile_from_dossier(*, title: str, extracted: str, url: str, language: str) -> dict:
    chosen = _usable(extracted)[:5]
    if len(chosen) < 3:
        raise AppError("The source page does not contain enough sentences to write a topic-specific script.")
    hook_frame, close_frame = _FRAMES.get(language, ("{title}. {sentence}", "{sentence}"))
    subject = title.rstrip(".")
    beats = [
        hook_frame.format(title=subject, sentence=chosen[0]),
        chosen[1],
        chosen[2],
        chosen[3] if len(chosen) > 3 else chosen[2],
        close_frame.format(title=subject, sentence=chosen[-1]),
    ]
    claims = [
        {"text": sentence, "sources": [url], "verified": True, "unverified": False} for sentence in chosen
    ]
    tags = [f"#{word}" for word in list(tokens(title))[:4]]
    return {
        "title": title[:200],
        "hook": beats[0],
        "narration": " ".join(beats),
        "beats": beats,
        "claims": claims,
        "description": (
            f"{title}. Drawn from {url}. The narration uses sentences from that page "
            "and does not add a study, a number, or a recommendation."
        ),
        "hashtags": tags,
        "provider": "dossier",
        "script_mode": "fallback",
        "rejected_model": "",
        "visual_queries": [],
    }


def _script_object(value) -> dict:
    if isinstance(value, dict):
        return value
    raise AppError("The model did not return a script object.")


def _lines(value) -> list[str]:
    """Accept the strings and objects models actually return."""
    if isinstance(value, str):
        found = [part.strip() for part in split_sentences(value) if part.strip()]
        return found or ([value.strip()] if value.strip() else [])
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    lines = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("narration") or item.get("sentence") or "").strip()
        else:
            text = ""
        if text:
            lines.append(text)
    return lines


def _queries(value) -> list[str]:
    if isinstance(value, str):
        parts = [part.strip(" -\t") for part in value.splitlines() if part.strip()]
        return parts or _lines(value)
    return _lines(value)


def _tags(value) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def narration_from_source(
    *,
    title: str,
    extracted: str,
    url: str,
    language: str,
    complete=None,
) -> dict:
    rejected = ""
    if complete is not None:
        try:
            draft = _script_object(complete(title=title, extracted=extracted, url=url, language=language))
            narration = str(draft.get("narration") or "").strip()
            beats = _lines(draft.get("beats"))
            if len(beats) < 4:
                beats = split_sentences(narration)
            _grounded(narration, extracted, title)
            if len(narration.split()) < 40:
                raise AppError("The original script is too short to carry one idea.")
            claims = []
            for text in _lines(draft.get("claims")):
                overlap = tokens(text) & tokens(extracted)
                if len(overlap) < 3:
                    raise AppError("A model claim is not grounded in the source page.")
                if _numbers(text) - _numbers(extracted) - _numbers(title):
                    raise AppError("A model claim adds a number that is not in the source.")
                claims.append(
                    {
                        "text": text,
                        "sources": [url],
                        "verified": True,
                        "unverified": False,
                    }
                )
            if not claims:
                raise AppError("The original script did not record a grounded claim.")
            queries = _queries(draft.get("visual_queries"))
            return {
                "title": str(draft.get("title") or title)[:200],
                "hook": str(draft.get("hook") or beats[0]),
                "narration": narration,
                "beats": beats[:6],
                "claims": claims,
                "description": str(draft.get("description") or f"{title}. Source: {url}"),
                "hashtags": _tags(draft.get("hashtags")),
                "provider": "openai",
                "script_mode": "original",
                "rejected_model": "",
                "visual_queries": queries,
            }
        except AppError as exc:
            rejected = exc.message
        except (AttributeError, TypeError, KeyError) as exc:
            rejected = f"The model script could not be read ({exc})."
    compiled = compile_from_dossier(title=title, extracted=extracted, url=url, language=language)
    compiled["rejected_model"] = rejected
    return compiled


def plan_scenes(beats: list[str], queries: list[str] | None = None) -> list[dict]:
    scenes: list[dict] = []
    supplied = queries or []
    for index, beat in enumerate(beats, start=1):
        motifs = list(tokens(beat))[:4]
        label = " ".join(motifs[:3])[:32]
        query = supplied[index - 1] if index - 1 < len(supplied) else " ".join(motifs[:4])
        scenes.append(
            {
                "index": index,
                "narration": beat,
                "visual": _KINDS[(index - 1) % len(_KINDS)],
                "motifs": motifs,
                "visual_query": query[:80] or "close detail",
                "on_screen_text": label or "source",
            }
        )
    return scenes
