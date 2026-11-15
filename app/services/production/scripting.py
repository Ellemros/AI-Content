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


from app.services.production.scene_engine import ACTIONS  # noqa: E402,F401

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


# Lab models whose results must not be presented as findings about people.
_STUDY_MODELS = {
    "mice": r"\b(mice|mouse|murine)\b",
    "rats": r"\b(rats?|rodents?)\b",
    "fruit flies": r"\b(fruit flies|drosophila)\b",
    "zebrafish": r"\bzebrafish\b",
    "worms": r"\b(c\. elegans|nematodes?)\b",
    "cells in a lab dish": r"\b(in vitro|cell cultures?|petri dish|organoids?)\b",
    "computer simulations": r"\b(simulations?|computer models?)\b",
}


def dropped_study_model(narration: str, extracted: str) -> str:
    """The lab model the source used, when the narration hides it. '' when it is said or absent."""
    source = extracted.casefold()
    spoken = narration.casefold()
    for name, pattern in _STUDY_MODELS.items():
        if len(re.findall(pattern, source)) >= 2 and not re.search(pattern, spoken):
            if name == "rats" and re.search(_STUDY_MODELS["mice"], spoken):
                continue
            if name == "mice" and re.search(_STUDY_MODELS["rats"] + r"|\banimals?\b", spoken):
                continue
            return name
    return ""


def dropped_hedges(texts: list[str], extracted: str) -> list[str]:
    """Script sentences that state flatly what the matching source sentence hedges."""
    source_sentences = split_sentences(extracted)
    # The hedged proposition is what follows the hedge word ("ink could help read the scrolls").
    # A script sentence restating a different clause of that source sentence is not a dropped hedge.
    clauses = [_hedged_clause(sentence) for sentence in source_sentences if hedged(sentence)]
    clauses = [clause for clause in clauses if clause]
    plain = [sentence for sentence in source_sentences if not hedged(sentence)]
    found = []
    for text in texts:
        for sentence in split_sentences(text) or [text]:
            if hedged(sentence) or sentence.rstrip().endswith("?"):
                continue
            match = _source_match(sentence, clauses)
            if not match:
                continue
            # Flag only when the hedged statement is the best support. If an unhedged source sentence
            # supports the line as well, the source states it plainly somewhere and the line is fine.
            words = tokens(sentence)
            hedged_overlap = len(words & tokens(match))
            plain_overlap = max((len(words & tokens(candidate)) for candidate in plain), default=0)
            if hedged_overlap > plain_overlap:
                found.append(sentence)
    return found


def _hedged_clause(sentence: str) -> str:
    words = sentence.split()
    for index, word in enumerate(words):
        if re.sub(r"\W", "", word.casefold()) in _HEDGES:
            return " ".join(words[index + 1 :])
    return ""


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
                        "direction": {
                            key: item.get(key)
                            for key in (
                                "layout",
                                "setting",
                                "props",
                                "camera",
                                "emphasis",
                                "chart",
                                "labels",
                                "template",
                                "kicker",
                                "params",
                            )
                            if item.get(key) not in (None, "", [])
                        },
                    }
                )
    return items


def _checked_direction(direction: dict, extracted: str, title: str) -> dict:
    """Drop any chart or emphasis whose number is not on the source page."""
    allowed = _numbers(extracted) | _numbers(title)
    clean = dict(direction)
    emphasis = str(clean.get("emphasis") or "")
    if _numbers(emphasis) - allowed:
        clean.pop("emphasis", None)
    chart = clean.get("chart")
    if chart is not None:
        rows = chart if isinstance(chart, list) else []
        if not rows or any(
            not isinstance(row, dict) or (_numbers(str(row.get("value"))) - allowed) for row in rows
        ):
            clean.pop("chart", None)
    return clean


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
                items.append(
                    {
                        "text": text,
                        "certainty": str(item.get("certainty") or "").strip().lower(),
                        "source": str(item.get("source") or ""),
                    }
                )
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
    strict_hedges: bool = True,
) -> dict:
    """Return an original model script, or the labelled dossier fallback.

    strict_hedges=False keeps the word-overlap hedge check as hints for a model fact checker
    (draft["hedge_hints"]) instead of a hard rejection; the fact checker then decides.

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
            for item in beat_items:
                if item.get("direction"):
                    item["direction"] = _checked_direction(item["direction"], extracted, title)
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
                        "source": item.get("source", ""),
                    }
                )
            if not claims:
                raise AppError("The original script did not record a grounded claim.")
            flat = dropped_hedges(beats + [claim["text"] for claim in claims], extracted)
            if flat and strict_hedges:
                raise AppError(
                    "The script states as certain what the source only suggests. Restore the hedge in: "
                    + " | ".join(dict.fromkeys(flat))[:300]
                )
            model = dropped_study_model(narration, extracted)
            if model:
                raise AppError(
                    f"The source's experiment was in {model}, but the narration never says so. "
                    f"Say plainly that the finding comes from {model}."
                )
            queries = _queries(draft.get("visual_queries"))
            return {
                "title": str(draft.get("title") or title)[:200],
                "hook": beats[0],
                "narration": narration,
                "beats": beats[:10],
                "beat_directions": beat_items[:10],
                "claims": claims,
                "description": str(draft.get("description") or f"{title}. Source: {url}"),
                "hashtags": _tags(draft.get("hashtags")),
                "provider": "openai",
                "hedge_hints": list(dict.fromkeys(flat)),
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
        if action:
            scene["action"] = action
        if direction.get("direction"):
            scene["direction"] = direction["direction"]
        scenes.append(scene)
    return scenes
