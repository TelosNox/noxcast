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
| Shading contour (obstacle edges per sun azimuth) | clear-sky hours, only where direct light is geometrically possible | rarely (tree, new building) |
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
between e.g. a freely hanging balcony array and modules close to a roof:

    T_cell = T_air + heating · irradiance on the plane / 1000 W/m²
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
diffuse model, low-light behaviour).

The heating is set per array (default 30 K, free behind). Typical values: balcony or rack
about 30 K, parallel on a tiled roof 45–55 K, integrated into the roof about 60 K (PVsyst uses
U = 29 / 20 / 15 W/m²K for free, semi-integrated and insulated mounting). On steady clear hours of
one installation the balcony fitted 30 K, while glass-glass full-black modules close to dark roof
tiles needed about 50 K: with 30 K the roof's output fell with irradiance and rose by about
1.2 % per m/s of wind, both signs of a hotter module, and the excess showed up as a direction
factor below 1 at near-normal incidence. Learning the heating automatically was tried and is not
robust: halves of the same data gave different values, and seasonal effects (air temperature,
haze) correlate with irradiance. Wind is not used. Air temperature comes from the weather forecast
(15 °C if missing); for learning from history it comes from the reanalysis archive.

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
- **Steady hours only** for efficiency, direction factor, reflection and the clear-hour median per
  shading cell, when the power sensors' hourly minimum and maximum are known (long-term
  statistics keep them): a sensor within ±15 % of its hourly mean. A 5 km satellite pixel
  called many hours clear while clouds passed over the installation; on one installation only
  a quarter to a third of the "clear" hours were steady. The others carried cloud-edge peaks cut at the
  inverter's 500 W input limit, restarts and dropouts, and they spread the direction factor to
  0.97–1.15 and pulled the efficiency median down by 2.5 % on the balcony. With steady hours the
  direction factor stayed within about ±5 % and the balcony's daily hindcast error dropped from
  5.8 % to 5.4 % (bias −2.2 % → +0.8 %); the roof stayed at 5.8 %. With fewer than 30
  steady clear hours (new installation, sensors without statistics) all clear hours are used.
- **Several sensors per array** (one per module or input) tell a cloud from a shadow: a cloud
  reaches all of them, a shadow edge often only one. An hour counts as steady when at least one
  sensor was steady, or when the sensors are far apart relative to their own peaks (below 60 %
  of each other: one part shaded). A steady test on the sum alone dropped exactly the hours with
  a shadow edge, so these cells kept the 90th percentile over all hours and stayed too bright: on
  a roof whose upper module lies in the shadow of a wall in the morning, the clear-hour forecast
  at sun azimuth 90–120° was 28 % too high and became 14 % too high; on the balcony the
  afternoon (azimuth 240–300°) improved from −9/−17 % to 0/−7 % bias. Daily errors hardly moved
  (balcony 5.4 → 5.5 %, roof 5.7 %): these hours carry little energy. Hours with the sun behind
  the plane are still too high on that roof (diffuse light from the bright side of the sky is
  blocked by the house behind the wall); the isotropic diffuse model cannot show that.
- **Shadow edges within the hour.** A per-cell estimate gives each hour to the cell of its
  mid-hour sun position; an edge crossed during the hour is smeared over about one and a half
  cells, and the forecast, which integrates over 10-minute sub-steps, smears it again. On steady
  clear hours the shading is therefore fitted so that the forecast integrated over the sub-steps
  meets the hourly means (see the contour below). The sun path of a clock hour shifts across the
  edge from day to day, which places it within the hour – from the hourly history, no finer data
  needed. Only hours whose expected light is at least 70 % direct take part: with the sun nearly
  behind the plane, errors of the diffuse model would otherwise darken the map (balcony
  afternoon bias went from 0 to −14 % without that limit). With free cells instead of the contour
  this gave, on the installation above, roof at sun azimuth 90–120° from +14 % to 0 % bias (error
  23 → 15 %), held up when fitting on even and checking on odd days and vice versa; balcony daily
  error 5.5 → 5.4 %. A smoothing term between neighbouring cells was tried and dropped: it blurred
  a sharp synthetic wall edge and did not help on real data.
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

### Shade and other effects are kept apart: the contour

A real obstacle has a shape: a building or the terrain shades low sun, an overhang high sun, and
the edge moves smoothly across the sky from day to day. Temperature, haze and model errors lower
values without such a shape.

