"""Planning flexible consumers: the best start window for a run, given the surplus of the forecast.

Pure functions, no Home Assistant imports. Surplus series are Wh per hour start (PV minus load,
only positive values). A run is a constant power for a duration; it is placed on a 15-minute grid.

Two shares are reported for a start time:
  pv_share   - part of the run's energy covered by surplus at all,
  lost_share - part covered by surplus that would otherwise be lost (battery full or power-limited);
               this is the energy a run saves without taking anything from the battery.
The best start maximises pv_share, then lost_share, then prefers the earlier start. A start with less than
MIN_SHARE from surplus is no window: without surplus every start ties and the earliest one, at night, would win.
Several consumers are placed one after the other (by priority); each takes its share of the surplus.

With a battery, surplus is not free: what a run takes from it would otherwise charge the battery, which may then
run empty earlier in the night. There the shares come from the battery simulation itself (a Cost), run with and
without the run: pv_share is the part that costs neither grid energy nor energy the battery would still hold at the
end of the simulation (the 48-hour outlook), lost_share the part by which the lost surplus shrinks. The battery's
what-if counts the same way, until the charge start.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from statistics import median

from .const import Series

H = 3600.0
STEP = 900.0                      # start grid and integration step: 15 minutes
MIN_SHARE = 0.05                  # below this share from surplus no start is recommended as a window

# the battery simulation with an extra load (Wh per hour start): (grid Wh, lost surplus Wh, energy still in the
# battery at the end in Wh it can deliver); see battery.planning_cost
Cost = Callable[[Series], tuple[float, float, float]]


@dataclass(frozen=True)
class RunProfile:
    name: str
    power_w: float
    minutes: float

    @property
    def energy_wh(self) -> float:
        return self.power_w * self.minutes / 60


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    pv_share: float
    lost_share: float


def _slots(series: Series) -> Series:
    """Hourly Wh -> Wh per 15-minute slot (evenly spread over the hour)."""
    out = {}
    for t, wh in series.items():
        for i in range(4):
            out[t + i * STEP] = max(wh, 0.0) / 4
    return out


def evaluate(run: RunProfile, start: float, surplus: Series, lost: Series) -> Window:
    """Shares of a run starting at `start`; surplus and lost are per 15-minute slot (Wh)."""
    need_per_slot = run.power_w * STEP / H
    remaining = run.minutes * 60
    t, pv, from_lost = start, 0.0, 0.0
    while remaining > 0:
        part = min(STEP, remaining) / STEP
        need = need_per_slot * part
        slot = t - t % STEP
        pv += min(need, surplus.get(slot, 0.0) * part)
        from_lost += min(need, lost.get(slot, 0.0) * part)
        remaining -= STEP
        t += STEP
    energy = run.energy_wh or 1.0
    return Window(start, start + run.minutes * 60, min(1.0, pv / energy), min(1.0, from_lost / energy))


def hourly(run: RunProfile, start: float) -> Series:
    """The run's energy per hour start (Wh), as the hourly battery simulation takes it."""
    out: Series = {}
    t, end = start, start + run.minutes * 60
    while t < end:
        hour = t - t % H
        stop = min(end, hour + H)
        out[hour] = out.get(hour, 0.0) + run.power_w * (stop - t) / H
        t = stop
    return out


def _add(a: Series, b: Series) -> Series:
    return {t: a.get(t, 0.0) + b.get(t, 0.0) for t in a.keys() | b.keys()}


def evaluate_stored(run: RunProfile, start: float, cost: Cost, planned: Series,
                    base: tuple[float, float, float]) -> Window:
    """Shares of a run starting at `start` from the battery simulation: base is the cost with the runs planned
    before (planned) and without this one."""
    grid, lost, kept = cost(_add(planned, hourly(run, start)))
    energy = run.energy_wh or 1.0
    taken = max(grid - base[0], 0.0) + max(base[2] - kept, 0.0)
    pv = min(max(1.0 - taken / energy, 0.0), 1.0)
    return Window(start, start + run.minutes * 60, pv, min(max((base[1] - lost) / energy, 0.0), 1.0))


def best_window(run: RunProfile, surplus: Series, lost: Series, earliest: float,
                latest_end: float, score: Callable[[float], Window] | None = None) -> Window | None:
    """Best start on the 15-minute grid with the run ending by latest_end; slot series as in evaluate, or
    score(start) for another way to count. None when no run fits or none gets MIN_SHARE from surplus."""
    start = earliest + (-earliest) % STEP
    best = None
    while start + run.minutes * 60 <= latest_end:
        w = score(start) if score else evaluate(run, start, surplus, lost)
        if best is None or (round(w.pv_share, 3), round(w.lost_share, 3)) > (round(best.pv_share, 3), round(best.lost_share, 3)):
            best = w
        start += STEP
    return best if best is not None and best.pv_share >= MIN_SHARE else None


