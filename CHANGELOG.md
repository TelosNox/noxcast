# Changelog

All notable changes to Noxcast are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor version can contain
breaking changes.

## Unreleased

### Added
- *Forecast accuracy* shows how often the actual value fell below, inside or above the published P10–P90 range,
  per weather class, for whole days and single hours, next to the target of 1 in 10 below and above. So far this was
  only in the diagnostics download.
- The battery forecast simulates separate systems each on its own: each charges from its own PV, so a full balcony
  battery loses its surplus even while the house battery still has room, and a battery at the house grid charges
  from what the others send out. They share the consumption evenly, each up to its discharge power, and a system
  with *fixed power* delivers it whatever the house draws. *Battery full at* and *Battery empty at* show the earliest
  system, and the `systems` attribute of these sensors and of *Battery highest level* lists each system; the
  *Battery & surplus* tab shows them per system. Batteries of one system stay one storage as before.
- *Fixed power* now counts: it caps the PV forecast of a system without a battery like an output limit, and learning
  counts the hours at it as "at least this much" instead of learning them as shade. With a battery, hours at the
  fixed power while the battery is full or charges at its maximum are treated the same way. The *Installation* tab
  shows the setting as computed (with a battery as simplified).

### Changed
- *Battery considered full at* is no longer a setting of the household. Each battery counts as full from its own
  *Counts as full from* below its upper limit, 5 percent points by default, so at an upper limit of 100 % from 95 %
  as before. A battery with a lower upper limit now counts as full 5 instead of 2 points below it, which leaves a few
  more hours with possibly curtailed PV out of learning. A value you had changed for the household is carried over
  to each battery, so that it counts as full where it did before.

## [0.6.0] - 2026-11-22

All changes since 0.5.0: the pre-releases 0.6.0b1 to 0.6.0b3 (their single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)). Systems, PV arrays and batteries can now
be changed, added and removed in the *Installation* tab. From the next nightly learning run on, learning changes for systems with an output limit, per phase or with a
battery, and the P10–P90 range of a day gets wider where it was too narrow.

### Added
- New sensor *Battery lowest level (48 h)*: the battery forecast now also runs for the next 48 hours, past
  tomorrow's charge start, as far as the PV forecast reaches. Its attribute `hourly` holds the expected level at
  every full hour in %, for tariff and charge planners.
- The forecast sensors carry the forecast also in 15-minute steps (attribute `quarter_hourly`, kWh per step), for
  planners with 15-minute tariffs. The four steps add up to the hourly value and stay under a power limit. The
  weather forecast comes in hours, so each step gets its hour's cloudiness and follows the course of the sun within
  the hour. The README templates for Predbat and
  EMHASS use them.
- The README shows how to feed the forecast to EVCC, Predbat and EMHASS.
- An output limit that holds per phase now counts (*Limit holds per phase*, e.g. EcoFlow Stream in three-phase
  mode): the devices on one phase share its limit, a device alone on a phase gets the whole limit. Without a battery
  it caps the PV forecast of each phase, and learning counts the hours in which a phase reached it as "at least this
  much"; with a battery, hours in which a phase's output reached it count the same, and the battery forecast shares
  the limit only with the devices on the same phase. Until now such a limit was not used at all. A three-phase
  device, or one without a phase, is left out of the phase limits; the *Installation* tab shows this as simplified.
- The array check flags a power sensor that showed power while the sun was down in the last 60 days: it measures more
  than PV, usually the output of an inverter with a battery, and so counts battery energy, even energy charged from
  the grid, as sun in the actual value and in learning. The array gets the *Check* badge and a notice under Repairs.
  The description of the power sensors now says that they should measure PV only.
- The installation diagram (*Installation* tab and the forms of systems, PV arrays and batteries) shows every setting
  where it acts: each array's orientation as a compass needle with tilt, direction and peak power, its power sensors,
  a power limit per sensor on the line from the array, each battery as a box of its own with its capacity and where it
  charges from, a system's output limit and its deviation from the feed-in rule on its output, a subsystem's phase,
  the house load and grid sensors, and the feed-in rule between house and grid. Tapping a battery shows its settings.
- Systems, PV arrays, batteries and the household can be changed in the *Installation* tab: *Change* opens the same
  form as the configuration dialog beside the diagram, the diagram shows each change before it is saved, and *Save*
  checks and stores it as the dialog does. A change made elsewhere in the meantime is not overwritten; the tab then
  reloads it. Administrators only.
- Parts can be added and removed in the *Installation* tab: dashed boxes in the diagram add a PV array, a battery, a
  subsystem or a system where one fits, with the same form as the configuration dialog, and the new part is drawn in
  the diagram before it is saved. A new system's template is picked on a picture of the four templates. *Remove*
  first names what goes with the part and shows the diagram without it; nothing is removed before you confirm.
  Administrators only.
- The first setup ends with a link to the *Installation* tab, which opens with a *+ System* box and a short hint for
  an installation without systems. The system dialogs point to the tab as well.
- The diagnostics download contains the learned range and how often the actual value fell inside the range that
  was published, per weather class.
- The Noxcast panel notices when Noxcast was updated while it was open, also across a restart of Home Assistant,
  and offers *Reload*. Until now such a page went on with the old interface, so that for example the "+" boxes
  and the *Change* button of the *Installation* tab were missing or did nothing until the page was reloaded by hand.

### Changed
- Nightly learning and the backtest need about a third less computing time: during a run, the sun's path through an
  hour is computed once instead of again in every step. The results stay the same; the run holds up to about 18 MB
  more memory while it lasts.
- The P10–P90 range of a day is wider where it was too narrow. It is learned from the past days of the same weather,
  and with few such days it now reaches their largest deviations instead of cutting them off. Replayed over a year
  of day-ahead forecasts on one installation, the actual value of a day fell inside the range on 73 % of the days
  instead of 66 % (target 80 %), with a better balance of width and misses. The hourly range hardly changes. So that
  a single bad day cannot widen it, the range now also leaves out what learning leaves out: days without output,
  hours under snow and excluded periods. The next learning run applies it.
- The installation diagram in the *Installation* tab grows with the width of the page, up to half as large again,
  so boxes and text stay readable on a wide screen. On a phone it keeps a readable size and scrolls sideways.
