"""System fonts that cover Czech and English."""

from pathlib import Path

from app.config import get_settings
from app.services.probes import MediaError


def find_font(bold: bool = False) -> tuple[Path, str]:
    candidates: list[Path] = []
    configured = get_settings().font_path.strip()
    if configured:
        candidates.append(Path(configured))
    dejavu = Path("/usr/share/fonts/truetype/dejavu")
    if bold:
        candidates.extend(
            [
                dejavu / "DejaVuSans-Bold.ttf",
                Path("C:/Windows/Fonts/arialbd.ttf"),
                Path("C:/Windows/Fonts/segoeuib.ttf"),
                dejavu / "DejaVuSans.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                dejavu / "DejaVuSans.ttf",
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("C:/Windows/Fonts/segoeui.ttf"),
                dejavu / "DejaVuSans-Bold.ttf",
            ]
        )
    for path in candidates:
        if path.is_file():
            name = path.name.casefold()
            if "dejavu" in name:
                family = "DejaVu Sans"
            elif "segoe" in name:
                family = "Segoe UI"
            else:
                family = "Arial"
            return path, family
    raise MediaError("No font found. Install fonts-dejavu or set FONT_PATH.")
