"""What holds the scripts and the integration together, without a browser."""
import re
from pathlib import Path

PKG = Path(__file__).parent.parent / "custom_components" / "noxcast"
FRONTEND = PKG / "frontend"
COMMON = '"./noxcast-common.js"'


def test_every_script_reads_the_payload_schema_of_the_integration():
    version = re.search(r"^WS_SCHEMA_VERSION = (\d+)", (PKG / "const.py").read_text(encoding="utf-8"), re.M).group(1)
    found = {p.name: m for p in FRONTEND.glob("*.js")
             for m in re.findall(r"^(?:export )?const W?S?_?SCHEMA = (\d+)", p.read_text(encoding="utf-8"), re.M)}
    assert "noxcast-common.js" in found and set(found.values()) == {version}


def test_the_shared_module_is_loaded_with_the_importers_cache_buster():
    panel = (PKG / "panel.py").read_text(encoding="utf-8")
    versions = re.findall(r"_version, ([^)]*)\)", panel)
    for path in FRONTEND.glob("*.js"):
        text = path.read_text(encoding="utf-8")
        if path.name == "noxcast-common.js" or COMMON not in text:
            continue
        assert f'{COMMON} + new URL(import.meta.url).search' in text, path.name
    # a script that imports it and is versioned on its own must have it in that version, or an update
    # pairs a new script with the old module
    for args in versions:
        names = re.findall(r'"([^"]+\.js)"', args)
        if any(COMMON in (FRONTEND / n).read_text(encoding="utf-8") for n in names):
            assert "noxcast-common.js" in names, names