- The data basis in the panel names a full battery just "battery full": the separate "battery full (household)" reason
  never applied any more, since a full battery is detected per system. The hour table and its CSV
  download no longer have the state of charge column, which stayed empty.

### Fixed
- A system with a battery and an output limit no longer teaches the cut at its limit as shade. When the PV
  less what the battery charges reached the output limit, the hour counts as "at least this much" in learning,
  however much the battery took, and the intraday correction no longer reads it as clouds. Before, only a full
  battery or one charging at its maximum power marked such hours. This needs the battery's *Charge / discharge
  power* sensor; the next learning run applies it to the whole history.
- The backtest on *Unlearned days, measured weather* (*Forecast accuracy*) now caps the model at the fixed output limits, as the forecast
  and the replay do. Learning counts the hours at a limit as "at least this much", so the measured value there is the
  cut output; scored uncapped, a correct model came out far worse than one that had learned the cut as shade. This
  mattered most for a limit shared by several devices, such as one per phase.

## [0.6.0b3] - 2026-11-22

### Added
- The Noxcast panel notices when Noxcast was updated while it was open, also across a restart of Home Assistant,
  and offers *Reload*. Until now such a page went on with the old interface, so that for example the "+" boxes
  and the *Change* button of the *Installation* tab were missing or did nothing until the page was reloaded by hand.

### Changed
- The installation diagram in the *Installation* tab grows with the width of the page, up to half as large again,
  so boxes and text stay readable on a wide screen. On a phone it keeps a readable size and scrolls sideways.
- The data basis in the panel names a full battery just "battery full": the separate "battery full (household)" reason
  never applied any more, since a full battery is detected per system. The hour table and its CSV
  download no longer have the state of charge column, which stayed empty.

## [0.6.0b2] - 2026-11-22

### Added
- An output limit that holds per phase now counts (*Limit holds per phase*, e.g. EcoFlow Stream in three-phase
  mode): the devices on one phase share its limit, a device alone on a phase gets the whole limit. Without a battery
  it caps the PV forecast of each phase, and learning counts the hours in which a phase reached it as "at least this
  much"; with a battery, hours in which a phase's output reached it count the same, and the battery forecast shares
  the limit only with the devices on the same phase. Until now such a limit was not used at all. A three-phase
  device, or one without a phase, is left out of the phase limits; the *Installation* tab shows this as simplified.
- The array check flags a power sensor that showed power while the sun was down in the last 60 days: it measures more
  than PV, usually the output of an inverter with a battery, and so counts battery energy, even energy charged from
  the grid, as sun in the actual value and in learning. The array gets the *Check* badge and a notice under Repairs.
  The description of the power sensors now says that they should measure PV only.
- The installation diagram (*Installation* tab and the forms of systems, PV arrays and batteries) shows every setting
  where it acts: each array's orientation as a compass needle with tilt, direction and peak power, its power sensors,
  a power limit per sensor on the line from the array, each battery as a box of its own with its capacity and where it
  charges from, a system's output limit and its deviation from the feed-in rule on its output, a subsystem's phase,
  the house load and grid sensors, and the feed-in rule between house and grid. Tapping a battery shows its settings.
- Systems, PV arrays, batteries and the household can be changed in the *Installation* tab: *Change* opens the same
  form as the configuration dialog beside the diagram, the diagram shows each change before it is saved, and *Save*
  checks and stores it as the dialog does. A change made elsewhere in the meantime is not overwritten; the tab then
  reloads it. Administrators only.
- Parts can be added and removed in the *Installation* tab: dashed boxes in the diagram add a PV array, a battery, a
  subsystem or a system where one fits, with the same form as the configuration dialog, and the new part is drawn in
  the diagram before it is saved. A new system's template is picked on a picture of the four templates. *Remove*
  first names what goes with the part and shows the diagram without it; nothing is removed before you confirm.
  Administrators only.
- The first setup ends with a link to the *Installation* tab, which opens with a *+ System* box and a short hint for
  an installation without systems. The system dialogs point to the tab as well.
- The diagnostics download contains the learned range and how often the actual value fell inside the range that
  was published, per weather class.

### Changed
- The P10–P90 range of a day is wider where it was too narrow. It is learned from the past days of the same weather,
  and with few such days it now reaches their largest deviations instead of cutting them off. Replayed over a year
  of day-ahead forecasts on one installation, the actual value of a day fell inside the range on 73 % of the days
  instead of 66 % (target 80 %), with a better balance of width and misses. The hourly range hardly changes. So that
  a single bad day cannot widen it, the range now also leaves out what learning leaves out: days without output,
  hours under snow and excluded periods. The next learning run applies it.

### Fixed
- The backtest on *Unlearned days, measured weather* (*Forecast accuracy*) now caps the model at the fixed output limits, as the forecast
  and the replay do. Learning counts the hours at a limit as "at least this much", so the measured value there is the
  cut output; scored uncapped, a correct model came out far worse than one that had learned the cut as shade. This
  mattered most for a limit shared by several devices, such as one per phase.

## [0.6.0b1] - 2026-11-21

### Added
- New sensor *Battery lowest level (48 h)*: the battery forecast now also runs for the next 48 hours, past
  tomorrow's charge start, as far as the PV forecast reaches. Its attribute `hourly` holds the expected level at
  every full hour in %, for tariff and charge planners.
- The forecast sensors carry the forecast also in 15-minute steps (attribute `quarter_hourly`, kWh per step), for
  planners with 15-minute tariffs. The four steps add up to the hourly value and stay under a power limit. The
  weather forecast comes in hours, so each step gets its hour's cloudiness and follows the course of the sun within
  the hour. The README templates for Predbat and
  EMHASS use them.
- The README shows how to feed the forecast to EVCC, Predbat and EMHASS.

### Changed
- Nightly learning and the backtest need about a third less computing time: during a run, the sun's path through an
  hour is computed once instead of again in every step. The results stay the same; the run holds up to about 18 MB
  more memory while it lasts.

### Fixed
- A system with a battery and an output limit no longer teaches the cut at its limit as shade. When the PV
  less what the battery charges reached the output limit, the hour counts as "at least this much" in learning,
  however much the battery took, and the intraday correction no longer reads it as clouds. Before, only a full
  battery or one charging at its maximum power marked such hours. This needs the battery's *Charge / discharge
  power* sensor; the next learning run applies it to the whole history.

## [0.5.0] - 2026-11-19

