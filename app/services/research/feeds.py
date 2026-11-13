"""RSS and Atom parsing. The feed text is a lead. It is not treated as a verified fact."""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

USER_AGENT = "AIContentFactory/2 research (public RSS)"
_TAGS = re.compile(r"<[^>]+>")
_MAX_BYTES = 2_000_000


@dataclass
class ParsedItem:
    external_id: str
    title: str
    summary: str
    url: str
    published_at: datetime | None = None
    verified: bool | None = None
    check_label: str = ""


@dataclass
class FetchResult:
    items: list[ParsedItem]
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False
    body_sha256: str = ""


def plain_text(value: str, limit: int = 600) -> str:
    text = html.unescape(value or "")
    text = _TAGS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_date(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        from datetime import UTC

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(node) if _local(child.tag) == name]


def _text(node: ElementTree.Element, name: str) -> str:
    found = _children(node, name)
    if not found or found[0].text is None:
        return ""
    return found[0].text.strip()


def _external_id(raw: str, url: str) -> str:
    value = (raw or url).strip()
    if len(value) <= 180:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _http_url(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return ""


def _rss_item(node: ElementTree.Element) -> ParsedItem | None:
    title = plain_text(_text(node, "title"), limit=300)
    url = _http_url(_text(node, "link"))
    if not title or not url:
        return None
    summary = plain_text(_text(node, "description") or _text(node, "encoded"))
    guid = _text(node, "guid")
    published = parse_date(_text(node, "pubDate") or _text(node, "date"))
    return ParsedItem(
        external_id=_external_id(guid, url),
        title=title,
        summary=summary,
        url=url,
        published_at=published,
    )


def _atom_item(node: ElementTree.Element) -> ParsedItem | None:
    title = plain_text(_text(node, "title"), limit=300)
    url = ""
    for link in _children(node, "link"):
        href = _http_url(link.attrib.get("href", ""))
        if not href:
            continue
        rel = link.attrib.get("rel", "alternate")
        if rel in {"alternate", ""}:
            url = href
            break
        url = url or href
    if not title or not url:
        return None
    summary = plain_text(_text(node, "summary") or _text(node, "content"))
    published = parse_date(_text(node, "updated") or _text(node, "published"))
    return ParsedItem(
        external_id=_external_id(_text(node, "id"), url),
        title=title,
        summary=summary,
        url=url,
        published_at=published,
    )


def parse_feed(xml_text: str, *, limit: int) -> list[ParsedItem]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError("Feed XML could not be parsed.") from exc
    kind = _local(root.tag)
    if kind == "rss":
        channel = next((child for child in list(root) if _local(child.tag) == "channel"), None)
        nodes = _children(channel, "item") if channel is not None else []
        parser = _rss_item
    elif kind == "feed":
        nodes = _children(root, "entry")
        parser = _atom_item
    else:
        raise ValueError("Feed is not RSS or Atom.")
    items: list[ParsedItem] = []
    seen: set[str] = set()
    for node in nodes:
        if len(items) >= limit:
            break
        parsed = parser(node)
        if parsed is None or parsed.external_id in seen:
            continue
        seen.add(parsed.external_id)
        items.append(parsed)
    return items


def check_url(client: httpx.Client, url: str) -> tuple[bool, str]:
    """Confirm the original URL responds. The body is not parsed into facts."""
    try:
        response = client.head(url)
        if response.status_code in {403, 405, 501}:
            response.close()
            with client.stream("GET", url) as streamed:
                code = streamed.status_code
        else:
            code = response.status_code
            response.close()
    except httpx.HTTPError as exc:
        return False, exc.__class__.__name__
    return 200 <= code < 400, str(code)


def fetch_source(source, settings) -> FetchResult:
    headers = {"User-Agent": USER_AGENT}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    timeout = settings.research_timeout_seconds
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        with client.stream("GET", source.url) as response:
            if response.status_code == 304:
                return FetchResult(
                    items=[],
                    etag=source.etag,
                    last_modified=source.last_modified,
                    not_modified=True,
                )
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) >= _MAX_BYTES:
                    break
            etag = response.headers.get("etag", "")
            last_modified = response.headers.get("last-modified", "")
        text = bytes(raw).decode("utf-8", errors="replace")
        items = parse_feed(text, limit=settings.research_max_items)
        budget = settings.research_verify_limit
        for item in items:
            if budget <= 0:
                item.verified = None
                item.check_label = "not_checked"
                continue
            budget -= 1
            item.verified, item.check_label = check_url(client, item.url)
        return FetchResult(
            items=items,
            etag=etag,
            last_modified=last_modified,
            body_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
