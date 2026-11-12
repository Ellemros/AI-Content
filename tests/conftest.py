import pytest
from app.config import get_settings
from app.core.database import get_sessionmaker, init_db, reset_engine


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("TTS_PROVIDER", "espeak")
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "25")
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "0")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
        reset_engine()
        get_settings.cache_clear()
