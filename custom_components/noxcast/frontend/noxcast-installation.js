// Noxcast panel section "Installation": the installation as a flow diagram (PV arrays -> systems -> house -> grid,
// drawn by the backend, flowchart.py) and, for the node clicked, its settings and how far Noxcast already computes
// each: exact, simplified or not yet. There a PV array, a battery or a system can be moved to another system
// ("noxcast/move_part", the same rules as the forms).
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
    settings: { feed_in: "Einspeisung", old_soc: "Akku-Ladestände, die das Lernen aller PV-Gruppen auslassen",
      one_storage: "Akkus werden als ein Speicher simuliert", output_limit: "Ausgangsgrenze",
      limit_per_phase: "Grenze gilt pro Phase", phase: "Phase", regulation: "Regelung", fixed_power: "Feste Leistung",
      when_full: "Wenn der Akku voll ist", charges_from: "Akku lädt aus", balance: "Akkus gleichen sich aus",
      sensor_limit: "Jeder Sensor liefert höchstens", full_learning: "PV-Gruppen, deren Lernen Stunden mit vollem Akku auslässt",
      charge_learning: "Stunden mit größter Ladeleistung zählen als „mindestens“" },
    values: { free: "frei", none: "keine (Nulleinspeisung)", all: "gibt alles ab", load: "deckt den Hausverbrauch", fixed: "feste Leistung",
      own_pv: "eigener PV", house: "dem Haushalt", l1: "L1", l2: "L2", l3: "L3", three: "dreiphasig", yes: "ja", no: "nein",
      whenFull: { all: "alles abgeben", load: "weiter nur den Verbrauch decken" },
      chargeMissing: "braucht Sensor Akkuleistung und max. Ladeleistung" },
    battery: "Akku", array: "PV-Gruppe", sensors: "{n} Sensoren", sensor: "1 Sensor", sub: "Teilsystem",
    empty: "enthält noch nichts", pick: "Auf einen Knoten im Bild tippen, um seine Einstellungen zu sehen.",
    openEntry: "Einträge öffnen", noSettings: "Keine Einstellungen, die Noxcast verrechnet.",
    belongs: "Gehört zu", batteryBelongs: "Akku gehört zu", alone: "– eigenständig am Haushalt –",
    save: "Zuordnung speichern", saving: "Speichert …", notPossible: "Dorthin kann der Teil nicht.",
    moveHelp: "Noxcast lädt danach neu, wie nach dem Bearbeiten eines Eintrags.",
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    refresh: "Refresh", house: "Household", noSystems: "No system entered yet.",
    edit: "Change under Settings → Devices & services → Noxcast.",
    status: { exact: "computed exactly", simplified: "computed simplified", not_yet: "not used yet" },
    help: "Every setting is stored, even where Noxcast does not compute it yet. Later versions compute it more precisely without you entering anything again.",
    templates: { inverter: "Inverter without battery", pv_battery: "PV with battery in one device", ac_battery: "Battery at the house grid",
      coupled: "Coupled systems", group: "Summarize only" },
    settings: { feed_in: "Feed-in", old_soc: "Battery states of charge that leave learning out for all PV arrays",
      one_storage: "Batteries are simulated as one storage", output_limit: "Output limit",
      limit_per_phase: "Limit holds per phase", phase: "Phase", regulation: "Regulation", fixed_power: "Fixed power",
      when_full: "When the battery is full", charges_from: "Battery charges from", balance: "Batteries balance each other",
      sensor_limit: "Each sensor delivers at most", full_learning: "PV arrays whose learning leaves out hours with the battery full",
      charge_learning: "Hours charging at the maximum count as \"at least\"" },
    values: { free: "free", none: "none (zero export)", all: "delivers everything", load: "covers the house load", fixed: "fixed power",
      own_pv: "own PV", house: "the household", l1: "L1", l2: "L2", l3: "L3", three: "three-phase", yes: "yes", no: "no",
      whenFull: { all: "deliver everything", load: "keep covering the load only" },
      chargeMissing: "needs the battery power sensor and max charge power" },
    battery: "Battery", array: "PV array", sensors: "{n} sensors", sensor: "1 sensor", sub: "Subsystem",
    empty: "contains nothing yet", pick: "Tap a node in the picture to see its settings.",
    openEntry: "Open entries", noSettings: "No settings Noxcast computes.",
    belongs: "Belongs to", batteryBelongs: "Battery belongs to", alone: "– on its own at the household –",
    save: "Save assignment", saving: "Saving …", notPossible: "The part cannot go there.",
    moveHelp: "Noxcast reloads afterwards, as after editing an entry.",
  },
};

