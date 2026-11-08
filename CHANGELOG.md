# Changelog

All notable changes to Noxcast are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor version can contain
breaking changes.

## Unreleased

### Fixed
- *Forecast today* of an array added after 6:00 followed every new weather forecast until midnight, and the total
  with it. It is now fixed at the array's first update; `fixed_at` shows that time, for the total the latest fix.
- Panel, Overview: the actual curve no longer runs out of the chart on the left, and the top scale value is no
  longer cut off; the scale now sits inside the right edge, without the zero, as on the dashboard card.
- Panel, Overview: the line under *Power now* shows the expected power of this hour, the value the chart draws,
  instead of the morning forecast.

### Changed
- Panel, Overview: *Expected today*, *So far* and *Rest of today* show two decimals; *So far* says until which full
  hour it counts, which is why it and *Rest of today* do not add up to *Expected today* during the current hour.
- Panel, Overview, and dashboard card: power is shown in W below 1 kW and in kW with two decimals above.

## [0.3.0] - 2026-11-08

All changes since 0.2.0, tested in the pre-releases 0.3.0b1 to 0.3.0b3 (their single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)). The first learning run after the update relearns
the geometry check and, with the satellite filter, the efficiency; nothing needs to be done.

### Added
- Panel: a new start tab *Overview* (*Übersicht*) with what is due today and in the days ahead: today expected, so
  far against its forecast, rest of today and PV power now with the day's course; for flexible consumers whether to
  start now and the best windows; the battery's state of charge, full and empty times and lost surplus; the next
  days; yesterday's forecast against actual and the error of the last 14 days; a status line with sensors that
  report no values. Each part links to the tab that explains it. Live values update in place without reloading the
  page.
- Panel, Diagnostics: shows the installed Noxcast version.

### Changed
- Panel: *Learned* is no longer the start tab; inside an array the way back reads "All arrays".
- Day view, groups: each group's course now shows, like the chart above, which part is measured (strong) and which
  is still forecast (faint), with a line at the current time.
- Forecast accuracy, "Weather or model?": the daily bars now show the weather and model part in kWh instead of as a
  share of that day's actual yield. A dark day with little yield no longer shows a huge model part for a few hundred
  Wh. The help text explains that the model part also contains the satellite's measurement errors (e.g. in fog), so
  a single day says little about the model.
- Day view: the deviation from the day-ahead forecast (today: the 6 am forecast) is no longer green when more came
  than forecast and red when less came. Its colour now shows how large the miss is, measured against what the array
  would give under a clear sky: no colour below 10 %, yellow from 10 %, red from 25 %. A +90 % on a dark November
  day with a few hundred Wh therefore stays calm, and a big miss in either direction stands out. The tooltip gives
  the miss in kWh.

### Fixed
- Learned values: the sky view shows "not learned yet" instead of 100 % while there are too few overcast hours to
  learn it (the forecast keeps assuming a free sky until then). The learned sensor has a new attribute
  `sky_view_learned`. Arrays learned before this version show the old value until the next nightly learning run.
- Satellite filter: the efficiency is learned only from hours whose sun position the shading map has learned as
  free. Before, positions the map did not know yet counted as unshaded, so soft shade there (trees) pulled the
  efficiency down and the direction factor up to its limit. The geometry check, with or without the satellite
  filter, also keeps away from shaded cells and needs free hours on both sides of the modules' direction, so
  "Check geometry" no longer appears just because trees shade one side. Takes effect with the next learning run.
- A forecast update during a learning run no longer interferes with the forecast archive. Before, this could
  rarely make the backtest or the saving of the archive fail, for example with a manual relearn around midnight.

## [0.3.0b3] - 2026-11-07

The third pre-release of 0.3.0. The sections below list what changed since 0.3.0b2.

### Changed
- Forecast accuracy, "Weather or model?": the daily bars now show the weather and model part in kWh instead of as a
  share of that day's actual yield. A dark day with little yield no longer shows a huge model part for a few hundred
  Wh. The help text explains that the model part also contains the satellite's measurement errors (e.g. in fog), so
  a single day says little about the model.