All changes since 0.4.0: the pre-releases 0.5.0b1 to 0.5.0b8 (their single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)) and, since then, the wording of the
geometry check. The installation is now entered as systems, and the update converts the configuration on its first
start. After the update, check the Repairs notices and the new *Installation* tab, which shows per setting how far
Noxcast computes it. Learning changes for most installations from the first nightly learning run on, since it now
leaves out only the hours a limit or a full battery really affected.

### Breaking
- The main entry no longer has its own list of battery state-of-charge sensors. A battery counts only where it is
  entered with its system, and then only for the PV arrays it can curtail. The update drops each sensor that such a
  battery already has; for any other, a repair asks you to enter its battery with its system and the PV arrays it
  charges. Until you do, hours in which that battery was full count in learning. Arrays that a battery cannot
  curtail now also learn from the hours in which it was full.
- Regulation and the deviation from the feed-in rule are set on the system only, and its subsystems follow it.
  Subsystems are devices one control coordinates (EcoFlow Stream, for example), so they cannot choose differently.
  The update moves a subsystem's value to its system where the system had none and its subsystems agreed, and drops
  it from the subsystems otherwise. If a subsystem was set differently from its system, it is a device that control
  does not regulate: make it a system of its own (*Belongs to* empty), or it now follows its system, also in what
  learning leaves out.

### Added
- Systems: a new entry *System* describes how modules and batteries reach the house, from the templates *Inverter
  without battery*, *PV with battery in one device*, *Battery at the house grid* and *Coupled systems* (e.g.
  EcoFlow Stream). A system holds its PV arrays and at most one battery, or subsystems that share its output limit.
  Each system is one entry with everything inside it; opening it shows a menu with each part, and removing a part
  asks first and names what goes with it; deleting a system deletes everything inside it, with what was learned for
  its PV arrays. A system's template can be changed and keeps the values the new template does not set. The
  configuration forms explain the building blocks with small drawings,
  and the README has a new section *How it fits together* with examples of common installations.
- A new panel tab *Installation* draws the installation as a flow diagram: PV arrays, systems with their batteries
  and limits, house and grid, with a dot per part for whether Noxcast computes its settings exactly, simplified or
  not yet. Tapping a part shows its settings, and *Belongs to* moves a PV array, a battery or a system to another
  system. The forms show the same diagram on top.
- The household's *Feed-in rule* can limit the feed-in to a value in W or to a share of the PV peak power. With the
  *Grid power* sensor in the main entry, hours in which the export reached the limit count as "at least this much"
  in learning, and the correction of today's forecast does not read them as clouds.
- The output limit of a system without battery caps the forecast, also when several PV arrays share it (east and
  west on one inverter): their sum stays below the limit in every 10-minute step, and each array gives up its share
  of what lies above. Forecast sensors, band, battery forecast and day view follow. Hours at the limit count as "at
  least this much" in learning, and entering or changing the limit relearns the system's PV arrays. Subsystems with batteries behind
  a shared limit deliver together at most that limit in the battery forecast.
- When the PV arrays of a system without battery together stay flat at a maximum on sunny days, Noxcast suggests
  that value as the system's output limit under Repairs: *Set output limit*, *Other value* or *There is no limit*.
- The battery forecast splits the surplus that does not fit into the battery into what goes into the grid and what
  is curtailed. It feeds in up to the household's feed-in limit, nothing under zero export, and follows a system's
  own deviation by that system's share of the PV; PV above an output limit is curtailed. The sensors *Surplus rest of
  today* and *Surplus lost today* have the new attributes `fed_in` and `curtailed`, and the overview and *Battery &
  surplus* show the split. The value of *Surplus lost today* stays all surplus without room.
- *Learned* → *Data basis* shows per array what the daylight hours were used for (used, capped, battery full, snow,
  no output, excluded), weighted by the energy a clear sky would have given, overall and per month, and points at
  anomalies with their most telling day: "battery full" although the array delivered what the model expects, output
  clearly above the learned model under a calm clear sky, days without output under a bright sky, bright days far
  below the model, and snow periods that only the 7-day limit ended. A calendar of the last year colours each day by
  output against the model; a day stays pale when its deviation lies within what the satellite's error and the
  array's ordinary day-to-day noise explain, and the page says whether there is enough data for the days shown.
  Admins click a day to see its hours and an hour to see the model's steps from clear sky to the result.
- *Hours as CSV* (for admins) exports every measured daylight hour of an array with whether learning used it,
  counted it only as "at least this much" (status `lower_bound`) or left it out, and every reason; with the satellite archive also the model
  on the measured irradiance, split into its steps. The diagnostics download holds the same table for every array.
- The *Day* view explains the whole day, per array or for all together, from clear sky to the forecast and on to the
  actual value; today it goes on over newer weather, today's correction and the measured hours to the expectation.
  Under *Groups* each array shows its actual value so far today.

### Changed
- A full battery leaves hours out of learning only for the PV arrays it can curtail: those of its own system and of
  subsystems that cover the house load together with it or share an output limit. Under zero export a battery no
  longer counts for PV it is not coupled with. Several devices with a battery each under one system count an hour as
  capped only when no battery takes more. A battery whose upper limit comes from an entity counts as full from that
  limit.
- A battery charging at its maximum power, where the rest cannot go out, makes the hour count as "at least this
  much" in learning, and the correction of today's forecast does not read it as clouds. Noxcast reads the charge
  power from 5-minute means, so a single spike of the sensor no longer counts. Where another battery's charge power
  is not configured, such an hour is left out.
- A system with the regulation *Self-consumption* and no battery delivers only what the house draws. With the *Grid
  power* sensor, hours in which the household imported little or nothing count as "at least this much" in learning
  instead of weak sun.
- The update turns the configuration into systems on its first start: every PV array gets a system of its own, a
  battery with a PV array behind its inverter becomes *PV with battery in one device*, an AC battery *Battery at the
  house grid*, and two or more batteries become subsystems of a system *Batteries* that balance each other, as
  simulated before. *Surplus is fed into the grid* becomes the household's *Feed-in rule* (*Free* or *Zero export*);
  a system at *Full power* under zero export gets *Feeds the rest in anyway*, since such an inverter cannot hold the
  export at zero, so the day card now shows its feed-in. Forecasts, learning history and entities stay. A DC battery
  without a PV array gets a Repairs notice.
