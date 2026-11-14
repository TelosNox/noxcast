"""Write the explanatory drawings (custom_components/noxcast/concepts.py) to docs/images for the README.

Run from the repository root with the development environment: python .github/scripts/render_concepts.py.
tests/test_concepts.py fails when the files differ from what the code draws.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_components.noxcast import concepts  # noqa: E402

LANGS = ("en", "de")


def files() -> dict[Path, str]:
    return {ROOT / "docs" / "images" / f"concept-{name}.{lang}.svg": concepts.render(name, lang) + "\n"
            for name in concepts.DRAWINGS for lang in LANGS}


if __name__ == "__main__":
    for path, svg in files().items():
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(path.relative_to(ROOT))
