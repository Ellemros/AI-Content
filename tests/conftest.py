import pytest
from app.config import Settings, get_settings
from app.core.database import get_sessionmaker, init_db, reset_engine
from app.providers.local_tts import kokoro_available

KOKORO_READY = kokoro_available(Settings())


def pytest_collection_modifyitems(config, items):
    """Integration tests use the real Kokoro voice and FFmpeg.

    Without the model they are skipped, not faked.
    """
    if KOKORO_READY:
        return
    skip = pytest.mark.skip(reason="Kokoro model missing. Run `python -m app.cli install-voice`.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("TTS_PROVIDER", "kokoro")
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
