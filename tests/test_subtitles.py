import pytest
from app.services.subtitles import format_timestamp, render_srt, time_cues


def test_cues_cover_the_duration_without_gaps():
    text = (
        "Machines learn by example. A model keeps patterns from the examples it was shown. "
        "Ask what it saw, what it missed, and where a person still decides."
    )
    cues = time_cues(text, 9.0)
    assert cues[0].start == 0
    assert cues[-1].end == 9.0
    for previous, nxt in zip(cues, cues[1:], strict=False):
        assert nxt.start == previous.end
        assert nxt.end > nxt.start
    assert all(cue.text.strip() for cue in cues)


def test_single_sentence_uses_the_full_duration():
    cues = time_cues("One short line.", 2.5)
    assert len(cues) == 1
    assert cues[0].start == 0
    assert cues[0].end == 2.5


def test_empty_narration_is_rejected():
    with pytest.raises(ValueError):
        time_cues("   ", 3)


def test_srt_timestamps():
    cues = time_cues("Hello there. Second line follows.", 3)
    body = render_srt(cues)
    assert body.startswith("1\n00:00:00,000 -->")
    assert "--> " in body
    assert format_timestamp(1.5) == "00:00:01,500"
    assert format_timestamp(3661.007) == "01:01:01,007"
