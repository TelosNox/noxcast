"""What lies between the modules and the house: the installation as configured (pure Python, no Home Assistant).

Power flows from the inputs (PV power sensors) through devices and groups to the house, and every level can hold
a limit. This first stage knows the two ends only: inputs with a fixed limit (an MPPT or micro-inverter that
delivers at most so many watts) and the house with the batteries whose full state may curtail the PV. Devices
and groups follow (#250); arrays without them keep today's behaviour.

A limit is fixed when no battery sits below it: the PV can never exceed it, so it caps the forecast. A limit that
depends on a state (a full battery) is not a cap; the hours it may have cut are left out instead.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Input:
    """One PV power sensor, part of an array (the learning unit)."""

    sensor: str
    array: str                           # array subentry id
    limit_w: float | None = None         # fixed limit in W (MPPT, micro-inverter), None = none known


@dataclass(frozen=True)
class House:
    soc_sensors: tuple[str, ...] = ()    # batteries whose full state may curtail every array (the old setting)
    soc_full_pct: float = 95.0


@dataclass(frozen=True)
class Installation:
    house: House = field(default_factory=House)
    inputs: Mapping[str, Input] = field(default_factory=dict)       # by sensor

    def inputs_of(self, array: str) -> list[Input]:
        return [i for i in self.inputs.values() if i.array == array]

    def array_limit(self, array: str) -> float | None:
        """The fixed limit of an array's measured sum: the sum of its sensors' limits, if every sensor has one.

        Exact when the sensors reach their limits together (same orientation). With one sensor unlimited the
        sum is unbounded, so there is no cap.
        """
        inputs = self.inputs_of(array)
        if not inputs or any(i.limit_w is None for i in inputs):
            return None
        return sum(i.limit_w for i in inputs)

    def limited_arrays(self) -> dict[str, float]:
        """Array id -> fixed limit (W), for arrays that have one."""
        return {a: w for a in {i.array for i in self.inputs.values()} if (w := self.array_limit(a)) is not None}


def build(arrays: Mapping[str, Iterable[str]], limits: Mapping[str, float] | None = None,
          soc_sensors: Iterable[str] = (), soc_full_pct: float = 95.0) -> Installation:
    """The installation from the configuration: arrays (id -> sensors), limits (sensor -> W)."""
    limits = limits or {}
    inputs = {}
    for array, sensors in arrays.items():
        for s in sensors:
            w = limits.get(s)
            inputs[s] = Input(s, array, float(w) if w is not None and w > 0 else None)
    return Installation(House(tuple(soc_sensors), float(soc_full_pct)), inputs)
