"""Array checks: geometry fit, efficiency by month, clear-sky balance per month."""
from conftest import load

solar = load("solar")
model = load("model")
ac = load("arraycheck")

SITE = solar.Site(48.0, 8.0, 300.0)
TRUE = model.ArrayGeometry(tilt=35, azimuth=200, peak_w=1000)
START = 1767225600.0          # 2026-01-01 UTC
EFF = 0.9
FREE = {f"{a},{e}": 1.0 for a in range(36) for e in range(18)}     # a map learned free everywhere


def clear_samples(geo, days=range(60, 300, 3)):
    samples, ghi = [], {}
    for d in days:
        for h in range(24):
            t = START + d * 86400 + h * 3600
            beam, diff, _, el, cs = model.clearsky_hour(SITE, geo, t)
            if el > 0:
                samples.append((t, 1000 * EFF * (beam + diff) / 1000))
                ghi[t] = cs
    return samples, ghi


def test_geometry_check_finds_the_azimuth_the_data_fits():
    samples, ghi = clear_samples(TRUE)
    wrong = model.ArrayGeometry(tilt=35, azimuth=170, peak_w=1000)
    res = ac.check_geometry(SITE, wrong, model.LearnedArray(efficiency=EFF, cells=FREE), samples, ghi)
    assert abs(res["azimuth"] - 200) <= 4 and abs(res["tilt"] - 35) <= 4
    assert res["deviates"] and res["improvement"] > 0.5 and res["spread"] < 0.01


def test_geometry_check_confirms_a_fitting_geometry_and_needs_the_satellite():
    samples, ghi = clear_samples(TRUE)
    res = ac.check_geometry(SITE, TRUE, model.LearnedArray(efficiency=EFF, cells=FREE), samples, ghi)
    assert not res["deviates"] and abs(res["azimuth"] - 200) <= 4
    assert ac.check_geometry(SITE, TRUE, model.LearnedArray(efficiency=EFF, cells=FREE), samples, None) is None


def test_unlearned_positions_and_shadow_edges_are_not_known_free():
    learned = model.LearnedArray(cells={"18,6": 1.0, "19,6": 1.0, "20,6": 0.4})
    assert model.known_free(learned, 185.0, 32.0, margin=True)      # cell 18,6 with free and unlearned neighbours
    assert model.known_free(learned, 195.0, 32.0)                   # free itself ...
    assert not model.known_free(learned, 195.0, 32.0, margin=True)  # ... but next to the shaded cell 20,6
    assert not model.known_free(learned, 95.0, 32.0)        # not learned: transmission says 1, but nothing is known
    assert model.transmission(learned, 95.0, 32.0) == 1.0
    # a facade fills only its own sector: an unlearned position outside it stays unknown
    facade = {"direction": 120.0, "height": 20.0, "start": 90.0, "end": 150.0}
    with_facade = model.LearnedArray(cells=dict(learned.cells), facade=facade)
    assert model.known_free(with_facade, 125.0, 50.0)                # high above the facade's edge: known free
    assert not model.known_free(with_facade, 245.0, 50.0)            # west, outside the sector: not known


def test_geometry_check_stays_silent_when_a_tree_leaves_free_hours_on_one_side_only():
    samples, ghi = clear_samples(TRUE)
    # a tree west of the plane's direction (200°) takes every afternoon position; the map has learned it
    tree = {k: (0.3 if int(k.split(",")[0]) * 10 + 5 > 200 else 1.0) for k in FREE}
    learned = model.LearnedArray(efficiency=EFF, cells=tree)
    assert len(ac._rows(SITE, TRUE, learned, samples, ghi, None)) >= ac.GEOMETRY_MIN_HOURS    # enough, but one-sided
    assert ac.check_geometry(SITE, TRUE, learned, samples, ghi) is None
    # an afternoon dimmed by the tree that the map has not learned: no longer free, so no fit that would turn the plane
    east = {k: v for k, v in FREE.items() if int(k.split(",")[0]) * 10 + 5 < 200}
    dimmed = [(t, p * (0.6 if model.clearsky_hour(SITE, TRUE, t)[2] > 210 else 1.0)) for t, p in samples]
    assert ac.check_geometry(SITE, TRUE, model.LearnedArray(efficiency=EFF, cells=east), dimmed, ghi) is None


