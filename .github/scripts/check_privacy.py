"""Find private data before it is published: tokens, Home Assistant addresses, precise coordinates, and the
maintainer's own values from the git-ignored `*.local.json` files.

`--all` scans every tracked file with the generic patterns (tests and CI). `--changes` scans what a commit would
add, `--branch BASE` what a branch adds to BASE; both also look for the personal values (the Claude Code hook).
Findings name the kind and the place, never the value. A line that carries `privacy-ok` is skipped: use it only
for made-up values.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ALLOW = "privacy-ok"
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pkl", ".woff", ".woff2"}
PATTERNS = {
    "token": re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\."),
    "API key": re.compile(r"(?i)(api[_-]?key|token|secret)\W{0,3}[:=]\s*[\"']?[A-Za-z0-9_\-]{24,}"),
    "Home Assistant address": re.compile(
        r"(?i)(https?|wss?)://(?!homeassistant\.local|localhost|127\.0\.0\.1|example\.)[^\s\"'/]+:8123"
        r"|[\w-]+\.ui\.nabu\.casa|[\w-]+\.duckdns\.org"),
    "private IP address": re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"),
    "coordinate": re.compile(r"(?i)\b(lat|latitude|lon|lng|longitude)\W{0,3}[:=]\s*-?\d{1,3}\.\d{3,}"),
}
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def scan(text: str, personal: dict[str, str] | None = None) -> list[tuple[str, int]]:
    """(kind, line number) of every finding; personal maps a literal value to its kind."""
    found = []
    for no, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        found += [(kind, no) for kind, pattern in PATTERNS.items() if pattern.search(line)]
        found += [(kind, no) for value, kind in (personal or {}).items() if value in line]
    return found


def personal_values(root: Path) -> dict[str, str]:
    """Literal values from the git-ignored local files of the checkout at root."""
    ignored = [root / name for name in _git(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                                            "--", "*.local.json", "*token*.txt").splitlines()]
    return local_values([p for p in ignored if "worktrees" not in p.parts])


def local_values(paths: list[Path]) -> dict[str, str]:
    """Literal values from local files that must never appear in the repository, each with its kind."""
    values: dict[str, str] = {}

    def walk(node, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, float) and key.lower() in {"latitude", "longitude", "lat", "lon", "lng"}:
            text = f"{node:.6f}".rstrip("0")
            if len(text.split(".")[1]) >= 2:          # 50.0 is no location; three decimals are about 100 m
                values[text[:text.index(".") + 4]] = "coordinate from a local file"
        elif isinstance(node, str) and (ENTITY.match(node) or "://" in node):
            if not node.startswith(("ws://homeassistant.local", "http://homeassistant.local")):
                values[node] = "entity ID or address from a local file"

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                walk(json.loads(text))
            elif len(text.strip()) >= 16:
                values[text.strip()] = "token from a local file"
        except (OSError, ValueError):
            continue
    return values


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def _added(diff: str) -> dict[str, str]:
    """Added lines per file of a unified diff."""
    files: dict[str, list[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and line.startswith("+"):
            files.setdefault(current, []).append(line[1:])
    return {name: "\n".join(lines) for name, lines in files.items()}


def _readable(root: Path, name: str) -> str | None:
    path = root / name
    if path.suffix.lower() in BINARY or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def main_checkout(root: Path) -> Path:
    """The main checkout of the repository, where the git-ignored local files live, also from a worktree."""
    common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    return common.parent


def findings(root: Path, mode: str, base: str = "origin/main") -> list[str]:
    texts: dict[str, str] = {}
    personal = None
    if mode == "all":
        for name in _git(root, "ls-files").splitlines():
            if (text := _readable(root, name)) is not None:
                texts[name] = text
    else:
        personal = personal_values(main_checkout(root))
        if mode == "changes":
            texts = _added(_git(root, "diff", "HEAD", "--no-color", "-U0"))
            for name in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
                if (text := _readable(root, name)) is not None:
                    texts[name] = text
        else:
            texts = _added(_git(root, "diff", f"{base}...HEAD", "--no-color", "-U0"))
    added = mode != "all"
    return [f"{name}{'' if added else f':{no}'}: {kind}" for name, text in sorted(texts.items())
            for kind, no in scan(text, personal)]


def main(argv: list[str]) -> int:
    root = Path.cwd()
    mode = {"--all": "all", "--changes": "changes", "--branch": "branch"}.get(argv[0] if argv else "--all")
    if mode is None:
        print("usage: check_privacy.py [--all | --changes | --branch BASE]", file=sys.stderr)
        return 2
    problems = findings(root, mode, *argv[1:2])
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
