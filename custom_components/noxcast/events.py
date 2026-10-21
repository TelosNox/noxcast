"""Device runs found in the house load, as suggestions for consumers (load forecast step 6).

Pure functions, no Home Assistant imports. From 5-minute values (the recorder keeps them for about
ten days) the load above a moving baseline is searched for plateaus: a device switching on raises
the load by its power for its run time. Runs of similar power and duration are grouped; a group
that recurs is offered as a suggestion ("about 2.1 kW for 1.5 h, 4 times, mostly 19–21 h").

Nothing here changes the forecast: devices of equal power cannot be told apart, and small or
fluctuating loads (PCs, TV) stay in the base load. Configured consumers are subtracted first, so
known devices are not suggested again.
"""
from __future__ import annotations

from datetime import datetime, tzinfo
from statistics import median

SLOT = 300.0                 # 5-minute statistics
DAYS = 10                    # how far back the recorder usually keeps them
BASELINE_SLOTS = 73         # ±3 h window of the moving baseline, so a run of two hours stays a minority
BASELINE_Q = 0.15            # baseline = a low quantile of the window: a run lifts the load, not the baseline
MIN_STEP_W = 500.0           # smaller steps disappear in the household noise
MIN_SLOTS = 3                # at least 15 minutes
MAX_GAP_SLOTS = 1            # a program may pause for 5 minutes (e.g. a washer between heating phases)
POWER_TOLERANCE = 0.25       # runs of one device: power within ±25 % ...
DURATION_TOLERANCE = 0.5     # ... and duration within ±50 % of the group
MIN_RUNS = 3                 # a group must recur to be a suggestion


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def excess(series: dict[float, float]) -> dict[float, float]:
    """Load above a low quantile of the surrounding six hours."""
    ts = sorted(series)
    out = {}
    half = BASELINE_SLOTS // 2
    for i, t in enumerate(ts):
        window = [series[ts[j]] for j in range(max(0, i - half), min(len(ts), i + half + 1))]
        out[t] = series[t] - _quantile(window, BASELINE_Q)
    return out


def runs(series: dict[float, float]) -> list[dict]:
    """Plateaus of the excess load: start, minutes, median power above the baseline, energy."""
    ex = excess(series)
    found, current = [], []
    for t in sorted(ex):
        if ex[t] >= MIN_STEP_W:
            if current and t - current[-1][0] > (MAX_GAP_SLOTS + 1) * SLOT:   # longer pause or missing data
                found.append(current)
                current = []
            current.append((t, ex[t]))
        elif current and t - current[-1][0] > MAX_GAP_SLOTS * SLOT:
            found.append(current)
            current = []
    if current:
        found.append(current)
    out = []
    for r in found:
        if len(r) < MIN_SLOTS:
            continue
        minutes = (r[-1][0] - r[0][0] + SLOT) / 60
        out.append({"start": r[0][0], "minutes": minutes, "power_w": median(w for _, w in r),
                    "energy_wh": sum(w for _, w in r) * SLOT / 3600})
    return out


def groups(found: list[dict], tz: tzinfo) -> list[dict]:
    """Runs of similar power and duration; recurring groups, strongest energy first."""
    clusters: list[list[dict]] = []
    for r in sorted(found, key=lambda r: r["power_w"]):
        for c in clusters:
            p, m = median(x["power_w"] for x in c), median(x["minutes"] for x in c)
            if abs(r["power_w"] - p) <= POWER_TOLERANCE * p and abs(r["minutes"] - m) <= DURATION_TOLERANCE * m:
                c.append(r)
                break
        else:
            clusters.append([r])
    out = []
    for c in clusters:
        if len(c) < MIN_RUNS:
            continue
        hours = sorted(datetime.fromtimestamp(r["start"], tz).hour for r in c)
        out.append({"power_w": round(median(r["power_w"] for r in c)), "minutes": round(median(r["minutes"] for r in c)),
                    "energy_wh": round(median(r["energy_wh"] for r in c)), "count": len(c),
                    "hours": [hours[len(hours) // 4], hours[(3 * len(hours)) // 4]],
                    "last": max(r["start"] for r in c)})
    return sorted(out, key=lambda g: -g["energy_wh"] * g["count"])


def suggestions(load: dict[float, float], consumers: list[dict[float, float]], tz: tzinfo) -> dict:
    """Recurring device runs in the house load minus the configured consumers."""
    rest = {t: w - sum(c.get(t, 0.0) for c in consumers) for t, w in load.items()}
    found = runs(rest)
    days = (max(load) - min(load)) / 86400 if load else 0
    return {"days": round(days, 1), "runs": len(found), "groups": groups(found, tz)}
