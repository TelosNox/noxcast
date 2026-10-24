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

EFFECT_MIN_WH = 50.0             # an effect is reported from this energy ...
EFFECT_MIN_SHARE = 0.1           # ... or from this share of the array's day, whichever is smaller
EFFECT_FLOOR_WH = 15.0           # never below this
GAIN_MIN_HOUR_WH = 10.0          # hours with less reflected light do not extend a reflection period


def hourly_effects(site: Site, geo: ArrayGeometry, learned: LearnedArray,
                   weather: list[WeatherHour]) -> tuple[dict[float, float], dict[float, float]]:
    """(shade loss, reflection gain) in Wh per hour under the given weather."""
    base = forecast(site, geo, learned, weather)
    free = forecast(site, geo, replace(learned, cells={}), weather)
    plain = forecast(site, geo, replace(learned, reflection={}), weather)
    loss = {t: max(free.get(t, 0.0) - v, 0.0) for t, v in base.items()}
    gain = {t: max(v - plain.get(t, 0.0), 0.0) for t, v in base.items()}
    return loss, gain


def clear_shares(site: Site, geo: ArrayGeometry, learned: LearnedArray,
                 clear_weather: list[WeatherHour]) -> tuple[dict[float, float], dict[float, float]]:
    """(shade share, reflection share) per hour under a clear sky: what the geometry allows.

    Shade: the share of the unshaded output the obstacles take; reflection: the share it adds to the
    output without reflection. Independent of today's weather, so the band can show both what shade
    could cost and what it costs today; under clouds the cost stays well below this.
    """
    base = forecast(site, geo, learned, clear_weather)
    free = forecast(site, geo, replace(learned, cells={}), clear_weather)
    plain = forecast(site, geo, replace(learned, reflection={}), clear_weather)
    shade_share = {t: min(1.0, max(0.0, (free[t] - base.get(t, 0.0)) / free[t])) for t in free if free[t] > 1.0}
    refl_share = {t: min(1.0, max(0.0, (base.get(t, 0.0) - plain[t]) / plain[t])) for t in plain if plain[t] > 1.0}
    return shade_share, refl_share


def _overlap_wh(series: dict[float, float], start: float, end: float) -> float:
    return sum(wh * max(0.0, min(end, t + 3600) - max(start, t)) / 3600 for t, wh in series.items())


def significant_periods(site: Site, geo: ArrayGeometry, learned: LearnedArray, day_start: float,
                        loss: dict[float, float], gain: dict[float, float], day_wh: float) -> list[dict]:
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
