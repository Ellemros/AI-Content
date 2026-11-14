"""SQLAlchemy engine. SQLite for local runs, PostgreSQL-compatible types."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models.tables import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    kwargs: dict[str, object] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


# Columns added after Phase 1. create_all does not alter an existing SQLite file.
# Startup adds any missing column so a database from Phase 1 keeps its rows.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "assets": {
        "name": "VARCHAR(160) NOT NULL DEFAULT ''",
        "library": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "api_usage": {
        "video_id": "VARCHAR(36)",
        "script_id": "VARCHAR(36)",
    },
    "videos": {
        "master_path": "TEXT NOT NULL DEFAULT ''",
        "subtitle_edited": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "brands": {
        "automation_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "schedule_interval_minutes": "INTEGER NOT NULL DEFAULT 1440",
        "next_production_at": "DATETIME",
        "last_production_at": "DATETIME",
        "last_automation_status": "VARCHAR(32) NOT NULL DEFAULT 'idle'",
        "last_automation_error": "TEXT NOT NULL DEFAULT ''",
        "max_videos_per_day": "INTEGER NOT NULL DEFAULT 1",
        "max_spend_usd_per_day": "NUMERIC(12, 4) NOT NULL DEFAULT 5",
        "video_spend_cap_usd": "NUMERIC(12, 4)",
        "max_attempts": "INTEGER NOT NULL DEFAULT 2",
        "content_formats": "JSON NOT NULL DEFAULT '[]'",
        "editorial": "JSON NOT NULL DEFAULT '{}'",
    },
    "production_jobs": {
        "attempts": "INTEGER NOT NULL DEFAULT 1",
    },
    "content_opportunities": {
        "question": "TEXT NOT NULL DEFAULT ''",
        "source_urls": "JSON NOT NULL DEFAULT '[]'",
    },
    "production_costs": {
        "brand_id": "VARCHAR(36)",
        "opportunity_id": "VARCHAR(36)",
        "video_id": "VARCHAR(36)",
    },
}


def ensure_added_columns(engine: Engine | None = None) -> list[str]:
    """Add Phase 2 columns on a database that was created before them.

    SQLite and this MVP have no Alembic revision. `create_all` creates missing
    tables and leaves existing tables alone. For each column above that
    `PRAGMA table_info` does not already list, this runs `ALTER TABLE ... ADD COLUMN`.
    New databases already have the columns, so this is a no-op there. Added
    columns are nullable or have a constant default, so existing rows stay valid.
    """
    engine = engine or get_engine()
    if engine.dialect.name != "sqlite":
        return []
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                added.append(f"{table}.{name}")
    return added


def _ensure_production_claim_index(engine: Engine | None = None) -> None:
    """One running job per opportunity. A second worker cannot start the same render."""
    engine = engine or get_engine()
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        return
    inspector = inspect(engine)
    if not inspector.has_table("production_jobs"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_production_job_running "
                "ON production_jobs (opportunity_id) WHERE status = 'running'"
            )
        )


def init_db() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("assets", "audio", "videos", "thumbnails", "exports", "cache", "library"):
        (settings.data_dir / name).mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_added_columns(engine)
    _ensure_production_claim_index(engine)


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