- A system follows the household's *Feed-in rule* unless it has a *Deviation from the feed-in rule*: *Feeds the rest
  in anyway* or *Never feeds in*. Its regulation is *Full power*, *Self-consumption* or *Fixed power*.
- The battery form no longer has *Coupling* and *Shared inverter*: where the battery sits and the output limit are set
  on its system. A battery added without a system gets one of its own at the house grid (default efficiency 90 %).
- Learning keeps its own copy of the temperature, snowfall and satellite archives and fetches only the new hours and
  the last two weeks each night; changing the archive URL, satellite model or location fetches the whole history
  again. If the temperature or satellite archive cannot be reached, the learning run is
  skipped and the previous learned values stay; the overview shows the failed run, and *Relearn* reports an error.
- The waterfall of today no longer shows a *Latest forecast* bar.
- The geometry check no longer shows a "Confidence" of low, medium or high, which read like a verdict on the
  configured tilt and azimuth. It shows the "Hint of a deviation": none, weak or clear, with how much the best fit
  lowers the spread.
- The diagnostics download no longer contains sensor IDs or the addresses of your own weather or archive services,
  so it can be posted in an issue.

### Fixed
- Days on which a PV array delivered almost nothing (an inverter that was off, modules under snow) and hours with snow
  on the modules no longer count in learning as shade; the snow filter needs *Air temperature from archive* (on by
  default). Snowfall in air warmer than 3 °C no longer counts as snow. The
  snow thresholds are physical assumptions not yet checked on installations with snowy winters; if the list in
  *Does the configuration fit?* looks wrong for yours, please report it.
- A short peak at a *Power limit*, as at the edge of a cloud, no longer turns the whole hour into "at least this
  much" for learning.
- A temperature coefficient from the datasheet is always shown and used as entered. A failed answer of the
  temperature archive no longer makes learning set it to 0 %/K.
- The shade area in the day card keeps the shape of the shadow once the hours have passed.
- The overview no longer lists the remaining or elapsed time of an idle appliance as a note.

## [0.5.0b8] - 2026-11-18

### Changed

- Learning keeps its own copy of the temperature, snowfall and satellite archives and fetches only the new hours and
  the last two weeks each night instead of the whole history. Changing the archive URL, satellite model or location
  fetches the whole history again.
- If the temperature or satellite archive cannot be reached, the learning run is skipped and the previous learned
  values stay; the overview shows the failed run, and the action *Relearn* reports an error. Only the very first
  learning run of a new installation goes ahead without the archive. Before, Noxcast learned without that archive, which set the
  temperature coefficient to 0 %/K, raised the efficiency and could flag the geometry after a single failed night.

### Fixed

- A temperature coefficient from the datasheet is always shown and used as entered. Before, it showed 0 %/K after a
  learning run without temperature history.

## [0.5.0b7] - 2026-11-17

### Breaking

- The main entry no longer has its own list of battery state-of-charge sensors. A battery counts only where it is
  entered with its system, and then only for the PV arrays it can curtail. The update drops each sensor that such a
  battery already has; for any other, a repair asks you to enter its battery with its system and the PV arrays it
  charges. Until you do, hours in which that battery was full count in learning again. Learning may change slightly
  after the update: arrays that battery cannot curtail now also learn from the hours in which it was full.

### Added

- The battery forecast splits the surplus that does not fit into the battery into what goes into the grid and what
  is curtailed. It feeds in up to the household's feed-in limit, nothing under zero export, and follows a system's own
  deviation (always or never feeds in) by that system's share of the PV; PV above an output limit is curtailed. The
  sensors *Surplus rest of today* and *Surplus lost today* have the new attributes `fed_in` and `curtailed`, the
  overview shows the split below the lost surplus, and *Battery & surplus* shows it for the plan and the what-if
  variant. The value of *Surplus lost today* stays all surplus without room. The limit is compared with the hour's
  mean power, so short peaks above it are not counted as curtailed.

### Changed

- The *Installation* tab marks a feed-in limit as counted (simplified) where a battery is set up, since the battery
  forecast now feeds in up to it.

### Fixed

- *Data basis* no longer warns that an array delivered clearly more than the learned model allows under a clear sky
  when this happens only under broken cloud. A module with the sun beside or behind it then gets light from sunlit
  clouds, which is real and no configuration error; on a balcony this reached 1.8 times the bound. The check now
  counts only hours under a calm sky, where a wrong peak power, sensor unit or orientation still shows.

## [0.5.0b6] - 2026-11-17

### Fixed

- Several devices with a battery each under one system no longer count an hour as *capped* or *battery full* as
  soon as one of the batteries is full or charges at full power. Their PV is curtailed only when its own battery takes
  no more and the other batteries take nothing more either, or when the device's output limit keeps the rest from
  reaching them. A battery whose charge power is at least what its own modules can deliver (from the input limits)
  no longer caps them by charging at full power. Today's hours follow the new rule at once; learning keeps more
  bright hours from the next nightly learning run. Where another battery's charge power is not configured, such an
  hour is left out of learning, since that battery may have been charging at full power.

## [0.5.0b5] - 2026-11-16

### Added

- *Data basis* says whether there is enough data for the days shown: with the satellite archive, the nightly
  backtest compares the hourly error on days the model has not learned with that on learned days. More than 10 % above
  apart, there are still too few days per sun position, and strong days in the calendar may come from missing data;
  otherwise they do not.

### Changed

- The calendar of the *Data basis* shows a day pale when its deviation from the model stays within the usual range:
  what the satellite's error under that day's sky (under clouds often a quarter, under a clear sky a few percent)
  and the array's ordinary day-to-day model noise cause together. The tooltip names the range of the day. Only
  strong colours now point at the model or the configuration. The pale days appear after the next nightly learning
  run.
- The waterfall of today no longer shows a *Latest forecast* bar after *Newer weather*; the chain goes straight on to
  the correction, the measured hours and the expectation (or the actual value of a past hour).
- *Data basis* calls the hours in which the PV could not deliver everything *capped* instead of *lower bound*: at a
  power limit, with the battery charging at full power or with PV held back. The CSV export keeps its status
  `lower_bound`.

### Fixed

