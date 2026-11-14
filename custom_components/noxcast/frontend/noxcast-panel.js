// Noxcast sidebar panel: what the system has learned per array (shade, reflection, direction factor),
// plus further sections that live in their own modules (e.g. forecast accuracy).
// Plain custom element, no build step. Data comes from the websocket command "noxcast/learned".
const { WS_SCHEMA, ask, errorText, esc } = await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href);

// further sections: [key, custom element, module]; loaded on first use with this module's cache buster
const SECTIONS = [["overview", "noxcast-overview", "./noxcast-overview.js"], ["day", "noxcast-day", "./noxcast-day.js"], ["accuracy", "noxcast-accuracy", "./noxcast-accuracy.js"], ["load", "noxcast-load", "./noxcast-load.js"],
  ["battery", "noxcast-battery", "./noxcast-battery.js"], ["installation", "noxcast-installation", "./noxcast-installation.js"],
  ["diagnostics", "noxcast-diagnostics", "./noxcast-diagnostics.js"]];
// shared look of the panel and its sections, loaded with this module's cache buster
const { adoptTheme, LOGO, ORBIT } = await import(new URL("./noxcast-theme.js" + new URL(import.meta.url).search, import.meta.url).href);

const SECTION_LEADS = {
  de: { overview: "Was heute und in den nächsten Tagen ansteht. Jede Zahl führt in den Bereich, der sie erklärt.",
    day: "Ein Tag im Detail: die Gruppen übereinander, Prognose gegen Erwartung und Ist, das Wetter jeder Stunde und wie eine Stunde zustande kommt.",
    learned: "Was Noxcast über jede Modulfläche gelernt hat: Schatten, Reflexion und Richtung, Sonnenposition für Sonnenposition.",
    accuracy: "Wie gut die Prognose vom Vortag eingetroffen ist, nach Tag, Wetterlage und Wetterquelle.",
    load: "Wann im Haus wie viel verbraucht wird, aus welchen Teilen sich das zusammensetzt und wie verlässlich die Prognose ist.",
    battery: "Wohin der Überschuss heute und morgen geht und was sich mit mehr Kapazität oder einem weiteren Verbraucher ändert.",
    installation: "Deine Anlage, wie du sie eingetragen hast: Haushalt, Systeme, PV-Gruppen und Akkus, und wie weit Noxcast jede Angabe schon verrechnet.",
    diagnostics: "Welche Daten Noxcast liest, wie vollständig sie sind und wo etwas fehlt." },
  en: { overview: "What is due today and in the days ahead. Every figure leads to the section that explains it.",
    day: "One day in detail: the groups stacked, forecast against expectation and actual, the weather of each hour and how an hour comes about.",
    learned: "What Noxcast has learned about each array: shade, reflection and direction, sun position by sun position.",
    accuracy: "How well the day-ahead forecast came true, by day, weather and weather source.",
    load: "When the house uses how much, which parts it is made of and how reliable the forecast is.",
    battery: "Where today's and tomorrow's surplus goes and what changes with more capacity or another consumer.",
    installation: "Your installation as you entered it: household, systems, PV arrays and batteries, and how far Noxcast already computes each setting.",
    diagnostics: "Which data Noxcast reads, how complete it is and where something is missing." },
};
// Home Assistant icons of the sections; on a phone the tabs get their own row, each with its icon over a short title
const SECTION_ICONS = { overview: "mdi:view-dashboard", learned: "mdi:brain", day: "mdi:calendar-today", accuracy: "mdi:target", load: "mdi:home-lightning-bolt",
  battery: "mdi:battery-charging", installation: "mdi:sitemap-outline", diagnostics: "mdi:stethoscope" };

const SECTION_TITLES = { de: { overview: "Übersicht", day: "Tag", learned: "Gelerntes", accuracy: "Prognosegüte", load: "Last", battery: "Akku & Überschuss", installation: "Anlage", diagnostics: "Diagnose" },
  en: { overview: "Overview", day: "Day", learned: "Learned", accuracy: "Forecast accuracy", load: "Load", battery: "Battery & surplus", installation: "Installation", diagnostics: "Diagnostics" } };
const SECTION_SHORT = { de: { overview: "Übersicht", day: "Tag", learned: "Gelernt", accuracy: "Güte", load: "Last", battery: "Akku", installation: "Anlage", diagnostics: "Diagnose" },
  en: { overview: "Overview", day: "Day", learned: "Learned", accuracy: "Accuracy", load: "Load", battery: "Battery", installation: "Setup", diagnostics: "Data" } };

const AZ_STEP = 10, EL_STEP = 5, COARSE_AZ = 20, COARSE_EL = 15;
const RAD = Math.PI / 180;


const TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden",
    status: { title: "Lernstand", running: "Lernlauf läuft – das dauert einige Minuten.", last: "Letzter Lernlauf {t}, Dauer {d}",
      never: "Noch kein Lernlauf.", failed: "Letzter Lernlauf fehlgeschlagen: {e}", next: "Nächster automatisch {t}",
      relearn: "Jetzt neu lernen", busy: "Läuft …",
      stages: { waiting: "wartet auf den ersten Lernlauf",
        early: "lernt noch: {h} Stunden aus {d} Tagen. Der Wirkungsgrad ist vorläufig, belastbar ab {D} Tagen und {H} Stunden.",
        growing: "Wirkungsgrad belastbar ({h} Stunden aus {d} Tagen). Die Schattenkarte ist zu {c} gefüllt und wächst, während die Sonne ihren Weg durchs Jahr nimmt.",
        settled: "eingelernt: {h} Stunden aus {d} Tagen, Schattenkarte zu {c} gefüllt." } },
    title: "Gelerntes", overview: "Alle Gruppen", map: "Karte", grid: "Jahresraster", pano: "Panorama",
    panoKinds: { plane: "eigene Modulebene (Dach, Wand, Brüstung)", obstacle: "Hindernis, gemessen", facade: "Hindernis, aus der Horizontlinie",
      inferred: "Hindernis, nach unten ergänzt", partial: "Teilschatten", free: "frei", none: "noch keine Daten",
      never: "hier steht die Sonne nie", ground: "unter dem Horizont" },
    panoIdle: "Ziehen schwenkt den Blick, Mausrad oder zwei Finger zoomen. Zeiger auf dem Bild: Richtung und Höhe.",
    panoAt: "Richtung {a}° ({d}) · Höhe {e}° · {k}", panoHome: "Blickrichtung Modul", panoFace: "Modul",
    shade: "Schatten", refl: "Reflexion", dir: "Richtungsfaktor",
    efficiency: "Wirkungsgrad", skyView: "Himmelssicht", tempCoeff: "Temperaturkoeff.", coverage: "Abdeckung Karte",
    facade: "Horizontlinie (Höhe · Richtung · Bereich)", fromFacade: "aus der Horizontlinie",
    dirRange: "Richtungsfaktor", reflMax: "Reflexion max.", samples: "Stunden", since: "Daten seit", learnedAt: "Letzter Lernlauf",
    free: "frei", partial: "Teilschatten", shaded: "Schatten", none: "keine", less: "weniger", more: "mehr",
    atLimit: "an der Leistungsgrenze", typical: "typischer Wert, an der Grenze nicht messbar", notLearned: "nicht gelernt", behind: "Sonne hinter Modul", today: "Heute", play: "Jahr abspielen", pause: "Anhalten",
    sunPathOn: "Sonnenbahn am", jun: "21. Juni", equ: "März/Sept.", dec: "21. Dez.",
    dayStrip: "Dieser Tag als Zeitleiste", selected: "Ausgewähltes Feld", sunHere: "Sonne steht hier", normal: "Sonne senkrecht auf den Modulen",
    never: "nie im Lauf des Jahres", close: "Schließen", noData: "Noch keine PV-Gruppe. Füge eine hinzu unter Einstellungen → Geräte & Dienste → Noxcast → ⋮ → PV-Gruppe hinzufügen; Noxcast lernt sie dann sofort aus der Historie.",
    open: "Öffnen", months: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
    help: {
      shade: "Jedes Feld ist eine Sonnenposition. Die Farbe zeigt, wie viel Direktlicht dort ankommt. Folge einer Sonnenbahn von links nach rechts, um einen Tag abzulesen.",
      facade: "Felder mit dem Hinweis „aus der Horizontlinie“ sind nicht gemessen. Für Sonnenstände ohne genug klare Stunden, meist im Winter, rechnet Noxcast mit einer geraden Linie für das Hindernis am Horizont, etwa Häuser, Wald oder Hügel. Sie gleicht Unebenheiten aus, einzelne höhere Gebäude oder Bäume fehlen darin. Sobald ein Feld gemessen ist, ersetzt der Messwert die Linie.",
      limit: "Felder mit Punkt liegen an der Leistungsgrenze: Dort erreichte die PV-Gruppe in den meisten klaren Stunden ihre Grenze. Ein Schatten ist dort nur zu sehen, wenn er die Leistung unter die Grenze drückt, deshalb rechnet Noxcast an diesen Sonnenständen ohne Schatten. An klaren Tagen begrenzt ohnehin die Grenze die Prognose.",
      refl: "Zusätzliches Licht von beschienenen Wänden oder Dächern, in Prozent der Direktstrahlung. Gelernt nur dort, wo das Modul selbst wenig Direktlicht bekommt.",
      dir: "Was nach Schatten und Reflexion übrig bleibt: Geometrie- und Modellfehler. Werte nahe 1 sind gut.",
      grid: "Jede Zeile ein Monat, jede Spalte eine Stunde (Ortszeit). Dieselben Daten wie die Karte, ohne Sonnenwinkel.",
      pano: [
        "Das Bild zeigt, was eine Kamera auf dem Modul sähe, die in Blickrichtung des Moduls fotografiert. Wie auf einem Foto bleiben gerade Kanten gerade: Hauswände, Dachkanten und Ecken sehen so aus wie vor Ort. Ziehen schwenkt den Blick nach links und rechts oder schiebt ihn nach oben und unten, Mausrad oder zwei Finger ändern den Bildwinkel. Das Dreieck am Horizont zeigt, wohin das Modul schaut.",
        "Noxcast sieht die Umgebung nur über die Sonne: wo sie stand und wie viel Licht ankam. Gemessen ist deshalb nur der Streifen zwischen der Dezember- und der Junibahn. Über der Junibahn steht die Sonne nie; ein Hindernis kann dort weitergehen, ohne dass Noxcast es sieht. Ein Hindernis zeigt das Bild, wo weniger als die Hälfte des Direktlichts ankommt, Teilschatten, wo es mehr ist; die Abstufungen zeigt die Karte.",
        "Unter der Dezemberbahn steht die Sonne ebenfalls nie. Liegt direkt darüber ein Hindernis, zieht Noxcast es blasser bis zum Horizont weiter, denn Häuser stehen auf dem Boden. Bei einem Baum mit freiem Stamm oder einem Balkon über dem Modul stimmt das nicht.",
        "Die gepunktete Fläche ist die eigene Modulebene, also die Verlängerung der Modulfläche: Dahinter erreicht die Sonne das Modul nicht. Liegt ihre Kante vor Ort sichtbar anders, stimmen Neigung oder Ausrichtung nicht.",
        "Gelernt wird in Feldern von 10° Richtung und 5° Höhe, Kanten sind deshalb weich und leicht gestuft.",
      ],
    },
    clickHint: "Feld anklicken für Details",
    checks: {
      balance: "Klarer Tag je Monat", clearDay: "Klarer Tag", shade: "Schatten", refl: "Reflexion", dir: "Richtung", temp: "Wärme",
      balanceHelp: "Ein wolkenloser Tag in der Monatsmitte mit allem Gelernten. Die Zeilen zeigen, wie viel jeder Effekt an diesem Tag ändert, in Prozent. Wärme nutzt die mittlere Lufttemperatur des Monats aus dem Verlauf; ohne Verlauf bleibt sie leer.",
      effTitle: "Wirkungsgrad je Monat", effHelp: "Aus klaren, unverschatteten Stunden des jeweiligen Monats. Langsamer Rückgang deutet auf Alterung, ein Einbruch auf Verschmutzung oder Schnee; ein Jahresgang eher auf Modellfehler bei tiefer Sonne.",
      effLearned: "gelernt", hours: "Std.", noEff: "Noch zu wenige klare Stunden je Monat.",
      geoTitle: "Geometrie-Check", configured: "Eingetragen", bestFit: "Passt am besten", spread: "Streuung",
      confidence: { high: "hoch", medium: "mittel", low: "gering" }, trust: "Vertrauen",
      geoOk: "Neigung und Ausrichtung passen zu den Messwerten.",
      geoHint: "Die Messwerte passen besser zu einer anderen Geometrie. Bitte Neigung und Ausrichtung prüfen; übernommen wird nichts automatisch, weil die Schattenkarte mit der eingetragenen Geometrie gelernt wurde.",
      geoNone: "Braucht den Satellitenfilter und mindestens 60 klare Stunden, in denen die Schattenkarte die Sonnenposition als frei kennt, davon mindestens 15 auf jeder Seite der Modulausrichtung.",
      geoPlanes: "Mehrere Ausrichtungen in einer PV-Gruppe: Jede Fläche wird für sich gerechnet. Eine einzelne beste Ebene beschreibt sie nicht, deshalb gibt es hier keinen Geometrie-Vorschlag.",
      geoHelp: "Bei der richtigen Geometrie ist das Verhältnis von Messung zu Modell über Tag und Jahr gleich. Eine falsche Ausrichtung zeigt sich als zu hohe Vormittage und zu niedrige Nachmittage (oder umgekehrt).",
      badge: "Geometrie prüfen",
      plTitle: "Passt die Konfiguration?", maxPower: "Höchste Stundenleistung", ofPeak: "von {p} Wp", effLearnedTile: "Wirkungsgrad",
      plOk: "Messwerte und Konfiguration passen zusammen.",
      plHelp: "Der Wirkungsgrad ist das Verhältnis von Messung zu dem, was die eingetragenen Module bei klarem Himmel liefern müssten. Schatten und wenig Daten drücken ihn nach unten; über 100 % kommt er nur, wenn die Konfiguration nicht zur Anlage passt. Die Lernstunden einzeln stehen im Diagnose-Download der Integration.",
      hint: {
        peak: "Die höchste gemessene Stundenleistung liegt über der eingetragenen Spitzenleistung. Das schafft kein Modul: Entweder sind die Wp zu niedrig eingetragen (ein Modul vergessen, die Wp eines Moduls statt aller Module) oder der Sensor misst mehr als diese Gruppe, etwa einen weiteren Eingang oder einen Eingang, der auch in einer anderen Gruppe steckt.",
        high: "Der Wirkungsgrad liegt über 105 %: Die Gruppe liefert mehr, als die eingetragenen Module bei dieser Ausrichtung können. Bitte prüfen: die Wp je Ausrichtung, ob der Sensor nur diese Gruppe misst, und ob die Module steiler oder anders ausgerichtet sind als eingetragen.",
        direction: "Vormittags und nachmittags ergibt sich ein deutlich anderer Wirkungsgrad. Das deutet auf eine falsch eingetragene Ausrichtung: Ist der Vormittag höher, zeigen die Module weiter nach Osten als eingetragen, ist der Nachmittag höher, weiter nach Westen. Auch ein Schatten, den die Karte noch nicht kennt, kann eine Seite drücken.",
        season: "Der Wirkungsgrad schwankt stark zwischen den Monaten. Steigt er zum Winter hin, sind die Module steiler als eingetragen, fällt er, flacher. Bei tiefer Sonne können auch Modellfehler dahinterstecken.",
      },
      dirTitle: "Wirkungsgrad je Sonnenrichtung", dirNone: "Noch zu wenige klare Stunden je Richtung.",
      dirHelp: "Aus denselben Stunden wie je Monat, sortiert nach der Richtung, aus der die Sonne kam. Bei richtig eingetragener Ausrichtung sind die Balken etwa gleich hoch. Funktioniert auch mit mehreren Ausrichtungen in einer Gruppe.",
      sectors: { N: "N", NE: "NO", E: "O", SE: "SO", S: "S", SW: "SW", W: "W", NW: "NW" },
      badgeCheck: "Prüfen",
    },
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    status: { title: "Learning", running: "Learning run in progress – this takes a few minutes.", last: "Last learning run {t}, took {d}",
      never: "No learning run yet.", failed: "Last learning run failed: {e}", next: "Next one automatically {t}",
      relearn: "Learn again now", busy: "Running …",
      stages: { waiting: "waiting for the first learning run",
        early: "still learning: {h} hours from {d} days. The efficiency is provisional, reliable from {D} days and {H} hours.",
        growing: "efficiency reliable ({h} hours from {d} days). The shading map is {c} filled and grows as the sun's path moves through the year.",
        settled: "learned: {h} hours from {d} days, shading map {c} filled." } },
    title: "Learned", overview: "All arrays", map: "Map", grid: "Year grid", pano: "Panorama",
    panoKinds: { plane: "the module's own plane (roof, wall, railing)", obstacle: "obstacle, measured", facade: "obstacle, from the horizon line",
      inferred: "obstacle, continued downwards", partial: "partial shade", free: "free", none: "no data yet",
      never: "the sun is never here", ground: "below the horizon" },
    panoIdle: "Drag to swivel the view, mouse wheel or two fingers to zoom. Pointer on the picture: direction and height.",
    panoAt: "Direction {a}° ({d}) · height {e}° · {k}", panoHome: "Module direction", panoFace: "Module",
    shade: "Shade", refl: "Reflection", dir: "Direction factor",
    efficiency: "Efficiency", skyView: "Sky view", tempCoeff: "Temp. coefficient", coverage: "Map coverage",
    facade: "Horizon line (height · direction · range)", fromFacade: "from the horizon line",
    dirRange: "Direction factor", reflMax: "Reflection max.", samples: "Hours", since: "Data since", learnedAt: "Last learning run",
    free: "free", partial: "partial shade", shaded: "shade", none: "none", less: "less", more: "more",
    atLimit: "at the power limit", typical: "typical value, not measurable at the limit", notLearned: "not learned yet", behind: "sun behind module", today: "Today", play: "Play year", pause: "Pause",
    sunPathOn: "Sun path on", jun: "21 June", equ: "Mar/Sep", dec: "21 Dec",
    dayStrip: "This day as a timeline", selected: "Selected cell", sunHere: "Sun is here", normal: "Sun perpendicular to the modules",
    never: "never during the year", close: "Close", noData: "No PV array yet. Add one under Settings → Devices & services → Noxcast → ⋮ → Add PV array; Noxcast then learns it from the history right away.",
    open: "Open", months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    help: {
      shade: "Each cell is a sun position. The colour shows how much direct light arrives there. Follow a sun path from left to right to read a day.",
      facade: "Cells marked \"from the horizon line\" are not measured. For sun positions without enough clear hours, mostly in winter, Noxcast uses one straight line for the obstacle on the horizon, such as houses, a forest or a hill. It evens out irregularities, so a single taller building or tree is missing from it. Once a cell is measured, the measurement replaces the line.",
      limit: "Cells with a dot are at the power limit: there the array reached its limit in most clear hours. Shade shows there only when it pushes the power below the limit, so Noxcast assumes no shade at these sun positions. On clear days the limit caps the forecast anyway.",
      refl: "Extra light from sunlit walls or roofs, in percent of the direct normal irradiance. Learned only where the module itself gets little direct light.",
      dir: "What remains after shade and reflection: geometry and model errors. Values close to 1 are good.",
      grid: "Each row a month, each column an hour (local time). The same data as the map, without sun angles.",
      pano: [
        "The picture shows what a camera on the module would see, taking a photo in the direction the module faces. As on a photo, straight edges stay straight: house walls, roof edges and corners look as they do on site. Drag to swivel the view left and right or to move it up and down; the mouse wheel or two fingers change the angle of view. The triangle on the horizon marks where the module faces.",
        "Noxcast sees the surroundings only through the sun: where it stood and how much light arrived. So only the band between the December and the June path is measured. Above the June path the sun never stands; an obstacle may go on there without Noxcast seeing it. The picture shows an obstacle where less than half of the direct light arrives, partial shade where more does; the map shows the shades in between.",
        "Below the December path the sun never stands either. Where an obstacle lies just above it, Noxcast continues it, paler, down to the horizon, because houses stand on the ground. For a tree with a bare trunk or a balcony above the module this is not true.",
        "The dotted area is the module's own plane, the module surface extended: behind it the sun cannot reach the module. If its edge looks clearly different on site, tilt or azimuth are wrong.",
        "Learning works in cells of 10° direction and 5° height, so edges are soft and slightly stepped.",
      ],
    },
    clickHint: "Click a cell for details",
    checks: {
      balance: "Clear day per month", clearDay: "Clear day", shade: "Shade", refl: "Reflection", dir: "Direction", temp: "Heat",
      balanceHelp: "A cloudless day in the middle of the month with everything learned. The rows show how much each effect changes that day, in percent. Heat uses the mean air temperature of that month from the history; without history it stays empty.",
      effTitle: "Efficiency per month", effHelp: "From clear unshaded hours of each month. A slow decline points at ageing, a dip at soiling or snow; a yearly swing rather at model errors at low sun.",
      effLearned: "learned", hours: "h", noEff: "Not enough clear hours per month yet.",
      geoTitle: "Geometry check", configured: "Configured", bestFit: "Fits best", spread: "Spread",
      confidence: { high: "high", medium: "medium", low: "low" }, trust: "Confidence",
      geoOk: "Tilt and azimuth fit the measurements.",
      geoHint: "The measurements fit another geometry better. Please check tilt and azimuth; nothing is applied automatically, since the shading map was learned with the configured geometry.",
      geoNone: "Needs the satellite filter and at least 60 clear hours whose sun position the shading map knows as free, at least 15 of them on each side of the modules' direction.",
      geoPlanes: "Several orientations in one array: each plane is computed on its own. No single best plane describes them, so there is no geometry suggestion here.",
      geoHelp: "With the right geometry the ratio of measurement to model is the same over the day and the year. A wrong azimuth shows as mornings too high and afternoons too low (or the other way round).",
      badge: "Check geometry",
      plTitle: "Does the configuration fit?", maxPower: "Highest hourly power", ofPeak: "of {p} Wp", effLearnedTile: "Efficiency",
      plOk: "Measurements and configuration fit together.",
      plHelp: "The efficiency is the ratio of the measurement to what the configured modules should give under a clear sky. Shade and little data push it down; it only goes above 100 % when the configuration does not fit the installation. The single learning hours are in the integration's diagnostics download.",
      hint: {
        peak: "The highest measured hourly power is above the configured peak power. No module does that: either the peak power is set too low (a module left out, the peak power of one module instead of the whole array) or the sensor measures more than this array, such as a further input or an input that is also part of another array.",
        high: "The efficiency is above 105 %: the array gives more than the configured modules can at this orientation. Please check the peak power of each plane, whether the sensor measures this array only, and whether the modules are steeper or face another way than configured.",
        direction: "Morning and afternoon give clearly different efficiencies. That points at a wrong azimuth: a higher morning means the modules face further east than configured, a higher afternoon further west. A shadow the map does not know yet can also push one side down.",
        season: "The efficiency varies strongly between the months. Rising towards winter means the modules are steeper than configured, falling means flatter. At low sun, model errors can also be behind it.",
      },
      dirTitle: "Efficiency per sun direction", dirNone: "Not enough clear hours per direction yet.",
      dirHelp: "From the same hours as per month, sorted by the direction the sun came from. With the right azimuth the bars are about equally high. Works with several orientations in one array too.",
      sectors: { N: "N", NE: "NE", E: "E", SE: "SE", S: "S", SW: "SW", W: "W", NW: "NW" },
      badgeCheck: "Check",
    },
  },
};

const PALETTE = {
  light: {
    free: [233, 243, 226], mid: [214, 120, 72], shade: [122, 24, 24],
    base: [243, 241, 236], refl: [[243, 241, 236], [244, 190, 120], [209, 104, 22]],
    dir: [[47, 111, 176], [243, 241, 236], [217, 130, 43]],
    unlearned: "#D6D6D2", hatch: "rgba(255,255,255,0.6)", behind: "#EDEDEA", mapBg: "#FFFFFF", path: "#1D2125",
    pano: { sky: "#DCEAF7", partial: "#B9C6D2", obstacle: "#3F4246", facadeBg: "#9A9C9F", plane: "#6E7176", dot: "#9EA1A6", sun: "#E8890C",
      ground: "#D8D0C4", grid: "rgba(0,0,0,0.12)" },
  },
  dark: {
    free: [118, 168, 108], mid: [214, 132, 72], shade: [150, 36, 36],
    base: [66, 70, 74], refl: [[66, 70, 74], [176, 110, 50], [240, 150, 60]],
    dir: [[90, 150, 220], [66, 70, 74], [235, 150, 60]],
    unlearned: "#34373A", hatch: "rgba(255,255,255,0.14)", behind: "#2B2D30", mapBg: "#1E2022", path: "#E4E6E8",
    pano: { sky: "#25313D", partial: "#3A4652", obstacle: "#C9CCD0", facadeBg: "#7C8085", plane: "#5A5E63", dot: "#7E8389", sun: "#F5A524",
      ground: "#2E2A26", grid: "rgba(255,255,255,0.13)" },
  },
};

// ---------------------------------------------------------------- sun position (NOAA, as solar.py)