class NoxcastInstallation extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (ev) => {
      const path = ev.composedPath();
      if (path.some((n) => n.dataset && n.dataset.refresh)) { this._load(); return; }
      const save = path.find((n) => n.dataset && n.dataset.save);
      if (save) { this._move(save.dataset.save); return; }
      if (path.some((n) => n.dataset && n.dataset.entries)) {
        history.pushState(null, "", "/config/integrations/integration/noxcast");
        window.dispatchEvent(new CustomEvent("location-changed"));
        return;
      }
      const node = path.find((n) => n.getAttribute && n.getAttribute("data-node"));
      if (node) { this._sel = node.getAttribute("data-node"); this._render(); }
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
      const data = await ask(this._hass, { type: "noxcast/installation", language: this._lang });
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

  // the moves changed in the selects of the detail panel, one after the other; the answer is the new installation
  async _move(entryId) {
    const changed = [...this.shadowRoot.querySelectorAll("select[data-move]")].filter((s) => s.value !== s.dataset.now);
    if (!changed.length || this._saving) return;
    // the choices stay in the selects while saving and after an error
    this._pending = Object.fromEntries(changed.map((s) => [s.dataset.move, s.value]));
    this._saving = true;
    this._moveError = null;
    this._render();
    let data = null;
    try {
      for (const s of changed) {
        data = await ask(this._hass, { type: "noxcast/move_part", entry_id: entryId, part: s.dataset.move,
          to: s.value, language: this._lang });
        delete this._pending[s.dataset.move];
      }
    } catch (err) {
      this._moveError = err && err.code === "not_possible" ? this._t.notPossible : errorText(err, this._hass.language);
    }
    // the answer holds this entry only: keep the others, and what an earlier move already saved
    if (data && data.v === WS_SCHEMA && this._data) {
      const fresh = Object.fromEntries(data.entries.map((x) => [x.entry_id, x]));
      this._data = { ...this._data, entries: this._data.entries.map((x) => fresh[x.entry_id] || x) };
    }
    this._saving = false;
    this._render();
  }

  // a select for where a part belongs, only where it has somewhere else to go and the user may change it
  _belongs(e, part, label) {
    const m = (e.moves || {})[part];
    const user = this._hass.user;
    if (!m || (user && !user.is_admin)) return "";
    if (!m.to.some(([id]) => id !== m.now)) return "";
    const t = this._t;
    const chosen = (this._pending || {})[part] ?? m.now;
    const opts = m.to.map(([id, name]) => `<option value="${esc(id)}" ${id === chosen ? "selected" : ""}>${
      id ? esc(name) : t.alone}</option>`).join("");
    return `<label class="move"><span class="name">${label}</span>
      <select data-move="${esc(part)}" data-now="${esc(m.now)}">${opts}</select></label>`;
  }

  _w(v) { return `${Number(v).toLocaleString(this._lang, { maximumFractionDigits: 0 })} W`; }

