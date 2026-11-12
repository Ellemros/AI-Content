from app.schemas.content import TopicCreate
from app.services.scripts import current_version, generate_script
from app.services.topics import create_topic
from app.services.voice import ensure_audio


def test_same_settings_skip_regeneration(db, tmp_path):
    topic = create_topic(db, TopicCreate(title="Voice skip check"))
    script = generate_script(db, topic, language="en", video_format="short")
    version = current_version(db, script)
    calls = []

    def fake(text, path, settings):
        path.write_bytes(b"not-empty")
        calls.append(settings["voice"])
        return {"duration": 1.5, "provider": "fake"}

    first = ensure_audio(
        db,
        version,
        language="en",
        stability=0.5,
        similarity=0.75,
        voice_id="en-us",
        engine="espeak",
        synthesizer=fake,
    )
    second = ensure_audio(
        db,
        version,
        language="en",
        stability=0.5,
        similarity=0.75,
        voice_id="en-us",
        engine="espeak",
        synthesizer=fake,
    )
    assert first.id == second.id
    assert calls == ["en-us"]


def test_changed_settings_regenerate(db):
    topic = create_topic(db, TopicCreate(title="Voice setting change"))
    script = generate_script(db, topic, language="cs", video_format="short")
    version = current_version(db, script)
    calls = []

    def fake(text, path, settings):
        path.write_bytes(b"not-empty")
        calls.append(round(settings["stability"], 2))
        return {"duration": 1.5, "provider": "fake"}

    ensure_audio(
        db,
        version,
        language="cs",
        stability=0.4,
        similarity=0.75,
        voice_id="cs",
        engine="espeak",
        synthesizer=fake,
    )
    ensure_audio(
        db,
        version,
        language="cs",
        stability=0.9,
        similarity=0.75,
        voice_id="cs",
        engine="espeak",
        synthesizer=fake,
    )
    assert calls == [0.4, 0.9]
