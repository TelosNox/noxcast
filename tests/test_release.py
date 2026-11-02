"""The release check: tag, manifest and changelog must agree before a GitHub release is created."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("release_notes", ROOT / ".github/scripts/release_notes.py")
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)
spec = importlib.util.spec_from_file_location("check_latest_ha", ROOT / ".github/scripts/check_latest_ha.py")
latest_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest_ha)

CHANGELOG = """# Changelog

## Unreleased

## [0.2.0] - 2026-12-01

### Fixed
- Something.

## [0.2.0b1] - 2026-11-20

### Added
- Early.

## [0.1.0] - 2026-11-03

### Added
- Everything.
"""


@pytest.mark.parametrize(("tag", "manifest"), [("v0.2.0", "0.2.0"), ("v0.2.0b1", "0.2.0b1")])
def test_tag_matches_manifest(tag, manifest):
    assert notes.tag_version(tag, manifest) == manifest


@pytest.mark.parametrize(("tag", "manifest"), [("v0.2.0", "0.2.1"), ("0.2.0", "0.2.0"), ("v0.2", "0.2"), ("v0.2.0-beta", "0.2.0-beta")])
def test_tag_refused(tag, manifest):
    with pytest.raises(ValueError):
        notes.tag_version(tag, manifest)


def test_section_is_only_that_version():
    assert notes.section(CHANGELOG, "0.2.0") == "### Fixed\n- Something.\n"
    assert notes.section(CHANGELOG, "0.2.0b1") == "### Added\n- Early.\n"
    assert notes.section(CHANGELOG, "0.1.0") == "### Added\n- Everything.\n"


@pytest.mark.parametrize("version", ["0.3.0", "Unreleased"])
def test_missing_or_empty_section_refused(version):
    with pytest.raises(ValueError):
        notes.section(CHANGELOG, version)


def test_repository_files_have_the_expected_form():
    assert notes.TAG.fullmatch("v" + json.loads((ROOT / notes.MANIFEST).read_text(encoding="utf-8"))["version"])
    assert "## Unreleased" in (ROOT / notes.CHANGELOG).read_text(encoding="utf-8")


def test_a_held_back_test_package_is_a_problem():
    assert latest_ha.problem("0.13.375", "0.13.375") is None
    assert "0.13.380" in latest_ha.problem("0.13.375", "0.13.380")


def test_month_of_a_home_assistant_version():
    assert latest_ha.month("2026.10.4") == latest_ha.month("2026.10.0") == "2026.10"