  _value(setting, v) {
    const t = this._t.values;
    if (v === true) return t.yes;
    if (v === false) return t.no;
    if (Array.isArray(v)) return v.map((x) => this._w(x)).join(", ");
    if (["output_limit", "fixed_power"].includes(setting)) return this._w(v);
    if (setting === "charge_learning") return v == null ? t.chargeMissing : this._w(v);
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

  // the node clicked in the diagram: house, a system (with subsystems), or a PV array
  _find(e, id) {
    const t = this._t;
    if (id === "house") return { title: t.house, sub: "", ids: ["house"] };
    const walk = (list, parent) => {
      for (const s of list) {
        if (s.id === id) {
          const sub = [parent ? `${t.sub} · ${parent.name}` : "", t.templates[s.template] || "",
            s.battery ? `${t.battery}: ${s.battery.name}` : ""].filter(Boolean).join(" · ");
          return { title: s.name, sub, ids: [s.id], moves: [[s.id, t.belongs], ...(s.battery ? [[s.battery.id, t.batteryBelongs]] : [])] };
        }
        const a = s.arrays.find((x) => x.id === id);
        if (a) return { title: a.name, sub: `${t.array} · ${s.name} · ${a.sensors === 1 ? t.sensor : t.sensors.replace("{n}", a.sensors)}`, ids: [a.id], moves: [[a.id, t.belongs]] };
        const found = walk(s.children, s);
        if (found) return found;
      }
      return null;
    };
    return walk(e.systems, null);
  }

  _detail(e, sel) {
    const t = this._t;
    const node = sel && this._find(e, sel);
    if (!node) return `<div class="muted small">${t.pick}</div>`;
    const rows = node.ids.map((id) => this._settings(id)).join("");
    const moves = (node.moves || []).map(([id, label]) => this._belongs(e, id, label)).join("");
    return `<div class="head"><span class="title">${esc(node.title)}</span></div>
      ${node.sub ? `<div class="muted small">${esc(node.sub)}</div>` : ""}
      ${rows || `<div class="muted small" style="margin-top:6px">${t.noSettings}</div>`}
      ${moves ? `<div class="moves">${moves}
        <div class="row"><button class="chip" data-save="${esc(e.entry_id)}" ${this._saving ? "disabled" : ""}>${
          this._saving ? t.saving : t.save}</button><span class="muted small">${t.moveHelp}</span></div>
        ${this._moveError ? `<div class="error small">${esc(this._moveError)}</div>` : ""}</div>` : ""}`;
  }

  _render() {
    const t = this._t;
    let body;
    if (this._error) body = `<section class="card pad">${esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else body = this._data.entries.map((e) => {
      this._effects = e.effects || [];
      const sel = this._sel ? `<style>[data-node="${CSS.escape(this._sel)}"] > rect { stroke-width: 2.4; }</style>` : "";
      return `<section class="card pad">${sel}
        ${e.systems.length ? `<div class="flow">${e.diagram || ""}</div>` : `<div class="muted">${t.noSystems}</div>`}
        <div class="detail">${this._detail(e, e.systems.length ? this._sel : "house")}</div>
        <p class="help">${t.help} ${t.edit}</p>
        <button class="chip" data-entries="1">${t.openEntry}</button></section>`;
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
      .flow { overflow-x: auto; --nx-text: var(--primary-text-color); --nx-muted: var(--secondary-text-color);
        --nx-line: var(--secondary-text-color); --nx-node: var(--secondary-background-color, var(--card-background-color));
        --nx-frame: var(--secondary-text-color); --nx-font: var(--ha-font-family-body, var(--paper-font-body1_-_font-family));
        ${d ? "--nx-pv: #3d2f16; --nx-pv-line: #e0a43c; --nx-sys: #15362c; --nx-sys-line: #4cc7a0; --nx-limit: #43231a; --nx-limit-line: #e88a68;"
            : "--nx-pv: #fbf1df; --nx-pv-line: #c98a12; --nx-sys: #e3f4ee; --nx-sys-line: #2e9e7b; --nx-limit: #fae8e2; --nx-limit-line: #d0603a;"} }
      .flow svg { display: block; width: 100%; max-width: 680px; height: auto; }
      .detail { border-top: 1px solid var(--divider-color); margin-top: 12px; padding-top: 12px; }
      .settings { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
      .settings li { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
      .settings .name { color: var(--secondary-text-color); }
      .settings .val { font-weight: 500; }
      .st { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--secondary-text-color); }
      .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: var(--nx-off); }
      .st.exact .dot { background: var(--nx-good); } .st.simplified .dot { background: var(--nx-warn); }
      .moves { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; font-size: 13px; }
      .move { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; }
      .move .name { color: var(--secondary-text-color); }
      .move select { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
        background: var(--card-background-color); color: var(--primary-text-color); max-width: 100%; }
      .moves .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
      .error { color: var(--error-color, #db4437); }
      .chip:disabled { opacity: .6; cursor: default; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .chip { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 16px;
        padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
    </style>`;
  }
}

if (!customElements.get("noxcast-installation")) customElements.define("noxcast-installation", NoxcastInstallation);
