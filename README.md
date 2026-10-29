# Noxcast

PV generation forecast for Home Assistant that explains every number: solar geometry and weather
first, then what Noxcast learns about each array (efficiency, shade, reflection, temperature), then
a statistical correction. Optional: house load forecast, battery simulation and the best time for
flexible consumers.

**Status:** early version (0.1.0), breaking changes possible.

## Overview

| Part | What you get |
|---|---|
| PV forecast | Per array and in total: today, tomorrow, day after (up to 7 days), hourly, with a P10–P90 band |
| Today | Forecast fixed at 06:00, expected day (measured + corrected rest), remaining energy |
| Learning | Efficiency, shading map, reflection, direction factor, sky view, temperature loss – from your own history |
| Weather | Open-Meteo (mean of ICON and ECMWF IFS), also as a weather entity for the dashboard |
| Accuracy | Day-ahead archive: forecast against actual, split into weather error and model error |
| Load (optional) | House load forecast: base load per hour plus your consumers |
| Battery (optional) | Simulation until tomorrow's charge start: full / empty, surplus, lost surplus, grid |
| Planning (optional) | Best window and PV share for flexible consumers (washer, dryer, …) |
| Frontend | Sidebar panel *Noxcast* (full overview), day card and weather card for dashboards |

Noxcast reads Home Assistant's long-term statistics; it needs no own database and no daily
energy sensors. Nothing is controlled – all outputs are sensors you can use in automations.

## Requirements

- Home Assistant 2025.6 or newer, recorder enabled.
- Per PV array a **power** sensor (W/kW, `state_class: measurement`), ideally DC per MPPT input.
  The more history, the sooner the learned values are good; up to two years are used.
