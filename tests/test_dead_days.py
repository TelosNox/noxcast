"""Days on which an array delivered almost nothing (outage, snow, too dark) are left out of learning."""
import random

from conftest import load

solar = load("solar")
model = load("model")
ln = load("learning")
bt = load("backtest")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(tilt=30, azimuth=180, peak_w=1000)
START = 1780272000.0          # 2026-06-01 UTC
EFF = 0.93


def _hour(t: float, sky: float, beam_free: float = 1.0) -> float:
    """Mean power (W) of the hour from t: sky scales the beam (1 clear, 0 overcast), diffuse stays."""
    total = 0.0
    dni_extra = solar.extra_radiation(t + 1800)
    for i in range(model.SUBSTEPS):
        sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
        if sun.elevation <= 0:
            continue
        ghi, dni, dhi = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, dni_extra)
        beam, diff = solar.plane_of_array(GEO.tilt, GEO.azimuth, sun, dni * sky * beam_free, dhi * max(sky, 0.3),
                                          ghi, dni_extra)
        total += GEO.peak_w * EFF * (beam + diff) / 1000
    return total / model.SUBSTEPS


def _days(kinds: list[str]) -> dict[float, float]:
    """One solar day per kind: clear, overcast, shaded (no beam all day, a deep winter shade), dead (0 W), trickle
    (1 % of clear) or none (before the array existed: 0 W)."""
    power = {}
    for d, kind in enumerate(kinds):
        for h in range(24):
            t = START + d * 86400 + h * 3600
            clear = _hour(t, 1.0)
            power[t] = {"clear": clear, "overcast": 0.15 * clear, "shaded": _hour(t, 1.0, 0.0),
                        "dead": 0.0, "none": 0.0, "trickle": 0.01 * clear}[kind]
    return power


def _day(n: int) -> int:
    return model.solar_day(SITE, START + n * 86400 + 12 * 3600)


def test_only_days_without_output_count():
    power = _days(["none", "none", "clear", "overcast", "dead", "shaded", "trickle", "clear"])
    # before the first production is commissioning, not an outage; overcast and shade still deliver diffuse light
    assert model.dead_days(SITE, GEO, power) == [_day(4), _day(6)]


def test_valid_since_and_a_day_too_dark_to_tell():
    power = _days(["clear", "dead", "clear", "dead", "clear"])
    assert model.dead_days(SITE, GEO, power, valid_since=START + 2 * 86400) == [_day(3)]
    tiny = model.ArrayGeometry(tilt=90, azimuth=0, peak_w=1000)          # a north wall: hardly any clear-sky energy
    assert model.dead_days(SITE, tiny, {t: 0.0 for t in power}) == []


def test_a_solar_day_keeps_its_daylight_together():
    noon = START + 12 * 3600 - SITE.longitude * 240
    assert model.solar_day(SITE, noon - 6 * 3600) == model.solar_day(SITE, noon + 6 * 3600)
    assert model.solar_day(SITE, noon - 13 * 3600) != model.solar_day(SITE, noon)


def _history(power: dict[float, float]) -> ln.PvHistory:
    return ln.PvHistory({"s": power}, {"s": dict(power)}, {"s": dict(power)}, {}, {})


def test_learning_leaves_dead_days_out_as_if_excluded_by_hand():
    rng = random.Random(3)
    kinds = ["clear" if rng.random() < 0.6 else "overcast" for _ in range(60)]
    for d in range(20, 30):
        kinds[d] = "dead"
    power = _days(kinds)
    arr = ln.ArrayConfig("a", "Roof", ("s",), GEO, None)
    now = START + 61 * 86400
    filtered = ln.learn_array(SITE, arr, _history(power), now, 95.0, [])
    start, end = START + 20 * 86400 - 6 * 3600, START + 30 * 86400 - 6 * 3600
    by_hand = ln.learn_array(SITE, arr, _history(power), now, 95.0, [(start, end)])
    assert (filtered.efficiency, filtered.cells, filtered.sky_view) == (by_hand.efficiency, by_hand.cells,
                                                                         by_hand.sky_view)
    assert len(filtered.dead_days) == 10
    assert filtered.dead_days == [model.solar_date(_day(d)) for d in range(20, 30)]   # dates, not noon times
    kept = ln.learn_array(SITE, arr, _history(power), now, 95.0, [], keep=lambda t: t < start)
    assert len(kept.dead_days) == 10                                     # the list does not depend on the half learned


def test_backtest_scores_the_hours_learning_uses():
    power = _days(["clear", "dead", "clear"])
    arr = ln.ArrayConfig("a", "Roof", ("s",), GEO, None)
    hours = bt.usable(arr, _history(power), 95.0, [], SITE)
    assert not any(model.solar_day(SITE, t) == _day(1) for t in hours)
    assert any(model.solar_day(SITE, t) == _day(1) for t in bt.usable(arr, _history(power), 95.0, []))


# ---------------------------------------------------------------------------- snow