function sunPosition(lat, lon, ts) {
  const jd = ts / 86400 + 2440587.5, jc = (jd - 2451545) / 36525;
  const l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360;
  const m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc);
  const e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc);
  const c = Math.sin(m * RAD) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + Math.sin(2 * m * RAD) * (0.019993 - 0.000101 * jc) + Math.sin(3 * m * RAD) * 0.000289;
  const omega = 125.04 - 1934.136 * jc;
  const appLong = l0 + c - 0.00569 - 0.00478 * Math.sin(omega * RAD);
  const meanObliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60;
  const obliq = meanObliq + 0.00256 * Math.cos(omega * RAD);
  const decl = Math.asin(Math.sin(obliq * RAD) * Math.sin(appLong * RAD));
  const y = Math.tan(obliq * RAD / 2) ** 2;
  const eqTime = 4 / RAD * (y * Math.sin(2 * l0 * RAD) - 2 * e * Math.sin(m * RAD) + 4 * e * y * Math.sin(m * RAD) * Math.cos(2 * l0 * RAD) - 0.5 * y * y * Math.sin(4 * l0 * RAD) - 1.25 * e * e * Math.sin(2 * m * RAD));
  const tst = (((ts % 86400) / 60 + eqTime + 4 * lon) % 1440 + 1440) % 1440;
  const ha = tst / 4 - 180;
  const la = lat * RAD;
  const cosZen = Math.sin(la) * Math.sin(decl) + Math.cos(la) * Math.cos(decl) * Math.cos(ha * RAD);
  const zen = Math.acos(Math.max(-1, Math.min(1, cosZen)));
  const denom = Math.cos(la) * Math.sin(zen);
  let az = 180;
  if (Math.abs(denom) > 1e-9) {
    const a = Math.acos(Math.max(-1, Math.min(1, (Math.sin(la) * Math.cos(zen) - Math.sin(decl)) / denom))) / RAD;
    az = ha > 0 ? (a + 180) % 360 : (540 - a) % 360;
  }
  return { az, el: 90 - zen / RAD };
}

function cosIncidence(tilt, facing, az, el) {
  return Math.sin(el * RAD) * Math.cos(tilt * RAD) + Math.cos(el * RAD) * Math.sin(tilt * RAD) * Math.cos((az - facing) * RAD);
}

// every module plane of an array (several orientations measured together, else the one configured)
function planes(arr) {
  return arr.planes && arr.planes.length ? arr.planes : [{ tilt: arr.tilt, azimuth: arr.azimuth, peak_w: arr.peak_w }];
}

// the sun is behind the array only when it is behind every one of its planes
function behind(arr, az, el) {
  return planes(arr).every((p) => cosIncidence(p.tilt, p.azimuth, az, el) <= 0);
}

// ---------------------------------------------------------------- learned values (as model.py / visual.py)

function displayValue(cells, ai, ei) {
  const v = cells[`${ai},${ei}`];
  if (v !== undefined) return v;
  const near = [];
  for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) {
    const k = `${(ai + x + 36) % 36},${ei + y}`;
    if (k in cells) near.push(cells[k]);
  }
  return near.length >= 2 ? near.reduce((a, b) => a + b, 0) / near.length : null;
}

// learned cells; cells without a learned value take the learned horizon line where there is one
function cellsOf(arr) {
  return { ...(arr.facade_cells || {}), ...((arr.learned || {}).cells || {}) };
}

function coarse(values, az, el, dflt) {
  if (!values || !Object.keys(values).length) return dflt;
  const fa = az / COARSE_AZ - 0.5, fe = el / COARSE_EL - 0.5;
  const a0 = Math.floor(fa), e0 = Math.floor(fe), da = fa - a0, de = fe - e0;
  let v = 0;
  for (const [ai, wa] of [[a0, 1 - da], [a0 + 1, da]]) for (const [ei, we] of [[e0, 1 - de], [e0 + 1, de]]) {
    const k = `${((ai % 18) + 18) % 18},${ei}`;
    v += wa * we * (k in values ? values[k] : dflt);
  }
  return v;
}

// ---------------------------------------------------------------- panorama: the view from the module

const PANO_STEP = 0.5;                       // degrees per step of the sun's reach
const PANO_SHADE = 0.5;                      // below this share of direct light a sun position shows an obstacle
const PANO_FOV = [60, 150, 120];             // angle of view across: least, most, at the start
const PANO_ASPECT = 0.62;                    // height of the picture per width

// learned share of direct light at a sun position, interpolated between cell centres as the model does;
// facade: the value comes mostly from the horizon line; null where nothing is learned nearby
function panoValue(arr, az, el) {
  const cells = (arr.learned || {}).cells || {}, fc = arr.facade_cells || {};
  const fa = az / AZ_STEP - 0.5, fe = el / EL_STEP - 0.5;
  const a0 = Math.floor(fa), e0 = Math.floor(fe), da = fa - a0, de = fe - e0;
  let v = 0, w = 0, f = 0;
  for (const [ai, wa] of [[a0, 1 - da], [a0 + 1, da]]) for (const [ei, we] of [[e0, 1 - de], [e0 + 1, de]]) {
    const k = `${((ai % 36) + 36) % 36},${Math.max(ei, 0)}`;
    const x = k in cells ? cells[k] : k in fc ? fc[k] : null;
    if (x === null) continue;
    v += wa * we * x; w += wa * we;
    if (!(k in cells)) f += wa * we;
  }
  return w >= 0.25 ? { t: v / w, facade: f / w > 0.5 } : null;
}

// what the module sees in a direction: its own plane, an obstacle (measured, from the horizon line, or continued
// downwards), partial shade, free sky, nothing learned yet, a position the sun never takes, or the ground;
// reach: lowest and highest sun of the year per PANO_STEP of azimuth, see NoxcastPanel._pano
function panoKind(arr, az, el, reach, below = true) {
  az = ((az % 360) + 360) % 360;
  if (el < 0) return "ground";
  if (behind(arr, az, el)) return "plane";
  const r = reach[Math.floor(az / PANO_STEP)];
  if (!r || el > r[1]) return "never";
  if (el < r[0]) {
    // the sun never gets this low here: an obstacle just above goes on down, houses stand on the ground
    if (!below) return "never";
    const k = panoKind(arr, az, r[0] + 0.3, reach, false);
    return k === "obstacle" || k === "facade" ? "inferred" : "never";
  }
  const v = panoValue(arr, az, el);
  if (!v) return "none";
  if (v.t < PANO_SHADE) return v.facade ? "facade" : "obstacle";
  return v.t < 0.9 ? "partial" : "free";
}

// the panorama as a camera on the module sees it: held level, so verticals stay vertical, and moved up and down
// like a shift lens (shift: tan of the elevation in the middle of the picture); f: focal length in pixels
function panoProject(v, az, el, W, H, f) {
  const d = ((az - v.yaw + 540) % 360) - 180;
  if (Math.abs(d) >= 89) return null;
  return [W / 2 + f * Math.tan(d * RAD), H / 2 + v.shift * f - f * Math.tan(el * RAD) / Math.cos(d * RAD)];
}

function panoUnproject(v, x, y, W, H, f) {
  const d = Math.atan((x - W / 2) / f) / RAD;
  return { az: (v.yaw + d + 360) % 360, el: Math.atan((H / 2 + v.shift * f - y) * Math.cos(d * RAD) / f) / RAD };
}

const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

// ---------------------------------------------------------------- time helpers

function tzOffsetMs(ts, tz) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ts)).map((p) => [p.type, p.value]));
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
  return asUtc - ts;
}

function localMidnight(year, doy, tz) {
  const guess = Date.UTC(year, 0, doy);
  return guess - tzOffsetMs(guess, tz);
}

