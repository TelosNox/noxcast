"""Forecast archive: snapshot, actuals, pruning and error."""
import copy
import json
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


def test_snapshot_keeps_the_steps_of_lit_hours():
    a = {}
    steps = {"roof": {T0 + 12 * 3600: [400.04, -100.0, 0.0, 0.0, 0.0, -20.0]}}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {T0 + h * 3600: 50.0 for h in range(24)}}, steps=steps)
    hours = archive.day(a, DAY).hours
    assert hours[12].steps == {"roof": [400.0, -100.0, 0.0, 0.0, 0.0, -20.0]} and hours[11].steps == {}
    assert "steps" not in a[DAY.isoformat()]["hours"][str(int(T0))]


def test_snapshot_names_the_weather_models_of_the_forecast():
    a = {}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}}, models=["icon_seamless", "ecmwf_ifs"])
    assert archive.day(a, DAY).models == ["icon_seamless", "ecmwf_ifs"]
    archive.snapshot(a, DAY, "y", _weather(), {"roof": {}}, models=["gfs_seamless"])      # the last one counts
    assert archive.day(a, DAY).models == ["gfs_seamless"]
    del a[DAY.isoformat()]["models"]                                                     # archived before
    assert archive.day(a, DAY).models == []


def test_ensemble_spread():
    assert archive.ensemble([100.0] * 9) is None                                         # too few runs
    assert archive.ensemble([float(v) for v in range(0, 1001, 25)]) == [100, 500, 900, 41]


def test_snapshot_keeps_the_ensemble_spread_of_lit_hours():
    a = {}
    runs = [float(v) for v in range(0, 1001, 25)]
    ens = {"icon_seamless": {T0: [0.0] * 40, T0 + 3600: runs, T0 + 2 * 3600: runs[:5]}}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}}, ens=ens)
    hours = archive.day(a, DAY).hours
    assert hours[0].ens == {}                       # night: forecast and every run dark
    assert hours[1].ens == {"icon_seamless": [100, 500, 900, 41]}
    assert hours[2].ens == {}                       # too few runs
    assert "ens" not in a[DAY.isoformat()]["hours"][str(int(T0))]
    archive.snapshot(a, DAY, "y", _weather(), {"roof": {}})                             # ensembles failed
    assert archive.day(a, DAY).hours[1].ens == {"icon_seamless": [100, 500, 900, 41]}


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


def test_alternative_sources():
    a = {}
    alt = {m: ({T0 + h * 3600: 50.0 for h in range(24)}, {"roof": {T0 + h * 3600: wh for h in range(24)}})
           for m, wh in (("icon_seamless", 30.0), ("ecmwf_ifs", 70.0))}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {T0 + h * 3600: 50.0 for h in range(24)}}, alt)
    archive.add_actuals(a, "roof", {T0 + h * 3600: 40.0 for h in range(24)}, before=T0 + 24 * 3600)
    primary, _ = archive.daily_error(a, date(2026, 10, 1))
    icon, _ = archive.daily_error(a, date(2026, 10, 1), source="icon_seamless")
    ifs, n = archive.daily_error(a, date(2026, 10, 1), source="ecmwf_ifs")
    assert abs(primary - 0.25) < 1e-9 and abs(icon - 0.25) < 1e-9 and abs(ifs - 0.75) < 1e-9 and n == 1
    assert archive.alt_sources(a) == ["icon_seamless", "ecmwf_ifs"] and archive.alt_sources(a, date(2027, 1, 1)) == []


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


def test_weather_details_only_for_daylight_and_compact():
    a = {}
    wx = {T0 + h * 3600: archive.details(87.4, 0.234, 3.06, 361.2) for h in range(24)}
    alt = {"open_meteo": ({T0 + h * 3600: 50.0 for h in range(24)}, {"roof": {}})}
    alt_wx = {"open_meteo": {T0 + 3600: archive.details(sun=1.0)}}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}}, alt, wx, alt_wx)
    hours = a[DAY.isoformat()]["hours"]
    assert "wx" not in hours[str(int(T0))]                     # no irradiance in this hour
    assert hours[str(int(T0 + 3600))]["wx"] == {"cloud": 87, "sun": 0.23, "wind": 3.1, "wdir": 1}
    assert hours[str(int(T0 + 3600))]["alt"]["open_meteo"]["wx"] == {"sun": 1.0}
    assert "wx" not in hours[str(int(T0 + 7200))]["alt"]["open_meteo"]
    assert archive.details() == {}


def test_complete_days_counts_as_the_parsed_days():
    store = {"2026-10-11": {"hours": {str(T0): {"actual": {"a": 1.0}}, str(T0 + 3600): {"actual": {"a": 2.0}}}},
             "2026-10-12": {"hours": {str(T0): {"actual": {"a": 1.0}}, str(T0 + 3600): {}}},
             "2026-10-13": {"hours": {}}}
    assert archive.complete_days(store) == sum(1 for d in archive.days(store) if d.complete) == 1


def test_history_leaves_the_day_a_job_reads_unchanged():
    a = {}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}})
    seen = a[DAY.isoformat()]
    archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.0})
    assert "history" not in seen and "history" in a[DAY.isoformat()]


