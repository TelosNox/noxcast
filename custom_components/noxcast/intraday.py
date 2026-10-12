"""Intraday correction (pure Python): measured hours of today adjust the coming hours.

The ratio actual/forecast of the last completed hours is applied to the coming hours with a
decaying weight. Weather deviations persist for an hour or two, not for the rest of the day, so
the weather forecast takes over again further ahead.
"""
from __future__ import annotations

WINDOW_HOURS = 3
RECENT_WEIGHT = 0.6              # weight per hour further back in the window
HALF_LIFE_HOURS = 2.0            # decay of the correction into the coming hours
RATIO_RANGE = (0.3, 2.5)
MIN_FORECAST_SHARE = 0.03        # window forecast must reach this share of peak power (Wh) to be meaningful


def correction_ratio(forecast: dict[float, float], actual: dict[float, float], now: float,
                     peak_w: float) -> float | None:
    """Weighted actual/forecast of the last completed hours; None if they carry too little energy."""
    ended = sorted((t for t in actual if t + 3600 <= now and t in forecast), reverse=True)[:WINDOW_HOURS]
    if not ended:
        return None
    weights = [RECENT_WEIGHT ** i for i in range(len(ended))]
    f = sum(w * forecast[t] for w, t in zip(weights, ended))
    a = sum(w * actual[t] for w, t in zip(weights, ended))
    if f < MIN_FORECAST_SHARE * peak_w * sum(weights):
        return None
    return min(max(a / f, RATIO_RANGE[0]), RATIO_RANGE[1])


def expected_hours(forecast: dict[float, float], actual: dict[float, float], now: float,
                   ratio: float | None) -> dict[float, float]:
    """Past hours: actual (forecast where no measurement exists); current and coming hours: corrected forecast."""
    out = {}
    for t, wh in forecast.items():
        if t + 3600 <= now:
            out[t] = actual.get(t, wh)
        elif ratio is None:
            out[t] = wh
        else:
            ahead = max(t - now, 0.0) / 3600
            out[t] = wh * (1 + (ratio - 1) * 0.5 ** (ahead / HALF_LIFE_HOURS))
    return out
