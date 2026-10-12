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
| Diffuse factor (obstructed sky) | overcast days | rarely |
| Weather forecast correction and uncertainty | all hours, forecast vs. actual | continuously |

Only the last part is "machine learning" in the narrow sense, and even there conditional
quantile tables or a small gradient-boosting model are sufficient. Geometry and shading are
statistics on top of physics (clear-sky model, plane-of-array irradiance, incidence angle losses).

### Calculation chain

The forecast is not "clear-sky output × cloud factor". Clouds change the *kind* of light, so
the weather enters first and every later step works on its result:

1. **Weather:** hourly irradiance, split into direct (beam) and diffuse.
2. **Geometry:** both parts projected onto the module plane (incidence-angle losses on beam).
3. **Shading:** learned transmission applied to the **beam part only** – on an overcast day a
   morning shadow costs almost nothing, on a clear day it costs everything.
4. **Diffuse factor:** share of the sky not obstructed by surroundings.
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
- **Regressions over many hours** (diffuse factor, temperature loss) may use satellite irradiance
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
  (measured hours + corrected rest) and the remaining energy. The intraday correction applies the
  actual/forecast ratio of the last three hours to the coming hours with a half-life of two hours,
  limited to 0.3–2.5: weather deviations persist briefly, then the forecast takes over again.
- With a weather entity as source, Open-Meteo is computed in parallel and archived, so the
  forecast error of both sources and of their mean can be compared before mixing them.
- Energy dashboard solar forecast.
- Diagnostics: shading-map coverage, last learning run, excluded hours with reason,
  forecast error of the last 30 days.
- Shading map per array as image entity (SVG): learned transmission over sun azimuth and
  elevation, sun paths (solstices, equinox, today) and key figures; usable in any dashboard.
- Forecast archive: the day-ahead forecast (with the weather it used) is kept for 120 days and
  completed with actual values during the nightly run. Weather forecasts are not recorded by
  Home Assistant, so this is the only basis for evaluating and calibrating against the forecast
  source (e.g. the diffuse factor).

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

## Later: surplus and grid draw

Two user types with the same planning problem and different objectives:

- **Tight on PV:** the battery must not be full before production ends; start consumers early
  enough to absorb the peak. Plan with P50 / P90.
- **Plenty of PV:** avoid running consumers in weak periods to avoid grid draw. Plan with P10.

Requires a load forecast (base-load profile per weekday and hour, large consumers), a battery
simulation and outputs such as "latest start" windows. The generation part must therefore
provide uncertainty bands, 15-minute values and intraday updates from the start.

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

Done: temperature loss (learned coefficient or datasheet value, fixed heating) and the optional
satellite filter with configurable sources.

1. **Diffuse factor per array**, learned from overcast hours: share of the modelled sky light
   the array really receives (obstructed sky, e.g. a wall behind the modules or a balcony above).
   A first check against satellite irradiance showed only 50–67 % of the modelled diffuse output
   in overcast hours on one installation. Validate against the forecast source (e.g. DWD) before
   building, because the factor must match the irradiance the forecast uses. Not direction
   dependent: the shading map already covers direction-dependent losses.
2. **Geometry check** as diagnostic: tilt and azimuth that fit clear unshaded hours best, with a
   hint on clear deviations. Never applied automatically, since the shading map depends on the
   geometry. Steep arrays are sensitive to azimuth; flat ones are not, so the hint needs a
   confidence measure.
3. **Batteries as subentries:** state-of-charge sensor, charge limit (entity with history, or a
   fixed value to be updated by the user), assignment to the arrays it charges. An hour counts
   as possibly curtailed for those arrays when its maximum state of charge reaches the limit
   within a small tolerance. The maximum is robust against saw-tooth charging when MPPTs
   throttle in steps. A plateau-only detection is not used: a constant state of charge can also
   mean PV exactly covers the load.
4. **Optional power limit per sensor** (e.g. 500 W per MPPT): forecast capped per sensor;
   hours near the limit are excluded from learning.
5. **Finer shading map from 5-minute statistics** (kept ~10 days by the recorder) for narrow,
   moving shadows such as masts or chimneys; aggregates per cell are stored because the raw
   data expires.
