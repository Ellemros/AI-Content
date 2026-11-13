"""Make narration easier for a speech engine to pronounce. The written script stays unchanged."""

import re

_TERMS = (
    ("Dr.", "Doctor"),
    ("Mr.", "Mister"),
    ("Mrs.", "Missus"),
    ("e.g.", "for example"),
    ("i.e.", "that is"),
    ("°C", " degrees Celsius"),
    ("°F", " degrees Fahrenheit"),
    ("%", " percent"),
)


def speakable(text: str) -> str:
    spoken = text
    for source, replacement in _TERMS:
        spoken = spoken.replace(source, replacement)
    spoken = re.sub(r"(\d),(\d{3})\b", r"\1\2", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken
