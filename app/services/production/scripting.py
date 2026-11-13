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
            draft = complete(title=title, extracted=extracted, url=url, language=language)
            narration = str(draft.get("narration") or "").strip()
            beats = [str(beat).strip() for beat in (draft.get("beats") or []) if str(beat).strip()]
            if len(beats) < 4:
                beats = split_sentences(narration)
            _grounded(narration, extracted, title)
            if len(narration.split()) < 40:
                raise AppError("The original script is too short to carry one idea.")
            claims = []
            for claim in draft.get("claims") or []:
                text = str(claim.get("text") or "").strip()
                if not text:
                    continue
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
            queries = [str(item).strip() for item in (draft.get("visual_queries") or []) if str(item).strip()]
            return {
                "title": str(draft.get("title") or title)[:200],
                "hook": str(draft.get("hook") or beats[0]),
                "narration": narration,
                "beats": beats[:6],
                "claims": claims,
                "description": str(draft.get("description") or f"{title}. Source: {url}"),
                "hashtags": list(draft.get("hashtags") or []),
                "provider": "openai",
                "script_mode": "original",
                "rejected_model": "",
                "visual_queries": queries,
            }
        except AppError as exc:
            rejected = exc.message
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
