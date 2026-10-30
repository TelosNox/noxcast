"""Where a day's energy went: PV, battery and grid into house, battery and grid (pure Python).

Estimated hour by hour from long-term statistics: PV yield, house load, grid power and the battery's
state of charge. With a grid sensor import and export are measured (a missing house load follows from
the balance); without one the grid is what PV and battery leave over. The battery energy of an hour is the change of its mean state of charge (central difference
of the neighbouring hours) times its capacity; losses inside the battery are ignored. Within an hour
PV feeds the house first, then the battery, the rest goes to the grid; the battery then covers what
the house still needs, the grid the rest. With hourly values this is an estimate: an hour with both
import and export shows only the net.
"""
from __future__ import annotations

from .const import Series

HOUR = 3600.0
KEYS = ("pv_home", "pv_battery", "pv_grid", "battery_home", "battery_grid", "grid_home", "grid_battery", "pv_rest")


def battery_energy(soc: Series, capacity_wh: float) -> Series:
    """Energy into the battery per hour (Wh, > 0 charging) from the hourly mean state of charge in %."""
    out = {}
    for t in soc:
        before, after = soc.get(t - HOUR), soc.get(t + HOUR)
        if before is not None and after is not None:
            out[t] = (after - before) / 2 / 100 * capacity_wh
    return out


def day_flows(pv: Series, load: Series | None, battery: Series | None,
              start: float, end: float, grid: Series | None = None) -> dict | None:
    """Totals in Wh of each path in [start, end).

    pv: PV energy per hour; load: house load per hour, or None without a house-load sensor; battery:
    energy into the battery per hour, or None without batteries; grid: energy from the grid per hour
    (< 0 = export), or None without a grid sensor. Without load and grid only PV into the battery and
    the rest are known. An hour counts only when every configured series has a value for it.
    """
    base = load if load is not None else grid if grid is not None else pv
    hours = sorted(t for t in base if start <= t < end)
    out = dict.fromkeys(KEYS, 0.0)
    used = 0
    for t in hours:
        if (battery is not None and t not in battery) or (grid is not None and t not in grid):
            continue
        p = max(0.0, pv.get(t, 0.0))
        b = battery.get(t, 0.0) if battery is not None else 0.0
        charge, discharge = max(b, 0.0), max(-b, 0.0)
        used += 1
        if load is None and grid is None:
            to_battery = min(p, charge)
            out["pv_battery"] += to_battery
            out["pv_rest"] += p - to_battery
            continue
        if load is not None:
            home = max(0.0, load[t])
        else:                                       # the balance: what came in and was not stored or fed in
            home = max(0.0, p + grid[t] - b)
        direct = min(p, home)
        surplus, deficit = p - direct, home - direct
        if grid is None:
            to_battery = min(surplus, charge)
            to_grid = surplus - to_battery
            from_battery = min(discharge, deficit)
            out["grid_battery"] += charge - to_battery
            out["battery_grid"] += discharge - from_battery
            out["grid_home"] += deficit - from_battery
        else:                                       # import and export as measured
            imported, exported = max(grid[t], 0.0), max(-grid[t], 0.0)
            to_grid = min(surplus, exported)
            to_battery = min(surplus - to_grid, charge)
            from_battery = min(discharge, deficit)
            out["battery_grid"] += min(exported - to_grid, discharge - from_battery)
            out["grid_battery"] += min(imported, charge - to_battery)
            out["grid_home"] += min(imported, deficit - from_battery) if load is not None else deficit - from_battery
        out["pv_home"] += direct
        out["pv_battery"] += to_battery
        out["pv_grid"] += to_grid
        out["battery_home"] += from_battery
    if not used:
        return None
    return {"hours": used, "load": load is not None or grid is not None, "battery": battery is not None,
            "grid": grid is not None, **{k: round(v, 1) for k, v in out.items()}}
