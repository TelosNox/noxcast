"""Check a release tag against the manifest and write its changelog section to a file for the GitHub release."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST = "custom_components/noxcast/manifest.json"
CHANGELOG = "CHANGELOG.md"
TAG = re.compile(r"v(\d+\.\d+\.\d+(?:b\d+)?)")


def tag_version(tag: str, manifest_version: str) -> str:
    """The version of a tag, which must be well formed and the one in the manifest."""
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"tag {tag} is not vX.Y.Z or vX.Y.ZbN")
    if match[1] != manifest_version:
        raise ValueError(f"tag {tag} does not match manifest version {manifest_version}")
    return match[1]


def section(changelog: str, version: str) -> str:
    """The text of a version's section, without its heading."""
    match = re.search(rf"^## \[?{re.escape(version)}\]?(?: [^\n]*)?\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match or not match[1].strip():
        raise ValueError(f"{CHANGELOG} has no text for version {version}")
    return match[1].strip() + "\n"


def main(tag: str, out: str) -> None:
    version = tag_version(tag, json.loads(Path(MANIFEST).read_text(encoding="utf-8"))["version"])
    Path(out).write_text(section(Path(CHANGELOG).read_text(encoding="utf-8"), version), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