- A short peak at a *Power limit*, as at the edge of a cloud, no longer turns the whole hour into "at least this
  much" for learning. On changeable days such hours counted for shade and efficiency only as "at least". An hour
  now stays as measured when its mean shows that the limit cannot have cut much: well below the limit, and close
  to what the modules give unshaded under the satellite sky. Learning may change from the next nightly run on.
- Snowfall in air warmer than 3 °C no longer counts as snow on the modules. The weather archive sometimes books a
  shower as snow at 7-8 °C, and learning then left out the following hours, up to the next morning, although the
  modules were free.

## [0.5.0b4] - 2026-11-15

### Added
- *Learned* → *Data basis* shows per array what the daylight hours were used for (used, lower bound, battery full,
  snow, no output, excluded), weighted by the energy a clear sky would have given, overall and per month. It points
  at anomalies with their most telling day: "battery full" although the array delivered what the model expects,
  clearly more often than in the hours learning used (the threshold or the system assignment may be wrong), clearly
  above the learned model's clear-sky output (orientation, peak power or sensor unit), days without output under a
  bright sky and without snow (an outage), bright days below 70 % of the model that no filter explains, and snow
  periods that only the limit of 7 days after the last snowfall ended, although there were bright hours in which
  the array never delivered what free modules deliver (if no snow lay that long, Noxcast does not see this array
  free again, for example under shade, and learning loses a week after every snowfall). The CSV
  export gains the column `bound_w`, that clear-sky output without the shading map. Renewed by every nightly
  learning run; it does not change learning or the forecast.
- *Data basis* gains a calendar of the last year: one cell per day, coloured by output against the model, or hatched
  in the colour of the reason when most of the day was left out, with a dot for single "battery full" hours. Admins
  click a day to see its hours (measured, model and the clear-sky bound, left-out hours shaded) and an hour to see the model's
  steps from clear sky to the result.
- *Learned* → *Does the configuration fit?* exports every measured daylight hour of an array as CSV (*Hours as
  CSV*, for admins): whether learning used it, counted it only as a lower bound (power limit, battery charging at full
  power, PV held back) or left it out, with every reason (battery full, snow, no output, excluded period, before
  commissioning), the measurement, the clear-sky power, temperature, snowfall and the household's state of charge.
  With the satellite archive switched on in the options it also holds the model on the measured irradiance, split
  into clear sky, clouds, shade, reflection, direction and heat. With it you can check whether the filters fit your installation, or attach it to an issue. The
  diagnostics download holds the same table for every array (instead of only the hours learning used).
- The household's feed-in limit now counts in learning, in W as well as in % of the PV peak power (the peak power
  of all PV arrays with their further orientations). With the *Grid power* sensor in the main entry, hours in which
  the household's export reached the limit (within 5 %, at least 100 W) count as "at least this much", since the PV
  may have been held back; the correction of today's forecast does not read them as clouds, and the backtest and
  the detection of inverter limits leave them out. A system with its own deviation from the feed-in rule is not
  affected. The *Installation* tab shows the limit in W, also for a share in %, or that the grid power sensor is
  needed. Learning may change for such installations from the next nightly run on. The battery forecast does not
  split the surplus at the limit yet.
- The *Day* view explains the whole day, per array or for all together: until an hour is chosen in the chart, its
  waterfall sums the steps of all hours, from clear sky to the forecast and on to the actual value. Today it goes on
  over newer weather, today's correction and the measured hours to the expectation, and below it says how much is
  measured so far and how much is still expected. Clicking the chosen hour again, or *Whole day*, goes back to the
  day. Under *Groups* each array shows its actual value so far today.

### Changed
- The diagnostics download no longer contains sensor IDs (each sensor gets an alias such as `sensor.entity_1`,
  the same throughout the file) or the addresses of your own weather or archive services, so it can be posted in an
  issue. Open-Meteo addresses stay, without their query.
- The menu of a system says what each item does: ✎ changes something that exists, + adds, − removes, with a
  short description under the general items. Removing a part asks first and names what goes with it; until then
  nothing is removed.
- The deviation from the feed-in rule in a system's settings says where the household's rule is set: on the
  integration page with ⋮ → *Reconfigure*.

### Fixed
- The overview no longer lists the remaining or elapsed time of an appliance that is not running as a note: such
  sensors are "unknown" while the appliance is idle. A sensor that is unavailable or missing is still listed.
- Days on which a PV array delivered almost nothing (at most 2 % of a clear day) no longer count in learning:
  an inverter that was off, modules fully under snow or a day too dark for the inverter to start. Before, learning
  read such a day as shade and a darker sky, unless you excluded it by hand. *Learned* → the array → *Does the
  configuration fit?* lists how many days were left out and the last one. Learning may change from the next
  nightly run on.
- Snow on the modules no longer counts as shade in learning. With *Air temperature from archive* (on by default),
  Noxcast also fetches the snowfall at your location, and after a snowfall of at least 1 cm it leaves the hours out
  until the array delivers again, it has thawed or a week has passed. The same place in the panel lists those days.
  The thresholds are physical assumptions not yet checked on installations with snowy winters; if the list looks
  wrong for yours, please report it.
- A single reading of the battery power sensor above the *Max charge power* (a few seconds are enough, common with
  sensors computed from several others) made the whole hour count as "the battery charged at its maximum", so
  learning read the PV of that hour only as "at least this much". Shade that falls in such hours could vanish from
  the map. Noxcast now reads the charge power from means: it keeps 5-minute means of the battery power sensors from
  now on, and an hour counts when one of them reaches 95 % of the maximum; older hours count from an hourly mean of
  85 %, and between 70 and 85 % they are left out. The correction of today's forecast reads the same 5-minute means,
  so a spike no longer hides an hour from it either. Learning may change from the next nightly run on.
- A system with the regulation *Self-consumption* and no battery delivers only what the house draws, so on days
  with a low load it held PV back, and learning read that as weak sun: efficiency and shade came out too low. With
  the *Grid power* sensor in the main entry, hours in which the household imported little or nothing at times
  (below 100 W) now count as "at least this much" in learning, the correction of today's forecast does not read
  them as clouds, and the backtest leaves them out. The *Installation* tab shows this per system, or that the grid
  power sensor is needed. Learning may change for such systems from the next nightly run on.

## [0.5.0b3] - 2026-11-14

