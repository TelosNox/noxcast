"""Uncertainty band of the PV forecast, learned from the day-ahead archive.

Pure functions, no Home Assistant imports. The band varies the clouds, not the output: for past
days (and hours) the shift in clearness is collected, (actual - forecast) / clear-sky output, where
the clear-sky output comes from the same learned model (efficiency, shading) with a clear sky. The
shifts are grouped per weather class of the forecast (clearness = forecast GHI relative to clear-sky
GHI). The 10th and 90th percentile of the shift, added to a new forecast's clearness, give P10 and
P90; the clearness stays between 0 and a clear sky plus a small tolerance (model errors such as
geometry or a wrongly learned shadow, cloud enhancement). A clear forecast can therefore hardly
rise, an overcast one can rise up to a clear sky. Hours with possible curtailment are left out,
since their actual value does not show what the sun gave.
"""
from __future__ import annotations

from collections.abc import Callable

VERSION = 2                  # 1: ratios actual / forecast (unbounded), 2: clearness shifts
CLASSES = ((0.35, "overcast"), (0.7, "mixed"), (float("inf"), "clear"))
Q_LOW, Q_HIGH = 0.1, 0.9
CLEARNESS_MAX = 1.05         # a clear sky plus a few percent for model errors; no more output than that
MIN_DAYS = 8                 # days a class needs; otherwise all days together
MIN_HOURS = 40               # hours a class needs; otherwise all hours together
MIN_DAY_WH = 200.0           # smaller clear-sky days give no usable shift
MIN_HOUR_WH = 50.0


def weather_class(clearness: float) -> str:
    for limit, name in CLASSES:
        if clearness < limit:
            return name
    return CLASSES[-1][1]


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _summary(groups: dict[str, list[float]], minimum: int) -> dict:
    out = {name: [round(_quantile(v, Q_LOW), 3), round(_quantile(v, Q_HIGH), 3), len(v)]
           for name, v in groups.items() if len(v) >= minimum}
    every = [r for v in groups.values() for r in v]
    if len(every) >= minimum:
        out["all"] = [round(_quantile(every, Q_LOW), 3), round(_quantile(every, Q_HIGH), 3), len(every)]
    return out


def learn_band(archive: dict, clear_ghi: Callable[[float], float],
               clear_wh: Callable[[float, list[str]], float]) -> dict:
    """{"version", "daily": {class: [q10, q90, n]}, "hourly": {...}} of clearness shifts.

    clear_wh(hour_start, array ids): clear-sky output of these arrays in that hour (Wh), from the
    current learned model. Classes with too little data are left out.
    """
    daily: dict[str, list[float]] = {}
    hourly: dict[str, list[float]] = {}
    for day in archive.values():
        hours = day.get("hours", {})
        if not hours or any("actual" not in h for h in hours.values()):
            continue
        f_sum = a_sum = c_sum = ghi_sum = clear_sum = 0.0
        for key, h in hours.items():
            if h.get("curtailed"):
                continue
            ids = list(h.get("forecast", {}))
            f = sum(h.get("forecast", {}).values())
            a = sum(h["actual"].get(sid, 0.0) for sid in ids)
            cs = clear_ghi(float(key))
            if cs <= 0:
                continue
            c = clear_wh(float(key), ids)
            f_sum, a_sum, c_sum = f_sum + f, a_sum + a, c_sum + c
            ghi_sum, clear_sum = ghi_sum + (h.get("ghi") or 0.0), clear_sum + cs
            if c >= MIN_HOUR_WH:
                hourly.setdefault(weather_class((h.get("ghi") or 0.0) / cs), []).append((a - f) / c)
        if c_sum >= MIN_DAY_WH and clear_sum > 0:
            daily.setdefault(weather_class(ghi_sum / clear_sum), []).append((a_sum - f_sum) / c_sum)
    return {"version": VERSION, "daily": _summary(daily, MIN_DAYS), "hourly": _summary(hourly, MIN_HOURS)}


def valid(band: dict | None) -> dict | None:
    """The band if it has the current form; an older stored band is dropped until it is relearned."""
    return band if band and band.get("version") == VERSION else None


def shifts(band: dict | None, kind: str, clearness: float) -> tuple[float, float] | None:
    """(low, high) clearness shift for a forecast of this weather clearness; the class if known, else all."""
    table = (band or {}).get(kind) or {}
    row = table.get(weather_class(clearness)) or table.get("all")
    return (row[0], row[1]) if row else None


def _bounded(forecast_wh: float, clear_wh: float, shift: tuple[float, float]) -> tuple[float, float]:
    """Forecast with the clearness shifted, between no sun and a clear sky (plus the tolerance)."""
    top = max(clear_wh * CLEARNESS_MAX, forecast_wh)
    return tuple(min(max(forecast_wh + s * clear_wh, 0.0), top) for s in shift)


def day_band(band: dict | None, forecast_wh: float, clear_wh: float, clearness: float) -> tuple[float, float] | None:
    r = shifts(band, "daily", clearness)
    return _bounded(forecast_wh, clear_wh, r) if r else None


def hour_band(band: dict | None, hours: dict[float, float], clear: dict[float, float],
              clearness: dict[float, float]) -> dict[float, tuple[float, float]]:
    out = {}
    for t, wh in hours.items():
        r = shifts(band, "hourly", clearness.get(t, 0.0))
        if r:
            out[t] = _bounded(wh, clear.get(t, wh), r)
    return out