- Internet access to Open-Meteo (free for non-commercial use, no key).

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/TelosNox/noxcast` (category
*Integration*), install, restart Home Assistant. Without HACS: copy `custom_components/noxcast`
into `config/custom_components` and restart.

## Setup

Settings → Devices & services → Add integration → **Noxcast**. Then add subentries on the
integration page (⋮ → *Add PV array* / *Add consumer* / *Add battery*).

### 1. Main entry

| Field | Meaning |
|---|---|
| Battery state of charge sensors | Hours in which a battery is full are left out of learning (output may be curtailed) |
| Battery considered full at | SOC from which that applies (default 95 %); match your charge limit |
| House load | Optional. Whole-house power (W/kW). Enables the load forecast |
| Day type | Optional. Entity whose state at noon is the day type (Workday sensor, an `input_select` such as home office / office / holiday). Profiles are learned per state |
| Grid power | Optional. Power at the grid connection, positive = import (*invert* if export is positive) |
| Surplus is fed into the grid | Off = zero export: surplus with full batteries counts as unused |
| Forecast days | 2–7, today included (default 3) |

### 2. PV arrays (one per group of power sensors)

An array is what is measured together, usually one MPPT input or one inverter.

| Field | Meaning |
|---|---|
| Power sensors | One or more; they are summed |
| Tilt, azimuth, peak power | Of the modules (azimuth 180° = south) |
| Further orientations 2–4 | Optional: modules on the same sensors that face another way (e.g. east + south + west on one input, vertical east + west). Each with tilt, azimuth and its own peak power; the array's peak is the sum |
| Valid since | Optional: ignore history before this date (e.g. after a rebuild) |
| Temperature coefficient | Optional: datasheet Pmax coefficient in %/K; otherwise learned |
| Heating | Optional: cell heating in K at 1000 W/m² (default 30 K; roof without rear ventilation higher, free-standing lower) |

Learning starts right away from the existing history and then runs every night (01:30).

### 3. Consumers (optional, for the load forecast)

A consumer with its own sensor is taken out of the house load and forecast on its own.

| Field | Meaning |
|---|---|
| Power or energy sensor | Power (W/kW) or energy (kWh, e.g. per-program energy of a washer) |
| Remaining / elapsed time | Optional: a running program counts until it ends |
| Timing | *Fixed*: part of the load forecast. *Flexible*: you choose when it runs; Noxcast suggests the window |
| Kind (fixed only) | *Profile*: own median per hour. *Program*: runs with an end (dishwasher); learned start times, runs per day, power curve. *Session*: on, then open-ended (PC, TV); learned on-probability and duration. *Regulated*: follows the outside temperature (heat pump, air conditioning) |
| Run power, run minutes | Flexible only, optional: otherwise learned from history |

### 4. Batteries (optional, needs the house load)

| Field | Meaning |
|---|---|
| State of charge sensor, capacity | Required |
| Battery power sensor | Optional: charge/discharge power for the card, positive = charging (*invert* otherwise) |
| Max / min SOC | Number or entity (e.g. charge limit, backup reserve) |
| Reserve | Percent points above min SOC where discharging stops in practice (e.g. a feed-in stop rule) |
| Coupling, efficiency | DC (default round trip 95 %) or AC (90 %), or own value |
| Max charge / discharge power | Optional limits; above them surplus goes to the grid or the grid supplies |

All batteries are simulated as one storage (capacities add up, limits weighted by capacity).

### 5. Options (⋮ → Configure)

| Option | Default |
|---|---|
| Weather models | `icon_seamless` + `ecmwf_ifs`, averaged hour by hour; any Open-Meteo model can be added |
| Air temperature from archive | On: needed to learn the temperature loss |
| Satellite filter | Off: uses satellite irradiance to pick clear hours for learning and to split the forecast error into weather and model |
| Archive URLs, satellite model, API key | Open-Meteo; change only for self-hosted or commercial access |

## Dashboard

### Sidebar panel *Noxcast* (full overview)

Appears in the sidebar after setup; nothing to configure. Tabs:

| Tab | Content |
|---|---|
| Learned | Per array the learned shade, reflection and direction maps over sun azimuth × elevation with the sun paths of the year, a date slider, values per cell, a month × hour grid, key figures (efficiency, sky view, temperature coefficient, coverage) and a plausibility check of tilt and azimuth |
| Day | One day (up to 120 days back, 2 ahead): the arrays stacked, forecast vs expectation vs actual, P10–P90, hourly weather, shade and reflection periods; per hour a waterfall from clear sky through clouds, shade, reflection, heat to the forecast; how the day's expectation developed hour by hour; where the energy went (PV, battery, grid, house) |
| Forecast accuracy | Day-ahead forecasts against actual over 14 / 30 / 90 days: error, bias, calendar, by weather class, each weather model against the mean; with the satellite filter the split into weather part and model part |
| Load | Rest of today and tomorrow by part (base load, each consumer), learned profiles, backtest, suggested recurring devices |
| Battery & surplus | Simulation until tomorrow's charge start, what-if with more capacity or an extra consumer, the morning prediction against what happened, last 30 days |
| Diagnostics | Weather, last learning run, archive; every sensor Noxcast reads with state, statistics and coverage; missing data and errors |

### Day card `custom:noxcast-card`

Registered automatically; add it via *Add card* → *Noxcast*. It always shows what Noxcast is
configured with (arrays, house load, grid, batteries, feed-in); the card settings only change the
display.

- **Power now** as a row of figures: PV, battery, home, grid, with moving dots for the
  flows. Icons above the figures show their state: the sun grows with the share of peak power, the
  battery is filled to its state of charge, a window of the house lights up per step of load, and
  the pylon gets a lightning bolt per step (blue while drawing, yellow while feeding in). A step
  goes up at once; it goes down at once below 60 % of its limit, otherwise after 30 s below it.
  Tapping a figure opens its entity.
- **Day chart:** PV actual (solid), expectation and forecast (dashed), shade loss hatched, house
  load, battery level (measured, then simulated; red where it is full and surplus is lost), P10–P90
  band, a lane per array with shade and reflection periods, hourly weather symbols on top. The chart
  uses the full card width, the scale sits inside it. Hover or tap an hour for its values.
- **Numbers of the day** below the chart: actual, expected, the morning forecast, shade loss and
  tomorrow's forecast.

| Option | Default | |
|---|---|---|
| `show_battery`, `show_effects`, `show_band`, `show_load` | `true` | Battery level, shade/reflection lanes, P10–P90, house load in the chart |
| `show_weather` | `true` | Weather symbols above the chart |
| `range` | `production` | `production` (an hour around sunrise and sunset) or `day` (0–24 h, night compressed) |
| `labels` | `icons` | `icons` (icons that show the state) or `text` (names, battery percentage and power) |
| `sun_glow` | `false` | The sun icon glows by the share of peak power |
| `home_steps` | `[100, 300, 1000]` | Icons: a window of the house lights from each of these watts |
| `grid_steps` | `[300, 1000]` | Icons: second and third lightning bolt of the grid, either direction (the first comes with the flow, from `flow_min_w`) |
| `battery_arrows` | `false` | Up and down arrows beside the battery icon, the one of the flow lit |
| `flow_style` | `chain` | `chain` (dots between the figures) or `bus` (a line below them) |
| `flow_speed` | `normal` | `calm`, `normal`, `lively` |
| `flow_min_w` | `10` | Smallest flow shown, in W |
| `look` | `plain` | `plain` (Home Assistant card) or `noxcast` (panel look) |
| `legend` | `true` | Legend below the chart |
| `font_scale` | `1` | 0.8–1.6 |

All options are also in the visual editor, grouped into power now, day chart and look.

### Weather card `custom:noxcast-weather-card`

The coming days one per row (symbol, high / low, precipitation) with the PV yield Noxcast expects,
from Noxcast's own weather – the same that drives the forecast.

| Option | Default | |
|---|---|---|
| `days` | `5` | 1–7 days of weather; PV only within the forecast horizon |
| `show_pv` | `true` | PV yield per day |
| `pv_bar` | `line` | Share of the clear-sky yield as `line`, `fill` or `none` |
| `temperature`, `humidity`, `name` | – | Optional measured outside values above the days |
| `look` | `noxcast` | `noxcast` or `plain` |

## Entities

**Noxcast (total)** and **Noxcast *array*** devices:

| Entity | Total | Array | Notes |
|---|---|---|---|
| Forecast today | ✓ | ✓ | Fixed at 06:00 |
| Expected today | ✓ | ✓ | Measured hours + corrected rest |
| Forecast remaining today | ✓ | ✓ | |
| Forecast tomorrow, day after | ✓ | ✓ | |
| System efficiency, shading map coverage, temperature coefficient, satellite agreement | | ✓ | Learned values |
| Shade today (sensor and image), shading map (image) | | ✓ | |
| Last learning run, forecast error (30 days) | ✓ | | Error per array and per weather model as attributes |
| Weather | ✓ | | Current hour, hourly and daily forecast for 7 days |

Forecast sensors carry `hourly` (ISO timestamp → kWh, `schema_version` 1); the total sensors also
`p10` / `p90`. The total is offered as solar forecast in the energy dashboard.

With house load: **Load rest of today**, **Load tomorrow** (attributes `hourly`, `base`,
`consumers`, `correction`, `backtest`). With batteries: **Surplus rest of today**, **Surplus lost
today**, **Battery full at**, **Battery highest level**, **Battery empty at**, **Battery at next
charge start** (attribute `grid`: grid energy until then). Per flexible consumer: **PV share now**
with the best window today and tomorrow as attributes.

## Services

| Service | |
|---|---|
| `noxcast.exclude_period` | Leave a period out of learning (e.g. curtailment while away, maintenance) |
| `noxcast.relearn` | Learn again from history now |

## How it works

### Forecast of an hour

Computed in six 10-minute steps per hour (sun position at each step) and averaged:

1. **Weather:** Open-Meteo GHI, DNI, DHI and air temperature, the configured models averaged.
2. **Geometry:** beam, sky diffuse and ground reflection on the module plane; with several
   orientations per plane, each clipped at zero, summed by peak share.
3. **Learned array values:** beam × shade transmission × direction factor, sky diffuse × sky view,
   plus reflected light from the surroundings.
4. **Efficiency** (learned, at 25 °C) × peak power.
5. **Temperature:** cell temperature from air temperature and irradiance (heating), loss with the
   learned or datasheet coefficient.

### Learning (nightly, from long-term statistics)

- **Data filters:** hours with a full battery, excluded periods, outages and incomplete hours are
  left out. A plateau at the inverter limit is learned and applied as a cap. Unsteady hours
  (passing clouds, restarts) do not count for efficiency, direction factor and reflection.
- **Efficiency:** from hours with strong expected output.
- **Shading map** (10° azimuth × 5° elevation): the share of direct light that arrives, only where
  direct light is possible. It follows the shape of real obstacles; values ≥ 0.9 count as free.
  Recent observations weigh more; consistent new observations replace old ones (a tree cut down).
- **Building edge:** a row of houses opposite, as one straight edge (height at right angles, row
  direction, azimuth range), fitted to clear hours. Towards the sides the row is further away, so
  the edge sinks. It fills only the cells without a learned value, mostly winter positions a few
  clear days cannot reach, and is drawn on along the row as long as the data do not contradict it.
  Kept only if it clearly explains the shade in its range; shown under Learned and in the map.
- **Direction factor** (coarse bins, 0.8–1.2): what remains at unshaded positions – geometry and
  model errors.
- **Reflection:** light from bright surfaces where the module itself gets little direct light.
- **Sky view:** share of the sky the modules see, from overcast hours (needs the satellite filter).
- **Temperature coefficient:** upper envelope of output over cell temperature.

### During the day

- The forecast refreshes every 30 minutes and at start.
- **Forecast today** is fixed at 06:00. **Expected today** = measured hours + the rest corrected by
  how much clearer or cloudier the last hours were than forecast (half the shift for the current
  hour, halving every further hour, never above clear sky). The running hour counts from 15
  measured minutes.
- The **P10–P90 band** comes from the archive: how far the clearness of actual and forecast differed
  on past days with similar weather. It shifts the clouds, so it never goes above a clear sky (plus
  5 % for model errors).

### Weather

- One Open-Meteo request returns all configured models; GHI, DNI and DHI are averaged alike.
  Each model is also archived on its own, so its accuracy can be compared with the mean.
- The weather entity uses the same averaged values for 7 days. Condition: precipitation
  ≥ 0.1 mm/h is rain (≥ 4 mm pouring, snow at ≤ 0 °C, sleet up to 2 °C), otherwise cloud cover
  < 20 % sunny / clear night, < 70 % partly cloudy, else cloudy; wind ≥ 10.8 m/s windy;
  thunderstorm and fog from the weather code. A day is judged on its daylight hours.
- If Open-Meteo cannot be reached, the last answer is used for up to 24 h, also after a restart.

### Archive and accuracy

The forecast for tomorrow is overwritten until midnight; the last version is kept as the day-ahead
forecast (120 days) and completed with the measured output at night. During the day the expected
total is recorded at every full hour (05–21). With the satellite filter the same model is run on
the measured irradiance of each past hour: forecast − that = weather error, that − actual = model
error.

A second archive keeps the 5-minute means of every power sensor, taken every night from Home
Assistant's 5-minute statistics (which are kept only about ten days), for sharper shading later:
5 minutes cover about 1.25° of sun azimuth, and two modules of one array that differ at the same
moment show partial shade. Only periods with the sun in front of the modules, in whole watts, for
at most three years: about 0.5 MB per sensor, however long Noxcast runs. Not used for the forecast
yet.

### Load forecast

load(h) = base profile × correction + fixed consumers + regulated consumers + running flexible ones

- **Base profile:** house load minus all consumers, median per hour and day type (last weeks).
- **Correction:** ratio of the last 4 hours to the profile, fading towards 1 over the next hours;
  a sustained higher level (e.g. air conditioning) is added flat to the rest of the day.
- **Program:** expected starts per hour × run curve, capped at the most runs seen per day minus
  the runs already done; a running program counts with its remaining energy.
- **Session:** while on, the current power for the typical remaining duration at that time of day;
  otherwise on-probability × typical power.
- **Regulated:** daily energy against daily mean temperature (threshold + slope, learned over the
  seasons), spread by the consumer's mean hourly shape.

### Battery simulation and planning

- Hour by hour from now until tomorrow's charge start (first hour with PV above load): surplus
  charges up to the upper limit and the charge power, the rest is lost (or fed in); a deficit
  discharges down to min SOC + reserve, the rest comes from the grid. Losses by coupling.
- The first simulation after 06:00 is kept per day and compared with the measured SOC.
- **Flexible consumers:** each run is placed into the surplus that would not go into the battery
  anyway; several are placed one after the other. Results: best window today and tomorrow, PV
  share if started now.

## Privacy

Noxcast sends only your coordinates to Open-Meteo. Everything else stays in Home Assistant.

## Development

- Concept and design decisions: [docs/concept.md](docs/concept.md)
- Tests: `pip install -r requirements_test.txt && pytest` (Linux/macOS; Home Assistant does not
  install on Windows). The physics tests compare against pvlib.

## License

GPL-3.0 – see [LICENSE](LICENSE).
