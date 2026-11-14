"""The installation as a flow diagram in SVG (pure Python): PV arrays -> systems -> house -> grid.

One drawing for both places that show it: the Installation tab inlines it and makes its nodes clickable
(data-node), the configuration forms show it as an image. Colours come from CSS variables with fallbacks, so the
tab follows the Home Assistant theme and the image follows the light or dark mode of the browser.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape

WIDTH = 680
ARRAY_X, ARRAY_W = 16, 150
SYS_X, SYS_W = 206, 168               # a system box; a system with subsystems is a frame around theirs
FRAME_PAD = 12
BUS_X = 404                           # where the outputs of the systems join before the house
HOUSE_X, HOUSE_W = 432, 104
GRID_X, GRID_W = 576, 88
ROW, GAP = 56, 16                     # an array box and the space between boxes
TOP = 16
NAME_CHARS = 20                       # what fits on one line of a box at 13 px

TEXT = {
    "de": {"house": "Haus", "grid": "Netz", "array": "PV-Gruppe", "battery": "Akku", "free": "frei",
           "none": "Nulleinspeisung", "load": "deckt Hausverbrauch", "all": "gibt alles ab", "fixed": "feste Leistung",
           "together": "zusammen", "balance": "gleicht aus", "empty": "noch keine PV und kein Akku",
           "no_systems": "Noch keine Systeme", "per_phase": "je Phase",
           "templates": {"inverter": "Wechselrichter", "pv_battery": "PV mit Akku", "ac_battery": "Akku am Hausnetz",
                         "coupled": "Geräte, die sich abstimmen", "group": "Gruppe"}},
    "en": {"house": "House", "grid": "Grid", "array": "PV array", "battery": "Battery", "free": "free",
           "none": "zero export", "load": "covers the house load", "all": "delivers everything",
           "fixed": "fixed power", "together": "together", "balance": "balances", "empty": "no PV and no battery yet",
           "no_systems": "No systems yet", "per_phase": "per phase",
           "templates": {"inverter": "Inverter", "pv_battery": "PV with battery", "ac_battery": "Battery at the house",
                         "coupled": "Devices that coordinate", "group": "Group"}},
}
STATUS_ORDER = ("not_yet", "simplified", "exact")     # the worst status of a node's settings colours its dot

STYLE = """<style>
svg.nx-flow { --nf-text: #1f1f1f; --nf-muted: #666; --nf-line: #8a8a8a; --nf-node: #f6f6f6; --nf-frame: #9a9a9a;
  --nf-pv: #fbf1df; --nf-pv-line: #c98a12; --nf-sys: #e3f4ee; --nf-sys-line: #2e9e7b; --nf-limit: #fae8e2;
  --nf-limit-line: #d0603a; font-family: var(--nx-font, Roboto, "Segoe UI", sans-serif); }
@media (prefers-color-scheme: dark) { svg.nx-flow { --nf-text: #ececec; --nf-muted: #a8a8a8; --nf-line: #8f8f8f;
  --nf-node: #2a2a2a; --nf-frame: #777; --nf-pv: #3d2f16; --nf-pv-line: #e0a43c; --nf-sys: #15362c;
  --nf-sys-line: #4cc7a0; --nf-limit: #43231a; --nf-limit-line: #e88a68; } }
.nx-flow .t { fill: var(--nx-text, var(--nf-text)); font-size: 13px; font-weight: 500; }
.nx-flow .s { fill: var(--nx-muted, var(--nf-muted)); font-size: 11px; }
.nx-flow .ln { stroke: var(--nx-line, var(--nf-line)); stroke-width: 1.4; fill: none; }
.nx-flow .node { fill: var(--nx-node, var(--nf-node)); stroke: var(--nx-line, var(--nf-line)); stroke-width: .6; }
.nx-flow .pv { fill: var(--nx-pv, var(--nf-pv)); stroke: var(--nx-pv-line, var(--nf-pv-line)); stroke-width: .6; }
.nx-flow .sys { fill: var(--nx-sys, var(--nf-sys)); stroke: var(--nx-sys-line, var(--nf-sys-line)); stroke-width: .6; }
.nx-flow .frame { fill: none; stroke: var(--nx-frame, var(--nf-frame)); stroke-width: .8; stroke-dasharray: 5 4; }
.nx-flow .lim { fill: var(--nx-limit, var(--nf-limit)); stroke: var(--nx-limit-line, var(--nf-limit-line)); stroke-width: .6; }
.nx-flow .exact { fill: #3f9b4f; } .nx-flow .simplified { fill: #c98a12; } .nx-flow .not_yet { fill: #8a8f98; }
.nx-flow [data-node] { cursor: pointer; }
</style>"""


def _short(text: str, n: int = NAME_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _w(value: Any) -> str:
    v = float(value)
    return f"{v / 1000:.1f} kW".replace(".0 kW", " kW") if v >= 1000 else f"{v:.0f} W"


def _status(effects: list[Mapping[str, Any]], node: str) -> str | None:
    found = {e["status"] for e in effects if e["node"] == node}
    return next((s for s in STATUS_ORDER if s in found), None)


def _text(x: float, y: float, cls: str, value: str, anchor: str = "middle") -> str:
    return f'<text class="{cls}" x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" dominant-baseline="central">' \
           f'{escape(value)}</text>'


def _dot(x: float, y: float, status: str | None) -> str:
    return f'<circle class="{status}" cx="{x:.0f}" cy="{y:.0f}" r="4"/>' if status else ""


def _limit(x: float, y: float, label: str) -> str:
    w = 8 + 6.4 * len(label)
    return f'<rect class="lim" x="{x - w / 2:.0f}" y="{y:.0f}" width="{w:.0f}" height="20" rx="5"/>' + \
        _text(x, y + 10, "s", label)


def _lines(s: Mapping[str, Any], t: Mapping[str, Any]) -> list[str]:
    """The lines of a system box below its name: kind, battery, regulation."""
    out = [t["templates"].get(s.get("template") or "", "")]
    if s.get("battery"):
        out.append(f'{t["battery"]}: {_short(s["battery"]["name"], 16)}')
    if s.get("regulation") in ("load", "fixed") and not s.get("children"):
        out.append(t[s["regulation"]] + (f' {_w(s["fixed_power_w"])}' if s["regulation"] == "fixed"
                                           and s.get("fixed_power_w") else ""))
    return [x for x in out if x]


def render(tree: Mapping[str, Any], lang: str = "en") -> str:
    """The SVG of an installation as systems.tree returns it; lang: "de" or "en"."""
    t = TEXT.get(lang, TEXT["en"])
    effects = list(tree.get("effects") or [])
    body: list[str] = []
    outputs: list[float] = []                 # y of every top-level system's output
    y = TOP

    def leaf(s: Mapping[str, Any], y0: float) -> float:
        """A system box with its arrays to the left; returns the y below it."""
        lines = _lines(s, t)
        arrays = s.get("arrays") or []
        empty = not arrays and not s.get("battery")
        height = max(30 + 16 * (len(lines) + empty) + 8, len(arrays) * (ROW + GAP) - GAP)
        sid = escape(s["id"])
        body.append(f'<g data-node="{sid}"><rect class="sys" x="{SYS_X}" y="{y0:.0f}" width="{SYS_W}" '
                    f'height="{height:.0f}" rx="8"/>' + _text(SYS_X + 12, y0 + 18, "t", _short(s["name"], 18), "start")
                    + "".join(_text(SYS_X + 12, y0 + 38 + 16 * i, "s", _short(x, 28), "start")
                              for i, x in enumerate(lines))
                    + _dot(SYS_X + SYS_W - 12, y0 + 12, _status(effects, s["id"])) + "</g>")
        if empty:
            body.append(_text(SYS_X + 12, y0 + 38 + 16 * len(lines), "s", t["empty"], "start"))
        top = y0 + (height - (len(arrays) * (ROW + GAP) - GAP)) / 2
        for i, a in enumerate(arrays):
            ay = top + i * (ROW + GAP)
            sub = t["array"] + (f" · ≤ {_w(max(a['sensor_limits']))}" if a.get("sensor_limits") else "")
            body.append(f'<g data-node="{escape(a["id"])}"><rect class="pv" x="{ARRAY_X}" y="{ay:.0f}" '
                        f'width="{ARRAY_W}" height="{ROW}" rx="8"/>'
                        + _text(ARRAY_X + ARRAY_W / 2, ay + 20, "t", _short(a["name"], 17))
                        + _text(ARRAY_X + ARRAY_W / 2, ay + 38, "s", sub)
                        + _dot(ARRAY_X + ARRAY_W - 10, ay + 10, _status(effects, a["id"])) + "</g>")
            body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{ARRAY_X + ARRAY_W} {ay + ROW / 2:.0f} '
                        f'H{SYS_X - 3}"/>')
        return y0 + height

    for s in tree.get("systems") or []:
        children = s.get("children") or []
        if children:
            frame_top = y
            y += 30
            mids = []
            for c in children:
                start = y
                y = leaf(c, y)
                mids.append((start + y) / 2)
                y += GAP
            frame_bottom = y - GAP + FRAME_PAD
            # the frame is about 26 characters wide at 13 px, the dot included
            label = f'{_short(s["name"], 11)} · {t["balance"]}' if s.get("balance") else _short(s["name"], 23)
            body.append(f'<g data-node="{escape(s["id"])}"><rect class="frame" x="{SYS_X - FRAME_PAD}" '
                        f'y="{frame_top:.0f}" width="{SYS_W + 2 * FRAME_PAD}" height="{frame_bottom - frame_top:.0f}" '
                        f'rx="12"/>' + _text(SYS_X, frame_top + 16, "t", label, "start")
                        + _dot(SYS_X + SYS_W, frame_top + 14, _status(effects, s["id"])) + "</g>")
            right = SYS_X + SYS_W
            join = (mids[0] + mids[-1]) / 2
            for m in mids:
                body.append(f'<path class="ln" d="M{right} {m:.0f} H{BUS_X - 14} V{join:.0f}"/>')
            if s.get("output_limit_w"):
                lab = f'{t["together"]} ≤ {_w(s["output_limit_w"])}' + (f' {t["per_phase"]}'
                                                                           if s.get("limit_per_phase") else "")
                body.append(_limit(SYS_X + SYS_W / 2, frame_bottom + 4, lab))
                frame_bottom += 24
            outputs.append(join)
            y = frame_bottom + GAP
        else:
            start = y
            y = leaf(s, y)
            mid = (start + y) / 2
            if s.get("output_limit_w"):
                body.append(_limit(SYS_X + SYS_W / 2, y + 4, f'≤ {_w(s["output_limit_w"])}'))
                y += 24
            body.append(f'<path class="ln" d="M{SYS_X + SYS_W} {mid:.0f} H{BUS_X - 14}"/>')
            outputs.append(mid)
            y += GAP

    if not outputs:
        body.append(_text(SYS_X + SYS_W / 2, y + 20, "s", t["no_systems"]))
        y += 40
    height = max(y, TOP + ROW + 40)
    house_y = (min(outputs) + max(outputs)) / 2 - ROW / 2 if outputs else TOP
    if outputs:
        body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{BUS_X - 14} {min(outputs):.0f} '
                    f'V{max(outputs):.0f} M{BUS_X - 14} {house_y + ROW / 2:.0f} H{HOUSE_X - 3}"/>')
    feed = tree.get("feed_in") or "none"
    feed_label = f'≤ {_w(tree["feed_in_limit_w"])}' if feed == "limited" and tree.get("feed_in_limit_w") \
        else t.get(feed, feed)
    body.append(f'<g data-node="house"><rect class="node" x="{HOUSE_X}" y="{house_y:.0f}" width="{HOUSE_W}" '
                f'height="{ROW}" rx="8"/>' + _text(HOUSE_X + HOUSE_W / 2, house_y + ROW / 2, "t", t["house"])
                + _dot(HOUSE_X + HOUSE_W - 10, house_y + 10, _status(effects, "house")) + "</g>")
    body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{HOUSE_X + HOUSE_W} {house_y + ROW / 2:.0f} '
                f'H{GRID_X - 3}"/>')
    body.append(f'<g data-node="house"><rect class="node" x="{GRID_X}" y="{house_y:.0f}" width="{GRID_W}" '
                f'height="{ROW}" rx="8"/>' + _text(GRID_X + GRID_W / 2, house_y + ROW / 2, "t", t["grid"])
                + _text(GRID_X + GRID_W / 2, house_y + ROW + 12, "s", _short(feed_label, 18)) + "</g>")
    height = max(height, house_y + ROW + 28)
    return (f'<svg class="nx-flow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
            f'width="{WIDTH}" height="{height:.0f}" role="img">{STYLE}'
            '<defs><marker id="nx-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
            'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
            'stroke-width="1.5"/></marker></defs>' + "".join(body) + "</svg>")
