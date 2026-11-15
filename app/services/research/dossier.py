"""Multi-source research dossier for one topic question.

Each source page is fetched politely (a descriptive User-Agent, Wikipedia through its
official REST API, no bypass of a site that refuses automated clients), reduced to
article text, and condensed to the paragraphs most relevant to the question, with
the opening paragraphs kept because abstracts and ledes carry the main finding.
The writer sees every source labelled [S1], [S2] ... and must cite them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app.config import get_settings
from app.core.errors import AppError
from app.services.production.extract import extract_article
from app.services.textutil import tokens

USER_AGENT = "AIContentFactory/1.0 (research fetcher; +https://github.com/Ellemros/AI-Content)"


@dataclass
class Source:
    label: str
    url: str
    status: int
    text: str


def _fetch_url(url: str) -> str:
    """The address actually requested.

    Wikipedia articles go through the Wikipedia REST API, and PubMed Central articles through
    NCBI's E-utilities (the official programmatic route; the PMC web pages may answer automated
    clients with a CAPTCHA, which is never solved or bypassed).
    """
    parsed = urlparse(url)
    pmc = _PMC_ID.search(url)
    if pmc and ("ncbi.nlm.nih.gov" in parsed.netloc):
        return f"{_EUTILS}?db=pmc&id={pmc.group(1)}&tool=AIContentFactory"
    if parsed.netloc.endswith("wikipedia.org") and parsed.path.startswith("/wiki/"):
        title = parsed.path.split("/wiki/", 1)[1]
        return f"{parsed.scheme}://{parsed.netloc}/api/rest_v1/page/html/{quote(title, safe='')}"
    return url


_PMC_ID = re.compile(r"/(?:pmc/)?articles/PMC(\d+)", re.IGNORECASE)
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'(name|property|content)\s*=\s*"([^"]*)"', re.IGNORECASE)
_TITLE_KEYS = ("citation_title", "dc.title", "og:title")
_VENUE_KEYS = ("citation_journal_title", "prism.publicationname", "og:site_name")
_DATE_KEYS = ("citation_publication_date", "prism.publicationdate", "dc.date", "article:published_time")


def citation_line(html: str) -> str:
    """'Citation: <title>. <venue>, <date>.' from standard meta tags, or ''. Gives the writer the year."""
    found: dict[str, str] = {}
    for tag in _META.findall(html or ""):
        attrs = {key.lower(): value for key, value in _ATTR.findall(tag)}
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key and "content" in attrs and key not in found:
            found[key] = " ".join(attrs["content"].split())

    def first(keys: tuple[str, ...]) -> str:
        return next((found[key] for key in keys if found.get(key)), "")

    title, venue, date = first(_TITLE_KEYS), first(_VENUE_KEYS), first(_DATE_KEYS)[:10]
    if not (title or venue or date):
        return ""
    parts = [part for part in (title, ", ".join(item for item in (venue, date) if item)) if part]
    return "Citation: " + ". ".join(parts) + "."


def fetch_source(url: str, client: httpx.Client | None = None) -> tuple[int, str]:
    owned = client is None
    client = client or httpx.Client(
        timeout=get_settings().research_timeout_seconds * 3, follow_redirects=True
    )
    try:
        response = client.get(_fetch_url(url), headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise AppError(f"Could not retrieve {url}: {exc}") from exc
    finally:
        if owned:
            client.close()
    if response.status_code >= 400:
        raise AppError(f"{url} refused the request (HTTP {response.status_code}). It is not used.")
    if "pdf" in response.headers.get("content-type", "").lower() or response.content[:5] == b"%PDF-":
        return response.status_code, pdf_text(response.content)
    if "<pmc-articleset" in response.text[:600]:
        return response.status_code, jats_text(response.text)
    if "recaptcha" in response.text[:2000].lower():
        raise AppError(f"{url} answered with a CAPTCHA. It is not used (CAPTCHAs are never bypassed).")
    citation = citation_line(response.text)
    text = extract_article(response.text)
    return response.status_code, f"{citation}\n{text}" if citation else text


_HYPHEN = re.compile(r"(\w)-\n(\w)")


def pdf_text(data: bytes, max_pages: int = 30) -> str:
    """Article text from a PDF (working papers, preprints): lines joined into paragraphs.

    The first page's title line becomes the citation line, so the year and venue
    printed on a paper's cover stay with the text.
    """
    from io import BytesIO

    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    except Exception as exc:  # noqa: BLE001 - a broken PDF is an unusable source, not a crash
        raise AppError(f"The PDF could not be read: {exc}") from exc
    paragraphs: list[str] = []
    for page in pages:
        page = _HYPHEN.sub(r"\1\2", page)
        block: list[str] = []
        for line in page.splitlines():
            line = " ".join(line.split())
            if not line:
                if block:
                    paragraphs.append(" ".join(block))
                    block = []
                continue
            block.append(line)
            if line.endswith((".", "?", "!", ":")) and len(" ".join(block).split()) >= 25:
                paragraphs.append(" ".join(block))
                block = []
        if block:
            paragraphs.append(" ".join(block))
    title = (reader.metadata.title if reader.metadata and reader.metadata.title else "").strip()
    cover = " ".join(pages[0].split())[:300] if pages else ""
    citation = f"Citation: {title}. {cover}" if title else f"Citation: {cover}"
    return citation + "\n" + "\n".join(paragraphs)


def jats_text(xml: str) -> str:
    """Article text from a JATS XML article (PubMed Central E-utilities): citation, abstract, body."""
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise AppError(f"The article XML could not be read: {exc}") from exc

    def text_of(node) -> str:
        return " ".join("".join(node.itertext()).split()) if node is not None else ""

    title = text_of(root.find(".//article-meta/title-group/article-title"))
    journal = text_of(root.find(".//journal-meta//journal-title"))
    year = text_of(root.find(".//article-meta/pub-date/year"))
    paragraphs = []
    for section in (root.find(".//article-meta/abstract"), root.find(".//body")):
        if section is None:
            continue
        for node in section.iter():
            if node.tag in {"p", "title"}:
                line = text_of(node)
                if line:
                    paragraphs.append(line)
    citation = f"Citation: {title}. {journal}, {year}." if title else ""
    return "\n".join([citation, *paragraphs]).strip()


# Site furniture that is not article text: government-site banners, cookie notices, contact lines.
_CHROME = re.compile(
    r"\.gov website|padlock|secure websites|accept cookies|cookies? (?:policy|settings)|"
    r"correspondence may be addressed|^email:|all rights reserved",
    re.IGNORECASE,
)
# Bibliography entries (Wikipedia reference lists, a paper's references). They name works the page
# cites, not what those works found; a writer that sees them can attach a finding to the wrong paper.
_REFERENCE = re.compile(
    r"\bdoi:\s*10\.|\bJSTOR\b|\bISBN\b|\bPMID\b|\bS2CID\b|\bRetrieved \d|\bArchived from\b|"
    r"^(?:\d+ ){2,}|^\^|^[A-Z][\w'-]+, [A-Z][\w.]*.{0,80}\((?:19|20)\d\d[a-z]?\)\. [\"\u201c]",
)


_RESULT = re.compile(r"\d+(?:\.\d+)?\s?(?:%|percent\b)|\bp\s?[<=]\s?\.?\d|\bn\s?=\s?\d", re.IGNORECASE)


def condense(text: str, question: str, budget: int) -> str:
    """Keep the opening and the paragraphs that share the most words with the question, in page order."""
    lines = [part.strip() for part in text.split("\n")]
    citation = lines[0] if lines and lines[0].startswith("Citation: ") else ""
    paragraphs = [
        part
        for part in lines
        if len(part.split()) >= 8
        and part != citation
        and not _CHROME.search(part)
        and not _REFERENCE.search(part)
    ]
    body = _condense(paragraphs, question, budget - len(citation))
    return f"{citation}\n{body}" if citation else body


def _condense(paragraphs: list[str], question: str, budget: int) -> str:
    if sum(len(part) for part in paragraphs) <= budget:
        return "\n".join(paragraphs)
    wanted = tokens(question)
    scored = []
    for index, part in enumerate(paragraphs):
        overlap = len(tokens(part) & wanted)
        bonus = 3 if index < 3 else 0
        # Results paragraphs carry the numbers a script needs (rates, percentages, sample sizes).
        if _RESULT.search(part):
            bonus += 2
        scored.append((overlap + bonus, index, part))
    chosen: list[tuple[int, str]] = []
    used = 0
    for _score, index, part in sorted(scored, key=lambda item: (-item[0], item[1])):
        if used + len(part) > budget:
            continue
        chosen.append((index, part))
        used += len(part)
    return "\n".join(part for _index, part in sorted(chosen))


def build(urls: list[str], question: str, *, budget_per_source: int = 6500, fetch=None) -> list[Source]:
    sources: list[Source] = []
    errors: list[str] = []
    for url in urls[:4]:
        try:
            status, text = (fetch or fetch_source)(url)
        except AppError as exc:
            errors.append(exc.message)
            continue
        condensed = condense(text, question, budget_per_source)
        if len(condensed.split()) < 80:
            errors.append(f"{url} has too little article text to use.")
            continue
        sources.append(Source(label=f"S{len(sources) + 1}", url=url, status=status, text=condensed))
    if not sources:
        raise AppError("No usable source. " + " ".join(errors)[:400])
    return sources


def combined_text(sources: list[Source]) -> str:
    return "\n\n".join(f"[{source.label}] {source.url}\n{source.text}" for source in sources)


def source_for(label: str, sources: list[Source]) -> str:
    match = re.search(r"\d+", str(label or ""))
    if match:
        index = int(match.group()) - 1
        if 0 <= index < len(sources):
            return sources[index].url
    return sources[0].url
