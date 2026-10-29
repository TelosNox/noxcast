// Noxcast day card: power flow now, today's yield with the loss to shade, house load,
// battery and unused surplus. Plain custom element, no build step.
//
// type: custom:noxcast-card
// The card shows what Noxcast is configured with: PV groups and their sensors, house load, grid,
// batteries and whether surplus is fed in. The card config only changes the display:
// show_battery: true / show_effects: true / show_band: true / show_load: true   # what the day chart shows
// show_weather: true                   # hourly weather above the chart (Noxcast's weather), a symbol at every change
// range: production                    # day chart: production (an hour either side) or day (0–24 h, night compressed)
// flow_style: chain                    # power now: chain (dots between the figures) or bus (a line below them)
// labels: icons                       # above the figures: icons that show the state (sun by the share of peak
//                                      # power, battery filled to its level, lit windows and lightning bolts in
//                                      # steps of the base load) or text (names, battery percentage and power)
// sun_glow: false                      # icons: the sun glows by the share of peak power
// battery_arrows: false                # icons: up and down arrows beside the battery, the active one lit
// flow_speed: normal                   # pace of the moving dots: calm, normal or lively
// flow_min_w: 10                       # a flow shows from this many watts (sensors drift by a few watts)
// look: plain                          # plain: Home Assistant card; noxcast: the Noxcast panel look
// legend: true                         # legend below the chart, only for what is shown
// font_scale: 1                        # text size, 0.8 to 1.6; all options are also in the visual editor
// Tapping PV, battery, home or grid opens the entity's more-info dialog.

const TEXT = {
  de: { pv: "PV", home: "Haus", grid: "Netz", battery: "Akku", tomorrow: "morgen", shade: "Schatten",
    full: "voll", load: "Hauslast", soc: "Akku-Ladestand", lost: "Schattenverlust",
    noData: "Noch keine Noxcast-Daten", kwh: "kWh", actual: "Ist", expected: "erwartet", morning: "Prognose",
    import: "Bezug", export: "Einspeisung",
    lg: { actual: "Ist", expected: "Erwartung", forecast: "Prognose", load: "Hauslast", deficit: "Last über PV", band: "Spanne P10–P90",
      loss: "Schattenverlust", bat: "Akku", lost: "voll, Überschuss verfällt",
      near: "fast voll" } },
  en: { pv: "PV", home: "Home", grid: "Grid", battery: "Battery", tomorrow: "tomorrow", shade: "shade",
    full: "full", load: "House load", soc: "Battery level", lost: "Lost to shade",
    noData: "No Noxcast data yet", kwh: "kWh", actual: "Actual", expected: "expected", morning: "forecast",
    import: "Import", export: "Export",
    lg: { actual: "actual", expected: "expected", forecast: "forecast", load: "house load", deficit: "load above PV", band: "range P10–P90",
      loss: "lost to shade", bat: "battery", lost: "full, surplus lost",
      near: "almost full" } },
};

const PALETTE = {
  light: { pv: "#F2B233", pvDark: "#C27A06", pvText: "#9A6400", grid: "#2F80C8", home: "#4F5964", bat: "#23874A",
    shade: "#C0392B", rail: "rgba(0,0,0,0.08)", unused: "#2F80C8", unusedBg: "rgba(47,128,200,0.12)", lost: "#D93025", near: "#F29900" },
  dark: { pv: "#F2B233", pvDark: "#C58410", pvText: "#F2B233", grid: "#5AA6E8", home: "#B4BCC4", bat: "#4CC77A",
    shade: "#F07058", rail: "rgba(255,255,255,0.10)", unused: "#5AA6E8", unusedBg: "rgba(90,166,232,0.15)", lost: "#F2635A", near: "#F5A524" },
};