- Day view: the deviation from the day-ahead forecast (today: the 6 am forecast) is no longer green when more came
  than forecast and red when less came. Its colour now shows how large the miss is, measured against what the array
  would give under a clear sky: no colour below 10 %, yellow from 10 %, red from 25 %. A +90 % on a dark November
  day with a few hundred Wh therefore stays calm, and a big miss in either direction stands out. The tooltip gives
  the miss in kWh.

## [0.3.0b2] - 2026-11-07

The second pre-release of 0.3.0. The sections below list what changed since 0.3.0b1.

### Fixed
- A forecast update during a learning run no longer interferes with the forecast archive. Before, this could
  rarely make the backtest or the saving of the archive fail, for example with a manual relearn around midnight.

## [0.3.0b1] - 2026-11-07

The first pre-release of 0.3.0. It changes how the efficiency is learned, so it runs through a nightly learning run
before 0.3.0.

### Added
- Panel: a new start tab *Overview* (*Übersicht*) with what is due today and in the days ahead: today expected, so
  far against its forecast, rest of today and PV power now with the day's course; for flexible consumers whether to
  start now and the best windows; the battery's state of charge, full and empty times and lost surplus; the next days; yesterday's
  forecast against actual and the error of the last 14 days; a status line with sensors that report no values. Each
  part links to the tab that explains it. Live values update in place without reloading the page.
- Panel, Diagnostics: shows the installed Noxcast version.

### Changed
- Panel: *Learned* is no longer the start tab; inside an array the way back reads "All arrays".
- Day view, groups: each group's course now shows, like the chart above, which part is measured (strong) and which
  is still forecast (faint), with a line at the current time.

### Fixed
- Learned values: the sky view shows "not learned yet" instead of 100 % while there are too few overcast hours to
  learn it (the forecast keeps assuming a free sky until then). The learned sensor has a new attribute
  `sky_view_learned`. Arrays learned before this version show the old value until the next nightly learning run.
- Satellite filter: the efficiency is learned only from hours whose sun position the shading map has learned as
  free. Before, positions the map did not know yet counted as unshaded, so soft shade there (trees) pulled the
  efficiency down and the direction factor up to its limit. The geometry check also keeps away from shaded cells
  and needs free hours on both sides of the modules' direction, so "Check geometry" no longer appears just
  because trees shade one side. Takes effect with the next learning run.

## [0.2.0] - 2026-11-07

All changes since 0.1.0, tested in the pre-releases 0.2.0b1 to 0.2.0b4 (their single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)). One change needs action: see Breaking.

### Breaking
- "Forecast remaining today" is now "Expected remaining today" ("Erwartung Rest heute" in German), with a new entity
  ID such as `sensor.noxcast_expected_remaining_today` (in German `sensor.noxcast_erwartung_rest_heute`), per array
  as well. It has always been the rest of "Expected today", recomputed with every update, not the rest of the
  forecast fixed at 6 am. The old sensors (`sensor.noxcast_forecast_remaining_today`, in German
  `sensor.noxcast_prognose_rest_heute`) are removed with their customisations; replace their entity IDs in
  automations and dashboards. Their recorded history stays under the old ID until the recorder purges it.

### Added
- "Compare weather models" in the forecast accuracy view: on request, Noxcast fetches what every offered weather
  model forecast one day ahead over the past year, runs it through your learned model and compares each model and
  each averaged pair with what your arrays produced. It ranks them by daily error, shows where they differ (by
  weather and by month) and recommends a change only when it holds on days that were not used to pick it. Nothing
  is changed automatically. A run asks Open-Meteo once per model with pauses, stops at once if Open-Meteo reports
  its request limit, and after a successful run the next one is possible after 7 days.

### Changed
- The weather card shows today the same yield as the sensor "Expected today" (measured so far plus the corrected
  rest of the day) instead of a running weather forecast that appeared nowhere else. The new card option
  `pv_today: forecast` shows the morning forecast instead, as the sensor "Forecast today" does.
