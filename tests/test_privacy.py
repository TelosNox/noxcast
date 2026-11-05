"""The privacy check and the Claude Code hook that runs it: private data must not reach the repository."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


privacy = _load("check_privacy", ".github/scripts/check_privacy.py")
guard = _load("guard", ".claude/hooks/guard.py")


def test_the_repository_holds_no_private_data():
    assert privacy.findings(ROOT, "all") == []


@pytest.mark.parametrize(("line", "kind"), [
    ('url = "http://192.168.1.20:8123"', "Home Assistant address"),               # privacy-ok
    ('url = "https://abcdef.ui.nabu.casa"', "Home Assistant address"),            # privacy-ok
    ("host 10.0.0.7", "private IP address"),                                      # privacy-ok
    ('"latitude": 50.1234', "coordinate"),                                        # privacy-ok
    ("lon=8.765", "coordinate"),                                                  # privacy-ok
    ("t = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig'", "token"),     # privacy-ok
    ('api_key = "abcdefghijklmnopqrstuvwxyz123456"', "API key"),                  # privacy-ok
])
def test_private_data_is_found(line, kind):
    assert (kind, 1) in privacy.scan(line)


@pytest.mark.parametrize("line", [
    '"url": "ws://homeassistant.local:8123/api/websocket"',
    '"latitude": 50.0',
    "version 2026.10.1",
    "apikey=SECRET",
    'lat = 50.1234  # privacy-ok: made up',
])
def test_harmless_lines_pass(line):
    assert privacy.scan(line) == []


def test_personal_values_come_from_the_local_files(tmp_path):
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({"home_assistant": {"url": "http://ha.home:8123"},  # privacy-ok
                                  "location": {"latitude": 51.98765, "longitude": 7.5, "altitude": 80},  # privacy-ok
                                  "arrays": [{"name": "Roof", "sensors": ["sensor.my_roof_power"], "tilt": 30}]}))
    token = tmp_path / "ha_token.txt"
    token.write_text("a-long-secret-token-value\n")
    values = privacy.local_values([config, token])
    assert set(values) == {"http://ha.home:8123",  # privacy-ok
                            "51.987", "sensor.my_roof_power", "a-long-secret-token-value"}
    assert privacy.scan("POWER = 'sensor.my_roof_power'\nx = 51.9876", values) == [
        ("entity ID or address from a local file", 1), ("coordinate from a local file", 2)]


@pytest.mark.parametrize(("command", "expected"), [
    ("git switch -c feat/x", ["switch"]),
    ("git checkout -b feat/x origin/main", ["switch"]),
    ("git checkout main", ["switch"]),
    ("gh pr checkout 12", ["switch"]),
    ("git checkout -- a.py", []),
    ("git worktree add .claude/worktrees/x -b feat/x origin/main", []),
    ("git add -A && git commit -m 'x' && git push -u origin feat/x", ["commit", "push"]),
    ("gh pr create --title x --body y", ["pr"]),
    ("git status; git log --oneline", []),
])
def test_the_hook_recognises_what_a_command_does(command, expected):
    assert [action for action, _ in guard.actions(command, ROOT)] == expected


def test_the_hook_follows_cd_and_dash_c():
    assert guard.actions("cd sub && git switch x", ROOT) == [("switch", (ROOT / "sub").resolve())]
    assert guard.actions("git -C other commit -m x", ROOT) == [("commit", (ROOT / "other").resolve())]



def test_a_pull_request_needs_a_review_section(tmp_path):
    assert guard.has_review('gh pr create --title x --body "Text\n\n## Review\nTier: Other"', tmp_path)
    assert not guard.has_review('gh pr create --title x --body "Text"', tmp_path)
    (tmp_path / "body.md").write_text("Text\n\n## Review\nTier: Other\n", encoding="utf-8")
    assert guard.has_review("gh pr create --title x --body-file body.md", tmp_path)
    assert not guard.has_review("gh pr create --title x --body-file missing.md", tmp_path)
    assert guard.has_review("gh pr create --title x --body-file=body.md", tmp_path)
    assert not guard.has_review('gh pr create --body "## Reviewer notes"', tmp_path)
    assert not guard.has_review("gh pr create --title '## Review' --body-file missing.md", tmp_path)
