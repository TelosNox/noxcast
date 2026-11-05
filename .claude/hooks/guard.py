"""Claude Code PreToolUse hook for shell commands.

- No branch switch in the main checkout: several sessions share it, and a switch moves the others' work to another
  branch. Code work runs in a worktree of its own.
- No commit on `main`: work reaches it only through a pull request.
- No commit, push or pull request with private data (`.github/scripts/check_privacy.py`).
- No pull request without a `## Review` section from the noxcast-review skill (`.claude/skills/noxcast-review`).

A blocked command exits with 2; the reason goes to stderr, which Claude Code shows to the session.
Finding the directory a command runs in is best effort: a leading `cd` and `git -C` are understood.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

REVIEW = "## Review"
WORKTREE_HINT = "git worktree add .claude/worktrees/<topic> -b <prefix>/<topic> origin/main"


def _words(command: str) -> list[list[str]]:
    """The commands of a command line, split at ;, &&, || and |, each as words."""
    parts = re.split(r"&&|\|\||;|\||\n", command)
    result = []
    for part in parts:
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            words = part.split()
        if words:
            result.append(words)
    return result


def _git_args(words: list[str]) -> tuple[list[str], str | None] | None:
    """(git subcommand and arguments, -C directory) if words run git, else None."""
    if not words or Path(words[0]).name.lower() not in {"git", "git.exe"}:
        return None
    rest, directory = words[1:], None
    while rest and rest[0].startswith("-"):
        if rest[0] == "-C" and len(rest) > 1:
            directory, rest = rest[1], rest[2:]
        elif rest[0] == "-c" and len(rest) > 1:
            rest = rest[2:]
        else:
            rest = rest[1:]
    return rest, directory


def switches_branch(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] == "switch":
        return True
    if args[0] == "checkout":
        return "--" not in args and not any(a in {"-p", "--patch"} for a in args) and len(args) > 1
    return False


def actions(command: str, cwd: Path) -> list[tuple[str, Path]]:
    """(action, directory) for each relevant step: switch, commit, push, pr."""
    found, here = [], cwd
    for words in _words(command):
        if words[0] in {"cd", "Set-Location", "pushd"} and len(words) > 1:
            here = (here / words[1]).resolve()
            continue
        if words[0] == "gh" and words[1:3] in (["pr", "create"], ["pr", "checkout"]):
            found.append(("pr" if words[2] == "create" else "switch", here))
            continue
        parsed = _git_args(words)
        if parsed is None:
            continue
        args, directory = parsed
        place = (here / directory).resolve() if directory else here
        if switches_branch(args):
            found.append(("switch", place))
        elif args and args[0] == "commit":
            found.append(("commit", place))
        elif args and args[0] == "push":
            found.append(("push", place))
    return found


def has_review(command: str, place: Path) -> bool:
    """Whether the description of `gh pr create` holds a review section, inline or in --body-file / -F."""
    if REVIEW in command:
        return True
    for words in _words(command):
        for flag in ("--body-file", "-F"):
            if words[:3] == ["gh", "pr", "create"] and flag in words[:-1]:
                path = (place / words[words.index(flag) + 1]).resolve()
                try:
                    return REVIEW in path.read_text(encoding="utf-8")
                except OSError:
                    return False
    return False


def _git(place: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(place), *args], capture_output=True, text=True, check=False).stdout.strip()


def is_main_checkout(place: Path) -> bool:
    git_dir = _git(place, "rev-parse", "--path-format=absolute", "--git-dir")
    common = _git(place, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(git_dir) and Path(git_dir) == Path(common)


def _privacy(place: Path):
    root = Path(_git(place, "rev-parse", "--show-toplevel"))
    spec = importlib.util.spec_from_file_location("check_privacy", root / ".github/scripts/check_privacy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, root


def problems(command: str, cwd: Path) -> list[str]:
    result = []
    for action, place in actions(command, cwd):
        if action == "switch" and is_main_checkout(place):
            result.append("No branch switch in the main checkout: other sessions work in it too. Start the work in a "
                          f"worktree of its own: {WORKTREE_HINT}")
        elif action == "commit" and _git(place, "branch", "--show-current") == "main":
            result.append("No commit on main: work reaches it only through a pull request. Commit on a branch.")
        elif action == "pr" and not has_review(command, place):
            result.append("No pull request without a review: run the noxcast-review skill and put its \"## Review\" "
                          "section into the description.")
        if action in {"commit", "push", "pr"}:
            try:
                privacy, root = _privacy(place)
                found = privacy.findings(root, "changes" if action == "commit" else "branch")
            except (OSError, subprocess.CalledProcessError, AttributeError):
                continue
            if found:
                result.append("Private data would be published (.github/scripts/check_privacy.py). Remove it, or "
                              "mark a made-up value with privacy-ok:\n" + "\n".join(found))
    return result


def main() -> int:
    event = json.load(sys.stdin)
    command = (event.get("tool_input") or {}).get("command") or ""
    found = problems(command, Path(event.get("cwd") or ".").resolve())
    if found:
        print("\n\n".join(found), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
