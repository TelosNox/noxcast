"""Power limits that nobody configured: a sensor whose power is flat at its maximum (pure Python, no Home Assistant).

An MPPT input or a micro-inverter that lets through less than the modules deliver cuts the power at a level: on
sunny days the power rises to it, stays flat while the sun would still raise or lower it, and falls again, and no
hour goes above it, not even when clouds reflect extra light onto the modules (cloud enhancement).

Flat alone proves nothing: around solar noon a clear sky changes the output of a flat or south-facing array by
less than 1 % within an hour. What unlimited modules cannot do is stay flat while the sun moves enough to change
their output: an hour counts as evidence only if the clear-sky output of the array's geometry would have changed
by at least MIN_CHANGE within it, while the measured power stayed within FLAT.

The detector only suggests; a limit changes nothing until the user confirms it (Repairs notice). That is why it
is strict: a missed limit costs some accuracy, a made-up one would cap the forecast for good.

A day is flat at a level when at the top of the day a run of flat hours (maxima agreeing within FLAT) holds at
least one such hour, the hours around it rise to it and fall from it, and no hour of the day goes above it. With
the 5-minute archive, an hour of consecutive 5-minute means within FLAT counts the same way, so a plateau that
does not fill a whole clock hour can count too. A sensor is limited when, within the last WINDOW_DAYS days, it
never delivered more than its peak and at least MIN_FLAT_DAYS days were flat close to that peak (DRIFT leaves
room for a limit that moves with the temperature, as a current limit does).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from .const import Series
from .model import ArrayGeometry, array_irradiance
from .solar import Site, clearsky_simplified_solis, extra_radiation, sun_position

HOUR = 3600
STEP = 300                     # the 5-minute archive (fine.py)
WINDOW_DAYS = 60
FLAT = 0.02                    # how much the power may vary while at a limit
MIN_CHANGE = 2 * FLAT          # how much a clear sky would have changed it meanwhile, for the flat period to count
DRIFT = 0.10                   # a flat day counts down to this far below the peak: a current limit moves with the cell temperature
MIN_FLAT_DAYS = 3
MIN_RUN_STEPS = 12             # an hour of the 5-minute archive
MIN_W = 20.0                   # below, a flat value is standby or a stuck sensor rather than a limit
DROPOUT = 0.5                  # an hourly minimum below this share of the maximum is a dropout of seconds, not light

# (start, end) -> how much the clear-sky output of the array changes within that period (1 - min / max), or None
# when the sun is not on the modules
Change = Callable[[float, float], float | None]


@dataclass(frozen=True)
class Plateau:
    """A detected limit of one sensor."""

    peak_w: float              # the most the sensor delivered within the window
    flat_days: int             # days flat close to that peak
    days: int                  # days with measurements within the window
    window_days: int = WINDOW_DAYS

    @property
    def limit_w(self) -> float:
        return suggested_limit(self.peak_w)

    def as_dict(self) -> dict:
        return {"peak_w": round(self.peak_w, 1), "limit_w": self.limit_w, "flat_days": self.flat_days,
                "days": self.days, "window_days": self.window_days}


def suggested_limit(peak_w: float) -> float:
    """A round value within 0.5 % of the peak, as a limit is written on a datasheet (501 W -> 500 W)."""
    for step in (100, 50, 25, 10, 5, 1):
        value = round(peak_w / step) * step
        if value > 0 and abs(value - peak_w) <= 0.005 * peak_w:
            return float(value)
    return float(round(peak_w))


def clear_change(site: Site, geo: ArrayGeometry, step: float = STEP) -> Change:
    """Change of the array's clear-sky irradiance within a period, from the sun's path alone (no shade, no weather)."""
    cache: dict[float, float] = {}

    def irradiance(t: float) -> float:
        if t not in cache:
            sun = sun_position(site, t)
            if sun.elevation <= 0:
                cache[t] = 0.0
            else:
                dni_extra = extra_radiation(t)
                ghi, dni, dhi = clearsky_simplified_solis(sun.elevation, site.pressure, dni_extra)
                beam, diffuse, _ = array_irradiance(geo, sun, dni, dhi, ghi, dni_extra)
                cache[t] = beam + diffuse
        return cache[t]

    def change(start: float, end: float) -> float | None:
        values = [irradiance(start + step / 2 + i * step) for i in range(max(1, round((end - start) / step)))]
        top = max(values)
        return 1 - min(values) / top if top > 0 else None

    return change


def _hour_level(mean: float | None, low: float | None, high: float | None) -> float | None:
    """The hour's maximum if the whole hour stayed within FLAT of it, else None.

    Some inverters report 0 W for a few seconds now and then: the minimum then says nothing, and the mean, which
    such a dropout hardly moves, must be within FLAT / 2 of the maximum instead.
    """
    if high is None or low is None or mean is None or high < MIN_W:
        return None
    if low >= (1 - FLAT) * high:
        return high
    if low < DROPOUT * high and mean >= (1 - FLAT / 2) * high:
        return high
    return None


def _moves(change: Change, start: float, end: float) -> bool:
    c = change(start, end)
    return c is not None and c >= MIN_CHANGE


def day_level_hours(hours: list[tuple[float, float | None, float | None, float | None]],
                    change: Change) -> float | None:
    """The level a day was flat at, from its hours (start, mean, min, max) in order; None if it was not.

    The flat run must be the top of the day (no hour above it), hold an hour in which the sun would have changed
    the power, and be framed by hours that rise to it and fall from it: a sensor stuck at one value, or a run cut
    off by missing data, does not count.
    """
    highs = [h for *_, h in hours if h is not None]
    if not highs:
        return None
    top = max(highs)
    best = None
    i = 0
    while i < len(hours):
        level = _hour_level(*hours[i][1:])
        if level is None or level < (1 - FLAT) * top:
            i += 1
            continue
        j = i + 1
        while (j < len(hours) and hours[j][0] - hours[j - 1][0] == HOUR
               and (nxt := _hour_level(*hours[j][1:])) is not None
               and abs(nxt - hours[j - 1][3]) <= FLAT * max(nxt, hours[j - 1][3])):
            j += 1
        run = hours[i:j]
        before = hours[i - 1] if i > 0 and hours[i][0] - hours[i - 1][0] == HOUR else None
        after = hours[j] if j < len(hours) and hours[j][0] - hours[j - 1][0] == HOUR else None
        level = max(h for *_, h in run)
        framed = all(x is not None and x[2] is not None and x[2] < (1 - FLAT) * level for x in (before, after))
        if (framed and top <= (1 + FLAT) * level
                and any(_moves(change, t, t + HOUR) for t, *_ in run)):
            best = max(best or 0.0, level)
        i = j
    return best


def day_level_steps(steps: list[tuple[float, float]], top: float, change: Change) -> float | None:
    """The level of a flat run of 5-minute means (start, W) at the top of the day; None without one.

    top: the most the sensor delivered that day (hourly maximum); a run below it by more than FLAT is no plateau.
    """
    best = None
    i = 0
    while i < len(steps):
        j = i + 1
        lo = hi = steps[i][1]
        while j < len(steps) and steps[j][0] - steps[j - 1][0] == STEP:
            lo2, hi2 = min(lo, steps[j][1]), max(hi, steps[j][1])
            if lo2 < (1 - FLAT) * hi2:
                break
            lo, hi = lo2, hi2
            j += 1
        if j - i >= MIN_RUN_STEPS and hi >= MIN_W and hi >= (1 - FLAT) * top:
            before = steps[i - 1][1] if i > 0 and steps[i][0] - steps[i - 1][0] == STEP else None
            after = steps[j][1] if j < len(steps) and steps[j][0] - steps[j - 1][0] == STEP else None
            if (before is not None and after is not None and max(before, after) < (1 - FLAT) * hi
                    and _moves(change, steps[i][0], steps[j - 1][0] + STEP)):
                best = max(best or 0.0, hi)
            i = j
        else:
            i += 1
    return best


def detect(mean: Series, low: Series, high: Series, now: float, day_of: Callable[[float], date], change: Change,
           fine: Series | None = None, window_days: int = WINDOW_DAYS) -> Plateau | None:
    """The limit of one sensor from its hourly mean, minimum and maximum (W), or None.

    change: the clear-sky change of the sensor's array (clear_change); fine: the sensor's 5-minute means (W)
    where kept. Only the last window_days days before now count.
    """
    start = now - window_days * 86400
    days: dict[date, list] = {}
    for t, h in high.items():
        if start <= t < now and h is not None:
            days.setdefault(day_of(t), []).append((t, mean.get(t), low.get(t), h))
    if not days:
        return None
    by_day_steps: dict[date, list[tuple[float, float]]] = {}
    for t, v in (fine or {}).items():
        if start <= t < now and v is not None:
            by_day_steps.setdefault(day_of(t), []).append((t, v))
    peak = max(h for hours in days.values() for *_, h in hours)
    if peak < MIN_W:
        return None
    flat = 0
    for d, hours in days.items():
        hours.sort()
        top = max(h for *_, h in hours)
        if top < (1 - DRIFT) * peak:          # a day that never came close cannot count; saves the sun's path
            continue
        level = day_level_hours(hours, change)
        if level is None and d in by_day_steps:
            level = day_level_steps(sorted(by_day_steps[d]), top, change)
        if level is not None and level >= (1 - DRIFT) * peak:
            flat += 1
    if flat < MIN_FLAT_DAYS:
        return None
    return Plateau(peak, flat, len(days), window_days)


def suggestion(sensors: list[str], found: Mapping[str, Plateau | dict]) -> dict | None:
    """One limit for every sensor of an array, as the array's setting holds it; None if there is none to suggest.

    Only when every sensor is limited, at levels that agree within 3 %: a limit on some sensors only does not
    cap the array's sum, and different limits per sensor cannot be entered in the array's settings.
    """
    items = [found.get(s) for s in sensors]
    if not sensors or any(x is None for x in items):
        return None
    rows = [x.as_dict() if isinstance(x, Plateau) else x for x in items]
    peaks = [float(x["peak_w"]) for x in rows]
    if max(peaks) > 1.03 * min(peaks):
        return None
    peak = max(peaks)
    return {"peak_w": round(peak, 1), "limit_w": suggested_limit(peak),
            "flat_days": min(int(x["flat_days"]) for x in rows), "days": max(int(x["days"]) for x in rows),
            "window_days": max(int(x["window_days"]) for x in rows)}


def dismissed(sensors: list[str], suggested: dict, rejected: Mapping[str, float]) -> bool:
    """The user said there is no limit at about this level: no notice until the level moves by more than 5 %."""
    level = float(suggested["peak_w"])
    return bool(sensors) and all(s in rejected and abs(level - rejected[s]) <= 0.05 * rejected[s] for s in sensors)
