// Noxcast panel section "Day": one day in detail. The groups stacked, forecast against expectation
// and actual, the weather of each hour, and for a chosen hour how its forecast comes about.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/day".

const DAY_TEXT = {
  de: {
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
    kwh: "kWh", w: "W",
  },
  en: {
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
      this._data = await this._hass.connection.sendMessagePromise({ type: "noxcast/day", offset: this._offset });
      this._loaded = Date.now();
      this._error = null;
      const e = (this._data.entries || [])[0];
      if (this._hour === null || this._hour < this._data.start || this._hour >= this._data.end) this._hour = e ? this._defaultHour(e) : null;
    } catch (err) {
      this._error = String((err && err.message) || err);
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
    const actual = new Map(arr.actual || []);
    if (this._phase === "past") return actual;
    const rest = new Map(this._phase === "today" ? arr.expected || [] : arr.forecast || []);
    for (const [t, v] of rest) out.set(t, t + HOUR <= now && actual.has(t) ? actual.get(t) : v);
    return out;
  }

  // the reference the day is measured against: morning forecast today, day-ahead forecast otherwise
  _reference(arr) {
    if (this._phase === "today" && arr.morning && arr.morning.length) return new Map(arr.morning);
    if (arr.day_ahead && arr.day_ahead.length) return new Map(arr.day_ahead);
    return this._phase === "future" ? new Map(arr.forecast || []) : new Map();
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
    if (this._error) body = `<section class="card pad">${this._esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!e || !e.arrays.some((a) => this._main(a).size)) body = `${this._nav()}<section class="card pad">${t.noData}</section>`;
    else body = `${this._nav()}${this._figures(e)}${this._chartCard(e)}<div class="split">${this._groups(e)}${this._waterfall(e)}</div>`;
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
    const refLabel = phase === "today" && e.arrays.some((a) => a.morning && a.morning.length) ? t.morning : phase === "future" ? t.forecast : t.dayAhead;
    const mainLabel = phase === "past" ? t.actual : phase === "today" ? t.expected : t.forecast;
    const totals = new Map();
    for (const arr of e.arrays) for (const [h, v] of this._main(arr)) totals.set(h, (totals.get(h) || 0) + v);
    let peakT = null, peak = 0;
    for (const [h, v] of totals) if (v > peak) { peak = v; peakT = h; }
    const band = e.band || [];
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
    const sky = new Map((e.weather || []).map(([ts, g, c, temp]) => [ts, [g, c, temp]]));
    const lit = hours.filter((h) => e.arrays.some((a) => (this._main(a).get(h) || 0) > 1 || (this._reference(a).get(h) || 0) > 1) || (sky.get(h) && sky.get(h)[1] > 20));
    const h0 = lit.length ? Math.max(d.start, lit[0] - HOUR) : d.start + 5 * HOUR, h1 = lit.length ? Math.min(d.end, lit[lit.length - 1] + 2 * HOUR) : d.end - 3 * HOUR;
    const span = hours.filter((h) => h >= h0 && h < h1);
    const x = (ts) => X0 + (ts - h0) / (h1 - h0) * (X1 - X0);
    const mains = e.arrays.map((a) => this._main(a)), refs = e.arrays.map((a) => this._reference(a));
    const held = e.arrays.map((a) => new Map(a.held || []));
    const total = (maps, h) => maps.reduce((s, m) => s + (m.get(h) || 0), 0);
    const band = new Map((e.band || []).map(([ts, lo, hi]) => [ts, [lo, hi]]));
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
      svg += `<text x="${X0 - 6}" y="${Ly + 8}" text-anchor="end" class="ax">${this._esc(arr.name).slice(0, 6)}</text>`;
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
      const parts = e.arrays.map((a, i) => `${this._esc(a.name)} ${Math.round(mains[i].get(h) || 0)} W`).join(", ");
      svg += `<rect x="${x(h).toFixed(1)}" y="0" width="${(x(h + HOUR) - x(h)).toFixed(1)}" height="${Sy + Sh}" class="hit" data-act="hour" data-t="${h}"><title>${this._hm(h)}: ${Math.round(total(mains, h))} W (${parts})</title></rect>`;
    }
    const mainLabel = phase === "past" ? t.actual : phase === "today" ? `${t.actual} / ${t.expected}` : t.forecast;
    const refLabel = phase === "today" && e.arrays.some((a) => a.morning && a.morning.length) ? t.morning : phase === "future" ? t.forecast : t.dayAhead;
    const legend = [...e.arrays.map((a, i) => `<span><i class="sw" style="background:${DAY_COLORS[i % 6]}"></i>${this._esc(a.name)}</span>`),
      `<span class="muted">${mainLabel}</span>`,
      phase !== "future" ? `<span><i class="ln dashed"></i>${refLabel}</span>` : "",
      phase === "today" ? `<span><i class="ln dotted"></i>${t.held}</span>` : "",
      band.size ? `<span><i class="sw bandsw"></i>${t.band}</span>` : "",
      `<span><i class="sw skysw"></i>${t.weather}</span>`].join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.chart}">${svg}</svg><div class="legend">${legend}</div>`;
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
    return `<section class="card pad grow"><h3>${t.groups}</h3><table><thead><tr><th></th><th class="num">${mainLabel}</th>
      ${phase !== "future" ? `<th class="num">${refLabel}</th><th class="num">${t.deviation}</th>` : ""}
      ${phase === "today" ? `<th class="num">${t.correction}</th>` : ""}<th class="num">${t.share}</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td><div class="gname"><i class="dot" style="background:${DAY_COLORS[r.i % 6]}"></i>${this._esc(r.arr.name)}</div>${spark(r.m, r.i)}</td>
        <td class="num">${this._kwh(r.main)} ${t.kwh}</td>
        ${phase !== "future" ? `<td class="num">${r.ref > 0 ? `${this._kwh(r.ref)} ${t.kwh}` : "–"}</td><td class="num">${devCell(r.dev)}</td>` : ""}
        ${phase === "today" ? `<td class="num">${r.arr.correction == null ? "–" : `${r.arr.correction > 0 ? "+" : ""}${this._num(r.arr.correction, 2)}`}</td>` : ""}
        <td class="num">${total > 0 ? Math.round(r.main / total * 100) : 0} %</td></tr>`).join("")}
    </tbody></table></section>`;
  }

  _waterfall(e) {
    return `<section class="card pad grow"><div id="wfbody">${this._waterfallBody(e)}</div></section>`;
  }

  _waterfallBody(e) {
    const t = this._t, h = this._hour, phase = this._phase, now = this._data.now;
    const chips = [["all", t.all], ...e.arrays.map((a) => [a.id, a.name])].map(([id, name]) =>
      `<button class="chip ${this._focus === id ? "on" : ""}" data-act="focus" data-id="${this._esc(id)}">${this._esc(name)}</button>`).join("");
    const arrays = this._focus === "all" ? e.arrays : e.arrays.filter((a) => a.id === this._focus);
    const steps = arrays.map((a) => (a.steps || {})[String(h)]).filter(Boolean);
    const head = `<h3>${t.waterfall}${h !== null ? ` · ${this._hm(h)}–${this._hm(h + HOUR)}` : ""}</h3><div class="chips">${chips}</div>`;
    if (h === null || !steps.length) return `${head}<p class="muted">${h === null ? t.hourHint : t.noSteps}</p>`;
    const sum = (k) => steps.reduce((a, s) => a + (s[k] || 0), 0);
    const items = [["clear", sum("clear"), "start"], ...["clouds", "shade", "reflection", "direction", "heat"].map((k) => [k, sum(k), "step"]), ["result", sum("result"), "total"]];
    const at = (key, a) => new Map(a[key] || []).get(h);
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
  _num(v, d) { return Number(v).toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d }); }
  _kwh(wh) { return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }
  _hm(ts) { return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: this._data.time_zone }); }
  _esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

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
      .spark { width: 120px; height: 22px; margin-top: 4px; }
      .good { color: var(--nx-good); font-weight: 500; } .bad { color: var(--nx-bad); font-weight: 500; }
      .wfall { max-width: 640px; }
      .wl { font-size: 12px; fill: var(--primary-text-color); } .wv { font-size: 12px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .wf.start { fill: #F5A524; } .wf.total { fill: #FF6A3D; } .wf.actual { fill: #8B5CF6; }
      .wf.pos { fill: var(--nx-good); } .wf.neg { fill: var(--nx-bad); opacity: .85; }
      .wv.total, .wv.actual, .wv.start { fill: var(--primary-text-color); font-weight: 600; }
      .wc { stroke: var(--secondary-text-color); stroke-dasharray: 2 2; opacity: .6; }
      .notes { margin: 10px 0 0; padding-left: 18px; color: var(--secondary-text-color); font-size: 12px; line-height: 1.5; }
      .notes b { color: var(--primary-text-color); font-weight: 500; }
    </style>`;
  }
}

customElements.define("noxcast-day", NoxcastDay);
