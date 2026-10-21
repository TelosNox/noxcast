// Noxcast panel section "Load": the learned load profile, the consumers and the forecast by part.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/load".

const LOAD_TEXT = {
  de: {
    noLoad: "Die Lastprognose ist nicht eingerichtet. In der Noxcast-Konfiguration den Hausverbrauch-Sensor eintragen und Verbraucher mit eigenem Sensor hinzufügen.",
    notLearned: "Das Lastprofil wird beim nächsten Lernlauf erstellt.",
    restToday: "Last Rest heute", tomorrow: "Last morgen", correction: "Korrektur jetzt", days: "Tage Daten",
    backtest: "Rückrechnung", model: "Modell", naive: "7-Tage-Mittel", better: "besser", worse: "schlechter", cases: "Fälle",
    forecast: "Prognose nach Anteilen", base: "Grundlast", profile: "Gelerntes Profil (Median je Stunde)",
    workday: "Werktag", weekend: "Wochenende", consumers: "Verbraucher", timing: "Zeitbindung", sensor: "Sensor",
    perDay: "Ø pro Tag", share: "Anteil", fixed: "fest", flexible: "flexibel", noConsumers: "Keine Verbraucher angelegt. Alles zählt als Grundlast.",
    flexNote: "Flexible Verbraucher zählen nur, solange sie laufen; im Profil sind sie deshalb nicht enthalten.",
    help: "Grundlast = Hausverbrauch minus aller Verbraucher. Die Korrektur vergleicht die letzten Stunden mit dem Profil und klingt über die nächsten Stunden auf 1 ab. Die Rückrechnung prognostiziert die letzten 14 Tage um 6, 12 und 18 Uhr mit dem damaligen Wissen und vergleicht den Fehler der Restlast mit einem einfachen 7-Tage-Mittel.",
    kwh: "kWh", now: "jetzt", today: "heute",
  },
  en: {
    noLoad: "The load forecast is not set up. Set the house load sensor in the Noxcast configuration and add consumers with their own sensor.",
    notLearned: "The load profile is created by the next learning run.",
    restToday: "Load rest of today", tomorrow: "Load tomorrow", correction: "Correction now", days: "days of data",
    backtest: "Backtest", model: "Model", naive: "7-day mean", better: "better", worse: "worse", cases: "cases",
    forecast: "Forecast by part", base: "Base load", profile: "Learned profile (median per hour)",
    workday: "Workday", weekend: "Weekend", consumers: "Consumers", timing: "Timing", sensor: "Sensor",
    perDay: "Avg per day", share: "Share", fixed: "fixed", flexible: "flexible", noConsumers: "No consumers added. Everything counts as base load.",
    flexNote: "Flexible consumers only count while they run, so they are not part of the profile.",
    help: "Base load = house load minus all consumers. The correction compares the last hours with the profile and decays to 1 over the next hours. The backtest forecasts the last 14 days at 6, 12 and 18 o'clock with the knowledge of that moment and compares the error of the remaining load with a simple 7-day mean.",
    kwh: "kWh", now: "now", today: "today",
  },
};

const LOAD_COLORS = { light: ["#2F80C8", "#D9772B", "#7B4FC2", "#2E9E5B", "#C2A21C", "#C83A2E"], dark: ["#5AA6E8", "#E08A3C", "#B48CE8", "#4CC77A", "#C9B03A", "#E0574A"] };

