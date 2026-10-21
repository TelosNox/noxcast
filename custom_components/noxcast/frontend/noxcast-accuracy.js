// Noxcast panel section "Forecast accuracy": day-ahead forecasts from the archive against what happened.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/accuracy".

const ACC_TEXT = {
  de: {
    title: "Prognosegüte", days: "Tage", error: "Tagesfehler", bias: "Abweichung", noData: "Noch keine abgeschlossenen Tage im Archiv. Die Tagesprognose wird jeden Abend archiviert und nachts mit den Messwerten ergänzt.",
    source: "Wetterquelle", primary: "Hauptquelle", open_meteo: "Open-Meteo", mix: "Mittel beider", all: "Alle Flächen",
    range: "Zeitraum", perDay: "Prognose und Ist pro Tag", forecast: "Prognose", actual: "Ist", calendar: "Kalender",
    byWeather: "Nach Wetterlage", clear: "klar", mixed: "wechselhaft", overcast: "bedeckt", compare: "Quellen im Vergleich",
    sameDays: "auf denselben Tagen", hourly: "Stundenverlauf", curtailed: "Akku voll (evtl. abgeregelt)", issued: "erstellt",
    pick: "Tag im Diagramm oder Kalender anklicken", over: "zu hoch", under: "zu niedrig", kwh: "kWh",
    help: "Fehler = Summe der absoluten Tagesabweichungen geteilt durch die Summe der Ist-Werte. Abweichung > 0 heißt: im Mittel zu hoch prognostiziert. Die Wetterlage folgt aus der prognostizierten Einstrahlung im Verhältnis zum klaren Himmel.",
    split: "Wetter oder Modell?", weatherPart: "Wetteranteil", modelPart: "Modellanteil", modelBias: "Modell-Abweichung",
    measured: "Mit gemessenem Wetter", splitDays: "Tage mit Satellitendaten",
    verdictWeather: "Der größte Teil des Fehlers kommt aus der Wettervorhersage. Mit dem tatsächlichen Wetter liegt das Modell deutlich näher am Ist.",
    verdictModel: "Der größere Teil des Fehlers bleibt auch mit gemessenem Wetter. Hier lohnt ein Blick auf Verschattung, Ausrichtung und Wirkungsgrad.",
    verdictBoth: "Wettervorhersage und Modell tragen etwa gleich viel zum Fehler bei.",
    splitHelp: "Für jede vergangene Stunde rechnet Noxcast das Modell noch einmal mit der Einstrahlung aus dem Satellitenarchiv. Wetteranteil = Prognose − Modell mit gemessenem Wetter, Modellanteil = Modell mit gemessenem Wetter − Ist. Beide Balken zusammen ergeben die Tagesabweichung. Ein Rest bleibt immer: Satellitenwerte sind Flächenmittel und keine Messung am Dach.",
    weekdays: ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
  },
  en: {
    title: "Forecast accuracy", days: "days", error: "Daily error", bias: "Bias", noData: "No completed days in the archive yet. The day-ahead forecast is archived every evening and completed with measurements at night.",
    source: "Weather source", primary: "Main source", open_meteo: "Open-Meteo", mix: "Mean of both", all: "All arrays",
    range: "Period", perDay: "Forecast and actual per day", forecast: "Forecast", actual: "Actual", calendar: "Calendar",
    byWeather: "By weather", clear: "clear", mixed: "mixed", overcast: "overcast", compare: "Sources compared",
    sameDays: "on the same days", hourly: "Hourly", curtailed: "Battery full (possibly curtailed)", issued: "issued",
    pick: "Click a day in the chart or calendar", over: "too high", under: "too low", kwh: "kWh",
    help: "Error = sum of the absolute daily deviations divided by the sum of the actual values. Bias > 0 means forecast too high on average. The weather class follows from the forecast irradiance relative to a clear sky.",
    split: "Weather or model?", weatherPart: "Weather part", modelPart: "Model part", modelBias: "Model bias",
    measured: "With measured weather", splitDays: "days with satellite data",
    verdictWeather: "Most of the error comes from the weather forecast. With the actual weather the model is much closer to what happened.",
    verdictModel: "The larger part of the error remains even with measured weather. Shading, orientation and efficiency are worth a look.",
    verdictBoth: "Weather forecast and model contribute about equally to the error.",
    splitHelp: "For every past hour Noxcast runs the model again on the irradiance from the satellite archive. Weather part = forecast − model on measured weather, model part = model on measured weather − actual. Both bars together are the daily deviation. Some rest always remains: satellite values are area means, not a measurement on the roof.",
    weekdays: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
  },
};

