"""Shade along today's sun path: classification, periods and timeline rendering."""
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from conftest import load

solar = load("solar")
model = load("model")
shade = load("shade")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=30, azimuth=135, peak_w=920)
TZ = timezone(timedelta(hours=2))
DAY = datetime(2026, 10, 14, tzinfo=TZ).timestamp()


def _all_cells(value: float) -> dict[str, float]:
    return {f"{a},{e}": value for a in range(36) for e in range(14)}


def test_unlearned_day_is_unknown():
    steps = shade.day_profile(SITE, GEO, None, DAY)
    assert steps and {s.state for s in steps} <= {shade.UNKNOWN, shade.BEHIND}
    assert shade.shaded_minutes(steps) == 0 and shade.shade_periods(steps) == []
    assert shade.unknown_periods(steps)


def test_sun_behind_the_modules_in_the_evening():
    steps = shade.day_profile(SITE, GEO, model.LearnedArray(cells=_all_cells(1.0)), DAY)
    assert steps[-1].state == shade.BEHIND and steps[0].state == shade.FREE


def test_morning_shade_is_one_period():
    cells = _all_cells(1.0)
    for a in range(9, 15):                                # east to south-east (90°-150°) shaded
        for e in range(14):
            cells[f"{a},{e}"] = 0.3
    cells["12,4"] = 0.9                                   # a single brighter cell does not split the period
    steps = shade.day_profile(SITE, GEO, model.LearnedArray(cells=cells), DAY)
    periods = shade.shade_periods(steps)
    assert len(periods) == 1
    p = periods[0]
    assert p["min_transmission"] == 0.3
    start, end = (datetime.fromtimestamp(p[k], TZ) for k in ("start", "end"))
    assert start.hour <= 8 and 11 <= end.hour <= 12
    assert shade.shaded_minutes(steps) >= 150


def test_timeline_is_valid_svg():
    cells = _all_cells(1.0)
    cells["13,4"] = 0.2
    steps = shade.day_profile(SITE, GEO, model.LearnedArray(cells=cells), DAY)
    svg = shade.render_day(steps, "Balkon <1>", lambda ts: datetime.fromtimestamp(ts, TZ), DAY + 12 * 3600, "de")
    root = ET.fromstring(svg)
    texts = " ".join(el.text or "" for el in root.iter("{http://www.w3.org/2000/svg}text"))
    assert "Balkon <1>" in texts and "jetzt" in texts and "ca." in texts and "Direktlicht" in texts
    assert shade.render_day([], "x", lambda ts: datetime.fromtimestamp(ts, TZ)).endswith("</svg>")
