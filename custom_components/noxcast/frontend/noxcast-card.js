// Noxcast day card: power flow now, today's yield with the loss to shade, house load,
// battery and unused surplus. Plain custom element, no build step.
//
// type: custom:noxcast-card
// The card shows what Noxcast is configured with: PV groups and their sensors, house load, grid,
// batteries and whether surplus is fed in. The card config only changes the display:
// show_battery: true / show_effects: true / show_band: true / show_load: true   # what the day chart shows
// show_weather: true                   # hourly weather above the chart, marked where it changes
// weather_entity: weather.home         # source of that strip; default: Noxcast's own weather entity
// range: production                    # day chart: production (an hour either side) or day (0–24 h, night compressed)
// flow: full                           # full: power flow diagram; compact: one row of figures
// flow_style: chain                    # compact row: chain (dots between the figures) or bus (a line below them)
// flow_speed: normal                   # pace of the moving dots: calm, normal or lively
// flow_min_w: 10                       # a flow shows from this many watts (sensors drift by a few watts)
// look: plain                          # plain: Home Assistant card; noxcast: the Noxcast panel look
// groups:                              # per Noxcast PV group, by its name in Noxcast
//   Balkon: { name: Süd, icon: mdi:balcony, hide: false }   # icon instead of the name; hide: not listed
//                                      # on its own (its power still counts in PV and the chart)
// battery_names: { Pro: Akku 1 }       # display names of Noxcast batteries
// legend: true                         # legend below the chart, only for what is shown
// font_scale: 1                        # text size, 0.8 to 1.6; all options are also in the visual editor
// icons: { pv: mdi:solar-power-variant, grid: mdi:transmission-tower, battery: auto, home: auto }
//   # Home Assistant icons; "auto" = battery icon by state of charge, house coloured by its sources
// Tapping PV, grid, home or battery opens the entity's more-info dialog.

const TEXT = {
  de: { pv: "PV", home: "Haus", grid: "Netz", battery: "Akku", tomorrow: "morgen", shade: "Schatten",
    unused: "ungenutzt", full: "voll", load: "Hauslast", soc: "Akku-Ladestand", lost: "Schattenverlust",
    noData: "Noch keine Noxcast-Daten", kwh: "kWh", actual: "Ist", expected: "erwartet", morning: "Prognose",
    start: "Start", empty: "leer", freeUntil: "frei bis", noSurplus: "kein Überschuss mehr", fullAt: "Akku voll",
    lasts: "Akku reicht bis morgen", emptyAt: "Akku leer um", import: "Bezug", export: "Einspeisung",
    lg: { actual: "Ist", expected: "Erwartung", forecast: "Prognose", load: "Hauslast", deficit: "Last über PV", band: "Spanne P10–P90",
      loss: "Schattenverlust", bat: "Akku", lost: "voll, Überschuss verfällt",
      near: "fast voll" } },
  en: { pv: "PV", home: "Home", grid: "Grid", battery: "Battery", tomorrow: "tomorrow", shade: "shade",
    unused: "unused", full: "full", load: "House load", soc: "Battery level", lost: "Lost to shade",
    noData: "No Noxcast data yet", kwh: "kWh", actual: "Actual", expected: "expected", morning: "forecast",
    start: "start", empty: "empty", freeUntil: "surplus until", noSurplus: "no more surplus", fullAt: "battery full",
    lasts: "battery lasts until tomorrow", emptyAt: "battery empty at", import: "Import", export: "Export",
    lg: { actual: "actual", expected: "expected", forecast: "forecast", load: "house load", deficit: "load above PV", band: "range P10–P90",
      loss: "lost to shade", bat: "battery", lost: "full, surplus lost",
      near: "almost full" } },
};

const PALETTE = {
  light: { pv: "#F2B233", pvDark: "#C27A06", pvText: "#9A6400", grid: "#2F80C8", home: "#4F5964", bat: "#23874A", night: "#5B6BC0",
    shade: "#C0392B", rail: "rgba(0,0,0,0.08)", unused: "#2F80C8", unusedBg: "rgba(47,128,200,0.12)",
    warnText: "#8E2418", warnBg: "#FBE3DE", lost: "#D93025", near: "#F29900" },
  dark: { pv: "#F2B233", pvDark: "#C58410", pvText: "#F2B233", grid: "#5AA6E8", home: "#B4BCC4", bat: "#4CC77A", night: "#7D8FD6",
    shade: "#F07058", rail: "rgba(255,255,255,0.10)", unused: "#5AA6E8", unusedBg: "rgba(90,166,232,0.15)",
    warnText: "#FFB4A6", warnBg: "#4A2620", lost: "#F2635A", near: "#F5A524" },
};

const ACTIVE_W = 5;
// with range "day" a night hour gets this share of a day hour's width in the day chart
const NIGHT_W = 0.3;
// a flow shows from flow_min_w (default FLOW_MIN_W) and stays until it falls below FLOW_OFF of that: sensors
// drift by a few watts, so small mismatches show no flow, and a flow near the threshold does not flicker
const FLOW_MIN_W = 10, FLOW_OFF = 0.6;
const REFRESH_MS = 5 * 60 * 1000;
const RETRY_MS = [5000, 15000, 30000, 60000];   // after a failed load; Noxcast may still be starting after a restart
const TIMEOUT_MS = 20000;                        // a request lost while the connection was rebuilt never answers

// weather conditions in classes; the strip marks where the class changes
const WX_CLASS = { sunny: "sun", "clear-night": "sun", partlycloudy: "part", cloudy: "cloud", fog: "cloud", windy: "cloud",
  "windy-variant": "cloud", rainy: "rain", pouring: "rain", "lightning-rainy": "rain", lightning: "rain", hail: "rain",
  snowy: "snow", "snowy-rainy": "snow", exceptional: "cloud" };
const WX_ICON = { sunny: "mdi:weather-sunny", "clear-night": "mdi:weather-night", partlycloudy: "mdi:weather-partly-cloudy",
  cloudy: "mdi:weather-cloudy", fog: "mdi:weather-fog", windy: "mdi:weather-windy", "windy-variant": "mdi:weather-windy-variant",
  rainy: "mdi:weather-rainy", pouring: "mdi:weather-pouring", "lightning-rainy": "mdi:weather-lightning-rainy",
  lightning: "mdi:weather-lightning", hail: "mdi:weather-hail", snowy: "mdi:weather-snowy", "snowy-rainy": "mdi:weather-snowy-rainy",
  exceptional: "mdi:alert-circle-outline" };

