"""Heuristic topic scores. Search volume and competition are unavailable unless a real source is added."""

from dataclasses import dataclass
from urllib.parse import urlparse

from app.domain import CATEGORIES
from app.services.research.brand import AUDIENCE
from app.services.textutil import fold, tokens

SCORE_WEIGHTS = {
    "source_reliability": 0.22,
    "source_check": 0.18,
    "novelty": 0.18,
    "evergreen": 0.16,
    "audience_fit": 0.14,
    "commercial_hint": 0.06,
    "craft": 0.06,
}

BRAND_TERMS = {
    "ai",
    "model",
    "models",
    "tool",
    "tools",
    "science",
    "research",
    "future",
    "agent",
    "agents",
    "machine",
    "learn",
    "learning",
    "neural",
    "robot",
    "robots",
    "data",
    "gemini",
    "chatgpt",
    "codex",
    "llm",
    "llms",
    "multimodal",
}
COMMERCIAL_TERMS = {"tool", "tools", "app", "api", "pricing", "workflow", "software", "subscription"}
EVERGREEN_MARKERS = ("how ", "why ", "guide", "explainer", "history", "works", "learn", "introduction")
TRENDING_MARKERS = ("announce", "launch", "today", "release", "breaking", "introducing")
CLICKBAIT = ("you won t believe", "you wont believe", "shocking", "one weird", "secret they")
TRUSTED_HOSTS = (
    "arxiv.org",
    "openai.com",
    "deepmind.google",
    "blog.google",
    "nature.com",
    "science.org",
    "mit.edu",
    "stanford.edu",
)


@dataclass
class ScoreResult:
    score: int
    breakdown: dict
    horizon: str
    category: str
    keywords: list[str]
    audience: str
    monetization_note: str
    clickbait: bool


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        return host[4:]
    return host


def _part(score: int | None, weight: float | None, note: str) -> dict:
    return {"score": score, "weight": weight, "note": note}


def is_clickbait(text: str) -> bool:
    folded = fold(text)
    raw = text.casefold()
    if "you won't believe" in raw or "you won’t believe" in raw:
        return True
    return any(phrase in folded for phrase in CLICKBAIT)


def classify_horizon(title: str, summary: str) -> str:
    text = fold(f"{title} {summary}")
    padded = f"{text} "
    if any(marker in padded or marker in text for marker in EVERGREEN_MARKERS):
        return "evergreen"
    if any(marker in text for marker in TRENDING_MARKERS):
        return "trending"
    return "unknown"


def classify_category(title: str, summary: str, fallback: str) -> str:
    text = fold(f"{title} {summary}")
    if any(word in text for word in ("study", "paper", "research", "arxiv")):
        return "science"
    if any(word in text for word in ("forecast", "someday", "future")):
        return "future"
    if any(word in text for word in ("tool", "app", "software", "workflow")):
        return "technology"
    if fallback in CATEGORIES:
        return fallback
    return "ai"


def keywords_for(title: str, summary: str) -> list[str]:
    return sorted(tokens(f"{title} {summary}"))[:8]


def monetization_note(title: str, summary: str) -> str:
    found = tokens(f"{title} {summary}") & COMMERCIAL_TERMS
    if found:
        return (
            "No affiliate program is configured. The topic uses tool language, so a later "
            "affiliate suggestion might fit. This is not a product, a link, or a commission rate."
        )
    return "No affiliate program is configured. No product is recommended."


def _reliability(url: str) -> tuple[int, str]:
    host = _host(url)
    if any(host == trusted or host.endswith("." + trusted) for trusted in TRUSTED_HOSTS):
        return 88, "Host is on the public-source list. This is not a traffic number."
    if host.endswith(".edu") or host.endswith(".gov"):
        return 80, "Education or government host."
    if url.startswith("https://"):
        return 50, "Public HTTPS page. The host is not on the trusted-source list."
    return 15, "The URL is missing or is not HTTPS."


