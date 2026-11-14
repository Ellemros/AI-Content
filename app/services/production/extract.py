"""Pull readable text out of a source page. Navigation and scripts are dropped."""

from html.parser import HTMLParser

from app.services.textutil import split_sentences

_SKIP = {"script", "style", "noscript", "nav", "footer", "header", "form"}
_KEEP = {"p", "h1", "h2", "li"}


class _ArticleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._capture = False
        self._buf: list[str] = []
        self._tag = ""
        self.parts: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
        if tag in _KEEP and self._skip == 0:
            self._capture = True
            self._tag = tag
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _KEEP and self._capture:
            text = " ".join("".join(self._buf).split())
            if text:
                if text[-1] not in ".!?":
                    text += "."
                self.parts.append(text)
                self.tags.append(self._tag)
            self._capture = False
        if tag in _SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._capture and self._skip == 0:
            self._buf.append(data)


# Where the article ends and the publisher's citation block or link lists begin.
_END_MARKERS = (
    "story source",
    "journal reference",
    "cite this page",
    "explore more",
    "related stories",
    "related articles",
    "more from",
    "read more",
)


def extract_article(html: str) -> str:
    """Article paragraphs only. Short list items are link lists (related headlines), not evidence."""
    parser = _ArticleText()
    parser.feed(html or "")
    parser.close()
    kept: list[str] = []
    for text, tag in zip(parser.parts, parser.tags, strict=True):
        folded = text.casefold().rstrip(".:")
        if kept and any(folded.startswith(marker) for marker in _END_MARKERS):
            break
        if tag == "li" and len(text.split()) < 8:
            continue
        kept.append(text)
    return "\n".join(kept)


def quotations_from(text: str, limit: int = 8) -> list[str]:
    found: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence.split()) < 8:
            continue
        if sentence not in found:
            found.append(sentence)
        if len(found) >= limit:
            break
    return found
