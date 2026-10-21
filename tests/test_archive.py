"""Forecast archive: snapshot, actuals, pruning and error."""
from datetime import date

from conftest import load

archive = load("archive")

DAY = date(2026, 10, 13)
T0 = 1792274400.0                 # some hour start


def _weather(n=24):
    return {T0 + h * 3600: (100.0 * h, 12.0) for h in range(n)}


def test_snapshot_needs_full_day_and_keeps_actuals():
    a = {}
    assert not archive.snapshot(a, DAY, "x", _weather(10), {"roof": {}})
    assert archive.snapshot(a, DAY, "x", _weather(), {"roof": {T0 + h * 3600: 50.0 for h in range(24)}})
    archive.add_actuals(a, "roof", {T0 + h * 3600: 40.0 for h in range(24)}, before=T0 + 24 * 3600, curtailed={T0})
    assert a[DAY.isoformat()]["hours"][str(int(T0))]["curtailed"] is True
    archive.snapshot(a, DAY, "y", _weather(), {"roof": {T0 + h * 3600: 60.0 for h in range(24)}})
    assert a[DAY.isoformat()]["hours"][str(int(T0))]["actual"] == {"roof": 40.0}


def test_daily_error_and_prune():
    a = {}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {T0 + h * 3600: 50.0 for h in range(24)},
                                               "balcony": {T0 + h * 3600: 10.0 for h in range(24)}})
    archive.add_actuals(a, "roof", {T0 + h * 3600: 40.0 for h in range(24)}, before=T0 + 24 * 3600)
    assert archive.daily_error(a, date(2026, 10, 1)) == (None, 0)       # balcony actuals missing
    archive.add_actuals(a, "balcony", {T0 + h * 3600: 10.0 for h in range(24)}, before=T0 + 24 * 3600)
    error, days = archive.daily_error(a, date(2026, 10, 1))
    assert days == 1 and abs(error - 240 / 1200) < 1e-9
    roof, _ = archive.daily_error(a, date(2026, 10, 1), "roof")
    assert abs(roof - 0.25) < 1e-9
    archive.prune(a, date(2026, 10, 14))
    assert a == {}


def test_actuals_only_for_ended_hours():
    a = {}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}})
    archive.add_actuals(a, "roof", {T0 + h * 3600: 1.0 for h in range(24)}, before=T0 + 5 * 3600)
    with_actual = [h for h in a[DAY.isoformat()]["hours"].values() if "actual" in h]
    assert len(with_actual) == 5


def test_alternative_source_and_mix():
    a = {}
    alt = {"open_meteo": ({T0 + h * 3600: 50.0 for h in range(24)}, {"roof": {T0 + h * 3600: 30.0 for h in range(24)}})}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {T0 + h * 3600: 50.0 for h in range(24)}}, alt)
    archive.add_actuals(a, "roof", {T0 + h * 3600: 40.0 for h in range(24)}, before=T0 + 24 * 3600)
    primary, _ = archive.daily_error(a, date(2026, 10, 1))
    other, _ = archive.daily_error(a, date(2026, 10, 1), source="open_meteo")
    mix, n = archive.daily_error(a, date(2026, 10, 1), source=archive.MIX)
    assert abs(primary - 0.25) < 1e-9 and abs(other - 0.25) < 1e-9 and n == 1 and mix == 0.0


def test_history_once_per_stamp_and_kept_on_snapshot():
    a = {}
    assert not archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.0})      # day not archived
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}})
    assert archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.4, T0: 0.0})
    assert not archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 900.0})
    assert a[DAY.isoformat()]["history"] == {str(int(T0 + 6 * 3600)): {str(int(T0 + 10 * 3600)): 500}}
    archive.snapshot(a, DAY, "y", _weather(), {"roof": {}})
    assert str(int(T0 + 6 * 3600)) in a[DAY.isoformat()]["history"]


def test_measured_once_per_hour_from_satellite():
    a = {}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}})
    sat = {T0 + h * 3600: 200.0 for h in range(24)}
    calls = []

    def predict(hours):
        calls.append(hours)
        return {t: g / 2 for t, g, _ in hours}

    n = archive.add_measured(a, "roof", sat, {T0: 5.0}, predict, before=T0 + 6 * 3600)
    hours = a[DAY.isoformat()]["hours"]
    assert n == 6 and hours[str(int(T0))]["measured"] == {"roof": 100.0} and hours[str(int(T0))]["sat_ghi"] == 200.0
    assert calls[0][0][2] == 5.0 and calls[0][1][2] == 12.0       # measured air temperature, else the archived one
    assert archive.add_measured(a, "roof", sat, None, predict, before=T0 + 6 * 3600) == 0   # never recomputed
    assert archive.add_measured(a, "roof", {}, None, predict, before=T0 + 24 * 3600) == 0   # no satellite data yet
