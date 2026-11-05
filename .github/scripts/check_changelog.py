"""A pull request that changes the integration says in the changelog what changes for users, or says why not.

Usage: check_changelog.py BASE, with the pull request's description in the environment variable PR_BODY.
The integration is everything under custom_components/. Its changes need either a changed `## Unreleased` section
(a release PR moves that section, which counts as well) or a line in the description that starts with
"No changelog entry", followed by the reason.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"
INTEGRATION = "custom_components/"
WAIVER = re.compile(r"^\W*no changelog entry\b", re.I | re.M)


def unreleased(changelog: str) -> str:
    """The text of the Unreleased section, without its heading."""
    match = re.search(r"^## Unreleased[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    return match[1].strip() if match else ""


def problem(files: list[str], base_changelog: str, head_changelog: str, body: str) -> str | None:
    if not any(f.startswith(INTEGRATION) for f in files):
        return None
    if unreleased(base_changelog) != unreleased(head_changelog) or WAIVER.search(body or ""):
        return None
    return ("This pull request changes the integration but not the Unreleased section of CHANGELOG.md. Add an entry "
            "for users, or start a line of the description with \"No changelog entry:\" and the reason.")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def main(base: str) -> int:
    files = _git("diff", "--name-only", f"{base}...HEAD").splitlines()
    found = problem(files, _git("show", f"{base}:{CHANGELOG}"), _git("show", f"HEAD:{CHANGELOG}"),
                    os.environ.get("PR_BODY", ""))
    if found:
        print(found, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