### Added
- The configuration forms explain the building blocks with small drawings: how household, systems, subsystems,
  PV arrays and batteries nest, the four templates, the three regulations, the chain of limits and what a PV array
  is. Each form shows the drawings that fit its settings. The README has the same drawings and a new section *How
  it fits together* with examples of common installations.
- The household's feed-in rule can also limit the feed-in to a share of the PV peak power (e.g. 60 %). It is
  stored and counts in a later version, like the limit in W.
- The output limit of a system without battery now caps the forecast also when several PV arrays share it, such
  as east and west on one inverter: their sum stays below the limit in every 10-minute step, and each array gives
  up its share of what lies above. Forecast sensors, band, battery forecast and day view follow. The
  *Installation* tab shows this limit as computed exactly.
- When the PV arrays of a system without battery together stay flat at a maximum on sunny days while the learned
  clear sky would have given more, Noxcast suggests that value as the system's output limit under Repairs: *Set
  output limit*, *Other value* or *There is no limit*. The value is what the power sensors measure at the limit,
  so it also fits sensors of DC power, which show a few percent more than the inverter's AC rating. It needs the
  5-minute values Noxcast has kept since its installation and PV arrays that have been learned.

### Changed
- **Breaking:** Regulation and the deviation from the feed-in rule are now set on the system only, and its
  subsystems follow it. Subsystems are devices one control coordinates (EcoFlow Stream, for example), so they
  cannot choose differently. The update moves a subsystem's value to its system where the system had none and its
  subsystems agreed, and drops it from the subsystems otherwise. If a subsystem was set differently from its
  system, it is a device that control does not regulate: make it a system of its own (*Belongs to* empty), or it
  now follows its system, also in what learning leaves out. The *Installation* tab shows the system's values on
  its subsystems.
- *When the battery is full* is replaced by an optional *Deviation from the feed-in rule* on the
  system. A system follows the household's feed-in rule unless it deviates: *Feeds the rest in anyway* or *Never
  feeds in*. The update turns the old setting into a deviation where it disagreed with the household's rule, and
  gives a system at *Full power* a *Feeds the rest in anyway* under zero export, since such an inverter cannot hold
  the export at zero; so everything computes as before, and the day card now shows feed-in for such a household.
  Check the *Installation* tab: it shows per system which rule it follows.
- The regulation options are named *Full power*, *Self-consumption* and *Fixed power*, and the household setting
  is the *Feed-in rule* with *Zero export* as one of its options.
- Rare settings sit in sections that open where they hold a value: the household's feed-in limit, and a
  system's fixed power, deviation from the feed-in rule and phases.
- Each system is one entry with its PV arrays, battery and subsystems inside. Opening it shows a menu with
  everything in it, and each item opens the form you know. The list of entries shows only the systems, so it is
  clear what belongs together. The update moves existing PV arrays, batteries and subsystems into their systems;
  their devices, entities, history and what was learned stay. Deleting a system now deletes what is inside it
  as well, with what was learned for its PV arrays.
- The template *Summarize only* is gone. The update turns its subsystems into systems of their own, with the
  behaviour they had, and removes the empty group; a group with an output limit or balancing batteries becomes
  *Coupled systems*. Their PV arrays keep their entities, history and what was learned.

### Fixed
- The household's zero export no longer makes a battery count for PV it is not coupled with. Learning left out
  the hours of every PV array whenever a battery at the house grid was full, although an inverter that holds the
  export at zero does not know that battery and curtails the same with it or without; and it counted hours in
  which a battery charged at its maximum as "at least this much" also in a system at *Full power*, whose
  rest goes out. What counts now follows from the systems alone: the battery of the PV array's own system, and
  those of subsystems that follow the house load together or share an output limit. Learning may use more hours
  from the next nightly run on. A system with the regulation *Self-consumption* and no battery is still not checked for
  the hours it held back (the *Installation* tab says "not used yet"). The household setting *Battery state of
  charge* still counts for every array.

## [0.5.0b2] - 2026-11-14

The second pre-release of 0.5.0: the *Installation* tab shows the installation as a flow diagram, and parts
can be moved to another system there. Learning is unchanged from 0.5.0b1.

### Added
- In the *Installation* tab, *Belongs to* moves a PV array, a battery or a system to another system without opening
  its form. The same rules apply as in the forms, and Noxcast reloads afterwards.

### Changed
- The *Installation* tab draws the installation as a flow diagram: PV arrays, systems with their batteries and limits,
  house and grid, with a dot per part for how far Noxcast computes its settings. Tapping a part shows its settings.
  The forms for systems, PV arrays and batteries show the same diagram on top.

## [0.5.0b1] - 2026-11-14

The first pre-release of 0.5.0. The update converts the configuration into systems and changes which hours learning
uses, so it runs through a nightly learning run before 0.5.0. After the update, check the Repairs notices and the new
*Installation* tab, which shows per setting how far it counts.

### Added
- Systems: a new entry *System* describes how modules and batteries reach the house: an inverter, a device with PV
  and battery, a battery at the house grid, devices that coordinate (e.g. EcoFlow Stream), or several systems summed
  up for the overview. Templates fill in the settings (output limit, per phase, regulation, when the battery is full,
  where the battery charges from, batteries balancing each other); a system can hold subsystems that share its
  limit. Every PV array and every battery belongs to a system, chosen in its own form.
- A new panel tab *Installation* shows the installation as entered (household, systems, subsystems, PV arrays,
  batteries) and per setting whether Noxcast computes it exactly, simplified or not yet. Settings that do not count
  yet are stored and count in later versions without new input.
- The household's feed-in can be limited to a value in W, besides free and none. The limit is stored now and counts
  in a later version.
- A Repairs notice asks which PV arrays charge a battery when the update could not tell (see below).
- The output limit of a system without a battery counts: hours at it are "at least this much" in learning, and it
  caps the PV forecast of a system with one PV array (with several, the forecast is not capped yet).
- Subsystems with batteries that share their system's output limit (e.g. EcoFlow Stream Ultra and AC Pro behind
  800 W) deliver together at most that limit in the battery forecast.
- A battery charging at its maximum power may hold PV back where the rest cannot go out (the system covers the house
  load, an output limit holds, or zero export): learning counts those hours as "at least this much", and the
  correction of today's forecast does not read them as clouds. It needs the battery power sensor and the maximum
  charge power, and applies from the next learning run (`noxcast.relearn` applies it at once).

