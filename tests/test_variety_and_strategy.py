"""Scene variety, format rotation, source cleaning, and stricter safety holds. No network."""

import random

from app.services.business.signals import hold_reason
from app.services.production import scene_engine
from app.services.production.extract import extract_article
from app.services.production.scripting import dropped_study_model
from app.services.strategy import STRUCTURES, choose_format


def test_neighbouring_scenes_never_repeat_layout_setting_or_camera():
    specs = [
        scene_engine.normalise({"layout": "solo", "setting": "room", "camera": "static"}, i, 7)
        for i in range(6)
    ]
    specs = scene_engine.diversify(specs)
    for prev, cur in zip(specs, specs[1:], strict=False):
        assert cur.layout != prev.layout
        assert cur.setting != prev.setting
        assert cur.camera != prev.camera
        assert cur.transition != prev.transition


def test_emphasis_cards_are_capped_and_numbers_win():
    specs = [
        scene_engine.normalise({"emphasis": text}, i, 1)
        for i, text in enumerate(["hook", "word", "235 ms", "other", "more", "70%"])
    ]
    specs = scene_engine.diversify(specs)
    kept = [spec.emphasis for spec in specs if spec.emphasis]
    assert len(kept) == 3 and "235 ms" in kept and "70%" in kept and "hook" in kept


def test_invalid_direction_is_filled_not_fatal():
    spec = scene_engine.normalise(
        {"layout": "spaceship", "props": ["laser", "coins"], "chart": [{"x": 1}]}, 0, 3
    )
    assert spec.layout in scene_engine.LAYOUTS
    assert spec.props == ("coins",)
    frame = scene_engine.compose(spec, 0.5, 1.0)
    assert frame.size == (1080, 1920)


def test_every_layout_and_style_draws():
    for style in scene_engine.STYLES:
        for layout in scene_engine.LAYOUTS:
            direction = {
                "layout": layout,
                "props": ["coins", "clock"],
                "labels": ["A", "B"],
                "emphasis": "3 times",
                "chart": [{"label": "a", "value": 2}, {"label": "b", "value": 5}],
            }
            scene_engine.compose(scene_engine.normalise(direction, 1, 2, style), 0.6, 1.2)


def test_formats_rotate_until_results_exist(db):
    from app.services.automation import create_brand

    brand = create_brand(db, name="Rotation", niche="money", audience="adults")
    plan = choose_format(db, brand, rng=random.Random(1))
    assert plan["structure"] in STRUCTURES and plan["reason"].startswith("exploring")


def test_source_footer_and_link_lists_are_not_evidence():
    html = (
        "<article><p>Coffee tastes bitter at first, and people learn to like it over many weeks.</p>"
        "<p>In mice, a gut signal taught the brain which flavours bring energy later on.</p></article>"
        "<ul><li>Strange Whale Face.</li><li>Dark Energy Twist.</li></ul>"
        "<p>Story Source:</p><p>Materials provided by a university press office for this story.</p>"
    )
    text = extract_article(html)
    assert "Whale" not in text and "Materials provided" not in text
    assert "gut signal" in text


def test_a_mouse_study_must_say_mice():
    source = "Researchers fed mice sugar water. The mice learned. A mouse brain region lit up."
    assert dropped_study_model("Your gut teaches your brain to love coffee.", source) == "mice"
    assert dropped_study_model("In mice, the gut teaches the brain to love sweet flavours.", source) == ""


def test_stricter_medical_hold_and_holds_are_not_released(db):
    from app.services.automation import create_brand

    brand = create_brand(db, name="Holds", niche="money", audience="adults", block_medical=True)
    assert hold_reason("A simple daily routine linked to less pain and depression", "", brand)
    assert hold_reason("Occasional marijuana use linked to stroke", "", brand)
    assert not hold_reason("Why tipping keeps rising", "", brand)
