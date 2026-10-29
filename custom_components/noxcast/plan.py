"""Planning flexible consumers: the best start window for a run, given the surplus of the forecast.

Pure functions, no Home Assistant imports. Surplus series are Wh per hour start (PV minus load,
only positive values). A run is a constant power for a duration; it is placed on a 15-minute grid.

Two shares are reported for a start time:
  pv_share   - part of the run's energy covered by surplus at all,
  lost_share - part covered by surplus that would otherwise be lost (battery full or power-limited);
               this is the energy a run saves without taking anything from the battery.
The best start maximises pv_share, then lost_share, then prefers the earlier start.
Several consumers are placed one after the other (by priority); each takes its share of the surplus.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

H = 3600.0
STEP = 900.0                      # start grid and integration step: 15 minutes


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


def _slots(series: dict[float, float]) -> dict[float, float]:
    """Hourly Wh -> Wh per 15-minute slot (evenly spread over the hour)."""
    out = {}
    for t, wh in series.items():
        for i in range(4):
            out[t + i * STEP] = max(wh, 0.0) / 4
    return out


def evaluate(run: RunProfile, start: float, surplus: dict[float, float], lost: dict[float, float]) -> Window:
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


def best_window(run: RunProfile, surplus: dict[float, float], lost: dict[float, float], earliest: float,
                latest_end: float) -> Window | None:
    """Best start on the 15-minute grid with the run ending by latest_end; slot series as in evaluate."""
    start = earliest + (-earliest) % STEP
    best = None
    while start + run.minutes * 60 <= latest_end:
        w = evaluate(run, start, surplus, lost)
        if best is None or (round(w.pv_share, 3), round(w.lost_share, 3)) > (round(best.pv_share, 3), round(best.lost_share, 3)):
            best = w
        start += STEP
    return best


def plan(runs: list[RunProfile], surplus_h: dict[float, float], lost_h: dict[float, float], now: float,
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


def day_plan(runs: list[RunProfile], pv: dict[float, float], load: dict[float, float], lost: dict[float, float],
             now: float, day_end: float) -> dict[str, dict]:
    """Best windows in the surplus of today (counting what the battery would lose) and of tomorrow, in run order."""
    surplus = {t: max(pv.get(t, 0.0) - w, 0.0) for t, w in load.items()}
    today = plan(runs, {t: v for t, v in surplus.items() if t < day_end}, lost, now, day_end)
    tomorrow = plan(runs, {t: v for t, v in surplus.items() if day_end <= t}, {}, day_end, day_end + 86400)
    return {r.name: {"run": r, "now": today[r.name]["now"], "today": today[r.name]["best"],
                     "tomorrow": tomorrow[r.name]["best"]} for r in runs}


def run_profile(name: str, power_w: float | None, minutes: float | None, learned: dict | None) -> RunProfile | None:
    """A flexible consumer's run: configured values first, learned ones ({"power_w", "minutes"}) otherwise."""
    learned = learned or {}
    power, minutes = power_w or learned.get("power_w"), minutes or learned.get("minutes")
    return RunProfile(name, float(power), float(minutes)) if power and minutes else None


RUN_W = 30.0                      # hourly mean above which a consumer counts as running
MIN_RUNS = 2


def learn_run(name: str, series: dict[float, float]) -> RunProfile | None:
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