**Why not free cells.** The first map had one free transmission per cell (10° azimuth × 5°
elevation). Near noon one hour covers about 15° of azimuth, so neighbouring cells are not
separable from hourly means, and the fit produced zigzag artefacts: on a steep south-east array
(tilt 60°, azimuth 135°) the sun path at 25–30° elevation was learned as 1.00 / 0.57 / 0.85 /
0.39 / 0.89 from 160° to 200°, while the cells below (20–25°) were free. There is no obstacle
there; midday output was 1.35–1.55 × the forecast. A ground obstacle cannot shade 25–30° and leave
20–25° free. Smoothing between neighbouring cells blurred a real roof shadow in cross-validation,
and a finer grid does not help, since the information limit is the hourly mean.

**The contour.** Per azimuth column (10°) the map holds at most two edges:

- a **lower edge** – ground obstacles (wall, house, tree, terrain): shaded below it, free above;
- an **upper edge** – an overhang (balcony roof, the floor above): shaded above it.

Each shaded segment has a transmission between 0 (wall) and 1 (above 0: tree, railing), so partial
shade stays possible. Without an edge the column is free. The forecast interpolates linearly
between column centres; an edge is soft over ±1°, so the sub-step integration stays smooth.
Elevations never visited follow the edge (below a lower edge shaded, above it free). A column
without enough data has no decision and counts as free – unless both neighbours have an edge, then
it takes their mean.

**Fit.** The same equations as before: every steady clear hour is the sun path over its 10-minute
sub-steps, with the measured satellite irradiance, efficiency and temperature factor. The beam and
diffuse light are split exactly as in the forecast (Erbs, from the measured GHI distributed over the
sub-steps with the clear-sky shape), so the contour corrects the forecast's own chain like the
direction factor does: a clear-sky beam that is too high at low sun would otherwise appear as a
shadow there (in a synthetic year with this mismatch, a fit on the clear-sky split marked 17–25 %
of the free sun positions as shade, with cells as well as with the contour; on the forecast's
split the contour marked none). The unknowns are the edges and transmissions per column:
columns are fitted one after the other with the others fixed (a few sweeps), each by a search over
the edge elevation in 0.5° steps with a least-squares transmission per step; a robust reweighting
damps hours that do not fit (haze or a cloud the filters missed). Pure Python, no dependencies.

**Model selection per column.** An edge is introduced only when it lowers the robust squared error
clearly against "free": by 25 noise variances and by 20 % of the column's error, so noise does not
create shadows. Most of the hours entirely on the shaded side must be dark (closer to the segment's
transmission than to free sky): a lone dark spot with free sky below it cannot pass as a lower edge
whose transmission is a compromise between the spot and the free sky. Transmissions from 0.9 count
as free (dead band); a segment of 0.7–0.9 needs free sky observed on the other side of its edge,
since a whole column slightly darker is model error, not shade. Without steady clear hours (no
satellite data or power statistics) the contour is fitted the same way to the per-cell estimates.

**What the contour cannot represent.** Objects with free sky below them, such as a tree crown above
a bare trunk or a cable, a gap between two houses narrower than a column, more than one shaded
band per column (a railing below and an overhang above are fine, a railing with a gap in it is
not), and side edges sharper than the 10° column width. Such shade is approximated by the nearest
contour or not learned; what remains at unshaded positions goes to the direction factor.

What remains at unshaded positions on clear hours goes into a separate, coarse direction factor on
the beam part (20° × 15° bins, median, 0.8–1.2). It may exceed 1 and covers geometry and model
errors. Light reflected by the surroundings is learned apart from it (see below). Both are learned
against the measured irradiance, split into beam and diffuse exactly as in the forecast, so that
they correct the forecast's own chain and not a clear-sky estimate. The map shows shade only; the
direction factor is reported as a key figure.

Two modules of the same array that deliver equally at a sun position rule out partial shade
there; diverging modules confirm it. The roof analysis showed exactly that: the wall in the east
shades one module more than the other, while the reduced output at high south-west sun was equal
on both modules and is therefore no shade – it turned out to be module heat (see Temperature).
Using per-module sensors as shade evidence is a later step.

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

### Several orientations in one array

