"""What lies between the modules and the house: the installation as configured (pure Python, no Home Assistant).

Power flows from the inputs (PV power sensors) through the systems (systems.py) to the house, and every level can
hold a limit. The chain of ceilings (docs/concept.md, Installation model) decides what each one means:

- A limit is fixed when no battery sits below it: the PV can never exceed it, so it caps the forecast and the
  hours at it are lower bounds in learning. That holds for an input (MPPT, micro-inverter) and for the output limit
  of a system without a battery, alone or shared by its subsystems.
- A battery's full state is a state, no cap: the hours it may have cut are left out of learning instead. Which
  batteries count for an array follows from where they sit (stores_of).
- A battery charging at its maximum power takes no more either, where the rest cannot go out
  (systems.charge_curtails): a ceiling of output plus charge power. The hour delivered at least what was measured,
  so it is a lower bound in learning like a fixed limit (charge_hours). Leaving it out instead would select on the
  measured PV itself, dropping the bright hours and bending the learned efficiency down (#248, E1).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import systems as sy
from .const import (
    CONF_BATTERY_INVERT, CONF_BATTERY_POWER, CONF_LIMIT_PER_PHASE, CONF_MAX_CHARGE, CONF_MAX_SOC, CONF_OUTPUT_LIMIT,
    CONF_SOC_SENSOR,
)

FULL_TOLERANCE = 2.0             # percent points below its upper limit from which a battery counts as full
CHARGE_MARGIN = 0.05             # a battery charges at its maximum from 95 % of it (hourly peak)


@dataclass(frozen=True)
class Input:
    """One PV power sensor, part of an array (the learning unit)."""

    sensor: str
    array: str                           # array subentry id
    limit_w: float | None = None         # fixed limit in W (MPPT, micro-inverter), None = none known


@dataclass(frozen=True)
class Charger:
    """A battery's power sensor (> 0 = charging, unless inverted) and its maximum charge power in W."""

    sensor: str
    invert: bool
    max_w: float

    def at_max(self, charging_w: float | None) -> bool:
        return charging_w is not None and charging_w >= (1 - CHARGE_MARGIN) * self.max_w


@dataclass(frozen=True)
class Store:
    """A battery whose full state may curtail PV: its state-of-charge sensor and from what % it counts as full;
    charger: set where charging at its maximum power curtails PV too and the sensor and maximum are known."""

    sensor: str
    full_pct: float
    charger: Charger | None = None


@dataclass(frozen=True)
class Cap:
    """A fixed limit on the summed output of arrays: a system with an output limit and no battery below it."""

    system: str
    arrays: tuple[str, ...]
    limit_w: float


@dataclass(frozen=True)
class House:
    soc_sensors: tuple[str, ...] = ()    # batteries whose full state may curtail every array (the old setting)
    soc_full_pct: float = 95.0


@dataclass(frozen=True)
class Installation:
    house: House = field(default_factory=House)
    inputs: Mapping[str, Input] = field(default_factory=dict)                # by sensor
    stores: Mapping[str, tuple[Store, ...]] = field(default_factory=dict)    # array -> batteries of its systems
    caps: tuple[Cap, ...] = ()

    def inputs_of(self, array: str) -> list[Input]:
        return [i for i in self.inputs.values() if i.array == array]

    def stores_of(self, array: str) -> tuple[Store, ...]:
        """The batteries whose full state may curtail the array: the old household setting and its systems'."""
        old = tuple(Store(s, self.house.soc_full_pct) for s in self.house.soc_sensors)
        return tuple(dict.fromkeys(old + tuple(self.stores.get(array, ()))))

    def soc_sensors(self) -> list[str]:
        """Every state-of-charge sensor that may curtail some array."""
        return sorted({s.sensor for a in {i.array for i in self.inputs.values()} for s in self.stores_of(a)})

    def chargers(self) -> list[Charger]:
        """Every battery power sensor whose maximum may curtail some array."""
        return sorted({st.charger for group in self.stores.values() for st in group if st.charger},
                      key=lambda c: c.sensor)

    def full_hours(self, array: str, soc: Mapping[str, Mapping[float, float | None]]) -> set[float]:
        """Period starts in which a battery that may curtail the array was full; soc: sensor -> highest % per
        period."""
        return {t for s in self.stores_of(array) for t, v in soc.get(s.sensor, {}).items()
                if v is not None and v >= s.full_pct}

    def charge_hours(self, array: str, charging: Mapping[str, Mapping[float, float]]) -> set[float]:
        """Period starts in which a battery that may curtail the array charged at its maximum power: the array
        delivered at least what was measured. charging: power sensor -> highest charge power per period, W
        (charge_peaks)."""
        return {t for s in self.stores_of(array) if s.charger
                for t, w in charging.get(s.charger.sensor, {}).items() if s.charger.at_max(w)}

    def array_limit(self, array: str) -> float | None:
        """The fixed limit of an array's measured sum: the sum of its sensors' limits, if every sensor has one,
        and the output limit of a system that holds only this array, whichever is lower.

        Exact when the sensors reach their limits together (same orientation). With one sensor unlimited the
        sum is unbounded, so there is no cap from the sensors.
        """
        inputs = self.inputs_of(array)
        found = [sum(i.limit_w for i in inputs)] if inputs and all(i.limit_w is not None for i in inputs) else []
        found += [c.limit_w for c in self.caps if c.arrays == (array,)]
        return min(found) if found else None

    def shared_caps(self, array: str) -> list[Cap]:
        """The fixed limits the array shares with others: they mark lower bounds, but cap no single forecast."""
        return [c for c in self.caps if array in c.arrays and len(c.arrays) > 1]

    def limited_arrays(self) -> dict[str, float]:
        """Array id -> fixed limit (W), for arrays that have one."""
        return {a: w for a in {i.array for i in self.inputs.values()} if (w := self.array_limit(a)) is not None}


