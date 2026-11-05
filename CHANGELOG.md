# Changelog

All notable changes to Noxcast are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor version can contain
breaking changes.

## Unreleased

### Added
- The README is available in German as well: [README.de.md](README.de.md).

### Fixed
- The weather card no longer stays on "No weather from Noxcast yet" after Home Assistant restarts. It asks again
  until Noxcast is up and picks the weather up again after a reconnect, without reloading the page.
- The day card no longer shows an empty day after Home Assistant restarts. While Noxcast is still starting it asks again
  within seconds instead of waiting for its next regular refresh, and then loads the house load, battery and weather
  of the day as well.
- Without a house load sensor, the day card no longer draws a battery forecast that only ever charges. It shows the
  measured state of charge and no estimate ahead, because the battery's discharge is not known.

### Changed
- The sidebar panel shows the Load and Battery tabs only when a house load sensor or a battery is set up.

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
