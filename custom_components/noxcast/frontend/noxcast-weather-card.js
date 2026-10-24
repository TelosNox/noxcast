// Noxcast weather card: the coming days one below the other, each with the PV yield Noxcast expects
// where its forecast reaches. Plain custom element, no build step.
//
// type: custom:noxcast-weather-card
// entity: weather.home                 # default: the weather entity Noxcast uses
// days: 5                              # 1 to 10; days beyond the Noxcast horizon show weather only
// show_pv: true                        # PV yield per day where Noxcast has a forecast
// temperature: sensor.outdoor_temp     # optional: measured outside values above the days
// humidity: sensor.outdoor_humidity
// name: Außen                          # label of the measured values
// look: noxcast                        # noxcast: the Noxcast panel look; plain: Home Assistant card

const W_TEXT = {
  de: { today: "Heute", tomorrow: "Morgen", outside: "Außen", humidity: "Feuchte", kwh: "kWh", noWeather: "Keine Wetterentität",
    entity: "Wetterentität (leer = die von Noxcast)", days: "Tage", show_pv: "PV-Ertrag pro Tag", temperature: "Außentemperatur (optional)",
    humidity_s: "Außenfeuchte (optional)", name: "Bezeichnung der Außenwerte", look: "Aussehen" },
  en: { today: "Today", tomorrow: "Tomorrow", outside: "Outside", humidity: "humidity", kwh: "kWh", noWeather: "No weather entity",
    entity: "Weather entity (empty = the one Noxcast uses)", days: "Days", show_pv: "PV yield per day", temperature: "Outside temperature (optional)",
    humidity_s: "Outside humidity (optional)", name: "Label of the outside values", look: "Look" },
};

const W_ICON = { sunny: "mdi:weather-sunny", "clear-night": "mdi:weather-night", partlycloudy: "mdi:weather-partly-cloudy",
  cloudy: "mdi:weather-cloudy", fog: "mdi:weather-fog", windy: "mdi:weather-windy", "windy-variant": "mdi:weather-windy-variant",
  rainy: "mdi:weather-rainy", pouring: "mdi:weather-pouring", "lightning-rainy": "mdi:weather-lightning-rainy",
  lightning: "mdi:weather-lightning", hail: "mdi:weather-hail", snowy: "mdi:weather-snowy", "snowy-rainy": "mdi:weather-snowy-rainy",
  exceptional: "mdi:alert-circle-outline" };
const W_COLOR = { sunny: "#F2B233", "clear-night": "#F2B233", partlycloudy: "#C27A06", rainy: "#2F80C8", pouring: "#2F80C8",
  "lightning-rainy": "#2F80C8", snowy: "#2F80C8", "snowy-rainy": "#2F80C8", hail: "#2F80C8" };
const W_REFRESH_MS = 15 * 60 * 1000;
const W_TIMEOUT_MS = 20000;

