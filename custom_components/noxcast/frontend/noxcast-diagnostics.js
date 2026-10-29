// Noxcast panel section "Diagnostics": data quality of every sensor Noxcast reads, learning runs,
// weather source, archive and excluded periods. Answers "why does it not learn?" without logs.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/diagnostics".

// payload schema this module reads (WS_SCHEMA_VERSION in const.py): another one means the integration was
// updated while this page was open, and the module asks for a reload instead of misreading the data
const WS_SCHEMA = 1;

const DIAG_TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden",
    ok: "Alles in Ordnung", problems: "Hinweise", weather: "Wetterquelle", learning: "Letzter Lernlauf", archive: "Prognose-Archiv", fine: "5-Minuten-Archiv", fineSub: "je Sensor, für schärfere Schatten, max. 3 Jahre",
    sensors: "Sensoren", arrays: "Gelernte Flächen", exclusions: "Ausgeschlossene Zeiträume", none: "keine",
    entity: "Entität", role: "Rolle", state: "Zustand", stats: "Statistik", coverage: "Abdeckung", since: "seit",
    days: "Tage", complete: "vollständig", hours: "Stunden", samples: "Stunden gelernt", mapCoverage: "Karte", efficiency: "Wirkungsgrad",
    ago: "vor", min: "min", h: "h", never: "noch nie", refresh: "Aktualisieren",
    help: "Abdeckung = Anteil der Stunden der letzten {days} Tage mit Langzeitstatistik. Unter 90 % lernt Noxcast mit Lücken; ohne Statistik (state_class fehlt) gar nicht.",
    roles: { pv: "PV-Leistung", curtailment_soc: "Ladestand (Abregelung)", house_load: "Hausverbrauch", consumer_power: "Verbraucher Leistung",
      consumer_energy: "Verbraucher Energie", remaining_time: "Restlaufzeit", elapsed_time: "Laufzeit", battery_soc: "Akku Ladestand",
      battery_max: "Akku Ladegrenze", battery_min: "Akku Entladegrenze" },
    issues: { missing: "existiert nicht", unavailable: "ist nicht verfügbar", noStats: "hat keine Langzeitstatistik (state_class fehlt?)",
      lowCoverage: "Statistik nur zu {p} vorhanden", learnError: "Lernlauf fehlgeschlagen: {e}", archiveError: "Archiv {n}: {e}",
      weatherStale: "Wetterprognose seit {t} nicht aktualisiert", noLearning: "Noch kein Lernlauf" },
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    ok: "All good", problems: "Notes", weather: "Weather source", learning: "Last learning run", archive: "Forecast archive", fine: "5-minute archive", fineSub: "per sensor, for sharper shading, at most 3 years",
    sensors: "Sensors", arrays: "Learned arrays", exclusions: "Excluded periods", none: "none",
    entity: "Entity", role: "Role", state: "State", stats: "Statistics", coverage: "Coverage", since: "since",
    days: "days", complete: "complete", hours: "hours", samples: "hours learned", mapCoverage: "Map", efficiency: "Efficiency",
    ago: "", min: "min ago", h: "h ago", never: "never", refresh: "Refresh",
    help: "Coverage = share of the hours of the last {days} days with long-term statistics. Below 90 % Noxcast learns with gaps; without statistics (missing state_class) not at all.",
    roles: { pv: "PV power", curtailment_soc: "State of charge (curtailment)", house_load: "House load", consumer_power: "Consumer power",
      consumer_energy: "Consumer energy", remaining_time: "Remaining time", elapsed_time: "Elapsed time", battery_soc: "Battery level",
      battery_max: "Battery upper limit", battery_min: "Battery lower limit" },
    issues: { missing: "does not exist", unavailable: "is unavailable", noStats: "has no long-term statistics (missing state_class?)",
      lowCoverage: "statistics only for {p} of the hours", learnError: "Learning failed: {e}", archiveError: "Archive {n}: {e}",
      weatherStale: "Weather forecast not updated for {t}", noLearning: "No learning run yet" },
  },
};

const STAT_ROLES = new Set(["pv", "curtailment_soc", "house_load", "consumer_power", "consumer_energy", "battery_soc"]);

