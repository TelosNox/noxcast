"""Remove what identifies a home from the diagnostics download, before a user posts it in a public issue.

Pure functions, no Home Assistant imports. Entity IDs become stable aliases (the same sensor keeps its alias across
the whole download, so the parts still fit together), addresses of private services are removed, coordinates and
keys are dropped. The location is never part of the download; the keys are dropped in case one slips in.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

ENTITY = re.compile(r"^([a-z_]+)\.([a-z0-9_]+)$")
URL = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>]+")
PUBLIC_HOSTS = ("open-meteo.com",)      # the default weather and archive services: knowing them helps a bug report
DROPPED = {"latitude", "longitude", "lat", "lon", "lng", "elevation", "api_key", "token", "password"}
REDACTED = "**REDACTED**"


def redact(data: Any) -> Any:
    """A copy of data with entity IDs replaced by "<domain>.entity_<n>" and private URLs removed.

    An entity ID is recognised where a whole string (value or key) is one; inside a longer text (an error message)
    the IDs found that way are replaced too. A text is not searched for IDs of its own: "open-meteo.com" has their form.
    """
    aliases: dict[str, str] = {}
    counts: dict[str, int] = {}

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                collect(k)
                collect(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                collect(v)
        elif isinstance(node, str) and (m := ENTITY.match(node)) and node not in aliases:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            aliases[node] = f"{m.group(1)}.entity_{counts[m.group(1)]}"

    known = None

    def text(value: str) -> str:
        if value in aliases:
            return aliases[value]
        value = URL.sub(_url, value)
        return known.sub(lambda m: aliases[m.group(0)], value) if known is not None else value

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {text(k) if isinstance(k, str) else k: walk(v) for k, v in node.items()
                    if not (isinstance(k, str) and k.lower() in DROPPED)}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        return text(node) if isinstance(node, str) else node

    collect(data)
    if aliases:
        # longest first, and not inside a longer ID: sensor.pv1 must not hit sensor.pv10
        known = re.compile(r"(?<![\w.])(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
                           + r")(?![\w])")
    return walk(data)


def _url(m: re.Match) -> str:
    host = (urlsplit(m.group(0)).hostname or "").lower()
    return m.group(0) if any(host == h or host.endswith("." + h) for h in PUBLIC_HOSTS) else REDACTED
