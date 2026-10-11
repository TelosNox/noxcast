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
