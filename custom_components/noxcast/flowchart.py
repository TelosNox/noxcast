"""The installation as a flow diagram in SVG (pure Python): PV arrays -> systems -> house -> grid.

One drawing for both places that show it: the Installation tab inlines it and makes its nodes clickable
(data-node), the configuration forms show it as an image. Colours come from CSS variables with fallbacks, so the
tab follows the Home Assistant theme and the image follows the light or dark mode of the browser.

Every setting that changes the forecast shows where it acts: an array's orientation as a compass needle in its box,
a power limit per sensor on the line from the array, a system's output limit and its deviation from the feed-in rule
on its output, the phase of a subsystem on the line it feeds, the feed-in rule between house and grid.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from xml.sax.saxutils import escape

WIDTH = 862
ARRAY_X, ARRAY_W = 16, 170
SYS_X, SYS_W = 236, 170               # a system box; a system with subsystems is a frame around theirs
FRAME_PAD = 12
JOIN_X = SYS_X + SYS_W + FRAME_PAD + 20   # where the outputs of subsystems join
BUS_X = 548                           # where the outputs of the systems join before the house
HOUSE_X, HOUSE_W = 576, 144
GRID_X, GRID_W = 776, 72
ROW, GAP = 64, 14                     # an array box and the space between boxes
BATTERY_H = 40
TOP = 16
NAME_CHARS = 20                       # what fits on one line of a box at 13 px
LINE_CHARS = 22                       # a detail line beside the compass at 11 px

TEXT = {
    "de": {"house": "Haus", "grid": "Netz", "array": "PV-Gruppe", "battery": "Akku", "free": "frei",
           "none": "Nulleinspeisung", "load": "Eigenverbrauch", "all": "volle Leistung", "fixed": "feste Leistung",
           "together": "zusammen", "balance": "gleicht aus", "empty": "noch keine PV und kein Akku",
           "no_systems": "Noch keine Systeme", "per_phase": "je Phase", "planes": "{n} Richtungen",
           "sensors": "{n} Sensoren addiert", "no_sensor": "kein Sensor", "own_pv": "lädt aus PV",
           "house_charge": "lädt aus Hausnetz", "consumption": "Verbrauch", "grid_sensor": "Netz",
           "export": "speist trotzdem ein", "never": "speist nie ein", "three": "L1–L3",
           "templates": {"inverter": "Wechselrichter", "pv_battery": "PV mit Akku", "ac_battery": "Akku am Hausnetz",
                         "coupled": "Geräte, die sich abstimmen"}},
    "en": {"house": "House", "grid": "Grid", "array": "PV array", "battery": "Battery", "free": "free",
           "none": "zero export", "load": "self-consumption", "all": "full power",
           "fixed": "fixed power", "together": "together", "balance": "balances", "empty": "no PV and no battery yet",
           "no_systems": "No systems yet", "per_phase": "per phase", "planes": "{n} directions",
           "sensors": "{n} sensors summed", "no_sensor": "no sensor", "own_pv": "charges from PV",
           "house_charge": "charges from house", "consumption": "Load", "grid_sensor": "Grid",
           "export": "feeds in anyway", "never": "never feeds in", "three": "L1–L3",
           "templates": {"inverter": "Inverter", "pv_battery": "PV with battery", "ac_battery": "Battery at the house",
                         "coupled": "Devices that coordinate"}},
}
COMPASS = {"de": ("N", "NO", "O", "SO", "S", "SW", "W", "NW"), "en": ("N", "NE", "E", "SE", "S", "SW", "W", "NW")}
STATUS_ORDER = ("not_yet", "simplified", "exact")     # the worst status of a node's settings colours its dot

STYLE = """<style>
svg.nx-flow { --nf-text: #1f1f1f; --nf-muted: #666; --nf-line: #8a8a8a; --nf-node: #f6f6f6; --nf-frame: #9a9a9a;
  --nf-pv: #fbf1df; --nf-pv-line: #c98a12; --nf-sys: #e3f4ee; --nf-sys-line: #2e9e7b; --nf-limit: #fae8e2;
  --nf-limit-line: #d0603a; --nf-bat: #e8eefb; --nf-bat-line: #4b6fc4; --nf-card: #ffffff;
  font-family: var(--nx-font, Roboto, "Segoe UI", sans-serif); }
