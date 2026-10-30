// Noxcast panel section "Day": one day in detail. The groups stacked, forecast against expectation
// and actual, the weather of each hour, and for a chosen hour how its forecast comes about.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/day".
const { WS_SCHEMA, ask, esc, hm, kwh, num, tsRows } = await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href);

const DAY_TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden",
    today: "Heute", yesterday: "Gestern", tomorrow: "Morgen", prev: "Tag zurück", next: "Tag vor",
    dayAhead: "Prognose Vortag", morning: "Prognose 6 Uhr", expected: "Erwartung", actual: "Ist", forecast: "Prognose",
    held: "Prognose laufend", deviation: "Abweichung", peak: "Spitze", band: "Spanne P10–P90", now: "jetzt",
    groups: "Gruppen", share: "Anteil", correction: "Korrektur", noData: "Für diesen Tag liegen keine Daten vor.",
    chart: "Tagesverlauf", weather: "Wetter", clearness: "Klarheit", effects: "Effekte", shade: "Schatten", refl: "Reflexion",
    waterfall: "Wie die Stunde zustande kommt", all: "Alle", hourHint: "Stunde im Diagramm anklicken",
    steps: { clear: "Klarer Himmel", clouds: "Wolken", shade: "Schatten", reflection: "Reflexion", direction: "Richtung", heat: "Wärme",
      result: "Prognose", corr: "Korrektur heute", expected: "Erwartung", actual: "Ist" },
    stepHelp: {
      clear: (eff) => `Was die Fläche bei wolkenlosem Himmel liefern würde, mit dem gelernten Wirkungsgrad von ${eff}.`,
      clouds: "Der Unterschied durch die Einstrahlung der Wetterquelle gegenüber klarem Himmel.",
      shade: "Direktlicht, das die gelernte Schattenkarte für diese Sonnenposition abhält.",
      reflection: "Zusätzliches Licht von beschienenen Flächen in der Umgebung.",
      direction: "Was die Richtungsfaktoren an Geometrie- und Modellfehlern ausgleichen.",
      heat: "Leistungsverlust oder -gewinn durch die Zelltemperatur bei der vorhergesagten Lufttemperatur.",
      corr: "Die Korrektur aus den gemessenen Stunden von heute (klarer oder trüber als vorhergesagt).",
    },
    noSteps: "Für diese Stunde gibt es keine Herleitung (Sonne unter dem Horizont oder kein Wetter).",
    energy: "Der Tag in Energie", lossSide: "Vom klaren Himmel zum Ertrag", useSide: "Wohin die Energie ging",
    pv: "PV", house: "Haus", battery: "Akku", grid: "Netz", houseGrid: "Haus / Netz", selfUse: "Eigenverbrauch", autarky: "Autarkie",
    devActual: "Abweichung Ist", devExpected: "Abweichung Erwartung",
    energyHelp: "Links: die Schritte der Prognose über alle Stunden des Tages summiert, Verluste zweigen nach unten ab, Gewinne kommen von oben dazu; am Ende die Abweichung zum Ist. Rechts: geschätzt aus stündlichen Statistiken von PV, Hauslast und Akku-Ladestand (Änderung × Kapazität). Innerhalb einer Stunde versorgt PV zuerst das Haus, dann den Akku; was fehlt, kommt aus dem Akku und danach aus dem Netz.",
    history: "Wie sich die Prognose entwickelt hat", snapshots: "Stände", firstLast: "Erster / letzter Stand", jump: "Größte Änderung",
    final: "Ergebnis", at: "um", early: "früh", late: "spät",
    historyHelp: "Zu jeder vollen Stunde zwischen 5 und 21 Uhr hält Noxcast die Erwartung für den ganzen Tag fest (gemessene Stunden plus korrigierter Rest). Die Linien zeigen diese Stände von früh (violett) bis spät (gelb), gestrichelt die Prognose vom Vortag. Unten der Tageswert jedes Stands: je schneller er sich dem Ergebnis nähert, desto früher war der Tag absehbar.",
    kwh: "kWh", w: "W",
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    today: "Today", yesterday: "Yesterday", tomorrow: "Tomorrow", prev: "Previous day", next: "Next day",
    dayAhead: "Day-ahead forecast", morning: "Forecast 6 am", expected: "Expected", actual: "Actual", forecast: "Forecast",
    held: "Running forecast", deviation: "Deviation", peak: "Peak", band: "Range P10–P90", now: "now",
    groups: "Groups", share: "Share", correction: "Correction", noData: "No data for this day.",
    chart: "Course of the day", weather: "Weather", clearness: "Clearness", effects: "Effects", shade: "Shade", refl: "Reflection",
    waterfall: "How the hour comes about", all: "All", hourHint: "Click an hour in the chart",
    steps: { clear: "Clear sky", clouds: "Clouds", shade: "Shade", reflection: "Reflection", direction: "Direction", heat: "Heat",
      result: "Forecast", corr: "Correction today", expected: "Expected", actual: "Actual" },
    stepHelp: {
      clear: (eff) => `What the array would give under a cloudless sky, with the learned efficiency of ${eff}.`,
      clouds: "The difference the weather source's irradiance makes compared with a clear sky.",
      shade: "Direct light the learned shading map holds back at this sun position.",
      reflection: "Additional light from sunlit surfaces nearby.",
      direction: "What the direction factors make up for in geometry and model errors.",
      heat: "Loss or gain from the cell temperature at the forecast air temperature.",
      corr: "The correction from today's measured hours (clearer or duller than forecast).",
    },
    noSteps: "No derivation for this hour (sun below the horizon or no weather).",
    energy: "The day in energy", lossSide: "From clear sky to yield", useSide: "Where the energy went",
    pv: "PV", house: "House", battery: "Battery", grid: "Grid", houseGrid: "House / grid", selfUse: "Self-consumption", autarky: "Self-sufficiency",
    devActual: "Deviation actual", devExpected: "Deviation expected",
    energyHelp: "Left: the steps of the forecast summed over all hours of the day; losses branch off downwards, gains join from above; at the end the deviation to the actual value. Right: estimated from hourly statistics of PV, house load and battery state of charge (change × capacity). Within an hour PV supplies the house first, then the battery; what is missing comes from the battery and then from the grid.",
    history: "How the forecast developed", snapshots: "Snapshots", firstLast: "First / last snapshot", jump: "Largest change",
    final: "Result", at: "at", early: "early", late: "late",
    historyHelp: "At every full hour between 5 am and 9 pm Noxcast records the expectation for the whole day (measured hours plus corrected rest). The lines show these snapshots from early (violet) to late (yellow), dashed the day-ahead forecast. Below, the day total of each snapshot: the faster it approaches the result, the earlier the day was foreseeable.",
    kwh: "kWh", w: "W",
  },
};