### Changed
- A full battery leaves hours out of learning only for the PV arrays it can curtail: those of its own system, of
  subsystems that cover the house load together with it, and of subsystems without a battery under a shared output
  limit; with zero export a battery at the house grid counts for every array. An array elsewhere keeps learning from
  those hours, and an array whose system battery is entered now leaves its full hours out even without the sensors
  in the main entry. A battery whose upper limit comes from an entity counts as full from that limit. The battery
  state of charge sensors in the main entry still count for every array; remove them there once all batteries are
  entered with their systems, then run `noxcast.relearn`.
- Entering or changing the output limit of a system without a battery relearns its PV arrays from the history.
- Migrated automatically, nothing to do in most cases: the configuration becomes systems on the
  first start. Every PV array gets a system of its own; a battery with a PV array behind its inverter becomes a
  system *PV with battery in one device* with that array and its output limit; an AC battery a system *Battery at
  the house grid*; two or more batteries become subsystems of a system *Batteries* that balance each other, as they
  were simulated before. *Surplus is fed into the grid* becomes the feed-in *free* or *none*. Forecasts, learning
  and entities stay as they were. A DC battery without a PV array gets a Repairs notice: choose its system in the
  PV arrays behind it, or change it to *Battery at the house grid*.
- The battery form no longer has *Coupling* and *Shared inverter*: where the battery sits and the output limit are
  set on its system. A battery added without a system gets one of its own at the house grid (default efficiency
  90 %, as AC before); choose *PV with battery in one device* for a battery before the output (95 %).
- A system's template can be changed; the output limit and the other values the new template does not set are
  kept.

### Fixed
- The shade area in the day card keeps the shape of the shadow once the hours have passed. It could swell into a
  hump or shrink where a shaded hour produced more or less than forecast, although that mostly showed how well the
  shadow edge was learned, not the weather. The loss to shade now follows how bright the day was as a whole.

## [0.4.0] - 2026-11-13

All changes since 0.3.1: the pre-release 0.4.0b1 (its single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)) and, since then, the battery level in
the day card, a full battery just below its upper limit and the limit step in the day view. Nothing needs to be done
after the update; arrays without a power limit learn as before.

### Added
- Noxcast suggests a power limit when the measurements show one: if every power sensor of a PV array stays flat at
  or near its maximum on several days while the sun would have changed the power, and never goes above it, a Repairs notice
  asks whether to set that maximum as the array's *Power limit* (*Set limit*, *Other value*, *There is no limit*).
  Nothing changes until you answer; "There is no limit" stops the notice until the level moves.
