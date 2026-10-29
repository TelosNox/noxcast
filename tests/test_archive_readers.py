"""Readers of the day-ahead archive (band, panel views, error) on a stored archive with every field."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

from conftest import load

archive = load("archive")
bd = load("band")
model = load("model")
solar = load("solar")
views = load("views")

SITE = solar.Site(48.97, 8.45, 113)
GEO = model.ArrayGeometry(tilt=35, azimuth=180, peak_w=1000)
FIRST = date(2026, 6, 22)
DAY0 = 1782086400.0                # 2026-06-22 00:00 UTC
DAYS = 10


def stored() -> dict:
    """An archive as it is stored (JSON round trip): string keys, all optional fields present somewhere."""
    days = {}
    for d in range(DAYS):
        hours = {}
        for h in range(4, 20):
            ts = DAY0 + d * 86400 + h * 3600
            sun = max(0.0, 1 - abs(h - 12) / 8)
            clear_day = d % 3 != 0
            ghi = round(800 * sun * (0.9 if clear_day else 0.3), 1)
            hour = {"ghi": ghi, "temp": None if h == 4 else 10.0 + h,
                    "forecast": {"roof": round(500 * sun * (0.9 if clear_day else 0.3), 1), "balcony": 50.0 * sun}}
            hour["alt"] = {"icon": {"ghi": ghi * 0.5, "forecast": {"roof": 100.0 * sun, "balcony": 10.0}}}
            if ghi > 0:
                hour["wx"] = {"cloud": 10 if clear_day else 80, "sun": 0.9, "wind": 2.5, "wdir": 270}
                hour["alt"]["icon"]["wx"] = {"cloud": 50}
            if not (d == DAYS - 1 and h == 19):             # the last day is incomplete
                hour["actual"] = {"roof": round(hour["forecast"]["roof"] * (1.1 if d % 2 else 0.8), 1),
                                  "balcony": 40.0 * sun}
            if h == 13 and d % 4 == 0:
                hour["curtailed"] = True
            if 8 <= h <= 16:
                hour["sat_ghi"] = ghi + 20.0
                hour["measured"] = {"roof": 300.0, "balcony": 30.0}
            hours[str(int(ts))] = hour
        day = {"issued": f"{(FIRST + timedelta(days=d - 1)).isoformat()}T22:00:00+00:00", "hours": hours}
        if d >= DAYS - 2:
            start = DAY0 + d * 86400
            day["history"] = {str(int(start + 9 * 3600)): {str(int(start + 12 * 3600)): 400, str(int(start + 10 * 3600)): 300},
                              str(int(start + 7 * 3600)): {str(int(start + 12 * 3600)): 350}}
        days[(FIRST + timedelta(days=d)).isoformat()] = day
    return json.loads(json.dumps(days))


def test_band_from_stored_archive():
    band = bd.learn_band(stored(), lambda ts: 800.0, lambda ts, ids: 600.0 * len(ids))
    assert band == {"version": 2, "daily": {"all": [-0.04, 0.015, 9]},
                    "hourly": {"overcast": [-0.029, 0.007, 89], "all": [-0.052, 0.022, 141]}}


def test_accuracy_days_from_stored_archive():
    days = views.accuracy_days(stored(), SITE)
    assert len(days) == DAYS and days[0]["date"] == "2026-06-22" and days[0]["issued"] == "2026-06-21T22:00:00+00:00"
    h = days[0]["hours"][9]
    assert h == {"t": 1782133200.0, "ghi": 210.0, "clear": 831.3, "forecast": {"roof": 131.2, "balcony": 43.75},
                 "actual": {"roof": 105.0, "balcony": 35.0}, "alt": {"icon": {"roof": 87.5, "balcony": 10.0}},
                 "curtailed": True, "sat_ghi": 230.0, "measured": {"roof": 300.0, "balcony": 30.0}}
    assert days[0]["hours"][0]["sat_ghi"] is None and days[0]["hours"][0]["measured"] is None
    assert days[0]["hours"][0]["curtailed"] is False
    assert days[-1]["hours"][-1]["actual"] is None and len(days[-1]["hours"]) == 16


def test_day_view_of_a_past_day():
    a = stored()
    key = "2026-07-01"
    start = DAY0 + 9 * 86400
    view = views.day_view(SITE, {"roof": SimpleNamespace(name="Roof", geometry=GEO)},
                          {"roof": model.LearnedArray(efficiency=0.9)}, archive.day(a, key), None, start, start + 86400, start + 5 * 86400)
    assert view["history"] == [{"at": 1782889200.0, "t": [1782907200.0], "wh": [350]},
                               {"at": 1782896400.0, "t": [1782900000.0, 1782907200.0], "wh": [300, 400]}]
    roof = view["arrays"][0]
    assert roof["day_ahead"]["t"][:3] == [1782878400.0, 1782882000.0, 1782885600.0]
    assert roof["day_ahead"]["wh"][:3] == [0.0, 18.8, 37.5]
    assert roof["actual"]["t"][-2:] == [1782925200.0, 1782928800.0] and roof["actual"]["wh"][-2:] == [61.8, 41.2]
    assert len(roof["day_ahead"]["t"]) == 16 and len(roof["actual"]["wh"]) == 15 and len(roof["steps"]) == 16
    assert {k: v[:2] for k, v in view["weather"].items()} == {
        "t": [1782878400.0, 1782882000.0], "ghi": [0.0, 30.0], "clear": [94.4, 243.3], "temp": [None, 15.0]}
    empty = {"t": [], "ghi": [], "clear": [], "temp": []}
    assert views.day_view(SITE, {}, {}, None, None, start, start + 86400, start + 5 * 86400) == \
        {"arrays": [], "band": {"t": [], "p10": [], "p90": []}, "history": [], "weather": empty}


def test_errors_and_sources_from_stored_archive():
    a = stored()
    since = FIRST
    assert archive.daily_error(a, since) == (0.1471663551121221, 9)
    assert archive.daily_error(a, since, "roof", source="icon") == (0.6969773490568419, 9)
    assert archive.alt_sources(a) == ["icon"]
    assert list(archive.day_totals(a)) == [(FIRST + timedelta(days=d)).isoformat() for d in range(DAYS - 1)]
