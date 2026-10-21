// Noxcast sidebar panel: what the system has learned per array (shade, reflection, direction factor),
// plus further sections that live in their own modules (e.g. forecast accuracy).
// Plain custom element, no build step. Data comes from the websocket command "noxcast/learned".

// further sections: [key, custom element, module]; loaded on first use with this module's cache buster
const SECTIONS = [["accuracy", "noxcast-accuracy", "./noxcast-accuracy.js"], ["load", "noxcast-load", "./noxcast-load.js"],
  ["battery", "noxcast-battery", "./noxcast-battery.js"], ["diagnostics", "noxcast-diagnostics", "./noxcast-diagnostics.js"]];
const SECTION_TITLES = { de: { learned: "Gelerntes", accuracy: "Prognosegüte", load: "Last", battery: "Akku & Überschuss", diagnostics: "Diagnose" },
  en: { learned: "Learned", accuracy: "Forecast accuracy", load: "Load", battery: "Battery & surplus", diagnostics: "Diagnostics" } };

const AZ_STEP = 10, EL_STEP = 5, COARSE_AZ = 20, COARSE_EL = 15;
const RAD = Math.PI / 180;

const TEXT = {
  de: {
    title: "Gelerntes", overview: "Übersicht", map: "Karte", grid: "Jahresraster",
    shade: "Schatten", refl: "Reflexion", dir: "Richtungsfaktor",
    efficiency: "Wirkungsgrad", skyView: "Himmelssicht", tempCoeff: "Temperaturkoeff.", coverage: "Abdeckung Karte",
    dirRange: "Richtungsfaktor", reflMax: "Reflexion max.", samples: "Stunden", since: "Daten seit", learnedAt: "Letzter Lernlauf",
    free: "frei", partial: "Teilschatten", shaded: "Schatten", none: "keine", less: "weniger", more: "mehr",
    notLearned: "nicht gelernt", behind: "Sonne hinter Modul", today: "Heute", play: "Jahr abspielen", pause: "Anhalten",
    sunPathOn: "Sonnenbahn am", jun: "21. Juni", equ: "März/Sept.", dec: "21. Dez.",
    dayStrip: "Dieser Tag als Zeitleiste", selected: "Ausgewähltes Feld", sunHere: "Sonne steht hier",
    never: "nie im Lauf des Jahres", close: "Schließen", noData: "Noch keine Modulgruppe gelernt.",
    open: "Öffnen", months: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
    help: {
      shade: "Jedes Feld ist eine Sonnenposition. Die Farbe zeigt, wie viel Direktlicht dort ankommt. Folge einer Sonnenbahn von links nach rechts, um einen Tag abzulesen.",
      refl: "Zusätzliches Licht von beschienenen Wänden oder Dächern, in Prozent der Direktstrahlung. Gelernt nur dort, wo das Modul selbst wenig Direktlicht bekommt.",
      dir: "Was nach Schatten und Reflexion übrig bleibt: Geometrie- und Modellfehler. Werte nahe 1 sind gut.",
      grid: "Jede Zeile ein Monat, jede Spalte eine Stunde (Ortszeit). Dieselben Daten wie die Karte, ohne Sonnenwinkel.",
    },
    clickHint: "Feld anklicken für Details",
  },
  en: {
    title: "Learned", overview: "Overview", map: "Map", grid: "Year grid",
    shade: "Shade", refl: "Reflection", dir: "Direction factor",
    efficiency: "Efficiency", skyView: "Sky view", tempCoeff: "Temp. coefficient", coverage: "Map coverage",
    dirRange: "Direction factor", reflMax: "Reflection max.", samples: "Hours", since: "Data since", learnedAt: "Last learning run",
    free: "free", partial: "partial shade", shaded: "shade", none: "none", less: "less", more: "more",
    notLearned: "not learned yet", behind: "sun behind module", today: "Today", play: "Play year", pause: "Pause",
    sunPathOn: "Sun path on", jun: "21 June", equ: "Mar/Sep", dec: "21 Dec",
    dayStrip: "This day as a timeline", selected: "Selected cell", sunHere: "Sun is here",
    never: "never during the year", close: "Close", noData: "No array learned yet.",
    open: "Open", months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    help: {
      shade: "Each cell is a sun position. The colour shows how much direct light arrives there. Follow a sun path from left to right to read a day.",
      refl: "Extra light from sunlit walls or roofs, in percent of the direct normal irradiance. Learned only where the module itself gets little direct light.",
      dir: "What remains after shade and reflection: geometry and model errors. Values close to 1 are good.",
      grid: "Each row a month, each column an hour (local time). The same data as the map, without sun angles.",
    },
    clickHint: "Click a cell for details",
  },
};

