"""The German README follows the English one: same structure, images and code, so a section cannot go missing."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FENCE = re.compile(r"```.*?```", re.S)


def _read(name: str) -> str:
    return FENCE.sub("", (ROOT / name).read_text(encoding="utf-8"))


def _headings(text: str) -> list[int]:
    return [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", text, re.M)]


def _images(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)|<img src=\"([^\"]+)\"", text)


def _code(text: str) -> Counter:
    return Counter(re.findall(r"`([^`\n]+)`", text))


def _table_rows(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("|"))


def test_german_readme_matches_english() -> None:
    en, de = _read("README.md"), _read("README.de.md")
    assert _headings(de) == _headings(en), "headings differ in number or level"
    assert _images(de) == _images(en), "images differ"
    assert _code(de) == _code(en), f"code spans differ: {(_code(en) - _code(de)) + (_code(de) - _code(en))}"
    assert _table_rows(de) == _table_rows(en), "table rows differ"
    assert "(README.de.md)" in en and "(README.md)" in de, "language switch missing"
