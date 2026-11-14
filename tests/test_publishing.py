"""Publishing queue and platform connectors.

MOCKED: every platform call here goes to httpx.MockTransport. These tests check the
request sequence, the safety gates, and the retry rules. They do not prove that a
live platform accepts the upload; that needs a connected account (see README).
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import get_settings
from app.models import PlatformPost, Script, ScriptVersion, Topic, Video
from app.services.automation import create_brand, set_automation_enabled
from app.services.publishing import credentials, metadata
from app.services.publishing import queue as publishing


def _video(db, tmp_path, *, status="ready", approved=True):
    topic = Topic(
        title="Why tipping keeps rising",
        category="science",
        target_language="en",
        target_platform="youtube_shorts",
        fingerprint=str(tmp_path),
    )
    db.add(topic)
    db.flush()
    script = Script(topic_id=topic.id, language="en", format="short", title="Why tipping keeps rising")
    db.add(script)
    db.flush()
    version = ScriptVersion(
        script_id=script.id,
        version_number=1,
        title="Why tipping keeps rising",
        hook="Tips keep climbing.",
        narration="Tips keep climbing. Service does not.",
        description="Tipping prompts are spreading. Researchers looked at what they do.",
        hashtags=["#money", "psychology", "#money"],
    )
    db.add(version)
    db.flush()
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 2048)
    video = Video(
        script_id=script.id,
        script_version_id=version.id,
        language="en",
        format="short",
        profile="short_9_16",
        path=str(path),
        status=status,
        approved=approved,
    )
    db.add(video)
    db.flush()
    return video


def _brand(db, mode="approval"):
    brand = create_brand(db, name="Money Minds Test", niche="money", audience="adults")
    brand.publishing_mode = mode
    brand.daily_publish_limit = 2
    db.flush()
    return brand


@pytest.fixture()
def enabled(monkeypatch, tmp_path, db):
    monkeypatch.setenv("PUBLISHING_ENABLED", "true")
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "secrets"))
    get_settings.cache_clear()
    set_automation_enabled(db, True)
    yield
    get_settings.cache_clear()


def test_metadata_fits_each_platform():
    yt = metadata.build(
        "youtube_short", title="x" * 120, description="d", hashtags=["#a", "a", "b"], sources=["https://s"]
    )
    assert yt["title"].endswith("#Shorts") and len(yt["title"]) <= 100
    assert "Source: https://s" in yt["description"] and metadata.DISCLOSURE in yt["description"]
    assert yt["tags"] == ["a", "b"]
    tt = metadata.build("tiktok", title="t", description="d" * 3000, hashtags=[], sources=[])
    assert len(tt["title"]) <= 2200
    ig = metadata.build("instagram_reel", title="t", description="d", hashtags=["#x"] * 40, sources=[])
    assert ig["description"].count("#x") == 1


def test_nothing_uploads_while_publishing_is_off(db, tmp_path):
    brand = _brand(db)
    video = _video(db, tmp_path)
    publishing.plan_posts(db, video, brand)
    publishing.queue_ready(db, brand)
    called = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: called.append(request) or httpx.Response(500))
    )
    result = publishing.publish_due(db, client=client, now=datetime.now(UTC) + timedelta(days=3))
    assert result["status"] == "refused" and "PUBLISHING_ENABLED" in result["reason"]
    assert called == []


def test_needs_review_and_unapproved_videos_are_never_queued(db, tmp_path):
    brand = _brand(db, mode="approval")
    review = _video(db, tmp_path / "a", status="needs_review", approved=True)
    unapproved = _video(db, tmp_path / "b", status="ready", approved=False)
    for video in (review, unapproved):
        publishing.plan_posts(db, video, brand)
    assert publishing.queue_ready(db, brand) == 0
    brand.publishing_mode = "autonomous"
    assert publishing.queue_ready(db, brand) == 4  # the ready one, on four short platforms
    statuses = {post.video_id: post.status for post in db.query(PlatformPost)}
    assert statuses[review.id] == "planned"


def test_test_mode_queues_nothing(db, tmp_path):
    brand = _brand(db, mode="test")
    publishing.plan_posts(db, _video(db, tmp_path), brand)
    assert publishing.queue_ready(db, brand) == 0


def _youtube_transport(log, *, put_error=None):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append((request.method, request.url.host, request.url.path))
        if request.url.path == "/upload/youtube/v3/videos" and request.method == "POST":
            body = json.loads(request.content)
            assert body["status"]["privacyStatus"] == "private"
            assert body["status"]["containsSyntheticMedia"] is True
            return httpx.Response(200, headers={"Location": "https://upload.test/session"})
        if request.url.host == "upload.test":
            if put_error:
                raise put_error
            return httpx.Response(200, json={"id": "yt123"})
        return httpx.Response(404)

    return handler


def _connect_youtube(brand):
    credentials.save(
        brand.slug,
        "youtube_short",
        {"access_token": "tok", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
    )


def test_youtube_upload_publishes_once(db, tmp_path, enabled):
    brand = _brand(db)
    brand.identity = {**(brand.identity or {}), "platforms": ["youtube_short"]}
    video = _video(db, tmp_path)
    publishing.plan_posts(db, video, brand)
    assert publishing.queue_ready(db, brand) == 1
    _connect_youtube(brand)
    log = []
    client = httpx.Client(transport=httpx.MockTransport(_youtube_transport(log)))
    later = datetime.now(UTC) + timedelta(days=3)
    result = publishing.publish_due(db, client=client, now=later)
    assert result["published"] == 1
    post = db.query(PlatformPost).one()
    assert post.status == "published" and post.external_url == "https://www.youtube.com/shorts/yt123"
    again = publishing.publish_due(db, client=client, now=later)
    assert again["published"] == 0
    assert sum(1 for entry in log if entry[1] == "upload.test") == 1
    assert publishing.plan_posts(db, video, brand)[0].id == post.id


def test_an_unconfirmed_upload_is_not_retried(db, tmp_path, enabled):
    brand = _brand(db)
    brand.identity = {**(brand.identity or {}), "platforms": ["youtube_short"]}
    publishing.plan_posts(db, _video(db, tmp_path), brand)
    publishing.queue_ready(db, brand)
    _connect_youtube(brand)
    client = httpx.Client(
        transport=httpx.MockTransport(_youtube_transport([], put_error=httpx.ReadTimeout("timed out")))
    )
    later = datetime.now(UTC) + timedelta(days=3)
    publishing.publish_due(db, client=client, now=later)
    post = db.query(PlatformPost).one()
    assert post.status == "needs_check"
    publishing.publish_due(db, client=client, now=later + timedelta(days=1))
    assert db.query(PlatformPost).one().attempts == 1


def test_a_server_error_before_upload_is_retried_later(db, tmp_path, enabled):
    brand = _brand(db)
    brand.identity = {**(brand.identity or {}), "platforms": ["youtube_short"]}
    publishing.plan_posts(db, _video(db, tmp_path), brand)
    publishing.queue_ready(db, brand)
    _connect_youtube(brand)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    publishing.publish_due(db, client=client, now=datetime.now(UTC) + timedelta(days=3))
    post = db.query(PlatformPost).one()
    assert post.status == "queued" and post.attempts == 1 and "503" in post.last_error


def test_unconnected_platform_waits_without_spending_an_attempt(db, tmp_path, enabled):
    brand = _brand(db)
    brand.identity = {**(brand.identity or {}), "platforms": ["tiktok"]}
    publishing.plan_posts(db, _video(db, tmp_path), brand)
    publishing.queue_ready(db, brand)
    publishing.publish_due(
        db,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        now=datetime.now(UTC) + timedelta(days=3),
    )
    post = db.query(PlatformPost).one()
    assert post.status == "queued" and post.attempts == 0 and "not connected" in post.last_error


def test_tiktok_instagram_and_facebook_request_sequences(db, tmp_path, enabled):
    from app.services.publishing import connectors

    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"\x00" * 100)
    post = PlatformPost(platform="tiktok", title="caption", description="caption", options={})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}})
        if path.endswith("/video/init/"):
            assert json.loads(request.content)["post_info"]["privacy_level"] == "SELF_ONLY"
            return httpx.Response(
                200, json={"data": {"publish_id": "p1", "upload_url": "https://up.tiktok.test/x"}}
            )
        if request.url.host == "up.tiktok.test":
            assert request.headers["Content-Range"] == "bytes 0-99/100"
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            return httpx.Response(
                200, json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [42]}}
            )
        if path.endswith("/media") and request.method == "POST":
            return httpx.Response(200, json={"id": "c1"})
        if request.url.host == "rupload.facebook.com":
            assert request.headers["file_size"] == "100"
            return httpx.Response(200, json={"success": True})
        if path.endswith("/c1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "m1"})
        if path.endswith("/m1"):
            return httpx.Response(200, json={"permalink": "https://instagram.test/p/m1"})
        if path.endswith("/video_reels"):
            phase = dict(httpx.QueryParams(request.content.decode()))["upload_phase"]
            return httpx.Response(200, json={"video_id": "v9"} if phase == "start" else {"success": True})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    tid, turl, _ = connectors.tiktok_publish(
        post, video_path, {"access_token": "t", "expires_at": soon}, client, sleep=lambda _s: None
    )
    assert tid == "42" and turl.endswith("/42")
    meta = {"page_id": "page", "ig_user_id": "ig", "page_access_token": "pt"}
    mid, murl, _ = connectors.instagram_publish(post, video_path, meta, client, sleep=lambda _s: None)
    assert mid == "m1" and murl == "https://instagram.test/p/m1"
    fid, furl, _ = connectors.facebook_publish(post, video_path, meta, client)
    assert fid == "v9" and furl.endswith("/v9")
