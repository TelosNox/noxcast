"""How the forecast of an hour comes about, step by step (the waterfall of the day view).

Pure functions, no Home Assistant imports. The same model runs with the learned effects switched on
one after the other; each step is the difference to the previous run:

  clear sky (with the learned efficiency) → clouds (the weather source's irradiance) → shade →
  reflection → direction factor → heat (temperature) = forecast

The order is fixed, so the steps always add up to the forecast. Shade before reflection, since
reflection is learned where the module itself gets little direct light.
"""
from __future__ import annotations

from .model import ArrayGeometry, LearnedArray, WeatherHour, clearsky_weather, forecast
from .solar import Site

STEPS = ("clear", "clouds", "shade", "reflection", "direction", "heat")


def hour_steps(site: Site, geo: ArrayGeometry, learned: LearnedArray, weather: list[WeatherHour]) -> dict[float, dict]:
    """Per hour start: Wh of the clear-sky start and of each step, plus the resulting forecast."""
    if not weather:
        return {}
    bare = LearnedArray(**{**learned.__dict__, "cells": {}, "contour": {}, "reflection": {}, "direction": {},
                           "temp_coeff": 0.0})
    shaded = LearnedArray(**{**bare.__dict__, "cells": learned.cells, "contour": learned.contour})
    reflected = LearnedArray(**{**shaded.__dict__, "reflection": learned.reflection})
    directed = LearnedArray(**{**reflected.__dict__, "direction": learned.direction})
    runs = [
        forecast(site, geo, bare, [clearsky_weather(site, w.start, w.temp) for w in weather]),
        forecast(site, geo, bare, weather),
        forecast(site, geo, shaded, weather),
        forecast(site, geo, reflected, weather),
        forecast(site, geo, directed, weather),
        forecast(site, geo, learned, weather),
    ]
    out = {}
    for w in weather:
        values = [r.get(w.start, 0.0) for r in runs]
        if values[0] <= 0 and values[-1] <= 0:
            continue
        step = {"clear": round(values[0], 1)}
        for i, name in enumerate(STEPS[1:], start=1):
            step[name] = round(values[i] - values[i - 1], 1)
        step["result"] = round(values[-1], 1)
        out[w.start] = step
    return out


def clearness(site: Site, weather: list[WeatherHour]) -> dict[float, tuple[float, float]]:
    """Per hour: (GHI of the weather source, clear-sky GHI), for the weather strip."""
    out = {}
    for w in weather:
        cs = clearsky_weather(site, w.start).ghi
        if cs > 0:
            out[w.start] = (w.ghi, cs)
    return out
