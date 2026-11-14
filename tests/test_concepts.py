"""The explanatory drawings: valid SVG in both languages, and the README files are what the code draws."""
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from custom_components.noxcast import concepts

ROOT = Path(__file__).parent.parent


def _render_script():
    spec = importlib.util.spec_from_file_location("render_concepts", ROOT / ".github" / "scripts" / "render_concepts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", sorted(concepts.DRAWINGS))
@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_drawing_is_valid_svg_within_its_width(name: str, lang: str) -> None:
    svg = concepts.render(name, lang)
    root = ET.fromstring(svg)
    assert root.get("viewBox").startswith(f"0 0 {concepts.WIDTH} ")
    # nothing drawn outside the picture
    for x in re.findall(r' x="(-?[\d.]+)"', svg):
        assert 0 <= float(x) <= concepts.WIDTH, (name, x)


def test_both_languages_name_the_same_things() -> None:
    assert concepts.TEXT["de"].keys() == concepts.TEXT["en"].keys()
    assert concepts.render("unknown", "de") is None
    assert concepts.render("blocks", "fr") == concepts.render("blocks", "en")      # other languages: English


def test_the_readme_files_are_what_the_code_draws() -> None:
    for path, svg in _render_script().files().items():
        assert path.exists() and path.read_text(encoding="utf-8") == svg, \
            f"{path.name} is outdated: run python .github/scripts/render_concepts.py"
