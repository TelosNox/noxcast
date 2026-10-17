// Noxcast day card: power flow now, today's yield per array with significant effects, house load,
// battery and unused surplus. Plain custom element, no build step.
//
// type: custom:noxcast-card
// pv:
//   entity: sensor.pv_total            # optional, otherwise the sum of the groups
//   groups:
//     - entity: sensor.pv_balcony
//       name: Balkon                   # also the Noxcast array name, unless "array" is given
// grid: { entity: sensor.grid, invert: false }        # positive = import
// home: { entity: sensor.house_load }
// batteries:
//   - { entity: sensor.bat_power, soc: sensor.bat_soc, capacity: 1.9, name: Pro, invert: false }  # positive = charging
//     # optional limits in percent, a number or an entity: max_soc (default 100), min_soc (default 0)
// export: false                        # surplus can be fed in; otherwise it counts as unused once batteries are full
// legend: true                         # legend below the chart, only for what is shown
// font_scale: 1                        # text size, 0.8 to 1.6 (also in the visual editor)

const TEXT = {
  de: { pv: "PV", home: "Haus", grid: "Netz", battery: "Akku", tomorrow: "morgen", shade: "Schatten",
    refl: "Reflexion", unused: "ungenutzt", full: "voll", load: "Hauslast", soc: "Akku-Ladestand", lost: "Schattenverlust",
    noData: "Noch keine Noxcast-Daten", kwh: "kWh", actual: "Ist", expected: "erwartet", morning: "Prognose früh",
    start: "Start", empty: "leer", freeUntil: "frei bis", noSurplus: "kein Überschuss mehr", fullAt: "Akku voll",
    lasts: "Akku reicht bis morgen", emptyAt: "Akku leer um",
    lg: { actual: "Ist", expected: "Erwartung", forecast: "Prognose", load: "Hauslast", deficit: "Last über PV",
      loss: "Schattenverlust", shade: "Schatten", refl: "Reflexion", bat: "Akku", lost: "voll, Überschuss verfällt",
      near: "fast voll" } },
  en: { pv: "PV", home: "Home", grid: "Grid", battery: "Battery", tomorrow: "tomorrow", shade: "shade",
    refl: "reflection", unused: "unused", full: "full", load: "House load", soc: "Battery level", lost: "Lost to shade",
    noData: "No Noxcast data yet", kwh: "kWh", actual: "Actual", expected: "expected", morning: "morning forecast",
    start: "start", empty: "empty", freeUntil: "surplus until", noSurplus: "no more surplus", fullAt: "battery full",
    lasts: "battery lasts until tomorrow", emptyAt: "battery empty at",
    lg: { actual: "actual", expected: "expected", forecast: "forecast", load: "house load", deficit: "load above PV",
      loss: "lost to shade", shade: "shade", refl: "reflection", bat: "battery", lost: "full, surplus lost",
      near: "almost full" } },
};

const PALETTE = {
  light: { pv: "#F2B233", pvDark: "#C27A06", pvText: "#9A6400", grid: "#2F80C8", home: "#4F5964", bat: "#23874A",
    shade: "#C0392B", refl: "#B07A00", rail: "rgba(0,0,0,0.08)", unused: "#2F80C8", unusedBg: "rgba(47,128,200,0.12)",
    warnText: "#8E2418", warnBg: "#FBE3DE", lost: "#D93025", near: "#F29900" },
  dark: { pv: "#F2B233", pvDark: "#C58410", pvText: "#F2B233", grid: "#5AA6E8", home: "#B4BCC4", bat: "#4CC77A",
    shade: "#F07058", refl: "#E8B640", rail: "rgba(255,255,255,0.10)", unused: "#5AA6E8", unusedBg: "rgba(90,166,232,0.15)",
    warnText: "#FFB4A6", warnBg: "#4A2620", lost: "#F2635A", near: "#F5A524" },
};

const ACTIVE_W = 5;
const REFRESH_MS = 5 * 60 * 1000;
// symmetric: PV paths on top (y 80), grid <-> battery in the middle (y 88), paths to the home below (y 96);
// where grid <-> battery crosses PV -> home it bridges over it, so the two read as separate paths
const FLOW_PATHS = {
  pvHome: "M204 62 L204 146",
  pvBat: "M212 62 L212 70 Q212 80 222 80 L352 80",
  pvGrid: "M196 62 L196 70 Q196 80 186 80 L52 80",
  gridHome: "M52 96 L186 96 Q196 96 196 106 L196 146",
  batHome: "M352 96 L222 96 Q212 96 212 106 L212 146",
  gridBat: "M52 88 L198 88 A6 6 0 0 1 210 88 L352 88",          // bridge over PV -> home
};

class NoxcastCard extends HTMLElement {
  static getConfigElement() { return document.createElement("noxcast-card-editor"); }

  static getStubConfig() {
    return { pv: { groups: [] }, grid: { entity: "" }, home: { entity: "" }, batteries: [] };
  }

