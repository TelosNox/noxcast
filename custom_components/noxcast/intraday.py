"""Intraday correction (pure Python): measured hours of today adjust the coming hours.

The correction works on clearness, not on output: clearness = output / clear-sky output of the
same hour (with learned efficiency, shading and temperature). If the last hours were clearer than
forecast, the coming hours get correspondingly fewer clouds, but never more than a clear sky.
An hour already forecast as sunny therefore stays as it is, while a cloudy one is raised.

The shift decays into the coming hours: weather deviations persist for an hour or two, and the
weather forecast itself is updated during the day.

The running hour enters as a partial observation once part of it is measured (5-minute statistics):
forecast and clear sky are scaled to the measured part with the clear-sky profile of the hour, and
its weight is the measured share, so a change in the weather shows within half an hour.
"""
from __future__ import annotations

from dataclasses import dataclass

WINDOW_HOURS = 3
RECENT_WEIGHT = 0.6              # weight per hour further back in the window
STRENGTH = 0.5                   # share of the measured clearness shift applied to the current hour
HALF_LIFE_HOURS = 1.0            # decay of the shift into the coming hours
CLEARNESS_MAX = 1.1              # slightly above clear sky (cloud enhancement, model tolerance)
MIN_CLEAR_SHARE = 0.03           # window clear-sky energy must reach this share of peak power to be meaningful


@dataclass
class Partial:
    """Measured part of the running hour."""
    start: float                 # hour start
    power: float                 # mean power over the measured part (W)
    share: float                 # measured share of the hour, 0..1
    profile: float               # clear-sky power over the measured part / over the whole hour


def clearness_shift(forecast: dict[float, float], actual: dict[float, float], clear: dict[float, float],
                    now: float, peak_w: float, partial: Partial | None = None) -> float | None:
    """Measured minus forecast clearness of the last hours, energy weighted; None if too dark.

    Observations are (actual, forecast, clear sky) of completed hours, newest first, optionally
    preceded by the measured part of the running hour.
    """
    ended = sorted((t for t in actual if t + 3600 <= now and t in forecast and t in clear),
                   reverse=True)[:WINDOW_HOURS]
    obs = [(actual[t], forecast[t], clear[t], 1.0) for t in ended]
    if partial is not None and partial.share > 0 and partial.start in forecast and partial.start in clear:
        k = partial.profile
        obs.insert(0, (partial.power, forecast[partial.start] * k, clear[partial.start] * k, partial.share))
        obs = obs[:WINDOW_HOURS + 1]
    if not obs:
        return None
    weights = [RECENT_WEIGHT ** i * share for i, (_, _, _, share) in enumerate(obs)]
    clear_sum = sum(w * c for w, (_, _, c, _) in zip(weights, obs))
    if clear_sum < MIN_CLEAR_SHARE * peak_w * sum(weights):
        return None
    deviation = sum(w * (a - f) for w, (a, f, _, _) in zip(weights, obs))
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
