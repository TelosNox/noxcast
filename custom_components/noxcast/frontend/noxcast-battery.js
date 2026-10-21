// Noxcast panel section "Battery & surplus": what-if runs on the current forecast and the last 30 days.
// Plain custom element, no build step. Data comes from the websocket command "noxcast/battery".
// The what-if simulation mirrors battery.simulate in the integration; without changes it gives the same numbers.

const BAT_TEXT = {
  de: {
    noBattery: "Kein Akku eingerichtet. In der Noxcast-Konfiguration einen Akku hinzufügen.",
    noSim: "Für die Simulation fehlen Lastprognose oder Ladestand. Die Lastprognose braucht den Hausverbrauch-Sensor und einen Lernlauf.",
    socNow: "Ladestand jetzt", fullAt: "Voll um", emptyAt: "Leer um", lost: "Überschuss ohne Platz", grid: "Netzbezug",
    until: "bis Ladebeginn morgen", never: "–",
    whatIf: "Was wäre, wenn …", capacity: "Akkukapazität", consumer: "Zusätzlicher Verbraucher", power: "Leistung",
    duration: "Dauer", start: "Start", reset: "Zurücksetzen", now: "jetzt", base: "Wie geplant", variant: "Variante",
    share: "Davon aus PV und Akku", chart: "Ladestand und Leistung", pv: "PV erwartet", load: "Last", soc: "Ladestand",
    socBase: "Ladestand wie geplant", socEnd: "Im Akku morgen 24 Uhr",
    review: "Rückblick", reviewHelp: "Tiefster und höchster Ladestand je Tag aus der Langzeitstatistik der Ladestand-Sensoren. Voll = obere Grenze erreicht, leer = untere Grenze plus Reserve erreicht.",
    daysFull: "Tage voll", daysEmpty: "Tage leer", noStats: "Noch keine Statistik für die Ladestand-Sensoren.",
    help: "Alle Akkus zählen als ein Speicher. Überschuss ohne Platz wird eingespeist oder abgeregelt. Die Variante rechnet mit derselben Prognose; nur Kapazität und der zusätzliche Verbraucher ändern sich.",
    horizon: "(bis morgen 24 Uhr)", kwh: "kWh", h: "h", today: "heute", tomorrow: "morgen",
  },
  en: {
    noBattery: "No battery set up. Add a battery in the Noxcast configuration.",
    noSim: "The simulation needs the load forecast and the state of charge. The load forecast needs the house load sensor and a learning run.",
    socNow: "State of charge now", fullAt: "Full at", emptyAt: "Empty at", lost: "Surplus without room", grid: "From grid",
    until: "until tomorrow's charge start", never: "–",
    whatIf: "What if …", capacity: "Battery capacity", consumer: "Additional consumer", power: "Power",
    duration: "Duration", start: "Start", reset: "Reset", now: "now", base: "As planned", variant: "Variant",
    share: "Covered by PV and battery", chart: "State of charge and power", pv: "PV expected", load: "Load", soc: "State of charge",
    socBase: "State of charge as planned", socEnd: "In the battery tomorrow midnight",
    review: "Review", reviewHelp: "Lowest and highest state of charge per day from the long-term statistics of the state of charge sensors. Full = upper limit reached, empty = lower limit plus reserve reached.",
    daysFull: "days full", daysEmpty: "days empty", noStats: "No statistics for the state of charge sensors yet.",
    help: "All batteries count as one storage. Surplus without room is fed in or curtailed. The variant uses the same forecast; only the capacity and the additional consumer change.",
    horizon: "(until tomorrow midnight)", kwh: "kWh", h: "h", today: "today", tomorrow: "tomorrow",
  },
};

const H = 3600;