- "How the hour comes about" in the day view now explains the forecast the chart compares with: today the
  6 am forecast, for past days the day-ahead forecast. Before, it recomputed the hour with the latest weather and
  model, so its "Forecast" matched no line in the chart. Today it continues with what newer weather changed
  ("Newer weather", "Latest forecast"), today's correction and the actual value. The values are in Wh, the energy of
  that hour. The derivation is stored from this version on: days archived before are recomputed with today's model
  and marked "Today's model".
- The small charts under "Groups" in the day view cover the same daylight hours as the main chart instead of the
  whole day, share one scale so their heights compare, and show the group's own forecast (6 am today, day-ahead for
  past days) as a dashed line.
- The weather source chips in the accuracy view name the models of the issued forecast ("Issued (ICON + ECMWF
  IFS)" instead of "Main source") and say when the models were changed within the period. With a single model its
  chip includes the days it made the forecast alone. The "Open-Meteo" chip of days archived next to a weather
  entity is now "Open-Meteo (earlier comparison)".
- The archive keeps which weather models each day's forecast came from.
- An Open-Meteo refusal shows its reason in the log (for example that the daily request limit is exceeded), not
  only the HTTP status.
- When a Noxcast page gets no answer while loading its data, for example while Noxcast relearns after its settings
  changed, it explains that Noxcast is not answering and that the page tries again, instead of showing just
  "timeout".
- The consumer form and the README say which properties the power, energy and time sensors need.

### Fixed
- A consumer with an energy counter that is unknown between runs (common for the program energy of washers and
  dishwashers) no longer takes those hours out of learning the base load. Hours without a value now count as no
  consumption of that device. Such a device was learned from its run hours only and forecast too high; expect its
  forecast to drop after the next learning run. If a meter that keeps counting is unavailable for a while, those
  hours count as no consumption too, so what the device used meanwhile counts as base load, and the meter's catch-up
  lands in the next hour.
- With an energy sensor and an elapsed time, the power of a running program is now right for a meter that keeps
  counting too, not only for a counter that starts at 0 with each program.
- The forecast error sensor no longer blocks Home Assistant for about a second at start and on every update
  ("Updating state ... took 0.9 seconds" in the log). Its figures are now computed in the background at start and
  after the nightly learning run, which is when new actual values arrive.
- When Open-Meteo is busy (often "503 Service Unavailable" on the full and half hour), Noxcast keeps its stored
  weather of the last 24 hours and tries again one to three minutes later instead of waiting for the next update.
  The log shows one warning per outage, only when the retry fails too, and a note when Open-Meteo answers again.
- The learning status in the panel no longer flickers during a run: only its text, the stages and the button change
  until the run is done.
- The weather card updates only the outside values when the temperature or humidity sensor reports, instead of
  rebuilding the whole card.
- After a Home Assistant restart the weather card shows the PV yield per day again as soon as Noxcast has computed
  it. Before, it showed the days without PV until the next refresh 15 minutes later, or until the page was reloaded.
- A browser tab that stays open across a Home Assistant restart no longer logs "the name "noxcast-panel" has already
  been used" (or the same for a card). It keeps the loaded panel and cards until the page is reloaded.

### Removed
- The weather models ICON Global, ICON-EU and ICON-D2, which Open-Meteo discontinues on 30 November 2026. Where
  one of them was selected, ICON (`icon_seamless`) takes its place automatically.

## [0.2.0b4] - 2026-11-07

The fourth pre-release of 0.2.0. The sections below list what changed since 0.2.0b3.

### Changed
- When a Noxcast page gets no answer while loading its data, for example while Noxcast relearns after its settings
  changed, it explains that Noxcast is not answering and that the page tries again, instead of showing just
  "timeout".
- The consumer form and the README say which properties the power, energy and time sensors need.

### Fixed
- A consumer with an energy counter that is unknown between runs (common for the program energy of washers and
  dishwashers) no longer takes those hours out of learning the base load. Hours without a value now count as no
  consumption of that device. Such a device was learned from its run hours only and forecast too high; expect its
  forecast to drop after the next learning run. If a meter that keeps counting is unavailable for a while, those
  hours count as no consumption too, so what the device used meanwhile counts as base load, and the meter's catch-up
  lands in the next hour.
- With an energy sensor and an elapsed time, the power of a running program is now right for a meter that keeps
  counting too, not only for a counter that starts at 0 with each program.