def charge_peaks(chargers: Iterable[Charger], high: Mapping[str, Mapping[float, float | None]],
                 low: Mapping[str, Mapping[float, float | None]], factors: Mapping[str, float]
                 ) -> dict[str, dict[float, float]]:
    """Power sensor -> highest charge power per period in W, from the period maxima (or minima of an inverted
    sensor, which reads charging as negative); factors: the sensor's unit to W."""
    out: dict[str, dict[float, float]] = {}
    for c in chargers:
        f = factors.get(c.sensor, 1.0) * (-1.0 if c.invert else 1.0)
        values = (low if c.invert else high).get(c.sensor, {})
        out[c.sensor] = {t: v * f for t, v in values.items() if v is not None}
    return out


def _stores(systems: Mapping[str, sy.System], batteries: Mapping[str, Mapping[str, Any]], feed_in_mode: str,
            soc_full: float, max_soc: Mapping[str, float]) -> dict[str, tuple[Store, ...]]:
    """Array -> the batteries that may curtail it (systems.curtailed_arrays).

    A battery counts as full from the household's "full" level, or FULL_TOLERANCE below its upper limit if that is
    lower; max_soc: the current upper limit per battery where it comes from an entity (its history is not kept).
    """
    out: dict[str, list[Store]] = {}
    for sid, arrays in sy.curtailed_arrays(systems, feed_in_mode).items():
        d = batteries.get(systems[sid].battery or "")
        if not d or not d.get(CONF_SOC_SENSOR):
            continue
        charger = (Charger(d[CONF_BATTERY_POWER], bool(d.get(CONF_BATTERY_INVERT)), float(d[CONF_MAX_CHARGE]))
                   if d.get(CONF_BATTERY_POWER) and d.get(CONF_MAX_CHARGE)
                   and sy.charge_curtails(systems, sid, feed_in_mode) else None)
        store = Store(d[CONF_SOC_SENSOR], min(soc_full, float(max_soc.get(systems[sid].battery or "",
                                                                            d.get(CONF_MAX_SOC) or 100))
                                              - FULL_TOLERANCE), charger)
        for a in arrays:
            out.setdefault(a, []).append(store)
    return {a: tuple(dict.fromkeys(s)) for a, s in out.items()}


def _caps(systems: Mapping[str, sy.System], arrays: Iterable[str]) -> tuple[Cap, ...]:
    """The output limits that are fixed: no battery in the system or its subsystems. A limit per phase is left
    out: the system's total may exceed it."""
    known = set(arrays)
    out = []
    for s in sorted(systems.values(), key=lambda s: s.id):
        limit = s.get(CONF_OUTPUT_LIMIT)
        if not limit or s.get(CONF_LIMIT_PER_PHASE):
            continue
        tree = [s, *(systems[c] for c in s.children)]
        if any(x.battery for x in tree):
            continue
        members = tuple(a for x in tree for a in x.arrays if a in known)
        if members:
            out.append(Cap(s.id, members, float(limit)))
    return tuple(out)


def build(arrays: Mapping[str, Iterable[str]], limits: Mapping[str, float] | None = None,
          soc_sensors: Iterable[str] = (), soc_full_pct: float = 95.0,
          systems: Mapping[str, sy.System] | None = None, batteries: Mapping[str, Mapping[str, Any]] | None = None,
          feed_in_mode: str = "free", max_soc: Mapping[str, float] | None = None) -> Installation:
    """The installation from the configuration: arrays (id -> sensors), limits (sensor -> W), the systems and the
    battery subentries' data; max_soc: battery id -> current upper limit in % where an entity sets it."""
    limits = limits or {}
    inputs = {}
    for array, sensors in arrays.items():
        for s in sensors:
            w = limits.get(s)
            inputs[s] = Input(s, array, float(w) if w is not None and w > 0 else None)
    systems = systems or {}
    return Installation(House(tuple(soc_sensors), float(soc_full_pct)), inputs,
                        _stores(systems, batteries or {}, feed_in_mode, float(soc_full_pct), max_soc or {}),
                        _caps(systems, arrays))