An array is what is measured together: one or more power sensors whose sum is learned and
forecast, typically one MPPT input. Sometimes the modules on such an input face different ways,
for example 3 × 400 W at 30° tilt facing east, south and west in parallel, or vertical modules on
both sides of a railing. Only their combined power can be measured. An array may therefore have
up to three further orientations, each with tilt, azimuth and its own peak power; the main fields
are then the first orientation, and the array's peak power is the sum.

**Physics per plane.** Beam and diffuse are computed for each plane on its own and summed,
weighted by each plane's share of the peak power. Each plane is clipped at zero by itself: when
the sun is behind the west modules they get no beam, and they do not take any from the east
modules. The rest of the chain (shading, direction factor, reflection, temperature, efficiency)
works on that sum.

**Why not one equivalent plane.** Summing the module normals (weighted by peak power) gives one
plane with the same response as long as the sun is in front of every module: the projection of
the sun direction onto a sum of normals is the sum of the projections. For east + south + west at
30° the equivalent plane has 10.9° tilt facing south and a factor |Σn| / 3 = 0.882. From about
30° sun elevation it is exact, but at low sun one of the real planes is behind the sun and
contributes zero, not the negative share the vector sum subtracts. Real over equivalent (beam
only, pure projection):

| Sun elevation / azimuth | E + S + W, 30° | E + W, 30° |
|---|---|---|
| 10° / 90° | 1.76 | 2.14 |
| 10° / 135° | 1.25 | 1.66 |
| 20° / 90° | 1.20 | 1.29 |
| from 30° (sun in front of all planes) | 1.00 | 1.00 |

For east + west at 30° the equivalent plane is flat with a factor 0.87; for vertical east + west
modules the normals cancel and no equivalent plane exists at all. Incidence-angle losses make the
low-sun error larger still, since grazing light on the equivalent plane loses more than near-normal
light on the plane the sun faces. Entering only one of the real orientations is worse in another
way: the shading map would then learn the missing morning or afternoon light of the other modules
as "shade" (a transmission of about 0.5 in a simulation of east + south + west), and the direction factor and the
reflection would absorb the rest. Hiding a geometry error in learned parameters contradicts
"physics first", so the planes are modelled.

**Learning is unchanged.** Efficiency, sky view, temperature coefficient, shading map, direction
factor and reflection stay per array and keep their structure. They depend on the sun position,
and for a given sun position the split of beam between the planes is fixed (it depends only on
the incidence angles), so the summed response is a unique function of the sun position just as
for one plane. A shadow that reaches only the east modules is learned as the share of the array's
direct light it removes at that sun position, which is exactly what the forecast needs. The
reflection is learned where the summed beam on the modules is below 30 % of the direct normal
irradiance, as before. The shading map is drawn as behind the modules only where the sun is behind
every plane, and each plane's normal is marked on it.

**Temperature per plane.** Each module heats with its own irradiance. Since the temperature
factor is linear in the irradiance, Σ share · f(POA_i) · POA_i equals f(POA) · POA of the weighted
mean irradiance when the heating is scaled by Σ share · POA_i² / POA² (at least 1). The planes'
irradiance for this ratio comes from the model without shade, direction factor and reflection,
which are learned per array, not per plane. One common cell temperature from the mean irradiance
would overestimate a clear day by 1–2.6 % (hourly up to about 4 %) for east + south + west,
east + west and vertical east + west at 50° N, with a time-of-day pattern the efficiency cannot absorb.

**Geometry check.** The panel's tilt / azimuth search fits one best plane. For an array with
several orientations it is skipped: one plane does not describe it, and shifting one of its planes
would mostly fit learned shade or direction factor. The panel lists the configured planes instead.

**Compatibility.** An array without further orientations is computed exactly as before (bit for
bit), and its learned state and geometry key stay valid. Adding, changing or removing an
orientation changes the geometry key and starts relearning.

## Requirements

- **Primary measurement required:** power per array, preferably DC per MPPT. Systems whose
  inverter clips systematically (e.g. 800 W inverter on 2 kWp) are rejected during setup:
  if more than ~20 % of clear midday hours sit on a plateau, geometry, efficiency and shading
  cannot be learned. Occasional clipping is detected and excluded from learning.
