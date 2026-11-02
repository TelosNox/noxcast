"""Fail when tests-latest did not get the newest Home Assistant: pip stays on an old one without a word if it needs a newer Python."""
from __future__ import annotations

import json
import sys
from importlib.metadata import version
from urllib.request import urlopen

TEST_PACKAGE = "pytest-homeassistant-custom-component"


def latest(package: str) -> str:
    with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=30) as resp:
        return json.load(resp)["info"]["version"]


def problem(installed: str, newest: str) -> str | None:
    if installed == newest:
        return None
    return f"{TEST_PACKAGE} {installed} is installed, but {newest} is the newest: check the Python version in validate.yml"


def month(ha_version: str) -> str:
    return ".".join(ha_version.split(".")[:2])


def main() -> int:
    ha, newest_ha = version("homeassistant"), latest("homeassistant")
    print(f"Home Assistant {ha} tested, {newest_ha} is the newest release")
    if month(ha) != month(newest_ha):       # the test package usually follows within days, so this alone is no failure
        print(f"::warning::Tests ran on Home Assistant {ha}, but {newest_ha} is out")
    message = problem(version(TEST_PACKAGE), latest(TEST_PACKAGE))
    if message:
        print(f"::error::{message}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
