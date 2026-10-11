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
- Energy dashboard solar forecast.
- Diagnostics: shading-map coverage, last learning run, excluded hours with reason,
  forecast error of the last 30 days.

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