def test_efficiency_by_month_recovers_the_efficiency():
    samples, ghi = clear_samples(TRUE)
    months = ac.efficiency_by_month(SITE, TRUE, model.LearnedArray(efficiency=EFF, cells=FREE), samples, ghi)
    assert len(months) >= 7
    assert all(abs(v - EFF) < 0.01 for v, n in months.values())


def test_month_balance_shows_shade_and_heat():
    shaded = model.LearnedArray(efficiency=EFF, temp_coeff=-0.004,
                                cells={f"{a},{e}": 0.0 for a in range(9, 24) for e in range(0, 4)})
    air = {START + m * 30 * 86400 + 12 * 3600: 25.0 for m in range(12)}
    months = ac.month_balance(SITE, TRUE, shaded, 2026, air)
    dec, jun = months[11], months[5]
    assert dec["shade"] < jun["shade"] <= 0                # low winter sun is hit harder by a low obstacle
    assert dec["shade"] < -0.3
    assert jun["temperature"] < 0 and jun["air_temp"] == 25.0
    assert dec["reflection"] == 0 and jun["clear_wh"] > dec["clear_wh"]


def test_hour_table_shows_a_peak_power_set_too_low():
    samples, ghi = clear_samples(TRUE)
    low = model.ArrayGeometry(tilt=35, azimuth=200, peak_w=720)      # the data fit 1000 Wp at 90 %: 125 %
    table = ac.hour_table(SITE, low, model.LearnedArray(efficiency=1.25, cells=FREE), samples, ghi, steady={samples[0][0]})
    assert table["columns"] == ac.HOUR_COLUMNS
    col = {c: i for i, c in enumerate(table["columns"])}
    checked = [r for r in table["rows"] if r[col["check"]]]
    assert len(checked) > 100 and all(abs(r[col["efficiency"]] - 1.25) < 0.01 for r in checked)
    assert table["rows"][0][col["steady"]] and not table["rows"][1][col["steady"]]
    assert all(r[col["shade"]] == 1 for r in table["rows"])                 # learned free everywhere
    assert ac.hour_table(SITE, low, model.LearnedArray(), samples)["rows"][0][col["efficiency"]] is None   # no satellite


def test_efficiency_by_direction_is_flat_with_the_right_azimuth_and_tilts_with_a_wrong_one():
    samples, ghi = clear_samples(TRUE)
    right = ac.efficiency_by_direction(SITE, TRUE, model.LearnedArray(efficiency=EFF, cells=FREE), samples, ghi)
    assert {"SE", "S", "SW"} <= set(right) and all(abs(v - EFF) < 0.01 for v, n in right.values())
    assert list(right) == [s for s in ac.SECTORS if s in right]
    wrong = model.ArrayGeometry(tilt=35, azimuth=150, peak_w=1000)      # configured further east than built
    d = ac.efficiency_by_direction(SITE, wrong, model.LearnedArray(efficiency=EFF, cells=FREE), samples, ghi)
    assert d["SW"][0] > d["SE"][0] + 0.1                                # the afternoon is higher


def _annotated(geo, samples, ghi, efficiency):
    la = model.LearnedArray(efficiency=efficiency, samples=len(samples), cells=dict(FREE))
    ac.annotate(SITE, geo, la, samples, None, ghi, 2026)
    return la


def test_hints_name_what_does_not_fit():
    samples, ghi = clear_samples(TRUE)
    assert _annotated(TRUE, samples, ghi, EFF).hints == []
    low = model.ArrayGeometry(tilt=35, azimuth=200, peak_w=720)         # peak power too low: 125 %, max above it
    la = _annotated(low, samples, ghi, 1.25)
    assert la.hints[:2] == ["peak", "high"] and la.max_power_w > 720 and la.max_power_t in dict(samples)
    east = model.ArrayGeometry(tilt=35, azimuth=140, peak_w=1000)
    assert "direction" in _annotated(east, samples, ghi, EFF).hints


def test_a_low_efficiency_alone_is_no_hint():
    shaded = model.LearnedArray(efficiency=0.47, samples=500)
    assert ac.hints(TRUE, shaded) == []
