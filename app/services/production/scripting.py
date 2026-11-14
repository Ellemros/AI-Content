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


ACTIONS = (
    "walk",
    "compare",
    "choose",
    "clock",
    "notice",
    "think",
    "point",
    "surprise",
    "explain",
    "celebrate",
)
_HEDGES = (
    "might",
    "could",
    "may",
    "possibly",
    "maybe",
    "suggest",
    "suggests",
    "suggested",
    "likely",
    "appear",
    "appears",
    "seem",
    "seems",
    "potentially",
    "linked",
    "associated",
    "think",
    "believe",
    "hypothesis",
    "preliminary",
)


def _source_match(sentence: str, source_sentences: list[str]) -> str:
    """The source sentence that a script sentence most plausibly paraphrases, or ''."""
    words = tokens(sentence)
    if len(words) < 3:
        return ""
    best = ""
    best_overlap = 0
    for candidate in source_sentences:
        overlap = len(words & tokens(candidate))
        if overlap > best_overlap:
            best, best_overlap = candidate, overlap
    if best_overlap >= 3 and best_overlap >= 0.5 * len(words):
        return best
    return ""


def dropped_hedges(texts: list[str], extracted: str) -> list[str]:
    """Script sentences that state flatly what the matching source sentence hedges."""
    source_sentences = split_sentences(extracted)
    found = []
    for text in texts:
        for sentence in split_sentences(text) or [text]:
            if hedged(sentence):
                continue
            match = _source_match(sentence, source_sentences)
            if match and hedged(match):
                found.append(sentence)
    return found


def _beat_items(value) -> list[dict]:
    """Beats arrive as strings or as {text, action, label} objects."""
    if isinstance(value, str):
        return [{"text": line} for line in _lines(value)]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append({"text": item.strip()})
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("narration") or item.get("sentence") or "").strip()
            if text:
                items.append(
                    {
                        "text": text,
                        "action": str(item.get("action") or "").strip().lower(),
                        "label": str(item.get("label") or item.get("on_screen_text") or "").strip(),
                    }
                )
    return items


def _claim_items(value) -> list[dict]:
    if isinstance(value, str):
        return [{"text": line} for line in _lines(value)]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append({"text": item.strip()})
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("claim") or "").strip()
            if text:
                items.append({"text": text, "certainty": str(item.get("certainty") or "").strip().lower()})
    return items


def hedged(text: str) -> bool:
    folded = text.casefold()
    return any(re.search(rf"\b{word}\b", folded) for word in _HEDGES)


def _certainty(text: str, stated: str) -> str:
    # Wording decides. A claim that says "may" is hedged even if the model labelled it established.
    if hedged(text):
        return "hedged"
    return "established" if stated in {"", "established"} else "hedged"


def narration_from_source(
    *,
    title: str,
    extracted: str,
    url: str,
    language: str,
    complete=None,
    feedback: str = "",
    allow_fallback: bool = True,
) -> dict:
    """Return an original model script, or the labelled dossier fallback.

    With allow_fallback=False a rejected model script raises instead, so the caller
    can ask for a revision rather than rendering stitched source sentences.
    """
    rejected = ""
    if complete is not None:
        try:
            kwargs = {"title": title, "extracted": extracted, "url": url, "language": language}
            if feedback:
                kwargs["feedback"] = feedback
            draft = _script_object(complete(**kwargs))
            beat_items = _beat_items(draft.get("beats"))
            narration = str(draft.get("narration") or "").strip()
            if len(beat_items) >= 4:
                # The beats are the narration. One source of truth keeps scenes and speech in sync.
                narration = " ".join(item["text"] for item in beat_items)
            else:
                beat_items = [{"text": line} for line in split_sentences(narration)]
            beats = [item["text"] for item in beat_items]
            _grounded(narration, extracted, title)
            written = str(draft.get("narration") or "").strip()
            if written and written != narration:
                # A model that invents a number anywhere in its answer is not trusted, spoken or not.
                _grounded(written, extracted, title)
            if len(narration.split()) < 40:
                raise AppError("The original script is too short to carry one idea.")
            claims = []
            for item in _claim_items(draft.get("claims")):
                text = item["text"]
                overlap = tokens(text) & tokens(extracted)
                if len(overlap) < 3:
                    raise AppError(f"A model claim is not grounded in the source page: {text[:120]}")
                if _numbers(text) - _numbers(extracted) - _numbers(title):
                    raise AppError(f"A model claim adds a number that is not in the source: {text[:120]}")
                claims.append(
                    {
                        "text": text,
                        "sources": [url],
                        "verified": True,
                        "unverified": False,
                        "certainty": _certainty(text, item.get("certainty", "")),
                    }
                )
            if not claims:
                raise AppError("The original script did not record a grounded claim.")
            flat = dropped_hedges(beats + [claim["text"] for claim in claims], extracted)
            if flat:
                raise AppError(
                    "The script states as certain what the source only suggests. Restore the hedge in: "
                    + " | ".join(dict.fromkeys(flat))[:300]
                )
            queries = _queries(draft.get("visual_queries"))
            return {
                "title": str(draft.get("title") or title)[:200],
                "hook": beats[0],
                "narration": narration,
                "beats": beats[:8],
                "beat_directions": beat_items[:8],
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
        if not allow_fallback:
            raise AppError(rejected or "The model script was rejected.")
    compiled = compile_from_dossier(title=title, extracted=extracted, url=url, language=language)
    compiled["rejected_model"] = rejected
    return compiled


def plan_scenes(
    beats: list[str], queries: list[str] | None = None, directions: list[dict] | None = None
) -> list[dict]:
    scenes: list[dict] = []
    supplied = queries or []
    directed = directions or []
    for index, beat in enumerate(beats, start=1):
        motifs = list(tokens(beat))[:4]
        direction = directed[index - 1] if index - 1 < len(directed) else {}
        label = str(direction.get("label") or "").strip()[:28] or " ".join(motifs[:3])[:28]
        action = str(direction.get("action") or "").strip().lower()
        query = supplied[index - 1] if index - 1 < len(supplied) else " ".join(motifs[:4])
        scene = {
            "index": index,
            "narration": beat,
            "visual": _KINDS[(index - 1) % len(_KINDS)],
            "motifs": motifs,
            "visual_query": query[:80] or "close detail",
            "on_screen_text": label or "source",
        }
        if action in ACTIONS:
            scene["action"] = action
        scenes.append(scene)
    return scenes