// smooth path through points without overshoot (monotone cubic, as in the day card), as " C..." segments
function smooth(p) {
  const n = p.length, f = (v) => v.toFixed(1);
  if (n < 3) return p.slice(1).map(([a, b]) => ` L${f(a)},${f(b)}`).join("");
  const m = [], t = [];
  for (let k = 0; k < n - 1; k++) m.push((p[k + 1][1] - p[k][1]) / (p[k + 1][0] - p[k][0] || 1e-9));
  t.push(m[0]);
  for (let k = 1; k < n - 1; k++) t.push(m[k - 1] * m[k] <= 0 ? 0 : (m[k - 1] + m[k]) / 2);
  t.push(m[n - 2]);
  for (let k = 0; k < n - 1; k++) {
    if (m[k] === 0) { t[k] = 0; t[k + 1] = 0; continue; }
    const a = t[k] / m[k], b = t[k + 1] / m[k], q = a * a + b * b;
    if (q > 9) { const tau = 3 / Math.sqrt(q); t[k] = tau * a * m[k]; t[k + 1] = tau * b * m[k]; }
  }
  let out = "";
  for (let k = 0; k < n - 1; k++) {
    const dx = (p[k + 1][0] - p[k][0]) / 3;
    out += ` C${f(p[k][0] + dx)},${f(p[k][1] + t[k] * dx)} ${f(p[k + 1][0] - dx)},${f(p[k + 1][1] - t[k + 1] * dx)} ${f(p[k + 1][0])},${f(p[k + 1][1])}`;
  }
  return out;
}

const DAY_COLORS = ["#F5A524", "#FF6A3D", "#8B5CF6", "#22C7E8", "#2EAA6E", "#E8543C"];
const HOUR = 3600;