  setConfig(config) {
    if (!config || !config.pv) throw new Error("pv is required");
    this._config = config;
    if (this._hass) this._renderAll();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (first) { this._renderAll(); this._refresh(); return; }
    const sig = this._signature();
    if (sig !== this._sig) { this._sig = sig; this._renderFlow(); }
    if (this._stale()) this._refresh();
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._refresh(), REFRESH_MS);
    if (!this._onVisible) {
      this._onVisible = () => { if (document.visibilityState === "visible" && this._stale(60)) this._refresh(); };
      document.addEventListener("visibilitychange", this._onVisible);
    }
    if (this._hass && this._stale(60)) this._refresh();
    if (this._flow) this._animate();
  }

  disconnectedCallback() {
    cancelAnimationFrame(this._frame);
    this._frame = null;
    clearInterval(this._timer);
    clearTimeout(this._midnight);
    this._timer = null;
    if (this._onVisible) document.removeEventListener("visibilitychange", this._onVisible);
    this._onVisible = null;
  }

  // data older than `seconds`, or from another day
  _stale(seconds = REFRESH_MS / 1000) {
    if (!this._hass || this._loading) return false;
    if (!this._data) return !this._error;
    if (this._localMidnight(new Date()) !== this._data.dayStart) return true;
    return Date.now() - this._data.loaded > seconds * 1000;
  }

  // reload shortly after the next local midnight, so the card turns to the new day by itself
  _scheduleMidnight() {
    clearTimeout(this._midnight);
    if (!this.isConnected) return;
    const next = this._localMidnight(new Date(Date.now() + 86400000));
    this._midnight = setTimeout(() => this._refresh(), Math.max(60000, next - Date.now() + 90000));
  }

  get _t() { return TEXT[(this._hass.language || "en").slice(0, 2)] || TEXT.en; }
  get _p() { return this._hass.themes && this._hass.themes.darkMode ? PALETTE.dark : PALETTE.light; }
  get _lang() { return this._hass.language || "en"; }
  get _tz() { return this._hass.config.time_zone; }
  get _scale() { return Math.min(1.6, Math.max(0.8, Number(this._config.font_scale) || 1)); }

  // ------------------------------------------------------------ live values (W, canonical signs)

  _watts(entityId, invert) {
    const s = entityId && this._hass.states[entityId];
    if (!s) return null;
    let v = parseFloat(s.state);
    if (!isFinite(v)) return null;
    const unit = (s.attributes.unit_of_measurement || "W").toLowerCase();
    if (unit === "kw") v *= 1000;
    return invert ? -v : v;
  }

  _soc(entityId) {
    const s = entityId && this._hass.states[entityId];
    const v = s ? parseFloat(s.state) : NaN;
    return isFinite(v) ? v : null;
  }

  _live() {
    const c = this._config;
    const groups = (c.pv.groups || []).map((g) => ({ name: g.name || g.entity, w: Math.max(0, this._watts(g.entity, g.invert) || 0) }));
    let pv = c.pv.entity ? this._watts(c.pv.entity, c.pv.invert) : null;
    if (pv === null) pv = groups.reduce((a, g) => a + g.w, 0);
    const bats = (c.batteries || []).map((b) => ({ name: b.name || b.entity, w: this._watts(b.entity, b.invert) || 0,
      soc: this._soc(b.soc), cap: Number(b.capacity) || 0 }));
    const capSum = bats.reduce((a, b) => a + b.cap, 0);
    const socAll = capSum ? bats.reduce((a, b) => a + (b.soc ?? 0) * b.cap, 0) / capSum
      : (bats.length ? bats.reduce((a, b) => a + (b.soc ?? 0), 0) / bats.length : null);
    return {
      pv: Math.max(0, pv), groups,
      home: Math.max(0, this._watts(c.home && c.home.entity, c.home && c.home.invert) || 0),
      grid: this._watts(c.grid && c.grid.entity, c.grid && c.grid.invert) || 0,
      bat: bats.reduce((a, b) => a + b.w, 0), bats, socAll,
    };
  }

  _signature() {
    const c = this._config, ids = [c.pv.entity, ...(c.pv.groups || []).map((g) => g.entity), c.grid && c.grid.entity,
      c.home && c.home.entity, ...(c.batteries || []).flatMap((b) => [b.entity, b.soc])].filter(Boolean);
    return ids.map((i) => this._hass.states[i] && this._hass.states[i].state).join("|") + (this._hass.themes && this._hass.themes.darkMode);
  }

  // ------------------------------------------------------------ data for the day band

  async _refresh() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      const today = await this._hass.connection.sendMessagePromise({ type: "noxcast/today" });
      const stats = await this._history();
      this._data = { today, ...stats, loaded: Date.now() };
      this._error = null;
    } catch (err) {
      this._error = String((err && err.message) || err);
    } finally {
      this._loading = false;
    }
    this._scheduleMidnight();
    this._renderBand();
  }

  async _history() {
    const c = this._config, now = new Date();
    const dayStart = this._localMidnight(now);
    const ids = [c.home && c.home.entity, ...(c.batteries || []).map((b) => b.soc)].filter(Boolean);
    // the last 7 days give the load profile for the rest of today and the night
    if (!ids.length) return { stats: {}, dayStart };
    const res = await this._hass.connection.sendMessagePromise({
      type: "recorder/statistics_during_period", start_time: new Date(dayStart - 7 * 86400000).toISOString(),
      end_time: now.toISOString(), statistic_ids: ids, period: "hour", types: ["mean", "max"],
    });
    return { stats: res, dayStart };
  }

  _localMidnight(d) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: this._tz, hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(d).map((p) => [p.type, p.value]));
    const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
    const offset = asUtc - Math.floor(d.getTime() / 1000) * 1000;       // the parts carry no milliseconds
    return Date.UTC(+parts.year, +parts.month - 1, +parts.day) - offset;
  }

  // ------------------------------------------------------------ rendering

  _renderAll() {
    const P = this._p;
    this.shadowRoot.innerHTML = `<style>
      ha-card { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
      .muted { color: var(--secondary-text-color); }
      .row { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; font-size: calc(13px * var(--nx-scale, 1)); }
      .big { font-size: calc(18px * var(--nx-scale, 1)); font-weight: 500; }
      .warn { font-size: calc(12px * var(--nx-scale, 1)); font-weight: 500; padding: 2px 6px; border-radius: 4px; }
      svg { display: block; width: 100%; height: auto; overflow: visible; }
      .legend { display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: calc(11px * var(--nx-scale, 1)); color: var(--secondary-text-color); }
      .legend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
      .legend svg { width: 16px; height: 10px; display: inline-block; }
      .ax { font-size: calc(9px * min(var(--nx-scale, 1), 1.2)); fill: var(--secondary-text-color); font-family: var(--code-font-family, monospace); }
      .divider { border-top: 1px solid var(--divider-color); padding-top: 8px; }
    </style>
    <ha-card style="--nx-scale:${this._scale}"><div id="flow"></div><div id="band" class="divider"></div></ha-card>`;
    this._sig = this._signature();
    this._renderFlow();
    this._renderBand();
  }

  // The flow is built once; later updates only change colours, texts and speeds, so the dots never jump.
  _renderFlow() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("flow");
    if (!el) return;
    const P = this._p, t = this._t, v = this._live();
    // allocation of the flows, all values >= 0
    const pvHome = Math.min(v.pv, v.home);
    const charge = Math.max(v.bat, 0), discharge = Math.max(-v.bat, 0);
    const pvBat = Math.min(v.pv - pvHome, charge);
    const pvGrid = Math.max(v.pv - pvHome - pvBat, 0);
    const batHome = Math.min(discharge, v.home - pvHome);
    const gridHome = Math.max(v.home - pvHome - batHome, 0);
    const gridBat = Math.max(charge - pvBat, 0);
    const batGrid = Math.max(discharge - batHome, 0);
    const flows = {
      pvHome: [pvHome, P.pv, 1], pvBat: [pvBat, P.pv, 1], pvGrid: [pvGrid, P.pv, 1], gridHome: [gridHome, P.grid, 1],
      batHome: [batHome, P.bat, 1], gridBat: [Math.max(gridBat, batGrid), gridBat >= batGrid ? P.grid : P.bat, batGrid > gridBat ? -1 : 1],
    };
    const sc = this._scale, fs = (n) => (n * sc).toFixed(1);
    const lift = (sc - 1) * 18;                  // larger text: PV symbol and groups move up
    const subY = (i) => 122 + 16 * sc + i * 14 * sc, homeY = 176 + 16 * sc;
    const height = Math.ceil(Math.max(homeY + 4, v.bats.length > 1 ? subY(Math.min(v.bats.length, 3) - 1) + 4 : 0, 186));
    if (!this._flow || this._flow.height !== height + lift || !el.contains(this._flow.svg)) {
      el.innerHTML = `<svg viewBox="0 ${(-lift).toFixed(1)} 408 ${(height + lift).toFixed(1)}" role="img">
        ${Object.entries(FLOW_PATHS).map(([k, d]) => `<path data-k="${k}" d="${d}" fill="none" stroke-linecap="round" stroke-linejoin="round"></path>`).join("")}
        ${Object.keys(FLOW_PATHS).map((k) => `<circle data-dot="${k}" r="4.5" stroke-width="2" visibility="hidden"></circle>`).join("")}
        <g data-labels></g></svg>`;
      const svg = el.querySelector("svg");
      const lines = {}, dots = {};
      for (const k of Object.keys(FLOW_PATHS)) {
        const path = svg.querySelector(`[data-k="${k}"]`);
        lines[k] = { path, len: path.getTotalLength ? path.getTotalLength() : 0, pos: 0, speed: 0, dir: 1 };
        dots[k] = svg.querySelector(`[data-dot="${k}"]`);
      }
      this._flow = { svg, lines, dots, height: height + lift, labels: svg.querySelector("[data-labels]") };
    }
    const f = this._flow;
    f.svg.setAttribute("aria-label", `${t.pv} ${Math.round(v.pv)} W, ${t.home} ${Math.round(v.home)} W, ${t.grid} ${Math.round(v.grid)} W, ${t.battery} ${Math.round(v.bat)} W`);
    for (const [k, [p, color, dir]] of Object.entries(flows)) {
      const line = f.lines[k], dot = f.dots[k], on = p >= ACTIVE_W;
      line.path.setAttribute("stroke", on ? color : P.rail);
      line.path.setAttribute("stroke-width", on ? "3.5" : "2");
      line.path.setAttribute("stroke-opacity", on ? "0.85" : "1");
      line.speed = on ? Math.min(140, 18 + p / 8) : 0;         // px per second: the speed carries the power
      line.dir = dir;
      dot.setAttribute("fill", "var(--card-background-color, #fff)");
      dot.setAttribute("stroke", color);
      dot.setAttribute("visibility", on && line.len ? "visible" : "hidden");
    }
    const fmtW = (x) => `${Math.round(x).toLocaleString(this._lang)} W`;
    const op = (x) => (Math.abs(x) < ACTIVE_W ? 0.35 : 1);
    const groups = v.groups.map((g) => `${this._esc(g.name)} ${Math.round(g.w)}`).join(" · ");
    const bats = v.bats.slice(0, 3).map((b, i) => `<text x="402" y="${subY(i).toFixed(1)}" text-anchor="end" font-size="${fs(11)}" fill="currentColor">${this._esc(b.name)} ${b.w > 0 ? "+" : ""}${Math.round(b.w)} · ${b.soc === null ? "–" : Math.round(b.soc)} %</text>`).join("");
    const gridLabel = v.grid > 0 ? `${fmtW(v.grid)} ↓` : v.grid < 0 ? `${fmtW(-v.grid)} ↑` : "0 W";
    f.labels.innerHTML = `
      <g opacity="${op(v.pv)}">
        <path transform="translate(0 ${(-lift).toFixed(1)})" d="M196 22l8-8l8 8M204 14v18M193 30h22" fill="none" stroke="${P.pv}" stroke-width="2" stroke-linecap="round"></path>
        <text x="204" y="54" text-anchor="middle" font-size="${fs(17)}" font-weight="500" fill="${P.pvText}">${fmtW(v.pv)}</text>
        ${groups ? `<text x="204" y="${(8 - lift).toFixed(1)}" text-anchor="middle" font-size="${fs(11)}" fill="${P.pvText}">${groups}</text>` : ""}
      </g>
      <g opacity="${op(v.grid)}">
        <path d="M22 96l6-22l6 22M25 86h6M20 96h16" fill="none" stroke="${P.grid}" stroke-width="2" stroke-linecap="round"></path>
        <text x="6" y="122" font-size="${fs(15)}" font-weight="500" fill="${P.grid}">${gridLabel}</text>
      </g>
      <g opacity="${op(v.bat)}">
        <rect x="364" y="74" width="20" height="26" rx="3" fill="none" stroke="${P.bat}" stroke-width="2"></rect>
        <rect x="369" y="70" width="10" height="4" rx="1" fill="${P.bat}"></rect>
        <rect x="367" y="${97 - 20 * (v.socAll ?? 0) / 100}" width="14" height="${20 * (v.socAll ?? 0) / 100}" rx="1" fill="${P.bat}"></rect>
        <text x="402" y="122" text-anchor="end" font-size="${fs(15)}" font-weight="500" fill="${P.bat}">${v.bat > 0 ? "+" : ""}${fmtW(v.bat)}${v.socAll === null ? "" : ` · ${Math.round(v.socAll)} %`}</text>
      </g>
      ${v.bats.length > 1 ? `<g color="var(--secondary-text-color)">${bats}</g>` : ""}
      <path d="M190 160l14-12l14 12M193 158v16h22v-16" fill="none" stroke="${P.home}" stroke-width="2" stroke-linejoin="round"></path>
      <text x="204" y="${homeY.toFixed(1)}" text-anchor="middle" font-size="${fs(15)}" font-weight="500" fill="var(--primary-text-color)">${fmtW(v.home)}</text>`;
    this._animate();
  }

  // moves the dots frame by frame; a speed change only changes the pace, never the position
  _animate() {
    if (this._frame || !this.isConnected) return;
    const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let last = performance.now();
    const step = (time) => {
      this._frame = null;
      const f = this._flow;
      if (!f || !this.isConnected) return;
      const dt = Math.min(0.1, (time - last) / 1000);
      last = time;
      let moving = false;
      for (const [k, line] of Object.entries(f.lines)) {
        if (!line.speed || !line.len) continue;
        moving = true;
        if (!reduced) line.pos = (line.pos + line.dir * line.speed * dt + line.len) % line.len;
        const pt = line.path.getPointAtLength(reduced ? line.len / 2 : line.pos);
        f.dots[k].setAttribute("cx", pt.x.toFixed(1));
        f.dots[k].setAttribute("cy", pt.y.toFixed(1));
      }
      if (moving && !reduced) this._frame = requestAnimationFrame(step);
    };
    this._frame = requestAnimationFrame(step);
  }

  _renderBand() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("band");
    if (!el) return;
    const t = this._t, P = this._p;
    if (this._error) { el.innerHTML = `<div class="muted">${this._esc(this._error)}</div>`; return; }
    if (!this._data) { el.innerHTML = `<div class="muted">…</div>`; return; }
    const arrays = this._arrays();
    if (!arrays.length) { el.innerHTML = `<div class="muted">${t.noData}</div>`; return; }

    const now = Date.now() / 1000, H = 3600;
    const sum = (key) => { const m = new Map(); for (const a of arrays) for (const [h, v] of a[key] || []) m.set(h, (m.get(h) || 0) + v); return m; };
    const expM = sum("expected"), fcM = sum("forecast"), mornM = sum("morning"), lossM = sum("loss"), tomM = sum("tomorrow");
    const exp = (h) => expM.get(h) || 0, loss = (h) => lossM.get(h) || 0;
    const fc = (h) => (fcM.has(h) ? fcM.get(h) : mornM.get(h)) || 0;
    const pv = (h) => (expM.has(h) ? exp(h) : tomM.get(h)) || 0;
    const sunny = [...new Set([...expM.keys(), ...fcM.keys()])].filter((h) => exp(h) > 1 || fc(h) > 1).sort((a, b) => a - b);
    if (!sunny.length) { el.innerHTML = `<div class="muted">${t.noData}</div>`; return; }
    const t0 = sunny[0], t1 = sunny[sunny.length - 1] + H;
    const dayH = []; for (let h = t0; h < t1; h += H) dayH.push(h);

    const m = this._model(now);
    const load = m.load;
    const W = 408, CAPW = 46, X0 = CAPW + 6, X1 = W - CAPW - 6, Hp = 116;
    const x = (ts) => X0 + (Math.max(t0, Math.min(t1, ts)) - t0) / (t1 - t0) * (X1 - X0);
    // fixed scale: installed peak power + 10 %, so a weak day shows as a low curve
    const peakW = arrays.reduce((a, arr) => a + (arr.peak_w || 0), 0);
    const top = peakW > 0 ? peakW * 1.1 : Math.max(...dayH.map((h) => Math.max(fc(h) + loss(h), exp(h))), 200) * 1.08;
    const step = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000].find((s) => top / s <= 4) || 20000;
    const ymax = peakW > 0 ? top : Math.ceil(top / step) * step;
    const y = (p) => Hp - 2 - Math.min(p, ymax) / ymax * (Hp - 12);
    const pts = (f) => [[x(t0), y(0)], ...dayH.map((h) => [x(h + H / 2), y(f(h))]), [x(t1), y(0)]];
    const fill = (p) => `${line(p)} L${p[p.length - 1][0].toFixed(1)},${(Hp - 2).toFixed(1)} L${p[0][0].toFixed(1)},${(Hp - 2).toFixed(1)} Z`;
    const between = (hi, lo) => { const a = pts(hi), b = pts(lo).reverse(); return `${line(a)} L${b[0][0].toFixed(1)},${b[0][1].toFixed(1)}${curve(b)} Z`; };
    const nowX = now > t0 && now < t1 ? x(now) : null;

    // PV: expectation pale, the past part (= actual) solid, held forecast as contour, shade as notch below the potential
    const potential = (h) => fc(h) + loss(h);
    const lossTotal = dayH.reduce((a, h) => a + loss(h), 0);
    const hasLoad = dayH.some((h) => load(h) !== null);
    const lv = (h) => load(h) ?? 0;
    const grid = []; for (let v = step; v <= ymax + 1e-9; v += step) grid.push(v);
    const kw = (v) => (v / 1000).toLocaleString(this._lang, { maximumFractionDigits: 2 });
    // load above the scale: one marker per run of hours, at its highest value
    const spikes = [];
    for (const h of hasLoad ? dayH : []) {
      const v = m.loadMax(h) ?? lv(h);
      if (v <= ymax) continue;
      const last = spikes[spikes.length - 1];
      if (last && h - last.end <= H) { last.end = h; if (v > last.v) Object.assign(last, { h, v }); } else spikes.push({ h, end: h, v });
    }
    let svgPv = `${grid.map((v) => `<line x1="${X0}" x2="${X1}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="${P.rail}"></line>
        <text x="${X1 + 3}" y="${(y(v) + 3).toFixed(1)}" class="ax">${kw(v)}</text>`).join("")}
      <text x="${X1 + 3}" y="${(y(ymax) - 9).toFixed(1)}" class="ax">kW</text>
      ${lossTotal > 1 ? `<path d="${between(potential, fc)}" fill="url(#nx-shade)"></path>
        <path d="${line(pts(potential))}" fill="none" stroke="${P.shade}" stroke-width="1" stroke-opacity=".6"></path>` : ""}
      <path d="${fill(pts(exp))}" fill="${P.pv}" fill-opacity=".3"></path>
      <path d="${fill(pts(exp))}" fill="${P.pv}" clip-path="url(#nx-past)"></path>
      ${hasLoad ? `<path d="${between((h) => Math.max(lv(h), exp(h)), exp)}" fill="${P.grid}" fill-opacity=".3"></path>` : ""}
      <path d="${line(pts(fc))}" fill="none" stroke="${P.pvDark}" stroke-width="1.4" stroke-dasharray="3 3"></path>
      ${hasLoad ? `<path d="${line(dayH.map((h) => [x(h + H / 2), y(lv(h))]))}" fill="none" stroke="${P.home}" stroke-width="1.5"></path>` : ""}
      ${spikes.map(({ h, v }) => { const sx = x(h + H / 2); return `<path d="M${(sx - 4).toFixed(1)},8 L${(sx + 4).toFixed(1)},8 L${sx.toFixed(1)},2 Z" fill="${P.shade}"></path>
        <text x="${(sx + 6).toFixed(1)}" y="9" font-size="9.5" fill="${P.shade}">${kw(v)} kW</text>`; }).join("")}
      <line x1="${X0}" x2="${X1}" y1="${Hp - 1.5}" y2="${Hp - 1.5}" stroke="var(--divider-color)"></line>`;

    // one lane per array: shade always, reflection where it adds something
    const Ly = Hp + 6, Lh = 9;
    const lanes = arrays.map((arr, i) => {
      const ly = Ly + i * (Lh + 4);
      const fx = arr.periods.filter((p) => p.end > t0 && p.start < t1).map((p) => {
        const title = `${p.type === "shade" ? t.shade : t.refl} ${this._hm(p.start)}–${this._hm(p.end)} · ${p.type === "shade" ? "−" : "+"}${this._kwh(p.wh)} ${t.kwh}`;
        return `<rect x="${x(p.start).toFixed(1)}" y="${ly + 1}" width="${Math.max(2, x(p.end) - x(p.start)).toFixed(1)}" height="${Lh - 2}" rx="2" fill="${p.type === "shade" ? P.shade : P.refl}"><title>${this._esc(title)}</title></rect>`;
      }).join("");
      return `<line x1="${X0}" x2="${X1}" y1="${ly + Lh / 2}" y2="${ly + Lh / 2}" stroke="${P.rail}" stroke-width="2"></line>${fx}
        <text x="${X1 - 2}" y="${ly + Lh - 1}" text-anchor="end" font-size="9" fill="var(--secondary-text-color)" stroke="var(--card-background-color, #fff)" stroke-width="3" paint-order="stroke">${this._esc(arr.label)}</text>`;
    }).join("");

    // battery: measured level so far, simulated with expected PV and load until the next charge tomorrow
    const B0 = Ly + arrays.length * (Lh + 4) + 26, Bh = 22, ys = (f) => B0 + Bh - Math.max(0, Math.min(1, f)) * Bh;
    const bat = m.battery;
    let batSvg = "", sim = null, chargeToday = null, chargeTom = null;
    const seenZones = new Set();
    if (bat) {
      chargeToday = dayH.find((h) => load(h) !== null && pv(h) > lv(h)) ?? t0;
      const tomH = [...tomM.keys()].sort((a, b) => a - b);
      chargeTom = tomH.find((h) => pv(h) > (load(h) ?? 0)) ?? (tomH.length ? tomH[0] : t1 + 12 * H);
      sim = this._simulate(bat, now, chargeTom, pv, load);
      const zone = (h) => {                       // red: at the limit with surplus (lost), orange: within 5 % of it
        const f = h + H <= now ? m.socAt(h + H / 2) : sim.at(h + H / 2);
        if (f === null) return null;
        if (f >= bat.max - 0.01 && pv(h) > lv(h)) return P.lost;
        return f >= bat.max - 0.05 ? P.near : null;
      };
      // ahead: the whole strip height as a warning; already happened: a stripe on top, so the level stays readable
      const zones = dayH.map((h) => { const c = zone(h), done = h + H <= now; if (c) seenZones.add(c); return c ? `<rect x="${x(h).toFixed(1)}" y="${B0}" width="${(x(h + H) - x(h) + 0.3).toFixed(1)}" height="${done ? 5 : Bh}" fill="${c}" fill-opacity="${done ? 0.9 : 0.85}"></rect>` : ""; }).join("");
      const past = m.socPast.filter(([ts]) => ts >= t0 && ts <= Math.min(now, t1)).map(([ts, f]) => [x(ts), ys(f)]);
      const fut = sim.points.filter(([ts]) => ts <= t1).map(([ts, f]) => [x(ts), ys(f)]);
      const startF = chargeToday <= now ? m.socAt(chargeToday) : sim.at(chargeToday);
      const endF = sim.empty !== null && sim.empty < chargeTom ? null : sim.at(chargeTom);
      const cap = (cx, f, label, time, future, empty) => {
        const bw = 12, bx = cx - bw / 2, h = Bh + 12, by = B0 - 12, fh = (f ?? 0) * (h - 4);
        const color = empty ? P.grid : P.bat;
        return `<rect x="${bx}" y="${by}" width="${bw}" height="${h}" rx="2" fill="none" stroke="${color}" stroke-width="1.4"${future ? ` stroke-dasharray="2 2"` : ""}></rect>
          <rect x="${bx + 2}" y="${(by + h - 2 - fh).toFixed(1)}" width="${bw - 4}" height="${fh.toFixed(1)}" fill="${P.bat}" fill-opacity="${future ? 0.45 : 0.85}"></rect>
          <text x="${cx}" y="${by - 13}" text-anchor="middle" font-size="11" font-weight="500" fill="${color}">${empty ? t.empty : f === null ? "–" : `${Math.round(f * 100)} %`}</text>
          <text x="${cx}" y="${by - 3}" text-anchor="middle" class="ax">${label}</text>
          <text x="${cx}" y="${B0 + Bh + 11}" text-anchor="middle" class="ax">${time}</text>`;
      };
      batSvg = `<rect x="${X0}" y="${B0}" width="${X1 - X0}" height="${Bh}" rx="3" fill="${P.rail}"></rect>
        ${past.length > 1 ? `<path d="${line(past)} L${past[past.length - 1][0].toFixed(1)},${B0 + Bh} L${past[0][0].toFixed(1)},${B0 + Bh} Z" fill="${P.bat}" fill-opacity=".45"></path>` : ""}${zones}
        ${fut.length > 1 ? `<path d="${line(fut)}" fill="none" stroke="${P.bat}" stroke-width="1.8" stroke-dasharray="3 3"></path>` : ""}
        ${sim.full !== null && sim.full < t1 ? `<circle cx="${x(sim.full).toFixed(1)}" cy="${ys(bat.max).toFixed(1)}" r="3" fill="var(--card-background-color, #fff)" stroke="${P.bat}" stroke-width="1.6"></circle>` : ""}
        ${cap(CAPW / 2, startF, chargeToday > now ? t.expected : t.start, this._hm(chargeToday), chargeToday > now, false)}
        ${cap(W - CAPW / 2, endF, t.tomorrow, endF === null ? this._hm(sim.empty) : this._hm(chargeTom), true, endF === null)}`;
    }
    const bottom = bat ? B0 + Bh + 14 : Ly + arrays.length * (Lh + 4) + 14;
    const tickStep = Math.max(1, Math.ceil((t1 - t0) / H / 6)) * H;
    const ticks = []; for (let h = Math.ceil(t0 / tickStep) * tickStep; h <= t1; h += tickStep) if (nowX === null || Math.abs(x(h) - nowX) > 16) ticks.push(h);

    const svg = `<svg viewBox="0 0 ${W} ${bottom}" role="img" aria-label="${t.pv}, ${t.load}, ${t.soc}">
      <defs>
        <clipPath id="nx-past"><rect x="0" y="0" width="${(nowX ?? (now >= t1 ? W : 0)).toFixed(1)}" height="${Hp}"></rect></clipPath>
        <pattern id="nx-shade" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="${P.shade}" fill-opacity=".15"></rect><line x1="0" y1="0" x2="0" y2="5" stroke="${P.shade}" stroke-width="1.6" stroke-opacity=".7"></line></pattern>
      </defs>
      ${svgPv}${lanes}${batSvg}
      ${nowX !== null ? `<line x1="${nowX.toFixed(1)}" y1="0" x2="${nowX.toFixed(1)}" y2="${bottom - 14}" stroke="var(--primary-text-color)" stroke-width="1.2"></line>
        <text x="${nowX.toFixed(1)}" y="${bottom - 3}" text-anchor="middle" class="ax" style="fill:var(--primary-text-color)">${this._hm(now)}</text>` : ""}
      ${ticks.map((h) => `<text x="${x(h).toFixed(1)}" y="${bottom - 3}" text-anchor="middle" class="ax">${this._hm(h).slice(0, 2)}</text>`).join("")}
    </svg>`;

    // planning line and the three numbers
    const after = now >= t1, before = now < t0;
    const ist = dayH.reduce((a, h) => a + (h + H <= now ? exp(h) : h < now ? exp(h) * (now - h) / H : 0), 0);
    const expected = dayH.reduce((a, h) => a + exp(h), 0);
    const morning = dayH.reduce((a, h) => a + (mornM.get(h) || 0), 0);
    const tomorrow = arrays.reduce((a, arr) => a + arr.tomorrow_wh, 0);
    const plan = [];
    if (!after && !before) {
      const free = dayH.filter((h) => h + H > now && load(h) !== null && exp(h) - lv(h) > 300);
      plan.push(free.length ? `${t.freeUntil} ${this._hm(free[free.length - 1] + H)}` : t.noSurplus);
    }
    if (sim && !after && sim.full !== null && sim.full < t1) plan.push(`${t.fullAt} ${this._hm(sim.full)}`);
    if (sim && !after && !this._config.export && sim.unused >= 50) plan.push(`<span class="warn" style="color:${P.warnText};background:${P.warnBg}">${this._kwh(sim.unused)} ${t.kwh} ${t.unused}</span>`);
    if (sim && after) plan.push(sim.empty !== null && sim.empty < chargeTom ? `${t.emptyAt} ${this._hm(sim.empty)}` : `${t.lasts} ${this._hm(chargeTom)}`);
    const dev = morning > 0 ? Math.round((ist / morning - 1) * 100) : null;
    const numbers = after
      ? `<span><span class="big">${this._kwh(ist)}</span> ${t.kwh}</span>
         <span class="muted">${t.morning} ${this._kwh(morning)}${dev === null ? "" : ` <b style="color:var(--primary-text-color);font-weight:500">(${dev > 0 ? "+" : ""}${dev} %)</b>`}</span>`
      : `<span>${t.actual} <span class="big">${this._kwh(ist)}</span></span>
         <span>${t.expected} <b style="font-weight:500">${this._kwh(expected)}</b></span>
         <span class="muted">${t.morning} ${this._kwh(morning)}</span>`;
    el.innerHTML = `${svg}${this._config.legend === false ? "" : this._legend({
        actual: now > t0, loss: lossTotal > 1, load: hasLoad, deficit: hasLoad && dayH.some((h) => lv(h) > exp(h) + 1),
        shade: arrays.some((a) => a.periods.some((p) => p.type === "shade")),
        refl: arrays.some((a) => a.periods.some((p) => p.type === "reflection")),
        bat: !!bat, lost: seenZones.has(P.lost), near: seenZones.has(P.near) })}
      ${plan.length ? `<div class="row">${plan.map((p) => `<span>${p}</span>`).join("")}</div>` : ""}
      <div class="row">${numbers}
        ${lossTotal >= 50 ? `<span style="color:${P.shade}">${t.shade} −${this._kwh(lossTotal)}</span>` : ""}
        <span class="muted">${t.tomorrow} <b style="color:var(--primary-text-color);font-weight:500">${this._kwh(tomorrow)}</b></span>
      </div>`;
  }

  // legend for what the chart shows right now
  _legend(on) {
    const P = this._p, l = this._t.lg;
    const sw = (inner) => `<svg width="16" height="10" aria-hidden="true">${inner}</svg>`;
    const box = (fill, op = 1) => sw(`<rect x="1" y="1" width="14" height="8" rx="2" fill="${fill}" fill-opacity="${op}"></rect>`);
    const dash = (stroke, d) => sw(`<line x1="0" y1="5" x2="16" y2="5" stroke="${stroke}" stroke-width="1.8"${d ? ` stroke-dasharray="3 2"` : ""}></line>`);
    const items = [
      [on.actual, box(P.pv), l.actual], [true, box(P.pv, 0.3), l.expected], [true, dash(P.pvDark, true), l.forecast],
      [on.loss, sw(`<rect x="1" y="1" width="14" height="8" rx="2" fill="url(#nx-shade)"></rect>`), l.loss],
      [on.load, dash(P.home, false), l.load], [on.deficit, box(P.grid, 0.3), l.deficit],
      [on.shade, box(P.shade), l.shade], [on.refl, box(P.refl), l.refl],
      [on.bat, box(P.bat, 0.45), l.bat], [on.lost, box(P.lost, 0.85), l.lost], [on.near, box(P.near, 0.85), l.near],
    ];
    return `<div class="legend">${items.filter(([show]) => show).map(([, icon, text]) => `<span>${icon}${text}</span>`).join("")}</div>`;
  }

  // arrays of today in the configured order (by group name / array), else all
  _arrays() {
    const all = this._data.today.arrays.map((a) => ({ ...a, label: a.name }));
    const groups = this._config.pv.groups || [];
    if (!groups.length) return all;
    const out = [];
    for (const g of groups) {
      const key = (g.array || g.name || "").toLowerCase();
      const a = all.find((x) => x.name.toLowerCase() === key);
      if (a) out.push({ ...a, label: g.name || a.name });
    }
    return out.length ? out : all;
  }

  // a battery limit: a number or the entity holding it, in percent
  _limit(value, fallback) {
    if (typeof value === "number") return value;
    const v = typeof value === "string" ? this._soc(value) : null;
    return v === null ? fallback : v;
  }

  // house load per hour (measured today, otherwise the mean of the last 7 days at that hour) and battery history
  _model(now) {
    const c = this._config, stats = this._data.stats || {}, H = 3600;
    const homeId = c.home && c.home.entity;
    const scale = homeId && this._hass.states[homeId] && (this._hass.states[homeId].attributes.unit_of_measurement || "").toLowerCase() === "kw" ? 1000 : 1;
    const today = this._data.dayStart / 1000;
    const measured = new Map(), peaks = new Map(), byHour = new Map();
    for (const r of stats[homeId] || []) {
      const ts = r.start / 1000;
      if (r.mean === null || r.mean === undefined) continue;
      if (ts >= today) { measured.set(ts, r.mean * scale); if (r.max !== null && r.max !== undefined) peaks.set(ts, r.max * scale); }
      const k = (((ts - today) % 86400) + 86400) % 86400;
      if (ts < today) { const l = byHour.get(k) || []; l.push(r.mean * scale); byHour.set(k, l); }
    }
    const liveHome = this._live().home;
    const load = (h) => {
      if (h + H <= now && measured.has(h)) return measured.get(h);
      if (h <= now && h + H > now && homeId) return liveHome;
      const l = byHour.get((((h - today) % 86400) + 86400) % 86400);
      return l && l.length ? l.reduce((a, b) => a + b, 0) / l.length : null;
    };
    const loadMax = (h) => (peaks.has(h) ? peaks.get(h) : null);

    const bats = (c.batteries || []).filter((b) => Number(b.capacity) > 0);
    const capWh = bats.reduce((a, b) => a + Number(b.capacity) * 1000, 0);
    if (!capWh) return { load, loadMax, battery: null, socPast: [], socAt: () => null };
    const battery = {
      cap: capWh,
      max: bats.reduce((a, b) => a + Number(b.capacity) * 1000 * this._limit(b.max_soc, 100) / 100, 0) / capWh,
      min: bats.reduce((a, b) => a + Number(b.capacity) * 1000 * this._limit(b.min_soc, 0) / 100, 0) / capWh,
      now: this._live().socAll === null ? null : this._live().socAll / 100,
    };
    const socPast = [];
    for (let h = today; h + H <= now; h += H) {
      let s = 0, ok = true;
      for (const b of bats) {
        const r = (stats[b.soc] || []).find((x) => x.start / 1000 === h);
        if (!r || r.mean === null || r.mean === undefined) { ok = false; break; }
        s += r.mean * Number(b.capacity) * 1000 / 100;
      }
      if (ok) socPast.push([h + H / 2, s / capWh]);
    }
    if (battery.now !== null) socPast.push([now, battery.now]);
    const socAt = (ts) => {
      if (!socPast.length) return null;
      let best = socPast[0];
      for (const p of socPast) if (Math.abs(p[0] - ts) < Math.abs(best[0] - ts)) best = p;
      return Math.abs(best[0] - ts) <= 5400 ? best[1] : null;
    };
    return { load, loadMax, battery, socPast, socAt };
  }

  // battery from now until `end`: level points, when it is full or empty, and surplus it cannot take
  _simulate(bat, now, end, pv, load) {
    const H = 3600, points = [];
    let e = (bat.now ?? bat.min) * bat.cap, full = null, empty = null, unused = 0;
    const eMax = bat.max * bat.cap, eMin = bat.min * bat.cap;
    points.push([now, e / bat.cap]);
    for (let h = Math.floor(now / H) * H; h < end; h += H) {
      const from = Math.max(h, now), share = (h + H - from) / H;
      if (share <= 0) continue;
      const net = (pv(h) - (load(h) ?? 0)) * share;
      if (net > 0) {
        const add = Math.min(Math.max(eMax - e, 0), net);
        if (full === null && add < net && e + add >= eMax - 1) full = from + (add / net) * (h + H - from);
        e += add;
        unused += net - add;
      } else if (net < 0) {
        const take = Math.min(Math.max(e - eMin, 0), -net);
        if (empty === null && take < -net) empty = from + (take / -net) * (h + H - from);
        e -= take;
      }
      points.push([h + H, e / bat.cap]);
    }
    const at = (ts) => {
      if (ts <= points[0][0]) return points[0][1];
      for (let i = 1; i < points.length; i++) if (points[i][0] >= ts) {
        const [a, fa] = points[i - 1], [b, fb] = points[i];
        return fa + (fb - fa) * (ts - a) / (b - a || 1);
      }
      return points[points.length - 1][1];
    };
    return { points, full, empty, unused, at };
  }

  _hm(ts) {
    return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", timeZone: this._tz });
  }

  _kwh(wh) {
    return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  _esc(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
}

// Smooth path through points (pixels) without overshoot: monotone cubic (Fritsch-Carlson), as " C..." segments.
function curve(p) {
  const n = p.length, f = (v) => v.toFixed(1);
  if (n < 3) return p.slice(1).map(([a, b]) => ` L${f(a)},${f(b)}`).join("");
  const m = [], t = [];
  for (let k = 0; k < n - 1; k++) m.push((p[k + 1][1] - p[k][1]) / (p[k + 1][0] - p[k][0] || 1e-9));
  t.push(m[0]);
  for (let k = 1; k < n - 1; k++) t.push(m[k - 1] * m[k] <= 0 ? 0 : (m[k - 1] + m[k]) / 2);
  t.push(m[n - 2]);
  for (let k = 0; k < n - 1; k++) {
    if (m[k] === 0) { t[k] = 0; t[k + 1] = 0; continue; }
    const a = t[k] / m[k], b = t[k + 1] / m[k], s = a * a + b * b;
    if (s > 9) { const tau = 3 / Math.sqrt(s); t[k] = tau * a * m[k]; t[k + 1] = tau * b * m[k]; }
  }
  let out = "";
  for (let k = 0; k < n - 1; k++) {
    const dx = (p[k + 1][0] - p[k][0]) / 3;
    out += ` C${f(p[k][0] + dx)},${f(p[k][1] + t[k] * dx)} ${f(p[k + 1][0] - dx)},${f(p[k + 1][1] - t[k + 1] * dx)} ${f(p[k + 1][0])},${f(p[k + 1][1])}`;
  }
  return out;
}

function line(p) {
  return `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}${curve(p)}`;
}

// Visual editor: text size and the main entities; groups and batteries are lists, edited in YAML.
class NoxcastCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) { this._hass = hass; this._render(); }

  _render() {
    if (!this._hass || !this._config) return;
    const de = (this._hass.language || "en").startsWith("de");
    if (!this._form) {
      this.innerHTML = `<p style="margin:0 0 8px;color:var(--secondary-text-color);font-size:13px">${de
        ? "PV-Gruppen und Akkus werden im YAML-Modus des Editors gepflegt."
        : "PV groups and batteries are edited in the editor's YAML mode."}</p>`;
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => {
        const v = ev.detail.value, c = { ...this._config };
        for (const k of ["font_scale", "legend", "export"]) c[k] = v[k];
        c.pv = { ...(this._config.pv || {}), entity: v.pv_entity || undefined };
        c.grid = { ...(this._config.grid || {}), entity: v.grid_entity || undefined, invert: v.grid_invert || undefined };
        c.home = { ...(this._config.home || {}), entity: v.home_entity || undefined };
        if (c.legend !== false) delete c.legend;
        if (!c.export) delete c.export;
        if (c.font_scale === 1) delete c.font_scale;
        this._config = c;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: c }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    const label = {
      font_scale: de ? "Schriftgröße" : "Text size", legend: de ? "Legende anzeigen" : "Show legend",
      export: de ? "Überschuss wird eingespeist (nicht als Verlust werten)" : "Surplus is fed in (not counted as lost)",
      pv_entity: de ? "PV gesamt (optional, sonst Summe der Gruppen)" : "PV total (optional, otherwise the sum of the groups)",
      grid_entity: de ? "Netz (positiv = Bezug)" : "Grid (positive = import)", grid_invert: de ? "Netz-Vorzeichen umkehren" : "Invert grid sign",
      home_entity: de ? "Hausverbrauch" : "House load",
    };
    const sensor = { entity: { domain: "sensor" } };
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "font_scale", selector: { number: { min: 0.8, max: 1.6, step: 0.1, mode: "slider" } } },
      { name: "legend", selector: { boolean: {} } },
      { name: "pv_entity", selector: sensor },
      { name: "grid_entity", selector: sensor },
      { name: "grid_invert", selector: { boolean: {} } },
      { name: "home_entity", selector: sensor },
      { name: "export", selector: { boolean: {} } },
    ];
    const c = this._config;
    this._form.data = {
      font_scale: Number(c.font_scale) || 1, legend: c.legend !== false, export: !!c.export,
      pv_entity: (c.pv && c.pv.entity) || "", grid_entity: (c.grid && c.grid.entity) || "",
      grid_invert: !!(c.grid && c.grid.invert), home_entity: (c.home && c.home.entity) || "",
    };
    this._form.computeLabel = (item) => label[item.name] || item.name;
  }
}

customElements.define("noxcast-card", NoxcastCard);
customElements.define("noxcast-card-editor", NoxcastCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({ type: "noxcast-card", name: "Noxcast", description: "Power flow, today's yield per array, effects and surplus", preview: false });
