// Noxcast panel section "Installation": the installation as entered (household -> systems -> subsystems -> PV arrays
// and battery) and, per setting, how far Noxcast already computes it: exact, simplified or not yet.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/installation".
const { WS_SCHEMA, ask, errorText, esc } = await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href);

const INST_TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden",
    refresh: "Aktualisieren", house: "Haushalt", noSystems: "Noch kein System eingetragen.",
    edit: "Ändern unter Einstellungen → Geräte & Dienste → Noxcast.",
    status: { exact: "rechnet genau", simplified: "rechnet vereinfacht", not_yet: "noch nicht berücksichtigt" },
    help: "Jede Angabe wird gespeichert, auch wenn Noxcast sie noch nicht verrechnet. Spätere Versionen rechnen sie genauer, ohne dass du etwas neu eintragen musst.",
    templates: { inverter: "Wechselrichter ohne Akku", pv_battery: "PV mit Akku in einem Gerät", ac_battery: "Akku am Hausnetz",
      coupled: "Gekoppelte Systeme", group: "Nur zusammenfassen" },
    settings: { feed_in: "Einspeisung", old_soc: "Akku-Ladestände (alte Einstellung, für alle PV-Gruppen)", output_limit: "Ausgangsgrenze",
      limit_per_phase: "Grenze gilt pro Phase", phase: "Phase", regulation: "Regelung", fixed_power: "Feste Leistung",
      when_full: "Wenn der Akku voll ist", charges_from: "Akku lädt aus", balance: "Akkus gleichen sich aus",
      sensor_limit: "Jeder Sensor liefert höchstens" },
    values: { free: "frei", none: "keine (Nulleinspeisung)", all: "gibt alles ab", load: "deckt den Hausverbrauch", fixed: "feste Leistung",
      own_pv: "eigener PV", house: "dem Haushalt", l1: "L1", l2: "L2", l3: "L3", three: "dreiphasig", yes: "ja", no: "nein",
      whenFull: { all: "alles abgeben", load: "weiter nur den Verbrauch decken" } },
    battery: "Akku", array: "PV-Gruppe", sensors: "{n} Sensoren", sensor: "1 Sensor", sub: "Teilsystem",
    empty: "enthält noch nichts",
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    refresh: "Refresh", house: "Household", noSystems: "No system entered yet.",
    edit: "Change under Settings → Devices & services → Noxcast.",
    status: { exact: "computed exactly", simplified: "computed simplified", not_yet: "not used yet" },
    help: "Every setting is stored, even where Noxcast does not compute it yet. Later versions compute it more precisely without you entering anything again.",
    templates: { inverter: "Inverter without battery", pv_battery: "PV with battery in one device", ac_battery: "Battery at the house grid",
      coupled: "Coupled systems", group: "Summarize only" },
    settings: { feed_in: "Feed-in", old_soc: "Battery states of charge (old setting, for all PV arrays)", output_limit: "Output limit",
      limit_per_phase: "Limit holds per phase", phase: "Phase", regulation: "Regulation", fixed_power: "Fixed power",
      when_full: "When the battery is full", charges_from: "Battery charges from", balance: "Batteries balance each other",
      sensor_limit: "Each sensor delivers at most" },
    values: { free: "free", none: "none (zero export)", all: "delivers everything", load: "covers the house load", fixed: "fixed power",
      own_pv: "own PV", house: "the household", l1: "L1", l2: "L2", l3: "L3", three: "three-phase", yes: "yes", no: "no",
      whenFull: { all: "deliver everything", load: "keep covering the load only" } },
    battery: "Battery", array: "PV array", sensors: "{n} sensors", sensor: "1 sensor", sub: "Subsystem",
    empty: "contains nothing yet",
  },
};