const SOURCES = ["primary", "open_meteo", "mix"];
const CLASSES = [["clear", 0.7, Infinity], ["mixed", 0.35, 0.7], ["overcast", -Infinity, 0.35]];

class NoxcastAccuracy extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._s = { range: 30, source: "primary", array: "all", day: null };
    this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
  }

  set hass(hass) {
    const first = !this._hass, dark = this._dark;
    this._hass = hass;
    if (first) this._load();
    else if (this._dark !== dark) this._render();
  }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return ACC_TEXT[(this._hass.language || "en").slice(0, 2)] || ACC_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "noxcast/accuracy" });
    } catch (err) {
      this._error = String((err && err.message) || err);
    }
    this._render();
  }

  // ------------------------------------------------------------ evaluation

  // forecast of one hour for the chosen source; null when that source has no value
  _fc(h, source, ids) {
    const pick = (f) => (ids.every((id) => id in f) ? ids.reduce((a, id) => a + f[id], 0) : null);
    const prim = pick(h.forecast);
    if (source === "primary") return prim;
    const other = Object.values(h.alt || {})[0];
    const alt = source === "open_meteo" ? (h.alt && h.alt.open_meteo ? pick(h.alt.open_meteo) : null) : other ? pick(other) : null;
    if (source === "open_meteo") return alt;
    return prim === null || alt === null ? null : (prim + alt) / 2;
  }

  // complete days: every hour has an actual value and a forecast of the source
  _days(source) {
    const entry = (this._data.entries || [])[0];
    if (!entry) return [];
    const ids = this._s.array === "all" ? entry.arrays.map((a) => a.id) : [this._s.array];
    const out = [];
    for (const d of entry.days) {
      let f = 0, a = 0, m = 0, ghi = 0, clear = 0, curtailed = 0, ok = d.hours.length >= 20, meas = true;
      for (const h of d.hours) {
        const fc = this._fc(h, source, ids);
        if (!ok || fc === null || !h.actual || !ids.every((id) => id in h.actual)) { ok = false; break; }
        f += fc;
        a += ids.reduce((s, id) => s + h.actual[id], 0);
        if (meas && h.measured && ids.every((id) => id in h.measured)) m += ids.reduce((s, id) => s + h.measured[id], 0);
        else meas = false;
        ghi += h.ghi || 0; clear += h.clear || 0;
        if (h.curtailed) curtailed++;
      }
      if (ok && a > 0) out.push({ date: d.date, f, a, m: meas ? m : null, k: clear > 0 ? ghi / clear : null, curtailed, raw: d });
    }
    return out;
  }

  _inRange(days) {
    if (!days.length) return days;
    const last = new Date(`${days[days.length - 1].date}T12:00:00Z`);
    const from = new Date(last.getTime() - (this._s.range - 1) * 86400000).toISOString().slice(0, 10);
    return days.filter((d) => d.date >= from);
  }

  _score(days) {
    const a = days.reduce((s, d) => s + d.a, 0);
    if (!days.length || a <= 0) return { error: null, bias: null, n: days.length };
    return { error: days.reduce((s, d) => s + Math.abs(d.f - d.a), 0) / a, bias: days.reduce((s, d) => s + d.f - d.a, 0) / a, n: days.length };
  }

  // ------------------------------------------------------------ rendering

  _render() {
    const t = this._t;
    let body;
    if (this._error) body = `<div class="card pad">${this._esc(this._error)}</div>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else {
      const all = this._days(this._s.source), days = this._inRange(all);
      body = days.length ? this._view(days) : `<div class="card pad">${t.noData}</div>`;
    }
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">${this._controls()}${body}</div>`;
  }

  _controls() {
    const t = this._t, s = this._s;
    const entry = this._data && (this._data.entries || [])[0];
    const hasAlt = entry && entry.days.some((d) => d.hours.some((h) => h.alt && Object.keys(h.alt).length));
    const chip = (key, val, label) => `<button class="chip ${s[key] === val ? "on" : ""}" data-set="${key}" data-val="${val}">${label}</button>`;
    const arrays = entry ? [chip("array", "all", t.all), ...entry.arrays.map((a) => chip("array", a.id, this._esc(a.name)))] : [];
    return `<div class="controls">
      <div class="group"><span class="muted small">${t.range}</span>${[14, 30, 90].map((r) => chip("range", r, `${r} ${t.days}`)).join("")}</div>
      ${hasAlt ? `<div class="group"><span class="muted small">${t.source}</span>${SOURCES.map((src) => chip("source", src, t[src])).join("")}</div>` : ""}
      ${arrays.length > 2 ? `<div class="group">${arrays.join("")}</div>` : ""}
    </div>`;
  }

  _view(days) {
    const t = this._t, sc = this._score(days);
    const sel = days.find((d) => d.date === this._s.day);
    const figs = [
      [t.error, this._pct(sc.error)], [t.bias, this._pct(sc.bias, true)], [t.days, String(sc.n)],
      [`${t.forecast} / ${t.actual}`, `${this._kwh(days.reduce((s, d) => s + d.f, 0))} / ${this._kwh(days.reduce((s, d) => s + d.a, 0))} ${t.kwh}`],
    ].map(([k, v]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span></div>`).join("");
    return `
      <section class="card pad"><div class="figs">${figs}</div><p class="help">${t.help}</p></section>
      <section class="card pad"><h3>${t.perDay}</h3>${this._bars(days)}</section>
      ${this._split(days)}
      <section class="card pad">${sel ? this._hourly(sel) : `<h3>${t.hourly}</h3><p class="muted">${t.pick}</p>`}</section>
      <div class="split">
        <section class="card pad"><h3>${t.calendar}</h3>${this._calendar(days)}</section>
        <section class="card pad grow"><h3>${t.byWeather}</h3>${this._weather(days)}</section>
        ${this._compare(days)}
      </div>`;
  }

  _bars(days) {
    const t = this._t, W = 720, H = 200, pad = 28;
    const max = Math.max(...days.map((d) => Math.max(d.f, d.a)), 1) * 1.08;
    const bw = (W - pad) / days.length;
    const y = (v) => H - 18 - (v / max) * (H - 30);
    const step = [500, 1000, 2000, 5000, 10000, 20000, 50000].find((s) => max / s <= 5) || 100000;
    let grid = "";
    for (let v = step; v < max; v += step) grid += `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line><text x="${pad - 4}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${this._kwh(v, 0)}</text>`;
    const bars = days.map((d, i) => {
      const x = pad + i * bw, err = (d.f - d.a) / d.a;
      const on = d.date === this._s.day;
      const label = `${this._date(d.date)}: ${t.forecast} ${this._kwh(d.f)}, ${t.actual} ${this._kwh(d.a)} ${t.kwh} (${this._pct(err, true)})`;
      return `<g data-day="${d.date}" class="hit"><title>${label}</title>
        <rect x="${(x + bw * 0.15).toFixed(1)}" y="${y(d.a).toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${(H - 18 - y(d.a)).toFixed(1)}" rx="2" class="${on ? "abar on" : "abar"}"></rect>
        <line x1="${(x + bw * 0.05).toFixed(1)}" x2="${(x + bw * 0.95).toFixed(1)}" y1="${y(d.f).toFixed(1)}" y2="${y(d.f).toFixed(1)}" stroke="${this._errColor(err)}" stroke-width="3" stroke-linecap="round"></line>
        <rect x="${x.toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${H}" fill="transparent"></rect></g>`;
    }).join("");
    const every = Math.ceil(days.length / 8);
    const labels = days.map((d, i) => (i % every ? "" : `<text x="${(pad + (i + 0.5) * bw).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${this._date(d.date, true)}</text>`)).join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.perDay}">${grid}${bars}${labels}</svg>
      <div class="legend"><span><i class="sw abar"></i>${t.actual}</span><span><i class="sw line" style="background:${this._errColor(0)}"></i>${t.forecast} (${this._pct(0.05)})</span>
      <span><i class="sw line" style="background:${this._errColor(0.3)}"></i>${this._pct(0.3)}</span><span><i class="sw line" style="background:${this._errColor(0.8)}"></i>${this._pct(0.8)}</span></div>`;
  }

  // deviation split: weather part (forecast − model on measured weather) and model part (that − actual)
  _split(days) {
    const t = this._t, sel = days.filter((d) => d.m !== null);
    if (sel.length < 3) return "";
    const a = sel.reduce((s, d) => s + d.a, 0);
    const wErr = sel.reduce((s, d) => s + Math.abs(d.f - d.m), 0) / a, mErr = sel.reduce((s, d) => s + Math.abs(d.m - d.a), 0) / a;
    const mBias = sel.reduce((s, d) => s + d.m - d.a, 0) / a;
    const verdict = wErr > 1.4 * mErr ? t.verdictWeather : mErr > 1.4 * wErr ? t.verdictModel : t.verdictBoth;
    const share = wErr + mErr > 0 ? wErr / (wErr + mErr) : 0.5;
    const figs = [[t.weatherPart, this._pct(wErr), "var(--nx-wx)"], [t.modelPart, this._pct(mErr), "var(--nx-md)"],
      [t.modelBias, this._pct(mBias, true), null], [t.splitDays, String(sel.length), null]]
      .map(([k, v, c]) => `<div class="fig"><span class="muted small">${c ? `<i class="dot" style="background:${c}"></i>` : ""}${k}</span><span class="big">${v}</span></div>`).join("");
    const W = 720, H = 180, pad = 34, mid = H / 2 - 6;
    const lim = Math.max(0.1, ...sel.flatMap((d) => [Math.abs(d.f - d.m) / d.a, Math.abs(d.m - d.a) / d.a])) * 1.1;
    const span = Math.min(lim, 1.5), y = (v) => mid - (Math.max(-span, Math.min(span, v)) / span) * (mid - 8);
    const bw = (W - pad) / sel.length;
    const ticks = [-span / 2, span / 2].map((v) => `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line>
      <text x="${pad - 4}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${this._pct(v, true)}</text>`).join("");
    const bars = sel.map((d, i) => {
      const x = pad + i * bw, w = (d.f - d.m) / d.a, m = (d.m - d.a) / d.a, on = d.date === this._s.day;
      const bar = (v, x0, cls) => `<rect x="${x0.toFixed(1)}" y="${Math.min(y(v), mid).toFixed(1)}" width="${(bw * 0.36).toFixed(1)}" height="${Math.max(1, Math.abs(y(v) - mid)).toFixed(1)}" rx="2" class="${cls}${on ? " on" : ""}"></rect>`;
      return `<g data-day="${d.date}" class="hit"><title>${this._date(d.date)}: ${t.weatherPart} ${this._pct(w, true)}, ${t.modelPart} ${this._pct(m, true)}</title>
        ${bar(w, x + bw * 0.12, "wx")}${bar(m, x + bw * 0.52, "md")}
        <rect x="${x.toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${H}" fill="transparent"></rect></g>`;
    }).join("");
    const every = Math.ceil(sel.length / 8);
    const labels = sel.map((d, i) => (i % every ? "" : `<text x="${(pad + (i + 0.5) * bw).toFixed(1)}" y="${H - 2}" text-anchor="middle" class="ax">${this._date(d.date, true)}</text>`)).join("");
    return `<section class="card pad"><h3>${t.split}</h3>
      <div class="figs">${figs}</div>
      <div class="share"><div style="width:${(share * 100).toFixed(0)}%"></div></div>
      <p class="verdict">${verdict}</p>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.split}">${ticks}<line x1="${pad}" x2="${W}" y1="${mid}" y2="${mid}" class="zero"></line>
        <text x="${pad - 4}" y="${mid + 3}" text-anchor="end" class="ax">0</text>${bars}${labels}</svg>
      <div class="legend"><span><i class="sw" style="background:var(--nx-wx)"></i>${t.weatherPart}</span><span><i class="sw" style="background:var(--nx-md)"></i>${t.modelPart}</span>
        <span>&gt; 0 = ${t.over}</span></div>
      <p class="help">${t.splitHelp}</p></section>`;
  }

  _calendar(days) {
    const t = this._t, by = new Map(days.map((d) => [d.date, d]));
    const first = new Date(`${days[0].date}T12:00:00Z`), last = new Date(`${days[days.length - 1].date}T12:00:00Z`);
    const start = new Date(first.getTime() - ((first.getUTCDay() + 6) % 7) * 86400000);
    const weeks = Math.ceil(((last - start) / 86400000 + 1) / 7);
    const c = 16, g = 3, left = 22, W = left + weeks * (c + g), H = 7 * (c + g) + 2;
    let cells = "";
    for (let w = 0; w < weeks; w++) for (let wd = 0; wd < 7; wd++) {
      const day = new Date(start.getTime() + (w * 7 + wd) * 86400000);
      if (day > last) continue;
      const key = day.toISOString().slice(0, 10), d = by.get(key);
      const fill = d ? this._errColor((d.f - d.a) / d.a) : "var(--nx-empty)";
      cells += `<rect x="${left + w * (c + g)}" y="${wd * (c + g)}" width="${c}" height="${c}" rx="3" fill="${fill}"${d ? ` data-day="${key}" class="hit${key === this._s.day ? " selcell" : ""}"` : ""}>${d ? `<title>${this._date(key)}: ${this._pct((d.f - d.a) / d.a, true)}</title>` : ""}</rect>`;
    }
    const wdLabels = [0, 2, 4, 6].map((wd) => `<text x="0" y="${wd * (c + g) + 12}" class="ax">${t.weekdays[wd]}</text>`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="width:${Math.round(W * 1.4)}px;max-width:100%" role="img" aria-label="${t.calendar}">${wdLabels}${cells}</svg>`;
  }

  _hourly(day) {
    const t = this._t, entry = this._data.entries[0];
    const ids = this._s.array === "all" ? entry.arrays.map((a) => a.id) : [this._s.array];
    const hours = day.raw.hours.filter((h) => h.actual);
    const series = SOURCES.map((src) => [src, hours.map((h) => this._fc(h, src, ids))]).filter(([, v]) => v.some((x) => x !== null));
    const actual = hours.map((h) => ids.reduce((s, id) => s + (h.actual[id] || 0), 0));
    const measured = hours.map((h) => (h.measured && ids.every((id) => id in h.measured) ? ids.reduce((s, id) => s + h.measured[id], 0) : null));
    if (measured.some((x) => x !== null)) series.push(["measured", measured]);
    const lit = hours.map((h, i) => actual[i] > 1 || series.some(([, v]) => (v[i] || 0) > 1));
    const i0 = Math.max(0, lit.indexOf(true) - 1), i1 = Math.min(hours.length - 1, lit.lastIndexOf(true) + 1);
    const W = 720, H = 200, pad = 28, n = i1 - i0 + 1, bw = (W - pad) / n;
    const max = Math.max(...actual.slice(i0, i1 + 1), ...series.flatMap(([, v]) => v.slice(i0, i1 + 1).map((x) => x || 0)), 1) * 1.1;
    const y = (v) => H - 18 - (v / max) * (H - 30);
    const colors = { primary: "var(--nx-fc)", open_meteo: "var(--nx-alt)", mix: "var(--nx-mix)", measured: "var(--nx-md)" };
    let svg = "";
    for (let i = i0; i <= i1; i++) {
      const x = pad + (i - i0) * bw, h = hours[i];
      if (h.curtailed) svg += `<rect x="${x.toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${H - 18}" fill="url(#acc-hatch)"></rect>`;
      svg += `<rect x="${(x + bw * 0.15).toFixed(1)}" y="${y(actual[i]).toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${(H - 18 - y(actual[i])).toFixed(1)}" rx="2" class="abar"><title>${this._hm(h.t)} ${t.actual} ${Math.round(actual[i])} Wh</title></rect>`;
      svg += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${this._hm(h.t).slice(0, 2)}</text>`;
    }
    for (const [src, v] of series) {
      const pts = [];
      for (let i = i0; i <= i1; i++) if (v[i] !== null) pts.push(`${(pad + (i - i0 + 0.5) * bw).toFixed(1)},${y(v[i]).toFixed(1)}`);
      svg += `<polyline points="${pts.join(" ")}" fill="none" stroke="${colors[src]}" stroke-width="2" stroke-linejoin="round"${src === "primary" ? "" : ` stroke-dasharray="4 3"`}></polyline>`;
    }
    const sums = series.map(([src, v]) => `<span><i class="sw line" style="background:${colors[src]}"></i>${t[src]} ${this._kwh(v.reduce((a, x) => a + (x || 0), 0))}</span>`).join("");
    const issued = day.raw.issued ? ` · ${t.issued} ${new Date(day.raw.issued).toLocaleString(this._lang, { timeZone: this._data.time_zone, dateStyle: "short", timeStyle: "short" })}` : "";
    return `<h3>${t.hourly} ${this._date(day.date)}<span class="muted small">${issued}</span></h3>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.hourly}"><defs><pattern id="acc-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="var(--nx-hatch)"></rect></pattern></defs>${svg}</svg>
      <div class="legend"><span><i class="sw abar"></i>${t.actual} ${this._kwh(actual.reduce((a, x) => a + x, 0))}</span>${sums}
      ${day.curtailed ? `<span><i class="sw" style="background:var(--nx-hatch)"></i>${t.curtailed}</span>` : ""}</div>`;
  }

  _weather(days) {
    const t = this._t;
    const rows = CLASSES.map(([name, lo, hi]) => {
      const sel = days.filter((d) => d.k !== null && d.k >= lo && d.k < hi);
      const sc = this._score(sel);
      return `<tr><td>${t[name]}</td><td class="num">${sel.length}</td><td class="num">${this._pct(sc.error)}</td>
        <td class="num">${this._pct(sc.bias, true)}</td><td>${this._meter(sc.error)}</td></tr>`;
    }).join("");
    return `<table><thead><tr><th></th><th class="num">${t.days}</th><th class="num">${t.error}</th><th class="num">${t.bias}</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  _compare(days) {
    const t = this._t;
    const per = SOURCES.map((src) => [src, new Map(this._inRange(this._days(src)).map((d) => [d.date, d]))]);
    const common = days.map((d) => d.date).filter((k) => per.every(([, m]) => m.has(k)));
    if (common.length < 3 || per.some(([, m]) => !m.size)) return "";
    const rows = per.map(([src, m]) => {
      const sc = this._score(common.map((k) => m.get(k)));
      return `<tr${src === this._s.source ? ` class="on"` : ""}><td>${t[src]}</td><td class="num">${this._pct(sc.error)}</td><td class="num">${this._pct(sc.bias, true)}</td><td>${this._meter(sc.error)}</td></tr>`;
    }).join("");
    return `<section class="card pad grow"><h3>${t.compare}</h3><p class="muted small">${common.length} ${t.days} ${t.sameDays}</p>
      <table><thead><tr><th></th><th class="num">${t.error}</th><th class="num">${t.bias}</th><th></th></tr></thead><tbody>${rows}</tbody></table></section>`;
  }

  _meter(v) {
    if (v === null) return "";
    const w = Math.min(1, v / 0.6) * 100;
    return `<div class="meter"><div style="width:${w.toFixed(0)}%;background:${this._errColor(v)}"></div></div>`;
  }

  _errColor(e) {
    const a = Math.abs(e);
    const P = this._dark ? ["#4CC77A", "#C9B03A", "#E08A3C", "#E0574A"] : ["#2E9E5B", "#C2A21C", "#D9772B", "#C83A2E"];
    return a < 0.1 ? P[0] : a < 0.25 ? P[1] : a < 0.5 ? P[2] : P[3];
  }

  // ------------------------------------------------------------ events and formatting

  _click(ev) {
    const el = ev.composedPath().find((n) => n.dataset && (n.dataset.set || n.dataset.day));
    if (!el) return;
    if (el.dataset.set) {
      const v = el.dataset.val;
      this._s[el.dataset.set] = el.dataset.set === "range" ? Number(v) : v;
    } else this._s.day = this._s.day === el.dataset.day ? null : el.dataset.day;
    this._render();
  }

  _pct(v, signed = false) {
    if (v === null || v === undefined) return "–";
    const s = (v * 100).toLocaleString(this._lang, { maximumFractionDigits: 0 });
    return `${signed && v > 0 ? "+" : ""}${s} %`;
  }

  _kwh(wh, d = 1) { return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d }); }
  _date(key, short = false) { return new Date(`${key}T12:00:00Z`).toLocaleDateString(this._lang, short ? { day: "numeric", month: "numeric" } : { weekday: "short", day: "numeric", month: "short" }); }
  _hm(ts) { return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", timeZone: this._data.time_zone }); }
  _esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --nx-fc: ${d ? "#F2B233" : "#C27A06"}; --nx-alt: ${d ? "#5AA6E8" : "#2F80C8"}; --nx-mix: ${d ? "#B48CE8" : "#7B4FC2"};
        --nx-bar: ${d ? "rgba(255,255,255,.22)" : "rgba(0,0,0,.16)"}; --nx-bar-on: ${d ? "rgba(255,255,255,.45)" : "rgba(0,0,0,.38)"};
        --nx-empty: ${d ? "rgba(255,255,255,.06)" : "rgba(0,0,0,.05)"}; --nx-hatch: ${d ? "rgba(224,87,74,.35)" : "rgba(200,58,46,.22)"};
        --nx-rail: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)"}; --nx-wx: #22C7E8; --nx-md: #8B5CF6; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
      .grow { flex: 1 1 380px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      h3 { margin: 0 0 8px; font-size: 15px; font-weight: 500; display: flex; gap: 8px; align-items: baseline; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .controls { display: flex; flex-wrap: wrap; gap: 8px 20px; }
      .group { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .chip { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 16px;
        padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
      .chip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
      .figs { display: flex; flex-wrap: wrap; gap: 12px 32px; }
      .fig { display: flex; flex-direction: column; gap: 2px; }
      .big { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
      svg { display: block; width: 100%; height: auto; }
      .ax { font-size: 10px; fill: var(--secondary-text-color); }
      .rail { stroke: var(--nx-rail); }
      .abar { fill: var(--nx-bar); } .abar.on { fill: var(--nx-bar-on); }
      .hit { cursor: pointer; } .selcell { stroke: var(--primary-text-color); stroke-width: 1.5; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; }
      .sw { display: inline-block; width: 14px; height: 10px; border-radius: 2px; }
      .sw.abar { background: var(--nx-bar); } .sw.line { height: 3px; }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 12px; padding: 4px 6px; }
      td { padding: 6px; border-top: 1px solid var(--divider-color); }
      tr.on td { font-weight: 500; }
      .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .meter { width: 80px; height: 6px; border-radius: 3px; background: var(--nx-empty); overflow: hidden; }
      .meter div { height: 100%; border-radius: 3px; }
      .wx { fill: var(--nx-wx); opacity: .75; } .md { fill: var(--nx-md); opacity: .75; } .wx.on, .md.on { opacity: 1; }
      .zero { stroke: var(--secondary-text-color); stroke-opacity: .5; }
      .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
      .share { height: 8px; border-radius: 4px; margin: 14px 0 6px; background: linear-gradient(90deg, var(--nx-md), color-mix(in srgb, var(--nx-md) 70%, #fff)); overflow: hidden; }
      .share div { height: 100%; background: linear-gradient(90deg, var(--nx-wx), color-mix(in srgb, var(--nx-wx) 70%, #fff)); box-shadow: 0 0 10px var(--nx-wx); }
      .verdict { margin: 4px 0 10px; font-size: 13px; line-height: 1.45; }
    </style>`;
  }
}

customElements.define("noxcast-accuracy", NoxcastAccuracy);
