"""One-command local render. Run from the repository root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "render-short",
                "--tts",
                "espeak",
                "--language",
                "en",
                "--title",
                "How machines learn from examples",
            ]
        )
    )
