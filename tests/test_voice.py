from app.config import get_settings
from app.core.errors import AppError
from app.schemas.content import TopicCreate
from app.services.scripts import current_version, generate_script
from app.services.topics import create_topic
from app.services.voice import ensure_audio, speak


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


def test_elevenlabs_refusal_still_writes_a_local_voice(monkeypatch, tmp_path):
    def refuse(*_args, **_kwargs):
        raise AppError("ElevenLabs returned HTTP 401: free tier disabled")

    def edge(text, *, voice, stability, similarity, out_path):
        out_path.write_bytes(b"ID3" + b"x" * 200)

    monkeypatch.setattr("app.services.voice.elevenlabs_provider.synthesize", refuse)
    monkeypatch.setattr("app.services.voice.synthesize_edge", edge)
    monkeypatch.setattr("app.services.voice.edge_available", lambda: True)
    raw = tmp_path / "raw.mp3"
    actual = speak(
        None,
        "People keep the option that was already selected when they plan a route home.",
        engine="elevenlabs",
        voice="voice-id",
        language="en",
        stability=0.5,
        similarity=0.75,
        raw_path=raw,
        settings=get_settings(),
    )
    assert actual == "edge"
    assert raw.is_file()
    assert "401" in speak.last_warning
    assert "not ready for publishing" in speak.last_warning