- **Open-Meteo as the only weather source** (GHI / DNI / DHI, free for non-commercial use, no key).
  Global irradiance is split into direct and diffuse (e.g. Erbs) if only GHI is available.
  - Several models are averaged hour by hour (GHI, DNI and DHI alike, so the mean stays
    consistent); default ICON (`icon_seamless`) + ECMWF IFS (`ecmwf_ifs`), changeable under
    advanced options. A day-ahead hindcast over 230 days (12/2025–10/2026, one installation,
    learned model, previous-runs API) gave 10.0 % daily error for that mean against 11.7 % (IFS,
    bias +5 %) and 12.6 % (ICON / Open-Meteo default, bias −7 %): the two err in opposite
    directions. Only the mean was significantly better (bootstrap); single models were not.
  - Weather entities were dropped as a source: the common ones (met.no, the Open-Meteo
    integration) carry no irradiance, and DWD MOSMIX irradiance was clearly worse in the archive
    (31–36 % against 16–17 % daily error over the same days).
  - The last answer is stored; when Open-Meteo cannot be reached it stands in for up to 24 h,
    also across a restart.
  - **Own weather entity** from the same averaged values, for a week regardless of the PV horizon,
    so dashboard weather and expected yield agree; the cards use it by default. The condition is
    derived from the averaged numbers rather than a weather code, which cannot be averaged:
    precipitation ≥ 0.1 mm/h is rain (≥ 4 mm pouring; snow at ≤ 0 °C, sleet up to 2 °C), otherwise
    cloud cover < 20 % sunny / clear night, < 70 % partly cloudy, else cloudy; wind ≥ 10.8 m/s
    windy. Only thunderstorms (any model) and fog (first model) come from the weather code. A day
    is judged on its daylight hours, rain on its sum (≥ 1 mm).
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
- **Ageing:** recent clear hours weigh more in the shading fit (half-life around one year) to
  follow slow changes such as a growing tree.
- **Change detection:** if several new clear observations in a cell deviate consistently, the
  per-cell estimate is replaced rather than averaged (a felled tree disappears within days); this
  applies where the contour is fitted to the cell estimates (no steady clear hours).
- **Efficiency drift** raises a hint (soiling / degradation) instead of being absorbed silently.

## Data filters

- Curtailment: full battery with zero export, export limits, inverter AC limit (learned as a
  plateau and applied as a cap in the forecast).
- Snow: zero output under clear sky and frost.
- Outages: `unavailable`, all-zero days, incomplete hours.
- Unsteady hours: hourly minimum or maximum of every power sensor of an array more than 15 % from
  its mean (passing clouds, peaks cut at the inverter limit, restarts, telemetry dropouts) are
  left out of efficiency, direction factor, reflection and the clear-hour median of the shading
  map – unless the sensors disagree strongly, which is a shadow on part of the array.
- Time reference: an hourly mean belongs to the sun position at the middle of the hour.

## Outputs

- Per array and total, 15-minute and hourly resolution, today / tomorrow / day after.
- Remaining energy today, peak time, P10 / P50 / P90. The band is empirical (`band.py`): from the
  day-ahead archive, the ratio actual / forecast per day and per hour, grouped by the clearness of
  the forecast (overcast below 0.35, mixed, clear from 0.7; forecast GHI relative to clear-sky GHI),
  gives the 10th and 90th percentile; a class with fewer than 8 days (40 hours) falls back to all
  days together, and hours with possible curtailment are left out. Applied to today's rest,
  tomorrow and the day after (attributes `p10` / `p90` of the total sensors), per hour in the day
  card, and to the battery simulation (lost surplus and full time with PV at P10 and at P90).
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
- With several weather models, each one is also computed on its own and archived next to their
  mean (no extra request: Open-Meteo returns all models in one answer), so each model can be
  compared with the mean on the same days.
- Energy dashboard solar forecast.
- Diagnostics: shading-map coverage (share of the cells the sun reaches within a year that lie in
  a column with an edge decision, within the elevations the column's data covers; the attribute
  `shading_cells` counts the cells derived from the contour for display), last learning run,
  excluded hours with reason,
  forecast error of the last 30 days.