class NoxcastInstallation extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (ev) => {
      if (ev.composedPath().some((n) => n.dataset && n.dataset.refresh)) this._load();
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }

  connectedCallback() { if (this._hass && this._data) this._load(); }

  get _t() { return INST_TEXT[(this._hass.language || "en").slice(0, 2)] || INST_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      const data = await ask(this._hass, { type: "noxcast/installation" });
      if (data.v !== WS_SCHEMA) { this._data = null; this._error = this._t.outdated; this._render(); return; }
      this._data = data;
      this._error = null;
    } catch (err) {
      this._error = errorText(err, this._hass.language);
      clearTimeout(this._retry);                // Noxcast may still be starting after a restart
      this._retry = setTimeout(() => { if (this.isConnected) this._load(); }, 10000);
    }
    this._render();
  }

  _w(v) { return `${Number(v).toLocaleString(this._lang, { maximumFractionDigits: 0 })} W`; }

  _value(setting, v) {
    const t = this._t.values;
    if (v === true) return t.yes;
    if (v === false) return t.no;
    if (Array.isArray(v)) return v.map((x) => this._w(x)).join(", ");
    if (["output_limit", "fixed_power"].includes(setting)) return this._w(v);
    if (setting === "feed_in" && typeof v === "number") return `≤ ${this._w(v)}`;
    if (setting === "when_full") return t.whenFull[v] || esc(v);
    if (setting === "old_soc") return String(v);
    return t[v] || esc(String(v));
  }

  // the settings of one node, each with how far it counts
  _settings(node) {
    const t = this._t;
    const rows = this._effects.filter((e) => e.node === node);
    if (!rows.length) return "";
    return `<ul class="settings">${rows.map((e) => `<li><span class="name">${t.settings[e.setting] || esc(e.setting)}</span>
      <span class="val">${this._value(e.setting, e.value)}</span>
      <span class="st ${e.status}" title="${t.status[e.status]}"><span class="dot"></span>${t.status[e.status]}</span></li>`).join("")}</ul>`;
  }

  _system(s, depth) {
    const t = this._t;
    const parts = [
      ...s.arrays.map((a) => `<div class="part"><ha-icon icon="mdi:solar-panel"></ha-icon><div><div>${esc(a.name)}</div>
        <div class="muted small">${t.array} · ${a.sensors === 1 ? t.sensor : t.sensors.replace("{n}", a.sensors)}</div>
        ${this._settings(a.id)}</div></div>`),
      s.battery ? `<div class="part"><ha-icon icon="mdi:battery-high"></ha-icon><div><div>${esc(s.battery.name)}</div>
        <div class="muted small">${t.battery}</div></div></div>` : "",
      ...s.children.map((c) => this._system(c, depth + 1)),
    ].filter(Boolean);
    return `<div class="system ${s.template === "group" ? "loose" : ""} ${depth ? "sub" : ""}">
      <div class="head"><span class="title">${esc(s.name)}</span>
        <span class="muted small">${depth ? `${t.sub} · ` : ""}${t.templates[s.template] || ""}</span></div>
      ${this._settings(s.id)}
      <div class="parts">${parts.length ? parts.join("") : `<div class="muted small">${t.empty}</div>`}</div></div>`;
  }

  _render() {
    const t = this._t;
    let body;
    if (this._error) body = `<section class="card pad">${esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else body = this._data.entries.map((e) => {
      this._effects = e.effects || [];
      return `<section class="card pad house"><div class="head"><ha-icon icon="mdi:home-lightning-bolt"></ha-icon>
        <span class="title">${t.house}</span></div>${this._settings("house")}
        <div class="systems">${e.systems.length ? e.systems.map((s) => this._system(s, 0)).join("") : `<div class="muted">${t.noSystems}</div>`}</div>
        <p class="help">${t.help} ${t.edit}</p></section>`;
    }).join("");
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">
      <div class="bar"><button class="chip" data-refresh="1">${t.refresh}</button></div>${body}</div>`;
  }

  _style() {
    const d = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    return `<style>
      :host { display: block; --nx-good: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-warn: ${d ? "#E0A43C" : "#C98A12"};
        --nx-off: ${d ? "#8A8F98" : "#8A8F98"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .bar { display: flex; justify-content: flex-end; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; }
      .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
      .head ha-icon { --mdc-icon-size: 20px; align-self: center; }
      .title { font-size: 15px; font-weight: 500; }
      .systems { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
      .system { border: 1px solid var(--divider-color); border-radius: 10px; padding: 12px; min-width: 0; }
      .system.loose { border-style: dashed; }
      .system.sub { background: none; }
      .parts { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
      .part { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; }
      .part ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); flex: none; }
      .settings { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
      .settings li { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
      .settings .name { color: var(--secondary-text-color); }
      .settings .val { font-weight: 500; }
      .st { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--secondary-text-color); }
      .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: var(--nx-off); }
      .st.exact .dot { background: var(--nx-good); } .st.simplified .dot { background: var(--nx-warn); }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .chip { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 16px;
        padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
    </style>`;
  }
}

if (!customElements.get("noxcast-installation")) customElements.define("noxcast-installation", NoxcastInstallation);
