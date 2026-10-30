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
                day_end: float) -> dict:
    """Shade loss and reflection gain of the day, scaled like the expected output, with significant periods."""
    if learned is None:
        return {"loss": {}, "gain": {}, "periods": []}
    loss, gain = hourly_effects(site, geo, learned, [w for w in weather if day_start <= w.start < day_end])

    def scaled(series: Series) -> Series:
        return {t: v * (expected.get(t, 0.0) / forecast_wh[t] if forecast_wh.get(t) else 1.0) for t, v in series.items()}

    loss, gain = scaled(loss), scaled(gain)
    periods = significant_periods(site, geo, learned, day_start, loss, gain, sum(expected.values()))
    return {"loss": loss, "gain": gain, "periods": periods}