class NoxcastDay extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._offset = 0;
    this._hour = null;          // selected hour start
    this._focus = "all";        // waterfall: all groups or one
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  set hass(hass) {
    const first = !this._hass, dark = this._dark;
    this._hass = hass;
    if (first) this._load();
    else if (this._dark !== dark) this._render();
  }

  connectedCallback() { if (this._hass && this._data && Date.now() - this._loaded > 300000) this._load(); }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return DAY_TEXT[(this._hass.language || "en").slice(0, 2)] || DAY_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      // a request lost while the connection is rebuilt never answers: give up after 20 s and try again
      const data = await ask(this._hass, { type: "noxcast/day", offset: this._offset });
      if (data.v !== WS_SCHEMA) { this._data = null; this._error = this._t.outdated; this._render(); return; }
      this._data = data;
      this._loaded = Date.now();
      this._error = null;
      const e = (this._data.entries || [])[0];
      if (this._hour === null || this._hour < this._data.start || this._hour >= this._data.end) this._hour = e ? this._defaultHour(e) : null;
    } catch (err) {
      this._error = String((err && err.message) || err);
      clearTimeout(this._retry);                // Noxcast may still be starting after a restart
      this._retry = setTimeout(() => { if (this.isConnected) this._load(); }, 10000);
    }
    this._render();
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    if (act === "prev" || act === "next" || act === "today") {
      this._offset = act === "today" ? 0 : this._offset + (act === "prev" ? -1 : 1);
      this._hour = null;
      this._load();
    } else if (act === "hour") {
      this._hour = Number(el.dataset.t);
      this._refresh();
    } else if (act === "focus") {
      this._focus = el.dataset.id;
      this._refresh();
    }
  }

  // ------------------------------------------------------------ data helpers

  get _phase() {
    const d = this._data;
    return d.end <= d.now ? "past" : d.start > d.now ? "future" : "today";
  }

  // the main series of an array: actual where measured, expectation (today) or forecast for the rest
  _main(arr) {
    const now = this._data.now, out = new Map();
    const actual = new Map(tsRows(arr.actual, "wh"));
    if (this._phase === "past") return actual;
    const rest = new Map(tsRows(this._phase === "today" ? arr.expected : arr.forecast, "wh"));
    for (const [t, v] of rest) out.set(t, t + HOUR <= now && actual.has(t) ? actual.get(t) : v);
    return out;
  }

  // the reference the day is measured against: morning forecast today, day-ahead forecast otherwise
  _reference(arr) {
    if (this._phase === "today" && arr.morning && arr.morning.t.length) return new Map(tsRows(arr.morning, "wh"));
    if (arr.day_ahead && arr.day_ahead.t.length) return new Map(tsRows(arr.day_ahead, "wh"));
    return this._phase === "future" ? new Map(tsRows(arr.forecast, "wh")) : new Map();
  }

  _sum(m) { let s = 0; for (const v of m.values()) s += v; return s; }

  _hours() {
    const d = this._data, out = [];
    for (let t = d.start; t < d.end; t += HOUR) out.push(t);
    return out;
  }

  _defaultHour(e) {
    const now = this._data.now, hour = now - (now % HOUR);
    if (this._phase === "today" && e.arrays.some((a) => (a.steps || {})[String(hour)])) return hour;
    let best = null, peak = -1;
    for (const t of this._hours()) {
      const v = e.arrays.reduce((a, arr) => a + (this._main(arr).get(t) || 0), 0);
      if (v > peak) { peak = v; best = t; }
    }
    return best;
  }

  // ------------------------------------------------------------ rendering

  _render() {
    const t = this._t, e = this._data && (this._data.entries || [])[0];
    let body;
    if (this._error) body = `<section class="card pad">${esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!e || !e.arrays.some((a) => this._main(a).size)) body = `${this._nav()}<section class="card pad">${t.noData}</section>`;
    else body = `${this._nav()}${this._figures(e)}${this._chartCard(e)}<div class="split">${this._groups(e)}${this._waterfall(e)}</div>${this._energy(e)}${this._history(e)}`;
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">${body}</div>`;
  }

  // a new hour or group only redraws the chart and the waterfall, so the cards do not appear again
  _refresh() {
    const e = this._data && (this._data.entries || [])[0], root = this.shadowRoot;
    const chart = root.getElementById("chartbox"), wf = root.getElementById("wfbody");
    if (!e || !chart || !wf) { this._render(); return; }
    chart.innerHTML = this._chart(e);
    wf.innerHTML = this._waterfallBody(e);
  }

  _nav() {
    const t = this._t, o = this._offset, d = this._data;
    const label = o === 0 ? t.today : o === -1 ? t.yesterday : o === 1 ? t.tomorrow : "";
    const date = new Date(`${d.date}T12:00:00Z`).toLocaleDateString(this._lang, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
    return `<div class="nav">
      <button class="round" data-act="prev" aria-label="${t.prev}" ${o <= -120 ? "disabled" : ""}>‹</button>
      <div class="when"><b>${label || date}</b>${label ? `<span class="muted small">${date}</span>` : ""}</div>
      <button class="round" data-act="next" aria-label="${t.next}" ${o >= 2 ? "disabled" : ""}>›</button>
      ${o !== 0 ? `<button class="chip" data-act="today">${t.today}</button>` : ""}
    </div>`;
  }

  _figures(e) {
    const t = this._t, phase = this._phase;
    const main = e.arrays.reduce((a, arr) => a + this._sum(this._main(arr)), 0);
    const ref = e.arrays.reduce((a, arr) => a + this._sum(this._reference(arr)), 0);
    const refLabel = phase === "today" && e.arrays.some((a) => a.morning && a.morning.t.length) ? t.morning : phase === "future" ? t.forecast : t.dayAhead;
    const mainLabel = phase === "past" ? t.actual : phase === "today" ? t.expected : t.forecast;
    const totals = new Map();
    for (const arr of e.arrays) for (const [h, v] of this._main(arr)) totals.set(h, (totals.get(h) || 0) + v);
    let peakT = null, peak = 0;
    for (const [h, v] of totals) if (v > peak) { peak = v; peakT = h; }
    const band = tsRows(e.band, "p10", "p90");
    const figs = [[mainLabel, `${this._kwh(main)} ${t.kwh}`]];
    if (ref > 0 && phase !== "future") {
      const dev = (main - ref) / ref;
      figs.push([refLabel, `${this._kwh(ref)} ${t.kwh}`], [t.deviation, this._pct(dev)]);
    }
    if (band.length) {
      const lo = band.reduce((a, [, l]) => a + l, 0), hi = band.reduce((a, [, , h]) => a + h, 0);
      const done = phase === "today" ? [...totals].filter(([h]) => !band.some(([b]) => b === h)).reduce((a, [, v]) => a + v, 0) : 0;
      figs.push([t.band, `${this._kwh(done + lo)}–${this._kwh(done + hi)} ${t.kwh}`]);
    }
    if (peakT !== null) figs.push([t.peak, `${this._num(peak / 1000, 2)} kW <span class="muted small">${this._hm(peakT)}</span>`]);
    return `<section class="card pad"><div class="figs">${figs.map(([k, v]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span></div>`).join("")}</div></section>`;
  }

  _chartCard(e) {
    const t = this._t;
    return `<section class="card pad"><h3>${t.chart}</h3><div id="chartbox">${this._chart(e)}</div><p class="help">${t.hourHint}</p></section>`;
  }

  _chart(e) {
    const t = this._t, d = this._data, hours = this._hours(), now = d.now, phase = this._phase;
    const W = 960, X0 = 40, X1 = W - 10, Hp = 240, top = 12;
    const sky = new Map(tsRows(e.weather, "ghi", "clear", "temp").map(([ts, g, c, temp]) => [ts, [g, c, temp]]));
    const lit = hours.filter((h) => e.arrays.some((a) => (this._main(a).get(h) || 0) > 1 || (this._reference(a).get(h) || 0) > 1) || (sky.get(h) && sky.get(h)[1] > 20));
    const h0 = lit.length ? Math.max(d.start, lit[0] - HOUR) : d.start + 5 * HOUR, h1 = lit.length ? Math.min(d.end, lit[lit.length - 1] + 2 * HOUR) : d.end - 3 * HOUR;
    const span = hours.filter((h) => h >= h0 && h < h1);
    const x = (ts) => X0 + (ts - h0) / (h1 - h0) * (X1 - X0);
    const mains = e.arrays.map((a) => this._main(a)), refs = e.arrays.map((a) => this._reference(a));
    const held = e.arrays.map((a) => new Map(tsRows(a.held, "wh")));
    const total = (maps, h) => maps.reduce((s, m) => s + (m.get(h) || 0), 0);
    const band = new Map(tsRows(e.band, "p10", "p90").map(([ts, lo, hi]) => [ts, [lo, hi]]));
    const peakW = e.arrays.reduce((a, arr) => a + (arr.peak_w || 0), 0);
    const max = Math.max(200, ...span.map((h) => Math.max(total(mains, h), total(refs, h), band.has(h) ? band.get(h)[1] : 0))) * 1.08;
    const ymax = Math.min(max, peakW > 0 ? Math.max(peakW, 200) : max);
    const y = (v) => top + (Hp - top) * (1 - Math.min(v, ymax) / ymax);
    const step = [100, 200, 250, 500, 1000, 2000, 5000].find((s) => ymax / s <= 5) || 10000;
    let svg = `<defs>${e.arrays.map((_, i) => `<linearGradient id="nx-g${i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${DAY_COLORS[i % 6]}" stop-opacity=".95"></stop><stop offset="1" stop-color="${DAY_COLORS[i % 6]}" stop-opacity=".55"></stop></linearGradient>`).join("")}
      <clipPath id="nx-past"><rect x="0" y="0" width="${phase === "past" ? W : phase === "future" ? 0 : Math.max(0, x(now)).toFixed(1)}" height="${Hp + 1}"></rect></clipPath>
      <clipPath id="nx-rest"><rect x="${phase === "past" ? W : phase === "future" ? 0 : Math.max(0, x(now)).toFixed(1)}" y="0" width="${W}" height="${Hp + 1}"></rect></clipPath></defs>`;
    for (let v = step; v <= ymax; v += step) svg += `<line x1="${X0}" x2="${X1}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line><text x="${X0 - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${v >= 1000 ? this._num(v / 1000, v % 1000 ? 1 : 0) + " k" : v}</text>`;
    // stacked groups: one band per array, smooth over the hour middles
    const pts = (f) => span.map((h) => [x(h + HOUR / 2), f(h)]);
    let below = span.map(() => 0);
    const layers = mains.map((m, i) => {
      const lo = below, hi = span.map((h, k) => lo[k] + (m.get(h) || 0));
      below = hi;
      const up = hi.map((v, k) => [x(span[k] + HOUR / 2), y(v)]), down = lo.map((v, k) => [x(span[k] + HOUR / 2), y(v)]).reverse();
      const d = `M${up[0][0].toFixed(1)},${up[0][1].toFixed(1)}${smooth(up)} L${down[0][0].toFixed(1)},${down[0][1].toFixed(1)}${smooth(down)} Z`;
      return `<path d="${d}" fill="url(#nx-g${i})" clip-path="url(#nx-past)"></path><path d="${d}" fill="${DAY_COLORS[i % 6]}" fill-opacity=".28" clip-path="url(#nx-rest)"></path>`;
    }).join("");
    if (band.size) {
      const bh = span.filter((h) => band.has(h));
      if (bh.length > 1) {
        const hi = bh.map((h) => [x(h + HOUR / 2), y(band.get(h)[1])]), lo = bh.map((h) => [x(h + HOUR / 2), y(band.get(h)[0])]).reverse();
        svg += `<path d="M${hi[0][0].toFixed(1)},${hi[0][1].toFixed(1)}${smooth(hi)} L${lo[0][0].toFixed(1)},${lo[0][1].toFixed(1)}${smooth(lo)} Z" class="bandarea"></path>`;
      }
    }
    svg += layers;
    const line = (f, cls) => {
      const p = pts(f);
      if (!p.some(([, v]) => v > 0)) return "";
      const q = p.map(([a, v]) => [a, y(v)]);
      return `<path d="M${q[0][0].toFixed(1)},${q[0][1].toFixed(1)}${smooth(q)}" class="${cls}"></path>`;
    };
    svg += line((h) => total(refs, h), "ref");
    if (phase === "today") svg += line((h) => total(held, h), "held");
    // weather strip and effect lanes below the chart
    const Sy = Hp + 18, Sh = 14;
    for (const h of span) {
      const s = sky.get(h);
      if (!s) continue;
      const k = Math.max(0, Math.min(1, s[0] / s[1]));
      svg += `<rect x="${(x(h) + 0.5).toFixed(1)}" y="${Sy}" width="${(x(h + HOUR) - x(h) - 1).toFixed(1)}" height="${Sh}" rx="3" fill="${this._sky(k)}"><title>${this._hm(h)} · ${t.clearness} ${Math.round(k * 100)} %${s[2] != null ? ` · ${this._num(s[2], 0)} °C` : ""}</title></rect>`;
      if (s[2] != null && Number(this._hm(h).slice(0, 2)) % 3 === 0) svg += `<text x="${x(h + HOUR / 2).toFixed(1)}" y="${Sy + Sh + 11}" text-anchor="middle" class="ax">${this._num(s[2], 0)}°</text>`;
    }
    let Ly = Sy + Sh + 18;
    e.arrays.forEach((arr, i) => {
      const per = (arr.periods || []).filter((p) => p.end > h0 && p.start < h1);
      if (!per.length) return;
      svg += `<text x="${X0 - 6}" y="${Ly + 8}" text-anchor="end" class="ax">${esc(arr.name).slice(0, 6)}</text>`;
      for (const p of per) svg += `<rect x="${x(Math.max(p.start, h0)).toFixed(1)}" y="${Ly}" width="${Math.max(2, x(Math.min(p.end, h1)) - x(Math.max(p.start, h0))).toFixed(1)}" height="9" rx="3" class="${p.type === "shade" ? "lshade" : "lrefl"}"><title>${p.type === "shade" ? t.shade : t.refl} ${this._hm(p.start)}–${this._hm(p.end)} · ${this._kwh(p.wh)} ${t.kwh}</title></rect>`;
      Ly += 13;
    });
    const H = Ly + 16;
    for (const h of span) {
      const hr = Number(this._hm(h).slice(0, 2));
      if (hr % 2 === 0) svg += `<text x="${x(h).toFixed(1)}" y="${H - 2}" text-anchor="middle" class="ax">${hr}</text>`;
    }
    if (phase === "today" && now > h0 && now < h1) svg += `<line x1="${x(now).toFixed(1)}" x2="${x(now).toFixed(1)}" y1="${top - 6}" y2="${Sy + Sh}" class="nowline"></line><text x="${(x(now) + 4).toFixed(1)}" y="${top}" class="ax">${t.now}</text>`;
    if (this._hour !== null && this._hour >= h0 && this._hour < h1) svg += `<rect x="${x(this._hour).toFixed(1)}" y="${top - 4}" width="${(x(this._hour + HOUR) - x(this._hour)).toFixed(1)}" height="${Hp - top + 4}" class="sel"></rect>`;
    // hit areas: one per hour
    for (const h of span) {
      const parts = e.arrays.map((a, i) => `${esc(a.name)} ${Math.round(mains[i].get(h) || 0)} W`).join(", ");
      svg += `<rect x="${x(h).toFixed(1)}" y="0" width="${(x(h + HOUR) - x(h)).toFixed(1)}" height="${Sy + Sh}" class="hit" data-act="hour" data-t="${h}"><title>${this._hm(h)}: ${Math.round(total(mains, h))} W (${parts})</title></rect>`;
    }
    const mainLabel = phase === "past" ? t.actual : phase === "today" ? `${t.actual} / ${t.expected}` : t.forecast;
    const refLabel = phase === "today" && e.arrays.some((a) => a.morning && a.morning.t.length) ? t.morning : phase === "future" ? t.forecast : t.dayAhead;
    const legend = [...e.arrays.map((a, i) => `<span><i class="sw" style="background:${DAY_COLORS[i % 6]}"></i>${esc(a.name)}</span>`),
      `<span class="muted">${mainLabel}</span>`,
      phase !== "future" ? `<span><i class="ln dashed"></i>${refLabel}</span>` : "",
      phase === "today" ? `<span><i class="ln dotted"></i>${t.held}</span>` : "",
      band.size ? `<span><i class="sw bandsw"></i>${t.band}</span>` : "",
      `<span><i class="sw skysw"></i>${t.weather}</span>`].join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.chart}">${svg}</svg><div class="legend">${legend}</div>`;
  }

  // the day as energy: the forecast steps from clear sky to the yield, and where the energy went
  _energy(e) {
    const t = this._t, loss = this._lossSankey(e), use = this._useSankey(e.flows);
    if (!loss && !use) return "";
    return `<section class="card pad"><h3>${t.energy}</h3><div class="sankeys">
      ${loss ? `<div class="sk"><div class="muted small sub">${t.lossSide}</div>${loss}</div>` : ""}
      ${use ? `<div class="sk"><div class="muted small sub">${t.useSide}</div>${use}</div>` : ""}</div>
      <p class="help">${t.energyHelp}</p></section>`;
  }

  _lossSankey(e) {
    const t = this._t, phase = this._phase, sum = { clear: 0, clouds: 0, shade: 0, reflection: 0, direction: 0, heat: 0, result: 0 };
    for (const arr of e.arrays) for (const st of Object.values(arr.steps || {})) for (const k in sum) sum[k] += st[k] || 0;
    if (sum.clear < 100) return "";
    const end = phase === "future" ? null : e.arrays.reduce((a, arr) => a + this._sum(this._main(arr)), 0);
    const colors = { clouds: "#94A3B8", shade: "#E8543C", reflection: "#22C7E8", direction: "#8B5CF6", heat: "#FF6A3D", dev: "#2EAA6E" };
    const steps = ["clouds", "shade", "reflection", "direction", "heat"].filter((k) => Math.abs(sum[k]) >= sum.clear * 0.005)
      .map((k) => [k, sum[k], t.steps[k], colors[k]]);
    if (end !== null && Math.abs(end - sum.result) >= sum.clear * 0.005) {
      steps.push(["dev", end - sum.result, phase === "past" ? t.devActual : t.devExpected, end >= sum.result ? colors.dev : colors.shade]);
    }
    const W = 560, X0 = 8, X1 = W - 8, band = 92;
    let cum = sum.clear, peak = cum, gains = 0;
    for (const [, v] of steps) { cum += v; peak = Math.max(peak, cum); if (v > 0) gains += v; }
    const k = band / peak, top0 = 34 + gains * k;
    const xs = steps.map((_, i) => X0 + 70 + i * ((X1 - X0 - 150) / Math.max(1, steps.length - 1)));
    let top = top0, bot = top0 + sum.clear * k, x = X0, svg = "", labels = "";
    const sinkY = top0 + band + 46;
    const seg = (x1) => `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${(x1 - x + 0.5).toFixed(1)}" height="${Math.max(0.5, bot - top).toFixed(1)}" fill="url(#sk-main)"></rect>`;
    labels += `<text x="${X0}" y="${(top - 6).toFixed(1)}" class="skl">${t.steps.clear}</text><text x="${X0}" y="${(top - 6 - 13).toFixed(1)}" class="skv">${this._kwh(sum.clear)} ${t.kwh}</text>`;
    steps.forEach(([key, v, name, color], i) => {
      const xi = xs[i], d = Math.abs(v) * k;
      svg += seg(xi);
      x = xi;
      const val = `${v > 0 ? "+" : "−"}${this._kwh(Math.abs(v))}`;
      if (v < 0) {
        const y0 = bot - d / 2, xe = xi + 34;
        svg += `<path d="M${xi.toFixed(1)},${y0.toFixed(1)} C${(xi + 26).toFixed(1)},${y0.toFixed(1)} ${xe.toFixed(1)},${(y0 + 14).toFixed(1)} ${xe.toFixed(1)},${(sinkY - 6).toFixed(1)}" stroke="${color}" stroke-width="${Math.max(1, d).toFixed(1)}" fill="none" class="skr"><title>${name} ${val} ${t.kwh}</title></path>`;
        const [lx, anchor] = xe > W - 70 ? [X1, "end"] : [xe, "middle"];      // near the edge: right-aligned
        labels += `<text x="${lx.toFixed(1)}" y="${(sinkY + 8).toFixed(1)}" text-anchor="${anchor}" class="skl">${name}</text><text x="${lx.toFixed(1)}" y="${(sinkY + 21).toFixed(1)}" text-anchor="${anchor}" class="skv">${val}</text>`;
        bot -= d;
      } else {
        const y0 = top - d / 2, xs0 = xi - 34;
        svg += `<path d="M${xs0.toFixed(1)},${(top0 - gains * k - 10).toFixed(1)} C${xs0.toFixed(1)},${(y0 - 14).toFixed(1)} ${(xi - 26).toFixed(1)},${y0.toFixed(1)} ${xi.toFixed(1)},${y0.toFixed(1)}" stroke="${color}" stroke-width="${Math.max(1, d).toFixed(1)}" fill="none" class="skr"><title>${name} ${val} ${t.kwh}</title></path>`;
        labels += `<text x="${xs0.toFixed(1)}" y="${(top0 - gains * k - 26).toFixed(1)}" text-anchor="middle" class="skl">${name}</text><text x="${xs0.toFixed(1)}" y="${(top0 - gains * k - 13).toFixed(1)}" text-anchor="middle" class="skv">${val}</text>`;
        top -= d;
      }
      if (key === "heat" || (i === steps.length - 1 && key !== "dev") || (steps[i + 1] && steps[i + 1][0] === "dev")) {
        labels += `<line x1="${(xi + 4).toFixed(1)}" x2="${(xi + 4).toFixed(1)}" y1="${(top - 4).toFixed(1)}" y2="${(bot + 4).toFixed(1)}" class="skm"></line>`;
        if (end !== null) labels += `<text x="${(xi + 8).toFixed(1)}" y="${(top - 6).toFixed(1)}" class="skv">${t.forecast} ${this._kwh(sum.result)}</text>`;
      }
    });
    svg += seg(X1);
    const endName = end === null ? t.forecast : phase === "past" ? t.actual : t.expected;
    labels += `<text x="${X1}" y="${(top - 6).toFixed(1)}" text-anchor="end" class="skl">${endName}</text><text x="${X1}" y="${(top - 19).toFixed(1)}" text-anchor="end" class="skv strong">${this._kwh(end === null ? sum.result : end)} ${t.kwh}</text>`;
    const H = sinkY + 26;
    return `<svg viewBox="0 0 ${W} ${H.toFixed(0)}" role="img" aria-label="${t.lossSide}"><defs><linearGradient id="sk-main" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#F5A524"></stop><stop offset="1" stop-color="#FF6A3D"></stop></linearGradient></defs>${svg}${labels}</svg>`;
  }

  _useSankey(f) {
    if (!f) return "";
    const t = this._t, C = { pv: "#F5A524", battery: "#2EAA6E", grid: "#22C7E8", house: "#FF6A3D", rest: "#8B5CF6" };
    const links = f.load
      ? [["pv", "house", f.pv_home], ["pv", "battery", f.pv_battery], ["pv", "grid", f.pv_grid], ["battery", "house", f.battery_home],
        ["battery", "grid", f.battery_grid], ["grid", "house", f.grid_home], ["grid", "battery", f.grid_battery]]
      : [["pv", "battery", f.pv_battery], ["pv", "rest", f.pv_rest]];
    const live = links.filter(([, , v]) => v >= 10);
    const total = live.reduce((a, [, , v]) => a + v, 0);
    if (total < 50) return "";
    const order = ["pv", "battery", "grid"], sinks = ["house", "battery", "grid", "rest"];
    const names = { pv: t.pv, battery: t.battery, grid: t.grid, house: t.house, rest: t.houseGrid };
    const W = 400, X0 = 92, X1 = W - 92, nw = 12, gap = 12, Hn = 150, top = 8;
    const left = order.map((n) => [n, live.filter(([a]) => a === n).reduce((s, [, , v]) => s + v, 0)]).filter(([, v]) => v > 0);
    const right = sinks.map((n) => [n, live.filter(([, b]) => b === n).reduce((s, [, , v]) => s + v, 0)]).filter(([, v]) => v > 0);
    const k = (Hn - gap * (Math.max(left.length, right.length) - 1)) / total;
    const place = (nodes) => { let y = top; return new Map(nodes.map(([n, v]) => { const p = [y, v]; y += v * k + gap; return [n, p]; })); };
    const L = place(left), R = place(right), lOff = new Map(), rOff = new Map();
    let svg = "", defs = "";
    live.sort((a, b) => sinks.indexOf(a[1]) - sinks.indexOf(b[1]) || order.indexOf(a[0]) - order.indexOf(b[0]));
    live.forEach(([a, b, v], i) => {
      const h = v * k, ya = L.get(a)[0] + (lOff.get(a) || 0), yb = R.get(b)[0] + (rOff.get(b) || 0);
      lOff.set(a, (lOff.get(a) || 0) + h); rOff.set(b, (rOff.get(b) || 0) + h);
      const xa = X0 + nw, xb = X1, mx = (xa + xb) / 2;
      defs += `<linearGradient id="sk-l${i}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="${C[a]}"></stop><stop offset="1" stop-color="${C[b]}"></stop></linearGradient>`;
      svg += `<path d="M${xa},${ya.toFixed(1)} C${mx},${ya.toFixed(1)} ${mx},${yb.toFixed(1)} ${xb},${yb.toFixed(1)} L${xb},${(yb + h).toFixed(1)} C${mx},${(yb + h).toFixed(1)} ${mx},${(ya + h).toFixed(1)} ${xa},${(ya + h).toFixed(1)} Z" fill="url(#sk-l${i})" class="skl-link"><title>${names[a]} → ${names[b]}: ${this._kwh(v)} ${t.kwh}</title></path>`;
    });
    const node = (map, x, anchor, tx) => [...map].map(([n, [y, v]]) => `<rect x="${x}" y="${y.toFixed(1)}" width="${nw}" height="${Math.max(1, v * k).toFixed(1)}" rx="3" fill="${C[n]}"></rect>
      <text x="${tx}" y="${(y + v * k / 2 - 1).toFixed(1)}" text-anchor="${anchor}" class="skl">${names[n]}</text><text x="${tx}" y="${(y + v * k / 2 + 12).toFixed(1)}" text-anchor="${anchor}" class="skv">${this._kwh(v)} ${t.kwh}</text>`).join("");
    svg += node(L, X0, "end", X0 - 8) + node(R, X1, "start", X1 + nw + 8);
    const pv = left.find(([n]) => n === "pv"), house = right.find(([n]) => n === "house");
    const figs = [];
    if (pv && pv[1] > 0 && f.load) figs.push([t.selfUse, Math.round((f.pv_home + f.pv_battery) / pv[1] * 100)]);
    if (house && house[1] > 0) figs.push([t.autarky, Math.round((f.pv_home + f.battery_home) / house[1] * 100)]);
    const H = Math.max(...[...L.values(), ...R.values()].map(([y, v]) => y + v * k)) + 18;
    return `<svg viewBox="0 0 ${W} ${H.toFixed(0)}" role="img" aria-label="${t.useSide}"><defs>${defs}</defs>${svg}</svg>
      ${figs.length ? `<div class="skfigs">${figs.map(([n, v]) => `<div class="fig"><span class="muted small">${n}</span><span class="big">${v} %</span></div>`).join("")}</div>` : ""}`;
  }

  // the expectation for the day at each recorded hour: a fan of hourly curves and the day totals converging
  _history(e) {
    const t = this._t, d = this._data, snaps = (e.history || []).map((s) => [s.at, new Map(tsRows(s, "wh"))]);
    if (!snaps.length) return "";
    const dayAhead = new Map();
    for (const arr of e.arrays) for (const [h, v] of tsRows(arr.day_ahead, "wh")) dayAhead.set(h, (dayAhead.get(h) || 0) + v);
    const result = new Map();
    for (const arr of e.arrays) for (const [h, v] of this._main(arr)) result.set(h, (result.get(h) || 0) + v);
    const resultSum = this._sum(result), final = this._phase === "past";
    const totals = snaps.map(([ts, m]) => [ts, this._sum(m)]);
    const all = [...(dayAhead.size ? [[null, this._sum(dayAhead)]] : []), ...totals];
    let jump = null;
    for (let i = 1; i < all.length; i++) {
      const dv = all[i][1] - all[i - 1][1];
      if (!jump || Math.abs(dv) > Math.abs(jump[1])) jump = [all[i][0], dv];
    }
    const figs = [[t.snapshots, String(snaps.length)],
      [t.firstLast, `${this._kwh(totals[0][1])} / ${this._kwh(totals[totals.length - 1][1])} ${t.kwh}`],
      ...(jump ? [[t.jump, `${jump[1] > 0 ? "+" : jump[1] < 0 ? "−" : ""}${this._kwh(Math.abs(jump[1]))} ${t.kwh} <span class="muted small">${jump[0] ? `${t.at} ${this._hm(jump[0])}` : ""}</span>`]] : []),
      ...(final ? [[t.final, `${this._kwh(resultSum)} ${t.kwh}`]] : [])]
      .map(([k, v]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span></div>`).join("");

    // fan: one smooth curve per snapshot, coloured from early (violet) to late (sun)
    const hours = this._hours();
    const lit = hours.filter((h) => snaps.some(([, m]) => (m.get(h) || 0) > 1) || (dayAhead.get(h) || 0) > 1 || (result.get(h) || 0) > 1);
    if (!lit.length) return "";
    const h0 = lit[0] - HOUR, h1 = lit[lit.length - 1] + 2 * HOUR;
    const W = 960, X0 = 40, X1 = W - 10, H = 200, top = 10, bottom = H - 16;
    const x = (ts) => X0 + (ts - h0) / (h1 - h0) * (X1 - X0);
    const max = Math.max(200, ...lit.flatMap((h) => [dayAhead.get(h) || 0, result.get(h) || 0, ...snaps.map(([, m]) => m.get(h) || 0)])) * 1.08;
    const y = (v) => top + (bottom - top) * (1 - v / max);
    const step = [100, 200, 250, 500, 1000, 2000, 5000].find((s) => max / s <= 4) || 10000;
    let svg = `<defs><linearGradient id="nx-hist" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#8B5CF6"></stop><stop offset="1" stop-color="#F5A524"></stop></linearGradient></defs>`;
    for (let v = step; v < max; v += step) svg += `<line x1="${X0}" x2="${X1}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line><text x="${X0 - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${v >= 1000 ? this._num(v / 1000, v % 1000 ? 1 : 0) + " k" : v}</text>`;
    const span = hours.filter((h) => h >= h0 && h < h1);
    if (final) for (const h of span) {
      const v = result.get(h) || 0;
      if (v > 0) svg += `<rect x="${(x(h) + 2).toFixed(1)}" y="${y(v).toFixed(1)}" width="${(x(h + HOUR) - x(h) - 4).toFixed(1)}" height="${(bottom - y(v)).toFixed(1)}" rx="3" class="hbar"><title>${this._hm(h)} ${t.actual} ${Math.round(v)} Wh</title></rect>`;
    }
    const curve = (m) => {
      const q = span.map((h) => [x(h + HOUR / 2), y(m.get(h) || 0)]);
      return `M${q[0][0].toFixed(1)},${q[0][1].toFixed(1)}${smooth(q)}`;
    };
    if (dayAhead.size) svg += `<path d="${curve(dayAhead)}" class="ref"><title>${t.dayAhead} ${this._kwh(this._sum(dayAhead))} ${t.kwh}</title></path>`;
    snaps.forEach(([ts, m], i) => {
      const k = snaps.length > 1 ? i / (snaps.length - 1) : 1;
      svg += `<path d="${curve(m)}" fill="none" stroke="${this._mix(k)}" stroke-width="${(1.2 + k * 1.2).toFixed(1)}" stroke-opacity="${(0.35 + 0.65 * k).toFixed(2)}" class="hline"><title>${this._hm(ts)}: ${this._kwh(this._sum(m))} ${t.kwh}</title></path>`;
    });
    for (const h of span) {
      const hr = Number(this._hm(h).slice(0, 2));
      if (hr % 2 === 0) svg += `<text x="${x(h).toFixed(1)}" y="${H - 2}" text-anchor="middle" class="ax">${hr}</text>`;
    }

    // convergence: the day total of each snapshot over the time it was taken
    const C = 120, cx0 = X0, cx1 = X1, cTop = 14, cBottom = C - 18;
    const tFirst = d.start + 4 * HOUR, tLast = d.start + 22 * HOUR;
    const cx = (ts) => cx0 + (ts - tFirst) / (tLast - tFirst) * (cx1 - cx0);
    const vals = all.map(([, v]) => v).concat(final ? [resultSum] : []);
    const lo = Math.min(...vals), hi = Math.max(...vals), padv = Math.max((hi - lo) * 0.15, 200);
    const cy = (v) => cTop + (cBottom - cTop) * (1 - (v - (lo - padv)) / (hi - lo + 2 * padv));
    let conv = "";
    for (let hr = 6; hr <= 21; hr += 3) conv += `<text x="${cx(d.start + hr * HOUR).toFixed(1)}" y="${C - 2}" text-anchor="middle" class="ax">${hr}</text>`;
    if (final) conv += `<line x1="${cx0}" x2="${cx1}" y1="${cy(resultSum).toFixed(1)}" y2="${cy(resultSum).toFixed(1)}" class="resline"></line><text x="${cx0 + 14}" y="${(cy(resultSum) + 12).toFixed(1)}" class="ax">${t.final} ${this._kwh(resultSum)} ${t.kwh}</text>`;
    const pts = all.map(([ts, v]) => [ts === null ? tFirst : ts, v]);
    conv += `<path d="M${pts.map(([ts, v]) => `${cx(ts).toFixed(1)},${cy(v).toFixed(1)}`).join(" L")}" class="convline"></path>`;
    conv += pts.map(([ts, v], i) => {
      const k = all[i][0] === null ? 0 : snaps.length > 1 ? (i - (all.length - snaps.length)) / (snaps.length - 1) : 1;
      const label = all[i][0] === null ? t.dayAhead : this._hm(ts);
      return `<circle cx="${cx(ts).toFixed(1)}" cy="${cy(v).toFixed(1)}" r="${all[i][0] === null ? 3.5 : 4}" fill="${all[i][0] === null ? "var(--card-background-color)" : this._mix(k)}" stroke="${all[i][0] === null ? "var(--primary-text-color)" : "none"}" stroke-width="1.4"><title>${label}: ${this._kwh(v)} ${t.kwh}</title></circle>`;
    }).join("");

    return `<section class="card pad"><h3>${t.history}</h3><div class="figs">${figs}</div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.history}" class="hfan">${svg}</svg>
      <svg viewBox="0 0 ${W} ${C}" role="img" aria-label="${t.snapshots}">${conv}</svg>
      <div class="legend"><span><i class="ln dashed"></i>${t.dayAhead}</span><span><i class="sw histsw"></i>${t.snapshots} ${t.early} → ${t.late}</span>${final ? `<span><i class="sw hbarsw"></i>${t.actual}</span>` : ""}</div>
      <p class="help">${t.historyHelp}</p></section>`;
  }

  _mix(k) {
    const a = [139, 92, 246], b = [245, 165, 36];
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`;
  }

  _sky(k) {
    // overcast grey to clear sun, in both themes
    const a = this._dark ? [70, 76, 86] : [196, 202, 210], b = [245, 165, 36];
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * k));
    return `rgb(${c.join(",")})`;
  }

  _groups(e) {
    const t = this._t, phase = this._phase;
    const rows = e.arrays.map((arr, i) => {
      const m = this._main(arr), r = this._reference(arr), main = this._sum(m), ref = this._sum(r);
      return { arr, i, m, main, ref, dev: ref > 0 && phase !== "future" ? (main - ref) / ref : null };
    });
    const total = rows.reduce((a, r) => a + r.main, 0);
    const spark = (m, i) => {
      const hs = this._hours(), max = Math.max(1, ...hs.map((h) => m.get(h) || 0));
      const p = hs.map((h, k) => `${(k / (hs.length - 1) * 100).toFixed(1)},${(22 - (m.get(h) || 0) / max * 20).toFixed(1)}`).join(" ");
      return `<svg viewBox="0 0 100 24" class="spark" preserveAspectRatio="none"><polyline points="0,22 ${p} 100,22" fill="${DAY_COLORS[i % 6]}" fill-opacity=".25" stroke="${DAY_COLORS[i % 6]}" stroke-width="1.5" vector-effect="non-scaling-stroke"></polyline></svg>`;
    };
    const devCell = (d) => (d === null ? "–" : `<span class="${Math.abs(d) < 0.05 ? "" : d < 0 ? "bad" : "good"}">${this._pct(d)}</span>`);
    const mainLabel = phase === "past" ? t.actual : phase === "today" ? t.expected : t.forecast;
    const refLabel = phase === "today" ? t.morning : t.dayAhead;
    // one block per group instead of a wide table: name and main value, the course, then small figures that wrap
    const fig = (k, v) => `<span class="gfig"><span class="muted">${k}</span> ${v}</span>`;
    return `<section class="card pad grow"><h3>${t.groups}</h3><div class="glist">
      ${rows.map((r) => `<div class="gitem">
        <div class="ghead"><div class="gname"><i class="dot" style="background:${DAY_COLORS[r.i % 6]}"></i>${esc(r.arr.name)}</div>
          <div class="gmain"><span class="muted small">${mainLabel}</span> <b>${this._kwh(r.main)} ${t.kwh}</b></div></div>
        ${spark(r.m, r.i)}
        <div class="gfigs">
          ${phase !== "future" ? fig(refLabel, r.ref > 0 ? `${this._kwh(r.ref)} ${t.kwh}` : "–") + fig(t.deviation, devCell(r.dev)) : ""}
          ${phase === "today" ? fig(t.correction, r.arr.correction == null ? "–" : `${r.arr.correction > 0 ? "+" : ""}${this._num(r.arr.correction, 2)}`) : ""}
          ${fig(t.share, `${total > 0 ? Math.round(r.main / total * 100) : 0} %`)}
        </div></div>`).join("")}
    </div></section>`;
  }

  _waterfall(e) {
    return `<section class="card pad grow"><div id="wfbody">${this._waterfallBody(e)}</div></section>`;
  }

  _waterfallBody(e) {
    const t = this._t, h = this._hour, phase = this._phase, now = this._data.now;
    const chips = [["all", t.all], ...e.arrays.map((a) => [a.id, a.name])].map(([id, name]) =>
      `<button class="chip ${this._focus === id ? "on" : ""}" data-act="focus" data-id="${esc(id)}">${esc(name)}</button>`).join("");
    const arrays = this._focus === "all" ? e.arrays : e.arrays.filter((a) => a.id === this._focus);
    const steps = arrays.map((a) => (a.steps || {})[String(h)]).filter(Boolean);
    const head = `<h3>${t.waterfall}${h !== null ? ` · ${this._hm(h)}–${this._hm(h + HOUR)}` : ""}</h3><div class="chips">${chips}</div>`;
    if (h === null || !steps.length) return `${head}<p class="muted">${h === null ? t.hourHint : t.noSteps}</p>`;
    const sum = (k) => steps.reduce((a, s) => a + (s[k] || 0), 0);
    const items = [["clear", sum("clear"), "start"], ...["clouds", "shade", "reflection", "direction", "heat"].map((k) => [k, sum(k), "step"]), ["result", sum("result"), "total"]];
    const at = (key, a) => new Map(tsRows(a[key], "wh")).get(h);
    if (phase === "today" && h + HOUR > now) {
      const exp = arrays.reduce((a, arr) => a + (at("expected", arr) || 0), 0);
      if (Math.abs(exp - sum("result")) >= 1) items.push(["corr", exp - sum("result"), "step"], ["expected", exp, "total"]);
    }
    const act = arrays.map((a) => at("actual", a)).filter((v) => v !== undefined);
    if (act.length === arrays.length && (phase === "past" || h + HOUR <= now)) items.push(["actual", act.reduce((a, v) => a + v, 0), "actual"]);
    const W = 520, rowH = 30, L = 118, R = W - 70, n = items.length;
    let run = 0;
    const max = Math.max(1, ...items.map(([, v, kind]) => { if (kind === "start" || kind === "total" || kind === "actual") { run = v; return v; } run += v; return Math.max(run, run - v); }));
    const x = (v) => L + Math.max(0, v) / max * (R - L);
    let svg = "", level = 0;
    items.forEach(([key, v, kind], i) => {
      const yy = i * rowH + 4;
      let a, b, cls;
      if (kind === "step") { a = level; b = level + v; level = b; cls = v < 0 ? "neg" : "pos"; } else { a = 0; b = v; level = v; cls = kind; }
      const x0 = x(Math.min(a, b)), x1 = x(Math.max(a, b));
      svg += `<text x="${L - 8}" y="${yy + 15}" text-anchor="end" class="wl">${t.steps[key]}</text>
        <rect x="${x0.toFixed(1)}" y="${yy + 3}" width="${Math.max(1.5, x1 - x0).toFixed(1)}" height="${rowH - 10}" rx="5" class="wf ${cls}"></rect>
        <text x="${(x1 + 6).toFixed(1)}" y="${yy + 15}" class="wv ${cls}">${kind === "step" && v > 0 ? "+" : kind === "step" && v < 0 ? "−" : ""}${Math.abs(Math.round(v))} W</text>`;
      if (i < n - 1 && kind !== "actual") svg += `<line x1="${x(level).toFixed(1)}" x2="${x(level).toFixed(1)}" y1="${yy + rowH - 7}" y2="${yy + rowH + 3}" class="wc"></line>`;
    });
    const eff = arrays.length === 1 ? `${this._num(arrays[0].efficiency * 100, 1)} %` : `${this._num(arrays.reduce((a, arr) => a + arr.efficiency, 0) / arrays.length * 100, 1)} %`;
    const notes = items.filter(([k, v, kind]) => (kind === "start" || (kind === "step" && Math.abs(v) >= 5)) && t.stepHelp[k])
      .map(([k]) => `<li><b>${t.steps[k]}</b> ${k === "clear" ? t.stepHelp.clear(eff) : t.stepHelp[k]}</li>`).join("");
    return `${head}<svg viewBox="0 0 ${W} ${n * rowH + 8}" class="wfall" role="img" aria-label="${t.waterfall}">${svg}</svg><ul class="notes">${notes}</ul>`;
  }

  // ------------------------------------------------------------ formatting

  _pct(v) { const r = Math.round(v * 100); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)} %`; }
  _num(v, d) { return num(v, this._lang, d); }
  _kwh(wh) { return kwh(wh, this._lang); }
  _hm(ts) { return hm(ts, this._lang, this._data.time_zone, { hourCycle: "h23" }); }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --nx-rail: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)"}; --nx-good: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-bad: ${d ? "#E0574A" : "#C83A2E"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
      .grow { flex: 1 1 420px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      h3 { margin: 0 0 8px; font-size: 15px; font-weight: 500; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 8px 0 0; line-height: 1.45; }
      .figs { display: flex; flex-wrap: wrap; gap: 12px 32px; }
      .fig { display: flex; flex-direction: column; gap: 2px; }
      .big { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
      .nav { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
      .when { display: flex; flex-direction: column; min-width: 180px; text-align: center; }
      .when b { font-size: 18px; font-weight: 600; }
      .round { width: 40px; height: 40px; border-radius: 20px; border: 1px solid var(--divider-color); background: var(--card-background-color);
        color: var(--primary-text-color); font-size: 22px; line-height: 1; cursor: pointer; }
      .round:disabled { opacity: .35; cursor: default; }
      .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
      .chip { min-height: 32px; padding: 0 14px; border-radius: 16px; border: 1px solid var(--divider-color); background: none; color: inherit; font: inherit; font-size: 13px; cursor: pointer; }
      svg { display: block; width: 100%; height: auto; }
      .ax { font-size: 10px; fill: var(--secondary-text-color); }
      .rail { stroke: var(--nx-rail); }
      .ref { fill: none; stroke: var(--primary-text-color); stroke-width: 1.6; stroke-dasharray: 5 4; opacity: .75; }
      .held { fill: none; stroke: var(--primary-text-color); stroke-width: 1.4; stroke-dasharray: 1.5 3; opacity: .6; }
      .bandarea { fill: #FF6A3D; fill-opacity: .12; stroke: #FF6A3D; stroke-opacity: .5; stroke-width: .8; stroke-dasharray: 2 2; }
      .nowline { stroke: var(--secondary-text-color); stroke-dasharray: 3 3; }
      .sel { fill: var(--primary-text-color); fill-opacity: .07; stroke: #FF6A3D; stroke-opacity: .6; rx: 4; }
      .hit { fill: transparent; cursor: pointer; } .hit:hover { fill: var(--primary-text-color); fill-opacity: .04; }
      .lshade { fill: var(--nx-bad); opacity: .8; } .lrefl { fill: #8B5CF6; opacity: .8; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; }
      .legend i { display: inline-block; } .legend .sw { width: 12px; height: 10px; border-radius: 3px; }
      .legend .ln { width: 18px; height: 0; border-top: 2px solid var(--primary-text-color); opacity: .75; }
      .legend .ln.dashed { border-top-style: dashed; } .legend .ln.dotted { border-top-style: dotted; }
      .bandsw { background: rgba(255,106,61,.15); border: 1px dashed rgba(255,106,61,.6); }
      .skysw { background: linear-gradient(90deg, ${d ? "rgb(70,76,86)" : "rgb(196,202,210)"}, #F5A524); }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 12px; padding: 4px 6px; }
      td { padding: 6px; border-top: 1px solid var(--divider-color); vertical-align: middle; }
      .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .gname { display: flex; align-items: center; gap: 6px; font-weight: 500; }
      .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; }
      .spark { width: 100%; height: 26px; margin: 4px 0 2px; }
      .glist { display: flex; flex-direction: column; }
      .gitem { padding: 10px 0; border-top: 1px solid var(--divider-color); } .gitem:first-child { border-top: 0; padding-top: 2px; }
      .ghead { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
      .gmain b { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
      .gfigs { display: flex; flex-wrap: wrap; gap: 2px 16px; font-size: 12px; font-variant-numeric: tabular-nums; }
      .gfig { white-space: nowrap; }
      .good { color: var(--nx-good); font-weight: 500; } .bad { color: var(--nx-bad); font-weight: 500; }
      .wfall { max-width: 640px; }
      .wl { font-size: 12px; fill: var(--primary-text-color); } .wv { font-size: 12px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .wf.start { fill: #F5A524; } .wf.total { fill: #FF6A3D; } .wf.actual { fill: #8B5CF6; }
      .wf.pos { fill: var(--nx-good); } .wf.neg { fill: var(--nx-bad); opacity: .85; }
      .wv.total, .wv.actual, .wv.start { fill: var(--primary-text-color); font-weight: 600; }
      .wc { stroke: var(--secondary-text-color); stroke-dasharray: 2 2; opacity: .6; }
      .notes { margin: 10px 0 0; padding-left: 18px; color: var(--secondary-text-color); font-size: 12px; line-height: 1.5; }
      .notes b { color: var(--primary-text-color); font-weight: 500; }
      .hbar { fill: var(--primary-text-color); fill-opacity: .09; }
      .sankeys { display: flex; flex-wrap: wrap; gap: 16px 32px; align-items: flex-start; }
      .sk { flex: 1 1 340px; min-width: 0; } .sk:first-child { flex-basis: 480px; } .sk svg { max-width: 640px; }
      .sub { margin: 4px 0 6px; text-transform: uppercase; letter-spacing: .06em; }
      .skl { font-size: 11px; fill: var(--primary-text-color); } .skv { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .skv.strong { fill: var(--primary-text-color); font-weight: 600; }
      .skr { stroke-opacity: .7; transition: stroke-opacity .15s; } .skr:hover { stroke-opacity: 1; }
      .skm { stroke: var(--primary-text-color); stroke-opacity: .5; stroke-dasharray: 2 2; }
      .skl-link { opacity: .55; transition: opacity .15s; } .skl-link:hover { opacity: .9; }
      .skfigs { display: flex; gap: 12px 32px; margin-top: 6px; }
      .hline { transition: stroke-opacity .15s; } .hline:hover { stroke-opacity: 1; stroke-width: 3; }
      .hfan { margin-top: 12px; }
      .convline { fill: none; stroke: var(--secondary-text-color); stroke-opacity: .5; stroke-width: 1.2; }
      .resline { stroke: var(--nx-good); stroke-dasharray: 4 3; stroke-width: 1.4; }
      .histsw { background: linear-gradient(90deg, #8B5CF6, #F5A524); } .hbarsw { background: var(--primary-text-color); opacity: .12; }
    </style>`;
  }
}

customElements.define("noxcast-day", NoxcastDay);
