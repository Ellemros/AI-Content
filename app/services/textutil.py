"""Text helpers shared by the script schema, subtitles, and topic dedupe."""

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

WORDS_PER_SECOND = {"en": 2.6, "cs": 2.3}

_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "for",
    "in",
    "on",
    "with",
    "from",
    "by",
    "at",
    "is",
    "are",
    "how",
    "what",
    "why",
    "your",
    "you",
    "jak",
    "se",
    "z",
    "ze",
    "na",
    "do",
    "je",
    "ta",
    "ten",
    "ve",
    "v",
    "o",
    "pro",
    "i",
    "u",
}


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text, flags=re.UNICODE)


def estimate_seconds(text: str, language: str) -> float:
    rate = WORDS_PER_SECOND.get(language, WORDS_PER_SECOND["en"])
    return round(len(words(text)) / rate, 2)


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = stripped.casefold()
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def fingerprint(title: str) -> str:
    return hashlib.sha256(fold(title).encode("utf-8")).hexdigest()


def tokens(title: str) -> set[str]:
    return {token for token in fold(title).split() if token not in _STOPWORDS and len(token) > 1}


def jaccard(left: str, right: str) -> float:
    a = tokens(left)
    b = tokens(right)
    if not a or not b:
        return 1.0 if fold(left) == fold(right) else 0.0
    return len(a & b) / len(a | b)


def sequence_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, fold(left), fold(right)).ratio()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return [part.strip() for part in parts if part.strip()]


def card_line(sentence: str, limit: int = 48) -> str:
    cleaned = sentence.strip().rstrip(".!?")
    if len(cleaned) <= limit:
        return cleaned or sentence.strip()
    cut = cleaned[:limit].rsplit(" ", 1)[0]
    return cut or cleaned[:limit]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
