"""Device runs in the house load: plateaus above a moving baseline, grouped into suggestions."""
import random
from datetime import timezone

from conftest import load

ev = load("events")

UTC = timezone.utc
SLOT = 300.0


def house(days=10, seed=5):
    rng = random.Random(seed)
    s = {}
    for i in range(int(days * 86400 / SLOT)):
        t = i * SLOT
        h = (t % 86400) / 3600
        w = 250 + 80 * rng.random() + (150 if 18 <= h < 22 else 0)      # base load with noise and an evening bump
        if (t // 86400) % 2 == 0 and 19 <= h < 20.5:                    # dishwasher every second day, 1.5 h, 2 kW
            w += 2000 if h < 19.5 else 1200 if (h * 60) % 20 > 0 else 0  # heating, then a phase with 5-minute pauses
        if (t // 86400) % 3 == 1 and 10 <= h < 10.75:                   # kettle-like oven run, 45 min, 3 kW
            w += 3000
        s[t] = w
    return s


def test_runs_find_the_plateaus_and_ignore_noise():
    found = ev.runs(house())
    assert all(r["power_w"] >= ev.MIN_STEP_W for r in found)
    starts = sorted(int((r["start"] % 86400) // 3600) for r in found)
    assert set(starts) <= {10, 19}
    assert len([s for s in starts if s == 19]) == 5 and len([s for s in starts if s == 10]) == 3


def test_groups_turn_recurring_runs_into_suggestions():
    out = ev.suggestions(house(), [], UTC)
    g = {x["hours"][0]: x for x in out["groups"]}
    assert set(g) == {10, 19}
    assert g[10]["count"] == 3 and 40 <= g[10]["minutes"] <= 50 and 2800 <= g[10]["power_w"] <= 3200
    # the dishwasher: 2 kW heating, then 1.2 kW with short pauses, as one run of 1.5 h
    assert g[19]["count"] == 5 and 80 <= g[19]["minutes"] <= 95 and 1100 <= g[19]["power_w"] <= 1500
    assert 1900 <= g[19]["energy_wh"] <= 2300


def test_configured_consumers_are_not_suggested_again():
    load_ = house()
    oven = {t: (3000.0 if (t // 86400) % 3 == 1 and 10 <= (t % 86400) / 3600 < 10.75 else 0.0) for t in load_}
    out = ev.suggestions(load_, [oven], UTC)
    assert [x["hours"][0] for x in out["groups"]] == [19]
