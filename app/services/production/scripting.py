"""Topic-specific narration built from a retrieved page.

The dossier compiler does not call a model. A model may be used when one is
configured, and its output is kept only when it stays inside the source text.
"""

from app.core.errors import AppError
from app.services.textutil import split_sentences, tokens

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


def _grounded(narration: str, extracted: str, title: str) -> None:
    shared = tokens(narration) & tokens(extracted)
    if len(shared) < 6:
        raise AppError("Model narration is not grounded in the source page.")
    if tokens(title) and not (tokens(title) & tokens(narration)):
        raise AppError("Model narration does not mention the topic.")


def compile_from_dossier(*, title: str, extracted: str, url: str, language: str) -> dict:
    chosen = _usable(extracted)[:5]
    if len(chosen) < 3:
        raise AppError(
            "The source page does not contain enough sentences to write a topic-specific script."
        )
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
        {"text": sentence, "sources": [url], "verified": True, "unverified": False}
        for sentence in chosen
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
        "rejected_model": "",
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
            beats = draft.get("beats") or split_sentences(narration)
            _grounded(narration, extracted, title)
            claims = draft.get("claims") or []
            for claim in claims:
                text = str(claim.get("text") or "")
                if text and text not in extracted:
                    raise AppError("A model claim is not a sentence from the source page.")
            if not claims:
                claims = [
                    {"text": sentence, "sources": [url], "verified": True, "unverified": False}
                    for sentence in _usable(extracted)[:5]
                ]
            return {
                "title": str(draft.get("title") or title)[:200],
                "hook": str(draft.get("hook") or beats[0]),
                "narration": narration,
                "beats": [str(beat) for beat in beats][:5] or split_sentences(narration)[:5],
                "claims": claims,
                "description": str(draft.get("description") or f"{title}. Source: {url}"),
                "hashtags": list(draft.get("hashtags") or []),
                "provider": "openai",
                "rejected_model": "",
            }
        except AppError as exc:
            rejected = exc.message
    compiled = compile_from_dossier(title=title, extracted=extracted, url=url, language=language)
    compiled["rejected_model"] = rejected
    return compiled


def plan_scenes(beats: list[str]) -> list[dict]:
    scenes: list[dict] = []
    for index, beat in enumerate(beats, start=1):
        motifs = list(tokens(beat))[:3]
        label = " ".join(motifs[:2])[:24]
        scenes.append(
            {
                "index": index,
                "narration": beat,
                "visual": _KINDS[(index - 1) % len(_KINDS)],
                "motifs": motifs,
                "on_screen_text": label or "source",
            }
        )
    return scenes