class NoxcastWeatherCard extends HTMLElement {
  static getStubConfig() { return { days: 5 }; }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", selector: { entity: { domain: "weather" } } },
        { name: "days", selector: { number: { min: 1, max: 10, step: 1, mode: "box" } } },
        { name: "show_pv", selector: { boolean: {} } },
        { name: "temperature", selector: { entity: { domain: "sensor", device_class: "temperature" } } },
        { name: "humidity", selector: { entity: { domain: "sensor", device_class: "humidity" } } },
        { name: "name", selector: { text: {} } },
        { name: "look", selector: { select: { mode: "dropdown", options: [{ value: "noxcast", label: "Noxcast" }, { value: "plain", label: "Home Assistant" }] } } },
      ],
      computeLabel: (s) => {
        const t = W_TEXT[(document.documentElement.lang || "en").slice(0, 2)] || W_TEXT.en;
        return s.name === "humidity" ? t.humidity_s : t[s.name] || s.name;
      },
    };
  }

  setConfig(config) {
    this._config = { days: 5, show_pv: true, look: "noxcast", ...(config || {}) };
    if (this._hass) { this._resubscribe(); this._render(); }
  }

  getCardSize() { return 2 + Math.ceil((Number(this._config.days) || 5) / 2); }
  getGridOptions() { return { columns: 6, min_columns: 3, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (first) { this._loadSource(); this._refresh(); }
    const sig = [this._config.temperature, this._config.humidity].map((id) => id && hass.states[id] && hass.states[id].state).join("|")
      + (hass.themes && hass.themes.darkMode);
    if (sig !== this._sig) { this._sig = sig; this._render(); }
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._refresh(), W_REFRESH_MS);
    if (this._hass) { this._resubscribe(); this._refresh(); }
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    this._unsubscribe();
  }

  get _t() { return W_TEXT[(this._hass.language || "en").slice(0, 2)] || W_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }
  get _entity() { return this._config.entity || this._srcWeather; }

  _ask(msg) {
    return Promise.race([this._hass.connection.sendMessagePromise(msg),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), W_TIMEOUT_MS))]);
  }

  async _loadSource() {
    try {
      const src = await this._ask({ type: "noxcast/card_config" });
      this._srcWeather = src && src.weather;
    } catch (err) {
      this._srcWeather = null;
    }
    this._resubscribe();
  }

  // PV per day from Noxcast; without Noxcast (or an older version) the card shows weather only
  async _refresh() {
    if (!this._hass || !this._config.show_pv) return;
    try {
      const today = await this._ask({ type: "noxcast/today" });
      this._pv = new Map((today.days || []).map((d) => [d.date, d]));
      this._tz = today.time_zone;
    } catch (err) {
      setTimeout(() => { if (this.isConnected) this._refresh(); }, 30000);   // Noxcast may still be starting
    }
    this._render();
  }

  async _resubscribe() {
    const id = this._entity;
    if (!id || !this._hass || !this.isConnected || this._subFor === id) return;
    this._unsubscribe();
    this._subFor = id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((msg) => { this._days = msg.forecast || []; this._render(); },
        { type: "weather/subscribe_forecast", forecast_type: "daily", entity_id: id });
    } catch (err) {
      this._subFor = null;
      this._days = null;
      this._render();
    }
  }

  _unsubscribe() {
    if (this._unsub) { try { this._unsub(); } catch (err) { /* connection already gone */ } }
    this._unsub = null;
    this._subFor = null;
  }

  _esc(s) { return String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])); }

  // local calendar date of a timestamp in the Home Assistant time zone
  _date(ms) {
    const tz = this._tz || this._hass.config.time_zone;
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
  }

  _num(v, d = 0) { return Number(v).toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d }); }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const t = this._t, c = this._config, dark = this._hass.themes && this._hass.themes.darkMode;
    const st = (id) => (id && this._hass.states[id]) || null;
    const temp = st(c.temperature), hum = st(c.humidity);
    const tv = temp && !isNaN(parseFloat(temp.state)) ? parseFloat(temp.state) : null;
    const hv = hum && !isNaN(parseFloat(hum.state)) ? parseFloat(hum.state) : null;
    const today = this._date(Date.now()), tomorrow = this._date(Date.now() + 86400000);
    const pvColor = dark ? "#F2B233" : "#9A6400";
    const rows = (this._days || []).filter((f) => this._date(Date.parse(f.datetime)) >= today).slice(0, Number(c.days) || 5).map((f) => {
      const date = this._date(Date.parse(f.datetime));
      const label = date === today ? t.today : date === tomorrow ? t.tomorrow
        : new Date(f.datetime).toLocaleDateString(this._lang, { weekday: "short", timeZone: this._tz || this._hass.config.time_zone });
      const pv = c.show_pv && this._pv && this._pv.get(date);
      const rain = Number(f.precipitation) >= 0.5 ? `${this._num(f.precipitation, Number(f.precipitation) < 10 ? 1 : 0)} mm` : "";
      return `<div class="day">
        <span class="d">${this._esc(label)}</span>
        <ha-icon icon="${W_ICON[f.condition] || "mdi:weather-cloudy"}" style="color:${W_COLOR[f.condition] || "var(--secondary-text-color)"}"></ha-icon>
        <span class="tmp">${f.temperature == null ? "–" : `${Math.round(f.temperature)}°`} <span class="lo">${f.templow == null ? "" : `${Math.round(f.templow)}°`}</span></span>
        <span class="rain">${rain}</span>
        <span class="pv" style="color:${pvColor}">${pv ? `${this._num(pv.wh / 1000, 1)} <small>${t.kwh}</small>` : ""}</span>
      </div>`;
    }).join("");
    const head = temp || hum ? `<div class="head">
        <span class="lbl">${this._esc(c.name || t.outside)}</span>
        ${tv === null ? "" : `<span class="now">${this._num(tv, 1)}°</span>`}
        ${hv === null ? "" : `<span class="hum">${this._num(hv)} % ${t.humidity}</span>`}
      </div>` : "";
    const body = this._entity ? rows : `<div class="muted">${t.noWeather}</div>`;
    this.shadowRoot.innerHTML = `<style>
      ha-card { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
      ha-card.nx { border-radius: 20px; border: 1px solid transparent;
        background: linear-gradient(var(--ha-card-background, var(--card-background-color, #fff)), var(--ha-card-background, var(--card-background-color, #fff))) padding-box,
          linear-gradient(135deg, rgba(245,165,36,.55), rgba(127,127,127,.14) 28%, rgba(127,127,127,.14) 72%, rgba(139,92,246,.55)) border-box;
        box-shadow: 0 18px 40px -24px rgba(40,24,80,.35); }
      .head { display: flex; flex-direction: column; }
      .lbl { font-size: 12px; font-weight: 600; letter-spacing: .09em; text-transform: uppercase; color: var(--secondary-text-color); }
      .now { font-size: 44px; font-weight: 500; letter-spacing: -.02em; line-height: 1.1; }
      .hum { font-size: 17px; color: var(--secondary-text-color); }
      .day { display: grid; grid-template-columns: 4.2em 28px 1fr auto auto; align-items: center; gap: 8px; min-height: 44px;
        border-top: 1px solid var(--divider-color); font-size: 16px; }
      .day:first-child { border-top: none; }
      .d { color: var(--secondary-text-color); }
      ha-icon { --mdc-icon-size: 24px; display: flex; }
      .lo { color: var(--secondary-text-color); }
      .rain { font-size: 12px; color: #2F80C8; white-space: nowrap; }
      .pv { font-weight: 500; white-space: nowrap; font-variant-numeric: tabular-nums; text-align: right; min-width: 3.6em; }
      .pv small { font-weight: 400; font-size: 11px; }
      .muted { color: var(--secondary-text-color); }
    </style>
    <ha-card class="${c.look === "plain" ? "" : "nx"}">${head}${body}</ha-card>`;
  }
}

customElements.define("noxcast-weather-card", NoxcastWeatherCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "noxcast-weather-card", name: "Noxcast Wetter",
  description: "The coming days one below the other, with the PV yield Noxcast expects per day", preview: true });
