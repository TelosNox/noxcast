"""Today's significant effects per array: what shade costs and reflected light adds (pure Python).

An effect is the difference between the forecast with and without the learned part, under today's
weather: on an overcast day a shadow costs almost nothing. Shade periods are always reported with
their cost; reflection only where it adds a relevant amount.
"""
from __future__ import annotations

from dataclasses import replace

from . import shade
from .model import ArrayGeometry, LearnedArray, WeatherHour, forecast
from .solar import Site
from .const import Series

EFFECT_MIN_WH = 50.0             # an effect is reported from this energy ...
EFFECT_MIN_SHARE = 0.1           # ... or from this share of the array's day, whichever is smaller
EFFECT_FLOOR_WH = 15.0           # never below this
GAIN_MIN_HOUR_WH = 10.0          # hours with less reflected light do not extend a reflection period


def hourly_effects(site: Site, geo: ArrayGeometry, learned: LearnedArray,
                   weather: list[WeatherHour]) -> tuple[Series, Series]:
    """(shade loss, reflection gain) in Wh per hour under the given weather."""
    base = forecast(site, geo, learned, weather)
    free = forecast(site, geo, replace(learned, cells={}, facade=None), weather)
    plain = forecast(site, geo, replace(learned, reflection={}), weather)
    loss = {t: max(free.get(t, 0.0) - v, 0.0) for t, v in base.items()}
    gain = {t: max(v - plain.get(t, 0.0), 0.0) for t, v in base.items()}
    return loss, gain


def _overlap_wh(series: Series, start: float, end: float) -> float:
    return sum(wh * max(0.0, min(end, t + 3600) - max(start, t)) / 3600 for t, wh in series.items())


def significant_periods(site: Site, geo: ArrayGeometry, learned: LearnedArray, day_start: float,
                        loss: Series, gain: Series, day_wh: float) -> list[dict]:
    """Shade and reflection periods of the day: {type, start, end, wh}.

    Shade periods are always reported (where the sun is behind an obstacle), with what they cost
    under today's weather; the band shows the cost. Reflection only where it adds a relevant amount.
    """
    threshold = max(EFFECT_FLOOR_WH, min(EFFECT_MIN_WH, EFFECT_MIN_SHARE * day_wh))
    out = []
    for p in shade.shade_periods(shade.day_profile(site, geo, learned, day_start)):
        out.append({"type": "shade", "start": p["start"], "end": p["end"],
                    "wh": round(_overlap_wh(loss, p["start"], p["end"]))})
    run: list[float] = []
    for t in sorted(gain) + [None]:
        if t is not None and gain[t] >= GAIN_MIN_HOUR_WH and (not run or t - run[-1] == 3600):
            run.append(t)
            continue
        if run:
            wh = sum(gain[h] for h in run)
            if wh >= threshold:
                out.append({"type": "reflection", "start": run[0], "end": run[-1] + 3600, "wh": round(wh)})
        run = [t] if t is not None and gain[t] >= GAIN_MIN_HOUR_WH else []
    return sorted(out, key=lambda p: p["start"])


def day_effects(site: Site, geo: ArrayGeometry, learned: LearnedArray | None, weather: list[WeatherHour],
                forecast_wh: Series, expected: Series, day_start: float,
                day_end: float, clear_weather: list[WeatherHour] | None = None,
                held: Series | None = None, cut: set[float] | frozenset[float] = frozenset()) -> dict:
    """Shade loss and reflection gain of the day, scaled like the expected output, with significant periods.

    One factor per day scales both: the expected output of the day over its forecast. The effects are computed
    under the forecast weather; the factor carries how much brighter or darker the day turned out. A factor per
    hour would read the deviation of a shaded hour as weather, although there it mostly says how well the
    shadow edge was learned: a shadow that lasted longer than learned shrank the loss, a little more diffuse
    light than forecast in deep shade inflated it, and the day card drew a hump instead of the shadow.
    Hours in cut (curtailed or at a power limit) stay out of the factor: there the measurement shows the
    limit, not the weather.

    The scaled loss is also bounded so that expected output plus loss, and the held forecast plus loss that the
    day card draws, never exceed the unshaded output under clear sky.
    """
    if learned is None:
        return {"loss": {}, "gain": {}, "periods": []}
    loss, gain = hourly_effects(site, geo, learned, [w for w in weather if day_start <= w.start < day_end])
    hours = [t for t in forecast_wh if day_start <= t < day_end and t not in cut]
    planned = sum(forecast_wh[t] for t in hours)
    factor = sum(expected.get(t, 0.0) for t in hours) / planned if planned > 0 else 1.0
    loss = {t: v * factor for t, v in loss.items()}
    gain = {t: v * factor for t, v in gain.items()}
    if clear_weather:
        free = forecast(site, geo, replace(learned, cells={}, facade=None),
                        [w for w in clear_weather if day_start <= w.start < day_end])
        held = held or {}
        loss = {t: min(v, max(free.get(t, 0.0) - max(expected.get(t, 0.0), held.get(t, 0.0)), 0.0))
                for t, v in loss.items()}
    periods = significant_periods(site, geo, learned, day_start, loss, gain, sum(expected.values()))
    return {"loss": loss, "gain": gain, "periods": periods}