// with range "day" a night hour gets this share of a day hour's width in the day chart
const NIGHT_W = 0.3;
// a flow shows from flow_min_w (default FLOW_MIN_W) and stays until it falls below FLOW_OFF of that: sensors
// drift by a few watts, so small mismatches show no flow, and a flow near the threshold does not flicker
const FLOW_MIN_W = 10, FLOW_OFF = 0.6;
// base load until 7 days of house load are known; the house and grid icons step at multiples of it
const BASE_LOAD_W = 300;
const REFRESH_MS = 5 * 60 * 1000;
const RETRY_MS = [5000, 15000, 30000, 60000];   // after a failed load; Noxcast may still be starting after a restart
const LAG_RETRY_MS = 15000;                     // Noxcast still has the day before (its midnight run is not done)
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
    return { ...u, pv: { groups: (s.pv && s.pv.groups) || [] }, home: s.home || {}, grid: s.grid || {},
      batteries: s.batteries || [], export: !!s.export };
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
    const id = this._src && this._src.weather;
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

  // data older than `seconds`, or from another day (asked again every LAG_RETRY_MS until Noxcast has the new one)
  _stale(seconds = REFRESH_MS / 1000) {
    if (!this._hass || this._loading) return false;
    if (!this._data) return !this._error;
    if (this._localMidnight(new Date()) !== this._data.dayStart) return Date.now() - this._data.loaded > LAG_RETRY_MS;
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
    const groups = (c.pv.groups || []).map((g) => ({ id: g.entity || (g.entities || [])[0], w: this._groupW(g) }));
    const pv = groups.reduce((a, g) => a + g.w, 0);
    const bats = (c.batteries || []).map((b) => ({ id: b.soc || b.entity, w: this._watts(b.entity, b.invert) || 0,
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
      // the day comes from Noxcast: just after midnight its new computation may still be running, then the
      // card keeps showing the day before and asks again shortly
      const dayStart = today.day_start ? today.day_start * 1000 : stats.dayStart;
      this._data = { today, ...stats, dayStart, loaded: Date.now() };
      this._error = null;
      this._tries = 0;
      clearTimeout(this._retry);
      if (dayStart !== this._localMidnight(new Date()) && this.isConnected) this._retry = setTimeout(() => this._refresh(), LAG_RETRY_MS);
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
      .cf .ic { display: block; height: calc(22px * var(--nx-scale, 1)); margin-bottom: 2px; }
      .cf .ic svg { height: 100%; width: auto; overflow: visible; }
      .cf .v { line-height: 1.2; }
      /* chain: the row aligns on the baseline of the values; a gap has no baseline of its own, so its bottom
         edge sits on it. It is one cap height tall (digits are cap height), so its middle is the middle of
         the digits, whatever the font, size or line height. 0.72em: the cap height of Roboto, where cap is unknown */
      .cf.chain { padding: 0 4px; align-items: last baseline; }
      .cf .gap { display: block; width: 100%; height: .72em; height: 1cap; overflow: visible; }
      .cf.chain .f { padding: 0 10px; }   /* room between a value and the lines beside it */
      .bus { display: block; width: 100%; height: 22px; margin-top: -4px; overflow: visible; }
      .wx { margin-bottom: 6px; }       /* a little air, so the strip does not read as a lid on the chart */
      .muted { color: var(--secondary-text-color); }
      .row { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; }
      #band { container-type: inline-size; }
      .nums { flex-wrap: nowrap; gap: 4px 8px; font-size: clamp(10px, 3.2cqw, 13px); white-space: nowrap; }
      .nums .big { font-size: clamp(13px, 4cqw, 16px); }
      .big { font-size: 16px; font-weight: 500; }
      svg { display: block; width: 100%; height: auto; overflow: visible; }
      .legend { display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 11px; color: var(--secondary-text-color); }
      .legend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
      .legend svg { width: 16px; height: 10px; display: inline-block; }
      .ax { font-size: calc(8px * min(var(--nx-scale, 1), 1.2)); fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .ax.in { paint-order: stroke; stroke: var(--card-background-color, #fff); stroke-width: 3px; stroke-opacity: .8; stroke-linejoin: round; }
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

  // more-info of the entity behind a tapped flow element
  _moreInfo(ev) {
    const g = ev.target.closest && ev.target.closest("[data-more]");
    if (!g || !g.dataset.more) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: g.dataset.more }, bubbles: true, composed: true }));
  }

  _renderFlow() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("flow");
    if (el) this._renderCompact(el, this._live());
  }

  // power now as one row of figures, in the order PV, battery, home, grid: then the power
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
    const shape = `${bus ? "bus" : "chain"}|${this._icons}|${nodes.map((n) => n.key).join()}`;
    if (!this._flow || this._flow.shape !== shape || !el.contains(this._flow.root)) this._buildCompact(el, nodes, bus, shape);
    const f = this._flow;

    // the arrows only on the bus, which leaves room for them; in the chain the dots show the direction
    const arrow = (w, key) => (!bus || !this._on(key, w) ? "" : w > 0 ? "▲ " : "▼ ");
    const text = {
      pv: num(v.pv), home: num(v.home), grid: `${arrow(v.grid, "n:grid")}${num(Math.abs(v.grid))}`,
      bat: v.socAll === null ? "–" : this._icons ? `${Math.round(v.socAll)} %`
        : `${Math.round(v.socAll)} % <small>${arrow(v.bat, "n:bat")}${num(Math.abs(v.bat))}</small>`,
    };
    const icons = this._icons ? this._stateIcons(v) : {};
    for (const n of nodes) {
      f.values[n.key].innerHTML = text[n.key];
      f.values[n.key].style.color = n.color;
      if (f.icons[n.key] && f.icons[n.key].innerHTML !== icons[n.key]) f.icons[n.key].innerHTML = icons[n.key];
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

  // icons instead of names above the figures (labels: icons; battery_style: power of earlier versions keeps the names)
  get _icons() { return (this._config.labels || (this._config.battery_style === "power" ? "text" : "icons")) !== "text"; }

  // base load: the quietest tenth of the hourly house load over the last 7 days; the steps of the house and grid
  // icons are multiples of it, so they read the same in a small flat and in a large house
  _baseLoad() {
    const id = this._config.home && this._config.home.entity, rows = (this._data && this._data.stats && this._data.stats[id]) || [];
    const st = id && this._hass.states[id], k = st && (st.attributes.unit_of_measurement || "").toLowerCase() === "kw" ? 1000 : 1;
    const means = rows.map((r) => r.mean).filter((m) => m > 0).map((m) => m * k).sort((x, y) => x - y);
    return means.length >= 24 ? Math.max(50, means[Math.floor(means.length * 0.1)]) : BASE_LOAD_W;
  }

  // 0 while the figure is idle, then 1 to 3: from 3 and from 8 times the base load
  _step(key, w, base) {
    if (!this._on(key, w)) return 0;
    return Math.abs(w) >= 8 * base ? 3 : Math.abs(w) >= 3 * base ? 2 : 1;
  }

  // the icons above the figures; each shows the state of its figure in its own way
  _stateIcons(v) {
    const P = this._p, c = this._config, off = "var(--secondary-text-color)", base = this._baseLoad();
    const peak = this._data ? this._arrays().reduce((a, arr) => a + (arr.peak_w || 0), 0) : 0;
    const svg = (w, body, style) => `<svg viewBox="0 0 ${w} 24" aria-hidden="true"${style ? ` style="${style}"` : ""}>${body}</svg>`;
    // sun: the core fills and the rays grow with the share of peak power
    const pvOn = this._on("n:pv", v.pv), share = !pvOn ? 0 : peak > 0 ? Math.min(1, v.pv / peak) : 1;
    let rays = "";
    for (let i = 0; pvOn && i < 8; i++) {
      const a = (i * Math.PI) / 4, len = 2.2 + 3.8 * share, p = (r) => `${(12 + r * Math.cos(a)).toFixed(2)} ${(12 + r * Math.sin(a)).toFixed(2)}`;
      rays += `M${p(6.5)}L${p(6.5 + len)}`;
    }
    const glow = pvOn && c.sun_glow ? `filter:drop-shadow(0 0 ${(1.5 + 5 * share).toFixed(1)}px ${P.pv})` : "";
    const pv = svg(24, `<circle cx="12" cy="12" r="4.4" fill="${pvOn ? P.pv : "none"}" fill-opacity="${(0.35 + 0.65 * share).toFixed(2)}"
      stroke="${pvOn ? P.pv : off}" stroke-width="1.8"></circle>${rays ? `<path d="${rays}" stroke="${P.pv}" stroke-width="1.8" stroke-linecap="round"></path>` : ""}`, glow);
    // battery: as high as the other icons, filled to the state of charge; with battery_arrows the arrow of the flow
    // lights up beside it
    const soc = v.socAll === null ? 0 : Math.max(0, Math.min(100, v.socAll)), dir = !this._on("n:bat", v.bat) ? 0 : v.bat > 0 ? 1 : -1;
    const arrows = !c.battery_arrows ? "" : `<path d="M42 3.5 L46 10.5 H38 Z" fill="${P.bat}" fill-opacity="${dir > 0 ? 1 : 0.22}"></path>
      <path d="M42 20.5 L46 13.5 H38 Z" fill="${P.bat}" fill-opacity="${dir < 0 ? 1 : 0.22}"></path>`;
    const bat = svg(c.battery_arrows ? 47 : 37, `<rect x="1.5" y="3.5" width="30" height="17" rx="3" fill="none" stroke="${off}" stroke-width="1.6"></rect>
      <rect x="32" y="8.5" width="3.5" height="7" rx="1" fill="${off}"></rect>
      <rect x="4" y="6" width="${((25 * soc) / 100).toFixed(2)}" height="12" rx="1.5" fill="${P.bat}"></rect>${arrows}`);
    // house: a window lights per step
    const hs = this._step("n:home", v.home, base);
    const win = (x, i) => `<rect x="${x}" y="13" width="4" height="4" fill="${i < hs ? P.pv : "none"}" stroke="${i < hs ? P.pv : off}" stroke-width="1"></rect>`;
    const home = svg(40, `<path d="M3 11 L20 3 L37 11 M5.5 9.8 V21.5 H34.5 V9.8" fill="none" stroke="${off}" stroke-width="1.7"
      stroke-linejoin="round" stroke-linecap="round"></path>${win(9, 0)}${win(18, 1)}${win(27, 2)}`);
    // grid: a pylon and a lightning bolt per step, blue while drawing, PV yellow while feeding in
    const gs = this._step("n:grid", v.grid, base), gc = !gs ? off : v.grid > 0 ? P.grid : P.pv;
    const bolt = (x, i) => `<path d="M${x + 2} 6 L${x - 2} 13 H${x + 1} L${x - 1} 19 L${x + 4} 11 H${x + 1} Z" fill="${i < gs ? gc : "none"}"
      stroke="${i < gs ? gc : off}" stroke-opacity="${i < gs ? 1 : 0.35}" stroke-width="1" stroke-linejoin="round"></path>`;
    const grid = svg(44, `<path d="M7 3 L3 22 M7 3 L11 22 M2 6.5 H12 M3 11 H11 M4.4 11 L10 17 M9.6 11 L4 17" fill="none" stroke="${gc}"
      stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path>${bolt(20, 0)}${bolt(29, 1)}${bolt(38, 2)}`);
    return { pv, bat, home, grid };
  }

  _buildCompact(el, nodes, bus, shape) {
    const N = nodes.length, mid = (i) => ((i + 0.5) / N) * 100;
    const head = (n) => (this._icons ? `<span class="ic" data-ic="${n.key}" title="${n.label}"></span>` : `<span class="l">${n.label}</span>`);
    const cell = (n) => `<div class="f" data-cell="${n.key}"${n.id ? ` data-more="${this._esc(n.id)}"` : ""}>${head(n)}
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
      icons: Object.fromEntries(q("[data-ic]").map((e) => [e.dataset.ic, e])),
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

  // hourly weather over [t0, t1): a symbol at every change of the condition class. A run under two hours, or
  // too narrow for its symbol (in the compressed night), joins the run before it, so every change keeps its
  // symbol; only a run reaching in from the day before may stay too narrow, it keeps its bar without a symbol
  _weatherStrip(t0, t1, x, X0, X1, W) {
    if (!this._show("weather") || !this._wx || !this._wx.length) return "";
    const P = this._p, H = 3600, SIZE = 14, MIN = SIZE + 4;
    const hours = this._wx.map((f) => ({ ts: Date.parse(f.datetime) / 1000, cond: f.condition }))
      .filter((f) => f.ts >= t0 - H && f.ts < t1 && f.cond);
    if (!hours.length) return "";
    const runs = [];
    const add = (r) => { const last = runs[runs.length - 1]; if (last && last.cls === r.cls) last.end = r.end; else runs.push(r); };
    for (const f of hours) add({ cls: WX_CLASS[f.cond] || "cloud", cond: f.cond, start: f.ts, end: f.ts + H });
    const width = (r) => Math.min(X1, x(r.end)) - Math.max(X0, x(r.start));
    for (let i = runs.length - 1; i > 0; i--) {
      if (runs[i].end - runs[i].start >= 2 * H && width(runs[i]) >= MIN) continue;
      runs[i - 1].end = runs[i].end;
      runs.splice(i, 1);
      if (runs[i] && runs[i].cls === runs[i - 1].cls) { runs[i - 1].end = runs[i].end; runs.splice(i, 1); }   // same class on both sides
    }
    const color = { sun: P.pv, part: P.pvDark, cloud: "var(--secondary-text-color)", rain: P.grid, snow: P.grid };
    const marks = runs.map((r) => {
      const a = Math.max(X0, x(r.start)), b = Math.min(X1, x(r.end));
      if (b - a < 1) return "";
      const bar = `<line x1="${a.toFixed(1)}" x2="${b.toFixed(1)}" y1="19" y2="19" stroke="${color[r.cls]}" stroke-width="1.6" stroke-opacity=".6" stroke-linecap="round"></line>`;
      return b - a < MIN ? bar : `${bar}${this._icon(WX_ICON[r.cond] || "mdi:weather-cloudy", a + SIZE / 2, 8, SIZE, color[r.cls]) || ""}`;
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
      if (f.tick(dt, reduced) && !reduced) this._frame = requestAnimationFrame(step);
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
    const expM = sum("expected"), fcM = sum("forecast"), mornM = sum("morning"), lossM = sum("loss");
    const exp = (h) => expM.get(h) || 0, loss = (h) => lossM.get(h) || 0;
    const fc = (h) => (fcM.has(h) ? fcM.get(h) : mornM.get(h)) || 0;
    // only hours of the chart's day, so data of another day can never tip the time axis
    const dayT0 = this._data.dayStart / 1000, dayT1 = this._localMidnight(new Date(this._data.dayStart + 30 * H * 1000)) / 1000;
    const sunny = [...new Set([...expM.keys(), ...fcM.keys()])].filter((h) => h >= dayT0 && h < dayT1 && (exp(h) > 1 || fc(h) > 1))
      .sort((a, b) => a - b);
    if (!sunny.length) { el.innerHTML = `<div class="muted">${t.noData}</div>`; return; }
    // production with an hour of margin on either side, so the day does not end abruptly;
    // range "day" shows the whole day from midnight to midnight, the night outside production compressed
    const prodStart = sunny[0], prodEnd = sunny[sunny.length - 1] + H, pS = prodStart - H, pE = prodEnd + H;
    const whole = this._config.range === "day";
    const t0 = whole ? dayT0 : pS, t1 = whole ? dayT1 : pE;
    const dayH = []; for (let h = t0; h < t1; h += H) dayH.push(h);
    const pvH = dayH.filter((h) => h >= prodStart && h < prodEnd);

    const m = this._model(now);
    const load = m.load;
    // the chart takes the full width; the axis labels sit inside it
    const W = 408, X0 = 0, X1 = W, Hp = 104;
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
    // scale labels inside the right edge, just below their line, on top of everything (also the night veil)
    const yLabels = grid.map((v) => `<text x="${X1 - 2}" y="${(y(v) + 8.5).toFixed(1)}" text-anchor="end" class="ax in">${kw(v)}${v === grid[grid.length - 1] ? " kW" : ""}</text>`).join("");
    let svgPv = `${grid.map((v) => `<line x1="${X0}" x2="${X1}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="${P.rail}"></line>`).join("")}
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

    // battery: measured level so far, simulated with expected PV and load until the end of the chart
    const S0 = Ly + 4, Sh = 44, ys = (f) => S0 + Sh - Math.max(0, Math.min(1, f)) * Sh;
    const bat = this._show("battery") ? m.battery : null;
    let batSvg = "", sim = null;
    const seenZones = new Set();
    if (bat) {
      const srv = this._data.today && this._data.today.battery;
      if (srv) {                                   // Noxcast's simulation (batteries configured in the integration)
        bat.max = srv.max; bat.min = srv.min;
        const pts = srv.points;
        sim = { points: pts, full: srv.full, at: (ts) => {
          if (!pts.length) return null;
          if (ts <= pts[0][0]) return pts[0][1];
          for (let i = 1; i < pts.length; i++) if (pts[i][0] >= ts) return pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (ts - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0] || 1);
          return pts[pts.length - 1][1];
        } };
      } else sim = this._simulate(bat, now, t1, exp, load);
      const zone = (h) => {                       // red: at the limit with surplus (lost), orange: within 5 % of it
        const f = h + H <= now ? m.socAt(h + H / 2) : sim.at(h + H / 2);
        if (f === null) return null;
        if (f >= bat.max - 0.01 && exp(h) > lv(h)) return P.lost;
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
      batSvg = `<rect x="${X0}" y="${S0}" width="${X1 - X0}" height="${Sh}" rx="3" fill="${P.rail}"></rect>
        ${past.length > 1 ? `<path d="${line(past)} L${past[past.length - 1][0].toFixed(1)},${S0 + Sh} L${past[0][0].toFixed(1)},${S0 + Sh} Z" fill="${P.bat}" fill-opacity=".45"></path>` : ""}${zones}
        ${fut.length > 1 ? `<path d="${line(fut)}" fill="none" stroke="${P.bat}" stroke-width="1.8" stroke-dasharray="3 3"></path>` : ""}
        ${sim.full !== null && sim.full < t1 ? `<circle cx="${x(sim.full).toFixed(1)}" cy="${ys(bat.max).toFixed(1)}" r="3" fill="var(--card-background-color, #fff)" stroke="${P.bat}" stroke-width="1.6"></circle>` : ""}`;
    }
    const bottom = bat ? S0 + Sh + 14 : Ly + 14;
    const tickStep = Math.max(1, Math.ceil((pE - pS) / H / 6)) * H;
    const ticks = [];
    const tick = (h) => { if (ticks.every((q) => Math.abs(x(q) - x(h)) > 14)) ticks.push(h); };
    if (whole) { tick(t0); tick(t1); }
    for (let h = Math.ceil(pS / tickStep) * tickStep; h <= pE; h += tickStep) tick(h);
    // the compressed night steps back: a veil in the card colour over everything drawn there (darker in a
    // dark theme, lighter in a light one), a faint line where the day begins and ends, a moon on top
    const nightSvg = !whole ? "" : [[t0, pS], [pE, t1]].filter(([a, b]) => b > a).map(([a, b]) => {
      const xa = x(a), xb = x(b), edge = a === t0 ? xb : xa;
      return `<rect x="${xa.toFixed(1)}" y="0" width="${(xb - xa).toFixed(1)}" height="${bottom - 14}" fill="var(--card-background-color, #fff)" fill-opacity=".55"></rect>
        <line x1="${edge.toFixed(1)}" x2="${edge.toFixed(1)}" y1="0" y2="${bottom - 14}" stroke="var(--secondary-text-color)" stroke-opacity=".3"></line>
        ${xb - xa > 12 ? this._icon("mdi:weather-night", (xa + xb) / 2, 7, 10, "var(--secondary-text-color)") || "" : ""}`;
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
      ${svgPv}${batSvg}${nightSvg}${yLabels}
      ${nowX !== null ? `<line x1="${nowX.toFixed(1)}" y1="0" x2="${nowX.toFixed(1)}" y2="${bottom - 14}" stroke="var(--secondary-text-color)" stroke-width="1" stroke-opacity=".6" stroke-dasharray="2 2"></line>` : ""}
      ${ticks.map((h) => { const tx = x(h), anchor = tx < 8 ? "start" : tx > W - 8 ? "end" : "middle";
        return `<text x="${tx.toFixed(1)}" y="${bottom - 3}" text-anchor="${anchor}" class="ax">${whole && h === t1 ? "24" : this._hm(h).slice(0, 2)}</text>`; }).join("")}
      <line id="nx-cursor" y1="0" y2="${bottom - 14}" stroke="var(--primary-text-color)" stroke-width="1" stroke-opacity=".35" visibility="hidden"></line>
      <g id="nx-hits">${hits}</g>
    </svg>`;

    // the numbers of the day
    const after = now >= prodEnd;
    const ist = dayH.reduce((a, h) => a + (h + H <= now ? exp(h) : h < now ? exp(h) * (now - h) / H : 0), 0);
    const expected = dayH.reduce((a, h) => a + exp(h), 0);
    const morning = dayH.reduce((a, h) => a + (mornM.get(h) || 0), 0);
    const tomorrow = arrays.reduce((a, arr) => a + arr.tomorrow_wh, 0);
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

  // arrays of today in the Noxcast order
  _arrays() { return this._data.today.arrays; }

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

  // battery from now until `end`: level points and when it is full
  _simulate(bat, now, end, pv, load) {
    const H = 3600, points = [];
    let e = (bat.now ?? bat.min) * bat.cap, full = null;
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
      } else if (net < 0) e -= Math.min(Math.max(e - eMin, 0), -net);
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
    return { points, full, at };
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
// display settings of the flow diagram, which is gone; dropped when the editor saves
const OBSOLETE_KEYS = ["flow", "icons", "groups", "battery_names"];

class NoxcastCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get _de() { return (this._hass.language || "en").startsWith("de"); }

  _emit(c) {
    for (const k of OBSOLETE_KEYS) if (!(k === "groups" && Array.isArray(c.groups))) delete c[k];
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
      look: de ? "Aussehen" : "Look",
      flow_style: de ? "Fluss zwischen den Werten" : "Flow between the figures",
      labels: de ? "Über den Werten" : "Above the figures", sun_glow: de ? "Sonne leuchtet nach Leistung" : "Sun glows by power",
      battery_arrows: de ? "Pfeile am Akku" : "Arrows at the battery", flow_speed: de ? "Tempo der Punkte" : "Pace of the dots",
      flow_min_w: de ? "Fluss ab (W)" : "Flow from (W)",
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
    const c = this._config;
    const shape = `${this._legacy()}/${this._de}`;
    if (this._shape === shape) {                       // only refresh hass, keeps the focus while typing
      for (const f of this.querySelectorAll("ha-form")) f.hass = this._hass;
      return;
    }
    this._shape = shape;
    const de = this._de;
    this.innerHTML = `<style>
        .nx-sec { margin: 16px 0 4px; font-weight: 500; }
        .nx-btn { background: none; border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 10px;
          color: var(--primary-color); font: inherit; cursor: pointer; }
        .nx-note { color: var(--secondary-text-color); font-size: 13px; margin: 4px 0 8px; }
        .nx-warn { border-left: 3px solid var(--warning-color, #F29900); padding: 6px 10px; margin: 8px 0; font-size: 13px; }
      </style>
      ${this._legacy() ? `<div class="nx-warn">${de
        ? "Diese Karte enthält Sensor-Einstellungen einer früheren Version. Sie werden ignoriert: Die Karte zeigt immer, was in Noxcast konfiguriert ist."
        : "This card has sensor settings of an earlier version. They are ignored: the card always shows what Noxcast is configured with."}
        <div style="margin-top:6px"><button class="nx-btn" id="clean">${de ? "Alte Sensor-Einstellungen entfernen" : "Remove old sensor settings"}</button></div></div>` : ""}
      <div class="nx-note">${de ? "Sensoren, Akkus und Einspeisung kommen aus der Noxcast-Konfiguration. Hier nur, wie sie angezeigt werden."
        : "Sensors, batteries and feed-in come from the Noxcast configuration. Here only how they are shown."}</div>
      <div id="main"></div>`;

    const shows = ["show_battery", "show_effects", "show_band", "show_load", "show_weather"];
    const opt = (pairs) => ({ select: { mode: "dropdown", options: pairs.map(([value, label]) => ({ value, label })) } });
    // grouped by the part of the card they change; flatten keeps the config keys at the top level
    const group = (title, schema, expanded) => ({ type: "expandable", name: "", flatten: true, title, expanded, schema });
    const bool = (name) => ({ name, selector: { boolean: {} } });
    this.querySelector("#main").appendChild(this._form([
      group(de ? "Leistung jetzt" : "Power now", [
        { name: "labels", selector: opt([["icons", de ? "Symbole, die den Zustand zeigen" : "Icons that show the state"],
          ["text", de ? "Namen, Akku mit Leistung" : "Names, battery with power"]]) },
        bool("sun_glow"), bool("battery_arrows"),
        { name: "flow_style", selector: opt([["chain", de ? "Kette: Punkte zwischen den Werten" : "Chain: dots between the figures"],
          ["bus", de ? "Bus: Linie unter den Werten" : "Bus: line below the figures"]]) },
        { name: "flow_speed", selector: opt([["calm", de ? "ruhig" : "calm"], ["normal", "normal"], ["lively", de ? "lebhaft" : "lively"]]) },
        { name: "flow_min_w", selector: { number: { min: 0, max: 200, step: 1, mode: "box", unit_of_measurement: "W" } } },
      ], true),
      group(de ? "Tagesverlauf" : "Day chart", [
        { name: "range", selector: opt([["production", de ? "Produktion, je 1 h Rand" : "Production, an hour either side"],
          ["day", de ? "Ganzer Tag, Nacht gestaucht" : "Whole day, night compressed"]]) },
        ...shows.map(bool), bool("legend"),
      ]),
      group(de ? "Aussehen" : "Look", [
        { name: "look", selector: opt([["plain", "Home Assistant"], ["noxcast", "Noxcast"]]) },
        { name: "font_scale", selector: { number: { min: 0.8, max: 1.6, step: 0.1, mode: "slider" } } },
      ]),
    ], {
      font_scale: Number(c.font_scale) || 1, legend: c.legend !== false, look: c.look || "plain", range: c.range || "production",
      flow_style: c.flow_style || "chain", flow_speed: c.flow_speed || "normal", flow_min_w: c.flow_min_w ?? FLOW_MIN_W,
      labels: c.labels || (c.battery_style === "power" ? "text" : "icons"), sun_glow: !!c.sun_glow, battery_arrows: !!c.battery_arrows,
      ...Object.fromEntries(shows.map((k) => [k, c[k] !== false])),
    }, (v) => {
      const n = { ...this._config };
      n.font_scale = v.font_scale; n.legend = v.legend;
      if (v.flow_style && v.flow_style !== "chain") n.flow_style = v.flow_style; else delete n.flow_style;
      delete n.battery_style;
      if (v.labels === "text") n.labels = "text"; else delete n.labels;
      for (const k of ["sun_glow", "battery_arrows"]) { if (v[k]) n[k] = true; else delete n[k]; }
      if (v.flow_speed && v.flow_speed !== "normal") n.flow_speed = v.flow_speed; else delete n.flow_speed;
      if (v.flow_min_w !== undefined && v.flow_min_w !== "" && Number(v.flow_min_w) !== FLOW_MIN_W) n.flow_min_w = Number(v.flow_min_w); else delete n.flow_min_w;
      if (v.look && v.look !== "plain") n.look = v.look; else delete n.look;
      if (v.range === "day") n.range = "day"; else delete n.range;
      for (const k of shows) { if (v[k] === false) n[k] = false; else delete n[k]; }
      if (n.legend !== false) delete n.legend;
      if (n.font_scale === 1) delete n.font_scale;
      this._emit(n);
    }));

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
