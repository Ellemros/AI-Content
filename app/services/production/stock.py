"""Optional Wikimedia Commons stills. Non-commercial and no-derivatives licenses are refused."""

from app.config import get_settings

_API = "https://commons.wikimedia.org/w/api.php"


def license_allowed(name: str) -> bool:
    text = (name or "").casefold().replace("_", " ")
    blocked = ("noncommercial", "non-commercial", " noderiv", "no derivatives", "-nc", " nc")
    if any(flag in text for flag in blocked):
        return False
    if "nd" in text and "cc" in text:
        return False
    allowed = ("public domain", "cc0", "cc by", "cc-by")
    return any(item in text for item in allowed)


def parse_commons(payload: dict) -> dict | None:
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        info = (page.get("imageinfo") or [None])[0] or {}
        meta = info.get("extmetadata") or {}
        license_name = str((meta.get("LicenseShortName") or {}).get("value") or "")
        thumb = info.get("thumburl") or ""
        if not thumb.startswith("https://") or not license_allowed(license_name):
            continue
        artist = str((meta.get("Artist") or {}).get("value") or "")
        return {
            "title": page.get("title") or "",
            "url": thumb,
            "license": license_name,
            "artist": _plain(artist)[:160],
            "source": "wikimedia-commons",
        }
    return None


def _plain(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", value or "").split("\n", 1)[0].strip()


def search_commons(query: str, *, client) -> dict | None:
    if get_settings().production_stock.strip().lower() != "wikimedia":
        return None
    if not query.strip():
        return None
    response = client.get(
        _API,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}"[:180],
            "gsrnamespace": 6,
            "gsrlimit": 5,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1080,
        },
    )
    response.raise_for_status()
    return parse_commons(response.json())
