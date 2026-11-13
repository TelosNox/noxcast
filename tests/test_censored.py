"""Hours at a power limit count as "at least this much" in learning (#248, censored learning)."""
import random
from datetime import date

from conftest import load

solar = load("solar")
model = load("model")
ln = load("learning")
visual = load("visual")
ac = load("arraycheck")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(tilt=30, azimuth=180, peak_w=1000)
START = 1767225600.0          # 2026-01-01 UTC
EFF = 0.93


def test_censored_quantile_without_bounds_is_the_weighted_quantile():
    rng = random.Random(2)
    for _ in range(50):
        values = [rng.uniform(0, 2) for _ in range(rng.randint(1, 40))]
        weights = [rng.uniform(0.1, 1) for _ in values]
        for q in (0.1, 0.5, 0.9, 0.95):
            assert model.censored_quantile(values, weights, [False] * len(values), q) == \
                model.weighted_quantile(values, weights, q)


def test_bounds_above_the_quantile_do_not_move_it():
    # 1..10, the top four known only as "at least 7": the median is still 5
    values = [1, 2, 3, 4, 5, 6, 7, 7, 7, 7]
    floors = [False] * 6 + [True] * 4
    assert model.censored_quantile(values, [1.0] * 10, floors, 0.5) == 5


def test_bounds_below_pass_their_weight_to_higher_values():
    # two hours known only as "at least 0.5" are somewhere above it: taken as measured they would set the median
    values, floors = [0.5, 0.5, 0.9, 1.0, 1.1], [True, True, False, False, False]
    assert model.weighted_quantile(values, [1.0] * 5, 0.5) == 0.9
    assert model.censored_quantile(values, [1.0] * 5, floors, 0.5) == 1.0
    assert model.censored_quantile(values, [1.0] * 5, floors, 0.2) == 0.9


def test_quantile_hidden_by_bounds_is_none():
    values, floors = [1, 2, 3, 5, 5, 5, 5, 5, 5, 5], [False] * 3 + [True] * 7
    assert model.censored_quantile(values, [1.0] * 10, floors, 0.5) is None
    assert model.censored_quantile(values, [1.0] * 10, floors, 0.3) == 3


def test_hot_bins_hidden_by_the_limit_are_left_out():
    rng = random.Random(3)
    rows = [(t_cell, (1 + -0.004 * (t_cell - 25)) * rng.uniform(0.95, 1.0), 1.0, False)
            for t_cell in range(5, 60) for _ in range(20)]
    free, _ = model._learn_temp_coeff(rows)
    # the hot bins (from 45 °C) mostly at the limit, their plateau as measured would flatten the slope
    capped = [(t, 0.85, w, True) if t >= 45 and rng.random() < 0.8 else (t, r, w, False) for t, r, w, _ in rows]
    measured, _ = model._learn_temp_coeff([(t, r, w) for t, r, w, _ in capped])
    censored, _ = model._learn_temp_coeff(capped)
    assert abs(censored - free) < abs(measured - free)
    assert abs(censored - free) < 0.0003


def clear_days(days: int = 200):
    """Clear, steady days without shade: power, satellite irradiance, steady hours."""
    rng = random.Random(5)
    samples, sat, steady = [], {}, set()
    for d in range(days):
        for h in range(24):
            t = START + 60 * 86400 + d * 86400 + h * 3600
            dni_extra = solar.extra_radiation(t + 1800)
            power = ghi_sum = 0.0
            for i in range(model.SUBSTEPS):
                sun = solar.sun_position(SITE, t + (i + 0.5) * 3600 / model.SUBSTEPS)
                if sun.elevation <= 0:
                    continue
                ghi = solar.clearsky_simplified_solis(sun.elevation, SITE.pressure, dni_extra)[0]
                dni, dhi = solar.erbs(ghi, sun.zenith, dni_extra)
                beam, diff = solar.plane_of_array(GEO.tilt, GEO.azimuth, sun, dni, dhi, ghi, dni_extra)
                power += GEO.peak_w * EFF * (beam + diff) / 1000
                ghi_sum += ghi
            power /= model.SUBSTEPS
            if power > 0:
                power *= rng.uniform(0.98, 1.02)
                steady.add(t)
            samples.append((t, power))
            sat[t] = ghi_sum / model.SUBSTEPS
    return samples, sat, steady


def test_hours_at_a_limit_neither_lower_the_efficiency_nor_darken_the_map():
    samples, sat, steady = clear_days()
    now = START + 260 * 86400
    limit = 0.7 * max(p for _, p in samples)
    clipped = [(t, min(p, limit)) for t, p in samples]
    floors = {t for t, p in samples if p >= 0.95 * limit}
    ref = model.learn(SITE, GEO, samples, now=now, satellite_ghi=sat, steady=steady)
    today = model.learn(SITE, GEO, clipped, now=now, satellite_ghi=sat, steady=steady)
    censored = model.learn(SITE, GEO, clipped, now=now, satellite_ghi=sat, steady=steady, floors=floors,
                           limit_w=limit)
    assert abs(censored.efficiency - ref.efficiency) < 0.01
    def darker(learned):
        """Cells darker than without the limit, from 15° up: below, the sun never reaches the limit here and the
        cells follow model errors at low sun."""
        return [k for k in ref.cells if k in learned.cells and int(k.split(",")[1]) >= 3
                and learned.cells[k] < ref.cells[k] - 0.03]

    assert darker(today)                                     # midday "shade" that is the limit
    assert not darker(censored)
    assert censored.limit_cells and all(censored.cells[k] == 1.0 for k in censored.limit_cells)
    assert not ref.limit_cells and not today.limit_cells


def test_floor_hours_from_the_period_maximum():
    history = ln.PvHistory(power={"a": {0.0: 100.0, 3600.0: 190.0}, "b": {0.0: 50.0, 3600.0: 50.0}},
                           power_min={}, power_max={"a": {0.0: 180.0, 3600.0: 199.0}, "b": {0.0: 60.0}},
                           factors={"a": 1000.0}, soc={})
    arr = ln.ArrayConfig("x", "X", ("a", "b"), GEO, None, limits=(("a", 199_000.0),))
    assert ln.floor_hours(arr, history) == {3600.0}          # 180 kW is below 95 % of 199 kW
    assert arr.limit_w is None                               # sensor b has no limit: no array limit
    both = ln.ArrayConfig("x", "X", ("a", "b"), GEO, None, limits=(("a", 199_000.0), ("b", 300.0)))
    assert both.limit_w == 199_300.0


def test_cells_at_the_limit_are_marked_in_the_image():
    learned = model.LearnedArray(cells={"18,10": 1.0, "17,10": 0.5}, limit_cells=["18,10"], learned_at=START)
    svg = visual.render_shading_map(SITE, learned, set(), date(2026, 6, 1), "Roof", "en", GEO)
    assert "at the power limit" in svg and "dot = at the power limit" in svg
    plain = visual.render_shading_map(SITE, model.LearnedArray(cells={"18,10": 1.0}, learned_at=START), set(),
                                      date(2026, 6, 1), "Roof", "de", GEO)
    assert "Leistungsgrenze" not in plain


def test_hour_table_flags_hours_at_the_limit():
    samples, sat, steady = clear_days(days=5)
    lit = [s for s in samples if s[1] > 0]
    table = ac.hour_table(SITE, GEO, model.LearnedArray(efficiency=EFF), lit, sat, steady=steady, floors={lit[3][0]})
    col = ac.HOUR_COLUMNS.index("at_limit")
    assert [r[col] for r in table["rows"]].count(True) == 1
