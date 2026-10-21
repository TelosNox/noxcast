# Noxcast – Concept

Status: draft, October 2026.

## Goal

A PV generation forecast for Home Assistant that is **accurate, explainable and stable**.
Later, the same integration optionally adds a load forecast and surplus / grid-draw planning.

The name combines *Nox* (night) and *forecast*: the sun's energy of the day should carry the
household through the night.

## Core idea

The forecast is built from four independently learned parts:

| Part | Learned from | Changes |
|---|---|---|
| Geometry and efficiency per array | clear-sky hours | almost never (soiling, ageing) |
| Shading map (sun azimuth × sun elevation) | clear-sky hours, only where direct light is geometrically possible | rarely (tree, new building) |
| Direction factor (non-shade effects on beam) | clear-sky hours at unshaded positions | rarely |
| Reflected light (sunlit surroundings) | clear-sky hours where the module gets little beam | rarely |
| Sky view (share of the sky diffuse the array sees) | overcast hours with satellite irradiance | rarely |
| Weather forecast correction and uncertainty | all hours, forecast vs. actual | continuously |

Only the last part is "machine learning" in the narrow sense, and even there conditional
quantile tables or a small gradient-boosting model are sufficient. Geometry and shading are
statistics on top of physics (clear-sky model, plane-of-array irradiance, incidence angle losses).

### Calculation chain

The forecast is not "clear-sky output × cloud factor". Clouds change the *kind* of light, so
the weather enters first and every later step works on its result:

1. **Weather:** hourly irradiance, split into direct (beam) and diffuse.
2. **Geometry:** both parts projected onto the module plane (incidence-angle losses on beam).
   Sky diffuse follows Hay-Davies: on clear days a large part of the sky light comes from around
   the sun. This circumsolar share is treated like beam (incidence losses, shading); the rest is
   isotropic. A purely isotropic sky underestimated sun-facing planes on clear days, which a
   tilt fit compensated with about 8° too steep modules on both arrays.
3. **Shading:** learned transmission applied to the **beam part only** – on an overcast day a
   morning shadow costs almost nothing, on a clear day it costs everything.
4. **Sky view:** share of the isotropic sky diffuse not obstructed by surroundings, applied to
   the sky diffuse only (ground reflection unchanged).
   **Reflected light:** sunlit walls or roofs in front of the module add light in proportion to
   the direct normal irradiance, learned per sun position.
5. **Temperature:** loss from air temperature and irradiance, applied to the result.
6. **Efficiency:** one factor for everything else (inverter, cables, tolerance, soiling).

### Temperature

Module output drops with cell temperature, and cell temperature depends on air temperature,
irradiance (heating) and wind (cooling) – and on mounting/ventilation, which differs strongly
between e.g. a freely hanging balcony array and modules close to a roof. Instead of asking for a
mounting type or a datasheet coefficient, Noxcast learns the loss directly:

    T_cell = T_air + 30 K · irradiance on the plane / 1000 W/m²
    temperature factor = 1 + a · (T_cell − 25 °C)

Only the coefficient a is learned: per 5 K cell-temperature bin the upper envelope (90th
percentile) of actual/expected is taken, and a line is fitted through the bins. With hourly data a
free regression of a, heating and wind is not identifiable (noise ~11 %), while the envelope gave
−0.23 to −0.28 %/K for a balcony array (datasheet −0.26 %/K). a starts at −0.35 %/K, is shrunk
towards it when few bins exist and rejected outside [−0.5, −0.2] %/K. Learning runs only on
satellite-confirmed clear hours: in a first installation, learning over all hours gave 0 and
−0.06 %/K, because cloudy summer hours and clear winter hours share temperature bins. Without the
satellite filter the typical value is used. An optional datasheet value per array replaces both
and is recommended: cell temperature rises with irradiance, so
learning over cell-temperature bins also picks up irradiance-dependent model errors (geometry,
diffuse model, low-light behaviour). Heating is not identifiable even with a fixed coefficient
(estimates ran into 0 K and 70 K for two arrays), so it keeps a fixed typical value;
wind is not used. Air temperature comes from the weather forecast (15 °C if missing); for
learning from history it comes from the reanalysis archive.

### Optional: satellite irradiance for learning

