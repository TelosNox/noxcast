"""Uncertainty band of the PV forecast, learned from the day-ahead archive.

Pure functions, no Home Assistant imports. The band is empirical: for past days (and hours) the
ratio actual / forecast is collected per weather class of the forecast (clearness = forecast GHI
relative to clear-sky GHI). The 10th and 90th percentile of that ratio, applied to a new forecast of
the same class, give P10 and P90. A clear forecast is rarely far off, an overcast one often by half;
the classes keep that apart. Hours with possible curtailment are left out, since their actual value
does not show what the sun gave.
"""
from __future__ import annotations

from collections.abc import Callable

CLASSES = ((0.35, "overcast"), (0.7, "mixed"), (float("inf"), "clear"))
Q_LOW, Q_HIGH = 0.1, 0.9
MIN_DAYS = 8                 # days a class needs; otherwise all days together
MIN_HOURS = 40               # hours a class needs; otherwise all hours together
MIN_DAY_WH = 200.0           # smaller day forecasts give no usable ratio
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


def learn_band(archive: dict, clear_ghi: Callable[[float], float]) -> dict:
    """{"daily": {class: [q10, q90, n]}, "hourly": {...}}; classes with too little data are left out."""
    daily: dict[str, list[float]] = {}
    hourly: dict[str, list[float]] = {}
    for day in archive.values():
        hours = day.get("hours", {})
        if not hours or any("actual" not in h for h in hours.values()):
            continue
        f_sum = a_sum = ghi_sum = clear_sum = 0.0
        for key, h in hours.items():
            if h.get("curtailed"):
                continue
            f = sum(h.get("forecast", {}).values())
            a = sum(h["actual"].get(sid, 0.0) for sid in h.get("forecast", {}))
            cs = clear_ghi(float(key))
            f_sum, a_sum = f_sum + f, a_sum + a
            if cs > 0:
                ghi_sum, clear_sum = ghi_sum + (h.get("ghi") or 0.0), clear_sum + cs
                if f >= MIN_HOUR_WH:
                    hourly.setdefault(weather_class((h.get("ghi") or 0.0) / cs), []).append(a / f)
        if f_sum >= MIN_DAY_WH and clear_sum > 0:
            daily.setdefault(weather_class(ghi_sum / clear_sum), []).append(a_sum / f_sum)
    return {"daily": _summary(daily, MIN_DAYS), "hourly": _summary(hourly, MIN_HOURS)}


def ratios(band: dict | None, kind: str, clearness: float) -> tuple[float, float] | None:
    """(low, high) factor for a forecast of this clearness; the class if known, else all together."""
    table = (band or {}).get(kind) or {}
    row = table.get(weather_class(clearness)) or table.get("all")
    return (row[0], row[1]) if row else None


def day_band(band: dict | None, forecast_wh: float, clearness: float) -> tuple[float, float] | None:
    r = ratios(band, "daily", clearness)
    return (forecast_wh * r[0], forecast_wh * r[1]) if r else None


def hour_band(band: dict | None, hours: dict[float, float], clearness: dict[float, float]) -> dict[float, tuple[float, float]]:
    out = {}
    for t, wh in hours.items():
        r = ratios(band, "hourly", clearness.get(t, 0.0))
        if r:
            out[t] = (wh * r[0], wh * r[1])
    return out
