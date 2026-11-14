"""Official upload APIs. No browser automation, no unofficial endpoints.

YouTube Data API v3 (resumable videos.insert) and YouTube Analytics API v2.
TikTok Content Posting API (Direct Post, FILE_UPLOAD).
Instagram Graph API (REELS container, resumable upload) and Facebook Pages video_reels.

Each `publish` returns (external_id, url). A PublishError says whether a retry is
safe. `uncertain=True` means the bytes may have reached the platform: the queue
stops and asks a person to check instead of risking a duplicate post.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.config import get_settings


class PublishError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.uncertain = uncertain


def _check(response: httpx.Response, what: str) -> dict:
    if response.status_code == 429 or response.status_code >= 500:
        raise PublishError(f"{what}: HTTP {response.status_code}", retryable=True)
    if response.status_code >= 400:
        raise PublishError(f"{what}: HTTP {response.status_code} {response.text[:300]}")
    try:
        return response.json() if response.content else {}
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------------------------------- YouTube

YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
)
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def _client_secrets() -> dict:
    path = Path(get_settings().youtube_client_secrets)
    if not path.is_file():
        raise PublishError(f"YouTube client secrets are missing: {path.resolve()}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def youtube_authorize(open_browser=None) -> dict:
    """One-time sign-in. Opens Google's consent page and catches the code on 127.0.0.1."""
    import http.server
    import threading
    import webbrowser

    secrets_json = _client_secrets()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")

        def log_message(self, *_args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    params = {
        "client_id": secrets_json["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = (
        secrets_json.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
        + "?"
        + urllib.parse.urlencode(params)
    )
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print(f"Open this address if the browser does not open:\n{url}")
    (open_browser or webbrowser.open)(url)
    thread.join(timeout=300)
    server.server_close()
    if received.get("state") != state or "code" not in received:
        raise PublishError("Google sign-in did not complete.")
    response = httpx.post(
        secrets_json.get("token_uri", _GOOGLE_TOKEN),
        data={
            "code": received["code"],
            "client_id": secrets_json["client_id"],
            "client_secret": secrets_json.get("client_secret", ""),
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=30,
    )
    token = _check(response, "Google token exchange")
    return _with_expiry(token)


def _with_expiry(token: dict) -> dict:
    token = dict(token)
    token["expires_at"] = (
        datetime.now(UTC) + timedelta(seconds=int(token.get("expires_in", 3600)) - 60)
    ).isoformat()
    return token


def youtube_access_token(creds: dict, client: httpx.Client) -> tuple[str, dict]:
    expires = creds.get("expires_at")
    if creds.get("access_token") and expires and datetime.fromisoformat(expires) > datetime.now(UTC):
        return creds["access_token"], creds
    if not creds.get("refresh_token"):
        raise PublishError("YouTube is not connected. Run connect-youtube once.")
    secrets_json = _client_secrets()
    response = client.post(
        secrets_json.get("token_uri", _GOOGLE_TOKEN),
        data={
            "client_id": secrets_json["client_id"],
            "client_secret": secrets_json.get("client_secret", ""),
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    fresh = _with_expiry({**creds, **_check(response, "Google token refresh")})
    return fresh["access_token"], fresh


def youtube_publish(post, video: Path, creds: dict, client: httpx.Client) -> tuple[str, str, dict]:
    token, creds = youtube_access_token(creds, client)
    options = post.options or {}
    body = {
        "snippet": {
            "title": post.title,
            "description": post.description,
            "tags": list(post.tags or []),
            "categoryId": str(options.get("category_id") or "27"),  # 27 = Education
            "defaultLanguage": options.get("language", "en"),
        },
        "status": {
            "privacyStatus": options.get("privacy") or get_settings().youtube_privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    size = video.stat().st_size
    start = client.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        content=json.dumps(body),
    )
    _check(start, "YouTube upload session")
    session_url = start.headers.get("Location")
    if not session_url:
        raise PublishError("YouTube did not return an upload session.", retryable=True)
    try:
        sent = client.put(session_url, headers={"Content-Type": "video/mp4"}, content=video.read_bytes())
    except httpx.HTTPError as exc:
        raise PublishError(f"YouTube upload did not confirm: {exc}", uncertain=True) from exc
    if sent.status_code >= 500:
        raise PublishError(f"YouTube upload did not confirm: HTTP {sent.status_code}", uncertain=True)
    data = _check(sent, "YouTube upload")
    video_id = data.get("id")
    if not video_id:
        raise PublishError("YouTube accepted the bytes but returned no video id.", uncertain=True)
    url = (
        f"https://www.youtube.com/shorts/{video_id}"
        if post.platform == "youtube_short"
        else (f"https://www.youtube.com/watch?v={video_id}")
    )
    return video_id, url, creds


def youtube_metrics(post, creds: dict, client: httpx.Client, start: str, end: str) -> tuple[list[dict], dict]:
    token, creds = youtube_access_token(creds, client)
    base = (
        "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
        "likes,comments,shares,subscribersGained"
    )
    rows: list[dict] = []
    for metrics in (base + ",estimatedRevenue", base):
        response = client.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params={
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": metrics,
                "dimensions": "day",
                "filters": f"video=={post.external_id}",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 403 and "estimatedRevenue" in metrics:
            continue  # Revenue needs YPP and the monetary scope. Fall back to audience metrics only.
        data = _check(response, "YouTube Analytics")
        names = [column["name"] for column in data.get("columnHeaders", [])]
        rows = [dict(zip(names, row, strict=False)) for row in data.get("rows", [])]
        break
    return rows, creds


# -------------------------------------------------------------------------------------------- TikTok

_TIKTOK = "https://open.tiktokapis.com"


def tiktok_exchange_code(code: str, redirect_uri: str, client: httpx.Client | None = None) -> dict:
    settings = get_settings()
    owned = client or httpx.Client(timeout=30)
    response = owned.post(
        f"{_TIKTOK}/v2/oauth/token/",
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    return _with_expiry(_check(response, "TikTok token exchange"))


def tiktok_access_token(creds: dict, client: httpx.Client) -> tuple[str, dict]:
    expires = creds.get("expires_at")
    if creds.get("access_token") and expires and datetime.fromisoformat(expires) > datetime.now(UTC):
        return creds["access_token"], creds
    settings = get_settings()
    response = client.post(
        f"{_TIKTOK}/v2/oauth/token/",
        data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": creds.get("refresh_token", ""),
        },
    )
    fresh = _with_expiry({**creds, **_check(response, "TikTok token refresh")})
    return fresh["access_token"], fresh


def tiktok_publish(
    post, video: Path, creds: dict, client: httpx.Client, sleep=time.sleep
) -> tuple[str, str, dict]:
    token, creds = tiktok_access_token(creds, client)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    info = _check(
        client.post(f"{_TIKTOK}/v2/post/publish/creator_info/query/", headers=headers), "TikTok creator"
    )
    allowed = (info.get("data") or {}).get("privacy_level_options") or ["SELF_ONLY"]
    wanted = (post.options or {}).get("privacy") or "SELF_ONLY"
    privacy = wanted if wanted in allowed else "SELF_ONLY"
    size = video.stat().st_size
    init = _check(
        client.post(
            f"{_TIKTOK}/v2/post/publish/video/init/",
            headers=headers,
            json={
                "post_info": {"title": post.title, "privacy_level": privacy, "is_aigc": True},
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
        ),
        "TikTok init",
    )
    data = init.get("data") or {}
    publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
    if not publish_id or not upload_url:
        raise PublishError("TikTok did not return an upload address.", retryable=True)
    try:
        sent = client.put(
            upload_url,
            headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"},
            content=video.read_bytes(),
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"TikTok upload did not confirm: {exc}", uncertain=True) from exc
    if sent.status_code >= 400:
        raise PublishError(f"TikTok upload: HTTP {sent.status_code}", uncertain=sent.status_code >= 500)
    for _ in range(30):
        status = _check(
            client.post(
                f"{_TIKTOK}/v2/post/publish/status/fetch/", headers=headers, json={"publish_id": publish_id}
            ),
            "TikTok status",
        )
        state = (status.get("data") or {}).get("status", "")
        if state == "PUBLISH_COMPLETE":
            ids = (status.get("data") or {}).get("publicaly_available_post_id") or []
            post_id = str(ids[0]) if ids else publish_id
            return post_id, f"https://www.tiktok.com/video/{post_id}" if ids else "", creds
        if state == "FAILED":
            reason = (status.get("data") or {}).get("fail_reason", "unknown")
            raise PublishError(f"TikTok rejected the video: {reason}")
        sleep(5)
    raise PublishError("TikTok did not finish processing in time.", uncertain=True)


# ---------------------------------------------------------------------------------------------- Meta


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{get_settings().meta_graph_version}/{path.lstrip('/')}"


def _rupload(kind: str, ident: str) -> str:
    return f"https://rupload.facebook.com/{kind}/{get_settings().meta_graph_version}/{ident}"


def instagram_publish(
    post, video: Path, creds: dict, client: httpx.Client, sleep=time.sleep
) -> tuple[str, str, dict]:
    token = creds.get("page_access_token") or creds.get("access_token")
    ig_user = creds.get("ig_user_id")
    if not token or not ig_user:
        raise PublishError("Instagram is not connected. Run connect-meta once.")
    container = _check(
        client.post(
            _graph(f"{ig_user}/media"),
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": post.description,
                "share_to_feed": "true",
                "access_token": token,
            },
        ),
        "Instagram container",
    )
    container_id = container.get("id")
    if not container_id:
        raise PublishError("Instagram did not return a container.", retryable=True)
    size = video.stat().st_size
    try:
        sent = client.post(
            _rupload("ig-api-upload", container_id),
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            content=video.read_bytes(),
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"Instagram upload did not confirm: {exc}", retryable=True) from exc
    _check(sent, "Instagram upload")
    for _ in range(40):
        status = _check(
            client.get(_graph(container_id), params={"fields": "status_code", "access_token": token}),
            "Instagram status",
        )
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise PublishError(f"Instagram could not process the video ({code}).")
        sleep(5)
    else:
        raise PublishError("Instagram did not finish processing in time.", retryable=True)
    # Until media_publish succeeds nothing is public, so a failure before this point is safe to retry.
    try:
        published = client.post(
            _graph(f"{ig_user}/media_publish"), data={"creation_id": container_id, "access_token": token}
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"Instagram publish did not confirm: {exc}", uncertain=True) from exc
    media_id = _check(published, "Instagram publish").get("id")
    if not media_id:
        raise PublishError("Instagram returned no media id.", uncertain=True)
    link = _check(
        client.get(_graph(media_id), params={"fields": "permalink", "access_token": token}), "Permalink"
    )
    return media_id, link.get("permalink", ""), creds


def facebook_publish(post, video: Path, creds: dict, client: httpx.Client) -> tuple[str, str, dict]:
    token = creds.get("page_access_token")
    page = creds.get("page_id")
    if not token or not page:
        raise PublishError("The Facebook Page is not connected. Run connect-meta once.")
    started = _check(
        client.post(_graph(f"{page}/video_reels"), data={"upload_phase": "start", "access_token": token}),
        "Facebook Reel start",
    )
    video_id = started.get("video_id")
    if not video_id:
        raise PublishError("Facebook did not return a video id.", retryable=True)
    size = video.stat().st_size
    try:
        sent = client.post(
            _rupload("video-upload", video_id),
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            content=video.read_bytes(),
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"Facebook upload did not confirm: {exc}", retryable=True) from exc
    _check(sent, "Facebook upload")
    try:
        finished = client.post(
            _graph(f"{page}/video_reels"),
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": post.description,
                "access_token": token,
            },
        )
    except httpx.HTTPError as exc:
        raise PublishError(f"Facebook publish did not confirm: {exc}", uncertain=True) from exc
    if not _check(finished, "Facebook Reel finish").get("success", False):
        raise PublishError("Facebook did not confirm the Reel.", uncertain=True)
    return video_id, f"https://www.facebook.com/reel/{video_id}", creds


PUBLISHERS = {
    "youtube_short": youtube_publish,
    "youtube_long": youtube_publish,
    "tiktok": tiktok_publish,
    "instagram_reel": instagram_publish,
    "facebook_reel": facebook_publish,
}
