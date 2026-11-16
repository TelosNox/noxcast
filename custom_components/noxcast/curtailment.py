"""Why an array produced less than the sun offered, per period, and the forecast cap of fixed limits (pure Python).

cuts() decides per array and period, from the modules outwards; the first reason found wins:

1. input: a sensor of the array reached its fixed limit (period maximum at least 95 % of it) -> fixed limit.
2. system: the arrays of a system without a battery reached its output limit together -> fixed limit.
3. battery: no battery that takes the array's PV took more (installation.blocked), and one of them was full ->
   possibly curtailed (a state); or they charged at their maximum power where the rest could not go out ->
   possibly at a ceiling, the hour delivered at least what was measured.

A fixed limit says the hour delivered at least so much; a state says the hour shows nothing about the sun.
Both are flagged in the archive. Learning still uses hours at a fixed limit: leaving them out selects the
hours on their measured value, and with a strong limit only clouded and shaded hours remain, which bends the
efficiency far more than the limit does (#248, experiment E1). Counting them as "at least this much" is the
planned fix. Periods are neutral: the rules work in W on one period, hourly or 5-minute alike.

The forecast is capped last, after the intraday correction and the band have varied the clouds: the capped
value of an hour is the mean over its sub-steps of min(power, limit), with the power following the clear-sky
shape of the hour scaled to the hour's value, so a ramp hour that crosses the limit loses only its part above.
A limit shared by several arrays (cap_group) caps their sum at every sub-step, each array with its own clear-sky
shape, and takes the part above off each array in proportion to its power at that step.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .const import Series
from .installation import Installation

LIMIT_MARGIN = 0.05              # a period counts as at the limit from 95 % of it: current limits drift with voltage


class Reason(StrEnum):
    INPUT_LIMIT = "input_limit"
    OUTPUT_LIMIT = "output_limit"
    BATTERY_FULL = "battery_full"
    CHARGE_LIMIT = "charge_limit"


class Level(StrEnum):
    INPUT = "input"
    SYSTEM = "system"
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
    soc: Mapping[str, float] = field(default_factory=dict)   # state of charge per battery sensor, %
    charging: Mapping[str, float] = field(default_factory=dict)   # highest charge power per battery power sensor, W


@dataclass(frozen=True)
class Cut:
    reason: Reason
    level: Level
    node: str                            # the sensor, the system or the battery's sensor
    fixed: bool                          # True: fixed limit (the hour delivered at least this); False: a state
    certain: bool                        # False: "possibly", a sensor needed for proof is missing


def at_limit(peak: float | None, limit: float | None) -> bool:
    return peak is not None and limit is not None and peak >= (1 - LIMIT_MARGIN) * limit


def cuts(inst: Installation, obs: Observed) -> dict[str, Cut]:
    """Array id -> the cut closest to the modules, for one period. Arrays not in the result are free."""
    out: dict[str, Cut] = {}
    for array in {i.array for i in inst.inputs.values()}:
        hit = next((i for i in inst.inputs_of(array)
                    if i.sensor in obs.pv and at_limit(obs.pv[i.sensor].peak, i.limit_w)), None)
        cap = next((c for c in inst.caps if array in c.arrays
                    and at_limit(_sum(inst, c.arrays, {s: v.peak for s, v in obs.pv.items()}), c.limit_w)), None)
        stores = inst.stores_of(array)
        full = [s for s in stores if obs.soc.get(s.sensor, -1.0) >= s.full_pct]
        charging = [s for s in stores if s.charger and s.charger.at_max(obs.charging.get(s.charger.sensor))]
        by_full, by_max, _ = inst.blocked(array, {s: {0.0} for s in full}, {s: {0.0} for s in charging})
        if hit is not None:
            out[array] = Cut(Reason.INPUT_LIMIT, Level.INPUT, hit.sensor, True, True)
        elif cap is not None:
            out[array] = Cut(Reason.OUTPUT_LIMIT, Level.SYSTEM, cap.system, True, True)
        elif by_full:
            out[array] = Cut(Reason.BATTERY_FULL, Level.HOUSE, full[0].sensor, False, False)
        elif by_max and charging[0].charger is not None:
            out[array] = Cut(Reason.CHARGE_LIMIT, Level.HOUSE, charging[0].charger.sensor, True, False)
    return out


def _sum(inst: Installation, arrays: tuple[str, ...], values: Mapping[str, float | None]) -> float | None:
    """The summed value of the arrays' sensors; None when a sensor has none. The sum of period maxima is at least
    the maximum of the sum: a period may count as at the limit although it was a little below, which only makes
    it a lower bound where it was exact."""
    out = 0.0
    for a in arrays:
        for i in inst.inputs_of(a):
            v = values.get(i.sensor)
            if v is None:
                return None
            out += v
    return out


def limited_hours(inst: Installation, array: str, peaks: Mapping[str, Mapping[float, float | None]]) -> set[float]:
    """Period starts in which a sensor of the array, or its system's arrays together, were at a fixed limit;
    peaks: sensor -> period maximum (W)."""
    out: set[float] = set()
    for i in inst.inputs_of(array):
        if i.limit_w is not None:
            out |= {t for t, v in peaks.get(i.sensor, {}).items() if at_limit(v, i.limit_w)}
    for c in inst.caps:
        if array in c.arrays:
            sensors = [i.sensor for a in c.arrays for i in inst.inputs_of(a)]
            for t in set().union(*(peaks.get(s, {}) for s in sensors)):
                if at_limit(_sum(inst, c.arrays, {s: peaks.get(s, {}).get(t) for s in sensors}), c.limit_w):
                    out.add(t)
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


def cap_group(hours: Mapping[str, float], profile: Callable[[str], list[float] | None], own: Mapping[str, float],
              shared: Iterable[tuple[tuple[str, ...], float]]) -> dict[str, float]:
    """One hour of several arrays under their fixed limits: own (array -> W) caps an array alone, shared
    (arrays, W) the sum of those arrays.

    hours: the hour's energy (Wh = mean W) per array; profile(array): its clear-sky power at the hour's sub-steps.
    Every array follows its own profile scaled to its value (as cap_hour), is capped by its own limit, and where
    a shared limit is exceeded at a sub-step, each of its arrays gives up the same share of its power there. An
    array without a usable profile counts with its hourly mean at every sub-step. Without a shared limit this is
    cap_hour for each array.
    """
    out = dict(hours)
    live = [a for a, wh in hours.items() if wh > 0]
    if not live:
        return out
    steps: dict[str, list[float] | None] = {}
    for a in live:
        p = profile(a)
        mean = sum(p) / len(p) if p else 0.0
        k = hours[a] / mean if mean > 0 else 0.0
        steps[a] = [k * x for x in p] if mean > 0 and p else None
    n = max((len(v) for v in steps.values() if v), default=1)
    power: dict[str, list[float]] = {}
    for a in live:
        v = steps[a]
        power[a] = list(v) if v is not None and len(v) == n else [hours[a]] * n
    changed: set[str] = set()
    for a in live:
        if a in own and max(power[a]) > own[a]:
            power[a] = [min(w, own[a]) for w in power[a]]
            changed.add(a)
    # the inner limits first (a subsystem's before its parent's): the outer one then takes only what is left
    # above it, while the other way round the inner one would cut arrays the outer one had already cut
    for members, limit in sorted(shared, key=lambda c: len(c[0])):
        group = [a for a in members if a in power]
        for j in range(n):
            total = sum(power[a][j] for a in group)
            if total > limit:
                for a in group:
                    power[a][j] *= limit / total
                changed.update(group)
    for a in changed:
        out[a] = sum(power[a]) / n
    return out


def cap(series: Series, limit: float, profile: Callable[[float], list[float] | None]) -> Series:
    """A series of hourly energies capped by cap_hour; profile(hour start) gives the hour's clear-sky sub-steps."""
    return {t: cap_hour(wh, limit, profile(t)) if wh > 0 else wh for t, wh in series.items()}
