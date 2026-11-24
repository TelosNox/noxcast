"""How the forecast of an hour comes about, step by step (the waterfall of the day view).

Pure functions, no Home Assistant imports. The same model runs with the learned effects switched on
one after the other; each step is the difference to the previous run:

  clear sky (with the learned efficiency) → clouds (the weather source's irradiance) → shade →
  reflection → direction factor → heat (temperature) = forecast

The order is fixed, so the steps always add up to the forecast. Shade before reflection, since
reflection is learned where the module itself gets little direct light.

The steps of a forecast that is kept (the morning fix, the day-ahead archive) are stored with it
(packed()), so the view explains that very forecast, not a replay with newer weather or a newer model.
"""
from __future__ import annotations

from .archive import Day, Hour
from .model import ArrayGeometry, LearnedArray, WeatherHour, clearsky_weather, forecast
from .solar import Site

STEPS = ("clear", "clouds", "shade", "reflection", "direction", "heat")
LIMIT_TOLERANCE_WH = 1.0      # below the rounding of the steps, far below any real cap


def hour_steps(site: Site, geo: ArrayGeometry, learned: LearnedArray, weather: list[WeatherHour]) -> dict[float, dict]:
    """Per hour start: Wh of the clear-sky start and of each step, plus the resulting forecast."""
    if not weather:
        return {}
    bare = LearnedArray(**{**learned.__dict__, "cells": {}, "facade": None, "reflection": {}, "direction": {}, "temp_coeff": 0.0})
    shaded = LearnedArray(**{**bare.__dict__, "cells": learned.cells, "facade": learned.facade})
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


def packed(step: dict) -> list[float]:
    """The steps of an hour as stored: the values in STEPS order (the forecast is their sum)."""
    return [step[k] for k in STEPS]


def unpacked(values: list[float]) -> dict:
    """The steps of an hour from their stored form, with the forecast as "result"."""
    step = dict(zip(STEPS, values, strict=True))
    step["result"] = round(sum(values), 1)
    return step


def with_limit(steps: dict[float, dict], capped: dict[float, float]) -> dict[float, dict]:
    """The steps with a last step "limit": the published forecast of a limited array is capped after the steps
    (forecasting._cap), so without it the steps would end above the value the view shows.

    capped: per hour start the forecast in Wh as published. An hour whose value is not clearly below its result (the
    steps are rounded) keeps its steps; else the step is the (negative) difference, so the steps still add up.
    """
    out = {}
    for t, step in steps.items():
        c = capped.get(t)
        if c is not None and c < step["result"] - LIMIT_TOLERANCE_WH:
            step = {**step, "limit": round(c - step["result"], 1), "result": round(c, 1)}
        out[t] = step
    return out


def archived_weather(day: Day | None) -> list[WeatherHour]:
    """The weather a day-ahead forecast was made from (irradiance and temperature), by hour."""
    return [WeatherHour(h.ts, h.ghi or 0.0, None, None, h.temp) for h in day.hours] if day else []


def replay(site: Site, geometries: dict[str, ArrayGeometry], learned: dict[str, LearnedArray],
           weather: list[WeatherHour]) -> dict[str, dict[float, dict]]:
    """Per array the steps of each hour (hour_steps) under the given weather; "result" is the forecast in Wh.

    With archived_weather() this replays a past day with the current learned model, as the day view
    does and a backtest can. An array without learned state runs with the defaults.
    """
    return {sid: hour_steps(site, geo, learned.get(sid) or LearnedArray(), weather) for sid, geo in geometries.items()}


def clearness(site: Site, weather: list[WeatherHour]) -> dict[float, tuple[float, float]]:
    """Per hour: (GHI of the weather source, clear-sky GHI), for the weather strip."""
    out = {}
    for w in weather:
        cs = clearsky_weather(site, w.start).ghi
        if cs > 0:
            out[w.start] = (w.ghi, cs)
    return out


# ---------------------------------------------------------------- why a past day deviated

DEVIATION = ("weather", "shade", "limit", "model", "open")
SHADE_SHARE = 0.1             # an hour is behind learned shade when the shade step takes 10 % of the light after clouds


def deviation(hours: list[Hour], sid: str, base: dict[float, float]) -> dict[float, dict]:
    """Per hour start: the deviation of the actual value from the forecast, split into parts that add up to it.

    base: the forecast of each hour as the view shows it (the result of its steps; an hour without steps has none), so
    that forecast plus all parts is exactly the actual value.
    The model on measured weather (satellite irradiance, archived as "measured" with the parameters learned then and
    capped like the forecast) separates the sky from the rest:
      weather = model on measured weather − forecast        (the sky was clearer or duller than forecast)
      limit   = actual − model on measured weather          in hours that were curtailed or at a fixed limit
      shade   = the same                                    in hours behind learned shade (SHADE_SHARE)
      model   = the same                                    in the other hours
      open    = actual − forecast                           without satellite irradiance, or without an actual value
    Shade is not a separate effect of the model on measured weather; it names the hours in which the learned shading
    map shaped the forecast, so a miss there points at the map rather than at efficiency or sky view.
    """
    out: dict[float, dict] = {}
    for h in hours:
        actual = (h.actual or {}).get(sid)
        f = base.get(h.ts, 0.0)
        if actual is None:
            if f:
                out[h.ts] = {"open": round(-f, 1)}
            continue
        m = (h.measured or {}).get(sid)
        if not f and not actual and not m:
            continue                                            # night: nothing to explain
        if m is None:
            out[h.ts] = {"open": round(actual - f, 1)}
            continue
        rest = actual - m
        steps = h.steps.get(sid) or ()
        light = steps[0] + steps[1] if len(steps) == len(STEPS) else 0.0
        if h.curtailed or h.limited:
            kind = "limit"
        elif light > 0 and -steps[2] >= SHADE_SHARE * light:
            kind = "shade"
        else:
            kind = "model"
        out[h.ts] = {"weather": round(m - f, 1), kind: round(rest, 1)}
    return out
