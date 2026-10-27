"""Shading along the sun path of one day, from the learned shading map (pure Python).

The day is sampled in steps from sunrise to sunset; each step gets the learned transmission at the
sun's position. Steps are classified as shaded, free, behind the modules (no direct light on any of
the array's planes anyway) or unknown (nothing learned yet). With the contour (learned edges per
10° azimuth column) a shadow rising or setting at an edge is placed to a few minutes; a side edge
(across azimuth) only to about a quarter of an hour, the width of a column. A map stored before the
contours uses its 10° x 5° cells until the next learning run.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from .model import AZ_STEP, EL_STEP, SHADING_VERSION, ArrayGeometry, LearnedArray, known_transmission
from .solar import Site, angle_of_incidence, sun_position
from .visual import DARK_STYLE, display_value, transmission_color

STEP_MIN = 10
SHADED_BELOW = 0.8               # transmission below this counts as shaded
MERGE_GAP_STEPS = 1              # single free steps inside a shaded period do not split it

FREE, SHADED, BEHIND, UNKNOWN = "free", "shaded", "behind", "unknown"


@dataclass
class Step:
    start: float                 # UNIX seconds
    end: float
    transmission: float | None   # learned direct-light transmission, None if unknown or behind
    state: str


def day_profile(site: Site, geo: ArrayGeometry, learned: LearnedArray | None, day_start: float,
                step_min: int = STEP_MIN) -> list[Step]:
    """Steps between sunrise and sunset of the day starting at day_start (local midnight)."""
    cells = learned.cells if learned else {}
    steps = []
    for k in range(0, 24 * 60, step_min):
        start = day_start + k * 60
        sun = sun_position(site, start + step_min * 30)
        if sun.elevation <= 0:
            continue
        if all(angle_of_incidence(tilt, azimuth, 90.0 - sun.elevation, sun.azimuth) >= 90.0
               for tilt, azimuth, _ in geo.planes):
            steps.append(Step(start, start + step_min * 60, None, BEHIND))
            continue
        if learned and learned.shading_version >= SHADING_VERSION:
            value = known_transmission(learned, sun.azimuth, sun.elevation)
        else:
            value = display_value(cells, int(sun.azimuth // AZ_STEP), int(sun.elevation // EL_STEP))
        if value is None:
            state = UNKNOWN
        else:
            state = SHADED if value < SHADED_BELOW else FREE
        steps.append(Step(start, start + step_min * 60, value, state))
    return steps


def shade_periods(steps: list[Step]) -> list[dict]:
    """Shaded periods with their lowest transmission; short free gaps inside a period are bridged."""
    periods: list[dict] = []
    gap = 0
    for s in steps:
        if s.state == SHADED:
            if periods and gap <= MERGE_GAP_STEPS and periods[-1]["open"]:
                p = periods[-1]
                p["end"] = s.end
                p["min"] = min(p["min"], s.transmission)
            else:
                periods.append({"start": s.start, "end": s.end, "min": s.transmission, "open": True})
            gap = 0
        else:
            gap += 1
            if periods and (gap > MERGE_GAP_STEPS or s.state != FREE):
                periods[-1]["open"] = False
    return [{"start": p["start"], "end": p["end"], "min_transmission": round(p["min"], 2)} for p in periods]


def unknown_periods(steps: list[Step]) -> list[dict]:
    out: list[dict] = []
    for s in steps:
        if s.state != UNKNOWN:
            continue
        if out and out[-1]["end"] == s.start:
            out[-1]["end"] = s.end
        else:
            out.append({"start": s.start, "end": s.end})
    return out


def shaded_minutes(steps: list[Step]) -> int:
    return sum(round((s.end - s.start) / 60) for s in steps if s.state == SHADED)


# ---------------------------------------------------------------------------- rendering

WIDTH, BAR_Y, BAR_H = 720, 44, 34
LEFT, RIGHT = 20, 20
TEXT = {
    "en": {"behind": "sun behind the modules", "unknown": "not learned yet", "now": "now",
           "legend": "green = free · red = shaded · grey = not learned yet · light = sun behind the modules",
           "none": "no shade expected", "direct": "direct light", "approx": "approx."},
    "de": {"behind": "Sonne hinter den Modulen", "unknown": "noch nicht gelernt", "now": "jetzt",
           "legend": "grün = frei · rot = verschattet · grau = noch nicht gelernt · hell = Sonne hinter den Modulen",
           "none": "kein Schatten erwartet", "direct": "Direktlicht", "approx": "ca."},
}


def render_day(steps: list[Step], title: str, to_local, now: float | None = None, language: str = "en") -> str:
    """Timeline of one day as SVG. to_local converts UNIX seconds to a local datetime."""
    t = TEXT.get(language[:2], TEXT["en"])
    height = BAR_Y + BAR_H + 66
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" '
           f'height="{height}" font-family="sans-serif" font-size="12">',
           DARK_STYLE, f'<rect class="bg" width="{WIDTH}" height="{height}" fill="#ffffff"/>',
           f'<text x="{LEFT}" y="18" font-size="15" font-weight="bold">{escape(title)}</text>']
    if not steps:
        out.append("</svg>")
        return "\n".join(out)
    t0, t1 = steps[0].start, steps[-1].end

    def x(ts: float) -> float:
        return LEFT + (ts - t0) / (t1 - t0) * (WIDTH - LEFT - RIGHT)

    def hm(ts: float) -> str:
        return to_local(ts).strftime("%H:%M")

    for s in steps:
        if s.state == BEHIND:
            fill, tip, cls = "#efefef", t["behind"], ' class="behind"'
        elif s.state == UNKNOWN:
            fill, tip, cls = "#c8c8c8", t["unknown"], ' class="nodata"'
        else:
            fill, tip, cls = transmission_color(s.transmission), f'{s.transmission * 100:.0f} % {t["direct"]}', ""
        out.append(f'<rect{cls} x="{x(s.start):.1f}" y="{BAR_Y}" width="{x(s.end) - x(s.start) + 0.3:.1f}" '
                   f'height="{BAR_H}" fill="{fill}"><title>{hm(s.start)}–{hm(s.end)}: {escape(tip)}</title></rect>')

    # full hours
    first = to_local(t0).replace(minute=0, second=0, microsecond=0)
    hour = first.timestamp() + 3600
    while hour < t1:
        xx = x(hour)
        out.append(f'<line x1="{xx:.1f}" y1="{BAR_Y + BAR_H}" x2="{xx:.1f}" y2="{BAR_Y + BAR_H + 5}" class="tick" stroke="#666666"/>')
        out.append(f'<text x="{xx:.1f}" y="{BAR_Y + BAR_H + 18}" text-anchor="middle" class="muted" fill="#444444">'
                   f'{to_local(hour).hour}</text>')
        hour += 3600
    out.append(f'<text x="{LEFT}" y="{BAR_Y - 6}" class="muted" fill="#444444">{hm(t0)}</text>')
    out.append(f'<text x="{WIDTH - RIGHT}" y="{BAR_Y - 6}" text-anchor="end" class="muted" fill="#444444">{hm(t1)}</text>')

    if now is not None and t0 <= now <= t1:
        xx = x(now)
        out.append(f'<line x1="{xx:.1f}" y1="{BAR_Y - 4}" x2="{xx:.1f}" y2="{BAR_Y + BAR_H + 4}" class="ink" stroke="#000000" '
                   f'stroke-width="2"/>')
        out.append(f'<text x="{xx:.1f}" y="{BAR_Y - 6}" text-anchor="middle">{t["now"]}</text>')

    periods = shade_periods(steps)
    summary = " · ".join(f'{t["approx"]} {hm(p["start"])}–{hm(p["end"])} ({p["min_transmission"] * 100:.0f} % {t["direct"]})'
                         for p in periods) or t["none"]
    out.append(f'<text x="{LEFT}" y="{BAR_Y + BAR_H + 40}">{escape(summary)}</text>')
    out.append(f'<text x="{LEFT}" y="{BAR_Y + BAR_H + 58}" class="muted" fill="#666666" font-size="11">{escape(t["legend"])}</text>')
    out.append("</svg>")
    return "\n".join(out)