// a websocket request that gives up instead of waiting forever
function ask(hass, msg) {
  return Promise.race([hass.connection.sendMessagePromise(msg),
    new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), TIMEOUT_MS))]);
}
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

  static getStubConfig() { return {}; }

  // sensors come from the Noxcast configuration; the card config only changes how they are shown
  setConfig(config) {
    this._user = config || {};
    this._config = this._merged();
    if (this._hass) this._renderAll();
  }

  _merged() {
    const u = this._user || {}, s = this._src || {};
    const shown = (u.groups && typeof u.groups === "object" && !Array.isArray(u.groups)) ? u.groups : {};
    const batNames = u.battery_names || {};
    return { ...u,
      pv: { groups: ((s.pv && s.pv.groups) || []).map((g) => {
        const d = shown[g.name] || {};
        return { ...g, label: d.name || g.name, icon: d.icon || "", hide: !!d.hide };
      }) },
      home: s.home || {}, grid: s.grid || {},
      batteries: (s.batteries || []).map((b) => ({ ...b, name: batNames[b.name] || b.name })),
      export: !!s.export };
  }

  async _loadSource() {
    try {
      this._src = await ask(this._hass, { type: "noxcast/card_config" });
      this._srcPending = false;
    } catch (err) {
      // an older integration, or Noxcast not loaded yet: the card config alone, asked again after the next good refresh
      this._src = null;
      this._srcPending = true;
    }
    this._config = this._merged();
    this._flow = null;
    this._renderAll();
    this._subscribeWeather();
  }

  // hourly weather for the strip above the chart. Coming hours: the hourly forecast, kept current by
  // Home Assistant. It starts at the current hour, so past hours come from the recorded states of the
  // weather entity (the condition as reported at the middle of each hour); same on every device.
  async _subscribeWeather() {
    const id = this._config.weather_entity || (this._src && this._src.weather);
    if (!this._show("weather") || !id || !this._hass || !this.isConnected || this._wxFor === id) return;
    this._unsubWeather();
    this._wxFor = id;
    this._wxPast = [];
    this._wxForecast = [];
    this._loadWeatherHistory(id);
    try {
      this._wxUnsub = await this._hass.connection.subscribeMessage((msg) => {
        this._wxForecast = msg.forecast || [];
        if (Date.now() - (this._wxPastAt || 0) > 30 * 60 * 1000) this._loadWeatherHistory(id);
        this._mergeWeather();
      }, { type: "weather/subscribe_forecast", forecast_type: "hourly", entity_id: id });
    } catch (err) {
      this._wxFor = null;                       // no hourly forecast: no strip
    }
  }

  async _loadWeatherHistory(id) {
    this._wxPastAt = Date.now();
    const start = this._localMidnight(new Date());
    try {
      const res = await ask(this._hass, { type: "history/history_during_period", start_time: new Date(start).toISOString(),
        entity_ids: [id], minimal_response: false, no_attributes: false, significant_changes_only: false });
      const states = ((res && res[id]) || []).map((r) => ({ ts: (r.lu ?? r.lc) * 1000, cond: r.s, temp: r.a && r.a.temperature }))
        .filter((r) => r.cond && r.cond !== "unavailable" && r.cond !== "unknown").sort((a, b) => a.ts - b.ts);
      const past = [];
      for (let h = start; h + 3600000 <= Date.now(); h += 3600000) {
        const mid = h + 1800000;
        let cur = null;
        for (const r of states) { if (r.ts <= mid) cur = r; else break; }
        if (cur) past.push({ datetime: new Date(h).toISOString(), condition: cur.cond, temperature: cur.temp });
      }
      if (this._wxFor !== id) return;
      this._wxPast = past;
      this._mergeWeather();
    } catch (err) {
      this._wxPast = [];                        // no recorder history: coming hours only
    }
  }

  _mergeWeather() {
    const hours = new Map();
    for (const f of this._wxPast || []) hours.set(Date.parse(f.datetime), f);
    for (const f of this._wxForecast || []) if (!hours.has(Date.parse(f.datetime))) hours.set(Date.parse(f.datetime), f);
    this._wx = [...hours.entries()].sort((a, b) => a[0] - b[0]).map(([, f]) => f);
    this._renderBand();
  }

  _unsubWeather() {
    if (this._wxUnsub) { try { this._wxUnsub(); } catch (err) { /* connection already gone */ } }
    this._wxUnsub = null;
    this._wxFor = null;
  }

  _show(part) { return this._config[`show_${part}`] !== false; }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (this.isConnected) this._listenReady();
    if (first) { this._renderAll(); this._loadSource(); this._refresh(); return; }
    const sig = this._signature();
    if (sig !== this._sig) { this._sig = sig; this._renderFlow(); }
    if (this._stale()) this._refresh();
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._refresh(), REFRESH_MS);
    this._listenReady();
    if (!this._onVisible) {
      this._onVisible = () => { if (document.visibilityState === "visible" && this._stale(60)) this._refresh(); };
      document.addEventListener("visibilitychange", this._onVisible);
    }
    if (this._hass && this._stale(60)) this._refresh();
    if (this._flow) this._animate();
    if (this._hass && this._config) this._subscribeWeather();
  }

  disconnectedCallback() {
    cancelAnimationFrame(this._frame);
    this._frame = null;
    clearInterval(this._timer);
    clearTimeout(this._midnight);
    this._timer = null;
    if (this._onVisible) document.removeEventListener("visibilitychange", this._onVisible);
    this._onVisible = null;
    clearTimeout(this._retry);
    this._unsubWeather();
    if (this._readyConn) this._readyConn.removeEventListener("ready", this._onReady);
    this._readyConn = null;
  }

  // after a reconnect (Home Assistant restarted, the app came back) load again instead of keeping an error
  _listenReady() {
    const conn = this._hass && this._hass.connection;
    if (!conn || this._readyConn === conn) return;
    if (this._readyConn) this._readyConn.removeEventListener("ready", this._onReady);
    this._onReady = this._onReady || (() => { this._tries = 0; this._refresh(); this._unsubWeather(); this._subscribeWeather(); });
    conn.addEventListener("ready", this._onReady);
    this._readyConn = conn;
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
  // whether the flow `key` with `w` watts counts as running, with hysteresis (see FLOW_MIN_W)
  _on(key, w) {
    const st = this._onState || (this._onState = {});
    const min = Number(this._config.flow_min_w) >= 0 && this._config.flow_min_w !== "" ? Number(this._config.flow_min_w) : FLOW_MIN_W;
    st[key] = Math.abs(w) >= Math.max(1, st[key] ? min * FLOW_OFF : min);
    return st[key];
  }

  // pace of the moving dots: a factor on their travel times (calm = slower)
  get _pace() { return { calm: 1.8, lively: 0.55 }[this._config.flow_speed] || 1; }
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

  // a PV group: one sensor or the sum of several (the sensors of a Noxcast array)
  _groupW(g) {
    const ids = g.entities || [g.entity];
    return ids.reduce((a, id) => a + Math.max(0, this._watts(id, g.invert) || 0), 0);
  }

  _soc(entityId) {
    const s = entityId && this._hass.states[entityId];
    const v = s ? parseFloat(s.state) : NaN;
    return isFinite(v) ? v : null;
  }

  _live() {
    const c = this._config;
    const groups = (c.pv.groups || []).map((g) => ({ name: g.label || g.name, icon: g.icon, hide: g.hide,
      id: g.entity || (g.entities || [])[0], w: this._groupW(g) }));
    const pv = groups.reduce((a, g) => a + g.w, 0);
    const bats = (c.batteries || []).map((b) => ({ name: b.name || b.entity, id: b.soc || b.entity, w: this._watts(b.entity, b.invert) || 0,
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
    const c = this._config, ids = [...(c.pv.groups || []).flatMap((g) => g.entities || [g.entity]), c.grid && c.grid.entity,
      c.home && c.home.entity, ...(c.batteries || []).flatMap((b) => [b.entity, b.soc])].filter(Boolean);
    return ids.map((i) => this._hass.states[i] && this._hass.states[i].state).join("|") + (this._hass.themes && this._hass.themes.darkMode);
  }

  // ------------------------------------------------------------ data for the day band

  async _refresh() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      const today = await ask(this._hass, { type: "noxcast/today" });
      const stats = await this._history();
      this._data = { today, ...stats, loaded: Date.now() };
      this._error = null;
      this._tries = 0;
      if (this._srcPending) this._loadSource();
    } catch (err) {
      this._error = String((err && err.message) || err);
      clearTimeout(this._retry);
      if (this.isConnected) {
        this._retry = setTimeout(() => this._refresh(), RETRY_MS[Math.min(this._tries || 0, RETRY_MS.length - 1)]);
        this._tries = (this._tries || 0) + 1;
      }
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
    const res = await ask(this._hass, {
      type: "recorder/statistics_during_period", start_time: new Date(dayStart - 7 * 86400000).toISOString(),
      end_time: now.toISOString(), statistic_ids: ids, period: "hour", types: ["mean"],
    });
    return { stats: res, dayStart };
  }

  _localMidnight(d) {
    const fmt = new Intl.DateTimeFormat("en-US", { timeZone: this._tz, hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const partsAt = (ms) => Object.fromEntries(fmt.formatToParts(new Date(ms)).map((p) => [p.type, p.value]));
    const offsetAt = (ms) => {                                          // the parts carry no milliseconds
      const p = partsAt(ms);
      return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(ms / 1000) * 1000;
    };
    const p = partsAt(d.getTime()), day = Date.UTC(+p.year, +p.month - 1, +p.day);
    // on the day the clocks change, the offset at midnight differs from the one now
    return day - offsetAt(day - offsetAt(d.getTime()));
  }

  // ------------------------------------------------------------ rendering

  _renderAll() {
    const P = this._p;
    this.shadowRoot.innerHTML = `<style>
      ha-card { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
      ha-card.nx { border-radius: 20px; border: var(--nx-edge-width, 2px) solid transparent;   /* edge width: set --nx-edge-width in a theme */
        background: linear-gradient(var(--ha-card-background, var(--card-background-color, #fff)), var(--ha-card-background, var(--card-background-color, #fff))) padding-box,
          linear-gradient(135deg, rgba(245,165,36,.55), rgba(127,127,127,.14) 28%, rgba(127,127,127,.14) 72%, rgba(139,92,246,.55)) border-box;
        box-shadow: 0 18px 40px -24px rgba(40,24,80,.35); }
      .cf { display: grid; align-items: end; container-type: inline-size; }
      .cf .f { display: flex; flex-direction: column; align-items: center; cursor: pointer; min-width: 0; padding: 0 8px; }
      .cf .f + .f { border-left: 1px solid var(--divider-color); }
      .cf .l { font-size: 11px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; color: var(--secondary-text-color); }
      .cf .v { font-size: calc(22px * var(--nx-scale, 1)); font-weight: 500; white-space: nowrap; font-variant-numeric: tabular-nums; }
      .cf .v { font-size: min(calc(22px * var(--nx-scale, 1)), 5.5cqw); }
      .cf .v small { font-size: .6em; color: var(--secondary-text-color); font-weight: 400; }
      .cf .v { line-height: 1.2; }
      /* chain: the row aligns on the baseline of the values; a gap has no baseline of its own, so its bottom
         edge sits on it. It is one cap height tall (digits are cap height), so its middle is the middle of
         the digits, whatever the font, size or line height. 0.72em: the cap height of Roboto, where cap is unknown */
      .cf.chain { padding: 0 4px; align-items: last baseline; }
      .cf .gap { display: block; width: 100%; height: .72em; height: 1cap; overflow: visible; }
      .cf.chain .f { padding: 0 10px; }   /* room between a value and the lines beside it */
      .bus { display: block; width: 100%; height: 22px; margin-top: -4px; overflow: visible; }
      .wx { margin-bottom: -2px; }
      .muted { color: var(--secondary-text-color); }
      .row { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; }
      #band { container-type: inline-size; }
      .nums { flex-wrap: nowrap; gap: 4px 8px; font-size: clamp(10px, 3.2cqw, 13px); white-space: nowrap; }
      .nums .big { font-size: clamp(13px, 4cqw, 16px); }
      .big { font-size: 16px; font-weight: 500; }
      .warn { font-size: 12px; font-weight: 500; padding: 2px 6px; border-radius: 4px; }
      svg { display: block; width: 100%; height: auto; overflow: visible; }
      .legend { display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 11px; color: var(--secondary-text-color); }
      .legend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
      .legend svg { width: 16px; height: 10px; display: inline-block; }
      .ax { font-size: calc(9px * min(var(--nx-scale, 1), 1.2)); fill: var(--secondary-text-color); font-family: var(--code-font-family, monospace); }
      .divider { border-top: 1px solid var(--divider-color); padding-top: 8px; }
      .chart { position: relative; touch-action: pan-y; }
      .tip { position: absolute; top: 2px; display: grid; grid-template-columns: auto auto; gap: 1px 10px; padding: 6px 8px;
        font-size: 11px; line-height: 1.35; background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,.15); pointer-events: none; z-index: 1; }
      .tip[hidden] { display: none; }
      .tip b { grid-column: 1 / -1; font-weight: 500; }
      .tip span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
      .tip i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
      .tip .v { justify-content: flex-end; font-variant-numeric: tabular-nums; }
    </style>
    <ha-card class="${this._config.look === "noxcast" ? "nx" : ""}" style="--nx-scale:${this._scale}"><div id="flow"></div><div id="band" class="divider"></div></ha-card>`;
    this._sig = this._signature();
    this._renderFlow();
    this._renderBand();
  }

  // a Home Assistant icon centred at (cx, cy); null when ha-icon is not available (the built-in symbol is used)
  _icon(name, cx, cy, size, color) {
    if (!name || !customElements.get("ha-icon")) return null;
    return `<foreignObject x="${cx - size / 2}" y="${(cy - size / 2).toFixed(1)}" width="${size}" height="${size}">
      <ha-icon icon="${this._esc(name)}" style="--mdc-icon-size:${size}px;color:${color};display:block;line-height:0"></ha-icon></foreignObject>`;
  }

  _batteryIcon(soc, charging) {
    if (soc === null) return "mdi:battery-unknown";
    const step = Math.round(Math.min(100, Math.max(0, soc)) / 10) * 10;
    if (charging) return step >= 100 ? "mdi:battery-charging-100" : step <= 0 ? "mdi:battery-charging-outline" : `mdi:battery-charging-${step}`;
    return step >= 100 ? "mdi:battery" : step <= 0 ? "mdi:battery-outline" : `mdi:battery-${step}`;
  }

  // more-info of the entity behind a tapped flow element
  _moreInfo(ev) {
    const g = ev.target.closest && ev.target.closest("[data-more]");
    if (!g || !g.dataset.more) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: g.dataset.more }, bubbles: true, composed: true }));
  }

  // The flow is built once; later updates only change colours, texts and speeds, so the dots never jump.
  _renderFlow() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("flow");
    if (!el) return;
    const P = this._p, t = this._t, v = this._live();
    if (this._config.flow === "compact") { this._renderCompact(el, v); return; }
    // allocation of the flows, all values >= 0
    const charge = Math.max(v.bat, 0), discharge = Math.max(-v.bat, 0);
    const pvBat = Math.min(v.pv, charge);                    // a charging battery takes PV first, the grid only covers the rest
    const pvHome = Math.min(v.pv - pvBat, v.home);
    // sensors rarely add up exactly: flows to and from the grid never exceed what the grid sensor shows
    const gridKnown = !!(this._config.grid && this._config.grid.entity);
    const imp = gridKnown ? Math.max(v.grid, 0) : Infinity, exp = gridKnown ? Math.max(-v.grid, 0) : Infinity;
    const pvGrid = Math.min(Math.max(v.pv - pvHome - pvBat, 0), exp);
    const batHome = Math.max(Math.min(discharge, v.home - pvHome), 0);
    const gridHome = Math.min(Math.max(v.home - pvHome - batHome, 0), imp);
    const gridBat = Math.min(Math.max(charge - pvBat, 0), imp - gridHome);
    const batGrid = Math.min(Math.max(discharge - batHome, 0), exp - pvGrid);
    const flows = {
      pvHome: [pvHome, P.pv, 1], pvBat: [pvBat, P.pv, 1], pvGrid: [pvGrid, P.pv, 1], gridHome: [gridHome, P.grid, 1],
      batHome: [batHome, P.bat, 1], gridBat: [Math.max(gridBat, batGrid), gridBat >= batGrid ? P.grid : P.bat, batGrid > gridBat ? -1 : 1],
    };
    const sc = this._scale * 1.3,      // the live numbers are the main reading, larger than the rows below
          fs = (n) => (n * sc).toFixed(1);
    const lift = (sc - 1) * 18;                  // larger text: PV symbol and groups move up
    const top = 10 - lift;                       // groups sit beside the PV symbol, so nothing is above it
    const subY = (i) => 122 + 16 * sc + i * 14 * sc, homeY = 176 + 16 * sc;
    const height = Math.ceil(Math.max(homeY + 4, v.bats.length > 1 ? subY(Math.min(v.bats.length, 3) - 1) + 4 : 0, 186));
    if (!this._flow || this._flow.height !== height - top || !el.contains(this._flow.svg)) {
      el.innerHTML = `<svg viewBox="0 ${top.toFixed(1)} 408 ${(height - top).toFixed(1)}" role="img">
        ${Object.entries(FLOW_PATHS).map(([k, d]) => `<path data-k="${k}" d="${d}" fill="none" stroke-linecap="round" stroke-linejoin="round"></path>`).join("")}
        ${Object.keys(FLOW_PATHS).map((k) => `<circle data-dot="${k}" r="4.5" stroke-width="2" visibility="hidden"></circle>`).join("")}
        <g data-labels></g></svg>`;
      const svg = el.querySelector("svg");
      svg.addEventListener("click", (ev) => this._moreInfo(ev));
      const lines = {}, dots = {};
      for (const k of Object.keys(FLOW_PATHS)) {
        const path = svg.querySelector(`[data-k="${k}"]`);
        lines[k] = { path, len: path.getTotalLength ? path.getTotalLength() : 0, pos: 0, speed: 0, dir: 1 };
        dots[k] = svg.querySelector(`[data-dot="${k}"]`);
      }
      this._flow = { svg, lines, dots, height: height - top, labels: svg.querySelector("[data-labels]") };
    }
    const f = this._flow;
    f.svg.setAttribute("aria-label", `${t.pv} ${Math.round(v.pv)} W, ${t.home} ${Math.round(v.home)} W, ${t.grid} ${Math.round(v.grid)} W, ${t.battery} ${Math.round(v.bat)} W`);
    for (const [k, [p, color, dir]] of Object.entries(flows)) {
      const line = f.lines[k], dot = f.dots[k], on = this._on(k, p);
      line.path.setAttribute("stroke", on ? color : P.rail);
      line.path.setAttribute("stroke-width", on ? "3.5" : "2");
      line.path.setAttribute("stroke-opacity", on ? "0.85" : "1");
      line.speed = on ? Math.min(140, 18 + p / 8) / this._pace : 0;   // px per second: the speed carries the power
      line.dir = dir;
      dot.setAttribute("fill", "var(--card-background-color, #fff)");
      dot.setAttribute("stroke", color);
      dot.setAttribute("visibility", on && line.len ? "visible" : "hidden");
    }
    // the house shows where its power comes from: hard colour bands left to right, PV, battery, grid, by share
    const srcs = [[pvHome, P.pv, "pvHome"], [batHome, P.bat, "batHome"], [gridHome, P.grid, "gridHome"]].filter(([w, , k]) => this._on(k, w));
    const srcSum = srcs.reduce((a, [w]) => a + w, 0);
    let homeGrad = "", homeStroke = P.home;
    if (srcs.length === 1) homeStroke = srcs[0][1];
    else if (srcs.length > 1) {
      let at = 0;
      const stops = srcs.map(([w, col]) => { const a = at; at += w / srcSum;
        return `<stop offset="${a.toFixed(3)}" stop-color="${col}"></stop><stop offset="${at.toFixed(3)}" stop-color="${col}"></stop>`; }).join("");
      homeGrad = `<defs><linearGradient id="nx-home" gradientUnits="userSpaceOnUse" x1="190" y1="0" x2="218" y2="0">${stops}</linearGradient></defs>`;
      homeStroke = "url(#nx-home)";
    }
    // from 1000 W on in kW with one decimal, so long numbers do not crowd the flow
    const num = (x) => (Math.abs(x) >= 1000
      ? `${(x / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} k`
      : `${Math.round(x)} `);
    const fmtW = (x) => `${num(x)}W`;
    const small = (x) => (Math.abs(x) >= 1000 ? fmtW(x) : `${Math.round(x)}`);   // group and battery rows
    const op = (x) => (Math.abs(x) < ACTIVE_W ? 0.35 : 1);
    // groups left and right of the PV symbol: first half left, the rest right; each opens its own entity.
    // HTML rows inside the SVG, so an icon can stand in for the name
    const iconOk = !!customElements.get("ha-icon");
    const gText = v.groups.filter((g) => !g.hide).map((g) => `<span data-more="${this._esc(g.id)}" title="${this._esc(g.name)}" style="cursor:pointer">${
      g.icon && iconOk ? `<ha-icon icon="${this._esc(g.icon)}" style="--mdc-icon-size:1.25em;vertical-align:-0.25em"></ha-icon>` : this._esc(g.name)} ${small(g.w)}</span>`);
    // small triangles instead of signs, pointing the way the power flows on screen (grid left, battery right);
    // smaller than the number, so raised to its middle and the baseline restored after it (U+FE0E: no emoji)
    const tri = (dir, size, text) => {
      const up = (0.36 * (text - size) * sc).toFixed(1);
      return `<tspan font-size="${fs(size)}" dy="-${up}">${dir > 0 ? "▶" : "◀"}\uFE0E</tspan><tspan dy="${up}"> </tspan>`;
    };
    const batArrow = (w, key, size = 9, text = 11) => (!this._on(key, w) ? "" : w > 0 ? tri(1, size, text) : tri(-1, size, text));
    const half = Math.ceil(gText.length / 2);
    const gH = 18 * sc, gTop = (27 - lift - 0.68 * gH).toFixed(1);
    const gRow = (x, align, items) => `<foreignObject x="${x}" y="${gTop}" width="186" height="${gH.toFixed(1)}"><div xmlns="http://www.w3.org/1999/xhtml"
        style="display:flex;justify-content:${align};align-items:center;height:100%;gap:0 .3em;font-size:${fs(11)}px;color:${P.pvText};white-space:nowrap;line-height:1">${items.join("<span>·</span>")}</div></foreignObject>`;
    const groups = gText.length ? `${gRow(0, "flex-end", gText.slice(0, half))}${gRow(222, "flex-start", gText.slice(half))}` : "";
    const bats = v.bats.slice(0, 3).map((b, i) => `<text data-more="${this._esc(b.id)}" style="cursor:pointer" x="402" y="${subY(i).toFixed(1)}" text-anchor="end" font-size="${fs(11)}" fill="currentColor">${this._esc(b.name)} ${batArrow(b.w, `bat:${b.name}`)}${small(Math.abs(b.w))} · ${b.soc === null ? "–" : Math.round(b.soc)} %</text>`).join("");
    const gridLabel = !this._on("n:grid", v.grid) ? fmtW(Math.abs(v.grid)) : v.grid > 0 ? `${tri(1, 11, 15)}${fmtW(v.grid)}` : `${tri(-1, 11, 15)}${fmtW(-v.grid)}`;
    const c = this._config, icons = c.icons || {};
    const more = (id) => (id ? ` data-more="${this._esc(id)}" style="cursor:pointer"` : "");
    const g0 = (c.pv.groups || [])[0] || {}, pvId = g0.entity || (g0.entities || [])[0];
    const batId = (v.bats[0] || {}).id;
    const pvIcon = this._icon(icons.pv || "mdi:solar-power-variant", 204, 22 - lift, 24, P.pv);
    const gridIcon = this._icon(icons.grid || "mdi:transmission-tower", 28, 84, 26, P.grid);
    const batName = !icons.battery || icons.battery === "auto" ? this._batteryIcon(v.socAll, v.bat > 0 && this._on("n:bat", v.bat)) : icons.battery;
    const batIcon = this._icon(batName, 374, 85, 30, P.bat);
    const homeIcon = icons.home && icons.home !== "auto"
      ? this._icon(icons.home, 204, 161, 28, srcs.length ? srcs.reduce((a, b) => (b[0] > a[0] ? b : a))[1] : P.home) : null;
    f.labels.innerHTML = `
      <g opacity="${op(v.pv)}"${more(pvId)}>
        ${pvIcon || `<path transform="translate(0 ${(-lift).toFixed(1)})" d="M196 22l8-8l8 8M204 14v18M193 30h22" fill="none" stroke="${P.pv}" stroke-width="2" stroke-linecap="round"></path>`}
        <text x="204" y="54" text-anchor="middle" font-size="${fs(17)}" font-weight="500" fill="${P.pvText}">${fmtW(v.pv)}</text>
        ${groups}
      </g>
      <g opacity="${op(v.grid)}"${more(c.grid && c.grid.entity)}>
        ${gridIcon || `<path d="M22 96l6-22l6 22M25 86h6M20 96h16" fill="none" stroke="${P.grid}" stroke-width="2" stroke-linecap="round"></path>`}
        <text x="6" y="122" font-size="${fs(15)}" font-weight="500" fill="${P.grid}">${gridLabel}</text>
      </g>
      <g opacity="${op(v.bat)}"${more(batId)}>
        ${batIcon || `<rect x="364" y="74" width="20" height="26" rx="3" fill="none" stroke="${P.bat}" stroke-width="2"></rect>
        <rect x="369" y="70" width="10" height="4" rx="1" fill="${P.bat}"></rect>
        <rect x="367" y="${97 - 20 * (v.socAll ?? 0) / 100}" width="14" height="${20 * (v.socAll ?? 0) / 100}" rx="1" fill="${P.bat}"></rect>`}
        <text x="402" y="122" text-anchor="end" font-size="${fs(15)}" font-weight="500" fill="${P.bat}">${batArrow(v.bat, "n:bat", 11, 15)}${fmtW(Math.abs(v.bat))}${v.socAll === null ? "" : ` · ${Math.round(v.socAll)} %`}</text>
      </g>
      ${v.bats.length > 1 ? `<g color="var(--secondary-text-color)">${bats}</g>` : ""}
      <g${more(c.home && c.home.entity)}>
      ${homeIcon || `${homeGrad}
      <path d="M190 160l14-12l14 12M193 158v16h22v-16" fill="none" stroke="${homeStroke}" stroke-width="2.5" stroke-linejoin="round"></path>`}
      <text x="204" y="${homeY.toFixed(1)}" text-anchor="middle" font-size="${fs(15)}" font-weight="500" fill="var(--primary-text-color)">${fmtW(v.home)}</text></g>`;
    this._animate();
  }

  // one row of figures instead of the flow diagram, in the order PV, battery, home, grid: then the power
  // between two neighbours is the net flow across that border. flow_style chain shows it as dots in the gaps,
  // bus as dots on a line below the figures with a stub to each. Built once per shape, so the dots never jump;
  // each figure opens its entity
  _renderCompact(el, v) {
    const P = this._p, t = this._t, c = this._config, bus = c.flow_style === "bus";
    const num = (x) => (Math.abs(x) >= 1000
      ? `${(x / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} kW`
      : `${Math.round(x)} W`);
    // prod > 0: the figure supplies power, < 0: it takes power
    const nodes = [{ key: "pv", label: t.pv, color: P.pvText, dot: P.pv, id: (v.groups.find((g) => g.id) || {}).id, prod: v.pv }];
    if (v.bats.length) nodes.push({ key: "bat", label: t.battery, color: P.bat, dot: P.bat, id: (v.bats.find((b) => b.id) || {}).id, prod: -v.bat });
    nodes.push({ key: "home", label: t.home, color: "var(--primary-text-color)", dot: P.home, id: c.home && c.home.entity, prod: -v.home });
    if (c.grid && c.grid.entity) nodes.push({ key: "grid", label: t.grid, color: P.grid, dot: P.grid, id: c.grid.entity, prod: v.grid });
    const shape = `${bus ? "bus" : "chain"}|${nodes.map((n) => n.key).join()}`;
    if (!this._flow || this._flow.shape !== shape || !el.contains(this._flow.root)) this._buildCompact(el, nodes, bus, shape);
    const f = this._flow;

    // the arrows only on the bus, which leaves room for them; in the chain the dots show the direction
    const arrow = (w, key) => (!bus || !this._on(key, w) ? "" : w > 0 ? "▲ " : "▼ ");
    const text = {
      pv: num(v.pv), home: num(v.home), grid: `${arrow(v.grid, "n:grid")}${num(Math.abs(v.grid))}`,
      bat: `${v.socAll === null ? "–" : `${Math.round(v.socAll)} %`} <small>${arrow(v.bat, "n:bat")}${num(Math.abs(v.bat))}</small>`,
    };
    for (const n of nodes) {
      f.values[n.key].innerHTML = text[n.key];
      f.values[n.key].style.color = n.color;
    }
    const gridDir = !this._on("n:grid", v.grid) ? "" : v.grid > 0 ? t.import : t.export;
    if (f.cells.grid) f.cells.grid.title = gridDir;

    // net flow across each border: what the left side gives and the right side takes. Sensors rarely add up
    // exactly, so only what both sides confirm counts (the smaller amount, none if they disagree on the
    // direction); made of the supplying figures on the sending side
    const borders = f.segs.map((seg, i) => {
      const give = nodes.slice(0, i + 1).reduce((a, n) => a + n.prod, 0), take = -nodes.slice(i + 1).reduce((a, n) => a + n.prod, 0);
      const p = give * take > 0 ? Math.sign(give) * Math.min(Math.abs(give), Math.abs(take)) : 0, side = p >= 0 ? nodes.slice(0, i + 1) : nodes.slice(i + 1);
      const srcs = side.filter((n) => n.prod > 0 && this._on(`n:${n.key}`, n.prod)).map((n) => [n.dot, n.prod]);
      return { p, mix: srcs.length ? srcs : [[side[0].dot, 1]] };
    });
    const topP = Math.max(...borders.map((b) => Math.abs(b.p)), 1);
    borders.forEach(({ p, mix }, i) => {
      const seg = f.segs[i], on = this._on(`seg:${i}`, p);
      const sum = mix.reduce((a, [, w]) => a + w, 0), main = mix.reduce((a, m) => (m[1] > a[1] ? m : a))[0];
      // dots by the share of the largest flow of the moment, at least one per source (the short chain gaps hold
      // three, the bus four); pace by the power itself
      seg.n = on ? Math.min(seg.max, Math.max(1 + Math.round(((seg.max - 1) * Math.abs(p)) / topP), mix.filter(([, w]) => w / sum > 0.1).length)) : 0;
      seg.dir = p >= 0 ? 1 : -1;
      seg.dur = Math.max(0.7, 2.8 - 0.75 * Math.log10(Math.max(Math.abs(p), 10) / 10)) * this._pace;
      seg.line.setAttribute("stroke", on ? main : P.rail);
      seg.line.setAttribute("stroke-opacity", on ? "0.35" : "1");
      // colours in proportion to the sources, interleaved: PV, battery, PV, ...
      const left = mix.map(([, w]) => (w / sum) * seg.n), colors = [];
      while (colors.length < seg.n) {
        let k = 0;
        for (let j = 1; j < left.length; j++) if (left[j] > left[k]) k = j;
        colors.push(mix[k][0]);
        left[k] -= 1;
      }
      seg.dots.forEach((d, j) => { d.setAttribute("fill", colors[j] || main); d.setAttribute("visibility", j < seg.n ? "visible" : "hidden"); });
    });
    if (bus) {
      for (const n of nodes) {
        const on = this._on(`n:${n.key}`, n.prod);
        f.stubs[n.key].setAttribute("stroke", on ? n.dot : P.rail);
        f.stubs[n.key].setAttribute("stroke-opacity", on ? "0.8" : "1");
        f.knots[n.key].setAttribute("fill", on ? n.dot : P.rail);
      }
    }
    this._animate();
  }

  _buildCompact(el, nodes, bus, shape) {
    const N = nodes.length, mid = (i) => ((i + 0.5) / N) * 100;
    const cell = (n) => `<div class="f" data-cell="${n.key}"${n.id ? ` data-more="${this._esc(n.id)}"` : ""}><span class="l">${n.label}</span>
      <span class="v" data-v="${n.key}"></span></div>`;
    const dots = (y) => [0, 1, 2, 3].map(() => `<circle cy="${y}" r="2.8" visibility="hidden"></circle>`).join("");
    let html;
    if (!bus) {
      // the figures take their width, the gaps share the rest, so a line runs from one value to the next;
      // it sits in the middle of the digits (see .cf .gap)
      const cols = nodes.map(() => "auto").join(" minmax(22px, 1fr) ");
      html = `<div class="cf chain" style="grid-template-columns:${cols}">${nodes.map((n, i) => `${cell(n)}${i < N - 1
        ? `<svg class="gap v" data-seg="${i}" data-x0="8" data-x1="92"><line x1="4%" x2="96%" y1="50%" y2="50%" stroke-width="2" stroke-linecap="round"></line>${dots("50%")}</svg>`
        : ""}`).join("")}</div>`;
    } else {
      const segs = nodes.slice(1).map((_, i) => `<g data-seg="${i}" data-x0="${mid(i) + 3}" data-x1="${mid(i + 1) - 3}"><line x1="${mid(i) + 2}%" x2="${mid(i + 1) - 2}%" y1="16" y2="16" stroke-width="2"></line>${dots(16)}</g>`).join("");
      const stubs = nodes.map((n, i) => `<line data-stub="${n.key}" x1="${mid(i)}%" x2="${mid(i)}%" y1="1" y2="16" stroke-width="2"></line>
        <circle data-knot="${n.key}" cx="${mid(i)}%" cy="16" r="3.5"></circle>`).join("");
      html = `<div class="cf" style="grid-template-columns:repeat(${N}, minmax(0, 1fr))">${nodes.map(cell).join("")}</div>
        <svg class="bus" aria-hidden="true"><line x1="${mid(0)}%" x2="${mid(N - 1)}%" y1="16" y2="16" stroke="${this._p.rail}" stroke-width="2" stroke-linecap="round"></line>${segs}${stubs}</svg>`;
    }
    el.innerHTML = html;
    el.onclick = (ev) => this._moreInfo(ev);
    const q = (sel) => [...el.querySelectorAll(sel)];
    const segs = q("[data-seg]").map((g) => ({ line: g.querySelector("line"), dots: [...g.querySelectorAll("circle")],
      x0: Number(g.dataset.x0), x1: Number(g.dataset.x1), max: bus ? 4 : 3, n: 0, dir: 1, dur: 1, phase: 0 }));
    this._flow = {
      shape, root: el.firstElementChild, segs,
      values: Object.fromEntries(q("[data-v]").map((e) => [e.dataset.v, e])),
      cells: Object.fromEntries(q("[data-cell]").map((e) => [e.dataset.cell, e])),
      stubs: Object.fromEntries(q("[data-stub]").map((e) => [e.dataset.stub, e])),
      knots: Object.fromEntries(q("[data-knot]").map((e) => [e.dataset.knot, e])),
      // one step of the dots: each run of n dots moves as one phase, fading in and out at the ends
      tick: (dt, reduced) => {
        let moving = false;
        for (const seg of segs) {
          if (!seg.n) continue;
          moving = true;
          if (!reduced) seg.phase = (seg.phase + dt / seg.dur) % 1;
          seg.dots.forEach((d, j) => {
            if (j >= seg.n) return;
            const f = reduced ? (j + 0.5) / seg.n : (seg.phase + j / seg.n) % 1, pos = seg.dir > 0 ? f : 1 - f;
            d.setAttribute("cx", `${(seg.x0 + (seg.x1 - seg.x0) * pos).toFixed(2)}%`);
            d.setAttribute("opacity", reduced ? "1" : Math.min(1, f / 0.2, (1 - f) / 0.2).toFixed(2));
          });
        }
        return moving;
      },
    };
  }

  // hourly weather over [t0, t1): one mark where the condition class changes, runs under two hours merged
  _weatherStrip(t0, t1, x, X0, X1, W) {
    if (!this._show("weather") || !this._wx || !this._wx.length) return "";
    const P = this._p, H = 3600;
    const hours = this._wx.map((f) => ({ ts: Date.parse(f.datetime) / 1000, cond: f.condition, temp: f.temperature }))
      .filter((f) => f.ts >= t0 - H && f.ts < t1 && f.cond);
    if (!hours.length) return "";
    const runs = [];
    for (const f of hours) {
      const cls = WX_CLASS[f.cond] || "cloud", last = runs[runs.length - 1];
      if (last && last.cls === cls) last.end = f.ts + H;
      else runs.push({ cls, cond: f.cond, temp: f.temp, start: f.ts, end: f.ts + H });
    }
    for (let i = runs.length - 1; i > 0; i--) {
      if (runs[i].end - runs[i].start < 2 * H) { runs[i - 1].end = runs[i].end; runs.splice(i, 1); }
    }
    const color = { sun: P.pv, part: P.pvDark, cloud: "var(--secondary-text-color)", rain: P.grid, snow: P.grid };
    let lastX = -99;
    const marks = runs.map((r) => {
      const a = Math.max(X0, x(r.start)), b = Math.min(X1, x(r.end));
      if (b - a < 1) return "";
      const bar = `<line x1="${a.toFixed(1)}" x2="${b.toFixed(1)}" y1="19" y2="19" stroke="${color[r.cls]}" stroke-width="1.6" stroke-opacity=".6" stroke-linecap="round"></line>`;
      if (a - lastX < 34) return bar;
      lastX = a;
      const icon = this._icon(WX_ICON[r.cond] || "mdi:weather-cloudy", a + 7, 8, 14, color[r.cls]) || "";
      const temp = r.temp == null ? "" : `<text x="${(a + 16).toFixed(1)}" y="12" class="ax">${Math.round(r.temp)}°</text>`;
      return `${bar}${icon}${temp}`;
    }).join("");
    return `<svg class="wx" viewBox="0 0 ${W} 22" role="img" aria-label="weather">${marks}</svg>`;
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
      // about 30 frames a second are smooth enough for the dots and halve the work on a wall tablet
      if (!reduced && time - last < 30) { this._frame = requestAnimationFrame(step); return; }
      const dt = Math.min(0.1, (time - last) / 1000);
      last = time;
      if (f.tick) { if (f.tick(dt, reduced) && !reduced) this._frame = requestAnimationFrame(step); return; }
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
    if (!this._data) { el.innerHTML = `<div class="muted">${this._error && (this._tries || 0) > 2 ? this._esc(this._error) : "…"}</div>`; return; }
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
    // production with an hour of margin on either side, so the day does not end abruptly;
    // range "day" shows the whole day from midnight to midnight, the night outside production compressed
    const prodStart = sunny[0], prodEnd = sunny[sunny.length - 1] + H, pS = prodStart - H, pE = prodEnd + H;
    const whole = this._config.range === "day";
    const t0 = whole ? this._data.dayStart / 1000 : pS, t1 = whole ? this._localMidnight(new Date(this._data.dayStart + 30 * H * 1000)) / 1000 : pE;
    const dayH = []; for (let h = t0; h < t1; h += H) dayH.push(h);
    const pvH = dayH.filter((h) => h >= prodStart && h < prodEnd);

    const m = this._model(now);
    const load = m.load;
    const W = 408, CAPW = 46, X0 = CAPW + 6, X1 = W - CAPW - 6, Hp = 104;
    // time axis in segments [from, to, width of an hour]
    const segs = whole ? [[t0, pS, NIGHT_W], [pS, pE, 1], [pE, t1, NIGHT_W]] : [[t0, t1, 1]];
    const span = segs.reduce((a, [a0, a1, w]) => a + (a1 - a0) * w, 0);
    const x = (ts) => X0 + segs.reduce((a, [a0, a1, w]) => a + (Math.max(a0, Math.min(a1, ts)) - a0) * w, 0) / span * (X1 - X0);
    // fixed scale: installed peak power, so a weak day shows as a low curve; the top 14 px stay free
    // for the value of a load cut off by the scale
    const peakW = arrays.reduce((a, arr) => a + (arr.peak_w || 0), 0);
    const top = peakW > 0 ? peakW : Math.max(...dayH.map((h) => Math.max(fc(h) + loss(h), exp(h))), 200) * 1.08;
    const step = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000].find((s) => top / s <= 4) || 20000;
    const ymax = peakW > 0 ? top : Math.ceil(top / step) * step;
    const y = (p) => Hp - 2 - Math.min(p, ymax) / ymax * (Hp - 16);
    const pts = (f) => [[x(prodStart), y(0)], ...pvH.map((h) => [x(h + H / 2), y(f(h))]), [x(prodEnd), y(0)]];
    const fill = (p) => `${line(p)} L${p[p.length - 1][0].toFixed(1)},${(Hp - 2).toFixed(1)} L${p[0][0].toFixed(1)},${(Hp - 2).toFixed(1)} Z`;
    const between = (hi, lo) => { const a = pts(hi), b = pts(lo).reverse(); return `${line(a)} L${b[0][0].toFixed(1)},${b[0][1].toFixed(1)}${curve(b)} Z`; };
    const nowX = now > t0 && now < t1 ? x(now) : null;

    // PV: expectation pale, the past part (= actual) solid, held forecast as contour, shade as notch below the potential
    const potential = (h) => fc(h) + loss(h);
    const lossTotal = this._show("effects") ? dayH.reduce((a, h) => a + loss(h), 0) : 0;
    const hasLoad = this._show("load") && dayH.some((h) => load(h) !== null);
    const lv = (h) => load(h) ?? 0;
    const grid = []; for (let v = step; v <= ymax + 1e-9; v += step) grid.push(v);
    const kw = (v) => (v / 1000).toLocaleString(this._lang, { maximumFractionDigits: 2 });
    // drawn load cut off by the scale: one marker per run of hours, at its highest hourly mean
    const spikes = [];
    for (const h of hasLoad ? dayH : []) {
      const v = lv(h);
      if (v <= ymax) continue;
      const last = spikes[spikes.length - 1];
      if (last && h - last.end <= H) { last.end = h; if (v > last.v) Object.assign(last, { h, v }); } else spikes.push({ h, end: h, v });
    }
    // lines get a halo in the card colour, so they stay visible on the PV area in both themes
    const halo = (d) => `<path d="${d}" fill="none" stroke="var(--card-background-color, #fff)" stroke-width="4" stroke-opacity=".75" stroke-linejoin="round"></path>`;
    // the load spans the same hours as PV: the first and last hourly means are held to the edges
    const loadPts = [[x(t0), y(lv(dayH[0]))], ...dayH.map((h) => [x(h + H / 2), y(lv(h))]), [x(t1), y(lv(dayH[dayH.length - 1]))]];
    const loadLine = hasLoad ? line(loadPts) : "";
    // measured until now, forecast after it: solid, then dashed like every forecast
    const nowCut = now <= t0 ? X0 : now >= t1 ? X1 : x(now);
    const loadSvg = `<clipPath id="nx-load-past"><rect x="0" y="0" width="${nowCut.toFixed(1)}" height="${Hp}"></rect></clipPath>
      <clipPath id="nx-load-next"><rect x="${nowCut.toFixed(1)}" y="0" width="${(W - nowCut).toFixed(1)}" height="${Hp}"></rect></clipPath>
      <path d="${loadLine}" fill="none" stroke="${P.home}" stroke-width="1.5" clip-path="url(#nx-load-past)"></path>
      <path d="${loadLine}" fill="none" stroke="${P.home}" stroke-width="1.5" stroke-dasharray="3 3" clip-path="url(#nx-load-next)"></path>`;
    // P10–P90 of the coming hours, learned by Noxcast from its forecast archive
    const bandM = new Map(((this._data.today && this._data.today.band) || []).map(([ts, lo, hi]) => [ts, [lo, hi]]));
    const bandH = dayH.filter((h) => h + H > now && bandM.has(h));
    let bandPath = "";
    if (bandH.length > 1 && this._show("band")) {
      const hi = bandH.map((h) => [x(h + H / 2), y(bandM.get(h)[1])]), lo = bandH.map((h) => [x(h + H / 2), y(bandM.get(h)[0])]).reverse();
      bandPath = `${line(hi)} L${lo[0][0].toFixed(1)},${lo[0][1].toFixed(1)}${curve(lo)} Z`;
    }
    let svgPv = `${grid.map((v) => `<line x1="${X0}" x2="${X1}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="${P.rail}"></line>
        <text x="${X1 + 3}" y="${(y(v) + 3).toFixed(1)}" class="ax">${kw(v)}${v === grid[grid.length - 1] ? " kW" : ""}</text>`).join("")}
      ${lossTotal > 1 ? `<path d="${between(potential, fc)}" fill="url(#nx-shade)"></path>
        <path d="${line(pts(potential))}" fill="none" stroke="${P.shade}" stroke-width="1" stroke-opacity=".6"></path>` : ""}
      <path d="${fill(pts(exp))}" fill="${P.pv}" fill-opacity=".3"></path>
      <path d="${fill(pts(exp))}" fill="${P.pv}" clip-path="url(#nx-past)"></path>
      ${bandPath ? `<path d="${bandPath}" fill="${P.pv}" fill-opacity=".2" stroke="${P.pvDark}" stroke-opacity=".45" stroke-width=".8" stroke-dasharray="2 2"></path>` : ""}
      ${hasLoad ? `<clipPath id="nx-above"><path d="${line(pts(exp))} L${x(t1).toFixed(1)},${Hp} L${x(t1).toFixed(1)},0 L${x(t0).toFixed(1)},0 L${x(t0).toFixed(1)},${Hp} Z"></path></clipPath>
        <path d="${fill(loadPts)}" fill="${P.home}" fill-opacity=".16" clip-path="url(#nx-above)"></path>` : ""}
      ${halo(line(pts(fc)))}<path d="${line(pts(fc))}" fill="none" stroke="${P.pvDark}" stroke-width="1.4" stroke-dasharray="3 3"></path>
      ${hasLoad ? `${halo(loadLine)}${loadSvg}` : ""}
      ${spikes.map(({ h, v }) => { const sx = x(h + H / 2); return `<path d="M${(sx - 4).toFixed(1)},8 L${(sx + 4).toFixed(1)},8 L${sx.toFixed(1)},2 Z" fill="${P.shade}"></path>
        <text x="${(sx + 6).toFixed(1)}" y="9" font-size="9.5" fill="${P.shade}">${kw(v)} kW</text>`; }).join("")}
      <line x1="${X0}" x2="${X1}" y1="${Hp - 1.5}" y2="${Hp - 1.5}" stroke="var(--divider-color)"></line>`;

    const Ly = Hp + 6;

    // battery: measured level so far, simulated with expected PV and load until the next charge tomorrow
    // caps (B0, Bh) sit with their labels below the lanes, which keep the margins for their names;
    // the strip between them also uses the space above
    const B0 = Ly + 26, Bh = 22;
    const S0 = Ly + 4, Sh = B0 + Bh - S0, ys = (f) => S0 + Sh - Math.max(0, Math.min(1, f)) * Sh;
    const bat = this._show("battery") ? m.battery : null;
    let batSvg = "", sim = null, chargeToday = null, chargeTom = null;
    const seenZones = new Set();
    if (bat) {
      chargeToday = dayH.find((h) => load(h) !== null && pv(h) > lv(h)) ?? t0;
      const tomH = [...tomM.keys()].sort((a, b) => a - b);
      chargeTom = tomH.find((h) => pv(h) > (load(h) ?? 0)) ?? (tomH.length ? tomH[0] : t1 + 12 * H);
      const srv = this._data.today && this._data.today.battery;
      if (srv) {                                   // Noxcast's simulation (batteries configured in the integration)
        chargeTom = srv.charge_start;
        bat.max = srv.max; bat.min = srv.min;
        const pts = srv.points;
        sim = { points: pts, full: srv.full, empty: srv.empty, unused: srv.lost_today, at: (ts) => {
          if (!pts.length) return null;
          if (ts <= pts[0][0]) return pts[0][1];
          for (let i = 1; i < pts.length; i++) if (pts[i][0] >= ts) return pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (ts - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0] || 1);
          return pts[pts.length - 1][1];
        } };
      } else sim = this._simulate(bat, now, chargeTom, pv, load);
      const zone = (h) => {                       // red: at the limit with surplus (lost), orange: within 5 % of it
        const f = h + H <= now ? m.socAt(h + H / 2) : sim.at(h + H / 2);
        if (f === null) return null;
        if (f >= bat.max - 0.01 && pv(h) > lv(h)) return P.lost;
        return f >= bat.max - 0.05 ? P.near : null;
      };
      // ahead: the whole strip height as a warning; already happened: a stripe on top, so the level stays readable
      const zones = dayH.map((h) => { const c = zone(h), done = h + H <= now; if (c) seenZones.add(c); return c ? `<rect x="${x(h).toFixed(1)}" y="${S0}" width="${(x(h + H) - x(h) + 0.3).toFixed(1)}" height="${done ? 5 : Sh}" fill="${c}" fill-opacity="${done ? 0.9 : 0.85}"></rect>` : ""; }).join("");
      const past = m.socPast.filter(([ts]) => ts >= t0 && ts <= Math.min(now, t1)).map(([ts, f]) => [x(ts), ys(f)]);
      // like PV, the measured level reaches the chart edge (and now): the level there, not the first sample
      const pastEnd = Math.min(now, t1), f0 = m.socAt(t0), f1 = m.socAt(pastEnd);
      if (past.length && f0 !== null && past[0][0] > x(t0)) past.unshift([x(t0), ys(f0)]);
      if (past.length && f1 !== null && past[past.length - 1][0] < x(pastEnd)) past.push([x(pastEnd), ys(f1)]);
      // the simulation starts at the full hour; drawn from now on, joined to the measured level
      const futStart = Math.max(now, t0), fStart = futStart === pastEnd && f1 !== null ? f1 : sim.at(futStart);
      const fut = [...(fStart === null ? [] : [[futStart, fStart]]), ...sim.points.filter(([ts]) => ts > futStart && ts <= t1)]
        .map(([ts, f]) => [x(ts), ys(f)]);
      const startF = chargeToday <= now ? m.socAt(chargeToday) : sim.at(chargeToday);
      const endF = sim.empty !== null && sim.empty < chargeTom ? null : sim.at(chargeTom);
      const cap = (cx, f, label, time, future, empty) => {
        const bw = 12, bx = cx - bw / 2, h = Bh, by = B0, fh = (f ?? 0) * (h - 4);
        const color = empty ? P.grid : P.bat;
        return `<rect x="${bx}" y="${by}" width="${bw}" height="${h}" rx="2" fill="none" stroke="${color}" stroke-width="1.4"${future ? ` stroke-dasharray="2 2"` : ""}></rect>
          <rect x="${bx + 2}" y="${(by + h - 2 - fh).toFixed(1)}" width="${bw - 4}" height="${fh.toFixed(1)}" fill="${P.bat}" fill-opacity="${future ? 0.45 : 0.85}"></rect>
          <text x="${cx}" y="${by - 13}" text-anchor="middle" font-size="11" font-weight="500" fill="${color}">${empty ? t.empty : f === null ? "–" : `${Math.round(f * 100)} %`}</text>
          <text x="${cx}" y="${by - 3}" text-anchor="middle" class="ax">${label}</text>
          <text x="${cx}" y="${B0 + Bh + 11}" text-anchor="middle" class="ax">${time}</text>`;
      };
      batSvg = `<rect x="${X0}" y="${S0}" width="${X1 - X0}" height="${Sh}" rx="3" fill="${P.rail}"></rect>
        ${past.length > 1 ? `<path d="${line(past)} L${past[past.length - 1][0].toFixed(1)},${S0 + Sh} L${past[0][0].toFixed(1)},${S0 + Sh} Z" fill="${P.bat}" fill-opacity=".45"></path>` : ""}${zones}
        ${fut.length > 1 ? `<path d="${line(fut)}" fill="none" stroke="${P.bat}" stroke-width="1.8" stroke-dasharray="3 3"></path>` : ""}
        ${sim.full !== null && sim.full < t1 ? `<circle cx="${x(sim.full).toFixed(1)}" cy="${ys(bat.max).toFixed(1)}" r="3" fill="var(--card-background-color, #fff)" stroke="${P.bat}" stroke-width="1.6"></circle>` : ""}
        ${cap(CAPW / 2, startF, chargeToday > now ? t.expected : t.start, this._hm(chargeToday), chargeToday > now, false)}
        ${cap(W - CAPW / 2, endF, t.tomorrow, endF === null ? this._hm(sim.empty) : this._hm(chargeTom), true, endF === null)}`;
    }
    const bottom = bat ? B0 + Bh + 14 : Ly + 14;
    const tickStep = Math.max(1, Math.ceil((pE - pS) / H / 6)) * H;
    const ticks = [];
    const tick = (h) => { if (ticks.every((q) => Math.abs(x(q) - x(h)) > 14)) ticks.push(h); };
    if (whole) { tick(t0); tick(t1); }
    for (let h = Math.ceil(pS / tickStep) * tickStep; h <= pE; h += tickStep) tick(h);
    // the compressed night: tinted, a line where the day begins and ends, a moon on top
    const nightSvg = !whole ? "" : [[t0, pS], [pE, t1]].filter(([a, b]) => b > a).map(([a, b]) => {
      const xa = x(a), xb = x(b), edge = a === t0 ? xb : xa;
      return `<rect x="${xa.toFixed(1)}" y="0" width="${(xb - xa).toFixed(1)}" height="${bottom - 14}" fill="${P.night}" fill-opacity=".10"></rect>
        <line x1="${edge.toFixed(1)}" x2="${edge.toFixed(1)}" y1="0" y2="${bottom - 14}" stroke="${P.night}" stroke-opacity=".45"></line>
        ${xb - xa > 12 ? this._icon("mdi:weather-night", (xa + xb) / 2, 7, 10, P.night) || "" : ""}`;
    }).join("");

    // values per hour for the tooltip: hovering or tapping an hour shows what the chart draws there
    const socAt = (ts) => (!bat ? null : ts <= now ? m.socAt(ts) : sim.at(ts));
    this._tipRows = dayH.map((h) => {
      const done = h + H <= now, rows = [[done ? t.actual : t.expected, exp(h), P.pvText]];
      if (Math.abs(fc(h) - exp(h)) >= 1 || !done) rows.push([t.lg.forecast, fc(h), P.pvDark]);
      if (loss(h) >= 1) rows.push([t.lg.loss, -loss(h), P.shade]);
      if (load(h) !== null) rows.push([t.lg.load, lv(h), "var(--primary-text-color)"]);
      const soc = socAt(Math.min(h + H, Math.max(h, now)));
      return { label: `${this._hm(h)}–${this._hm(h + H)}`, rows, soc, x: x(h + H / 2) };
    });
    const hits = dayH.map((h, i) => `<rect data-tip="${i}" x="${x(h).toFixed(1)}" y="0" width="${(x(h + H) - x(h)).toFixed(1)}" height="${bottom - 14}" fill="transparent"></rect>`).join("");
    const svg = `<svg viewBox="0 0 ${W} ${bottom}" role="img" aria-label="${t.pv}, ${t.load}, ${t.soc}">
      <defs>
        <clipPath id="nx-past"><rect x="0" y="0" width="${(nowX ?? (now >= t1 ? W : 0)).toFixed(1)}" height="${Hp}"></rect></clipPath>
        <pattern id="nx-shade" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="${P.shade}" fill-opacity=".15"></rect><line x1="0" y1="0" x2="0" y2="5" stroke="${P.shade}" stroke-width="1.6" stroke-opacity=".7"></line></pattern>
      </defs>
      ${nightSvg}${svgPv}${batSvg}
      ${nowX !== null ? `<line x1="${nowX.toFixed(1)}" y1="0" x2="${nowX.toFixed(1)}" y2="${bottom - 14}" stroke="var(--secondary-text-color)" stroke-width="1" stroke-opacity=".6" stroke-dasharray="2 2"></line>` : ""}
      ${ticks.map((h) => `<text x="${x(h).toFixed(1)}" y="${bottom - 3}" text-anchor="middle" class="ax">${whole && h === t1 ? "24" : this._hm(h).slice(0, 2)}</text>`).join("")}
      <line id="nx-cursor" y1="0" y2="${bottom - 14}" stroke="var(--primary-text-color)" stroke-width="1" stroke-opacity=".35" visibility="hidden"></line>
      <g id="nx-hits">${hits}</g>
    </svg>`;

    // planning line and the three numbers
    const after = now >= prodEnd, before = now < prodStart;
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
    // best windows of flexible consumers, planned by Noxcast
    for (const p of (!after && this._data.today && this._data.today.plan) || []) {
      if (p.start !== null && p.pv >= 0.5 && p.end > now) plan.push(`${this._esc(p.name)} ${this._hm(Math.max(p.start, now))}–${this._hm(p.end)} (${Math.round(p.pv * 100)} %)`);
    }
    if (sim && !after && !this._config.export && sim.unused >= 50) plan.push(`<span class="warn" style="color:${P.warnText};background:${P.warnBg}">${this._kwh(sim.unused)} ${t.kwh} ${t.unused}</span>`);
    if (sim && after) plan.push(sim.empty !== null && sim.empty < chargeTom ? `${t.emptyAt} ${this._hm(sim.empty)}` : `${t.lasts} ${this._hm(chargeTom)}`);
    const dev = morning > 0 ? Math.round((ist / morning - 1) * 100) : null;
    const numbers = after
      ? `<span><span class="big">${this._kwh(ist)}</span> ${t.kwh}</span>
         <span class="muted">${t.morning} ${this._kwh(morning)}${dev === null ? "" : ` <b style="color:var(--primary-text-color);font-weight:500">(${dev > 0 ? "+" : ""}${dev} %)</b>`}</span>`
      : `<span>${t.actual} <span class="big">${this._kwh(ist)}</span></span>
         <span>${t.expected} <b style="font-weight:500">${this._kwh(expected)}</b></span>
         <span class="muted">${t.morning} ${this._kwh(morning)}</span>`;
    el.innerHTML = `${this._weatherStrip(t0, t1, x, X0, X1, W)}<div class="chart">${svg}<div id="nx-tip" class="tip" hidden></div></div>${this._config.legend === false ? "" : this._legend({
        actual: now > prodStart, loss: lossTotal > 1, band: !!bandPath, load: hasLoad, deficit: hasLoad && dayH.some((h) => lv(h) > exp(h) + 1),
        bat: !!bat, lost: seenZones.has(P.lost), near: seenZones.has(P.near) })}
      ${plan.length ? `<div class="row">${plan.map((p) => `<span>${p}</span>`).join("")}</div>` : ""}
      <div class="row nums">${numbers}
        ${lossTotal >= 50 ? `<span style="color:${P.shade}">${t.shade} −${this._kwh(lossTotal)}</span>` : ""}
        <span class="muted">${t.tomorrow} <b style="color:var(--primary-text-color);font-weight:500">${this._kwh(tomorrow)}</b></span>
      </div>`;
    const chart = el.querySelector(".chart");
    chart.addEventListener("pointermove", (ev) => this._tip(ev));
    chart.addEventListener("pointerdown", (ev) => this._tip(ev));
    chart.addEventListener("pointerleave", () => this._tip(null));
  }

  // tooltip for the hour under the pointer; a tap on touch screens shows it until the next tap elsewhere
  _tip(ev) {
    const root = this.shadowRoot, tip = root.getElementById("nx-tip"), cursor = root.getElementById("nx-cursor");
    if (!tip || !cursor) return;
    const hit = ev && ev.target && ev.target.dataset && ev.target.dataset.tip !== undefined ? this._tipRows[Number(ev.target.dataset.tip)] : null;
    if (!hit) {
      if (ev && ev.type === "pointermove" && ev.pointerType !== "mouse") return;
      tip.hidden = true; cursor.setAttribute("visibility", "hidden");
      return;
    }
    const w = (v) => `${Math.round(v).toLocaleString(this._lang)} W`;
    tip.innerHTML = `<b>${hit.label}</b>${hit.rows.map(([k, v, c]) => `<span><i style="background:${c}"></i>${k}</span><span class="v">${w(v)}</span>`).join("")}
      ${hit.soc === null || hit.soc === undefined ? "" : `<span><i style="background:${this._p.bat}"></i>${this._t.lg.bat}</span><span class="v">${Math.round(hit.soc * 100)} %</span>`}`;
    tip.hidden = false;
    cursor.setAttribute("x1", hit.x.toFixed(1)); cursor.setAttribute("x2", hit.x.toFixed(1)); cursor.setAttribute("visibility", "visible");
    const box = tip.parentElement.getBoundingClientRect(), svgBox = tip.parentElement.querySelector("svg").getBoundingClientRect();
    const px = (hit.x / 408) * svgBox.width;
    const left = px > box.width / 2 ? px - tip.offsetWidth - 10 : px + 10;
    tip.style.left = `${Math.max(0, Math.min(box.width - tip.offsetWidth, left))}px`;
  }

  // legend for what the chart shows right now
  _legend(on) {
    const P = this._p, l = this._t.lg;
    const sw = (inner) => `<svg width="16" height="10" aria-hidden="true">${inner}</svg>`;
    const box = (fill, op = 1) => sw(`<rect x="1" y="1" width="14" height="8" rx="2" fill="${fill}" fill-opacity="${op}"></rect>`);
    const dash = (stroke, d) => sw(`<line x1="0" y1="5" x2="16" y2="5" stroke="${stroke}" stroke-width="1.8"${d ? ` stroke-dasharray="3 2"` : ""}></line>`);
    const items = [
      [on.actual, box(P.pv), l.actual], [true, box(P.pv, 0.3), l.expected], [true, dash(P.pvDark, true), l.forecast], [on.band, box(P.pv, 0.2), l.band],
      [on.loss, sw(`<rect x="1" y="1" width="14" height="8" rx="2" fill="url(#nx-shade)"></rect>`), l.loss],
      [on.load, dash(P.home, false), l.load], [on.deficit, box(P.home, 0.16), l.deficit],
      [on.bat, box(P.bat, 0.45), l.bat], [on.lost, box(P.lost, 0.85), l.lost], [on.near, box(P.near, 0.85), l.near],
    ];
    return `<div class="legend">${items.filter(([show]) => show).map(([, icon, text]) => `<span>${icon}${text}</span>`).join("")}</div>`;
  }

  // arrays of today in the Noxcast order, with the display settings of their group
  _arrays() {
    const groups = this._config.pv.groups || [];
    return this._data.today.arrays.map((a) => {
      const g = groups.find((x) => x.name === a.name) || {};
      return { ...a, label: g.label || a.name, icon: g.icon || "", hide: !!g.hide };
    });
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
    const measured = new Map(), byHour = new Map();
    for (const r of stats[homeId] || []) {
      const ts = r.start / 1000;
      if (r.mean === null || r.mean === undefined) continue;
      if (ts >= today) measured.set(ts, r.mean * scale);
      const k = (((ts - today) % 86400) + 86400) % 86400;
      if (ts < today) { const l = byHour.get(k) || []; l.push(r.mean * scale); byHour.set(k, l); }
    }
    const liveHome = this._live().home;
    const srvLoad = new Map((this._data.today && this._data.today.load) || []);     // Noxcast load forecast
    const load = (h) => {
      if (h + H <= now && measured.has(h)) return measured.get(h);
      if (h <= now && h + H > now && homeId) return liveHome;
      if (srvLoad.has(h)) return srvLoad.get(h);
      const l = byHour.get((((h - today) % 86400) + 86400) % 86400);
      return l && l.length ? l.reduce((a, b) => a + b, 0) / l.length : null;
    };

    const bats = (c.batteries || []).filter((b) => Number(b.capacity) > 0);
    const capWh = bats.reduce((a, b) => a + Number(b.capacity) * 1000, 0);
    if (!capWh) return { load, battery: null, socPast: [], socAt: () => null };
    const battery = {
      cap: capWh,
      max: bats.reduce((a, b) => a + Number(b.capacity) * 1000 * this._limit(b.max_soc, 100) / 100, 0) / capWh,
      min: bats.reduce((a, b) => a + Number(b.capacity) * 1000 * Math.min(100, this._limit(b.min_soc, 0) + (Number(b.reserve) || 0)) / 100, 0) / capWh,
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
    return { load, battery, socPast, socAt };
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

// Visual editor: display only. Sensors, batteries and feed-in come from the Noxcast configuration.
const LEGACY_KEYS = ["pv", "grid", "home", "export"];

class NoxcastCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._loadSource();
    this._render();
  }

  get _de() { return (this._hass.language || "en").startsWith("de"); }

  async _loadSource() {
    try {
      this._src = await ask(this._hass, { type: "noxcast/card_config" });
    } catch (err) {
      this._src = null;
    }
    this._shape = null;
    this._render();
  }

  _emit(c) {
    this._config = c;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: c }, bubbles: true, composed: true }));
  }

  _labels() {
    const de = this._de;
    return {
      font_scale: de ? "Schriftgröße" : "Text size", legend: de ? "Legende anzeigen" : "Show legend",
      show_battery: de ? "Akku-Ladestandsprognose" : "Battery forecast", show_effects: de ? "Schattenverlust" : "Lost to shade",
      show_band: de ? "Spanne P10–P90" : "Range P10–P90", show_load: de ? "Hauslast" : "House load",
      show_weather: de ? "Stundenwetter über dem Verlauf" : "Hourly weather above the chart",
      range: de ? "Zeitraum des Verlaufs" : "Time range of the chart",
      flow: de ? "Leistung jetzt" : "Power now", look: de ? "Aussehen" : "Look",
      flow_style: de ? "Fluss in der kompakten Zeile" : "Flow in the compact row", flow_speed: de ? "Tempo der Punkte" : "Pace of the dots",
      flow_min_w: de ? "Fluss ab (W)" : "Flow from (W)",
      icon_pv: de ? "Symbol PV" : "PV icon", icon_grid: de ? "Symbol Netz" : "Grid icon",
      icon_battery: de ? "Symbol Akku (leer = nach Ladestand)" : "Battery icon (empty = by state of charge)",
      icon_home: de ? "Symbol Haus (leer = nach Quellen gefärbt)" : "Home icon (empty = coloured by its sources)",
      name: de ? "Anzeigename (leer = Name in Noxcast)" : "Display name (empty = name in Noxcast)",
      icon: de ? "Symbol statt Name (optional)" : "Icon instead of the name (optional)",
      hide: de ? "Nicht einzeln anzeigen" : "Do not list on its own",
    };
  }

  _form(schema, data, onChange) {
    const f = document.createElement("ha-form");
    const label = this._labels();
    f.hass = this._hass; f.schema = schema; f.data = data;
    f.computeLabel = (item) => label[item.name] || item.name;
    f.addEventListener("value-changed", (ev) => { ev.stopPropagation(); f.data = ev.detail.value; onChange(ev.detail.value); });
    return f;
  }

  // sensor settings of earlier versions; the card ignores them
  _legacy() {
    const c = this._config || {};
    return LEGACY_KEYS.some((k) => k in c) || Array.isArray(c.batteries) || Array.isArray(c.groups);
  }

  _render() {
    if (!this._hass || !this._config) return;
    const c = this._config, src = this._src || {};
    const groups = ((src.pv && src.pv.groups) || []).map((g) => g.name);
    const bats = (src.batteries || []).map((b) => b.name).filter(Boolean);
    const shape = `${groups.join("|")}/${bats.join("|")}/${this._legacy()}/${this._de}`;
    if (this._shape === shape) {                       // only refresh hass, keeps the focus while typing
      for (const f of this.querySelectorAll("ha-form")) f.hass = this._hass;
      return;
    }
    this._shape = shape;
    const de = this._de;
    this.innerHTML = `<style>
        .nx-sec { margin: 16px 0 4px; font-weight: 500; }
        .nx-item { border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 12px; margin: 8px 0; }
        .nx-head { color: var(--secondary-text-color); font-size: 13px; margin-bottom: 4px; }
        .nx-btn { background: none; border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 10px;
          color: var(--primary-color); font: inherit; cursor: pointer; }
        .nx-note { color: var(--secondary-text-color); font-size: 13px; margin: 4px 0 8px; }
        .nx-warn { border-left: 3px solid var(--warning-color, #F29900); padding: 6px 10px; margin: 8px 0; font-size: 13px; }
      </style>
      ${this._legacy() ? `<div class="nx-warn">${de
        ? "Diese Karte enthält Sensor-Einstellungen einer früheren Version. Sie werden ignoriert: Die Karte zeigt immer, was in Noxcast konfiguriert ist."
        : "This card has sensor settings of an earlier version. They are ignored: the card always shows what Noxcast is configured with."}
        <div style="margin-top:6px"><button class="nx-btn" id="clean">${de ? "Alte Sensor-Einstellungen entfernen" : "Remove old sensor settings"}</button></div></div>` : ""}
      <div class="nx-sec">${de ? "Anzeige" : "Display"}</div><div id="main"></div>
      <div class="nx-sec">${de ? "PV-Gruppen" : "PV groups"}</div>
      <div class="nx-note">${de ? "Sensoren, Akkus und Einspeisung kommen aus der Noxcast-Konfiguration. Hier nur, wie sie angezeigt werden."
        : "Sensors, batteries and feed-in come from the Noxcast configuration. Here only how they are shown."}</div>
      <div id="groups">${groups.length ? "" : `<div class="nx-note">${de ? "Noxcast ist noch nicht geladen." : "Noxcast is not loaded yet."}</div>`}</div>
      ${bats.length ? `<div class="nx-sec">${de ? "Akkus" : "Batteries"}</div><div id="bats"></div>` : ""}`;

    const shows = ["show_battery", "show_effects", "show_band", "show_load", "show_weather"];
    const opt = (pairs) => ({ select: { mode: "dropdown", options: pairs.map(([value, label]) => ({ value, label })) } });
    this.querySelector("#main").appendChild(this._form([
      { name: "flow", selector: opt([["full", de ? "Flussdiagramm" : "Flow diagram"], ["compact", de ? "Kompakte Zeile" : "Compact row"]]) },
      { name: "flow_style", selector: opt([["chain", de ? "Kette: Punkte zwischen den Werten" : "Chain: dots between the figures"],
        ["bus", de ? "Bus: Linie unter den Werten" : "Bus: line below the figures"]]) },
      { name: "flow_speed", selector: opt([["calm", de ? "ruhig" : "calm"], ["normal", "normal"], ["lively", de ? "lebhaft" : "lively"]]) },
      { name: "flow_min_w", selector: { number: { min: 0, max: 200, step: 1, mode: "box", unit_of_measurement: "W" } } },
      { name: "look", selector: opt([["plain", "Home Assistant"], ["noxcast", "Noxcast"]]) },
      { name: "range", selector: opt([["production", de ? "Produktion, je 1 h Rand" : "Production, an hour either side"],
        ["day", de ? "Ganzer Tag, Nacht gestaucht" : "Whole day, night compressed"]]) },
      ...shows.map((name) => ({ name, selector: { boolean: {} } })),
      { name: "legend", selector: { boolean: {} } },
      { name: "font_scale", selector: { number: { min: 0.8, max: 1.6, step: 0.1, mode: "slider" } } },
      { name: "icon_pv", selector: { icon: {} } }, { name: "icon_grid", selector: { icon: {} } },
      { name: "icon_battery", selector: { icon: {} } }, { name: "icon_home", selector: { icon: {} } },
    ], {
      font_scale: Number(c.font_scale) || 1, legend: c.legend !== false, flow: c.flow || "full", look: c.look || "plain", range: c.range || "production",
      flow_style: c.flow_style || "chain", flow_speed: c.flow_speed || "normal", flow_min_w: c.flow_min_w ?? FLOW_MIN_W,
      ...Object.fromEntries(shows.map((k) => [k, c[k] !== false])),
      ...Object.fromEntries(["pv", "grid", "battery", "home"].map((k) => [`icon_${k}`, ((c.icons || {})[k] || "").replace(/^auto$/, "")])),
    }, (v) => {
      const n = { ...this._config };
      n.font_scale = v.font_scale; n.legend = v.legend;
      if (v.flow && v.flow !== "full") n.flow = v.flow; else delete n.flow;
      if (v.flow_style && v.flow_style !== "chain") n.flow_style = v.flow_style; else delete n.flow_style;
      if (v.flow_speed && v.flow_speed !== "normal") n.flow_speed = v.flow_speed; else delete n.flow_speed;
      if (v.flow_min_w !== undefined && v.flow_min_w !== "" && Number(v.flow_min_w) !== FLOW_MIN_W) n.flow_min_w = Number(v.flow_min_w); else delete n.flow_min_w;
      if (v.look && v.look !== "plain") n.look = v.look; else delete n.look;
      if (v.range === "day") n.range = "day"; else delete n.range;
      for (const k of shows) { if (v[k] === false) n[k] = false; else delete n[k]; }
      const icons = Object.fromEntries(["pv", "grid", "battery", "home"].filter((k) => v[`icon_${k}`]).map((k) => [k, v[`icon_${k}`]]));
      if (Object.keys(icons).length) n.icons = icons; else delete n.icons;
      if (n.legend !== false) delete n.legend;
      if (n.font_scale === 1) delete n.font_scale;
      this._emit(n);
    }));

    const item = (box, title, form) => {
      const el = document.createElement("div");
      el.className = "nx-item";
      el.innerHTML = `<div class="nx-head">${title.replace(/[&<>"']/g, "")}</div>`;
      el.appendChild(form);
      box.appendChild(el);
    };
    // per Noxcast group: display name, icon, hidden; only set values are kept
    const shown = () => (this._config.groups && !Array.isArray(this._config.groups) ? this._config.groups : {});
    const gBox = this.querySelector("#groups");
    for (const name of groups) {
      const d = shown()[name] || {};
      item(gBox, name, this._form([
        { name: "name", selector: { text: {} } }, { name: "icon", selector: { icon: {} } }, { name: "hide", selector: { boolean: {} } },
      ], { name: d.name || "", icon: d.icon || "", hide: !!d.hide }, (v) => {
        const next = { ...shown() };
        const entry = Object.fromEntries(Object.entries({ name: v.name, icon: v.icon, hide: v.hide }).filter(([, x]) => x));
        if (Object.keys(entry).length) next[name] = entry; else delete next[name];
        const n = { ...this._config };
        if (Object.keys(next).length) n.groups = next; else delete n.groups;
        this._emit(n);
      }));
    }
    const bBox = this.querySelector("#bats");
    for (const name of bats) {
      const names = this._config.battery_names || {};
      item(bBox, name, this._form([{ name: "name", selector: { text: {} } }], { name: names[name] || "" }, (v) => {
        const next = { ...(this._config.battery_names || {}) };
        if (v.name) next[name] = v.name; else delete next[name];
        const n = { ...this._config };
        if (Object.keys(next).length) n.battery_names = next; else delete n.battery_names;
        this._emit(n);
      }));
    }
    const clean = this.querySelector("#clean");
    if (clean) clean.addEventListener("click", () => {
      const n = { ...this._config };
      for (const k of LEGACY_KEYS) delete n[k];
      if (Array.isArray(n.batteries)) delete n.batteries;
      if (Array.isArray(n.groups)) delete n.groups;
      this._emit(n);
      this._render();
    });
  }
}

customElements.define("noxcast-card", NoxcastCard);
customElements.define("noxcast-card-editor", NoxcastCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({ type: "noxcast-card", name: "Noxcast", description: "Power flow, today's yield, shade loss and surplus", preview: false });