- PV arrays can have a power limit (*Power limit* in the array's settings): when an MPPT input or a micro-inverter
  lets through less than the modules deliver, the forecast, *Expected today*, the P10–P90 range and the battery
  forecast are capped at it. The cap comes last, so the correction during the day and the range still see the
  clouds. An hour that reaches the limit only for part of the time loses only that part. The forecast sensors show
  `limit_w` and `limited_wh` (energy above the limit), and the forecast archive marks hours at the limit.
- The forecast archive keeps, for each hour of tomorrow, how far the runs of the weather ensembles (ICON-EPS,
  ECMWF ENS) spread. Noxcast does not use it yet: after a few weeks it shows whether the range of a forecast
  can tell sure days from unsure ones better than now. One more request to Open-Meteo every 3 hours.
- The forecast archive also keeps, for each day from now on, the published P10–P90 range, the split of the
  irradiance into direct and diffuse for each weather model, and the intraday correction applied each hour. Nothing
  uses them yet: they allow seasonal checks of the range and the corrections and a fair comparison of weather
  sources later.

### Changed
- Arrays with a power limit learn efficiency and shade correctly from hours at the limit: such an hour counts as
  "at least this much" instead of as the real output, which made the efficiency too low and midday look shaded.
  Sun positions where most clear hours were at the limit show no shade on the panel's map and the shading image,
  marked *at the power limit*. With a limit far below what the modules deliver, the efficiency comes from low sun
  and may be a few percent off; where even those hours do not show it, the typical 90 % is used and the panel says
  so. Setting or changing a limit relearns the array at once. The diagnostics' hour table gets an `at_limit` column.
  Arrays without a limit learn exactly as before.
- Panel, Overview: *So far* and the badge at the top (*As forecast*, *Sunnier* or *Duller than forecast*) compare
  the measured hours with the morning forecast. Before, they compared with the latest forecast, which follows the
  measured hours during the day, so a day far below the morning forecast could still show *As forecast*.
- The forecast archive keeps 400 days instead of 120, and the *Day* page goes back as far. The range (P10–P90) and
  the *Forecast accuracy* page still use the last 120 days, so nothing changes there. The archive grows to about
  9 MB over a year with two arrays.
- The day card shows a full battery in the battery level instead of as boxes: ahead, the simulated line and a soft
  tint below it turn amber in the hours in which the battery is full and surplus is lost, from the moment it is
  full; hours in which that happens only with more sun (PV at P90) are hatched. Surplus above a battery's charge
  power while it still has room is not marked. Hovering an hour shows how much is lost. Past hours and the "almost full"
  marking are no longer shown. The legend says *full, surplus lost* and *full with more sun*.

### Fixed
- The shade loss in the day card no longer shows a peak above what the sun can deliver: in a shaded hour that
  produced a little more diffuse light than forecast, the loss was scaled up with it. The output without shade that the
  card draws now stays below the unshaded clear-sky output.
- The correction of today's forecast from the measured hours no longer reads PV cut by a full battery as clouds.
  Before, a battery that was full at noon lowered the expected PV for the rest of the day. An hour in which a
  battery reached *Battery considered full at* now only counts when it was sunnier than forecast;
  if every recent hour was cut and duller than forecast, today's forecast stays as it is. This needs *Battery state
  of charge sensors* in the settings.
- A battery that stops charging just below its upper limit now counts as full: while there is surplus, a battery
  within 2 percentage points of its limit takes nothing more in the battery forecast, so *Full at*, the day card's
  warning and the panel's *Battery & surplus* show the surplus as lost from now on instead of expecting it to fit the
  last percent. With several batteries this applies to each one on its own.
- Panel, Day: for an array with a power limit, *How the hour comes about* and the energy flow end with the step
  *Limit* at the capped forecast. Before, the steps ended at the value without the limit, above the forecast in the
  chart.

## [0.4.0b1] - 2026-11-13

The first pre-release of 0.4.0. Arrays with a power limit learn differently, so it runs through a nightly learning
run before 0.4.0. Nothing needs to be done after the update; arrays without a power limit learn as before.

### Added
- Noxcast suggests a power limit when the measurements show one: if every power sensor of a PV array stays flat at
  or near its maximum on several days while the sun would have changed the power, and never goes above it, a Repairs notice
  asks whether to set that maximum as the array's *Power limit* (*Set limit*, *Other value*, *There is no limit*).
  Nothing changes until you answer; "There is no limit" stops the notice until the level moves.
- PV arrays can have a power limit (*Power limit* in the array's settings): when an MPPT input or a micro-inverter
  lets through less than the modules deliver, the forecast, *Expected today*, the P10–P90 range and the battery
  forecast are capped at it. The cap comes last, so the correction during the day and the range still see the
  clouds. An hour that reaches the limit only for part of the time loses only that part. The forecast sensors show
  `limit_w` and `limited_wh` (energy above the limit), and the forecast archive marks hours at the limit.
- The forecast archive keeps, for each hour of tomorrow, how far the runs of the weather ensembles (ICON-EPS,
  ECMWF ENS) spread. Noxcast does not use it yet: after a few weeks it shows whether the range of a forecast
  can tell sure days from unsure ones better than now. One more request to Open-Meteo every 3 hours.
- The forecast archive also keeps, for each day from now on, the published P10–P90 range, the split of the
  irradiance into direct and diffuse for each weather model, and the intraday correction applied each hour. Nothing
  uses them yet: they allow seasonal checks of the range and the corrections and a fair comparison of weather
  sources later.

### Changed
- Arrays with a power limit learn efficiency and shade correctly from hours at the limit: such an hour counts as
  "at least this much" instead of as the real output, which made the efficiency too low and midday look shaded.
  Sun positions where most clear hours were at the limit show no shade on the panel's map and the shading image,
  marked *at the power limit*. With a limit far below what the modules deliver, the efficiency comes from low sun
  and may be a few percent off; where even those hours do not show it, the typical 90 % is used and the panel says
  so. Setting or changing a limit relearns the array at once. The diagnostics' hour table gets an `at_limit` column.
  Arrays without a limit learn exactly as before.
- Panel, Overview: *So far* and the badge at the top (*As forecast*, *Sunnier* or *Duller than forecast*) compare
  the measured hours with the morning forecast. Before, they compared with the latest forecast, which follows the
  measured hours during the day, so a day far below the morning forecast could still show *As forecast*.
- The forecast archive keeps 400 days instead of 120, and the *Day* page goes back as far. The range (P10–P90) and
  the *Forecast accuracy* page still use the last 120 days, so nothing changes there. The archive grows to about
  9 MB over a year with two arrays.

### Fixed
- The shade loss in the day card no longer shows a peak above what the sun can deliver: in a shaded hour that
  produced a little more diffuse light than forecast, the loss was scaled up with it. The output without shade that the
  card draws now stays below the unshaded clear-sky output.
- The correction of today's forecast from the measured hours no longer reads PV cut by a full battery as clouds.
  Before, a battery that was full at noon lowered the expected PV for the rest of the day. An hour in which a
  battery reached *Battery considered full at* now only counts when it was sunnier than forecast;
  if every recent hour was cut and duller than forecast, today's forecast stays as it is. This needs *Battery state
  of charge sensors* in the settings.

## [0.3.1] - 2026-11-09

All changes since 0.3.0, tested in the pre-release 0.3.1b1 (its single steps are listed in
[CHANGELOG.md](https://github.com/TelosNox/noxcast/blob/main/CHANGELOG.md)). Nothing needs to be done after the
update.

### Changed
- Panel, Overview: the chart of today looks like the one on the *Day* page: measured hours as a solid area, the
  expectation for the rest of the day pale, smooth curves, the forecast dashed and clear sky as a dotted upper
  bound.
- Panel, Overview: *Expected today* and *Rest of today* show two decimals, *So far* one like the other forecasts;
  *So far* says until which full hour it counts, which is why it and *Rest of today* do not add up to *Expected
  today* during the current hour.
- Panel, Overview: *Power now* is shown in W below 1 kW and in kW with two decimals above.
- Panel, Day: the day's main value and the main value of each group show two decimals; forecasts, ranges and the
  energy flow keep one. The scale of the chart is in kW.
- Dashboard card: the large actual value of today shows two decimals, and power in kW two decimals.

### Fixed
- Consumer planning no longer recommends a window without surplus, such as tomorrow at midnight in winter. When no
  start gets at least 5 % of the run from surplus, *Best window today* and *Best window tomorrow* are unknown, and
  the Overview shows *No surplus* instead of *Better wait* when there is none now either.
- *Forecast today* of an array added after 6:00 followed every new weather forecast until midnight, and the total
  with it. It is now fixed at the array's first update; `fixed_at` shows that time, for the total the latest fix.
- Panel, Overview: the actual curve no longer runs out of the chart on the left, and the top scale value is no
  longer cut off; the scale now sits inside the right edge, without the zero, as on the dashboard card.
- Panel, Overview: the line under *Power now* shows the expected power of this hour, the value the chart draws,
  instead of the morning forecast.

## [0.3.1b1] - 2026-11-08

The first pre-release of 0.3.1. It stores when an array added during the day was fixed, so it runs through a
nightly learning run before 0.3.1.

### Changed
- Panel, Overview: *Expected today*, *So far* and *Rest of today* show two decimals; *So far* says until which full
  hour it counts, which is why it and *Rest of today* do not add up to *Expected today* during the current hour.
- Panel, Overview: *Power now* is shown in W below 1 kW, as on the dashboard card, and in kW with two decimals
  above; the dashboard card also shows kW with two decimals now.

### Fixed
- *Forecast today* of an array added after 6:00 followed every new weather forecast until midnight, and the total
  with it. It is now fixed at the array's first update; `fixed_at` shows that time, for the total the latest fix.
- Panel, Overview: the actual curve no longer runs out of the chart on the left, and the top scale value is no
  longer cut off; the scale now sits inside the right edge, without the zero, as on the dashboard card.
- Panel, Overview: the line under *Power now* shows the expected power of this hour, the value the chart draws,
  instead of the morning forecast.

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
