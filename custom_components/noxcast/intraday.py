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

An hour in which the PV was possibly curtailed (full battery) measured less than the sun offered: it says
the sky was at least that clear, not how clear. Such an hour can therefore raise the shift but never lower
it; the window does not reach further back for it, since the sky an hour earlier says less about the rest
of the day. With nothing left to compare there is no correction.
"""
from __future__ import annotations

from dataclasses import dataclass

from .const import Series

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


def clearness_shift(forecast: Series, actual: Series, clear: Series, now: float, peak_w: float,
                    partial: Partial | None = None, curtailed: set[float] | frozenset[float] = frozenset()
                    ) -> float | None:
    """Measured minus forecast clearness of the last hours, energy weighted; None if too dark.

    Observations are (actual, forecast, clear sky) of completed hours, newest first, optionally
    preceded by the measured part of the running hour. curtailed: hour starts in which the PV was
    possibly curtailed; such an hour only counts where it raises the shift (see the module text).
    """
    ended = sorted((t for t in actual if t + 3600 <= now and t in forecast and t in clear),
                   reverse=True)[:WINDOW_HOURS]
    obs = [(actual[t], forecast[t], clear[t], 1.0, t in curtailed) for t in ended]
    if partial is not None and partial.share > 0 and partial.start in forecast and partial.start in clear:
        k = partial.profile
        obs.insert(0, (partial.power, forecast[partial.start] * k, clear[partial.start] * k, partial.share,
                       partial.start in curtailed))
        obs = obs[:WINDOW_HOURS + 1]
    # the weight belongs to the position in the window, so leaving out an hour does not move the others up
    weighted = [(RECENT_WEIGHT ** i * share, a, f, c, cut) for i, (a, f, c, share, cut) in enumerate(obs)]
    free = [o for o in weighted if not o[4]]
    # a curtailed hour that was clearer than forecast is a lower bound of the sky: it may only raise the shift
    raised = free + [o for o in weighted if o[4] and o[1] >= o[2]]
    shifts = [s for s in (_shift(free, peak_w), _shift(raised, peak_w)) if s is not None]
    return max(shifts) if shifts else None


def _shift(obs: list[tuple[float, float, float, float, bool]], peak_w: float) -> float | None:
    """Energy-weighted clearness difference of (weight, actual, forecast, clear sky, curtailed); None if too dark."""
    weight = sum(w for w, *_ in obs)
    clear_sum = sum(w * c for w, _, _, c, _ in obs)
    if not obs or clear_sum < MIN_CLEAR_SHARE * peak_w * weight:
        return None
    deviation = sum(w * (a - f) for w, a, f, _, _ in obs)
    return max(min(deviation / clear_sum, CLEARNESS_MAX), -CLEARNESS_MAX)


def expected_hours(forecast: Series, actual: Series, clear: Series,
                   now: float, shift: float | None) -> Series:
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


def held_forecast(previous: Series, current: Series, now: float,
                  day_start: float) -> Series:
    """Today's forecast per hour, held once the hour has begun.

    Hours still ahead follow the latest forecast; an hour that has begun keeps the value it had
    before, so the deviation of the actual value stays visible instead of being replaced by it.
    """
    held = {t: v for t, v in previous.items() if t >= day_start}
    for t, v in current.items():
        if t > now or t not in held:
            held[t] = v
    return dict(sorted(held.items()))