## [0.2.0b3] - 2026-11-07

The third pre-release of 0.2.0. The section below lists what changed since 0.2.0b2.

### Fixed
- The forecast error sensor no longer blocks Home Assistant for about a second at start and on every update
  ("Updating state ... took 0.9 seconds" in the log). Its figures are now computed in the background at start and
  after the nightly learning run, which is when new actual values arrive.
- When Open-Meteo is busy (often "503 Service Unavailable" on the full and half hour), Noxcast keeps its stored
  weather of the last 24 hours and tries again one to three minutes later instead of waiting for the next update. The log shows one warning per outage, only when the
  retry fails too, and a note when Open-Meteo answers again.

## [0.2.0b2] - 2026-11-06

The second pre-release of 0.2.0. The section below lists what changed since 0.2.0b1.

### Fixed
- The learning status in the panel no longer flickers during a run: only its text, the stages and the button change
  until the run is done.
- The forecast accuracy view no longer flickers while a weather model comparison is going: only its progress and
  button change until the result is there.
- The weather card updates only the outside values when the temperature or humidity sensor reports, instead of
  rebuilding the whole card.

## [0.2.0b1] - 2026-11-06

The first pre-release of 0.2.0. The sections below list what changed since 0.1.0.

### Added
- "Compare weather models" in the forecast accuracy view: on request, Noxcast fetches what every offered weather
  model forecast one day ahead over the past year, runs it through your learned model and compares each model and
  each averaged pair with what your arrays produced. It ranks them by daily error, shows where they differ (by
  weather and by month) and recommends a change only when it holds on days that were not used to pick it. Nothing
  is changed automatically. A run asks Open-Meteo once per model with pauses, stops at once if Open-Meteo reports
  its request limit, and after a successful run the next one is possible after 7 days.

### Changed
- The weather card shows today the same yield as the sensor "Expected today" (measured so far plus the corrected
  rest of the day) instead of a running weather forecast that appeared nowhere else. The new card option
  `pv_today: forecast` shows the morning forecast instead, as the sensor "Forecast today" does.
- The weather source chips in the accuracy view name the models of the issued forecast ("Issued (ICON + ECMWF
  IFS)" instead of "Main source") and say when the models were changed within the period. With a single model its
  chip includes the days it made the forecast alone. The "Open-Meteo" chip of days archived next to a weather
  entity is now "Open-Meteo (earlier comparison)".
- The archive keeps which weather models each day's forecast came from.
- An Open-Meteo refusal shows its reason in the log (for example that the daily request limit is exceeded), not
  only the HTTP status.
- "How the hour comes about" in the day view now explains the forecast the chart compares with: today the
  6 am forecast, for past days the day-ahead forecast. Before, it recomputed the hour with the latest weather and
  model, so its "Forecast" matched no line in the chart. Today it continues with what newer weather changed
  ("Newer weather", "Latest forecast"), today's correction and the actual value. The values are in Wh, the energy of
  that hour. The derivation is stored from this version on: days archived before are recomputed with today's model
  and marked "Today's model".
- The small charts under "Groups" in the day view cover the same daylight hours as the main chart instead of the
  whole day, share one scale so their heights compare, and show the group's own forecast (6 am today, day-ahead for
  past days) as a dashed line.
- **Breaking:** "Forecast remaining today" is now "Expected remaining today" ("Erwartung Rest heute" in German),
  with a new entity ID such as `sensor.noxcast_expected_remaining_today` (in German
  `sensor.noxcast_erwartung_rest_heute`), per array as well. It has always been the rest of "Expected today",
  recomputed with every update, not the rest of the forecast fixed at 6 am. The old sensors
  (`sensor.noxcast_forecast_remaining_today`, in German `sensor.noxcast_prognose_rest_heute`) are removed with
  their customisations; replace their entity IDs in automations and dashboards. Their recorded history stays under
  the old ID until the recorder purges it.

### Removed
- The weather models ICON Global, ICON-EU and ICON-D2, which Open-Meteo discontinues on 30 November 2026. Where
  one of them was selected, ICON (`icon_seamless`) takes its place automatically.