Which past hours were really clear can be taken from satellite-derived irradiance instead of
being inferred statistically. Checked against one installation (Dec 2025 – Oct 2026, unshaded
hours only):

| Archive | Correlation with PV clearness | "clear" in archive but cloudy at the site | hourly error |
|---|---|---|---|
| ERA5 reanalysis (~25 km) | 0.69–0.78 | 4–5 % | 22–24 % |
| Satellite (EUMETSAT, ~5 km) | 0.90–0.94 | 0.4–1.3 % | 12–14 % |

Consequences:

- **Satellite irradiance is used as a pre-filter for clear hours.** Expectations are scaled to
  the measured clearness and the median is used for efficiency (over unshaded positions) and per
  shading cell: clouds are already removed, so a high percentile would only pick satellite errors
  (it gave 100 % system efficiency in a first test). Cells with too few clear hours keep the
  90th percentile over all hours, so coverage does not drop.
- **Regressions over many hours** (sky view, temperature loss) may use satellite irradiance
  directly, because hourly errors are random and average out.
- **Temperature and wind** come from the reanalysis archive; spatial resolution is sufficient.
- The feature is **optional** (off by default). When the archive is unreachable, learning falls
  back to the percentile method and the diagnostics say so; the forecast never fails because of it.
- A diagnostic reports the ongoing agreement (correlation) between archive and installation,
  because satellite quality varies by region (mountains, coastal fog).

**Sources are configurable** under advanced options, with Open-Meteo as default: archive URL
for irradiance (`https://satellite-api.open-meteo.com/v1/archive`), irradiance model
(`satellite_radiation_seamless`), archive URL for temperature/wind
(`https://archive-api.open-meteo.com/v1/archive`) and an optional API key. This allows
self-hosted Open-Meteo instances, commercial access and model changes without an update. A
different response format still needs a new source adapter; each source sits behind its own
small interface. URLs are validated with a test request when saved.

### Why shading only matters where direct light is possible

A shadow can only remove direct light that would otherwise reach the module. When the sun is
behind the module plane, physics already expects zero direct light and the shading is both
undetectable and irrelevant. The shading map is therefore learned and applied only where the
expected beam irradiance is significant.

### Shade and other effects are kept apart

A real obstacle has a shape: a building or the terrain shades low sun, an overhang high sun, and
the edge moves smoothly across the sky from day to day. Temperature, haze and model errors lower
values without such a shape. The shading map therefore follows physical rules after learning:

- Per azimuth the transmission rises with elevation to a free band and may fall again above it
  (overhang); dips in between are raised, a clear core (below 0.7) is never changed.
- Values from 0.9 count as free (dead band).
- Values between 0.7 and 0.9 are shade only within two cells of a core, in any direction: side
  walls give edges across azimuth.

What remains at unshaded positions on clear hours goes into a separate, coarse direction factor on
the beam part (20° × 15° bins, median, 0.8–1.2). It may exceed 1 and covers geometry and model
errors. Light reflected by the surroundings is learned apart from it (see below). Both are learned
against the measured irradiance, split into beam and diffuse exactly as in the forecast, so that
they correct the forecast's own chain and not a clear-sky estimate. The map shows shade only; the
direction factor is reported as a key figure.

Two modules of the same array that deliver equally at a sun position rule out partial shade
there; diverging modules confirm it. The roof analysis showed exactly that: the wall in the east
shades one module more than the other, while the reduced output at high south-west sun was equal
on both modules and is therefore no shade. Using per-module sensors as shade evidence is a later
step.

### Sky view

Diffuse light reaches the module from the whole half-space in front of it, weighted by the cosine
of the angle to the module normal. Everything in that half-space that hides sky costs diffuse
light: houses opposite, an overhang or the floor above a balcony module, a neighbouring roof.
Hiding ground costs little, since the ground reflects only about a fifth of the light. The
shading map cannot see this, because it is learned from the beam part on clear hours.

The sky view is learned from overcast hours (satellite clearness below 0.4), which carry almost
only diffuse light: what the installation produced beyond beam and ground reflection, relative to
the sky diffuse a free plane would receive from the measured irradiance (limits 0.2–1). Clear
hours contain diffuse light too, so efficiency, shading map and direction factor are learned again
with the new value until it settles. Without satellite data the sky view stays 1.

