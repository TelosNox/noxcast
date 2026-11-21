"""Small drawings that explain one building block of the installation each and how the blocks nest (pure Python,
SVG). The configuration forms show them as images, the README as files rendered from the same code. They are
examples, not the user's installation: that is the flow diagram (flowchart.py), whose colours they share.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from .const import TEMPLATE_OPTIONS
from .flowchart import STYLE, _text

WIDTH = 680
EXTRA = """<style>
.nx-flow .out { stroke: var(--nx-sys-line, var(--nf-sys-line)); stroke-width: 2.2; fill: none; }
.nx-flow .load { stroke: var(--nx-muted, var(--nf-muted)); stroke-width: 1.4; stroke-dasharray: 4 3; fill: none; }
.nx-flow .bat { fill: var(--nx-sys-line, var(--nf-sys-line)); fill-opacity: .28; stroke: none; }
.nx-flow .dis { fill: var(--nx-limit-line, var(--nf-limit-line)); fill-opacity: .3; stroke: none; }
.nx-flow .pva { fill: var(--nx-pv, var(--nf-pv)); stroke: var(--nx-pv-line, var(--nf-pv-line)); stroke-width: .8; }
.nx-flow .h { fill: var(--nx-text, var(--nf-text)); font-size: 14px; font-weight: 600; }
</style>"""
ARROW = ('<defs><marker id="nx-c-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
         'orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
         'stroke-width="1.5"/></marker></defs>')

TEXT = {
    "de": {
        "household": "Haushalt", "household_sub": "Hausverbrauch, Netz, Einspeisevorgabe",
        "system": "System", "sub": "Teilsystem", "array": "PV-Gruppe", "battery": "Akku", "house": "Haus",
        "grid": "Netz", "rule": "Einspeisevorgabe",
        "roof": "Wechselrichter ohne Akku", "balcony": "PV mit Akku in einem Gerät",
        "coupled": "Gekoppelte Systeme, z. B. EcoFlow Stream", "south": "Süd", "west": "West", "east": "Ost",
        "limit": "Ausgangsgrenze", "shared": "gemeinsame Grenze",
        "full": "Volle Leistung", "full_sub": "gibt alles ab, was die PV liefert",
        "load": "Eigenverbrauch", "load_sub": "deckt, was das Haus gerade braucht",
        "fixed": "Feste Leistung", "fixed_sub": "gibt eine eingestellte Leistung ab",
        "pv": "PV", "output": "Ausgang des Systems", "house_load": "Hausverbrauch", "to_battery": "in den Akku",
        "from_battery": "aus dem Akku",
        "note": ["Liefert die PV mehr als der Ausgang, lädt der Akku; liefert sie weniger, gibt er ab.",
                 "Ist der Akku voll, folgt das System der Einspeisevorgabe des Haushalts – außer es hat eine Abweichung."],
        "templates": [("Wechselrichter", "ohne Akku", "Mikro-, String-WR,", "ältere Dachanlage"),
                      ("PV mit Akku", "in einem Gerät", "Balkongerät mit Akku,", "Hybrid-WR"),
                      ("Akku am", "Hausnetz", "Steckerspeicher mit", "eigenem WR, ohne PV"),
                      ("Gekoppelte", "Systeme", "Geräte mit einer", "Steuerung, z. B. Stream")],
        "inverter": "WR", "device": "Gerät", "battery_inv": "Akku + WR", "unit": "Gerät",
        "group_title": "PV-Gruppe", "group_sub": "was gemeinsam gemessen wird",
        "modules_main": "Module Ost · 15° · 1,6 kWp", "modules_more": "Module West · 15° · 1,6 kWp",
        "main_orient": "Ausrichtung", "more_orient": "weitere Ausrichtung",
        "sensor": "Leistungssensor", "per_sensor": "je Sensor ≤ 800 W", "sum": "Summe",
        "to_system": "zum System",
        "array_note": ["Eine PV-Gruppe pro gemeinsam gemessener Einheit (MPPT-Eingang, Wechselrichter).",
                       "Mehrere Sensoren werden addiert; Module anderer Richtung am selben Sensor sind",
                       "eine weitere Ausrichtung. Die Grenze je Sensor gilt, wenn er flach am Maximum bleibt."],
        "chain": [("PV-Gruppe", "Leistungsgrenze je Sensor", "MPPT-Eingang oder Mikro-WR lässt höchstens so viel durch"),
                  ("System", "Ausgangsgrenze", "was das System höchstens abgibt; sein Akku nimmt mehr auf, bis er voll ist"),
                  ("Haus", "Einspeisevorgabe", "was ins Netz darf; der Rest deckt das Haus oder wird abgeregelt"),
                  ("Netz", "", "")],
        "nested": "Mit Teilsystemen gilt jede Grenze – die engere zählt.",
        "sub_title": "Gekoppelte Systeme", "sub_sub": "eine Steuerung regelt alle Teilsysteme",
        "sub1": "Teilsystem 1", "sub2": "Teilsystem 2", "own_limit": "eigene Grenze",
        "shared_limit": "gemeinsame Grenze", "balance": "gleichen sich aus", "charges_house": "lädt aus dem Haus",
        "from_system": ["Vom System: Regelung,", "Einspeisung, gemeinsame", "Grenze, Ausgleich der Akkus"],
        "per_sub": ["Je Teilsystem: eigene Grenze,", "Phase, PV-Gruppen, ein Akku"],
    },
    "en": {
        "household": "Household", "household_sub": "house load, grid, feed-in rule",
        "system": "System", "sub": "Subsystem", "array": "PV array", "battery": "Battery", "house": "House",
        "grid": "Grid", "rule": "feed-in rule",
        "roof": "Inverter without battery", "balcony": "PV with battery in one device",
        "coupled": "Coupled systems, e.g. EcoFlow Stream", "south": "South", "west": "West", "east": "East",
        "limit": "output limit", "shared": "shared limit",
        "full": "Full power", "full_sub": "delivers everything the PV gives",
        "load": "Self-consumption", "load_sub": "covers what the house needs right now",
        "fixed": "Fixed power", "fixed_sub": "delivers a set power",
        "pv": "PV", "output": "output of the system", "house_load": "house load", "to_battery": "into the battery",
        "from_battery": "from the battery",
        "note": ["When the PV gives more than the output, the battery charges; when it gives less, the battery delivers.",
                 "With the battery full, the system follows the household's feed-in rule – unless it has a deviation."],
        "templates": [("Inverter", "without battery", "micro or string inverter,", "older roof installation"),
                      ("PV with battery", "in one device", "balcony device with", "battery, hybrid inverter"),
                      ("Battery at the", "house grid", "plug-in battery with", "own inverter, no PV"),
                      ("Coupled", "systems", "devices with one", "control, e.g. Stream")],
        "inverter": "Inverter", "device": "Device", "battery_inv": "Battery + inv.", "unit": "Device",
        "group_title": "PV array", "group_sub": "what is measured together",
        "modules_main": "Modules east · 15° · 1.6 kWp", "modules_more": "Modules west · 15° · 1.6 kWp",
        "main_orient": "orientation", "more_orient": "further orientation",
        "sensor": "Power sensor", "per_sensor": "per sensor ≤ 800 W", "sum": "sum",
        "to_system": "to the system",
        "array_note": ["One PV array per unit that is measured together (MPPT input, inverter).",
                       "Several sensors are summed; modules facing another way on the same sensor are",
                       "a further orientation. The limit per sensor holds when it stays flat at a maximum."],
        "chain": [("PV array", "power limit per sensor", "an MPPT input or micro-inverter lets through at most this"),
                  ("System", "output limit", "what the system delivers at most; its battery takes more until full"),
                  ("House", "feed-in rule", "what may go to the grid; the rest covers the house or is curtailed"),
                  ("Grid", "", "")],
        "nested": "With subsystems every limit holds – the tighter one counts.",
        "sub_title": "Coupled systems", "sub_sub": "one control regulates all subsystems",
        "sub1": "Subsystem 1", "sub2": "Subsystem 2", "own_limit": "own limit",
        "shared_limit": "shared limit", "balance": "balance each other", "charges_house": "charges from the house",
        "from_system": ["From the system: regulation,", "feed-in, shared limit,", "batteries balancing"],
        "per_sub": ["Per subsystem: own limit,", "phase, PV arrays, one battery"],
    },
}


def _svg(height: float, body: list[str]) -> str:
    return (f'<svg class="nx-flow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
            f'width="{WIDTH}" height="{height:.0f}" role="img">{STYLE}{EXTRA}{ARROW}' + "".join(body) + "</svg>")


def _box(cls: str, x: float, y: float, w: float, h: float, label: str = "", sub: str = "") -> str:
    out = f'<rect class="{cls}" x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="7"/>'
    if label and sub:
        return out + _text(x + w / 2, y + h / 2 - 7, "t", label) + _text(x + w / 2, y + h / 2 + 9, "s", sub)
    return out + (_text(x + w / 2, y + h / 2, "t", label) if label else "")


def _title(x: float, y: float, kind: str, what: str) -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" dominant-baseline="central"><tspan class="t">{kind}</tspan>'
            f'<tspan class="s" dx="6">{what}</tspan></text>')


def _chip(x: float, y: float, label: str) -> str:
    w = 10 + 6.2 * len(label)
    return (f'<rect class="lim" x="{x - w:.0f}" y="{y:.0f}" width="{w:.0f}" height="20" rx="5"/>'
            + _text(x - w / 2, y + 10, "s", label))


def _arrow(d: str) -> str:
    return f'<path class="ln" marker-end="url(#nx-c-arrow)" d="{d}"/>'


def blocks(lang: str) -> str:
    """Household > systems > PV arrays and battery; a system can hold subsystems that one control coordinates."""
    t = TEXT.get(lang, TEXT["en"])
    b = ['<rect class="frame" x="8" y="8" width="580" height="330" rx="12"/>',
         _title(22, 26, t["household"], t["household_sub"])]
    # three systems, each with what sits inside it
    b += [_box("sys", 24, 46, 418, 70), _title(36, 62, t["system"], t["roof"]),
          _box("pv", 36, 76, 120, 30, f'{t["array"]} {t["south"]}'),
          _box("pv", 164, 76, 120, 30, f'{t["array"]} {t["west"]}'), _chip(432, 81, t["limit"])]
    b += [_box("sys", 24, 126, 418, 70), _title(36, 142, t["system"], t["balcony"]),
          _box("pv", 36, 156, 120, 30, t["array"]), _box("node", 164, 156, 90, 30, t["battery"])]
    b += [_box("sys", 24, 206, 418, 120),
          _title(36, 222, t["system"], t["coupled"]), _chip(432, 212, t["shared"])]
    for i, (x, array) in enumerate(((36, t["east"]), (238, t["west"]))):
        b += [_box("node", x, 240, 194, 76), _title(x + 10, 255, t["sub"], f"{i + 1}"),
              _box("pv", x + 10, 272, 96, 30, array), _box("node", x + 114, 272, 70, 30, t["battery"])]
    # the outputs join at the house, which feeds the grid within its rule
    b += [_arrow("M442 81 H460 V170 H478"), _arrow("M442 161 H478"), '<path class="ln" d="M442 266 H460 V170"/>',
          _box("node", 480, 148, 92, 44, t["house"]), _box("node", 604, 148, 68, 44, t["grid"]),
          _arrow("M572 170 H602"), _text(626, 204, "s", t["rule"])]
    return _svg(346, b)


def _curve(x0: float, x1: float, base: float, f: Callable[[float], float], steps: int = 48) -> list[tuple[float, float]]:
    return [(x0 + (x1 - x0) * i / steps, base - f(i / steps)) for i in range(steps + 1)]


def _path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def regulation(lang: str) -> str:
    """The three regulations over one sunny day: what the system delivers, against PV and house load."""
    t = TEXT.get(lang, TEXT["en"])

    def pv(u: float) -> float:
        return 88 * math.sin(math.pi * u) ** 1.6

    def load(u: float) -> float:
        return 24 + 7 * math.sin(7 * u) + 10 * math.exp(-((u - 0.55) / 0.08) ** 2)

    def fixed(u: float) -> float:
        return 38.0

    b = []
    for i, (key, out) in enumerate((("full", pv), ("load", load), ("fixed", fixed))):
        x = 8 + i * 226
        x0, x1, base = x + 14, x + 196, 166
        b += [f'<rect class="node" x="{x}" y="8" width="212" height="176" rx="10"/>',
              _text(x + 106, 28, "h", t[key]), _text(x + 106, 46, "s", t[f"{key}_sub"])]
        top = _curve(x0, x1, base, pv)
        b.append(f'<path class="pva" d="{_path(top)} L{x1} {base} L{x0} {base} Z"/>')
        output = _curve(x0, x1, base, out)
        if key != "full":
            # what the PV gives above the output charges the battery, what it gives below comes from the battery
            covered = _curve(x0, x1, base, lambda u, out=out: min(pv(u), out(u)))
            b += [f'<path class="bat" d="{_path(top)} L{_path(covered[::-1])[1:]} Z"/>',
                  f'<path class="dis" d="{_path(output)} L{_path(covered[::-1])[1:]} Z"/>']
        b += [f'<path class="load" d="{_path(_curve(x0, x1, base, load))}"/>',
              f'<path class="out" d="{_path(output)}"/>',
              f'<path class="ln" d="M{x0} {base} H{x1}"/>']
    # legend and the rule when the battery is full
    y = 200
    items = [("pva", t["pv"]), ("out", t["output"]), ("load", t["house_load"]), ("bat", t["to_battery"]),
             ("dis", t["from_battery"])]
    x = 14
    for cls, label in items:
        if cls in ("pva", "bat", "dis"):
            b.append(f'<rect class="{cls}" x="{x}" y="{y - 6}" width="18" height="12" rx="2"/>')
        else:
            b.append(f'<path class="{cls}" d="M{x} {y} H{x + 18}"/>')
        b.append(_text(x + 24, y, "s", label, "start"))
        x += 24 + 6.2 * len(label) + 14
    b.append(_lines(WIDTH / 2, 226, "s", t["note"], dy=16))
    return _svg(256, b)


def _lines(x: float, y: float, cls: str, lines: list[str], anchor: str = "middle", dy: float = 14) -> str:
    return "".join(_text(x, y + i * dy, cls, line, anchor) for i, line in enumerate(lines))


def templates(lang: str) -> str:
    """The four templates side by side: where PV, device, battery and house sit in each."""
    t = TEXT.get(lang, TEXT["en"])
    b = []
    for i, (head1, head2, ex1, ex2) in enumerate(t["templates"]):
        x = 8 + i * 168
        c = x + 80
        # each card a group: the Installation tab picks a template by tapping its card
        b += [f'<g data-template="{TEMPLATE_OPTIONS[i]}"><rect class="node" x="{x}" y="8" width="160" height="226" '
              'rx="10"/>',
              _lines(c, 26, "t", [head1, head2], dy=16), _lines(c, 200, "s", [ex1, ex2])]
        if i == 0:
            b += [_box("pv", c - 50, 60, 100, 26, t["pv"]), _arrow(f"M{c} 86 V102"),
                  _box("sys", c - 50, 104, 100, 34, t["inverter"])]
        elif i == 1:
            b += [_box("pv", c - 50, 60, 100, 26, t["pv"]), _arrow(f"M{c} 86 V102"),
                  _box("sys", c - 60, 104, 120, 34, t["device"], t["battery"])]
        elif i == 2:
            b += [_text(c, 76, "s", t["charges_house"]), _box("sys", c - 50, 104, 100, 34, t["battery_inv"])]
        else:
            b += [_box("pv", c - 70, 60, 64, 26, t["pv"]), _box("pv", c + 6, 60, 64, 26, t["pv"]),
                  _arrow(f"M{c - 38} 86 V104"), _arrow(f"M{c + 38} 86 V104"),
                  _box("sys", c - 76, 98, 152, 46), _box("node", c - 70, 106, 64, 30, t["unit"]),
                  _box("node", c + 6, 106, 64, 30, t["unit"])]
        bottom = 144 if i == 3 else 138
        both = ' marker-start="url(#nx-c-arrow)"' if i == 2 else ""
        b += [f'<path class="ln" marker-end="url(#nx-c-arrow)"{both} d="M{c} {bottom} V{156 if i == 2 else 158}"/>'
              if i == 2 else _arrow(f"M{c} {bottom} V158"),
              _box("node", c - 40, 160, 80, 24, t["house"]), "</g>"]
    return _svg(242, b)


def array(lang: str) -> str:
    """A PV array: modules of one or more orientations on sensors that are summed, each sensor with its limit."""
    t = TEXT.get(lang, TEXT["en"])
    b = ['<rect class="pv" x="8" y="8" width="664" height="196" rx="12"/>',
         _title(22, 26, t["group_title"], t["group_sub"])]
    b += [_box("node", 24, 48, 210, 40, t["modules_main"], t["main_orient"]),
          _box("node", 24, 100, 210, 40, t["modules_more"], t["more_orient"])]
    for k, y in enumerate((48, 100)):
        # each sensor passes at most its limit, then the sensors are summed
        b += [_arrow(f"M234 {y + 20} H252"), _box("node", 254, y, 150, 40, f'{t["sensor"]} {k + 1}'),
              f'<path class="ln" d="M404 {y + 20} H412"/>', _chip(530, y + 10, t["per_sensor"]),
              f'<path class="ln" d="M530 {y + 20} H544 V94"/>']
    b += [_box("node", 546, 78, 32, 32, "Σ"), _text(562, 122, "s", t["sum"]), _arrow("M578 94 H600"),
          _lines(636, 88, "s", t["to_system"].split(" ", 1)), _lines(340, 154, "s", t["array_note"])]
    return _svg(212, b)


def limits(lang: str) -> str:
    """The chain of ceilings: limit per sensor, output limit of the system, feed-in rule of the household."""
    t = TEXT.get(lang, TEXT["en"])
    xs = (16, 196, 376, 556)
    w = 108
    b = []
    for i, (name, limit, why) in enumerate(t["chain"]):
        x = xs[i]
        cls = "pv" if i == 0 else "sys" if i == 1 else "node"
        b.append(_box(cls, x, 40, w, 40, name))
        if limit:
            mid = x + w + (xs[i + 1] - x - w) / 2
            b += [_arrow(f"M{x + w} 60 H{xs[i + 1] - 2}"), _chip(mid + 6.2 * len(limit) / 2 + 5, 14, limit),
                  f'<path class="ln" d="M{mid} 34 V58" stroke-dasharray="2 2"/>']
            words, lines = why.split(), [""]
            for word in words:
                if len(lines[-1]) + len(word) > 26:
                    lines.append("")
                lines[-1] = (lines[-1] + " " + word).strip()
            b.append(_lines(mid, 100, "s", lines))
    b.append(_text(WIDTH / 2, 170, "s", t["nested"]))
    return _svg(186, b)


def subsystems(lang: str) -> str:
    """A system with subsystems: what they take from it and what each keeps."""
    t = TEXT.get(lang, TEXT["en"])
    b = [_box("sys", 8, 8, 448, 196), _title(22, 26, t["sub_title"], t["sub_sub"]), _chip(446, 176, t["shared_limit"])]
    for x, name, pv in ((22, t["sub1"], True), (240, t["sub2"], False)):
        b += [_box("node", x, 48, 202, 104), _title(x + 10, 64, name, ""), _chip(x + 194, 54, t["own_limit"])]
        if pv:
            b += [_box("pv", x + 10, 82, 96, 30, t["array"]), _box("node", x + 114, 82, 78, 30, t["battery"])]
        else:
            b += [_lines(x + 54, 90, "s", t["charges_house"].rsplit(" ", 2)[0:1] + [" ".join(t["charges_house"].rsplit(" ", 2)[1:])]),
                  _box("node", x + 114, 82, 78, 30, t["battery"])]
    b += ['<path class="ln" marker-start="url(#nx-c-arrow)" marker-end="url(#nx-c-arrow)" d="M175 114 V136 H393 V114"/>',
          _text(304, 146, "s", t["balance"]),
          _arrow("M456 106 H496"), _box("node", 498, 84, 80, 44, t["house"]),
          _lines(486, 156, "s", t["from_system"], "start"), _lines(22, 172, "s", t["per_sub"], "start")]
    return _svg(212, b)


DRAWINGS: dict[str, Callable[[str], str]] = {"blocks": blocks, "templates": templates, "array": array,
                                             "regulation": regulation, "limits": limits, "subsystems": subsystems}


def render(name: str, lang: str) -> str | None:
    """A drawing by name, None for an unknown one."""
    f = DRAWINGS.get(name)
    return f(lang[:2]) if f else None
