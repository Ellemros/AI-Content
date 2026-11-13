import sqlite3

from app.config import get_settings
from app.core.database import get_engine, init_db, reset_engine
from app.models import Asset
from sqlalchemy import text


def test_existing_sqlite_file_gains_phase2_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "phase1.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE assets (
            id VARCHAR(36) PRIMARY KEY,
            kind VARCHAR(32) NOT NULL,
            path TEXT NOT NULL,
            source VARCHAR(160) NOT NULL,
            license VARCHAR(80) NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            metadata JSON,
            created_at DATETIME
        )
        """
    )
    conn.execute("CREATE TABLE videos (id VARCHAR(36) PRIMARY KEY)")
    conn.execute("CREATE TABLE api_usage (id VARCHAR(36) PRIMARY KEY, month_key VARCHAR(7))")
    conn.execute(
        """
        INSERT INTO assets (id, kind, path, source, license, checksum, metadata, created_at)
        VALUES ('asset-1', 'graphic', '/tmp/card.png', 'pillow', 'original', 'abc', '{}', '2026-01-01')
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    reset_engine()
    try:
        init_db()
        with get_engine().connect() as connection:
            asset_cols = {row[1] for row in connection.execute(text("PRAGMA table_info(assets)"))}
            video_cols = {row[1] for row in connection.execute(text("PRAGMA table_info(videos)"))}
            usage_cols = {row[1] for row in connection.execute(text("PRAGMA table_info(api_usage)"))}
        assert {"name", "library"} <= asset_cols
        assert {"master_path", "subtitle_edited"} <= video_cols
        assert {"video_id", "script_id"} <= usage_cols
        from app.core.database import get_sessionmaker

        session = get_sessionmaker()()
        asset = session.get(Asset, "asset-1")
        assert asset is not None
        assert asset.name == ""
        assert asset.library is False
        session.close()
    finally:
        reset_engine()
        get_settings.cache_clear()