def plan(runs: list[RunProfile], surplus_h: Series, lost_h: Series, now: float,
         latest_end: float) -> dict[str, dict]:
    """Place runs in priority order. Returns per name: best window and the shares when started now."""
    surplus, lost = _slots(surplus_h), _slots(lost_h)
    out = {}
    for run in runs:
        now_w = evaluate(run, now, surplus, lost)
        best = best_window(run, surplus, lost, now, latest_end)
        out[run.name] = {"now": now_w, "best": best}
        if best is not None:                      # the planned run takes its part of the surplus
            need = run.power_w * STEP / H
            t = best.start
            while t < best.end:
                slot = t - t % STEP
                take = min(need, surplus.get(slot, 0.0))
                surplus[slot] = surplus.get(slot, 0.0) - take
                lost[slot] = max(0.0, lost.get(slot, 0.0) - take)
                t += STEP
    return out


def plan_stored(runs: list[RunProfile], cost: Cost, now: float, earliest: float,
                latest_end: float) -> dict[str, dict]:
    """plan() with a battery: the shares from the battery simulation (evaluate_stored)."""
    planned: Series = {}
    out = {}
    for run in runs:
        base = cost(planned)
        score = lambda start, run=run, base=base: evaluate_stored(run, start, cost, planned, base)  # noqa: E731
        best = best_window(run, {}, {}, earliest, latest_end, score)
        out[run.name] = {"now": score(now), "best": best}
        if best is not None:                      # the planned run is part of the load for the next ones
            planned = _add(planned, hourly(run, best.start))
    return out


def day_plan(runs: list[RunProfile], pv: Series, load: Series, lost: Series,
             now: float, day_end: float, cost: Cost | None = None) -> dict[str, dict]:
    """Best windows in the surplus of today (counting what the battery would lose) and of tomorrow, in run order.
    With a battery (cost), both days count with the battery simulation instead (see plan_stored)."""
    if cost is not None:
        today = plan_stored(runs, cost, now, now, day_end)
        tomorrow = plan_stored(runs, cost, day_end, day_end, day_end + 86400)
        return {r.name: {"run": r, "now": today[r.name]["now"], "today": today[r.name]["best"],
                         "tomorrow": tomorrow[r.name]["best"]} for r in runs}
    surplus = {t: max(pv.get(t, 0.0) - w, 0.0) for t, w in load.items()}
    today = plan(runs, {t: v for t, v in surplus.items() if t < day_end}, lost, now, day_end)
    tomorrow = plan(runs, {t: v for t, v in surplus.items() if day_end <= t}, {}, day_end, day_end + 86400)
    return {r.name: {"run": r, "now": today[r.name]["now"], "today": today[r.name]["best"],
                     "tomorrow": tomorrow[r.name]["best"]} for r in runs}


NEAR_BEST = 0.05                  # "start now" also needs a share this close to the best window still to come today


def start_now(now: Window, best_today: Window | None, threshold: float) -> bool:
    """Whether starting now is worth it: at least `threshold` of the run from surplus, and nearly as much
    as the best window still to come today (or no such window fits before the day ends)."""
    if now.pv_share < threshold:
        return False
    return best_today is None or now.pv_share >= best_today.pv_share - NEAR_BEST


def run_profile(name: str, power_w: float | None, minutes: float | None, learned: dict | None) -> RunProfile | None:
    """A flexible consumer's run: configured values first, learned ones ({"power_w", "minutes"}) otherwise."""
    learned = learned or {}
    power, minutes = power_w or learned.get("power_w"), minutes or learned.get("minutes")
    return RunProfile(name, float(power), float(minutes)) if power and minutes else None


RUN_W = 30.0                      # hourly mean above which a consumer counts as running
MIN_RUNS = 2


def learn_run(name: str, series: Series) -> RunProfile | None:
    """Typical run from hourly mean power: runs are blocks of consecutive hours above RUN_W.

    Hourly means blur start and end, so the run power is taken from the block's strongest hour
    and the duration follows from the energy: minutes = energy / power.
    """
    runs, block = [], []
    for t in sorted(series):
        if series[t] >= RUN_W and (not block or t - block[-1][0] <= H):
            block.append((t, series[t]))
            continue
        if block:
            runs.append(block)
        block = [(t, series[t])] if series[t] >= RUN_W else []
    if block:
        runs.append(block)
    if len(runs) < MIN_RUNS:
        return None
    energy = median(sum(w for _, w in b) for b in runs)
    power = median(max(w for _, w in b) for b in runs)
    return RunProfile(name, round(power, 1), round(energy / power * 60)) if power > 0 else None