## [0.1.0] - 2026-11-05

The first stable release. It contains everything from the pre-releases 0.1.0b1 to 0.1.0b5, listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md): a PV forecast per array and in total for
up to 7 days with a P10–P90 band, nightly learning of efficiency, shade, reflection and temperature loss from your own
history, a day-ahead accuracy archive, and optionally a house load forecast, a battery simulation and the best time for
flexible consumers, shown in a sidebar panel, a day card and a weather card. Changes since 0.1.0b5:

### Fixed
- "Prediction and reality" in Battery & surplus compares when the battery is empty for the night: from when it
  stays at its lower limit until it charges again. Before, a battery that was empty in the morning showed "empty" in
  the morning in prediction and actual, so the evening was never compared. Days on which the battery stayed at its
  lower limit all day are marked "not charged". A new figure shows how often the prediction got the night right:
  empty, lasts the night, or not charged. Predictions stored before this version get no empty comparison.
- While a learning run started with "Learn again now" is going, the panel no longer rebuilds the whole page every
  few seconds. Only the learning status updates; the rest is redrawn once when the run is done.
  If Home Assistant restarts during the run, the status picks up again instead of staying on "running".

## [0.1.0b5] - 2026-11-05

The last pre-release before the first stable version 0.1.0. The sections below list what changed since 0.1.0b4.

### Added
- Forecast sensors for 3 to 6 days ahead, per array and in total, as far as the configured forecast days reach. They
  are disabled at first: enable the ones you need under the Noxcast device. They have no P10–P90, since that band is
  learned from day-ahead forecasts.
- "Learned" starts with a learning status: when the last run was and how long it took, when the next one runs, and per
  PV array how far learning has come (efficiency still provisional, shading map still filling over the year, or
  learned), with the numbers behind it. Administrators can start a new learning run there and see when it is done.
- Noxcast reports problems under Settings → System → Repairs: no PV array added yet, configured sensors that no longer
  exist, no weather forecast for more than a day, and PV arrays whose measurements do not fit their configuration.
  Each report disappears once the problem is solved. Before, these were visible only in the panel's diagnostics.
- The panel tells how to add the first PV array instead of only saying that none is learned yet.
- Noxcast has its own icon: a sun on its daily arc under a night sky, shown under Devices & services and in
  HACS.
- Batteries of balcony systems can have the output limit of their inverter (e.g. 800 W, or 1600 W with several
  phases) and the PV array on the same inverter. The battery forecast then lets the battery deliver only what is
  left under the limit next to its PV, and PV above the limit charges the battery even while the house draws from
  the grid. Before, the forecast emptied such a battery faster than it can.
- The README is available in German as well: [README.de.md](README.de.md).

### Changed
- The sidebar panel shows the Load and Battery tabs only when a house load sensor or a battery is set up.
- On a phone, the panel's sections sit in a row of their own below the menu, each with its icon and a short name,
  so every section can be told apart without trying them.
- Shorter forms. A PV array asks for its mounting (free behind, on the roof, in the roof) instead of a cell heating in
  K; the date, the temperature coefficient and an own heating value are under "Advanced". A consumer is set up in two
  steps, and the second shows only the fields of its timing. A battery shows name, sensor, capacity and coupling; limits,
  power and a shared inverter are in sections. Existing entries keep their values.

### Fixed
- The weather entity's daily forecast shows the full week everywhere. West of UTC (e.g. in the Americas), and
  east of it in the first hours after local midnight, the last day was missing.
- Reconfiguring Noxcast (Settings → Devices & services → Noxcast → Reconfigure) reloads it once instead of twice and
  keeps working with Home Assistant 2026.12, which refuses the double reload. Home Assistant 2026.6 to 2026.11 log a
  warning about it.
- The weather card no longer stays on "No weather from Noxcast yet" after Home Assistant restarts. It asks again
  until Noxcast is up and picks the weather up again after a reconnect, without reloading the page.
- The day card no longer shows an empty day after Home Assistant restarts. While Noxcast is still starting it asks again
  within seconds instead of waiting for its next regular refresh, and then loads the house load, battery and weather
  of the day as well.