def test_merged_keeps_what_the_loop_changed_meanwhile():

    other = date(2026, 10, 12)
    live = {}
    archive.snapshot(live, other, "x", _weather(), {"roof": {}})
    archive.snapshot(live, DAY, "x", _weather(), {"roof": {}})
    base = dict(live)                                                            # as the coordinator does
    work = copy.deepcopy(base)
    archive.add_actuals(work, "roof", {T0 + h * 3600: 40.0 for h in range(24)}, before=T0 + 24 * 3600, curtailed={T0})
    archive.add_history(live, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.0})     # the loop replaces DAY in place
    live = dict(live)
    archive.prune(live, DAY)                                                     # and drops the older day
    out = archive.merged(live, base, work)
    assert set(out) == {DAY.isoformat()}
    hour = out[DAY.isoformat()]["hours"][str(int(T0))]
    assert hour["actual"] == {"roof": 40.0} and hour["curtailed"] is True
    assert str(int(T0 + 6 * 3600)) in out[DAY.isoformat()]["history"]
    untouched = {DAY.isoformat(): base[DAY.isoformat()]}
    assert archive.merged(untouched, base, work)[DAY.isoformat()] is work[DAY.isoformat()]


def _new_fields(a):
    split = {T0 + h * 3600: (300.0 + h, 40.04) for h in range(24)}
    split[T0 + 2 * 3600] = (None, 40.0)                                        # one value missing: none kept
    alt = {"icon": ({T0 + h * 3600: 50.0 * h for h in range(24)}, {"roof": {}})}
    alt_split = {"icon": {T0 + 3600: (210.26, 30.0)}}
    band = {T0 + 3600: (80.04, 120.06)}
    archive.snapshot(a, DAY, "x", _weather(), {"roof": {}}, alt, split=split, alt_split=alt_split, band=band,
                     day_band=[4.1234, 7.5678])


def test_band_split_and_shift_are_stored_compactly():
    a = {}
    _new_fields(a)
    day = a[DAY.isoformat()]
    hours = day["hours"]
    assert day["band"] == [4.123, 7.568]
    assert {"dni", "dhi", "band"}.isdisjoint(hours[str(int(T0))])               # no light, no band published
    assert hours[str(int(T0 + 3600))]["dni"] == 301.0 and hours[str(int(T0 + 3600))]["dhi"] == 40.0
    assert hours[str(int(T0 + 3600))]["band"] == [80.0, 120.1]
    assert "dni" not in hours[str(int(T0 + 2 * 3600))] and "band" not in hours[str(int(T0 + 2 * 3600))]
    assert hours[str(int(T0 + 3600))]["alt"]["icon"]["dni"] == 210.3
    assert "dni" not in hours[str(int(T0 + 2 * 3600))]["alt"]["icon"]
    assert archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.0}, {"roof": 0.12345, "wall": None})
    assert archive.add_history(a, DAY, T0 + 7 * 3600, {T0 + 10 * 3600: 500.0}, {"roof": None})   # no shift: none kept
    assert a[DAY.isoformat()]["shift"] == {str(int(T0 + 6 * 3600)): {"roof": 0.123}}
    archive.snapshot(a, DAY, "y", _weather(), {"roof": {}})                       # kept on a later snapshot
    assert a[DAY.isoformat()]["shift"] == {str(int(T0 + 6 * 3600)): {"roof": 0.123}} and "band" not in a[DAY.isoformat()]


def test_recent_selects_days_without_copying_them():
    a = {"2026-10-11": {}, "2026-10-12": {}, "2026-10-13": {}}
    out = archive.recent(a, date(2026, 10, 12))
    assert list(out) == ["2026-10-12", "2026-10-13"] and out["2026-10-12"] is a["2026-10-12"]


def test_old_rows_load_without_the_new_fields():
    # a store as written before the band, the irradiance split and the shift were archived
    old = {"days": {DAY.isoformat(): {"issued": "x", "hours": {str(int(T0 + h * 3600)): {
        "ghi": 100.0, "temp": 12.0, "forecast": {"roof": 9.0}, "alt": {"icon": {"ghi": 90.0, "forecast": {"roof": 8.0}}}}
        for h in range(24)}, "history": {str(int(T0 + 6 * 3600)): {str(int(T0 + 10 * 3600)): 500}}}}}
    d = archive.day(archive.from_store(old), DAY)
    h = d.hours[0]
    assert d.band is None and d.shift == [] and d.models == [] and len(d.history) == 1
    assert h.dni is None and h.dhi is None and h.band is None and h.ens == {}
    assert h.alt["icon"].dni is None and h.alt["icon"].dhi is None and h.alt["icon"].ghi == 90.0


def test_store_round_trip_keeps_old_and_new_days():
    a = {"2026-10-12": {"issued": "x", "hours": {str(int(T0 - 86400 + h * 3600)): {"ghi": 1.0, "forecast": {}}
                                                 for h in range(24)}}}
    _new_fields(a)
    archive.add_history(a, DAY, T0 + 6 * 3600, {T0 + 10 * 3600: 500.0}, {"roof": 0.1})
    archive.add_actuals(a, "roof", {T0 + 3600: 42.0}, before=T0 + 7200, curtailed={T0 + 3600})
    loaded = archive.from_store(json.loads(json.dumps(archive.to_store(a))))     # what the Store writes and reads
    assert loaded == a
    old, new = archive.days(loaded)
    assert old.band is None and old.hours[0].dni is None
    h = new.hours[1]
    assert new.band == [4.123, 7.568] and new.shift == [(T0 + 6 * 3600, {"roof": 0.1})]
    assert (h.dni, h.dhi, h.band, h.actual, h.curtailed) == (301.0, 40.0, [80.0, 120.1], {"roof": 42.0}, True)
    assert h.alt["icon"].dni == 210.3 and h.alt["icon"].dhi == 30.0
