"""Upload one approved Money Minds video to our own YouTube channel (complete, working script).

This is the only way the project uploads to YouTube. It is used by the channel owner for their
own channel; there are no other users.

Flow:
1. The channel owner signs in once with Google OAuth (`python -m app.cli connect-youtube`).
   The refresh token is stored locally in data/secrets/<brand>/youtube.json, never in Git.
2. A video is produced locally and a person reviews and approves it
   (`python -m app.cli approve --job JOB_ID`).
3. This script uploads that one file with the YouTube Data API v3 resumable upload
   (videos.insert, part=snippet,status), with privacy set to private unless --public is given.

Usage (Windows CMD, from the project folder):
    python scripts\\youtube_upload.py --video output\\review\\...\\video.mp4 --title "..." --description "..."

Quota: one videos.insert call (about 1,600 units) per video, at most a few videos per day.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

SECRETS = Path("data/secrets")
TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_URI = "https://www.googleapis.com/upload/youtube/v3/videos"


def client_secret() -> dict:
    data = json.loads((SECRETS / "youtube_client_secret.json").read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def access_token(brand: str, http: httpx.Client) -> str:
    path = SECRETS / brand / "youtube.json"
    creds = json.loads(path.read_text(encoding="utf-8"))
    expires = creds.get("expires_at")
    if creds.get("access_token") and expires and datetime.fromisoformat(expires) > datetime.now(UTC):
        return creds["access_token"]
    secret = client_secret()
    response = http.post(
        secret.get("token_uri", TOKEN_URI),
        data={
            "client_id": secret["client_id"],
            "client_secret": secret.get("client_secret", ""),
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    fresh = response.json()
    creds.update(fresh)
    creds["expires_at"] = (
        datetime.now(UTC) + timedelta(seconds=int(fresh.get("expires_in", 3600)) - 60)
    ).isoformat()
    path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    return creds["access_token"]


def upload(video: Path, title: str, description: str, tags: list[str], privacy: str, brand: str) -> str:
    with httpx.Client(timeout=600) as http:
        token = access_token(brand, http)
        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": "27",  # Education
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,  # the narration is a synthetic (text-to-speech) voice
            },
        }
        start = http.post(
            UPLOAD_URI,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video.stat().st_size),
            },
            content=json.dumps(metadata),
        )
        start.raise_for_status()
        session_url = start.headers["Location"]
        sent = http.put(session_url, headers={"Content-Type": "video/mp4"}, content=video.read_bytes())
        sent.raise_for_status()
        return sent.json()["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload one approved video to our own YouTube channel.")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--brand", default="money-minds")
    parser.add_argument("--public", action="store_true", help="Publish publicly (default: private).")
    args = parser.parse_args()
    if not args.video.is_file():
        print(f"Video not found: {args.video}")
        return 1
    video_id = upload(
        args.video, args.title, args.description, args.tag, "public" if args.public else "private", args.brand
    )
    print(f"Uploaded: https://www.youtube.com/shorts/{video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
