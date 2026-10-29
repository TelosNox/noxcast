"""SVG rendering of a learned shading map with sun paths and key figures (pure Python)."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape

from .model import AZ_STEP, EL_STEP, ArrayGeometry, LearnedArray, facade_cells
from .solar import Site, sun_position

WIDTH, HEIGHT = 720, 400
LEFT, RIGHT, TOP, BOTTOM = 48, 36, 40, 56
EL_MAX = 70.0

TEXT = {
    "en": {"efficiency": "Efficiency", "direction": "Direction factor", "temp": "Temp. coeff.", "coverage": "Coverage", "agreement": "Satellite",
           "datasheet": "datasheet", "learned": "learned", "default": "typical", "none": "off",
           "summer": "Jun 21", "winter": "Dec 21", "equinox": "Mar/Sep", "today": "today", "no_data": "no data", "facade": "from the building edge",
           "elevation": "Sun elevation", "not_learned": "not learned yet", "modules": "modules",
           "normal": "sun perpendicular to the modules",
           "legend": "green = free · red = shaded · grey = no data yet · light = sun behind the modules"},
    "de": {"efficiency": "Wirkungsgrad", "direction": "Richtungsfaktor", "temp": "Temp.-Koeff.", "coverage": "Abdeckung", "agreement": "Satellit",
           "datasheet": "Datenblatt", "learned": "gelernt", "default": "typisch", "none": "aus",
           "summer": "21. Jun", "winter": "21. Dez", "equinox": "Mär/Sep", "today": "heute", "no_data": "keine Daten", "facade": "aus der Häuserkante",
           "elevation": "Sonnenhöhe", "not_learned": "noch nicht gelernt", "modules": "Module",
           "normal": "Sonne senkrecht auf den Modulen",
           "legend": "grün = frei · rot = verschattet · grau = noch keine Daten · hell = Sonne hinter den Modulen"},
}
COMPASS = {0: "N", 45: "NE", 90: "E", 135: "SE", 180: "S", 225: "SW", 270: "W", 315: "NW", 360: "N"}
COMPASS_DE = {"NE": "NO", "E": "O", "SE": "SO"}


# dark variant, chosen by the viewer's light/dark setting (also for SVGs shown as <img>)
DARK_STYLE = ("<style>@media (prefers-color-scheme: dark){svg{fill:#e6e6e6}.bg{fill:#1c1c1c}.gap{stroke:#1c1c1c}"
              ".nodata{fill:#555555}.behind{fill:#2c2c2c}.grid{stroke:#ffffff26}.muted{fill:#a8a8a8}"
              ".tick{stroke:#a8a8a8}.ink{stroke:#ffffff}polyline.p-equinox{stroke:#b0b0b0}text.p-equinox{fill:#b0b0b0}"
              "polyline.p-winter{stroke:#6aa6ff}text.p-winter{fill:#6aa6ff}polyline.p-today{stroke:#ffffff}"
              "text.p-today{fill:#ffffff}line.modules{stroke:#c39cf0}text.modules,circle.modules{fill:#c39cf0}}</style>")


def transmission_color(value: float) -> str:
    """0 = red (shaded), 0.5 = yellow, 1 = green (free)."""
    v = min(max(value, 0.0), 1.0)
    if v < 0.5:
        r, g = 220, int(60 + 330 * v)
    else:
        r, g = int(220 - 360 * (v - 0.5)), 225
    return f"#{r:02x}{g:02x}50"


def _x(az: float, az_range: tuple[float, float]) -> float:
    a, b = az_range
    return LEFT + (az - a) / (b - a) * (WIDTH - LEFT - RIGHT)


def _y(el: float) -> float:
    return TOP + (1 - el / EL_MAX) * (HEIGHT - TOP - BOTTOM)


def sun_path(site: Site, day: date, step_min: int = 10) -> list[tuple[float, float]]:
    # local solar midnight, so the whole day is covered at any longitude
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() - site.longitude / 15 * 3600
    points = []
    for k in range(0, 24 * 60, step_min):
        sun = sun_position(site, start + k * 60)
        if sun.elevation > 0:
            points.append((sun.azimuth, sun.elevation))
    return points


def sun_cells(site: Site, year: int) -> set[tuple[int, int]]:
    """All cells the sun passes through during a year."""
    out = set()
    start = date(year, 1, 1).toordinal()
    for d in range(0, 366, 4):
        for az, el in sun_path(site, date.fromordinal(start + d), step_min=20):
            out.add((int(az // AZ_STEP), int(el // EL_STEP)))
    return out


def display_value(cells: dict[str, float], ai: int, ei: int) -> float | None:
    """Learned value, or the mean of learned neighbours to close gaps between hourly sample positions."""
    v = cells.get(f"{ai},{ei}")
    if v is not None:
        return v
    near = [cells[k] for k in (f"{(ai + x) % 36},{ei + y}" for x in (-1, 0, 1) for y in (-1, 0, 1)) if k in cells]
    return sum(near) / len(near) if len(near) >= 2 else None


def render_shading_map(site: Site, learned: LearnedArray | None, reachable: set[tuple[int, int]], today: date,
                       title: str, language: str = "en", geo: ArrayGeometry | None = None) -> str:
    t = TEXT.get(language[:2], TEXT["en"])
    paths = {
        "summer": sun_path(site, date(today.year, 6, 21)),
        "equinox": sun_path(site, date(today.year, 3, 20)),
        "winter": sun_path(site, date(today.year, 12, 21)),
        "today": sun_path(site, today),
    }
    domain = sun_cells(site, today.year)
    azs = [a for p in paths.values() for a, _ in p]
    a0 = math.floor(min(azs) / 45) * 45 if azs else 45
    a1 = math.ceil(max(azs) / 45) * 45 if azs else 315
    rng = (a0, a1)
    cells = {**facade_cells(learned), **learned.cells} if learned else {}
    derived = set(cells) - set(learned.cells) if learned else set()

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" '
           'font-family="sans-serif" font-size="12">',
           DARK_STYLE, f'<rect class="bg" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
           f'<text x="{LEFT}" y="18" font-size="15" font-weight="bold">{escape(title)}</text>']

    # cells: learned transmission; grey where direct light is possible but nothing is learned yet;
    # light where the sun is behind the module plane (shading irrelevant)
    w = (WIDTH - LEFT - RIGHT) * AZ_STEP / (a1 - a0)
    h = (HEIGHT - TOP - BOTTOM) * EL_STEP / EL_MAX
    learned_keys = {tuple(map(int, k.split(","))) for k in cells}
    for ai, ei in sorted(domain | set(reachable) | learned_keys):
        az, el = ai * AZ_STEP, ei * EL_STEP
        if el >= EL_MAX or az < a0 or az + AZ_STEP > a1:
            continue
        value = display_value(cells, ai, ei)
        # hourly samples hit only some cells; gaps between learned cells take the neighbours' mean
        inside = sum((ai + x, ei + y) in learned_keys for x in (-1, 0, 1) for y in (-1, 0, 1)) >= 3
        if value is not None and ((ai, ei) in reachable or (ai, ei) in learned_keys or inside):
            fill, label, cls = transmission_color(value), f"{value:.2f}", "gap"
            if f"{ai},{ei}" in derived:
                label += f" ({t['facade']})"
        elif (ai, ei) in reachable:
            fill, label, cls = "#c8c8c8", t["no_data"], "gap nodata"
        else:
            fill, label, cls = "#efefef", "–", "gap behind"
        tip = f"{az:.0f}–{az + AZ_STEP:.0f}° / {el:.0f}–{el + EL_STEP:.0f}°: {label}"
        out.append(f'<rect class="{cls}" x="{_x(az, rng):.1f}" y="{_y(el + EL_STEP):.1f}" width="{w:.1f}" height="{h:.1f}" '
                   f'fill="{fill}" stroke="#ffffff" stroke-width="0.5"><title>{escape(tip)}</title></rect>')

    # axes and grid
    for el in range(0, int(EL_MAX) + 1, 10):
        y = _y(el)
        out.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{WIDTH - RIGHT}" y2="{y:.1f}" class="grid" stroke="#00000022"/>')
        out.append(f'<text x="{LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">{el}°</text>')
    for az in range(int(a0), int(a1) + 1, 45):
        x = _x(az, rng)
        label = COMPASS.get(az % 360 if az != 360 else 360, f"{az}°")
        if language[:2] == "de":
            label = COMPASS_DE.get(label, label)
        out.append(f'<line x1="{x:.1f}" y1="{TOP}" x2="{x:.1f}" y2="{_y(0):.1f}" class="grid" stroke="#00000022"/>')
        out.append(f'<text x="{x:.1f}" y="{_y(0) + 16:.1f}" text-anchor="middle">{label} {az % 360}°</text>')
    out.append(f'<text x="14" y="{(TOP + _y(0)) / 2:.0f}" transform="rotate(-90 14 {(TOP + _y(0)) / 2:.0f})" '
               f'text-anchor="middle">{t["elevation"]}</text>')

    # sun paths
    styles = {"summer": ("#e07b00", ""), "equinox": ("#555555", ' stroke-dasharray="6 4"'),
              "winter": ("#2060c0", ""), "today": ("#000000", ' stroke-dasharray="2 3"')}
    for name, points in paths.items():
        pts = [(a, e) for a, e in points if a0 <= a <= a1 and e <= EL_MAX]
        if len(pts) < 2:
            continue
        color, dash = styles[name]
        d = " ".join(f"{_x(a, rng):.1f},{_y(e):.1f}" for a, e in pts)
        out.append(f'<polyline class="p-{name}" points="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash}/>')
        a, e = max(pts, key=lambda p: p[1])
        out.append(f'<text x="{_x(a, rng):.1f}" y="{_y(e) - 5:.1f}" text-anchor="middle" class="p-{name}" fill="{color}">'
                   f'{t[name]}</text>')

    # module orientation and the sun position perpendicular to the modules (highest irradiance), per plane
    if geo is not None:
        for tilt, azimuth, _ in geo.planes:
            az = azimuth if a0 <= azimuth <= a1 else azimuth + 360
            if not a0 <= az <= a1:
                continue
            x = _x(az, rng)
            out.append(f'<line x1="{x:.1f}" y1="{TOP}" x2="{x:.1f}" y2="{_y(0):.1f}" class="modules" stroke="#7a3db8" '
                       f'stroke-width="1.4" stroke-dasharray="8 3"/>')
            out.append(f'<text x="{x + 4:.1f}" y="{TOP + 10}" class="modules" fill="#7a3db8">{t["modules"]} {azimuth:.0f}°</text>')
            normal_el = 90.0 - tilt
            if 0 < normal_el <= EL_MAX:
                out.append(f'<circle cx="{x:.1f}" cy="{_y(normal_el):.1f}" r="5" class="modules" fill="#7a3db8" stroke="#ffffff" '
                           f'stroke-width="1.5"><title>{escape(t["normal"])}: {azimuth:.0f}° / '
                           f'{normal_el:.0f}°</title></circle>')

    # key figures
    if learned and learned.learned_at:
        parts = [f'{t["efficiency"]} {learned.efficiency * 100:.1f} %',
                 f'{t["temp"]} {learned.temp_coeff * 100:.2f} %/K ({t.get(learned.temp_source, learned.temp_source)})',
                 f'{t["coverage"]} {learned.coverage * 100:.0f} %']
        if learned.direction:
            parts.append(f'{t["direction"]} {min(learned.direction.values()):.2f}–{max(learned.direction.values()):.2f}')
        if learned.agreement is not None:
            parts.append(f'{t["agreement"]} {learned.agreement:.2f}')
        figures = " · ".join(parts)
    else:
        figures = t["not_learned"]
    out.append(f'<text x="{LEFT}" y="{HEIGHT - 26}">{escape(figures)}</text>')
    out.append(f'<text x="{LEFT}" y="{HEIGHT - 8}" class="muted" fill="#666666" font-size="11">{escape(t["legend"])}</text>')
    out.append("</svg>")
    return "\n".join(out)
