"""Cached model responses, keyed by the request payload."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIResponseCache


def make_cache_key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached(session: Session, key: str) -> dict | None:
    row = session.scalar(select(AIResponseCache).where(AIResponseCache.cache_key == key))
    if row is None:
        return None
    return row.response


def store_cached(session: Session, *, key: str, provider: str, operation: str, response: dict) -> None:
    session.add(
        AIResponseCache(
            cache_key=key,
            provider=provider,
            operation=operation,
            response=response,
        )
    )
    session.flush()
