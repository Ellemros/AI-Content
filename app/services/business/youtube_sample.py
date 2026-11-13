"""Optional YouTube Data API sample. View counts are not turned into a demand score."""

from statistics import median

import httpx

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def sample_search(api_key: str, query: str, *, client: httpx.Client) -> dict | None:
    if not api_key.strip() or not query.strip():
        return None
    search = client.get(
        SEARCH_URL,
        params={"part": "snippet", "type": "video", "q": query[:120], "maxResults": 5, "key": api_key},
    )
    search.raise_for_status()
    ids = [
        item.get("id", {}).get("videoId")
        for item in search.json().get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not ids:
        return {"median_views": None, "sample_size": 0, "note": "YouTube search returned no videos."}
    stats = client.get(
        VIDEOS_URL,
        params={"part": "statistics", "id": ",".join(ids), "key": api_key},
    )
    stats.raise_for_status()
    views = []
    for item in stats.json().get("items", []):
        raw = (item.get("statistics") or {}).get("viewCount")
        if raw is not None and str(raw).isdigit():
            views.append(int(raw))
    return {
        "median_views": int(median(views)) if views else None,
        "sample_size": len(views),
        "note": (
            "Median viewCount of videos returned by a YouTube search. "
            "Not niche audience size, not a competitor count, and not RPM."
        ),
    }