@media (prefers-color-scheme: dark) { svg.nx-flow { --nf-text: #ececec; --nf-muted: #a8a8a8; --nf-line: #8f8f8f;
  --nf-node: #2a2a2a; --nf-frame: #777; --nf-pv: #3d2f16; --nf-pv-line: #e0a43c; --nf-sys: #15362c;
  --nf-sys-line: #4cc7a0; --nf-limit: #43231a; --nf-limit-line: #e88a68; --nf-bat: #1a2747; --nf-bat-line: #7d9ae6;
  --nf-card: #1c1c1c; } }
.nx-flow .t { fill: var(--nx-text, var(--nf-text)); font-size: 13px; font-weight: 500; }
.nx-flow .s { fill: var(--nx-muted, var(--nf-muted)); font-size: 11px; }
.nx-flow .ln { stroke: var(--nx-line, var(--nf-line)); stroke-width: 1.4; fill: none; }
.nx-flow .node { fill: var(--nx-node, var(--nf-node)); stroke: var(--nx-line, var(--nf-line)); stroke-width: .6; }
.nx-flow .pv { fill: var(--nx-pv, var(--nf-pv)); stroke: var(--nx-pv-line, var(--nf-pv-line)); stroke-width: .6; }
.nx-flow .sys { fill: var(--nx-sys, var(--nf-sys)); stroke: var(--nx-sys-line, var(--nf-sys-line)); stroke-width: .6; }
.nx-flow .batt { fill: var(--nx-bat, var(--nf-bat)); stroke: var(--nx-bat-line, var(--nf-bat-line)); stroke-width: .6; }
.nx-flow .frame { fill: transparent; stroke: var(--nx-frame, var(--nf-frame)); stroke-width: .8; stroke-dasharray: 5 4; }
.nx-flow .lim { fill: var(--nx-limit, var(--nf-limit)); stroke: var(--nx-limit-line, var(--nf-limit-line)); stroke-width: .6; }
.nx-flow .ph { fill: var(--nx-node, var(--nf-node)); stroke: var(--nx-line, var(--nf-line)); stroke-width: .6; }
.nx-flow .dev { fill: var(--nx-pv, var(--nf-pv)); stroke: var(--nx-pv-line, var(--nf-pv-line)); stroke-width: .6; }
.nx-flow .dial { fill: var(--nx-card, var(--nf-card)); stroke: var(--nx-line, var(--nf-line)); stroke-width: .6; }
.nx-flow .needle { stroke: var(--nx-pv-line, var(--nf-pv-line)); stroke-width: 1.8; stroke-linecap: round; fill: none; }
.nx-flow .north { stroke: var(--nx-muted, var(--nf-muted)); stroke-width: 1.2; }
.nx-flow .exact { fill: #3f9b4f; } .nx-flow .simplified { fill: #c98a12; } .nx-flow .not_yet { fill: #8a8f98; }
.nx-flow [data-node] { cursor: pointer; }
</style>"""


def _short(text: str, n: int = NAME_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _w(value: Any) -> str:
    v = float(value)
    return f"{v / 1000:.1f} kW".replace(".0 kW", " kW") if v >= 1000 else f"{v:.0f} W"


def _wp(value: float) -> str:
    return f"{value / 1000:.1f} kWp".replace(".0 kWp", " kWp") if value >= 1000 else f"{value:.0f} Wp"


def _status(effects: list[Mapping[str, Any]], node: str) -> str | None:
    found = {e["status"] for e in effects if e["node"] == node}
    return next((s for s in STATUS_ORDER if s in found), None)


def _text(x: float, y: float, cls: str, value: str, anchor: str = "middle") -> str:
    return f'<text class="{cls}" x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" dominant-baseline="central">' \
           f'{escape(value)}</text>'


def _dot(x: float, y: float, status: str | None) -> str:
    return f'<circle class="{status}" cx="{x:.0f}" cy="{y:.0f}" r="4"/>' if status else ""


def _pill(x: float, y: float, label: str, cls: str = "lim") -> str:
    """A label centred on (x, y): limits in red, phases neutral, deviations from the feed-in rule in yellow."""
    w = 12 + 6 * len(label)
    return f'<rect class="{cls}" x="{x - w / 2:.0f}" y="{y - 10:.0f}" width="{w:.0f}" height="20" rx="10"/>' + \
        _text(x, y, "s", label)


def _compass(x: float, y: float, planes: list[Mapping[str, float]]) -> str:
    """A dial with one needle per orientation: its direction is the azimuth (north up), its length grows with the
    tilt, so flat modules show a short needle."""
    r = 13
    out = [f'<circle class="dial" cx="{x:.0f}" cy="{y:.0f}" r="{r}"/>',
           f'<path class="north" d="M{x:.0f} {y - r - 3:.0f}V{y - r + 2:.0f}"/>']
    for p in planes:
        a = math.radians(p["azimuth"])
        n = r * (0.35 + 0.65 * min(max(p["tilt"], 0.0), 90.0) / 90.0)
        out.append(f'<path class="needle" d="M{x:.0f} {y:.0f}L{x + n * math.sin(a):.1f} {y - n * math.cos(a):.1f}"/>')
    return "".join(out)


def _orientation(planes: list[Mapping[str, float]], t: Mapping[str, Any], lang: str) -> str:
    if not planes:
        return ""
    peak = sum(p["peak_w"] for p in planes)
    if len(planes) > 1:
        return f'{t["planes"].format(n=len(planes))} · {_wp(peak)}'
    p = planes[0]
    direction = COMPASS.get(lang, COMPASS["en"])[round(p["azimuth"] % 360 / 45) % 8]
    return f'{p["tilt"]:.0f}° · {direction} {p["azimuth"]:.0f}° · {_wp(peak)}'


def _sensors(a: Mapping[str, Any], t: Mapping[str, Any]) -> str:
    ids = a.get("sensor_ids") or []
    if len(ids) > 1:
        return t["sensors"].format(n=len(ids))
    if ids:
        return ids[0].removeprefix("sensor.")
    return t["no_sensor"] if not a.get("sensors") else ""


def _lines(s: Mapping[str, Any], t: Mapping[str, Any]) -> list[str]:
    """The lines of a system box below its name: kind and regulation; the battery has a box of its own."""
    out = [t["templates"].get(s.get("template") or "", "")]
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
        """A system box with its battery inside and its arrays to the left; returns the y below it."""
        lines = _lines(s, t)
        arrays = s.get("arrays") or []
        battery = s.get("battery")
        empty = not arrays and not battery
        head = 30 + 16 * (len(lines) + empty) + (BATTERY_H + 6 if battery else 0) + 8
        height = max(head, len(arrays) * (ROW + GAP) - GAP)
        sid = escape(s["id"])
        body.append(f'<g data-node="{sid}"><rect class="sys" x="{SYS_X}" y="{y0:.0f}" width="{SYS_W}" '
                    f'height="{height:.0f}" rx="8"/>' + _text(SYS_X + 12, y0 + 18, "t", _short(s["name"], 18), "start")
                    + "".join(_text(SYS_X + 12, y0 + 38 + 16 * i, "s", _short(x, 28), "start")
                              for i, x in enumerate(lines))
                    + _dot(SYS_X + SYS_W - 12, y0 + 12, _status(effects, s["id"])) + "</g>")
        if empty:
            body.append(_text(SYS_X + 12, y0 + 38 + 16 * len(lines), "s", t["empty"], "start"))
        if battery:
            by = y0 + 30 + 16 * len(lines) + 2
            detail = [f'{float(battery["capacity_kwh"]):g} kWh'] if battery.get("capacity_kwh") else []
            detail.append(t["house_charge"] if s.get("charges_from") == "house" else t["own_pv"])
            body.append(f'<g data-node="{escape(battery["id"])}"><rect class="batt" x="{SYS_X + 8}" y="{by:.0f}" '
                        f'width="{SYS_W - 16}" height="{BATTERY_H}" rx="6"/>'
                        + _text(SYS_X + 18, by + 13, "t", _short(battery["name"], 18), "start")
                        + _text(SYS_X + 18, by + 29, "s", _short(" · ".join(detail), 26), "start")
                        + _dot(SYS_X + SYS_W - 18, by + 10, _status(effects, battery["id"])) + "</g>")
        top = y0 + (height - (len(arrays) * (ROW + GAP) - GAP)) / 2
        for i, a in enumerate(arrays):
            ay = top + i * (ROW + GAP)
            planes = a.get("planes") or []
            body.append(f'<g data-node="{escape(a["id"])}"><rect class="pv" x="{ARRAY_X}" y="{ay:.0f}" '
                        f'width="{ARRAY_W}" height="{ROW}" rx="8"/>'
                        + _text(ARRAY_X + 10, ay + 16, "t", _short(a["name"], 17), "start")
                        + _text(ARRAY_X + 10, ay + 35, "s", _short(_orientation(planes, t, lang) or t["array"],
                                                                   LINE_CHARS), "start")
                        + _text(ARRAY_X + 10, ay + 51, "s", _short(_sensors(a, t), LINE_CHARS), "start")
                        + (_compass(ARRAY_X + ARRAY_W - 22, ay + 40, planes) if planes else "")
                        + _dot(ARRAY_X + ARRAY_W - 10, ay + 10, _status(effects, a["id"])) + "</g>")
            body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{ARRAY_X + ARRAY_W} {ay + ROW / 2:.0f} '
                        f'H{SYS_X - 3}"/>')
            if a.get("sensor_limits"):
                body.append(_pill((ARRAY_X + ARRAY_W + SYS_X) / 2, ay + ROW / 2 - 14,
                                  f'≤ {_w(max(a["sensor_limits"]))}'))
        return y0 + height

    def output(s: Mapping[str, Any], x0: float, mid: float) -> None:
        """The labels on a system's output: its limit above the line, a deviation from the feed-in rule below."""
        cx = (x0 + BUS_X) / 2
        if s.get("output_limit_w"):
            w = _w(s["output_limit_w"])
            label = (f'≤ {w} {t["per_phase"]}' if s.get("limit_per_phase")
                     else f'{t["together"]} ≤ {w}' if s.get("children") else f'≤ {w}')
            body.append(_pill(cx, mid - 14, label))
        if s.get("own_feed_in") in ("export", "never"):
            body.append(_pill(cx, mid + 14, t[s["own_feed_in"]], "dev"))

    for s in tree.get("systems") or []:
        children = s.get("children") or []
        if children:
            frame_top, at = y, len(body)
            y += 30
            mids = []
            for c in children:
                start = y
                y = leaf(c, y)
                mid = (start + y) / 2
                mids.append(mid)
                body.append(f'<path class="ln" d="M{SYS_X + SYS_W} {mid:.0f} H{JOIN_X}"/>')
                if c.get("phase"):
                    phase = t["three"] if c["phase"] == "three" else c["phase"].upper()
                    body.append(_pill((SYS_X + SYS_W + JOIN_X) / 2, mid, phase, "ph"))
                y += GAP
            frame_bottom = y - GAP + FRAME_PAD
            # the frame is about 26 characters wide at 13 px, the dot included
            label = f'{_short(s["name"], 11)} · {t["balance"]}' if s.get("balance") else _short(s["name"], 23)
            # drawn first, so that the boxes inside stay clickable on top of it
            body.insert(at, f'<g data-node="{escape(s["id"])}"><rect class="frame" x="{SYS_X - FRAME_PAD}" '
                        f'y="{frame_top:.0f}" width="{SYS_W + 2 * FRAME_PAD}" height="{frame_bottom - frame_top:.0f}" '
                        f'rx="12"/>' + _text(SYS_X, frame_top + 16, "t", label, "start")
                        + _dot(SYS_X + SYS_W, frame_top + 14, _status(effects, s["id"])) + "</g>")
            join = (mids[0] + mids[-1]) / 2
            body.append(f'<path class="ln" d="M{JOIN_X} {mids[0]:.0f} V{mids[-1]:.0f} M{JOIN_X} {join:.0f} '
                        f'H{BUS_X}"/>')
            output(s, JOIN_X, join)
            outputs.append(join)
            y = frame_bottom + GAP + 8
        else:
            start = y
            y = leaf(s, y)
            mid = (start + y) / 2
            body.append(f'<path class="ln" d="M{SYS_X + SYS_W} {mid:.0f} H{BUS_X}"/>')
            output(s, SYS_X + SYS_W, mid)
            outputs.append(mid)
            y += GAP + 8

    if not outputs:
        body.append(_text(SYS_X + SYS_W / 2, y + 20, "s", t["no_systems"]))
        y += 40
    height = max(y, TOP + ROW + 40)
    house_y = (min(outputs) + max(outputs)) / 2 - ROW / 2 if outputs else TOP
    house_y = max(house_y, TOP)
    if outputs:
        body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{BUS_X} {min(outputs):.0f} '
                    f'V{max(outputs):.0f} M{BUS_X} {house_y + ROW / 2:.0f} H{HOUSE_X - 3}"/>')
    sensors = [(t["consumption"], tree.get("house_load")), (t["grid_sensor"], tree.get("grid"))]
    lines = [f'{name}: {_short(eid.removeprefix("sensor."), 12)}' for name, eid in sensors if eid]
    body.append(f'<g data-node="house"><rect class="node" x="{HOUSE_X}" y="{house_y:.0f}" width="{HOUSE_W}" '
                f'height="{ROW}" rx="8"/>'
                + (_text(HOUSE_X + 10, house_y + 16, "t", t["house"], "start")
                   + "".join(_text(HOUSE_X + 10, house_y + 35 + 16 * i, "s", x, "start") for i, x in enumerate(lines))
                   if lines else _text(HOUSE_X + HOUSE_W / 2, house_y + ROW / 2, "t", t["house"]))
                + _dot(HOUSE_X + HOUSE_W - 10, house_y + 10, _status(effects, "house")) + "</g>")
    body.append(f'<path class="ln" marker-end="url(#nx-arrow)" d="M{HOUSE_X + HOUSE_W} {house_y + ROW / 2:.0f} '
                f'H{GRID_X - 3}"/>')
    feed = tree.get("feed_in") or "none"
    limit = (f'≤ {_w(tree["feed_in_limit_w"])}' if feed == "limited" and tree.get("feed_in_limit_w")
             else f'≤ {tree["feed_in_limit_pct"]:g} %' if feed == "percent" and tree.get("feed_in_limit_pct")
             else "0 W" if feed == "none" else None)
    if limit:
        body.append(_pill((HOUSE_X + HOUSE_W + GRID_X) / 2, house_y + ROW / 2 - 14, limit))
    body.append(f'<g data-node="house"><rect class="node" x="{GRID_X}" y="{house_y:.0f}" width="{GRID_W}" '
                f'height="{ROW}" rx="8"/>' + _text(GRID_X + GRID_W / 2, house_y + ROW / 2, "t", t["grid"])
                + (_text(GRID_X + GRID_W / 2, house_y + ROW + 12, "s", t[feed]) if feed in ("free", "none") else "")
                + "</g>")
    height = max(height, house_y + ROW + 28)
    return (f'<svg class="nx-flow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
            f'width="{WIDTH}" height="{height:.0f}" role="img">{STYLE}'
            '<defs><marker id="nx-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
            'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
            'stroke-width="1.5"/></marker></defs>' + "".join(body) + "</svg>")