// state of charge simulation, same rules as battery.simulate
function simulate(st, hours, now, end) {
  const cap = st.capacity_wh, eta = Math.sqrt(Math.max(0.01, Math.min(1, st.efficiency)));
  let e = (st.soc ?? st.min) * cap;
  const eMax = st.max * cap, eMin = st.min * cap;
  const r = { points: [[now, e / cap]], full: null, empty: null, lost: 0, grid: 0 };
  for (const [t, pv, load] of hours) {
    if (t + H <= now || t >= end) continue;
    const start = Math.max(t, now), stop = Math.min(t + H, end), share = (stop - start) / H;
    const net = (pv - load) * share;
    if (net > 0) {
      const room = Math.max(eMax - e, 0) / eta, limit = st.max_charge_w ? st.max_charge_w * share : Infinity;
      const take = Math.min(net, room, limit);
      e += take * eta;
      if (take < net && room <= limit && r.full === null) r.full = start + (take / net) * (stop - start);
      r.lost += net - take;
    } else if (net < 0) {
      const need = -net, avail = Math.max(e - eMin, 0) * eta, limit = st.max_discharge_w ? st.max_discharge_w * share : Infinity;
      const give = Math.min(need, avail, limit);
      e -= give / eta;
      if (give < need && avail <= limit && r.empty === null) r.empty = start + (give / need) * (stop - start);
      r.grid += need - give;
    }
    r.points.push([stop, e / cap]);
  }
  return r;
}