class NoxcastLoad extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    const first = !this._hass, dark = this._dark;
    this._hass = hass;
    if (first) this._load();
    else if (this._dark !== dark) this._render();
  }

  connectedCallback() { if (this._hass && this._data && Date.now() - this._loaded > 300000) this._load(); }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return LOAD_TEXT[(this._hass.language || "en").slice(0, 2)] || LOAD_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "noxcast/load" });
      this._loaded = Date.now();
      this._error = null;
    } catch (err) {
      this._error = String((err && err.message) || err);
    }
    this._render();
  }

  _render() {
    const t = this._t, e = this._data && (this._data.entries || [])[0];
    let body;
    if (this._error) body = `<section class="card pad">${this._esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!e) body = `<section class="card pad">${t.noLoad}</section>`;
    else if (!e.profile) body = `<section class="card pad">${t.notLearned}</section>`;
    else body = this._view(e);
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">${body}</div>`;
  }

  // ------------------------------------------------------------ data helpers

  _dayBounds(offset) {
    const tz = this._data.time_zone, now = new Date(this._data.now * 1000);
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(now).map((p) => [p.type, p.value]));
    const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
    const offsetMs = asUtc - Math.floor(now.getTime() / 1000) * 1000;
    const start = (Date.UTC(+parts.year, +parts.month - 1, +parts.day + offset) - offsetMs) / 1000;
    return [start, start + 86400];
  }

  // hourly series of the forecast: [t, base, {consumer: W}, total]
  _hours(e) {
    const fc = e.forecast;
    if (!fc) return [];
    const parts = Object.fromEntries(Object.entries(fc.consumers).map(([sid, s]) => [sid, new Map(s)]));
    return fc.base.map(([ts, b]) => {
      const c = Object.fromEntries(Object.entries(parts).map(([sid, m]) => [sid, m.get(ts) || 0]));
      return [ts, b, c, b + Object.values(c).reduce((a, v) => a + v, 0)];
    });
  }

  _sum(hours, a, b, now) {
    let s = 0;
    for (const [ts, , , total] of hours) {
      if (ts + 3600 <= a || ts >= b) continue;
      s += total * (ts < now && now < ts + 3600 ? (ts + 3600 - now) / 3600 : 1);
    }
    return s;
  }

  // ------------------------------------------------------------ views

  _view(e) {
    const t = this._t, now = this._data.now, hours = this._hours(e);
    const [, todayEnd] = this._dayBounds(0), [tomA, tomB] = this._dayBounds(1);
    const bt = e.backtest || {};
    const gain = bt.mae_kwh != null && bt.naive_mae_kwh ? 1 - bt.mae_kwh / bt.naive_mae_kwh : null;
    const figs = [
      [t.restToday, `${this._kwh(this._sum(hours, now, todayEnd, now))} ${t.kwh}`],
      [t.tomorrow, `${this._kwh(this._sum(hours, tomA, tomB, now))} ${t.kwh}`],
      [t.correction, e.forecast ? `× ${this._num(e.forecast.k, 2)}` : "–"],
      [t.days, String(e.profile.days || 0)],
    ].map(([k, v]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span></div>`).join("");
    const btFig = bt.mae_kwh == null ? "" : `<div class="fig"><span class="muted small">${t.backtest} (${bt.cases} ${t.cases})</span>
      <span class="big">${this._num(bt.mae_kwh, 2)} <span class="muted small">${t.kwh} ${t.model}</span></span>
      <span class="small muted">${t.naive} ${this._num(bt.naive_mae_kwh, 2)} ${t.kwh}${gain === null ? "" : ` · <b class="${gain >= 0 ? "good" : "bad"}">${Math.abs(Math.round(gain * 100))} % ${gain >= 0 ? t.better : t.worse}</b>`}</span></div>`;
    return `
      <section class="card pad"><div class="figs">${figs}${btFig}</div><p class="help">${t.help}</p></section>
      ${hours.length ? `<section class="card pad"><h3>${t.forecast}</h3>${this._stack(e, hours, todayEnd)}</section>` : ""}
      <div class="split">
        <section class="card pad grow"><h3>${t.profile}</h3>${this._profile(e)}</section>
        <section class="card pad grow"><h3>${t.consumers}</h3>${this._consumers(e)}</section>
      </div>`;
  }

  _color(i) { return (this._dark ? LOAD_COLORS.dark : LOAD_COLORS.light)[i % 6]; }

  _stack(e, hours, todayEnd) {
    const t = this._t, W = 900, H = 220, pad = 34, n = hours.length, bw = (W - pad) / n;
    const max = Math.max(...hours.map((h) => h[3]), 100) * 1.08;
    const y = (v) => H - 18 - (v / max) * (H - 28);
    const step = [100, 200, 250, 500, 1000, 2000, 5000].find((s) => max / s <= 5) || 10000;
    let svg = "";
    for (let v = step; v < max; v += step) svg += `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line><text x="${pad - 4}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${v >= 1000 ? `${v / 1000} k` : v}</text>`;
    const ids = e.consumers.map((c) => c.id);
    hours.forEach(([ts, base, parts, total], i) => {
      const x = pad + i * bw;
      let acc = 0;
      const seg = (v, fill) => { if (v <= 0) return ""; const r = `<rect x="${(x + 1).toFixed(1)}" y="${y(acc + v).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${(y(acc) - y(acc + v)).toFixed(1)}" fill="${fill}"></rect>`; acc += v; return r; };
      const title = `${this._hm(ts)}: ${Math.round(total)} W (${t.base} ${Math.round(base)}${ids.filter((id) => parts[id] > 0).map((id) => `, ${this._esc(e.consumers.find((c) => c.id === id).name)} ${Math.round(parts[id])}`).join("")})`;
      svg += `<g><title>${title}</title>${seg(base, "var(--nx-base)")}${ids.map((id, k) => seg(parts[id] || 0, this._color(k))).join("")}</g>`;
      if (ts === todayEnd) svg += `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="0" y2="${H - 18}" stroke="var(--secondary-text-color)" stroke-dasharray="3 3"></line>`;
      const hour = Number(this._hm(ts).slice(0, 2));
      if (hour % 3 === 0) svg += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${hour}</text>`;
    });
    const legend = [`<span><i style="background:var(--nx-base)"></i>${t.base}</span>`,
      ...e.consumers.map((c, k) => `<span><i style="background:${this._color(k)}"></i>${this._esc(c.name)}</span>`)].join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.forecast}">${svg}</svg><div class="legend">${legend}</div>`;
  }

  _profile(e) {
    const t = this._t, W = 640, H = 200, pad = 34;
    const cells = e.profile.cells || {}, fb = e.profile.fallback || [];
    const val = (arr, h) => (arr && arr[h] != null ? arr[h] : fb[h]);
    const lines = [[t.workday, (h) => val(cells.workday, h), "var(--nx-base-line)", ""], [t.weekend, (h) => val(cells.weekend, h), "var(--nx-base-line)", "4 3"]];
    e.consumers.forEach((c, k) => {
      if (c.timing === "fixed" && c.profile) lines.push([c.name, (h) => val(c.profile.cells && c.profile.cells.workday, h) ?? (c.profile.fallback || [])[h], this._color(k), ""]);
    });
    const max = Math.max(100, ...lines.flatMap(([, f]) => Array.from({ length: 24 }, (_, h) => f(h) || 0))) * 1.1;
    const x = (h) => pad + (h + 0.5) / 24 * (W - pad), y = (v) => H - 18 - (v / max) * (H - 28);
    const step = [50, 100, 200, 250, 500, 1000, 2000].find((s) => max / s <= 5) || 5000;
    let svg = "";
    for (let v = step; v < max; v += step) svg += `<line x1="${pad}" x2="${W}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="rail"></line><text x="${pad - 4}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" class="ax">${v}</text>`;
    for (let h = 0; h < 24; h += 3) svg += `<text x="${x(h).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="ax">${h}</text>`;
    for (const [name, f, color, dash] of lines) {
      const pts = Array.from({ length: 24 }, (_, h) => f(h)).map((v, h) => (v == null ? null : `${x(h).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
      svg += `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"${dash ? ` stroke-dasharray="${dash}"` : ""}><title>${this._esc(name)}</title></polyline>`;
    }
    const legend = lines.map(([name, , color, dash]) => `<span><i class="ln" style="border-top:2px ${dash ? "dashed" : "solid"} ${color}"></i>${this._esc(name)}</span>`).join("");
    const hasFlex = e.consumers.some((c) => c.timing === "flexible");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.profile}">${svg}</svg><div class="legend">${legend}</div>${hasFlex ? `<p class="help">${t.flexNote}</p>` : ""}`;
  }

  _consumers(e) {
    const t = this._t;
    if (!e.consumers.length) return `<p class="muted">${t.noConsumers}</p>`;
    const daily = (p) => (p ? (p.fallback || []).reduce((a, v) => a + (v || 0), 0) : 0);
    const base = daily(e.profile);
    const rows = e.consumers.map((c, k) => [c, daily(c.profile), k]);
    const total = base + rows.reduce((a, [, d]) => a + d, 0);
    const pct = (v) => (total > 0 ? `${Math.round((v / total) * 100)} %` : "–");
    return `<table><thead><tr><th></th><th>${t.timing}</th><th class="num">${t.perDay}</th><th class="num">${t.share}</th></tr></thead><tbody>
      <tr><td><i class="dot" style="background:var(--nx-base)"></i>${t.base}</td><td></td><td class="num">${this._kwh(base)} ${t.kwh}</td><td class="num">${pct(base)}</td></tr>
      ${rows.map(([c, d, k]) => `<tr><td><i class="dot" style="background:${this._color(k)}"></i>${this._esc(c.name)}<div class="muted small mono">${this._esc(c.sensor || "")}</div></td>
        <td>${t[c.timing] || c.timing}</td><td class="num">${this._kwh(d)} ${t.kwh}</td><td class="num">${pct(d)}</td></tr>`).join("")}
    </tbody></table>`;
  }

  // ------------------------------------------------------------ formatting

  _num(v, d) { return Number(v).toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d }); }
  _kwh(wh) { return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }
  _hm(ts) { return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: this._data.time_zone }); }
  _esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --nx-base: ${d ? "rgba(255,255,255,.28)" : "rgba(0,0,0,.22)"}; --nx-base-line: ${d ? "#D6DADF" : "#4F5964"};
        --nx-rail: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
      .grow { flex: 1 1 380px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      h3 { margin: 0 0 8px; font-size: 15px; font-weight: 500; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; } .mono { font-family: var(--code-font-family, monospace); }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .figs { display: flex; flex-wrap: wrap; gap: 12px 32px; }
      .fig { display: flex; flex-direction: column; gap: 2px; }
      .big { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
      .good { color: ${d ? "#4CC77A" : "#2E9E5B"}; } .bad { color: ${d ? "#E0574A" : "#C83A2E"}; }
      svg { display: block; width: 100%; height: auto; }
      .ax { font-size: 10px; fill: var(--secondary-text-color); }
      .rail { stroke: var(--nx-rail); }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; }
      .legend i { display: inline-block; width: 12px; height: 10px; border-radius: 2px; } .legend i.ln { height: 0; width: 16px; border-radius: 0; }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 12px; padding: 4px 6px; }
      td { padding: 6px; border-top: 1px solid var(--divider-color); vertical-align: top; }
      .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
    </style>`;
  }
}

customElements.define("noxcast-load", NoxcastLoad);
