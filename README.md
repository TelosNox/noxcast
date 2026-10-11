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

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/TelosNox/noxcast`
(category *Integration*), install, restart Home Assistant. Alternatively copy
`custom_components/noxcast` into your `config/custom_components`.

Then: Settings → Devices & services → Add integration → **Noxcast**, choose the weather source,
and add one **PV array** per orientation.

## Entities

Per array and in total: forecast today, tomorrow, day after tomorrow (kWh, attribute `hourly`
with ISO timestamps → kWh, `schema_version` 1) and remaining today. Diagnostics: system
efficiency, shading map coverage, last learning run. The total is available as solar forecast
in the energy dashboard.

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