- Sidebar panel "Noxcast" (no dashboard setup needed): what the system has learned, to check
  whether it can be right. An overview with all arrays (shade, reflection, direction factor as
  small maps and key figures) leads to one array: the full map over sun azimuth and elevation with
  sun paths, a date slider that moves the sun path through the year (with the day as a timeline),
  details per cell (values, when the sun is there) and a year grid of month × hour for those who do
  not think in sun angles. Follows the Home Assistant theme (light and dark). Data comes from the
  websocket command `noxcast/learned`, not from attributes, so the maps do not grow the database.
  Further sections are tabs in the panel header, each a module of its own. One shared stylesheet
  (`noxcast-theme.js`, adopted into the panel and every section) gives all pages the same look:
  a page header with title and lead, frosted cards with a gradient edge, key figures as tiles.
  - **Day** (`noxcast/day`, offset −120 … +2 days): one day in detail. The groups stacked (actual
    where measured, the expectation or forecast for the rest), the reference as a dashed line (the
    6 o'clock forecast today, the day-ahead forecast for past days), the running forecast and the
    P10–P90 band; a weather strip (clearness of each hour, temperature) and today's shade and
    reflection periods per group. A table per group with its deviation, correction and share.
    For a chosen hour a waterfall shows how its forecast comes about (`explain.py`): clear sky with
    the learned efficiency, then clouds, shade, reflection, direction factor and heat, the steps
    adding up to the forecast, followed by today's correction and the actual value. Past days come
    from the day-ahead archive. At every full hour from 5 to 21 o'clock the expectation for the
    whole day (all arrays) is recorded in the archive (`history`); the day view draws these
    snapshots as a fan of curves from early to late next to the day-ahead forecast, and the day
    total of each snapshot over time, so it shows when the day became foreseeable and where the
    forecast jumped.
    "The day in energy" shows two Sankey diagrams. The loss side sums the forecast steps over all
    hours: clear sky, losses branching off (clouds, shade, heat), gains joining (reflection), the
    forecast, and the deviation to the actual value (the expectation today). The use side
    (`flows.py`, past days and today) estimates hour by hour from long-term statistics where the
    energy went: PV, battery discharge and grid into house, battery charge and grid. The battery
    energy of an hour is the change of its mean state of charge (central difference) times its
    capacity; within an hour PV supplies the house first, then the battery. Self-consumption and
    self-sufficiency follow from it. With the optional grid sensor (configuration, positive =
    import, can be inverted) import and export are measured instead of derived, and a missing
    house load follows from the balance PV + import − battery charge + discharge. Without a house-load sensor only PV into the battery and the
    rest are shown; without house load and batteries the use side is left out.
  - **Forecast accuracy** (`noxcast/accuracy`, from the day-ahead archive): daily error and bias over
    14 / 30 / 90 days, per array or in total, per weather source (the mean used for the forecast, each averaged model on its own);
    forecast against actual per day, a calendar coloured by error, the hourly course of a chosen day
    (hours with a full battery hatched), the error by weather class (forecast irradiance relative to
    clear sky) and each model against the mean on the days it was archived. With the satellite archive enabled, the
    deviation is split into a weather part and a model part ("Weather or model?"): the learning run
    computes the model once more for every past hour on the satellite irradiance and the measured air
    temperature (stored as `measured` per array, once, with the parameters of that night). Weather
    part = forecast − model on measured weather, model part = model on measured weather − actual;
    both add up to the daily deviation, and a verdict names the larger source of error.
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
    Prediction and reality: the first simulation after the morning fix is kept per day (store,
    30 days: full and empty time, highest state of charge, limits, the next charge start) and
    compared with the 5-minute statistics of the state of charge sensors (all batteries as one,
    weighted by capacity; within 2 percentage points of a limit counts as reached). Shown as a
    dumbbell per day with the mean deviation of the full and empty times and how often "full or
    not" was right. Short-term statistics are kept for about ten days, so older predictions have
    no comparison.
  - **Diagnostics** (`noxcast/diagnostics`): weather source, last learning run and archive at a glance;
    notes on missing or unavailable sensors, missing statistics, low statistics coverage (last 30
    days), learning and archive errors; every sensor Noxcast reads with role, state, state class and
    coverage; learned arrays and excluded periods.
- Day card `custom:noxcast-card` (registered as a Lovelace resource on start). Its sensors come from
  the Noxcast configuration (`noxcast/card_config`: PV arrays with their sensors, house load, grid,
  batteries with state of charge, capacity, limits and the optional charge / discharge power, and
  whether surplus is fed in). The card configuration changes the display only: what the day chart
  shows (battery forecast, shade and reflection, P10–P90, house load), icons, text size, and per PV
  group a display name, an icon instead of the name, or not listing it on its own (its power still
  counts). Sensors cannot be set in the card, so it never shows other values than Noxcast learns
  from; sensor settings of earlier versions are ignored and the editor offers to remove them. It shows the power flow now
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
  elevation, the learned edges as lines, sun paths (solstices, equinox, today) and key figures;
  usable in any dashboard. The cells shown (and those of the panel) are derived from the contour:
  the mean transmission over each cell.
- Shade today per array: today's sun path through the shading map in 10-minute steps. A sensor
  reports the shaded minutes (transmission below 0.8) with the shaded periods (start, end, lowest
  transmission) and the periods not learned yet as attributes; an image entity shows the day as a
  timeline with the current time. A shadow rising or setting at an edge is placed to a few minutes;
  a side edge (across azimuth) only to about a quarter of an hour (column width).
- Forecast archive: the day-ahead forecast (with the weather it used) is kept for 120 days and
  completed with actual values during the nightly run. Weather forecasts are not recorded by
  Home Assistant, so this is the only basis for evaluating and calibrating against the forecast
  source (e.g. the sky view). For daylight hours it also keeps what each source says besides
  irradiance – cloud cover, sunshine share of the hour, wind speed and direction, as far as the
  source has them – so other predictors can be tested on archived days: on one installation the
  sunshine duration of Open-Meteo matched sunny autumn days better than its irradiance, while
  total cloud cover was too pessimistic under thin high cloud; wind cools the modules by about
  1 % per m/s. These values are not used by the model. They add about 40 bytes per daylight hour
  and source, well below 1 MB over the 120 days.

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
the bias next to the absolute error) and step 5 (regulated consumers against the outside
temperature), day types from an entity and step 6 as suggestions of recurring device runs. Today the day card computes a simple version in the browser
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
- **Sustained load:** events lift the hourly mean, not the hourly minimum. When the median of
  the base-load floor (hourly minimum, consumers subtracted) of the last 3 h – 4 h from 14:00 on,
  so an afternoon of cooking does not count – exceeds its learned profile by more than
  max(150 W, 30 %), the difference is added flat to every remaining hour and k is computed on top
  of it. Recomputed with every update. Backtest on a summer with a split air conditioner
  (108 days, no temperature input): remaining-day error at 12:00 on air-conditioning days
  4.7 → 3.1 kWh, other days unchanged.
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
- **Regulated:** power against temperature as a hinge (threshold plus slope); dropped when no
  relation is visible. Built on daily values: daily energy against the daily mean air temperature
  from the temperature archive over a year (the relation needs the seasons), heating below or
  cooling above a threshold, whichever fits better (R² at least 0.3, 14 days). The forecast takes
  the day's mean forecast temperature and spreads the energy by the consumer's mean hourly shape; a
  running consumer starts at its current power and fades into the model within a few hours.