class NoxcastDiagnostics extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (ev) => { if (ev.composedPath().some((n) => n.dataset && n.dataset.refresh)) this._load(); });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
  }

  connectedCallback() { if (this._hass && this._data) this._load(); }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return DIAG_TEXT[(this._hass.language || "en").slice(0, 2)] || DIAG_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      // a request lost while the connection is rebuilt never answers: give up after 20 s and try again
      const data = await Promise.race([this._hass.connection.sendMessagePromise({ type: "noxcast/diagnostics" }),
        new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 20000))]);
      if (data.v !== WS_SCHEMA) { this._data = null; this._error = this._t.outdated; this._render(); return; }
      this._data = data;
      this._error = null;
    } catch (err) {
      this._error = String((err && err.message) || err);
      clearTimeout(this._retry);                // Noxcast may still be starting after a restart
      this._retry = setTimeout(() => { if (this.isConnected) this._load(); }, 10000);
    }
    this._render();
  }

  _issues(e) {
    const t = this._t.issues, out = [];
    for (const s of e.sensors) {
      const name = `<span class="mono">${this._esc(s.entity_id)}</span>`;
      if (s.state === null) out.push(["bad", `${name} ${t.missing}`]);
      else if (s.state === "unavailable") out.push(["warn", `${name} ${t.unavailable}`]);
      if (STAT_ROLES.has(s.role) && s.state !== null) {
        if (!s.statistics) out.push(["bad", `${name} ${t.noStats}`]);
        else if (s.coverage !== null && s.coverage < 0.9) out.push(["warn", `${name}: ${t.lowCoverage.replace("{p}", this._pct(s.coverage))}`]);
      }
    }
    if (!e.learning.last) out.push(["warn", t.noLearning]);
    if (e.learning.error) out.push(["bad", t.learnError.replace("{e}", this._esc(e.learning.error))]);
    for (const [n, err] of Object.entries(e.learning.archive_errors || {})) out.push(["warn", t.archiveError.replace("{n}", n).replace("{e}", this._esc(err))]);
    if (e.weather.updated && Date.now() - Date.parse(e.weather.updated) > 2 * 3600000) out.push(["warn", t.weatherStale.replace("{t}", this._ago(e.weather.updated))]);
    return out;
  }

  _render() {
    const t = this._t;
    let body;
    if (this._error) body = `<section class="card pad">${this._esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else body = this._data.entries.map((e) => this._entry(e)).join("");
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">
      <div class="bar"><button class="chip" data-refresh="1">${t.refresh}</button></div>${body}</div>`;
  }

  _entry(e) {
    const t = this._t, issues = this._issues(e);
    const tiles = [
      [t.weather, e.weather.updated ? this._ago(e.weather.updated) : t.never, `Open-Meteo ${this._esc((e.weather.models || []).join(" + "))} · ${e.weather.hours} ${t.hours}`],
      [t.learning, e.learning.last ? this._ago(e.learning.last) : t.never, e.learning.last ? this._dt(e.learning.last) : ""],
      [t.archive, `${e.archive.days} ${t.days}`, `${e.archive.complete} ${t.complete}`],
      [t.fine, `${(e.fine || {}).days || 0} ${t.days}`, t.fineSub],
    ].map(([k, v, sub]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span><span class="muted small">${sub}</span></div>`).join("");
    const list = issues.length
      ? `<ul class="issues">${issues.map(([lvl, text]) => `<li class="${lvl}"><span class="dot"></span><span>${text}</span></li>`).join("")}</ul>`
      : `<p class="okline"><span class="dot ok"></span>${t.ok}</p>`;
    const owners = [...new Set(e.sensors.map((s) => s.owner))];
    const rows = owners.map((o) => `<tr class="group"><td colspan="5">${this._esc(o)}</td></tr>${e.sensors.filter((s) => s.owner === o).map((s) => `
      <tr><td><span class="mono">${this._esc(s.entity_id)}</span><div class="muted small">${t.roles[s.role] || s.role}</div></td>
        <td class="num">${s.state === null ? "–" : `${this._esc(s.state)} ${this._esc(s.unit || "")}`}</td>
        <td class="muted small">${this._esc(s.state_class || "")}</td>
        <td>${STAT_ROLES.has(s.role) ? (s.statistics ? this._meter(s.coverage) : `<span class="bad">✕</span>`) : ""}</td>
        <td class="muted small">${s.first ? `${t.since} ${this._d(s.first)}` : ""}</td></tr>`).join("")}`).join("");
    const arrays = e.arrays.map((a) => `<tr><td>${this._esc(a.name)}</td><td class="num">${(a.samples || 0).toLocaleString(this._lang)}</td>
      <td class="num">${a.coverage === null ? "–" : this._pct(a.coverage)}</td><td class="num">${a.efficiency === null ? "–" : this._pct(a.efficiency)}</td></tr>`).join("");
    const excl = e.exclusions.length
      ? `<ul class="plain">${e.exclusions.map((x) => `<li>${this._d(x.start)} – ${this._d(new Date(Date.parse(x.end) - 1000).toISOString())}${x.note ? ` · <span class="muted">${this._esc(x.note)}</span>` : ""}</li>`).join("")}</ul>`
      : `<p class="muted">${t.none}</p>`;
    return `
      <section class="card pad"><div class="figs">${tiles}</div></section>
      <section class="card pad"><h3>${t.problems}</h3>${list}</section>
      <section class="card pad"><h3>${t.sensors}</h3>
        <table><thead><tr><th>${t.entity}</th><th class="num">${t.state}</th><th></th><th>${t.coverage}</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        <p class="help">${t.help.replace("{days}", this._data.days)}</p></section>
      <div class="split">
        <section class="card pad grow"><h3>${t.arrays}</h3><table><thead><tr><th></th><th class="num">${t.samples}</th><th class="num">${t.mapCoverage}</th><th class="num">${t.efficiency}</th></tr></thead><tbody>${arrays}</tbody></table></section>
        <section class="card pad grow"><h3>${t.exclusions}</h3>${excl}</section>
      </div>`;
  }

  _meter(v) {
    if (v === null || v === undefined) return "";
    const c = v >= 0.9 ? "var(--nx-good)" : v >= 0.5 ? "var(--nx-warn)" : "var(--nx-bad)";
    return `<span class="meterwrap"><span class="meter"><span style="width:${Math.min(100, v * 100).toFixed(0)}%;background:${c}"></span></span>${this._pct(v)}</span>`;
  }

  _pct(v) { return `${Math.round(v * 100)} %`; }
  _d(iso) { return new Date(iso).toLocaleDateString(this._lang, { timeZone: this._data.time_zone }); }
  _dt(iso) { return new Date(iso).toLocaleString(this._lang, { timeZone: this._data.time_zone, dateStyle: "short", timeStyle: "short" }); }
  _ago(iso) {
    const t = this._t, m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
    return m < 120 ? `${t.ago} ${m} ${t.min}`.trim() : `${t.ago} ${Math.round(m / 60)} ${t.h}`.trim();
  }
  _esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --nx-good: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-warn: ${d ? "#E0A43C" : "#C98A12"}; --nx-bad: ${d ? "#E0574A" : "#C83A2E"};
        --nx-empty: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.06)"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .bar { display: flex; justify-content: flex-end; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
      .grow { flex: 1 1 380px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); overflow-x: auto; }
      .pad { padding: 16px; }
      h3 { margin: 0 0 8px; font-size: 15px; font-weight: 500; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; } .mono { font-family: var(--code-font-family, monospace); font-size: 12px; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .figs { display: flex; flex-wrap: wrap; gap: 12px 40px; }
      .fig { display: flex; flex-direction: column; gap: 2px; }
      .big { font-size: 20px; font-weight: 500; }
      .chip { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 16px;
        padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
      .issues { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
      .issues li { display: flex; gap: 8px; align-items: baseline; }
      .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; background: var(--nx-warn); }
      .issues .bad .dot { background: var(--nx-bad); } .dot.ok { background: var(--nx-good); }
      .okline { margin: 0; display: flex; gap: 8px; align-items: center; font-size: 13px; }
      .bad { color: var(--nx-bad); }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 12px; padding: 4px 6px; }
      td { padding: 6px; border-top: 1px solid var(--divider-color); vertical-align: top; }
      tr.group td { font-weight: 500; padding-top: 12px; border-top: none; }
      .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .meterwrap { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; white-space: nowrap; }
      .meter { width: 70px; height: 6px; border-radius: 3px; background: var(--nx-empty); overflow: hidden; display: inline-block; }
      .meter span { display: block; height: 100%; }
      .plain { margin: 0; padding-left: 18px; font-size: 13px; }
    </style>`;
  }
}

customElements.define("noxcast-diagnostics", NoxcastDiagnostics);