def _snow_power(days: int, cover: dict[int, float]) -> dict[float, float]:
    """Clear days; cover: day -> share of the light the snow lets through (0.3 = partly covered)."""
    return {START + d * 86400 + h * 3600: _hour(START + d * 86400 + h * 3600, 1.0) * cover.get(d, 1.0)
            for d in range(days) for h in range(24)}


def _cold(days: int, temp: float = -3.0) -> dict[float, float]:
    return {START + h * 3600: temp for h in range(days * 24)}


def _on(hours: set[float], day: int) -> bool:
    return any(START + day * 86400 <= t < START + (day + 1) * 86400 for t in hours)


def test_partly_covered_array_is_left_out_until_it_delivers_again():
    snowfall = {START + 86400 + 3 * 3600: 4.0}                            # 4 cm in the night of day 1
    power = _snow_power(8, {1: 0.3, 2: 0.3, 3: 0.4})                     # uncovered again on day 4
    hours = model.snow_hours(SITE, GEO, power, snowfall, _cold(8))
    assert all(_on(hours, d) for d in (1, 2, 3)) and not _on(hours, 5)
    # the first strong hour of day 4 ends the window: only its dark morning hours are still in it
    assert all(t < START + 4 * 86400 + 9 * 3600 for t in hours)


def test_a_dusting_and_no_archive_leave_nothing_out():
    power = _snow_power(4, {1: 0.3})
    assert model.snow_hours(SITE, GEO, power, {START + 86400: 0.5}, _cold(4)) == set()
    assert model.snow_hours(SITE, GEO, power, None, _cold(4)) == set()


def test_thaw_and_the_longest_window_end_it_although_the_array_stays_dim():
    snowfall = {START + 86400: 3.0}
    dim = {d: 0.3 for d in range(1, 20)}                                 # e.g. deep shade: never looks free
    warm = model.snow_hours(SITE, GEO, _snow_power(20, dim), snowfall, _cold(20, 2.0))   # 24 K h after 12 h
    assert warm and max(warm) < START + 86400 + 13 * 3600
    cold = model.snow_hours(SITE, GEO, _snow_power(20, dim), snowfall, _cold(20))
    assert _on(cold, 7) and not _on(cold, 8)                              # at most SNOW_MAX_DAYS after the snow
    hours, capped = model.snow_windows(SITE, GEO, _snow_power(20, dim), snowfall, _cold(20))
    assert hours == cold and capped == [START + 86400 + model.SNOW_MAX_DAYS * 86400]   # closed by the cap alone
    assert model.snow_windows(SITE, GEO, _snow_power(20, dim), snowfall, _cold(20, 2.0))[1] == []   # by thaw
    _, unfinished = model.snow_windows(SITE, GEO, _snow_power(5, dim), snowfall, _cold(5))
    assert unfinished == []                                              # the history ends inside the window
    power = _snow_power(20, dim)
    dull = {t: 0.2 * model.clearsky_weather(SITE, t).ghi for t in power}       # an overcast week: no chance
    assert model.snow_windows(SITE, GEO, power, snowfall, _cold(20), dull)[1] == []
    later = {t: p for t, p in power.items() if t >= START + 10 * 86400}        # installed after the window
    assert model.snow_windows(SITE, GEO, later, snowfall, _cold(20))[1] == []


def test_under_clouds_the_array_cannot_show_it_is_free():
    snowfall = {START + 86400: 3.0}
    power = _snow_power(4, {1: 0.3})
    sat = {t: 0.2 * model.clearsky_weather(SITE, t).ghi for t in power}  # overcast: kt 0.2
    hours = model.snow_hours(SITE, GEO, power, snowfall, _cold(4), sat)
    assert _on(hours, 3)                                                  # full output in relative terms, but too dark


def test_learning_and_backtest_leave_snow_out():
    snowfall = {START + 20 * 86400: 5.0}
    power = _snow_power(40, {20: 0.2, 21: 0.3})
    arr = ln.ArrayConfig("a", "Roof", ("s",), GEO, None)
    history = ln.PvHistory({"s": power}, {"s": dict(power)}, {"s": dict(power)}, {}, {}, _cold(40), snowfall=snowfall)
    learned = ln.learn_array(SITE, arr, history, START + 41 * 86400, 95.0, [])
    # the third day holds the dawn hours before the first strong hour ends the window
    assert len(learned.snow_days) == 3 and not learned.dead_days
    lit = {t for t, w in bt.usable(arr, history, 95.0, [], SITE).items() if w > 0}
    assert not _on(lit, 21) and _on(lit, 19)


def test_snow_before_the_array_produced_is_not_counted():
    power = _snow_power(6, {0: 0.0, 1: 0.0, 2: 0.0})                    # installed on day 3
    hours = model.snow_hours(SITE, GEO, power, {START + 86400: 5.0}, _cold(6))
    # from the first production on the window still runs, until the first strong hour shows the modules free
    assert all(START + 3 * 86400 <= t < START + 3 * 86400 + 9 * 3600 for t in hours)