class NoxcastBattery extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._v = null;          // what-if settings: capacity_wh, power, minutes, start
  }

  set hass(hass) {
    const first = !this._hass, dark = this._dark;
    this._hass = hass;
    if (first) this._load();
    else if (this._dark !== dark) this._render();
  }

  connectedCallback() { if (this._hass && this._data && Date.now() - this._loaded > 300000) this._load(); }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return BAT_TEXT[(this._hass.language || "en").slice(0, 2)] || BAT_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    try {
      this._data = await this._hass.connection.sendMessagePromise({ type: "noxcast/battery" });
      this._loaded = Date.now();
      this._error = null;
    } catch (err) {
      this._error = String((err && err.message) || err);
    }
    this._render();
  }

  _defaults(e) {
    return { capacity_wh: e.storage.capacity_wh, power: 0, minutes: 120, start: Math.ceil(this._data.now / 900) * 900 };
  }

  _render() {
    const t = this._t, e = this._data && (this._data.entries || [])[0];
    let body;
    if (this._error) body = `<section class="card pad">${this._esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else if (!e) body = `<section class="card pad">${t.noBattery}</section>`;
    else {
      if (e.storage && !this._v) this._v = this._defaults(e);
      body = `${e.storage ? this._whatIf(e) : `<section class="card pad">${t.noSim}</section>`}${this._review(e)}`;
    }
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">${body}</div>`;
    this.shadowRoot.querySelectorAll("input[data-k]").forEach((el) => el.addEventListener("input", () => {
      this._v[el.dataset.k] = Number(el.value);
      this._update(e);
    }));
    const reset = this.shadowRoot.querySelector("#reset");
    if (reset) reset.addEventListener("click", () => { this._v = this._defaults(e); this._render(); });
  }

  // ------------------------------------------------------------ what-if

  _variant(e) {
    const v = this._v, a = v.start, b = v.start + v.minutes * 60;
    const hours = e.hours.map(([ts, pv, load]) => {
      const overlap = Math.max(0, Math.min(ts + H, b) - Math.max(ts, a)) / H;
      return [ts, pv, load + v.power * overlap];
    });
    return { sim: simulate({ ...e.storage, capacity_wh: v.capacity_wh }, hours, this._data.now, this._end(e)), hours };
  }

  // the variant runs to the end of the forecast (tomorrow midnight), so a consumer can be placed in tomorrow's surplus
  _end(e) { return e.hours.length ? e.hours[e.hours.length - 1][0] + H : e.charge_start; }

  _results(e) {
    const t = this._t, now = this._data.now;
    const base = simulate(e.storage, e.hours, now, this._end(e)), { sim, hours } = this._variant(e);
    const v = this._v, energy = v.power * v.minutes / 60;
    const extra = sim.grid - base.grid;
    const row = (label, a, b, fmt, goodIfLower) => {
      const diff = b - a, cls = Math.abs(diff) < 1 ? "" : (diff < 0) === goodIfLower ? "good" : "bad";
      return `<tr><td>${label}</td><td class="num">${fmt(a)}</td><td class="num ${cls}">${fmt(b)}</td></tr>`;
    };
    const time = (ts) => (ts === null ? t.never : this._when(ts));
    const timeRow = (label, a, b) => `<tr><td>${label}</td><td class="num">${time(a)}</td><td class="num${a !== b ? " chg" : ""}">${time(b)}</td></tr>`;
    const kwh = (wh) => `${this._kwh(wh)} ${t.kwh}`, last = (r) => r.points[r.points.length - 1][1];
    const table = `<table><thead><tr><th></th><th class="num">${t.base}</th><th class="num">${t.variant}</th></tr></thead><tbody>
      ${timeRow(t.fullAt, base.full, sim.full)}${timeRow(t.emptyAt, base.empty, sim.empty)}
      ${row(t.lost, base.lost, sim.lost, kwh, true)}${row(t.grid, base.grid, sim.grid, kwh, true)}
      ${row(t.socEnd, last(base) * e.storage.capacity_wh, last(sim) * v.capacity_wh, kwh, false)}
      ${energy > 0 ? `<tr><td>${t.share}</td><td></td><td class="num"><b>${Math.round(Math.max(0, Math.min(1, 1 - extra / energy)) * 100)} %</b></td></tr>` : ""}
    </tbody></table>`;
    return { table, chart: this._chart(e, base, sim, hours) };
  }

  _whatIf(e) {
    const t = this._t, st = e.storage, v = this._v, now = this._data.now;
    const figs = [
      [t.socNow, `${Math.round((st.soc ?? 0) * 100)} %`],
      [t.fullAt, e.sim.full ? this._when(e.sim.full) : t.never],
      [t.emptyAt, e.sim.empty ? this._when(e.sim.empty) : t.never],
      [t.lost, `${this._kwh(e.sim.lost_wh)} ${t.kwh}`],
      [t.grid, `${this._kwh(e.sim.grid_wh)} ${t.kwh}`],
    ].map(([k, val]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${val}</span></div>`).join("");
    const lastStart = Math.max(now, this._end(e) - 900);
    const res = this._results(e);
    const slider = (k, label, min, max, step, out) => `<label class="ctl"><span>${label}</span>
      <input type="range" data-k="${k}" min="${min}" max="${max}" step="${step}" value="${v[k]}"><output id="o-${k}">${out}</output></label>`;
    return `
      <section class="card pad"><div class="figs">${figs}</div><p class="help">${t.until}: ${this._when(e.charge_start)}. ${t.help}</p></section>
      <section class="card pad"><div class="head"><h3>${t.whatIf} <span class="muted small">${t.horizon}</span></h3><button id="reset">${t.reset}</button></div>
        <div class="ctls">
          ${slider("capacity_wh", t.capacity, 0, Math.max(30000, st.capacity_wh * 3), 500, `${this._kwh(v.capacity_wh)} ${t.kwh}`)}
          <div class="sub muted small">${t.consumer}</div>
          ${slider("power", t.power, 0, 5000, 100, `${v.power} W`)}
          ${slider("minutes", t.duration, 15, 480, 15, this._dur(v.minutes))}
          ${slider("start", t.start, Math.ceil(now / 900) * 900, lastStart, 900, this._when(v.start))}
        </div>
        <div class="split"><div class="grow" id="table">${res.table}</div><div class="grow2" id="chart">${res.chart}</div></div>
      </section>`;
  }

  _update(e) {
    const t = this._t, v = this._v, root = this.shadowRoot;
    root.querySelector("#o-capacity_wh").textContent = `${this._kwh(v.capacity_wh)} ${t.kwh}`;
    root.querySelector("#o-power").textContent = `${v.power} W`;
    root.querySelector("#o-minutes").textContent = this._dur(v.minutes);
    root.querySelector("#o-start").textContent = this._when(v.start);
    const res = this._results(e);
    root.querySelector("#table").innerHTML = res.table;
    root.querySelector("#chart").innerHTML = res.chart;
  }

  _chart(e, base, sim, hours) {
    const t = this._t, W = 640, Hh = 220, pad = 34, now = this._data.now, end = this._end(e);
    const shown = hours.filter(([ts]) => ts + H > now && ts < end);
    if (!shown.length) return "";
    const t0 = Math.max(shown[0][0], now - (now % H)), t1 = end;
    const x = (ts) => pad + ((ts - t0) / (t1 - t0 || 1)) * (W - pad - 30);
    const max = Math.max(500, ...shown.flatMap(([, pv, load]) => [pv, load])) * 1.08;
    const y = (w) => Hh - 18 - (w / max) * (Hh - 28), ys = (f) => Hh - 18 - f * (Hh - 28);
    const step = [250, 500, 1000, 2000, 5000].find((s) => max / s <= 5) || 10000;
    let svg = `<defs><linearGradient id="nx-pvfill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--nx-pv-line)" stop-opacity=".55"></stop>
      <stop offset="1" stop-color="var(--nx-pv-line)" stop-opacity=".04"></stop></linearGradient></defs>`;
    for (let w = step; w < max; w += step) svg += `<line x1="${pad}" x2="${W - 30}" y1="${y(w).toFixed(1)}" y2="${y(w).toFixed(1)}" class="rail"></line><text x="${pad - 4}" y="${(y(w) + 3).toFixed(1)}" text-anchor="end" class="ax">${w >= 1000 ? `${w / 1000} k` : w}</text>`;
    for (const f of [0, 0.5, 1]) svg += `<text x="${W - 26}" y="${(ys(f) + 3).toFixed(1)}" class="ax">${f * 100} %</text>`;
    const st = e.storage;
    svg += `<line x1="${pad}" x2="${W - 30}" y1="${ys(st.max).toFixed(1)}" y2="${ys(st.max).toFixed(1)}" class="lim"></line><line x1="${pad}" x2="${W - 30}" y1="${ys(st.min).toFixed(1)}" y2="${ys(st.min).toFixed(1)}" class="lim"></line>`;
    const area = shown.map(([ts, pv]) => `${x(Math.max(ts, t0)).toFixed(1)},${y(pv).toFixed(1)} ${x(Math.min(ts + H, t1)).toFixed(1)},${y(pv).toFixed(1)}`).join(" ");
    svg += `<polygon points="${x(t0).toFixed(1)},${y(0)} ${area} ${x(t1).toFixed(1)},${y(0)}" class="pv"></polygon>`;
    const loadLine = shown.map(([ts, , load]) => `${x(Math.max(ts, t0)).toFixed(1)},${y(load).toFixed(1)} ${x(Math.min(ts + H, t1)).toFixed(1)},${y(load).toFixed(1)}`).join(" ");
    svg += `<polyline points="${loadLine}" class="load"></polyline>`;
    const socLine = (pts, cls) => `<polyline points="${pts.map(([ts, f]) => `${x(ts).toFixed(1)},${ys(f).toFixed(1)}`).join(" ")}" class="${cls}"></polyline>`;
    svg += socLine(base.points, "soc0") + socLine(sim.points, "soc");
    for (let ts = Math.ceil(t0 / H) * H; ts <= t1; ts += H) {
      const hour = Number(this._hm(ts).slice(0, 2));
      if (hour % 3 === 0) svg += `<text x="${x(ts).toFixed(1)}" y="${Hh - 4}" text-anchor="middle" class="ax">${hour}</text>`;
      if (ts === e.day_end) svg += `<line x1="${x(ts).toFixed(1)}" x2="${x(ts).toFixed(1)}" y1="0" y2="${Hh - 18}" stroke="var(--secondary-text-color)" stroke-dasharray="3 3"></line>`;
    }
    const legend = [[t.pv, `<i class="sw pvsw"></i>`], [t.load, `<i class="ln" style="border-top:2px solid var(--nx-load)"></i>`],
      [t.soc, `<i class="ln" style="border-top:2px solid var(--nx-soc)"></i>`], [t.socBase, `<i class="ln" style="border-top:2px dashed var(--nx-soc0)"></i>`]]
      .map(([n, i]) => `<span>${i}${n}</span>`).join("");
    return `<svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="${t.chart}">${svg}</svg><div class="legend">${legend}</div>`;
  }

  // ------------------------------------------------------------ review

  _review(e) {
    const t = this._t, days = e.days || [];
    if (!days.length) return `<section class="card pad"><h3>${t.review}</h3><p class="muted">${t.noStats}</p></section>`;
    const st = e.storage || {}, hi = st.max ?? 1, lo = st.min ?? 0;
    const full = days.filter((d) => d.max >= hi - 0.02).length, empty = days.filter((d) => d.min <= lo + 0.02).length;
    const W = 900, Hh = 180, pad = 34, n = days.length, bw = (W - pad) / n, y = (f) => Hh - 18 - f * (Hh - 28);
    let svg = "";
    for (const f of [0, 0.5, 1]) svg += `<line x1="${pad}" x2="${W}" y1="${y(f).toFixed(1)}" y2="${y(f).toFixed(1)}" class="rail"></line><text x="${pad - 4}" y="${(y(f) + 3).toFixed(1)}" text-anchor="end" class="ax">${f * 100}</text>`;
    svg += `<line x1="${pad}" x2="${W}" y1="${y(hi).toFixed(1)}" y2="${y(hi).toFixed(1)}" class="lim"></line><line x1="${pad}" x2="${W}" y1="${y(lo).toFixed(1)}" y2="${y(lo).toFixed(1)}" class="lim"></line>`;
    days.forEach((d, i) => {
      const x = pad + i * bw, isFull = d.max >= hi - 0.02, isEmpty = d.min <= lo + 0.02;
      svg += `<g><title>${this._date(d.date)}: ${Math.round(d.min * 100)}–${Math.round(d.max * 100)} %</title>
        <rect x="${(x + 2).toFixed(1)}" y="${y(d.max).toFixed(1)}" width="${Math.max(1, bw - 4).toFixed(1)}" height="${Math.max(2, y(d.min) - y(d.max)).toFixed(1)}" rx="2" class="${isFull ? "full" : isEmpty ? "empty" : "bar"}"></rect></g>`;
      if (i % Math.ceil(n / 10) === 0) svg += `<text x="${(x + bw / 2).toFixed(1)}" y="${Hh - 4}" text-anchor="middle" class="ax">${this._date(d.date)}</text>`;
    });
    const figs = [[t.daysFull, `${full} / ${n}`], [t.daysEmpty, `${empty} / ${n}`]]
      .map(([k, v]) => `<div class="fig"><span class="muted small">${k}</span><span class="big">${v}</span></div>`).join("");
    return `<section class="card pad"><h3>${t.review} (${this._data.review_days} d)</h3><div class="figs">${figs}</div>
      <svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="${t.review}">${svg}</svg><p class="help">${t.reviewHelp}</p></section>`;
  }

  // ------------------------------------------------------------ formatting

  _kwh(wh) { return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 }); }
  _hm(ts) { return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: this._data.time_zone }); }
  _dayKey(ts) { return new Date(ts * 1000).toLocaleDateString("en-CA", { timeZone: this._data.time_zone }); }
  _when(ts) {
    const t = this._t, d = this._dayKey(ts), today = this._dayKey(this._data.now);
    return `${d === today ? "" : `${t.tomorrow} `}${this._hm(ts)}`;
  }
  _date(key) { const [y, m, d] = key.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(this._lang, { day: "numeric", month: "numeric", timeZone: "UTC" }); }
  _dur(min) { return min < 60 ? `${min} min` : `${(min / 60).toLocaleString(this._lang, { maximumFractionDigits: 2 })} ${this._t.h}`; }
  _esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --nx-rail: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)"};
        --nx-pv: ${d ? "rgba(240,180,40,.35)" : "rgba(240,170,20,.30)"}; --nx-pv-line: ${d ? "#F0B428" : "#D99A00"};
        --nx-load: ${d ? "#D6DADF" : "#4F5964"}; --nx-soc: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-soc0: ${d ? "rgba(76,199,122,.55)" : "rgba(46,158,91,.5)"};
        --nx-good: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-bad: ${d ? "#E0574A" : "#C83A2E"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; margin-top: 12px; }
      .grow { flex: 1 1 260px; min-width: 0; } .grow2 { flex: 2 1 380px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      .head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
      h3 { margin: 0 0 8px; font-size: 15px; font-weight: 500; }
      button { font: inherit; font-size: 13px; color: var(--primary-color); background: none; border: 1px solid var(--divider-color);
        border-radius: 8px; padding: 4px 10px; cursor: pointer; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .figs { display: flex; flex-wrap: wrap; gap: 12px 32px; margin-bottom: 8px; }
      .fig { display: flex; flex-direction: column; gap: 2px; }
      .big { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
      .ctls { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px 24px; }
      .sub { grid-column: 1 / -1; margin-top: 4px; }
      .ctl { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 2px 8px; font-size: 13px; }
      .ctl input { grid-column: 1 / -1; width: 100%; accent-color: var(--primary-color); }
      .ctl output { font-variant-numeric: tabular-nums; font-weight: 500; }
      svg { display: block; width: 100%; height: auto; }
      .ax { font-size: 10px; fill: var(--secondary-text-color); }
      .rail { stroke: var(--nx-rail); } .lim { stroke: var(--secondary-text-color); stroke-dasharray: 2 4; opacity: .6; }
      .pv { fill: url(#nx-pvfill); stroke: var(--nx-pv-line); stroke-width: 1.2; stroke-linejoin: round; }
      .load { fill: none; stroke: var(--nx-load); stroke-width: 1.6; }
      .soc { fill: none; stroke: var(--nx-soc); stroke-width: 2.6; stroke-linejoin: round; stroke-linecap: round;
        filter: drop-shadow(0 0 5px color-mix(in srgb, var(--nx-soc) 55%, transparent)); }
      .soc0 { fill: none; stroke: var(--nx-soc0); stroke-width: 1.6; stroke-dasharray: 5 4; }
      .bar { fill: var(--secondary-text-color); opacity: .35; } .full { fill: var(--nx-soc); filter: drop-shadow(0 0 4px color-mix(in srgb, var(--nx-soc) 50%, transparent)); }
      .empty { fill: var(--nx-bad); opacity: .85; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
      .legend span { display: inline-flex; align-items: center; gap: 5px; }
      .legend i { display: inline-block; } .legend i.ln { width: 16px; height: 0; } .legend i.sw { width: 12px; height: 10px; border-radius: 2px; }
      .pvsw { background: var(--nx-pv); border: 1px solid var(--nx-pv-line); }
      table { width: 100%; border-collapse: collapse; font-size: 13px; }
      th { text-align: left; font-weight: 500; color: var(--secondary-text-color); font-size: 12px; padding: 4px 6px; }
      td { padding: 6px; border-top: 1px solid var(--divider-color); }
      .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .good { color: var(--nx-good); font-weight: 500; } .bad { color: var(--nx-bad); font-weight: 500; } .chg { font-weight: 500; }
    </style>`;
  }
}

customElements.define("noxcast-battery", NoxcastBattery);
