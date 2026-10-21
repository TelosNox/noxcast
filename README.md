# Noxcast

Transparent PV generation forecast for Home Assistant: physics, learned array efficiency and
shading, and the irradiance forecast of your weather source – every number explainable.

**Status:** early prototype (0.1.0). Expect breaking changes.

## How it works

- Each PV array is configured with its power sensors, tilt, azimuth and peak power.
- Noxcast learns from Home Assistant's long-term statistics (no own database, no daily sensors):
  - **system efficiency** from hours with strong expected output,
  - a **shading map** over sun azimuth × sun elevation – the share of direct light that reaches
    the array, learned only where direct light is geometrically possible.
- Learning runs at startup and every night; recent observations weigh more, and abrupt changes
  (a tree cut, a new building) replace old values instead of being averaged.
- Hours with a full battery (possible curtailment) and excluded periods are ignored.
- The forecast combines clear-sky geometry, the shading map and the hourly irradiance of a
  weather entity (e.g. DWD `sun_irradiance`) or Open-Meteo, for today, tomorrow and the day after.
- Optional load forecast: with a house load sensor, Noxcast learns a base-load profile (median per
  hour, workday / weekend, last 6 weeks) without the consumers you add with their own power
  sensor (or an energy sensor in kWh, e.g. the per-program energy of a washer). Fixed consumers
  have a kind: *profile* (their own median per hour), *program* (learned start times, runs per day
  and the power curve of a run, e.g. a dishwasher) or *session* (learned on-probability and how long
  it stays on at that time of day, e.g. PCs); flexible ones (e.g. a washer you start on surplus) only count while they
  run. With a remaining-time sensor a running program counts until it ends (its power from the power
  sensor, or energy / elapsed time). A short-term correction follows today's level for the next
  hours.
- Optional battery simulation: add a **Battery** (state of charge, capacity, limits as number or
  entity, reserve, DC/AC coupling, power limits). With the load forecast, Noxcast simulates all
  batteries as one storage from now until tomorrow's charge start: surplus and lost surplus today,
  when the battery is full or empty, its highest level and its level at the next charge start.
- Flexible consumers get a sensor *PV share now* (how much of a run started now would come from
  surplus) with the best window today and tomorrow as attributes. The run (power, duration) is
  learned from the consumer's history or set in its configuration; several flexible consumers are
  placed one after the other, so they are not offered the same hours.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/TelosNox/noxcast`
(category *Integration*), install, restart Home Assistant. Alternatively copy
`custom_components/noxcast` into your `config/custom_components`.

Then: Settings → Devices & services → Add integration → **Noxcast**, choose the weather source,
and add one **PV array** per orientation. For the load forecast, set the house load sensor in the
entry and add a **Consumer** for each device with its own power sensor.

## Entities

Per array and in total: forecast today, tomorrow, day after tomorrow (kWh, attribute `hourly`
with ISO timestamps → kWh, `schema_version` 1) and remaining today. Diagnostics: system
efficiency, shading map coverage, last learning run. The total is available as solar forecast
in the energy dashboard. With a house load sensor: load rest of today and tomorrow (kWh,
attributes `hourly`, `base`, `consumers`, `correction`, `profile_days` and `backtest` with the
mean error of the replayed last 14 days next to a naive 7-day mean).

## Services

- `noxcast.exclude_period` – ignore a period with curtailment (e.g. holidays with zero export).
- `noxcast.relearn` – learn again from history now.

## Development

- Concept: [docs/concept.md](docs/concept.md)
- Validation prototype: [prototype/](prototype/)
- Tests: `pip install -r requirements_test.txt && pytest` (Linux/macOS; Home Assistant does not
  install on Windows). The physics tests compare against pvlib.

## License

GPL-3.0 – see [LICENSE](LICENSE).
