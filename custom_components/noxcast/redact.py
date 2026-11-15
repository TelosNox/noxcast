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
# whole addresses only: "2026.10.1" is a version
PRIVATE_IP = re.compile(r"(?<![\d.])(?:(?:127|10)(?:\.\d{1,3}){3}|(?:192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2})(?![\d.]*\d)")
PUBLIC_HOSTS = ("open-meteo.com",)      # the default weather and archive services: knowing them helps a bug report
DROPPED = {"latitude", "longitude", "lat", "lon", "lng", "elevation", "api_key", "token", "password"}
REDACTED = "**REDACTED**"


def redact(data: Any) -> Any:
    """A copy of data with entity IDs replaced by "<domain>.entity_<n>" and private addresses removed.

    An entity ID is recognised where a whole string (value or key) is one; inside a longer text (an error message)
    the IDs found that way are replaced too. A text is not searched for IDs of its own: "open-meteo.com" has their form.
    The host of every private URL is removed from texts too (an archive error names the bare host), and so is any
    private IP address. Public URLs keep only scheme, host and path.
    """
    aliases: dict[str, str] = {}
    counts: dict[str, int] = {}
    hosts: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                collect(k)
                collect(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                collect(v)
        elif isinstance(node, str):
            if (m := ENTITY.match(node)) and node not in aliases:
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
                aliases[node] = f"{m.group(1)}.entity_{counts[m.group(1)]}"
            hosts.update(h for u in URL.findall(node) if (h := _host(u)) and not _public(h))

    patterns: list[tuple[re.Pattern, Any]] = []

    def text(value: str) -> str:
        if value in aliases:
            return aliases[value]
        value = URL.sub(_url, value)
        for pattern, repl in patterns:
            value = pattern.sub(repl, value)
        return value

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {text(k) if isinstance(k, str) else k: walk(v) for k, v in node.items()
                    if not (isinstance(k, str) and k.lower() in DROPPED)}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        return text(node) if isinstance(node, str) else node

    collect(data)
    if hosts:
        patterns.append((re.compile(r"(?i)(?<![\w.-])(" + "|".join(re.escape(h) for h in hosts) + r")(?![\w-])"),
                         REDACTED))
    patterns.append((PRIVATE_IP, REDACTED))
    if aliases:
        # longest first, and not inside a longer ID: sensor.pv1 must not hit sensor.pv10
        patterns.append((re.compile(r"(?<![\w.])(" + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
                                    + r")(?![\w])"), lambda m: aliases[m.group(0)]))
    return walk(data)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _public(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in PUBLIC_HOSTS)


def _url(m: re.Match) -> str:
    if not _public(_host(m.group(0))):
        return REDACTED
    parts = urlsplit(m.group(0))         # no query or user: a key or coordinates may be typed into either
    return f"{parts.scheme}://{parts.hostname}{parts.path}"