On one installation the hindcast against satellite irradiance gave a sky view of 0.45 for a steep
balcony array (houses opposite up to about 27°, a floor above) and 0.69 for a roof array; a
geometric estimate for the balcony gave 46–55 %. The daily error fell from 20 % to 9 % on the
balcony and from 13 % to 7 % on the roof; overcast days had been overestimated by 65 % and 34 %.
Partly cloudy hours now come out 7–16 % low, clear balcony days about 4 % low: one isotropic
factor cannot fit every sky.

### Reflected light

Sunlit surfaces in front of the module, such as a bright wall or a glass roof, send light to it.
Unlike shade this light does not depend on how the sun meets the module but on how strongly the
sun lights the surface, so it is modelled as an addition proportional to the direct normal
irradiance, per coarse sun-position bin (same bins as the direction factor). On overcast days it
vanishes like a shadow does.

Where the module gets a lot of direct light itself, "a little more beam" and "light from a wall"
cannot be told apart in hourly data. The reflection is therefore learned only where the beam on the
plane is below 30 % of the direct normal irradiance (grazing sun or sun behind the plane), as the
median of what remains beyond beam and diffuse; never negative, at most 0.3. The direction factor is
learned after it on the rest. In a setup with a lot of reflected light (a white wall opposite, a
neighbour's glass front) this keeps the direction factor from running into its limit.

On one installation the steep balcony array showed 3.5–4.6 % of the direct normal irradiance from
south-west to west at low sun, where bright house walls opposite are lit while the module only gets
grazing light; the roof array showed none. Together with learning against the measured irradiance
the balcony's clear-day bias went from −4.5 % to −1.5 %.

## Requirements

- **Primary measurement required:** power per array, preferably DC per MPPT. Systems whose
  inverter clips systematically (e.g. 800 W inverter on 2 kWp) are rejected during setup:
  if more than ~20 % of clear midday hours sit on a plateau, geometry, efficiency and shading
  cannot be learned. Occasional clipping is detected and excluded from learning.
- **One weather source** with irradiance: a Home Assistant weather entity that provides
  irradiance (e.g. DWD MOSMIX `sun_irradiance`), or Open-Meteo directly (GHI / DNI / DHI).
  Global irradiance is split into direct and diffuse (e.g. Erbs) if only GHI is available.
- **No local weather sensors** (temperature, humidity, lux, wind). A 2 K deviation is ~0.7 %
  power, while badly placed sensors cause much larger errors. Fog probability comes from the
  weather service; current conditions are visible in the PV output itself. Revisit only if
  backtesting shows a measurable need.

## User groups

1. **With history.** Import Home Assistant long-term statistics (hourly means; 5-minute data
   is kept only ~10 days). The date since which the current configuration exists is required.
   Noxcast proposes it automatically via change-point detection (jump in time of daily peak or
   in the ratio to clear-sky expectation); the user confirms. This also catches relocations
   with the same sensor.
2. **Without history.** Start from user-supplied tilt, azimuth and peak power without shading.
   Geometry settles after a few clear days; the shading map fills over a year, because each
   sun position occurs only twice a year. Coverage is shown as a diagnostic sensor.

## Learning and adaptation

- **Learning run:** nightly, incremental, seconds of CPU.
- **Forecast:** on every new weather dataset (hourly), milliseconds.
- **Intraday correction:** hourly or every 15 minutes; the deviation of the last hours
  (actual vs. forecast) corrects the rest of the day with decaying weight.
- **Ageing:** per shading-map cell, the most recent clear observations dominate (half-life
  around one year) to follow slow changes such as a growing tree.
- **Change detection:** if several new clear observations in a cell deviate consistently,
  the cell is replaced rather than averaged (a felled tree disappears within days).
- **Efficiency drift** raises a hint (soiling / degradation) instead of being absorbed silently.

## Data filters

- Curtailment: full battery with zero export, export limits, inverter AC limit (learned as a
  plateau and applied as a cap in the forecast).
- Snow: zero output under clear sky and frost.
- Outages: `unavailable`, all-zero days, incomplete hours.
- Time reference: an hourly mean belongs to the sun position at the middle of the hour.

## Outputs

- Per array and total, 15-minute and hourly resolution, today / tomorrow / day after.
- Remaining energy today, peak time, P10 / P50 / P90.
- Today three ways: the forecast fixed at 06:00 (to see the day's deviation), the expected total
  (measured hours + corrected rest) and the remaining energy. The intraday correction works on
  clearness (output relative to the clear-sky output of the same hour): if the last three hours
  were clearer than forecast, the coming hours get fewer clouds, never more than a clear sky. An
  hour already forecast as sunny is not lifted. Half of the shift applies to the current hour,
  halving every further hour, because the weather forecast itself is updated during the day.
  Hourly statistics are compiled a few minutes after each hour, so the hour just ended and the
  running hour come from 5-minute statistics. The running hour counts from 15 measured minutes,
  weighted by its measured share and compared with the clear-sky profile of the measured part;
  a change in the weather shows within half an hour instead of after the next full hour.
- With a weather entity as source, Open-Meteo is computed in parallel and archived, so the
  forecast error of both sources and of their mean can be compared before mixing them.
- Energy dashboard solar forecast.
- Diagnostics: shading-map coverage, last learning run, excluded hours with reason,
  forecast error of the last 30 days.
- Sidebar panel "Noxcast" (no dashboard setup needed): what the system has learned, to check
  whether it can be right. An overview with all arrays (shade, reflection, direction factor as
  small maps and key figures) leads to one array: the full map over sun azimuth and elevation with
  sun paths, a date slider that moves the sun path through the year (with the day as a timeline),
  details per cell (values, when the sun is there) and a year grid of month × hour for those who do
  not think in sun angles. Follows the Home Assistant theme (light and dark). Data comes from the
  websocket command `noxcast/learned`, not from attributes, so the maps do not grow the database.
  Further sections are tabs in the panel header, each a module of its own:
  - **Forecast accuracy** (`noxcast/accuracy`, from the day-ahead archive): daily error and bias over
    14 / 30 / 90 days, per array or in total, per weather source (main, Open-Meteo, mean of both);
    forecast against actual per day, a calendar coloured by error, the hourly course of a chosen day
    (hours with a full battery hatched), the error by weather class (forecast irradiance relative to
    clear sky) and the sources compared on the same days.
  - **Load** (`noxcast/load`): rest of today and tomorrow, the correction now, the backtest against a
    7-day mean; the forecast by part (base load and each consumer, stacked per hour), the learned
    profile (workday, weekend, fixed consumers) and the consumers with their timing, sensor and share.
  - **Battery & surplus** (`noxcast/battery`): the simulation until tomorrow's charge start (state of
    charge, full and empty times, surplus without room, grid); a what-if on the same forecast until
    tomorrow midnight with another capacity and an additional consumer (power, duration, start),
    compared with the plan, including the share of the consumer covered by PV and battery; the last 30
    days as lowest and highest state of charge per day from the long-term statistics, with the days
    full and the days at the lower limit. The what-if runs in the browser with the same rules as
    `battery.simulate`.
  - **Diagnostics** (`noxcast/diagnostics`): weather source, last learning run and archive at a glance;
    notes on missing or unavailable sensors, missing statistics, low statistics coverage (last 30
    days), learning and archive errors; every sensor Noxcast reads with role, state, state class and
    coverage; learned arrays and excluded periods.
- Day card `custom:noxcast-card` (registered as a Lovelace resource on start): the power flow now
  (PV, grid, home, batteries; inactive paths dimmed, PV to battery never through the home; lines
  have one width and the speed of the dots shows the power, so no line covers another; a charging battery takes PV before the grid; the house icon is coloured
  by its sources PV, battery and grid in proportion to their share; Home Assistant icons (battery icon by
  state of charge, configurable), arrows instead of signs, tapping an element opens its more-info dialog;
  hovering or tapping an hour of the chart shows its values; with the load forecast, batteries and
  flexible consumers configured in Noxcast the card shows their results instead of its own estimate)
  and the
  calendar day from sunrise to sunset on one time axis:
  - PV on a fixed kW scale (installed peak power + 10 %, so a weak day shows as a low curve): the expectation pale, the part already past (= actual) solid, the forecast
    as a dashed contour. Hours ahead follow the latest forecast; an hour keeps its value once it has
    begun, so a deviation stays visible instead of being replaced by the actual value. The shade
    loss is hatched below the potential without shade.
  - House load per hour (measured, then the mean of the last 7 days), capped at the PV scale;
    hours whose mean is cut off by the scale are marked once per run with their highest mean. Blue where the load is above the generation.
  - One lane per array: shade periods always, reflection where it adds max(15 Wh, min(50 Wh,
    10 % of the day)) or more. An effect is the forecast with vs without the learned part under
    today's weather.
  - Battery level (measured, then simulated between the configured min/max limits): red where it
    sits at the upper limit with surplus (lost), orange within 5 % of it. The night is not drawn
    on the time axis; end caps show the level at the start of charging today and the expected
    level at the start of charging tomorrow (or when it runs empty).
  - A planning line (surplus until, battery full, unused kWh; in the evening whether the battery
    lasts until tomorrow) and the three numbers actual, expected and morning forecast; after
    sunset actual against the morning forecast. A legend lists only what the chart shows (`legend: false`
    hides it). A visual editor covers every option: text size of the flow (`font_scale`, 0.8 to 1.6; the flow numbers are larger than the rows below), legend, main
    entities, PV groups and batteries with their limits. The card reloads every 5 minutes, when it becomes
    visible again and after midnight.
  Data from the websocket command `noxcast/today`, live values from the configured entities.
- Shading map per array as image entity (SVG): learned transmission over sun azimuth and
  elevation, sun paths (solstices, equinox, today) and key figures; usable in any dashboard.
- Shade today per array: today's sun path through the shading map in 10-minute steps. A sensor
  reports the shaded minutes (transmission below 0.8) with the shaded periods (start, end, lowest
  transmission) and the periods not learned yet as attributes; an image entity shows the day as a
  timeline with the current time. Times are accurate to about a quarter of an hour (cell size).
- Forecast archive: the day-ahead forecast (with the weather it used) is kept for 120 days and
  completed with actual values during the nightly run. Weather forecasts are not recorded by
  Home Assistant, so this is the only basis for evaluating and calibrating against the forecast
  source (e.g. the sky view).

## Architecture

- One integration, `noxcast`. PV generation is mandatory; load and planning are optional
  parts enabled via config subentries.
- Internal modules: `pv/`, `load/`, `plan/`, plus shared weather access, storage, backtesting
  and diagnostics.
- Stable public interface: versioned entity IDs and attribute schema.
- Transparent storage: learned parameters as readable, versioned files; diagnostic export.
- Backtesting harness: replay history as if forecasting at that time. Every model change must
  prove itself there; users see how good the forecast is for their system.
- Special cases: east-west roofs, vertical and bifacial modules, several arrays on one
  inverter, daylight saving time.

## Load forecast and surplus (planned)

Status: step 1 built (base-load profile, consumers with fixed / flexible timing and a power or energy
sensor, running programs until their remaining time ends, correction k,
load sensors for the rest of today and tomorrow, backtest) and step 2 (battery subentries, simulation
until tomorrow's charge start, surplus and battery sensors) and step 3 (flexible consumers: run profile
learned from history or configured, best window today and tomorrow, PV share if started now) and
the kinds of step 4 (fixed consumers as profile, program with learned start rates, runs per day and
run curve, or session with on-probability and remaining hours by time of day; the backtest reports
the bias next to the absolute error); day types from a sensor and the rest are concept. Today the day card computes a simple version in the browser
(7-day mean per hour, battery simulation until tomorrow's charge start); it only exists while a
dashboard is open, so automations cannot use it. The model below moves into the integration and
the card only reads its sensors.

Two user types share the planning problem with different objectives:

- **Tight on PV:** the battery must not be full before production ends; flexible consumers should
  absorb the peak. Plan with P50 / P90.
- **Plenty of PV:** avoid running consumers in weak periods to avoid grid draw. Plan with P10.

### Consumers: kind and timing

Every consumer is described by its behaviour, not by the device. Two independent properties:

**Kind** (how it draws power):

| Kind | Examples | Shape | While on | While off |
|---|---|---|---|---|
| Base load | fridge, router, standby, light | continuous, varies by hour | – | profile × k |
| Program | washer, dryer, dishwasher | one run with an end, energy per run roughly fixed | remaining energy (from remaining time, otherwise the learned run duration) | – (see timing) |
| Session | PC, TV, console | on, then open-ended, fluctuating power | current power until the learned end, conditioned on the start time | learned on-probability per hour × typical power |
| Regulated | air conditioning, heat pump, heating rod | power follows an outside quantity | current power, damped | learned relation to the temperature forecast |

**Timing** (when it runs):

- `fixed`: runs regardless of surplus (dishwasher after dinner, PCs). Part of the load forecast.
- `flexible`: runs when it makes sense; the forecast is used to choose that moment (the washer
  usually has no plan of its own). Not part of the load forecast unless it is running; an output
  of the planning instead.
- `surplus_only` (later): only runs on surplus (heating rod with surplus control). Consumes the
  surplus after the forecast, never predicted as load.

A session's end is learned, not assumed: in one household PCs stay on until bedtime, in another
they run for two hours. The model learns the expected remaining duration given the time of day
the session is observed ("on at 14:00, median 9 h left").

### Learning

- **Base load** = total house load minus all configured consumers, median per hour and day type
  from 4 to 8 weeks of recorder statistics (median, so rare runs and spikes do not bend it).
  Past runs of flexible consumers are removed as well, otherwise they would reappear as "midday
  load" and the forecast would anticipate the very surplus they used.
- **Short-term correction k:** ratio of the cleaned base load of the last 4 h to the profile of
  the same hours, clamped (0.35 to 1.3), decaying towards 1 with the distance to the target hour
  (half-life about 1.4 h). Proven in a template prototype: a running large consumer no longer
  inflates the rest of the day, and a low night does not scale down the midday.
- **Program:** energy per run, duration and power curve over the run (so the heating phase of a
  washer lands at the start). For `fixed` programs also the start-time distribution and runs per
  day (median and maximum, so a third run is not predicted after two). Built as an expected value:
  each hour carries its start rate (starts per day in that local hour and day type) times the run
  curve; per day the expected starts are capped at the most runs seen on one day minus the runs
  already done today. The expected value is deliberately not the median: a dishwasher on one evening
  in three has a median of zero, which minimises the absolute error of a single day but leaves its
  energy out of every battery simulation. The backtest therefore reports the bias as well.
- **Session:** on-probability per hour, typical power, remaining duration by time of day. While on,
  the current power counts for the median remaining hours seen at that time of day; afterwards and
  while off, on-probability times typical power.
- **Regulated:** power against temperature as a hinge (threshold plus slope), per hour; dropped
  when no relation is visible.
- **Day types:** weekday / weekend by default; optionally any sensor as key (presence, holiday
  switch), profiles are then learned per state. Later: day types from the data itself, with the
  morning load deciding which type today is.
- Outlier days (guests, outage, holiday without a sensor) are detected and excluded, as for PV.

### Forecast and battery simulation

```
load(h) = base_profile[day type, h] × k(h)
        + Σ fixed programs   (running: remaining energy; otherwise learned starts, capped)
        + Σ sessions         (on: current power until learned end; off: probability × power)
        + Σ regulated        (temperature forecast)
        + Σ running flexible programs (remaining energy only)
```

The battery is simulated hour by hour from now until tomorrow's charge start (first hour with PV
above load), not until midnight, so "battery empty at" is part of the result:

- Limits per battery (`max_soc` / `min_soc`, number or entity, plus an optional reserve in percent
  points above `min_soc` where discharging stops in practice, e.g. a feed-in stop rule), capacity per battery.
- Charge and discharge power limits: above them surplus is fed in or drawn from the grid although
  the battery still has room or charge.
- Round-trip efficiency by coupling: DC-coupled batteries lose little (default about 95 %),
  AC-coupled ones more (about 90 %); configurable. Inverter losses are already inside the
  measured PV and house load and are not counted again.

### Planning flexible consumers

The forecast gives the surplus without flexible consumers. Each flexible consumer has a run
profile (energy, duration, power curve: learned from its sensor or entered by hand / from a
template). Planning places runs into that surplus, counting only what would not go into the
battery anyway:

- best start window today, with its PV share ("11:00–12:30, 95 % from PV");
- PV share if started now (for a notification or a smart start);
- whether today is worth it or tomorrow is clearly better.

Several flexible consumers are placed one after the other by a configured priority, so washer
and dryer are not both offered the same midday hour.

### Without own sensors

The model degrades in steps; each step works on its own and every sensor adds precision.

0. **House load only** (or grid + PV + battery): one profile from the total load (median per hour
   and day type) with k. Flexible planning still works with run profiles entered by hand.
   A running device only shows as higher actual load, which k carries for the next hours.
1. **Events from the total load** (later, optional): steps above a threshold that hold for a while
   and fall again are clustered by power, duration and energy. They are removed from the base
   profile, offered as suggestions ("device ~2.1 kW, ~1.5 h") to become a run profile, and a live
   step that matches a cluster counts as a weak "running". Equally strong devices cannot be told
   apart; sessions are too small and noisy and stay in the base load.
2. **Individual sensors:** each configured consumer leaves the base load and brings its own
   behaviour; a day-type sensor splits the profiles.

### Outputs

- Surplus today: total, into the battery, lost without intervention, lost after the plan.
- Lost from (time), highest state of charge and when, battery empty at, remaining load today.
- Per flexible consumer: best window, PV share now, PV share in the best window.
- Attributes with the contribution of every term and the data basis ("base load from total load,
  3 consumers separated, day type weekday, 28 days of data"). With little data the result is a
  band ("lost 0.4–1.2 kWh") instead of a falsely precise number.
- Entity IDs and attributes follow the versioned public interface; they are fixed before the
  first release of this part.

### Verification

- Backtest from the recorder: for every hour of past weeks, forecast with the knowledge of that
  moment and compare with what happened (error of the remaining load, of the lost surplus, hit
  rate of the warning level).
- Parallel run against an existing template solution; switch over only when Noxcast is measurably
  better.

### Order

1. Base-load profile with consumer configuration and backtest.
2. Battery simulation and sensors; the card reads them.
3. Flexible planning.
4. Sessions, fixed programs with learned starts (built), day types from a sensor (open).
5. Temperature (regulated consumers).
6. Event detection without sensors.

The generation part must provide uncertainty bands, 15-minute values and intraday updates for
this.

## Prototype findings (one installation, Oct 2026)

- Shading maps derived from ten months of hourly data show clear, physically consistent
  patterns (morning shading at low sun elevation on a balcony array, an obstacle to the east
  for a roof array).
- Excluding curtailment hours changed results noticeably only for affected months; the
  90th-percentile approach is robust against occasional curtailment.
- First day-ahead comparison: physics + shading map + DWD irradiance was within 3 % of the
  daily actual on an overcast day; an existing ML-based integration overestimated by 84 %.
  One day is anecdotal; systematic backtesting is the next step.

## Roadmap (agreed next steps)

Done: temperature loss (learned coefficient or datasheet value, fixed heating), the optional
satellite filter with configurable sources and the sky view per array (learned against satellite
irradiance; whether the forecast source's overcast irradiance matches is checked with the forecast
archive).

1. **Geometry check** as diagnostic: tilt and azimuth that fit clear unshaded hours best, with a
   hint on clear deviations. Never applied automatically, since the shading map depends on the
   geometry. Steep arrays are sensitive to azimuth; flat ones are not, so the hint needs a
   confidence measure.
2. **Batteries as subentries:** state-of-charge sensor, charge limit (entity with history, or a
   fixed value to be updated by the user), assignment to the arrays it charges. An hour counts
   as possibly curtailed for those arrays when its maximum state of charge reaches the limit
   within a small tolerance. The maximum is robust against saw-tooth charging when MPPTs
   throttle in steps. A plateau-only detection is not used: a constant state of charge can also
   mean PV exactly covers the load.
3. **Optional power limit per sensor** (e.g. 500 W per MPPT): forecast capped per sensor;
   hours near the limit are excluded from learning.
4. **Finer shading map from 5-minute statistics** (kept ~10 days by the recorder) for narrow,
   moving shadows such as masts or chimneys; aggregates per cell are stored because the raw
   data expires.