const PALETTE = {
  light: {
    free: [233, 243, 226], mid: [214, 120, 72], shade: [122, 24, 24],
    base: [243, 241, 236], refl: [[243, 241, 236], [244, 190, 120], [209, 104, 22]],
    dir: [[47, 111, 176], [243, 241, 236], [217, 130, 43]],
    unlearned: "#D6D6D2", hatch: "rgba(255,255,255,0.6)", behind: "#EDEDEA", mapBg: "#FFFFFF", path: "#1D2125",
  },
  dark: {
    free: [118, 168, 108], mid: [214, 132, 72], shade: [150, 36, 36],
    base: [66, 70, 74], refl: [[66, 70, 74], [176, 110, 50], [240, 150, 60]],
    dir: [[90, 150, 220], [66, 70, 74], [235, 150, 60]],
    unlearned: "#34373A", hatch: "rgba(255,255,255,0.14)", behind: "#2B2D30", mapBg: "#1E2022", path: "#E4E6E8",
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
    this._state = { section: "learned", view: "overview", arrayId: null, mode: "map", layer: "shade", doy: null, sel: null, playing: false };
    this._sections = {};
    this._data = null;
    this._cache = {};
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("input", (ev) => this._onInput(ev));
  }

  set hass(hass) {
    const first = !this._hass;
    const dark = this._hass && this._hass.themes && this._hass.themes.darkMode;
    this._hass = hass;
    for (const el of Object.values(this._sections)) el.hass = hass;
    if (first) this._load();
    else if ((hass.themes && hass.themes.darkMode) !== dark) this._render();
  }

  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }

  connectedCallback() { if (this._data) this._render(); }
  disconnectedCallback() { this._stop(); }

  get _t() { return TEXT[(this._hass && this._hass.language || "en").slice(0, 2)] || TEXT.en; }
  get _pal() { return this._hass && this._hass.themes && this._hass.themes.darkMode ? PALETTE.dark : PALETTE.light; }
  get _lang() { return (this._hass && this._hass.language) || "en"; }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "noxcast/learned" });
      this._cache = {};
      if (this._state.doy === null) this._state.doy = dayOfYear(new Date());
    } catch (err) {
      this._error = String(err && err.message || err);
    }
    this._render();
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
    if (cosIncidence(arr.tilt, arr.azimuth, az, el) <= 0) return { kind: "behind" };
    const la = arr.learned || {};
    const shade = displayValue(la.cells || {}, ai, ei);
    if (shade === null) return { kind: "unlearned" };
    return {
      kind: "value", shade,
      refl: coarse(la.reflection, az, el, 0),
      dir: coarse(la.direction, az, el, 1),
    };
  }

  _pos(arr, az, el) {
    const ai = Math.floor(((az % 360) + 360) % 360 / AZ_STEP), ei = Math.floor(el / EL_STEP);
    if (el <= 0) return { kind: "night" };
    if (cosIncidence(arr.tilt, arr.azimuth, az, el) <= 0) return { kind: "behind" };
    const la = arr.learned || {};
    const shade = displayValue(la.cells || {}, ai, ei);
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
      this._sections[key].hass = this._hass;
    }
    return this._sections[key];
  }

  _render() {
    if (!this.shadowRoot) return;
    const t = this._t, s = this._state;
    const titles = SECTION_TITLES[(this._hass && this._hass.language || "en").slice(0, 2)] || SECTION_TITLES.en;
    const tabs = ["learned", ...SECTIONS.map(([k]) => k)].map((k) => `<button class="navtab ${s.section === k ? "on" : ""}" data-action="section" data-section="${k}">${titles[k]}</button>`).join("");
    if (s.section !== "learned") {
      this._stop();
      this.shadowRoot.innerHTML = `${this._style()}
        <div class="toolbar"><ha-menu-button></ha-menu-button><div class="brand">Noxcast</div><nav class="navtabs">${tabs}</nav></div>
        <div class="content" id="section"></div>`;
      const section = this._section(s.section);
      if (section) this.shadowRoot.getElementById("section").appendChild(section);
      const menu = this.shadowRoot.querySelector("ha-menu-button");
      if (menu) { menu.hass = this._hass; menu.narrow = this._narrow; }
      return;
    }
    let body;
    if (this._error) body = `<div class="card pad">${this._esc(this._error)}</div>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!this._data.arrays.length) body = `<div class="card pad">${t.noData}</div>`;
    else if (this._state.view === "array" && this._array(this._state.arrayId)) body = this._renderArray(this._array(this._state.arrayId));
    else body = this._renderOverview();
    const crumbs = this._state.view === "array"
      ? `<button class="link" data-action="overview">${t.overview}</button><span class="muted"> / </span><span>${this._esc(this._array(this._state.arrayId).name)}</span>`
      : `<span>${t.overview}</span>`;
    this.shadowRoot.innerHTML = `${this._style()}
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="brand">Noxcast</div>
        <nav class="navtabs">${tabs}</nav>
        <nav class="crumbs">${crumbs}</nav>
      </div>
      <div class="content">${body}</div>`;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) { menu.hass = this._hass; menu.narrow = this._narrow; }
  }

  _figures(arr) {
    const t = this._t, la = arr.learned;
    if (!la) return [];
    const pct = (v, d = 1) => `${(v * 100).toLocaleString(this._lang, { maximumFractionDigits: d })} %`;
    const num = (v, d = 2) => v.toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d });
    const dir = Object.values(la.direction || {}), refl = Object.values(la.reflection || {});
    const date = (s) => s ? new Date(typeof s === "number" ? s * 1000 : s).toLocaleDateString(this._lang, { timeZone: this._data.time_zone }) : "–";
    return [
      [t.efficiency, pct(la.efficiency)],
      [t.skyView, pct(la.sky_view ?? 1, 0)],
      [t.tempCoeff, `${num(la.temp_coeff * 100)} %/K`],
      [t.coverage, pct(la.coverage, 0)],
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
        <button class="mini" data-action="open" data-id="${arr.id}" data-layer="${layer}" aria-label="${this._esc(arr.name)}: ${t[layer]}">
          ${this._mapSvg(arr, layer, { mini: true })}
          <span class="muted small">${t[layer]}</span>
        </button>`).join("");
      return `<section class="card row">
        <div class="figs"><h2>${this._esc(arr.name)}</h2>${figs}
          <button class="btn" data-action="open" data-id="${arr.id}" data-layer="shade">${t.open}</button></div>
        <div class="minis">${maps}</div>
      </section>`;
    }).join("");
    return `<div class="stack">${cards}${this._legend("shade")}</div>`;
  }

  _renderArray(arr) {
    const t = this._t, s = this._state;
    const modeTabs = ["map", "grid"].map((m) => `<button role="tab" aria-selected="${s.mode === m}" class="tab ${s.mode === m ? "on" : ""}" data-action="mode" data-mode="${m}">${t[m]}</button>`).join("");
    const layerTabs = ["shade", "refl", "dir"].map((l) => `<button class="chip ${s.layer === l ? "on" : ""}" data-action="layer" data-layer="${l}">${t[l]}</button>`).join("");
    const main = s.mode === "map" ? `
        <div class="card mapcard">${this._mapSvg(arr, s.layer, {})}</div>
        ${this._slider(arr)}` : `<div class="card mapcard">${this._gridSvg(arr, s.layer)}</div>`;
    const figs = this._figures(arr).map(([k, v]) => `<div class="fig"><span class="muted">${k}</span><span class="mono">${v}</span></div>`).join("");
    return `
      <div class="split">
        <div class="main">
          <div class="tabs" role="tablist">${modeTabs}</div>
          <div class="chips">${layerTabs}${s.mode === "map" ? `<span class="muted small hint">${t.clickHint}</span>` : ""}</div>
          ${main}
          ${this._legend(s.layer)}
        </div>
        <aside class="side">
          ${s.sel ? this._detail(arr) : ""}
          <section class="card pad"><h2>${this._esc(arr.name)}</h2>${figs}</section>
          <section class="card pad"><h3>${s.mode === "grid" ? t.grid : t[s.layer]}</h3><p class="help">${s.mode === "grid" ? t.help.grid : t.help[s.layer]}</p></section>
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
    const hatch = `<defs><pattern id="nx-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="${P.unlearned}"></rect><line x1="0" y1="0" x2="0" y2="6" stroke="${P.hatch}" stroke-width="2"></line></pattern></defs>`;
    const vb = mini ? `0 0 ${W} ${H}` : `-40 -8 ${W + 48} ${H + 34}`;
    return `<svg viewBox="${vb}" class="${mini ? "minisvg" : "mapsvg"}" role="img" aria-label="${t[layer]}">${hatch}<rect width="${W}" height="${H}" fill="${P.mapBg}"></rect>${axes}${rects}${paths}</svg>`;
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
        <div class="tile"><span class="muted small">${t.shade}</span><b class="mono">${num(c.shade)}</b><span class="small">${label}</span></div>
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

  _legend(layer) {
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
    else if (a === "layer") s.layer = el.dataset.layer;
    else if (a === "cell") s.sel = [Number(el.dataset.ai), Number(el.dataset.ei)];
    else if (a === "close") s.sel = null;
    else if (a === "today") { s.doy = dayOfYear(new Date()); this._stop(); }
    else if (a === "play") { if (s.playing) this._stop(); else this._play(); }
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

  _esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
      .crumbs { margin-left: 8px; display: flex; gap: 4px; align-items: center; font-size: 14px; }
      .content { padding: 16px; max-width: 1400px; margin: 0 auto; box-sizing: border-box; }
      .muted { color: var(--secondary-text-color); }
      .small { font-size: 12px; }
      .mono { font-family: var(--code-font-family, ui-monospace, monospace); }
      .card { background: var(--card-background-color, var(--ha-card-background)); border-radius: var(--ha-card-border-radius, 12px);
        border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color)); box-shadow: var(--ha-card-box-shadow, none); }
      .pad { padding: 16px; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
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
      .cell:hover { stroke: ${P.path}; stroke-width: 1.5; }
      .cell.sel { stroke: ${P.path}; stroke-width: 3; }
      .sp { fill: none; stroke: ${P.path}; stroke-opacity: 0.55; stroke-width: 1.5; }
      .sp-day { fill: none; stroke: var(--primary-color); stroke-width: 3; }
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
    </style>`;
  }
}

customElements.define("noxcast-panel", NoxcastPanel);
