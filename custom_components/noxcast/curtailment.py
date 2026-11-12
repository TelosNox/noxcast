"""Why an array produced less than the sun offered, per period, and the forecast cap of fixed limits (pure Python).

cuts() decides per array and period, from the modules outwards; the first reason found wins:

1. input: a sensor of the array reached its fixed limit (period maximum at least 95 % of it) -> fixed limit.
2. house: the highest state of charge of the batteries reached "full" -> possibly curtailed (a state).

A fixed limit says the hour delivered at least so much; a state says the hour shows nothing about the sun.
Both are flagged in the archive. Learning still uses hours at a fixed limit: leaving them out selects the
hours on their measured value, and with a strong limit only clouded and shaded hours remain, which bends the
efficiency far more than the limit does (#248, experiment E1). Counting them as "at least this much" is the
planned fix. Periods are neutral: the rules work in W on one period, hourly or 5-minute alike.

The forecast is capped last, after the intraday correction and the band have varied the clouds: the capped
value of an hour is the mean over its sub-steps of min(power, limit), with the power following the clear-sky
shape of the hour scaled to the hour's value, so a ramp hour that crosses the limit loses only its part above.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .const import Series
from .installation import Installation

LIMIT_MARGIN = 0.05              # a period counts as at the limit from 95 % of it: current limits drift with voltage


class Reason(StrEnum):
    INPUT_LIMIT = "input_limit"
    BATTERY_FULL = "battery_full"


class Level(StrEnum):
    INPUT = "input"
    HOUSE = "house"


@dataclass(frozen=True)
class Stat:
    """A sensor's power in one period, W; high is the period maximum (5-minute periods have means only)."""

    mean: float | None
    high: float | None = None

    @property
    def peak(self) -> float | None:
        return self.high if self.high is not None else self.mean


@dataclass(frozen=True)
class Observed:
    """One period."""

    pv: Mapping[str, Stat]               # per PV sensor
    house_soc: float | None = None       # highest state of charge of the house's batteries, %


@dataclass(frozen=True)
class Cut:
    reason: Reason
    level: Level
    node: str                            # the sensor or "house"
    fixed: bool                          # True: fixed limit (the hour delivered at least this); False: a state
    certain: bool                        # False: "possibly", a sensor needed for proof is missing


def at_limit(peak: float | None, limit: float | None) -> bool:
    return peak is not None and limit is not None and peak >= (1 - LIMIT_MARGIN) * limit


def cuts(inst: Installation, obs: Observed) -> dict[str, Cut]:
    """Array id -> the cut closest to the modules, for one period. Arrays not in the result are free."""
    out: dict[str, Cut] = {}
    full = obs.house_soc is not None and bool(inst.house.soc_sensors) and obs.house_soc >= inst.house.soc_full_pct
    for array in {i.array for i in inst.inputs.values()}:
        hit = next((i for i in inst.inputs_of(array)
                    if i.sensor in obs.pv and at_limit(obs.pv[i.sensor].peak, i.limit_w)), None)
        if hit is not None:
            out[array] = Cut(Reason.INPUT_LIMIT, Level.INPUT, hit.sensor, True, True)
        elif full:
            out[array] = Cut(Reason.BATTERY_FULL, Level.HOUSE, "house", False, False)
    return out


def limited_hours(inst: Installation, array: str, peaks: Mapping[str, Mapping[float, float | None]]) -> set[float]:
    """Period starts in which a sensor of the array was at its fixed limit; peaks: sensor -> period maximum (W)."""
    out: set[float] = set()
    for i in inst.inputs_of(array):
        if i.limit_w is not None:
            out |= {t for t, v in peaks.get(i.sensor, {}).items() if at_limit(v, i.limit_w)}
    return out


def cap_hour(wh: float, limit: float, profile: list[float] | None) -> float:
    """An hour's energy (Wh = mean W) under a fixed limit; profile: clear-sky power at the hour's sub-steps.

    The sub-steps follow the profile scaled to the hour's value. Without a usable profile the hourly mean is
    capped as a whole.
    """
    if wh <= 0:
        return wh
    mean = sum(profile) / len(profile) if profile else 0.0
    if mean <= 0:
        return min(wh, limit)
    k = wh / mean
    if k * max(profile) <= limit:
        return wh
    return sum(min(k * p, limit) for p in profile) / len(profile)


def cap(series: Series, limit: float, profile: Callable[[float], list[float] | None]) -> Series:
    """A series of hourly energies capped by cap_hour; profile(hour start) gives the hour's clear-sky sub-steps."""
    return {t: cap_hour(wh, limit, profile(t)) if wh > 0 else wh for t, wh in series.items()}
