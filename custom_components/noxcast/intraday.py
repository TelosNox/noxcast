"""Intraday correction (pure Python): measured hours of today adjust the coming hours.

The correction works on clearness, not on output: clearness = output / clear-sky output of the
same hour (with learned efficiency, shading and temperature). If the last hours were clearer than
forecast, the coming hours get correspondingly fewer clouds, but never more than a clear sky.
An hour already forecast as sunny therefore stays as it is, while a cloudy one is raised.

The shift decays into the coming hours: weather deviations persist for an hour or two, and the
weather forecast itself is updated during the day.
"""
from __future__ import annotations

WINDOW_HOURS = 3
RECENT_WEIGHT = 0.6              # weight per hour further back in the window
STRENGTH = 0.5                   # share of the measured clearness shift applied to the current hour
HALF_LIFE_HOURS = 1.0            # decay of the shift into the coming hours
CLEARNESS_MAX = 1.1              # slightly above clear sky (cloud enhancement, model tolerance)
MIN_CLEAR_SHARE = 0.03           # window clear-sky energy must reach this share of peak power to be meaningful


def clearness_shift(forecast: dict[float, float], actual: dict[float, float], clear: dict[float, float],
                    now: float, peak_w: float) -> float | None:
    """Measured minus forecast clearness of the last completed hours, energy weighted; None if too dark."""
    ended = sorted((t for t in actual if t + 3600 <= now and t in forecast and t in clear),
                   reverse=True)[:WINDOW_HOURS]
    if not ended:
        return None
    weights = [RECENT_WEIGHT ** i for i in range(len(ended))]
    clear_sum = sum(w * clear[t] for w, t in zip(weights, ended))
    if clear_sum < MIN_CLEAR_SHARE * peak_w * sum(weights):
        return None
    deviation = sum(w * (actual[t] - forecast[t]) for w, t in zip(weights, ended))
    return max(min(deviation / clear_sum, CLEARNESS_MAX), -CLEARNESS_MAX)


def expected_hours(forecast: dict[float, float], actual: dict[float, float], clear: dict[float, float],
                   now: float, shift: float | None) -> dict[float, float]:
    """Past hours: actual (forecast where no measurement exists); current and coming hours: forecast with
    the clearness shifted towards the measurement, limited to [0, clear sky]."""
    out = {}
    for t, wh in forecast.items():
        if t + 3600 <= now:
            out[t] = actual.get(t, wh)
            continue
        c = clear.get(t, 0.0)
        if shift is None or c <= 0:
            out[t] = wh
            continue
        ahead = max(t - now, 0.0) / 3600
        k = wh / c + shift * STRENGTH * 0.5 ** (ahead / HALF_LIFE_HOURS)
        out[t] = c * min(max(k, 0.0), max(CLEARNESS_MAX, wh / c))
    return out