def _check_score(verified: bool | None) -> tuple[int, str]:
    if verified is True:
        return 90, "The original URL responded. The page was not parsed into facts."
    if verified is False:
        return 15, "The original URL did not respond. The feed text is not evidence."
    return 35, "The original URL was not checked on this run."


def _evergreen_score(horizon: str) -> tuple[int, str]:
    if horizon == "evergreen":
        return 90, "Title looks like a lasting explainer. This is a keyword heuristic."
    if horizon == "trending":
        return 35, "Title looks like a launch or announcement. Long-term value is weighted higher."
    return 55, "No evergreen or announcement marker."


def _audience_fit(title: str, summary: str) -> tuple[int, str]:
    overlap = tokens(f"{title} {summary}") & BRAND_TERMS
    if len(overlap) >= 2:
        fit = 85
    elif len(overlap) == 1:
        fit = 62
    else:
        fit = 35
    return fit, "Overlap with the AI & Future vocabulary. Not a search-volume measurement."


def _craft(title: str, clickbait: bool) -> tuple[int, str]:
    if clickbait:
        return 20, "Clickbait phrasing lowers the score."
    if len(tokens(title)) < 2:
        return 40, "The title is too thin to promise a useful video."
    return 80, "The title names a subject without a clickbait marker."


def score_topic(
    *,
    title: str,
    summary: str,
    url: str,
    source_category: str,
    verified: bool | None,
    duplicate: bool,
) -> ScoreResult:
    horizon = classify_horizon(title, summary)
    category = classify_category(title, summary, source_category)
    clickbait = is_clickbait(f"{title} {summary}")
    reliability, reliability_note = _reliability(url)
    check, check_note = _check_score(verified)
    novelty = 0 if duplicate else 85
    novelty_note = (
        "The title matches an existing topic or an earlier discovery."
        if duplicate
        else "No close title match in the topic history."
    )
    evergreen, evergreen_note = _evergreen_score(horizon)
    fit, fit_note = _audience_fit(title, summary)
    commercial_terms = tokens(f"{title} {summary}") & COMMERCIAL_TERMS
    commercial = 70 if commercial_terms else 25
    commercial_note = (
        "Tool language only. No affiliate catalog is connected."
        if commercial_terms
        else "No tool-language signal. No product is recommended."
    )
    craft, craft_note = _craft(title, clickbait)
    parts = {
        "source_reliability": reliability,
        "source_check": check,
        "novelty": novelty,
        "evergreen": evergreen,
        "audience_fit": fit,
        "commercial_hint": commercial,
        "craft": craft,
    }
    notes = {
        "source_reliability": reliability_note,
        "source_check": check_note,
        "novelty": novelty_note,
        "evergreen": evergreen_note,
        "audience_fit": fit_note,
        "commercial_hint": commercial_note,
        "craft": craft_note,
    }
    total = sum(SCORE_WEIGHTS[key] * parts[key] for key in SCORE_WEIGHTS)
    score = max(0, min(100, int(round(total))))
    total_note = "Weighted editorial heuristic. Not a search-volume score."
    if duplicate:
        score = min(score, 20)
        total_note = "Capped because the title matches existing content. Not a search-volume score."
    breakdown = {
        key: _part(parts[key], SCORE_WEIGHTS[key], notes[key]) for key in SCORE_WEIGHTS
    }
    breakdown["search_potential"] = _part(
        None,
        None,
        "No search-volume source is configured. Audience fit is a keyword heuristic only.",
    )
    breakdown["competition"] = _part(
        None,
        None,
        "No competition data source is configured.",
    )
    breakdown["total"] = _part(score, None, total_note)
    return ScoreResult(
        score=score,
        breakdown=breakdown,
        horizon=horizon,
        category=category,
        keywords=keywords_for(title, summary),
        audience=AUDIENCE.get(category, AUDIENCE["ai"]),
        monetization_note=monetization_note(title, summary),
        clickbait=clickbait,
    )