- Without a house load sensor, the day card no longer draws a battery forecast that only ever charges. It shows the
  measured state of charge and no estimate ahead, because the battery's discharge is not known.
- Without a grid sensor, "Where the energy went" on the day card calls the export "Grid (rest)" instead of "Grid". It is
  not measured but what is left of the balance, so it also contains what a battery without state of charge or an
  unmeasured consumer took.
- Diagnosis shows sensor states without needless decimals ("0 W" instead of "0.0 W").

## [0.1.0b4] - 2026-11-04

### Added
- Per flexible consumer, the best window today and tomorrow as sensors with the start time, and a binary sensor
  "Start now" that turns on while starting now gets at least a set PV share (default 70 %, per consumer) and nearly as
  much as the best window still to come today. Automations can use them as triggers directly, without templates.
- Diagnostics: each excluded period has an "Include again" button. It takes the period back into learning, and
  Noxcast learns again in the background. Before, a period excluded by mistake could not be undone.

### Changed
- The panorama under "Learned" is now the view a camera on the module would see, and you can swivel it: drag to look
  left, right, up and down, zoom with the mouse wheel or two fingers, and go back to the module's direction with one
  button. As on a photo, straight edges stay straight, so house walls, corners and roof edges look as they do on site.
  An obstacle just above the lowest sun of the year is continued, paler, down to the horizon, since houses stand on the
  ground. The edge of the module's own plane is now exact, and a triangle on the horizon marks where the module faces.
  The explanation beside the picture says what can be measured and what cannot.

### Fixed
- Saving a consumer or a battery confirmed "PV array saved".
- German texts call a PV array "PV-Gruppe" throughout; "Fläche" now only means one orientation within it.
- The weather card is named in English in the card picker when Home Assistant is set to English.

## [0.1.0b3] - 2026-11-04

### Added
- A "Panorama" view next to "Map" and "Year grid" under "Learned": the view from the module, direction across and height
  up at the same scale as the eye sees them. It shows the obstacles learned from the measurements, the horizon line
  where measurements are still missing, the module's own plane (roof, wall, railing) and the sun paths of June,
  March/September and December, so the learned shade can be compared with the real surroundings. If the edge of the own
  plane looks clearly different on site, tilt or azimuth are configured wrong.

## [0.1.0b2] - 2026-11-03

### Added
- The diagnostics download contains, per PV array, the hours learning used: measured power, clear-sky power, the
  satellite's clearness, temperature, shade and what each hour says about the efficiency. This shows where an
  implausible efficiency comes from. The download takes a few seconds longer, since it fetches the history again.
- Each array under "Learned" shows whether its configuration fits the measurements: the highest hourly power against
  the configured peak power, the efficiency per sun direction next to the one per month, and plain hints when the peak
  power, the sensor, the azimuth or the tilt look wrong. Such an array gets a "Check" badge, and the diagnostics tab
  lists it. Shown after the next learning run.

## [0.1.0b1] - 2026-11-02

### Added

- PV forecast per array and in total for up to 7 days, hourly, with a P10–P90 band, from Open-Meteo weather.
- Today's forecast fixed at 06:00, the expected day from measurements plus the corrected rest, and the remaining energy.
- Nightly learning from your own history: efficiency, shading map, reflection, direction factor, sky view and
  temperature loss.
- Accuracy view from a day-ahead archive, with the forecast error split into weather and model.
- A backtest of the current model on past days and on days it did not learn from.
- Optional house load forecast, battery simulation until the next charge start, and planning for flexible consumers.
- Sidebar panel, a day card, a weather card and a weather entity for dashboards.
- `noxcast.exclude_period` and `noxcast.relearn` services for administrators.
- A diagnostics section and download showing what data Noxcast reads and how complete it is.

### Fixed

- Day section: the lanes below the chart show the full array names instead of their first six characters. A long name
  reaches into the chart; a bar behind it stays faint, so its start remains visible. The tooltip of a bar names its array.
- Diagnostics and load forecast on a phone: long entity IDs wrap instead of being cut off.
- Day card: the list behind a figure shows a long name on two lines before shortening it, and the full name in a tooltip.