function dayOfYear(d) {
  return Math.floor((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 86400000);
}

// ---------------------------------------------------------------- colours

const lerp = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgb = (c) => `rgb(${c.join(",")})`;
function scale(stops, t) {
  t = Math.max(0, Math.min(1, t));
  const n = stops.length - 1, i = Math.min(Math.floor(t * n), n - 1);
  return rgb(lerp(stops[i], stops[i + 1], t * n - i));
}

class NoxcastPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    adoptTheme(this.shadowRoot);
    this._state = { section: "overview", view: "overview", arrayId: null, mode: "map", layer: "shade", doy: null, sel: null, playing: false };
    this._sections = {};
    this._data = null;
    this._cache = {};
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("input", (ev) => this._onInput(ev));
    // the overview links each figure to the section that explains it
    this.shadowRoot.addEventListener("noxcast-section", (ev) => {
      ev.stopPropagation();
      this._state.section = ev.detail.section;
      this._state.sel = null;
      this._render();
      window.scrollTo({ top: 0 });
    });
    for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) this.shadowRoot.addEventListener(type, (ev) => this._onPanoPointer(ev));
    this.shadowRoot.addEventListener("pointerleave", (ev) => this._onPanoPointer(ev), true);
    this.shadowRoot.addEventListener("wheel", (ev) => this._onPanoWheel(ev), { passive: false });
    // the theme import above delays the definition: Home Assistant may already have created the
    // element and set these as plain properties, which would hide the setters; pass them on
    for (const p of ["narrow", "panel", "route", "hass"]) {
      if (!Object.prototype.hasOwnProperty.call(this, p)) continue;
      const v = this[p];
      delete this[p];
      this[p] = v;
    }
  }

  set hass(hass) {
    const first = !this._hass;
    if (hass.connection !== (this._hass && this._hass.connection)) this._listenReady(hass.connection);
    const dark = this._hass && this._hass.themes && this._hass.themes.darkMode;
    this._hass = hass;
    this.toggleAttribute("dark", !!(hass.themes && hass.themes.darkMode));
    for (const el of Object.values(this._sections)) { el.toggleAttribute("dark", this.hasAttribute("dark")); el.hass = hass; }
    if (first) { this._render(); this._load(); }
    else if ((hass.themes && hass.themes.darkMode) !== dark) this._render();
  }

  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }

  connectedCallback() {
    if (!this._hass) return;
    this._render();
    if (!this._data) this._load();
  }
  disconnectedCallback() { this._stop(); clearTimeout(this._retry); clearTimeout(this._statusTimer); }

  // a reconnect (Home Assistant restarted, the app came back from the background) loads again
  _listenReady(conn) {
    if (this._conn) this._conn.removeEventListener("ready", this._onReady);
    this._onReady = this._onReady || (() => { if (!this._data || this._error) this._load(); });
    this._conn = conn;
    if (conn) conn.addEventListener("ready", this._onReady);
  }

  get _t() { return TEXT[(this._hass && this._hass.language || "en").slice(0, 2)] || TEXT.en; }
  get _pal() { return this._hass && this._hass.themes && this._hass.themes.darkMode ? PALETTE.dark : PALETTE.light; }
  get _lang() { return (this._hass && this._hass.language) || "en"; }

  async _load() {
    if (this._loading) return;
    this._loading = true;
    clearTimeout(this._retry);
    try {
      // a request lost while the connection is rebuilt never answers: give up and ask again
      const data = await ask(this._hass, { type: "noxcast/learned" });
      this._tries = 0;
      if (data.v !== WS_SCHEMA) { this._data = null; this._error = this._t.outdated; } else {
        this._data = data;
        this._cache = {};
        this._error = null;
        if (this._state.doy === null) this._state.doy = dayOfYear(new Date());
      }
    } catch (err) {
      this._tries = (this._tries || 0) + 1;
      if (this._tries > 2) this._error = errorText(err, this._hass.language);   // shown only once retries failed
      if (this.isConnected) this._retry = setTimeout(() => this._load(), Math.min(60000, 5000 * this._tries));
    } finally {
      this._loading = false;
    }
    this._render();
    this._loadStatus();
  }

  // learning runs and how far each array has come; polled while a run is going, then the learned data reloads
  async _loadStatus() {
    clearTimeout(this._statusTimer);
    let st;
    const retry = () => {                      // Home Assistant may restart during a run: keep asking until it answers
      if (this._status && this._status.running && this.isConnected) this._statusTimer = setTimeout(() => this._loadStatus(), 10000);
    };
    try { st = await ask(this._hass, { type: "noxcast/learning" }); } catch (err) { retry(); return; }
    if (!st || st.v !== WS_SCHEMA) { retry(); return; }
    const finished = this._status && this._status.running && !st.running;
    this._status = st;
    if (st.running && this.isConnected) this._statusTimer = setTimeout(() => this._loadStatus(), 10000);
    if (finished) this._load(); else this._updateStatus();
  }

  // only what changed in the status box: rebuilding the whole panel every few seconds would scroll it back and
  // redraw the maps, and rebuilding the box would make it flicker while the run only moves on
  _updateStatus() {
    const slot = this.shadowRoot && this.shadowRoot.getElementById("nx-status");
    if (!slot) return;
    const box = slot.querySelector(".status"), parts = this._statusParts();
    if (!box || !parts) { slot.innerHTML = this._renderStatus(); return; }
    // compared with what was written, not with innerHTML: the browser serialises escaped text differently
    const set = (sel, html) => { const el = box.querySelector(sel); if (el && el._nx !== html) { el.innerHTML = html; el._nx = html; } };
    set(".status-lines", parts.lines);
    set(".stages", parts.rows);
    const button = box.querySelector(".btn");
    if (button && parts.button) {
      if (button.disabled !== parts.button.disabled) button.disabled = parts.button.disabled;
      if (button.textContent !== parts.button.text) button.textContent = parts.button.text;
    }
  }

  async _relearn() {
    this._statusError = null;                  // an error of an earlier attempt no longer holds
    try {
      await ask(this._hass, { type: "noxcast/relearn" });
      if (this._status) this._status.running = true;
    } catch (err) {
      this._statusError = String((err && err.message) || err);
    }
    this._updateStatus();
    clearTimeout(this._statusTimer);
    this._statusTimer = setTimeout(() => this._loadStatus(), 3000);
  }

  _renderStatus() {
    const parts = this._statusParts();
    if (!parts) return "";
    const b = parts.button;
    const btn = b ? `<button class="btn" data-action="relearn" ${b.disabled ? "disabled" : ""}>${b.text}</button>` : "";
    return `<section class="card pad status"><h2>${this._t.status.title}</h2>
      <p class="muted status-lines">${parts.lines}</p><ul class="stages">${parts.rows}</ul>${btn}</section>`;
  }

  // the parts of the status box that change while a run goes on
  _statusParts() {
    const st = this._status;
    if (!st) return null;
    const t = this._t.status, lang = this._lang, tz = this._data.time_zone;
    const when = (iso) => new Date(iso).toLocaleString(lang, { timeZone: tz, weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
    const took = (sec) => sec == null ? "–" : sec < 90 ? `${Math.round(sec)} s` : `${Math.round(sec / 60)} min`;
    const pct = (v) => `${Math.round(v * 100)} %`;
    const fill = (str, o) => str.replace(/\{(\w)\}/g, (_, k) => o[k]);
    const lines = [st.running ? t.running : st.last ? fill(t.last, { t: when(st.last), d: took(st.seconds) }) : t.never];
    if (st.error && !st.running) lines.push(`<span class="bad">${esc(fill(t.failed, { e: st.error }))}</span>`);
    if (this._statusError) lines.push(`<span class="bad">${esc(this._statusError)}</span>`);
    lines.push(fill(t.next, { t: when(st.next) }));
    const th = st.thresholds || {};
    const rows = st.arrays.map((a) => `<li><b>${esc(a.name)}</b>: ${esc(fill(t.stages[a.stage] || a.stage,
      { h: a.hours.toLocaleString(lang), d: a.days, c: pct(a.coverage), D: th.days, H: th.hours }))}</li>`).join("");
    const admin = this._hass.user && this._hass.user.is_admin;
    return { lines: lines.join("<br>"), rows, button: admin ? { disabled: !!st.running, text: st.running ? t.busy : t.relearn } : null };
  }

  // ------------------------------------------------------------ derived data per array

  _array(id) { return this._data.arrays.find((a) => a.id === id); }

  _derived(arr) {
    if (this._cache[arr.id]) return this._cache[arr.id];
    const { latitude: lat, longitude: lon } = this._data;
    const year = new Date().getFullYear();
    const domain = new Set();
    let maxEl = 0, minAz = 360, maxAz = 0;
    for (let d = 0; d < 366; d += 2) {
      const day = Date.UTC(year, 0, 1) / 1000 + d * 86400;
      for (let m = 0; m < 1440; m += 10) {
        const p = sunPosition(lat, lon, day + m * 60);
        if (p.el <= 0) continue;
        domain.add(`${Math.floor(p.az / AZ_STEP)},${Math.floor(p.el / EL_STEP)}`);
        maxEl = Math.max(maxEl, p.el); minAz = Math.min(minAz, p.az); maxAz = Math.max(maxAz, p.az);
      }
    }
    const az0 = Math.max(0, Math.floor(minAz / 30) * 30), az1 = Math.min(360, Math.ceil(maxAz / 30) * 30);
    const elMax = Math.ceil(maxEl / 10) * 10;
    const out = { domain, az0, az1, elMax };
    this._cache[arr.id] = out;
    return out;
  }

  // classification of one sun position for the current layer
  _cell(arr, ai, ei) {
    const az = ai * AZ_STEP + AZ_STEP / 2, el = ei * EL_STEP + EL_STEP / 2;
    if (behind(arr, az, el)) return { kind: "behind" };
    const la = arr.learned || {};
    const shade = displayValue(cellsOf(arr), ai, ei);
    if (shade === null) return { kind: "unlearned" };
    return {
      kind: "value", shade, facade: !(`${ai},${ei}` in (la.cells || {})) && `${ai},${ei}` in (arr.facade_cells || {}),
      limit: (la.limit_cells || []).includes(`${ai},${ei}`),
      refl: coarse(la.reflection, az, el, 0),
      dir: coarse(la.direction, az, el, 1),
    };
  }

  _pos(arr, az, el) {
    const ai = Math.floor(((az % 360) + 360) % 360 / AZ_STEP), ei = Math.floor(el / EL_STEP);
    if (el <= 0) return { kind: "night" };
    if (behind(arr, az, el)) return { kind: "behind" };
    const la = arr.learned || {};
    const shade = displayValue(cellsOf(arr), ai, ei);
    if (shade === null) return { kind: "unlearned" };
    return { kind: "value", shade, refl: coarse(la.reflection, az, el, 0), dir: coarse(la.direction, az, el, 1) };
  }

  _fill(c, layer) {
    const P = this._pal;
    if (c.kind === "behind") return P.behind;
    if (c.kind === "unlearned") return "url(#nx-hatch)";
    if (c.kind === "night") return "transparent";
    if (layer === "refl") return scale(P.refl, c.refl / 0.06);
    if (layer === "dir") return scale(P.dir, (c.dir - 0.9) / 0.2);
    const t = c.shade;
    return rgb(t < 0.5 ? lerp(P.shade, P.mid, t / 0.5) : lerp(P.mid, P.free, (t - 0.5) / 0.5));
  }

  _path(arr, dayStart, geo) {
    const { latitude: lat, longitude: lon } = this._data;
    const pts = [];
    for (let m = 0; m < 1440; m += 6) {
      const p = sunPosition(lat, lon, dayStart / 1000 + m * 60);
      if (p.el > 0 && p.az >= geo.az0 && p.az <= geo.az1) pts.push(`${geo.x(p.az).toFixed(1)},${geo.y(p.el).toFixed(1)}`);
    }
    return pts.join(" ");
  }

  // ------------------------------------------------------------ rendering

  _section(key) {
    const def = SECTIONS.find(([k]) => k === key);
    if (!def) return null;
    const [, tag, module] = def;
    if (!customElements.get(tag)) {             // load the module, render again once the element exists
      import(new URL(module + new URL(import.meta.url).search, import.meta.url).href);
      customElements.whenDefined(tag).then(() => this._render());
      return null;
    }
    if (!this._sections[key]) {
      this._sections[key] = document.createElement(tag);
      adoptTheme(this._sections[key].shadowRoot);
      this._sections[key].toggleAttribute("dark", this.hasAttribute("dark"));
      this._sections[key].hass = this._hass;
    }
    return this._sections[key];
  }

  _render() {
    if (!this.shadowRoot) return;
    // the new content replaces the old at once: hold the height for a frame, so the page does not shrink and
    // scroll back to the top in between
    const height = this.offsetHeight;
    if (height) {
      this.style.minHeight = `${height}px`;
      requestAnimationFrame(() => { this.style.minHeight = ""; });
    }
    const t = this._t, s = this._state;
    const titles = SECTION_TITLES[(this._hass && this._hass.language || "en").slice(0, 2)] || SECTION_TITLES.en;
    const short = SECTION_SHORT[(this._hass && this._hass.language || "en").slice(0, 2)] || SECTION_SHORT.en;
    const lang = (this._hass && this._hass.language || "en").slice(0, 2);
    // load and battery only when set up in the integration (sections unknown while loading: all shown)
    const known = (this._data && this._data.sections) || {};
    // the overview first, as the start; then what was learned and the further sections
    const keys = ["overview", "learned", ...SECTIONS.slice(1).map(([k]) => k)].filter((k) => known[k] !== false);
    if (!keys.includes(s.section)) { s.section = keys[0]; s.sel = null; }
    const lead = (SECTION_LEADS[lang] || SECTION_LEADS.en)[s.section];
    const hero = `<header class="hero"><div class="hero-text"><h1>${titles[s.section]}</h1><p>${lead}</p></div>${ORBIT}</header>`;
    const tabs = keys.map((k) => `<button class="navtab ${s.section === k ? "on" : ""}" data-action="section" data-section="${k}" title="${titles[k]}" aria-label="${titles[k]}"><ha-icon icon="${SECTION_ICONS[k]}"></ha-icon><span class="long">${titles[k]}</span><span class="short">${short[k]}</span></button>`).join("");
    if (s.section !== "learned") {
      this._stop();
      this.shadowRoot.innerHTML = `${this._style()}
        <div class="toolbar"><ha-menu-button></ha-menu-button><div class="brand">${LOGO}<span>Noxcast</span></div><nav class="navtabs">${tabs}</nav></div>
        <div class="content">${hero}<div id="section"></div></div>`;
      const section = this._section(s.section);
      if (section) this.shadowRoot.getElementById("section").appendChild(section);
      this._afterRender();
      return;
    }
    let body;
    if (this._error) body = `<div class="card pad">${esc(this._error)}</div>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!this._data.arrays.length) body = `<div class="card pad">${t.noData}</div>`;
    else if (this._state.view === "array" && this._array(this._state.arrayId)) body = this._renderArray(this._array(this._state.arrayId));
    else body = this._renderOverview();
    // inside an array the way back sits above the content, not in the toolbar
    const crumbs = this._state.view === "array" && this._array(this._state.arrayId)
      ? `<nav class="crumbs"><button class="link" data-action="overview">‹ ${t.overview}</button><span class="muted"> / </span><span>${esc(this._array(this._state.arrayId).name)}</span></nav>`
      : "";
    this.shadowRoot.innerHTML = `${this._style()}
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="brand">${LOGO}<span>Noxcast</span></div>
        <nav class="navtabs">${tabs}</nav>
      </div>
      <div class="content">${crumbs}${this._state.view === "array" ? "" : hero}${body}</div>`;
    this._afterRender();
  }

  _afterRender() {
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) { menu.hass = this._hass; menu.narrow = this._narrow; }
    const on = this.shadowRoot.querySelector(".navtab.on"), bar = this.shadowRoot.querySelector(".navtabs");
    if (on && bar && bar.scrollWidth > bar.clientWidth) bar.scrollLeft = on.offsetLeft - (bar.clientWidth - on.offsetWidth) / 2;
    this._drawPano();
  }

  _figures(arr) {
    const t = this._t, la = arr.learned;
    if (!la) return [];
    const pct = (v, d = 1) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: d })} %`;
    const num = (v, d = 2) => v.toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d });
    const dir = Object.values(la.direction || {}), refl = Object.values(la.reflection || {});
    const date = (s) => s ? new Date(typeof s === "number" ? s * 1000 : s).toLocaleDateString(this._lang, { timeZone: this._data.time_zone }) : "–";
    return [
      [t.efficiency, la.efficiency_source === "typical" ? `${pct(la.efficiency)} (${t.typical})` : pct(la.efficiency)],
      // stored before the flag existed: undefined, shown as before until the next learning run
      [t.skyView, la.sky_view_learned === false ? t.notLearned : pct(la.sky_view ?? 1, 0)],
      [t.tempCoeff, `${num(la.temp_coeff * 100)} %/K`],
      [t.coverage, pct(la.coverage, 0)],
      [t.facade, la.facade ? `${num(la.facade.height, 1)}° · ${Math.round(la.facade.direction)}° · ${Math.round(la.facade.start)}–${Math.round(la.facade.end)}°` : "–"],
      [t.dirRange, dir.length ? `${num(Math.min(...dir))}–${num(Math.max(...dir))}` : "–"],
      [t.reflMax, refl.length ? `+${pct(Math.max(...refl))}` : "–"],
      [t.samples, (la.samples || 0).toLocaleString(this._lang)],
      [t.since, date(la.first_sample)],
      [t.learnedAt, date(arr.last_learning)],
    ];
  }

  _renderOverview() {
    const t = this._t;
    const cards = this._data.arrays.map((arr) => {
      const figs = this._figures(arr).slice(0, 6).map(([k, v]) => `<div class="fig"><span class="muted">${k}</span><span class="mono">${v}</span></div>`).join("");
      const maps = ["shade", "refl", "dir"].map((layer) => `
        <button class="mini" data-action="open" data-id="${arr.id}" data-layer="${layer}" aria-label="${esc(arr.name)}: ${t[layer]}">
          ${this._mapSvg(arr, layer, { mini: true })}
          <span class="muted small">${t[layer]}</span>
        </button>`).join("");
      const gc = arr.learned && arr.learned.geometry_check;
      const hints = (arr.learned && arr.learned.hints) || [];
      const badge = (hints.includes("peak") || hints.includes("high") ? `<span class="badge">${t.checks.badgeCheck}</span>` : "")
        + (gc && gc.deviates ? `<span class="badge">${t.checks.badge}</span>` : "");
      return `<section class="card row">
        <div class="figs"><h2>${esc(arr.name)}${badge}</h2>${figs}
          <button class="btn" data-action="open" data-id="${arr.id}" data-layer="shade">${t.open}</button></div>
        <div class="minis">${maps}</div>
      </section>`;
    }).join("");
    return `<div class="stack"><div id="nx-status" class="status-slot">${this._renderStatus()}</div>${cards}${this._legend("shade")}</div>`;
  }

  _renderArray(arr) {
    const t = this._t, s = this._state;
    const modeTabs = ["map", "grid", "pano"].map((m) => `<button role="tab" aria-selected="${s.mode === m}" class="tab ${s.mode === m ? "on" : ""}" data-action="mode" data-mode="${m}">${t[m]}</button>`).join("");
    const layerTabs = ["shade", "refl", "dir"].map((l) => `<button class="chip ${s.layer === l ? "on" : ""}" data-action="layer" data-layer="${l}">${t[l]}</button>`).join("");
    const main = s.mode === "map" ? `
        <div class="card mapcard">${this._mapSvg(arr, s.layer, {})}</div>
        ${this._slider(arr)}` : s.mode === "pano" ? this._panoHtml(arr)
      : `<div class="card mapcard">${this._gridSvg(arr, s.layer)}</div>`;
    const figs = this._figures(arr).map(([k, v]) => `<div class="fig"><span class="muted">${k}</span><span class="mono">${v}</span></div>`).join("");
    return `
      <div class="split">
        <div class="main">
          <div class="tabs" role="tablist">${modeTabs}</div>
          ${s.mode === "pano" ? "" : `<div class="chips">${layerTabs}${s.mode === "map" ? `<span class="muted small hint">${t.clickHint}</span>` : ""}</div>`}
          ${main}
          ${s.mode === "pano" ? this._panoLegend() : this._legend(s.layer, ((arr.learned || {}).limit_cells || []).length > 0)}
          ${this._checks(arr)}
        </div>
        <aside class="side">
          ${s.sel ? this._detail(arr) : ""}
          <section class="card pad"><h2>${esc(arr.name)}</h2>${figs}</section>
          <section class="card pad"><h3>${s.mode === "pano" ? t.pano : s.mode === "grid" ? t.grid : t[s.layer]}</h3>${[].concat(s.mode === "pano" ? t.help.pano : s.mode === "grid" ? t.help.grid : t.help[s.layer]).map((x) => `<p class="help">${x}</p>`).join("")}${(s.layer === "shade" || s.mode === "pano") && (arr.learned || {}).facade ? `<p class="help">${t.help.facade}</p>` : ""}${s.mode !== "pano" && s.layer === "shade" && ((arr.learned || {}).limit_cells || []).length ? `<p class="help">${t.help.limit}</p>` : ""}</section>
        </aside>
      </div>`;
  }

  _geo(arr, w, h) {
    const d = this._derived(arr);
    return {
      az0: d.az0, az1: d.az1, elMax: d.elMax, w, h,
      x: (az) => ((az - d.az0) / (d.az1 - d.az0)) * w,
      y: (el) => h - (el / d.elMax) * h,
    };
  }

  _mapSvg(arr, layer, { mini }) {
    const W = 840, H = 400, P = this._pal, t = this._t;
    const g = this._geo(arr, W, H), d = this._derived(arr);
    const cw = (AZ_STEP / (g.az1 - g.az0)) * W, ch = (EL_STEP / g.elMax) * H;
    let rects = "";
    for (const key of d.domain) {
      const [ai, ei] = key.split(",").map(Number);
      const az = ai * AZ_STEP;
      if (az < g.az0 || az >= g.az1) continue;
      const c = this._cell(arr, ai, ei);
      const sel = !mini && this._state.sel && this._state.sel[0] === ai && this._state.sel[1] === ei;
      rects += `<rect x="${(g.x(az) + 0.5).toFixed(1)}" y="${(g.y((ei + 1) * EL_STEP) + 0.5).toFixed(1)}" width="${(cw - 1).toFixed(1)}" height="${(ch - 1).toFixed(1)}" fill="${this._fill(c, layer)}" ${mini ? "" : `data-action="cell" data-ai="${ai}" data-ei="${ei}" class="cell${sel ? " sel" : ""}"`}></rect>`;
      // at the power limit: no shade learned there, the dot says why
      if (c.limit && layer === "shade") rects += `<circle cx="${(g.x(az + AZ_STEP / 2)).toFixed(1)}" cy="${(g.y((ei + 0.5) * EL_STEP)).toFixed(1)}" r="${mini ? 1 : 2}" class="lim"></circle>`;
    }
    const year = new Date().getFullYear(), tz = this._data.time_zone;
    const path = (doy) => this._path(arr, localMidnight(year, doy, tz), g);
    const paths = `
      <polyline points="${path(172)}" class="sp"></polyline>
      <polyline points="${path(80)}" class="sp" stroke-dasharray="6 4"></polyline>
      <polyline points="${path(355)}" class="sp" stroke-dasharray="2 4"></polyline>
      <polyline points="${path(this._state.doy)}" class="sp-day"${mini ? "" : ` id="nx-daypath"`}></polyline>`;
    let axes = "";
    if (!mini) {
      const names = { 90: "O", 135: "SO", 180: "S", 225: "SW", 270: "W", 45: "NO", 315: "NW" };
      if (this._lang.startsWith("en")) Object.assign(names, { 90: "E", 135: "SE", 45: "NE" });
      for (let a = Math.ceil(g.az0 / 45) * 45; a <= g.az1; a += 45) axes += `<text x="${g.x(a).toFixed(0)}" y="${H + 18}" text-anchor="middle" class="ax">${names[a] || ""} ${a}°</text>`;
      for (let e = 10; e <= g.elMax; e += 10) axes += `<text x="-6" y="${(g.y(e) + 4).toFixed(0)}" text-anchor="end" class="ax">${e}°</text><line x1="0" x2="${W}" y1="${g.y(e).toFixed(1)}" y2="${g.y(e).toFixed(1)}" class="grid"></line>`;
    }
    // sun position perpendicular to each module plane (highest irradiance on it)
    let normals = "";
    if (!mini) {
      for (const p of planes(arr)) {
        const el = 90 - p.tilt, az = p.azimuth >= g.az0 ? p.azimuth : p.azimuth + 360;
        if (el <= 0 || el > g.elMax || az < g.az0 || az > g.az1) continue;
        normals += `<circle cx="${g.x(az).toFixed(1)}" cy="${g.y(el).toFixed(1)}" r="6" class="normal"><title>${t.normal}: ${Math.round(p.azimuth)}° / ${Math.round(el)}°</title></circle>`;
      }
    }
    const hatch = `<defs><pattern id="nx-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="${P.unlearned}"></rect><line x1="0" y1="0" x2="0" y2="6" stroke="${P.hatch}" stroke-width="2"></line></pattern></defs>`;
    const vb = mini ? `0 0 ${W} ${H}` : `-40 -8 ${W + 48} ${H + 34}`;
    return `<svg viewBox="${vb}" class="${mini ? "minisvg" : "mapsvg"}" role="img" aria-label="${t[layer]}">${hatch}<rect width="${W}" height="${H}" fill="${P.mapBg}"></rect>${axes}${rects}${paths}${normals}</svg>`;
  }

  _gridSvg(arr, layer) {
    const { latitude: lat, longitude: lon, time_zone: tz } = this._data;
    const year = new Date().getFullYear(), t = this._t, P = this._pal;
    const mids = [15, 46, 75, 105, 135, 162, 198, 228, 258, 289, 319, 345];
    const rows = mids.map((doy) => {
      const start = localMidnight(year, doy, tz);
      return Array.from({ length: 24 }, (_, h) => this._pos(arr, ...Object.values(sunPosition(lat, lon, start / 1000 + (h + 0.5) * 3600))));
    });
    const used = [...Array(24).keys()].filter((h) => rows.some((r) => r[h].kind !== "night"));
    const h0 = used[0], h1 = used[used.length - 1], cols = h1 - h0 + 1;
    const W = 840, cw = W / cols, rh = 30;
    const today = new Date(), tm = today.getMonth();
    let out = "";
    rows.forEach((row, m) => {
      for (let h = h0; h <= h1; h++) {
        const c = row[h];
        if (c.kind === "night") continue;
        out += `<rect x="${((h - h0) * cw + 1).toFixed(1)}" y="${m * rh + 1}" width="${(cw - 2).toFixed(1)}" height="${rh - 2}" rx="3" fill="${this._fill(c, layer)}"${m === tm ? ` class="todayrow"` : ""}></rect>`;
      }
      out += `<text x="-8" y="${m * rh + 20}" text-anchor="end" class="ax">${t.months[m]}</text>`;
    });
    for (let h = h0; h <= h1; h++) out += `<text x="${((h - h0) * cw + cw / 2).toFixed(1)}" y="${12 * rh + 16}" text-anchor="middle" class="ax">${h}</text>`;
    const hatch = `<defs><pattern id="nx-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="${P.unlearned}"></rect><line x1="0" y1="0" x2="0" y2="6" stroke="${P.hatch}" stroke-width="2"></line></pattern></defs>`;
    return `<svg viewBox="-44 -4 ${W + 50} ${12 * rh + 26}" class="mapsvg" role="img" aria-label="${t.grid}">${hatch}${out}</svg>`;
  }

  _slider(arr) {
    const t = this._t, s = this._state;
    return `<section class="card pad slider">
      <div class="sl-row">
        <button class="round" data-action="play" aria-label="${s.playing ? t.pause : t.play}">${s.playing
          ? `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="3" y="2" width="3.5" height="12" fill="currentColor"></rect><rect x="9.5" y="2" width="3.5" height="12" fill="currentColor"></rect></svg>`
          : `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4 2 L13 8 L4 14 Z" fill="currentColor"></path></svg>`}</button>
        <label for="nx-day" class="sl-label" id="nx-daylabel">${this._dayLabel()}</label>
        <input id="nx-day" type="range" min="1" max="365" value="${s.doy}">
        <button class="btn" data-action="today">${t.today}</button>
      </div>
      <div id="nx-daybox">${this._dayStrip(arr)}</div>
    </section>`;
  }

  _dayStart() {
    return localMidnight(new Date().getFullYear(), this._state.doy, this._data.time_zone);
  }

  _dayLabel() {
    const tz = this._data.time_zone;
    return `${this._t.sunPathOn} ${new Date(this._dayStart() + 12 * 3600000).toLocaleDateString(this._lang, { day: "numeric", month: "long", timeZone: tz })}`;
  }

  _dayStrip(arr) {
    const t = this._t, s = this._state, tz = this._data.time_zone, start = this._dayStart();
    const { latitude: lat, longitude: lon } = this._data;
    let strip = "", first = null, last = null;
    for (let m = 0; m < 1440; m += 10) {
      const p = sunPosition(lat, lon, start / 1000 + (m + 5) * 60);
      if (p.el <= 0) continue;
      if (first === null) first = m;
      last = m + 10;
      strip += `<span style="background:${this._fill(this._pos(arr, p.az, p.el), s.layer).replace("url(#nx-hatch)", this._pal.unlearned)}"></span>`;
    }
    const hm = (m) => new Date(start + m * 60000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", timeZone: tz });
    return `<div class="muted small">${t.dayStrip}</div>
      <div class="strip">${strip}</div>
      <div class="strip-ax muted small mono"><span>${first !== null ? hm(first) : ""}</span><span>${last !== null ? hm(last) : ""}</span></div>`;
  }

  // only the day-dependent parts, so the slider keeps working while it is dragged
  _updateDay() {
    const arr = this._array(this._state.arrayId), root = this.shadowRoot;
    const line = root.getElementById("nx-daypath");
    if (!arr || !line) { this._stop(); return; }          // map not shown: nothing to move
    line.setAttribute("points", this._path(arr, this._dayStart(), this._geo(arr, 840, 400)));
    root.getElementById("nx-daylabel").textContent = this._dayLabel();
    root.getElementById("nx-daybox").innerHTML = this._dayStrip(arr);
    const input = root.getElementById("nx-day");
    if (input && Number(input.value) !== this._state.doy) input.value = this._state.doy;
  }

  _detail(arr) {
    const t = this._t, [ai, ei] = this._state.sel;
    const c = this._cell(arr, ai, ei);
    const az = ai * AZ_STEP, el = ei * EL_STEP;
    const num = (v, d = 2) => v.toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d });
    let rows;
    if (c.kind === "behind") rows = `<p class="help">${t.behind}</p>`;
    else if (c.kind === "unlearned") rows = `<p class="help">${t.notLearned}</p>`;
    else {
      const label = c.shade >= 0.9 ? t.free : c.shade >= 0.7 ? t.partial : t.shaded;
      rows = `<div class="tiles">
        <div class="tile"><span class="muted small">${t.shade}</span><b class="mono">${num(c.shade)}</b><span class="small">${label}${c.facade ? ` · ${t.fromFacade}` : ""}${c.limit ? ` · ${t.atLimit}` : ""}</span></div>
        <div class="tile"><span class="muted small">${t.refl}</span><b class="mono">${c.refl > 0.004 ? `+${num(c.refl * 100, 1)} %` : "–"}</b></div>
        <div class="tile"><span class="muted small">${t.dir}</span><b class="mono">${num(c.dir)}</b></div>
      </div>`;
    }
    return `<section class="card pad">
      <div class="dhead"><div><div class="muted small">${t.selected}</div><h2>${az}–${az + AZ_STEP}° · ${el}–${el + EL_STEP}°</h2></div>
        <button class="x" data-action="close" aria-label="${t.close}">✕</button></div>
      <div class="small"><span class="muted">${t.sunHere}:</span> ${this._whenThere(ai, ei)}</div>
      ${rows}
    </section>`;
  }

  _whenThere(ai, ei) {
    const { latitude: lat, longitude: lon, time_zone: tz } = this._data;
    const year = new Date().getFullYear(), t = this._t;
    const months = new Set(), mins = [];
    for (let d = 1; d <= 365; d += 2) {
      const start = localMidnight(year, d, tz) / 1000;
      for (let m = 0; m < 1440; m += 10) {
        const p = sunPosition(lat, lon, start + m * 60);
        if (p.el > 0 && Math.floor(p.az / AZ_STEP) === ai && Math.floor(p.el / EL_STEP) === ei) {
          months.add(new Date((start + 43200) * 1000).getUTCMonth());
          mins.push(m);
        }
      }
    }
    if (!mins.length) return t.never;
    const ms = [...months].sort((a, b) => a - b).map((m) => t.months[m]).join(", ");
    const hm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
    return `${ms} · ${hm(Math.min(...mins))}–${hm(Math.max(...mins) + 10)}`;
  }

  // ------------------------------------------------------------ checks: month balance, efficiency, geometry

  _checks(arr) {
    const la = arr.learned;
    if (!la) return "";
    return `${this._plausible(arr, la)}${this._balance(la)}<div class="checks">${this._effMonths(la)}${this._effDirections(la)}${this._geometry(arr, la)}</div>`;
  }

  _plausible(arr, la) {
    const c = this._t.checks, hints = la.hints || [];
    const pct = (v) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: 1 })} %`;
    const max = la.max_power_w;
    const sub = c.ofPeak.replace("{p}", Math.round(arr.peak_w).toLocaleString(this._lang))
      + (max == null ? "" : ` · ${pct(max / arr.peak_w)}`)
      + (la.max_power_t ? ` · ${new Date(la.max_power_t * 1000).toLocaleDateString(this._lang)}` : "");
    const tiles = `<div class="tiles2">
      <div class="tile ${hints.includes("peak") ? "warn" : ""}"><span class="muted small">${c.maxPower}</span><b>${max == null ? "–" : `${Math.round(max).toLocaleString(this._lang)} W`}</b><span class="muted small">${sub}</span></div>
      <div class="tile ${hints.includes("high") ? "warn" : ""}"><span class="muted small">${c.effLearnedTile}</span><b>${pct(la.efficiency)}</b></div></div>`;
    const notes = hints.length ? hints.map((h) => `<p class="note warn">${c.hint[h] || h}</p>`).join("") : `<p class="note">${c.plOk}</p>`;
    return `<section class="card pad"><h3>${c.plTitle}</h3>${tiles}${notes}<p class="help">${c.plHelp}</p></section>`;
  }

  _effDirections(la) {
    const c = this._t.checks, entries = Object.entries(la.efficiency_directions || {});
    if (!entries.length) return `<section class="card pad"><h3>${c.dirTitle}</h3><p class="muted">${c.dirNone}</p><p class="help">${c.dirHelp}</p></section>`;
    const W = 420, H = 150, pad = 36, vals = entries.map(([, d]) => d.efficiency).concat([la.efficiency, 1]);
    const hi = Math.max(...vals) * 1.08, bw = Math.min(40, (W - pad) / entries.length - 10);
    const x = (i) => pad + (i + 0.5) * (W - pad) / entries.length;
    const y = (v) => H - 20 - v / hi * (H - 34);
    const pct = (v) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: 1 })} %`;
    let svg = "";
    for (const v of [0.5, 1]) svg += `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="grid"></line><text x="${pad - 4}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="ax">${Math.round(v * 100)}</text>`;
    entries.forEach(([s, { efficiency: v, hours: n }], i) => {
      const fill = v > 1.05 ? "#E8543C" : "#FF6A3D";
      svg += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(y(0) - y(v)).toFixed(1)}" rx="4" fill="${fill}" opacity=".85"><title>${c.sectors[s] || s}: ${pct(v)} (${n} ${c.hours})</title></rect>`;
      svg += `<text x="${x(i).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${c.sectors[s] || s}</text>`;
    });
    svg += `<line x1="${pad}" x2="${W}" y1="${y(la.efficiency).toFixed(1)}" y2="${y(la.efficiency).toFixed(1)}" class="sp" stroke-dasharray="5 4"></line>`;
    return `<section class="card pad"><h3>${c.dirTitle}</h3><svg viewBox="0 0 ${W} ${H}" class="mapsvg">${svg}</svg>
      <div class="legend"><span class="lg"><i class="sw" style="border:0;border-top:2px dashed currentColor;border-radius:0;height:0"></i>${c.effLearned} ${pct(la.efficiency)}</span></div>
      <p class="help">${c.dirHelp}</p></section>`;
  }

  _balance(la) {
    const t = this._t, c = t.checks, months = la.months || [];
    if (!months.length) return "";
    const pct = (v) => { if (v == null) return ""; const r = Math.round(v * 100); return `${r > 0 ? "+" : ""}${r}`; };
    const tone = (v) => {
      if (v == null || Math.abs(v) < 0.005) return "transparent";
      const a = Math.min(Math.abs(v) / 0.4, 1) * 0.75 + 0.1;
      return v < 0 ? `rgba(232, 84, 60, ${a.toFixed(2)})` : `rgba(46, 170, 110, ${a.toFixed(2)})`;
    };
    const max = Math.max(...months.map((m) => m.clear_wh), 1);
    const head = months.map((m) => `<div class="mh">${t.months[m.month - 1]}</div>`).join("");
    const bars = months.map((m) => `<div class="mb" title="${(m.clear_wh / 1000).toLocaleString(this._lang, { maximumFractionDigits: 1 })} kWh">
      <i style="height:${(m.clear_wh / max * 100).toFixed(1)}%"></i><span>${(m.clear_wh / 1000).toLocaleString(this._lang, { maximumFractionDigits: 1 })}</span></div>`).join("");
    const row = (key, label) => `<div class="ml">${label}</div>${months.map((m) => `<div class="mc" style="background:${tone(m[key])}"
      title="${label} ${t.months[m.month - 1]}: ${pct(m[key]) || "–"} %${key === "temperature" && m.air_temp != null ? ` (${m.air_temp} °C)` : ""}">${pct(m[key])}</div>`).join("")}`;
    return `<section class="card pad"><h3>${c.balance}</h3>
      <div class="mgrid"><div></div>${head}<div class="ml col">${c.clearDay}<span class="small">kWh</span></div>${bars}
        ${row("shade", c.shade)}${row("reflection", c.refl)}${row("direction", c.dir)}${row("temperature", c.temp)}</div>
      <p class="help">${c.balanceHelp}</p></section>`;
  }

  _effMonths(la) {
    const c = this._t.checks, entries = Object.entries(la.efficiency_months || {});
    if (!entries.length) return `<section class="card pad"><h3>${c.effTitle}</h3><p class="muted">${c.noEff}</p></section>`;
    const W = 420, H = 150, pad = 36, vals = entries.map(([, m]) => m.efficiency).concat([la.efficiency]);
    const lo = Math.min(...vals) - 0.02, hi = Math.max(...vals) + 0.02;
    const x = (i) => pad + (entries.length === 1 ? (W - pad) / 2 : i / (entries.length - 1) * (W - pad - 10));
    const y = (v) => H - 20 - (v - lo) / (hi - lo) * (H - 34);
    const pct = (v) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: 1 })} %`;
    let svg = `<defs><linearGradient id="nx-eff" x1="0" x2="1"><stop offset="0" stop-color="#F5A524"></stop><stop offset=".5" stop-color="#FF6A3D"></stop><stop offset="1" stop-color="#8B5CF6"></stop></linearGradient></defs>`;
    for (const v of [lo + 0.02, (lo + hi) / 2, hi - 0.02]) svg += `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="grid"></line><text x="${pad - 4}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="ax">${Math.round(v * 100)}</text>`;
    svg += `<line x1="${pad}" x2="${W}" y1="${y(la.efficiency).toFixed(1)}" y2="${y(la.efficiency).toFixed(1)}" class="sp" stroke-dasharray="5 4"></line>`;
    svg += `<polyline points="${entries.map(([, m], i) => `${x(i).toFixed(1)},${y(m.efficiency).toFixed(1)}`).join(" ")}" fill="none" stroke="url(#nx-eff)" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"></polyline>`;
    entries.forEach(([m, { efficiency: v, hours: n }], i) => {
      const [yy, mm] = m.split("-").map(Number);
      svg += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4.5" fill="var(--card-background-color)" stroke="#FF6A3D" stroke-width="2.5"><title>${this._t.months[mm - 1]} ${yy}: ${pct(v)} (${n} ${c.hours})</title></circle>`;
      if (entries.length <= 14 || i % 2 === 0) svg += `<text x="${x(i).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${this._t.months[mm - 1]}</text>`;
    });
    return `<section class="card pad"><h3>${c.effTitle}</h3><svg viewBox="0 0 ${W} ${H}" class="mapsvg">${svg}</svg>
      <div class="legend"><span class="lg"><i class="sw" style="background:linear-gradient(90deg,#F5A524,#8B5CF6);border:0"></i>${c.effTitle}</span>
        <span class="lg"><i class="sw" style="border:0;border-top:2px dashed currentColor;border-radius:0;height:0"></i>${c.effLearned} ${pct(la.efficiency)}</span></div>
      <p class="help">${c.effHelp}</p></section>`;
  }

  _geometry(arr, la) {
    const c = this._t.checks, g = la.geometry_check;
    if (planes(arr).length > 1) {
      const list = planes(arr).map((p) => `<div class="tile"><span class="muted small">${Math.round(p.peak_w)} Wp</span><b>${Math.round(p.tilt)}° / ${Math.round(p.azimuth)}°</b></div>`).join("");
      return `<section class="card pad"><h3>${c.geoTitle}</h3><div class="tiles2">${list}</div><p class="note">${c.geoPlanes}</p></section>`;
    }
    if (!g) return `<section class="card pad"><h3>${c.geoTitle}</h3><p class="muted">${c.geoNone}</p><p class="help">${c.geoHelp}</p></section>`;
    const pct = (v) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: 1 })} %`;
    const deg = (tilt, az) => `${Math.round(tilt)}° / ${Math.round(az)}°`;
    // compass: configured direction thin, best fit in the brand gradient
    const arrow = (az, cls) => { const r = (az - 90) * Math.PI / 180; return `<line x1="50" y1="50" x2="${(50 + 38 * Math.cos(r)).toFixed(1)}" y2="${(50 + 38 * Math.sin(r)).toFixed(1)}" class="${cls}"></line>`; };
    const compass = `<svg viewBox="0 0 100 100" class="compass"><defs><linearGradient id="nx-geo" x1="0" x2="1"><stop offset="0" stop-color="#F5A524"></stop><stop offset="1" stop-color="#8B5CF6"></stop></linearGradient></defs>
      <circle cx="50" cy="50" r="44" class="ring"></circle>${["N", "O", "S", "W"].map((n, i) => `<text x="${50 + 34 * Math.sin(i * Math.PI / 2)}" y="${54 - 34 * Math.cos(i * Math.PI / 2)}" text-anchor="middle" class="ax">${this._lang.startsWith("en") && n === "O" ? "E" : n}</text>`).join("")}
      ${arrow(arr.azimuth, "cfg")}${arrow(g.azimuth, "fit")}<circle cx="50" cy="50" r="3" fill="currentColor"></circle></svg>`;
    return `<section class="card pad"><h3>${c.geoTitle}</h3>
      <div class="geo">${compass}<div class="tiles2">
        <div class="tile"><span class="muted small">${c.configured}</span><b>${deg(arr.tilt, arr.azimuth)}</b></div>
        <div class="tile ${g.deviates ? "warn" : "ok"}"><span class="muted small">${c.bestFit}</span><b>${deg(g.tilt, g.azimuth)}</b></div>
        <div class="tile"><span class="muted small">${c.spread}</span><b>${pct(g.spread)}</b><span class="muted small">${pct(g.spread_configured)}</span></div>
        <div class="tile"><span class="muted small">${c.trust}</span><b>${c.confidence[g.confidence] || g.confidence}</b><span class="muted small">${g.hours} ${c.hours}</span></div>
      </div></div>
      <p class="${g.deviates ? "note warn" : "note"}">${g.deviates ? c.geoHint : c.geoOk}</p><p class="help">${c.geoHelp}</p></section>`;
  }

  // ------------------------------------------------------------ panorama

  // lowest and highest sun of the year per azimuth step: at one azimuth the sun stands higher the longer the
  // day, so December and June bound it (where the December sun never gets, the horizon does)
  _pano(arr) {
    const d = this._derived(arr);
    if (d.pano) return d.pano;
    const { latitude: lat, longitude: lon } = this._data;
    const year = new Date().getFullYear(), n = 360 / PANO_STEP, reach = new Array(n).fill(null);
    for (const [doy, side] of [[355, 0], [172, 1]]) {
      const start = localMidnight(year, doy, this._data.time_zone) / 1000;
      for (let m = 0; m < 1440; m += 0.25) {                 // 15 s: around noon the June sun moves faster than a step
        const p = sunPosition(lat, lon, start + m * 60);
        if (p.el <= 0) continue;
        const r = reach[Math.floor(p.az / PANO_STEP) % n] || (reach[Math.floor(p.az / PANO_STEP) % n] = [null, 0]);
        if (side) r[1] = Math.max(r[1], p.el); else r[0] = r[0] === null ? p.el : Math.min(r[0], p.el);
      }
    }
    for (const r of reach) if (r && (r[0] === null || r[0] > r[1])) r[0] = 0;   // azimuths the December sun never reaches
    d.pano = { reach };
    return d.pano;
  }

  // the view of the open panorama: where it looks, how far it is moved up, the angle of view
  _panoView(arr, W, H) {
    if (!this._pv || this._pv.id !== arr.id) {
      const fov = PANO_FOV[2], f = W / 2 / Math.tan(fov / 2 * RAD);
      this._pv = { id: arr.id, yaw: planes(arr)[0].azimuth, shift: 0.38 * H / f, fov };   // the horizon near the bottom
    }
    return this._pv;
  }

  _panoHtml(arr) {
    const t = this._t;
    return `<div class="card mapcard">
      <div class="pano-stage" id="nx-pano" data-id="${arr.id}" role="img" aria-label="${t.pano}"><canvas></canvas><svg class="pano-ov" aria-hidden="true"></svg></div>
      <div class="pano-bar"><span class="pano-read muted small" id="nx-panoread">${t.panoIdle}</span><button class="chip" data-action="panohome">${t.panoHome}</button></div></div>`;
  }

  // scale below 1 while the view moves: fewer pixels, the full picture follows once it rests
  _drawPano(scale = 1) {
    const stage = this.shadowRoot && this.shadowRoot.getElementById("nx-pano");
    const arr = stage && this._array(stage.dataset.id);
    if (!arr) return;
    const W = stage.clientWidth, H = Math.round(Math.min(W * PANO_ASPECT, window.innerHeight * 0.7));
    if (!W) return;
    const v = this._panoView(arr, W, H), f = W / 2 / Math.tan(v.fov / 2 * RAD), reach = this._pano(arr).reach;
    const C = this._pal.pano, col = Object.fromEntries(["sky", "partial", "obstacle", "facadeBg", "plane", "dot", "ground"].map((k) => [k, hexRgb(C[k])]));
    col.inferred = col.obstacle.map((x, i) => Math.round(x * 0.45 + col.sky[i] * 0.55));
    col.none = col.obstacle.map((x, i) => Math.round(x * 0.3 + col.sky[i] * 0.7));
    const canvas = stage.querySelector("canvas"), w = Math.round(W * scale), h = Math.round(H * scale);
    canvas.width = w; canvas.height = h; canvas.style.height = `${H}px`; stage.style.height = `${H}px`;
    const ctx = canvas.getContext("2d"), img = ctx.createImageData(w, h), px = img.data;
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      const x = (i + 0.5) / scale, y = (j + 0.5) / scale, { az, el } = panoUnproject(v, x, y, W, H, f);
      const k = panoKind(arr, az, el, reach);
      const c = k === "plane" ? (Math.round(x) % 5 === 0 && Math.round(y) % 5 === 0 ? col.dot : col.plane)
        : k === "facade" ? ((x + y) % 7 < 2.5 ? col.obstacle : col.facadeBg)
          : k === "none" ? ((x - y + 7000) % 7 < 1.5 ? col.none : col.sky)
            : col[k] || col.sky;
      const o = (j * w + i) * 4;
      px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    stage.querySelector("svg").outerHTML = this._panoOverlay(arr, v, W, H, f);
    if (!this._panoSize) {                                    // a new width (phone turned, sidebar opened): draw again
      this._panoSize = new ResizeObserver((es) => {
        const w = Math.round(es[0].contentRect.width);
        if (w && w !== this._panoSize.w) { this._panoSize.w = w; this._drawPano(); }
      });
    }
    if (this._panoSize.el !== stage) { this._panoSize.disconnect(); this._panoSize.observe(stage); this._panoSize.el = stage; this._panoSize.w = W; }
  }

  // grid, directions, sun paths and where the module faces, over the picture
  _panoOverlay(arr, v, W, H, f) {
    const P = this._pal.pano, parts = [];
    const line = (pts, cls, attrs = "") => {
      let d = "", pen = false;
      for (const p of pts) { if (!p) { pen = false; continue; } d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; pen = true; }
      if (d) parts.push(`<path d="${d}" class="${cls}" ${attrs}></path>`);
    };
    const at = (az, el) => panoProject(v, az, el, W, H, f);
    for (let a = 0; a < 360; a += 15) line(Array.from({ length: 18 }, (_, i) => at(a, i * 5)), "pgrid");
    for (let e = 10; e <= 80; e += 10) {
      line(Array.from({ length: 177 }, (_, i) => at(v.yaw - 88 + i, e)), "pgrid");
      const p = at(v.yaw - v.fov / 2 + 3, e);
      if (p && p[1] > 14 && p[1] < H - 6) parts.push(`<text x="6" y="${(p[1] - 3).toFixed(0)}" class="ax">${e}°</text>`);
    }
    const names = this._lang.startsWith("en") ? ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] : ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
    const step = [15, 30, 45, 90].find((x) => f * Math.tan(x * RAD) >= 58) || 90;      // labels apart on a phone
    for (let a = 0; a < 360; a += step) {
      const p = at(a, 0);
      if (!p || p[0] < 16 || p[0] > W - 16 || p[1] > H - 4) continue;
      parts.push(`<text x="${p[0].toFixed(0)}" y="${Math.min(H - 6, p[1] + 16).toFixed(0)}" text-anchor="middle" class="ax">${a % 45 ? "" : names[a / 45] + " "}${a}°</text>`);
    }
    const year = new Date().getFullYear(), tz = this._data.time_zone, { latitude: lat, longitude: lon } = this._data;
    for (const [doy, dash] of [[172, ""], [80, "8 6"], [355, "2 5"]]) {
      const start = localMidnight(year, doy, tz) / 1000;
      line(Array.from({ length: 361 }, (_, i) => { const p = sunPosition(lat, lon, start + i * 240); return p.el > 0 ? at(p.az, p.el) : null; }),
        "psun", dash ? `stroke-dasharray="${dash}"` : "");
    }
    for (const q of planes(arr)) {
      const p = at(q.azimuth, 0);
      if (p && p[1] < H) parts.push(`<path d="M${p[0].toFixed(1)},${(p[1] - 1).toFixed(1)}l-7,-11h14z" fill="${P.sun}"></path><text x="${p[0].toFixed(1)}" y="${(p[1] - 16).toFixed(1)}" text-anchor="middle" class="ax pface">${this._t.panoFace} ${Math.round(q.azimuth)}°</text>`);
    }
    return `<svg class="pano-ov" aria-hidden="true" viewBox="0 0 ${W} ${H}">${parts.join("")}</svg>`;
  }

  _panoLegend() {
    const t = this._t, C = this._pal.pano, k = t.panoKinds;
    const sw = (style) => `<span class="sw" style="${style}"></span>`;
    const mix = (a, b, x) => rgb(hexRgb(a).map((v, i) => Math.round(v * x + hexRgb(b)[i] * (1 - x))));
    const line = (dash) => `<svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" class="psun"${dash ? ` stroke-dasharray="${dash}"` : ""}></line></svg>`;
    return `<div class="legend">
      <span class="lg">${sw(`background:${C.sky}`)}${k.free}</span>
      <span class="lg">${sw(`background:${C.obstacle}`)}${k.obstacle}</span>
      <span class="lg">${sw(`background-color:${C.facadeBg};background-image:repeating-linear-gradient(45deg, ${C.obstacle} 0 2px, transparent 2px 6px)`)}${k.facade}</span>
      <span class="lg">${sw(`background:${mix(C.obstacle, C.sky, 0.45)}`)}${k.inferred}</span>
      <span class="lg">${sw(`background:${C.partial}`)}${k.partial}</span>
      <span class="lg">${sw(`background-color:${C.plane};background-image:radial-gradient(${C.dot} 0.8px, transparent 1px);background-size:5px 5px`)}${k.plane}</span>
      <span class="lg">${sw(`background-color:${C.sky};background-image:repeating-linear-gradient(-45deg, ${mix(C.obstacle, C.sky, 0.3)} 0 1.5px, transparent 1.5px 7px)`)}${k.none}</span>
      <span class="lg">${sw(`background:${C.ground}`)}${k.ground}</span>
      <span class="lg">${line("")}${t.jun}</span><span class="lg">${line("8 6")}${t.equ}</span><span class="lg">${line("2 5")}${t.dec}</span></div>`;
  }

  // dragging swivels the view and moves it up and down, two fingers zoom; a pointer at rest reads direction and height
  _onPanoPointer(ev) {
    const stage = ev.composedPath().find((n) => n.id === "nx-pano");
    const read = this.shadowRoot.getElementById("nx-panoread");
    const g = this._panoDrag;
    if (ev.type === "pointerup" || ev.type === "pointercancel" || (ev.type === "pointerleave" && !g)) {
      if (g) {
        g.pts.delete(ev.pointerId);
        if (!g.pts.size) { this._panoDrag = null; if (g.moved) this._drawPano(); }
        else if (this._pv) g.start = { yaw: this._pv.yaw, shift: this._pv.shift, fov: this._pv.fov, pts: new Map(g.pts) };  // one finger left: it drags on from here
      }
      if (read && !this._panoDrag && ev.type !== "pointerup") read.textContent = this._t.panoIdle;
      return;
    }
    if (!stage || !this._pv) return;
    const W = stage.clientWidth, H = stage.clientHeight, v = this._pv, f = W / 2 / Math.tan(v.fov / 2 * RAD);
    if (ev.type === "pointerdown") {
      try { stage.setPointerCapture(ev.pointerId); } catch (err) { /* the pointer is already gone */ }
      const d = this._panoDrag || (this._panoDrag = { pts: new Map(), moved: false });
      d.pts.set(ev.pointerId, [ev.clientX, ev.clientY]);
      d.start = { yaw: v.yaw, shift: v.shift, fov: v.fov, pts: new Map(d.pts) };
      this._panoRead(stage, ev);
      return;
    }
    if (g && g.pts.has(ev.pointerId)) {
      g.pts.set(ev.pointerId, [ev.clientX, ev.clientY]);
      const [a, b] = [...g.pts.keys()], s0 = g.start.pts;
      if (g.pts.size >= 2 && s0.has(a) && s0.has(b)) {
        const dist = (m) => Math.hypot(m.get(a)[0] - m.get(b)[0], m.get(a)[1] - m.get(b)[1]) || 1;
        v.fov = Math.max(PANO_FOV[0], Math.min(PANO_FOV[1], g.start.fov * dist(s0) / dist(g.pts)));
      } else if (s0.has(a)) {
        const f0 = W / 2 / Math.tan(g.start.fov / 2 * RAD);
        v.yaw = (g.start.yaw - (g.pts.get(a)[0] - s0.get(a)[0]) / f0 / RAD + 360) % 360;
        v.shift = Math.max(-0.3, Math.min(3, g.start.shift + (g.pts.get(a)[1] - s0.get(a)[1]) / f0));
      }
      g.moved = true;
      this._panoLater();
      return;
    }
    this._panoRead(stage, ev);
  }

  _panoRead(stage, ev) {
    const read = this.shadowRoot.getElementById("nx-panoread"), arr = this._array(stage.dataset.id), v = this._pv;
    if (!read || !arr) return;
    const W = stage.clientWidth, H = stage.clientHeight, f = W / 2 / Math.tan(v.fov / 2 * RAD);
    const r = stage.getBoundingClientRect(), p = panoUnproject(v, ev.clientX - r.left, ev.clientY - r.top, W, H, f);
    const names = this._lang.startsWith("en") ? ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] : ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
    const a = Math.round(p.az) % 360;
    read.textContent = this._t.panoAt.replace("{a}", a).replace("{d}", names[Math.round(a / 45) % 8]).replace("{e}", p.el.toFixed(0))
      .replace("{k}", this._t.panoKinds[panoKind(arr, p.az, p.el, this._pano(arr).reach)]);
  }

  _onPanoWheel(ev) {
    if (!ev.composedPath().some((n) => n.id === "nx-pano") || !this._pv) return;
    ev.preventDefault();
    this._pv.fov = Math.max(PANO_FOV[0], Math.min(PANO_FOV[1], this._pv.fov + Math.sign(ev.deltaY) * 5));
    this._panoLater();
  }

  // a coarse picture at once, the full one once the view rests
  _panoLater() {
    this._drawPano(0.4);
    clearTimeout(this._panoFull);
    this._panoFull = setTimeout(() => this._drawPano(), 160);
  }

  _legend(layer, limit = false) {
    const t = this._t, P = this._pal;
    const sw = (c) => `<span class="sw" style="background:${c}"></span>`;
    const items = layer === "refl" ? [[rgb(P.refl[0]), t.none], [rgb(P.refl[1]), "+3 %"], [rgb(P.refl[2]), "+6 %"]]
      : layer === "dir" ? [[rgb(P.dir[0]), `0,90 ${t.less}`], [rgb(P.dir[1]), "1,00"], [rgb(P.dir[2]), `1,10 ${t.more}`]]
        : [[rgb(P.free), t.free], [rgb(P.mid), t.partial], [rgb(P.shade), t.shaded]];
    const hatch = `<span class="sw hatch"></span>`;
    const lines = `
      <span class="lg"><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" class="sp-day"></line></svg>${this._state.view === "array" ? t.sunPathOn.split(" ")[0] : t.today}</span>
      <span class="lg"><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" class="sp"></line></svg>${t.jun}</span>
      <span class="lg"><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" class="sp" stroke-dasharray="6 4"></line></svg>${t.equ}</span>
      <span class="lg"><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" class="sp" stroke-dasharray="2 4"></line></svg>${t.dec}</span>`;
    return `<div class="legend">${items.map(([c, l]) => `<span class="lg">${sw(c)}${l}</span>`).join("")}
      ${limit && layer === "shade" ? `<span class="lg"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="2.5" class="lim"></circle></svg>${t.atLimit}</span>` : ""}
      <span class="lg">${hatch}${t.notLearned}</span><span class="lg">${sw(P.behind)}${t.behind}</span>${this._state.mode === "grid" && this._state.view === "array" ? "" : lines}</div>`;
  }

  // ------------------------------------------------------------ interaction

  _onClick(ev) {
    const el = ev.composedPath().find((n) => n.dataset && n.dataset.action);
    if (!el) return;
    const s = this._state, a = el.dataset.action;
    if (a === "section") { s.section = el.dataset.section; s.sel = null; }
    else if (a === "overview") { s.view = "overview"; s.sel = null; this._stop(); }
    else if (a === "open") { s.view = "array"; s.arrayId = el.dataset.id; s.layer = el.dataset.layer || "shade"; s.mode = "map"; s.sel = null; }
    else if (a === "mode") { s.mode = el.dataset.mode; if (s.mode !== "map") this._stop(); }
    else if (a === "panohome") { this._pv = null; this._drawPano(); return; }
    else if (a === "layer") s.layer = el.dataset.layer;
    else if (a === "cell") s.sel = [Number(el.dataset.ai), Number(el.dataset.ei)];
    else if (a === "close") s.sel = null;
    else if (a === "today") { s.doy = dayOfYear(new Date()); this._stop(); }
    else if (a === "play") { if (s.playing) this._stop(); else this._play(); }
    else if (a === "relearn") { this._relearn(); return; }
    this._render();
  }

  _onInput(ev) {
    if (ev.target.id !== "nx-day") return;
    this._state.doy = Number(ev.target.value);
    this._updateDay();
  }

  _play() {
    this._state.playing = true;
    this._timer = setInterval(() => {
      this._state.doy = ((this._state.doy + 2) % 365) + 1;
      this._updateDay();
    }, 120);
  }

  _stop() {
    this._state.playing = false;
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }

  _style() {
    const P = this._pal;
    return `<style>
      :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, sans-serif)); }
      button { font: inherit; color: inherit; }
      .toolbar { display: flex; align-items: center; gap: 16px; height: var(--header-height, 56px); padding: 0 12px;
        background: var(--app-header-background-color, var(--primary-background-color)); color: var(--app-header-text-color, var(--primary-text-color));
        border-bottom: var(--app-header-border-bottom, 1px solid var(--divider-color)); box-sizing: border-box; }
      .brand { font-size: 20px; font-weight: 500; }
      .navtabs { display: flex; gap: 4px; overflow-x: auto; }
      .navtab { background: none; border: none; border-bottom: 2px solid transparent; padding: 8px 10px; cursor: pointer;
        font-size: 14px; color: var(--secondary-text-color); white-space: nowrap; }
      .navtab.on { color: var(--primary-text-color); border-bottom-color: var(--primary-color); }
      .crumbs { display: flex; gap: 4px; align-items: center; font-size: 14px; margin: 0 0 12px; }
      .navtabs { min-width: 0; }
      .navtab { display: inline-flex; align-items: center; gap: 6px; flex: none; }
      .navtab ha-icon { --mdc-icon-size: 18px; }
      .navtab .short { display: none; }
      @media (max-width: 720px) {
        /* the tabs get a row of their own below the menu, all visible at once: icon over a short title */
        .toolbar { flex-wrap: wrap; height: auto; min-height: var(--header-height, 56px); gap: 0 8px; padding: 0 8px; }
        .navtabs { order: 3; flex: 1 0 100%; gap: 0; overflow-x: visible; }
        .navtabs .navtab { flex: 1 1 0; min-width: 0; flex-direction: column; gap: 2px; padding: 5px 2px 4px !important;
          border-radius: 16px; font-size: 11px; }
        .navtab ha-icon { --mdc-icon-size: 22px; }
        .navtab .long { display: none; }
        .navtab .short { display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
      }
      .content { padding: 16px; max-width: 1400px; margin: 0 auto; box-sizing: border-box; }
      .muted { color: var(--secondary-text-color); }
      .small { font-size: 12px; }
      .mono { font-family: var(--code-font-family, ui-monospace, monospace); }
      .card { background: var(--card-background-color, var(--ha-card-background)); border-radius: var(--ha-card-border-radius, 12px);
        border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color)); box-shadow: var(--ha-card-box-shadow, none); }
      .pad { padding: 16px; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .status-slot { display: contents; }
      .row { display: flex; flex-wrap: wrap; gap: 16px; padding: 16px; }
      .figs { flex: 0 1 220px; min-width: 200px; display: flex; flex-direction: column; }
      h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; }
      h3 { margin: 0 0 6px; font-size: 14px; font-weight: 500; }
      .fig { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-top: 1px solid var(--divider-color); font-size: 13px; }
      .minis { flex: 999 1 560px; min-width: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
      .mini { display: flex; flex-direction: column; gap: 4px; padding: 0; border: 0; background: none; cursor: pointer; text-align: left; }
      .mini:focus-visible, .cell:focus-visible { outline: 2px solid var(--primary-color); }
      .minisvg { width: 100%; height: auto; border-radius: 6px; border: 1px solid var(--divider-color); }
      .mapsvg { width: 100%; height: auto; display: block; }
      .mapcard { padding: 12px; }
      .status h2 { margin: 0 0 6px; }
      .status p { margin: 0 0 8px; }
      .stages { margin: 0; padding-left: 18px; }
      .stages li { margin: 4px 0; }
      .bad { color: var(--error-color, #db4437); }
      .btn:disabled { opacity: .5; cursor: default; }
      .btn { margin-top: 12px; align-self: flex-start; min-height: 36px; padding: 0 14px; border-radius: 18px; cursor: pointer;
        border: 1px solid var(--primary-color); background: none; color: var(--primary-color); }
      .link { border: 0; background: none; color: var(--primary-color); cursor: pointer; padding: 0; font-size: 14px; }
      .split { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
      .main { flex: 999 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
      .side { flex: 1 1 280px; display: flex; flex-direction: column; gap: 16px; }
      .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--divider-color); }
      .tab { min-height: 44px; padding: 0 16px; border: 0; background: none; cursor: pointer; color: var(--secondary-text-color);
        border-bottom: 3px solid transparent; margin-bottom: -1px; }
      .tab.on { color: var(--primary-text-color); border-bottom-color: var(--primary-color); font-weight: 500; }
      .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .chip { min-height: 36px; padding: 0 14px; border-radius: 18px; cursor: pointer; border: 1px solid var(--divider-color); background: none; }
      .chip.on { border-color: var(--primary-color); color: var(--primary-color); font-weight: 500; }
      .hint { margin-left: auto; }
      .cell { cursor: pointer; }
      .lim { fill: ${P.path}; pointer-events: none; }
      .cell:hover { stroke: ${P.path}; stroke-width: 1.5; }
      .cell.sel { stroke: ${P.path}; stroke-width: 3; }
      .sp { fill: none; stroke: ${P.path}; stroke-opacity: 0.55; stroke-width: 1.5; }
      .sp-day { fill: none; stroke: var(--primary-color); stroke-width: 3; }
      .psun { fill: none; stroke: ${P.pano.sun}; stroke-width: 2.5; }
      .pano-stage { position: relative; touch-action: none; cursor: grab; border-radius: 10px; overflow: hidden; user-select: none; }
      .pano-stage:active { cursor: grabbing; }
      .pano-stage canvas { display: block; width: 100%; }
      .pano-ov { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
      .pgrid { fill: none; stroke: ${P.pano.grid}; stroke-width: 1; }
      .pface { fill: ${P.pano.sun}; }
      .pano-bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
      .pano-read { flex: 1; min-height: 18px; }
      .normal { fill: #8B5CF6; stroke: var(--card-background-color, #fff); stroke-width: 2; }
      .ax { fill: var(--secondary-text-color); font-size: 12px; }
      .grid { stroke: var(--divider-color); stroke-width: 0.5; }
      .todayrow { stroke: var(--primary-color); stroke-width: 2; }
      .legend { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12px; color: var(--secondary-text-color); }
      .lg { display: flex; align-items: center; gap: 6px; }
      .sw { width: 14px; height: 14px; border-radius: 3px; border: 1px solid var(--divider-color); display: inline-block; }
      .sw.hatch { background-color: ${P.unlearned}; background-image: repeating-linear-gradient(45deg, ${P.hatch} 0 2px, transparent 2px 6px); }
      .help { margin: 0; font-size: 13px; line-height: 1.55; color: var(--secondary-text-color); }
      .slider { display: flex; flex-direction: column; gap: 8px; }
      .sl-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
      .sl-row .btn { margin-top: 0; }
      .sl-label { font-weight: 500; min-width: 200px; }
      .sl-row input { flex: 1 1 200px; accent-color: var(--primary-color); }
      .round { width: 44px; height: 44px; border-radius: 22px; border: 1px solid var(--primary-color); background: none; color: var(--primary-color);
        display: flex; align-items: center; justify-content: center; cursor: pointer; }
      .strip { display: flex; height: 22px; border-radius: 4px; overflow: hidden; }
      .strip span { flex: 1; }
      .strip-ax { display: flex; justify-content: space-between; }
      .dhead { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
      .x { width: 36px; height: 36px; border-radius: 18px; border: 1px solid var(--divider-color); background: none; cursor: pointer; }
      .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
      .tile { display: flex; flex-direction: column; gap: 2px; border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; }
      .tile b { font-size: 18px; font-weight: 500; }
      .checks { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
      .mgrid { display: grid; grid-template-columns: minmax(84px, auto) repeat(12, minmax(0, 1fr)); gap: 3px; align-items: stretch; font-size: 12px; }
      .ml.col { flex-direction: column; align-items: flex-start; justify-content: center; }
      .mh { text-align: center; color: var(--secondary-text-color); font-size: 11px; }
      .ml { color: var(--secondary-text-color); display: flex; align-items: center; padding-right: 6px; white-space: nowrap; }
      .mc { border-radius: 6px; min-height: 26px; display: flex; align-items: center; justify-content: center; font-variant-numeric: tabular-nums;
        border: 1px solid var(--divider-color); }
      .mb { position: relative; height: 64px; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; }
      .mb i { display: block; width: 70%; border-radius: 6px 6px 2px 2px; background: linear-gradient(180deg, #F5A524, #FF6A3D 60%, #8B5CF6); opacity: .85; }
      .mb span { position: absolute; top: 0; font-size: 10px; color: var(--secondary-text-color); }
      .geo { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
      .compass { width: 120px; height: 120px; flex: none; }
      .compass .ring { fill: none; stroke: var(--divider-color); }
      .compass .cfg { stroke: var(--secondary-text-color); stroke-width: 2; stroke-dasharray: 3 3; }
      .compass .fit { stroke: url(#nx-geo); stroke-width: 4; stroke-linecap: round; }
      .tiles2 { flex: 1 1 200px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
      .tile.warn { border-color: #FF6A3D; } .tile.ok { border-color: rgba(46, 170, 110, .6); }
      .mgrid + .help, svg + .legend, .legend + .help, .note + .help, .help + .help { margin-top: 10px; }
      .note { margin: 12px 0 0; font-size: 13px; line-height: 1.5; } .note.warn { color: #E8543C; }
      .badge { margin-left: 8px; font-size: 11px; font-weight: 600; vertical-align: middle; padding: 2px 8px; border-radius: 999px;
        color: #fff; background: linear-gradient(115deg, #FF6A3D, #8B5CF6); }
    </style>`;
  }
}

// a page open across a Home Assistant restart loads the module again under a new cache buster; defining the element
// twice would throw, so the old one stays until the page is reloaded (the same in every module)
if (!customElements.get("noxcast-panel")) customElements.define("noxcast-panel", NoxcastPanel);