- **Day types:** weekday / weekend by default; optionally any sensor as key (presence, holiday
  switch), profiles are then learned per state. Built: the entity's state at local noon is the
  day type of that date (from the recorder history; today from the current state, later days by
  weekday). On / off map to workday / weekend, so the Workday binary sensor turns holidays into
  weekends; other states (an input_select "home office") become day types of their own, for the
  base profile, profiles, programs and sessions alike. Later: day types from the data itself, with the
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
   apart; sessions are too small and noisy and stay in the base load. Built so far as
   suggestions only: from the 5-minute house load of the last ten days (minus configured
   consumers), steps of at least 500 W above a moving baseline (a low quantile of the surrounding
   six hours) that hold for 15 minutes, allowing 5-minute pauses, are grouped by power (±25 %) and
   duration (±50 %); groups with three runs or more are listed with power, duration, energy and
   usual start time. Removing them from the base profile and live matching are not done yet.
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
4. Sessions, fixed programs with learned starts, day types from a sensor (built).
5. Temperature (regulated consumers, built).
6. Event detection without sensors (built as suggestions: `events.py`, shown in the load panel).

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

1. **Geometry check** as diagnostic (built): tilt and azimuth that fit clear unshaded hours best,
   with a hint on clear deviations. Never applied automatically, since the shading map depends on
   the geometry. The fit minimises the spread (interquartile range over median) of measured to
   modelled power on a coarse and a fine grid; the confidence comes from how much the spread drops
   and from the number of hours, so a flat array, where the azimuth barely matters, gets no hint.
   Shown in the panel together with a clear-sky balance per month (what shade, reflection, the
   direction factor and heat change on a cloudless mid-month day) and the efficiency per month.
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
