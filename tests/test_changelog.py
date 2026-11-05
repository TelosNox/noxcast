"""The changelog check: a pull request that changes the integration says what changes for users, or why nothing."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("check_changelog", ROOT / ".github/scripts/check_changelog.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

BASE = "# Changelog\n\n## Unreleased\n\n### Fixed\n- Old fix.\n\n## [0.1.0] - 2026-11-03\n\n### Added\n- All.\n"
CODE = ["custom_components/noxcast/model.py"]


def test_a_change_of_the_integration_needs_an_entry():
    assert check.problem(CODE, BASE, BASE, "Refactors the model.")


def test_a_new_entry_in_unreleased_is_enough():
    head = BASE.replace("- Old fix.\n", "- Old fix.\n- New fix.\n")
    assert check.problem(CODE, BASE, head, "") is None


def test_an_entry_in_an_old_version_is_not_enough():
    head = BASE.replace("- All.\n", "- All.\n- Late.\n")
    assert check.problem(CODE, BASE, head, "")


def test_a_release_moves_the_section():
    head = BASE.replace("## Unreleased\n\n", "## Unreleased\n\n## [0.1.1] - 2026-11-06\n\n")
    assert check.problem(["custom_components/noxcast/manifest.json", "CHANGELOG.md"], BASE, head, "") is None


def test_a_reason_in_the_description_waives_it():
    assert check.problem(CODE, BASE, BASE, "Moves code.\n\nNo changelog entry: nothing changes for users.") is None
    assert check.problem(CODE, BASE, BASE, "**No changelog entry**: internal.") is None
    assert check.problem(CODE, BASE, BASE, "There is no changelog entry needed.")    # only at the start of a line


def test_changes_outside_the_integration_need_none():
    assert check.problem(["docs/concept.md", "tests/test_model.py", ".github/workflows/validate.yml"], BASE, BASE, "") is None


def test_the_repository_changelog_has_an_unreleased_section():
    assert "## Unreleased" in (ROOT / check.CHANGELOG).read_text(encoding="utf-8")
