"""What a learning run changed: cells, parameters and the clear-day energy before and after."""
from conftest import load

solar = load("solar")
model = load("model")
lc = load("learnchange")

SITE = solar.Site(48.0, 8.0, 300.0)
GEO = model.ArrayGeometry(tilt=30, azimuth=180, peak_w=1000)


def test_first_run_has_nothing_before():
    new = model.LearnedArray(efficiency=0.9, cells={"18,6": 0.5}, samples=100)
    out = lc.changes(SITE, GEO, None, new, 2026)
    assert out["first"] and out["efficiency"] == [None, 0.9] and out["samples"] == [None, 100]
    assert out["cells"] == {"moved": 0, "up": 0, "down": 0, "added": 0, "removed": 0} and out["top"] == []
    assert all(d["before"] is None and d["after"] > 0 for d in out["clear_days"]) and len(out["clear_days"]) == 12


def test_moved_cells_and_their_effect_on_a_clear_day():
    old = model.LearnedArray(efficiency=0.9, cells={"18,6": 0.9, "17,6": 0.9, "16,6": 0.9, "19,6": 0.9}, samples=100,
                             dead_days=["2026-03-01"])
    # a new shadow at noon in winter, one cell barely moved, one dropped, one new; ten more hours
    new = model.LearnedArray(efficiency=0.9, cells={"18,6": 0.3, "17,6": 0.92, "19,6": 0.9, "20,6": 0.6}, samples=110,
                             cell_info={"18,6": [14, "clear", 0.05]}, dead_days=["2026-03-01", "2026-03-04"])
    out = lc.changes(SITE, GEO, old, new, 2026)
    assert not out["first"] and out["samples"] == [100, 110] and out["dead_days"] == ["2026-03-04"]
    assert out["cells"] == {"moved": 1, "up": 0, "down": 1, "added": 1, "removed": 1}
    assert out["cell_before"] == {"18,6": 0.9, "20,6": None, "16,6": 0.9}
    top = out["top"][0]
    assert top["key"] == "18,6" and top["before"] == 0.9 and top["after"] == 0.3 and top["hours"] == 14
    assert top["origin"] == "clear" and top["azimuth"] == [180.0, 190.0] and top["elevation"] == [30.0, 35.0]
    # the shadow costs energy where the sun passes it (winter noon at 48° N stands near 20–30°, summer above 60°)
    dec = next(d for d in out["clear_days"] if d["month"] == 2)
    jun = next(d for d in out["clear_days"] if d["month"] == 6)
    assert dec["after"] < dec["before"] and abs(jun["after"] - jun["before"]) < 1e-6


def test_the_same_state_changes_nothing():
    la = model.LearnedArray(efficiency=0.9, cells={"18,6": 0.5}, samples=100)
    out = lc.changes(SITE, GEO, la, la, 2026)
    assert out["cells"]["moved"] == 0 and out["cell_before"] == {} and out["dead_days"] == []
    assert all(d["before"] == d["after"] for d in out["clear_days"])
