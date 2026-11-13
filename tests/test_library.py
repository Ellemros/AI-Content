import io

from app.models import Asset
from app.services.assets import register_library_asset, resolve_visuals
from PIL import Image
from sqlalchemy import func, select


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (22, 58, 68)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_library_image_is_reused_and_cards_fill_the_gaps(db, tmp_path):
    asset = register_library_asset(
        db,
        name="Teal card",
        kind="image",
        source="Drawn for this project",
        license_name="owned",
        filename="teal.png",
        data=_png(),
    )
    scenes = [
        {"index": 1, "on_screen_text": "One", "asset_id": asset.id, "narration": "a", "visual": "v"},
        {"index": 2, "on_screen_text": "Two", "narration": "b", "visual": "v"},
    ]
    first, kinds = resolve_visuals(
        db,
        scenes=scenes,
        mood="curious",
        label="AI",
        size=(1080, 1920),
        work_dir=tmp_path / "one",
    )
    second, _kinds = resolve_visuals(
        db,
        scenes=scenes,
        mood="curious",
        label="AI",
        size=(1080, 1920),
        work_dir=tmp_path / "two",
    )
    assert first[0].id == asset.id
    assert second[0].id == asset.id
    assert kinds == ["image", "image"]
    assert first[1].id != asset.id
    assert first[1].library is False
    assert first[1].source == "pillow"
    library_count = db.scalar(select(func.count()).select_from(Asset).where(Asset.library.is_(True)))
    assert library_count == 1
