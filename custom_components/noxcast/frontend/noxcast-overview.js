// Noxcast panel section "Overview": what is due today and in the days ahead, each figure linked to the section
// that explains it. Plain custom element, no build step. Data comes from the websocket command "noxcast/overview".
//
// Updates never rebuild the section: the frame is drawn once (again only for another theme or after an error),
// then every part is a slot that is written only when its content changed, and the live figures (PV power,
// state of charge, rest of today) are text nodes set from the Home Assistant states. A new answer is fetched when
// Noxcast's own sensor of the rest of today changes (Noxcast computed anew) and every few minutes besides.
const { WS_SCHEMA, ask, errorText, esc, escId, kwh, num, hm, power, powerParts, smooth, smoothPath, tsRows } = await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href);

const RELOAD_MS = 5 * 60000;
const PLAN_BAND = 0.1;          // "as forecast" while the measured hours so far are this close to their morning forecast

const OV_TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden", noEntry: "Noxcast ist noch nicht eingerichtet.",
    today: "Heute", expected: "Erwartet heute", sofar: "Bis jetzt", rest: "Rest heute", power: "Leistung jetzt",
    morning: "Prognose am Morgen {v}", range: "Spanne {a}–{b} kWh", clear: "klar {v}", forecastFor: "bis {t} · Morgenprognose dafür {v} kWh",
    noActual: "noch keine volle Stunde gemessen", hourFc: "erwartet diese Stunde {v}", peak: "stärkste Stunde {t}",
    asPlanned: "Wie vorhergesagt", sunnier: "Sonniger als vorhergesagt", dimmer: "Trüber als vorhergesagt",
    lForecast: "Prognose", lActual: "Ist", lExpected: "Erwartet", lBand: "Spanne (Wolken)", lClear: "Klarer Himmel", now: "jetzt",
    consumers: "Verbraucher planen", startNow: "Jetzt starten", wait: "Noch warten", noSurplus: "Kein Überschuss", shareNow: "PV-Anteil jetzt {p}",
    bestToday: "Bestes Fenster heute", bestTomorrow: "Bestes Fenster morgen", pv: "PV", noWindow: "keines",
    loadRest: "Hausverbrauch bis Tagesende", loadRestSub: "Prognose ab dieser Stunde",
    battery: "Akku", socNow: "jetzt {p}", socMax: "erwartet max. {p}", fullAt: "Voll um", notFull: "heute nicht voll",
    lost: "Verlorener Überschuss heute", emptyAt: "Leer um", notEmpty: "nicht vor der nächsten Ladung", tomorrow: "morgen",
    days: "Nächste Tage", lDayFc: "Prognose", lDayBand: "Spanne", lDayClear: "klarer Himmel", todayShort: "Heute",
    yesterday: "Gestern", yFc: "Prognose", yAct: "Ist", deviation: "Ist gegenüber Prognose", tooHigh: "Prognose zu hoch", tooLow: "Prognose zu niedrig",
    curtailed: "{n} h Akku voll, Ist evtl. abgeregelt", recent: "Letzte {n} Tage", recentSub: "{d} vollständige Tage",
    avgErr: "±{p} im Mittel", noYesterday: "Für gestern liegt noch kein vollständiger Tag vor.",
    ok: "Alles in Ordnung", problems: "{n} Hinweis(e)", lastRun: "Lernlauf {t}", noRun: "noch kein Lernlauf", running: "Lernlauf läuft",
    arrays: "{n} Gruppe(n)", sensorsOk: "alle {n} Sensoren liefern", unavailable: "{e} ({r}, {o}) liefert keine Werte",
    learnError: "Letzter Lernlauf fehlgeschlagen: {e}", version: "Noxcast {v}",
    go: { day: "Tag", load: "Last", battery: "Akku & Überschuss", accuracy: "Prognosegüte", diagnostics: "Diagnose", dayPick: "Tag wählen" },
    roles: { pv: "PV-Leistung", curtailment_soc: "Ladestand (Abregelung)", house_load: "Hausverbrauch", consumer_power: "Verbraucher Leistung",
      consumer_energy: "Verbraucher Energie", remaining_time: "Restlaufzeit", elapsed_time: "Laufzeit", battery_soc: "Akku Ladestand",
      battery_max: "Akku Ladegrenze", battery_min: "Akku Entladegrenze" },
  },
  en: {
    outdated: "Noxcast was updated – please reload the page", noEntry: "Noxcast is not set up yet.",
    today: "Today", expected: "Expected today", sofar: "So far", rest: "Rest of today", power: "Power now",
    morning: "morning forecast {v}", range: "range {a}–{b} kWh", clear: "clear {v}", forecastFor: "until {t} · morning forecast for it {v} kWh",
    noActual: "no full hour measured yet", hourFc: "expected this hour {v}", peak: "strongest hour {t}",
    asPlanned: "As forecast", sunnier: "Sunnier than forecast", dimmer: "Duller than forecast",
    lForecast: "Forecast", lActual: "Actual", lExpected: "Expected", lBand: "Range (clouds)", lClear: "Clear sky", now: "now",
    consumers: "Plan consumers", startNow: "Start now", wait: "Better wait", noSurplus: "No surplus", shareNow: "PV share now {p}",
    bestToday: "Best window today", bestTomorrow: "Best window tomorrow", pv: "PV", noWindow: "none",
    loadRest: "House load until the end of the day", loadRestSub: "forecast from this hour on",
    battery: "Battery", socNow: "now {p}", socMax: "expected max. {p}", fullAt: "Full at", notFull: "not full today",
    lost: "Surplus lost today", emptyAt: "Empty at", notEmpty: "not before the next charge", tomorrow: "tomorrow",
    days: "Next days", lDayFc: "Forecast", lDayBand: "Range", lDayClear: "clear sky", todayShort: "Today",
    yesterday: "Yesterday", yFc: "Forecast", yAct: "Actual", deviation: "Actual against forecast", tooHigh: "forecast too high", tooLow: "forecast too low",
    curtailed: "{n} h battery full, actual possibly curtailed", recent: "Last {n} days", recentSub: "{d} complete days",
    avgErr: "±{p} on average", noYesterday: "No complete day for yesterday yet.",
    ok: "All good", problems: "{n} note(s)", lastRun: "Learning run {t}", noRun: "no learning run yet", running: "Learning run in progress",
    arrays: "{n} array(s)", sensorsOk: "all {n} sensors report", unavailable: "{e} ({r}, {o}) reports no values",
    learnError: "Last learning run failed: {e}", version: "Noxcast {v}",
    go: { day: "Day", load: "Load", battery: "Battery & surplus", accuracy: "Forecast accuracy", diagnostics: "Diagnostics", dayPick: "Pick a day" },
    roles: { pv: "PV power", curtailment_soc: "State of charge (curtailment)", house_load: "House load", consumer_power: "Consumer power",
      consumer_energy: "Consumer energy", remaining_time: "Remaining time", elapsed_time: "Elapsed time", battery_soc: "Battery level",
      battery_max: "Battery upper limit", battery_min: "Battery lower limit" },
  },
};

const fill = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k]);

class NoxcastOverview extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (ev) => {
      const go = ev.composedPath().find((n) => n.dataset && n.dataset.go);
      if (go) this.dispatchEvent(new CustomEvent("noxcast-section", { detail: { section: go.dataset.go }, bubbles: true, composed: true }));
    });
  }

  set hass(hass) {
    const first = !this._hass, dark = this._dark;
    this._hass = hass;
    if (first) { this._load(); return; }
    if (this._dark !== dark) { this._render(); return; }
    this._live();
    // Noxcast computed anew: its sensor of the rest of today changed
    const id = this._data && this._data.live && this._data.live.remaining, s = id && hass.states[id];
    const stamp = s && s.last_updated;
    if (stamp && this._stamp && stamp !== this._stamp) {
      // hidden on another tab: load once it is shown again
      if (this.isConnected) this._load(); else this._stale = true;
    }
    if (stamp) this._stamp = stamp;
  }

  connectedCallback() {
    clearInterval(this._timer);
    this._timer = setInterval(() => this._load(), RELOAD_MS);
    if (this._hass && !this._loading && (!this._data || this._stale || Date.now() - this._loaded > RELOAD_MS)) this._load();
  }
  disconnectedCallback() { clearInterval(this._timer); clearTimeout(this._retry); }

  get _dark() { return !!(this._hass && this._hass.themes && this._hass.themes.darkMode); }
  get _t() { return OV_TEXT[(this._hass.language || "en").slice(0, 2)] || OV_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }

  async _load() {
    if (!this._hass) return;
    // a trigger while a request runs: its answer may predate the new computation, so ask once more afterwards
    if (this._loading) { this._again = true; return; }
    this._loading = true;
    this._again = false;
    this._stale = false;
    const hadFrame = !!this.shadowRoot.getElementById("ov");
    if (!hadFrame && !this._data && !this._error) this._render();         // a placeholder while the first answer is on its way
    let error = null;
    try {
      // a request lost while the connection is rebuilt never answers: give up after 20 s and try again
      const data = await ask(this._hass, { type: "noxcast/overview" });
      if (!data) error = this._t.noEntry;
      else if (data.v !== WS_SCHEMA) error = this._t.outdated;
      else { this._data = data; this._loaded = Date.now(); }
    } catch (err) {
      error = errorText(err, this._hass.language);
      clearTimeout(this._retry);                // Noxcast may still be starting after a restart
      this._retry = setTimeout(() => { if (this.isConnected) this._load(); }, 10000);
    } finally {
      this._loading = false;
      if (this._again) setTimeout(() => this._load(), 0);
    }
    // a failed reload keeps what is shown; only without data the error replaces it
    if (error && this._data && hadFrame && error !== this._t.outdated) return;
    this._error = error;
    if (this._error || !hadFrame) this._render(); else this._fill();
  }

  // ------------------------------------------------------------ frame and slots

  _render() {
    if (!this._hass) return;
    if (this._error || !this._data) {
      this.shadowRoot.innerHTML = `${this._style()}<div class="stack">${this._error ? `<section class="card pad">${esc(this._error)}</section>` : `<div class="pad muted">…</div>`}</div>`;
      return;
    }
    const t = this._t, more = (k, label) => `<button class="more" data-go="${k}">${label || t.go[k]} →</button>`;
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack" id="ov">
      <section class="card pad">
        <div class="head"><h3>${t.today}</h3><span id="s-pill"></span></div>
        <div class="figs">
          <div class="fig"><span class="muted small">${t.expected}</span><span class="big" id="s-exp"></span><span class="muted small" id="s-exp-sub"></span></div>
          <div class="fig"><span class="muted small">${t.sofar}</span><span class="big" id="s-sofar"></span><span class="muted small" id="s-sofar-sub"></span></div>
          <div class="fig"><span class="muted small">${t.rest}</span><span class="big"><span id="l-rest">–</span> <span class="muted unit">kWh</span></span><span class="muted small" id="l-rest-sub"></span></div>
          <div class="fig"><span class="muted small">${t.power}</span><span class="big"><span id="l-power">–</span> <span class="muted unit" id="l-power-unit">kW</span></span><span class="muted small" id="s-power-sub"></span></div>
        </div>
        <div id="s-chart" class="chart"></div>
        <div class="head"><div class="legend">
          <span><i class="sw box" style="background:linear-gradient(180deg,#F5A524,#FF6A3D)"></i>${t.lActual}</span><span><i class="sw box rest"></i>${t.lExpected}</span>
          <span><i class="sw dash" style="--c:var(--primary-text-color)"></i>${t.lForecast}</span><span><i class="sw box band"></i>${t.lBand}</span>
          <span><i class="sw dot"></i>${t.lClear}</span></div>${more("day")}</div>
      </section>
      <div class="split">
        <section class="card pad grow2" id="c-load" hidden><div class="head"><h3>${t.consumers}</h3>${more("load")}</div><div id="s-consumers"></div></section>
        <section class="card pad grow" id="c-bat" hidden><div class="head"><h3>${t.battery}</h3>${more("battery")}</div>
          <div class="soc"><div class="socfill" id="l-socbar"></div><div class="socmax" id="s-socmax"></div></div>
          <div class="socl muted small"><span id="l-soc"></span><span id="s-socmax-t"></span></div>
          <div id="s-battery"></div></section>
      </div>
      <div class="split">
        <section class="card pad grow2"><div class="head"><h3>${t.days}</h3>${more("day", t.go.dayPick)}</div><div id="s-days"></div>
          <div class="legend"><span><i class="sw" style="background:var(--nx-grad, var(--ov-fc))"></i>${t.lDayFc}</span><span><i class="sw" style="background:var(--primary-text-color);opacity:.5"></i>${t.lDayBand}</span><span><i class="sw" style="background:var(--ov-rail)"></i>${t.lDayClear}</span></div></section>
        <section class="card pad grow"><div class="head"><h3>${t.yesterday}</h3>${more("accuracy")}</div><div id="s-yday"></div></section>
      </div>
      <div class="status" id="s-status"></div>
    </div>`;
    for (const el of this.shadowRoot.querySelectorAll("[id^='s-'], [id^='l-']")) el._nx = undefined;
    this._fill();
  }

  // write a slot only when its content changed (compared with what was written: the browser serialises differently)
  _set(id, html) {
    const el = this.shadowRoot.getElementById(id);
    if (el && el._nx !== html) { el.innerHTML = html; el._nx = html; }
  }
  _text(id, text) {
    const el = this.shadowRoot.getElementById(id);
    if (el && el.textContent !== text) el.textContent = text;
  }

  _fill() {
    if (!this.shadowRoot.getElementById("ov")) return;
    const d = this._data, t = this._t;
    const hours = tsRows(d.today, "forecast", "morning", "expected", "actual", "clear", "p10", "p90")
      .map(([ts, fc, mo, ex, act, clear, p10, p90]) => ({ ts, fc, mo, ex, act, clear, p10, p90 }));
    this._hours = hours;
    const today = this._today();
    // expected today, with the morning forecast, the range and clear sky
    const k = (wh) => kwh(wh, this._lang), k2 = (wh) => kwh(wh, this._lang, 2);
    this._set("s-exp", today ? `${k2(today.expected_wh ?? today.wh)} <span class="muted unit">kWh</span>` : "–");
    const sub = [];
    if (today && today.morning_wh != null) sub.push(fill(t.morning, { v: k(today.morning_wh) }));
    if (today && today.p10 != null) sub.push(fill(t.range, { a: k(today.p10), b: k(today.p90) }));
    if (today && today.clear_wh) sub.push(fill(t.clear, { v: k(today.clear_wh) }));
    this._set("s-exp-sub", esc(sub.join(" · ")));
    // so far: the measured full hours against the morning forecast, the reference the day is judged by; the
    // latest forecast follows the measured hours and would call nearly every day "as forecast"
    const done = hours.filter((h) => h.act != null && h.mo != null);
    const act = done.reduce((s, h) => s + h.act, 0), fc = done.reduce((s, h) => s + h.mo, 0);
    this._set("s-sofar", done.length ? `${k2(act)} <span class="muted unit">kWh</span>` : "–");
    const dev = fc > 0 ? act / fc - 1 : null;
    this._set("s-sofar-sub", done.length ? `${esc(fill(t.forecastFor, { t: this._hm(done[done.length - 1].ts + 3600), v: k(fc) }))}${dev !== null ? ` · <span class="${dev >= 0 ? "good" : "warn"}">${dev >= 0 ? "+" : "−"}${num(Math.abs(dev) * 100, this._lang)} %</span>` : ""}` : esc(t.noActual));
    this._set("s-pill", dev === null || fc < 200 ? "" : Math.abs(dev) <= PLAN_BAND ? `<span class="pill good">${t.asPlanned}</span>`
      : dev > 0 ? `<span class="pill good">${t.sunnier}</span>` : `<span class="pill warn">${t.dimmer}</span>`);
    // the hour now and the strongest hour of the expected curve
    const nowTs = Date.now() / 1000, cur = hours.find((h) => h.ts <= nowTs && nowTs < h.ts + 3600);
    const peak = hours.reduce((m, h) => ((h.ex ?? h.fc ?? 0) > ((m && (m.ex ?? m.fc)) ?? 0) ? h : m), null);
    const powerSub = [];
    // the corrected expectation of this hour, the value the chart draws; the morning forecast only without one
    const curW = cur ? cur.ex ?? cur.fc : null;
    if (curW != null) powerSub.push(fill(t.hourFc, { v: power(curW, this._lang) }));
    if (peak && (peak.ex ?? peak.fc) > 0) powerSub.push(fill(t.peak, { t: `${this._hm(peak.ts)}–${this._hm(peak.ts + 3600)}` }));
    this._set("s-power-sub", esc(powerSub.join(" · ")));
    this._set("s-chart", this._chart(hours, nowTs));
    this._consumers();
    this._battery();
    this._set("s-days", this._days());
    this._set("s-yday", this._yesterday());
    this._set("s-status", this._status());
    this._live();
  }

  // ------------------------------------------------------------ live figures from the Home Assistant states

  _live() {
    if (!this._data || !this.shadowRoot.getElementById("ov")) return;
    const live = this._data.live || {}, st = this._hass.states;
    // PV power: the sum of the arrays' power sensors, in kW (a sensor in kW counts as such)
    let w = 0, any = false;
    for (const id of live.pv || []) {
      const s = st[id], v = s ? Number(s.state) : NaN;
      if (!Number.isFinite(v)) continue;
      w += (s.attributes.unit_of_measurement || "W").toLowerCase() === "kw" ? v * 1000 : v;
      any = true;
    }
    const [pv, unit] = any ? powerParts(w, this._lang) : ["–", "kW"];
    this._text("l-power", pv);
    this._text("l-power-unit", unit);
    // rest of today: Noxcast's own sensor, with its range
    const r = live.remaining && st[live.remaining], rv = r ? Number(r.state) : NaN;
    this._text("l-rest", Number.isFinite(rv) ? num(rv, this._lang, 2) : "–");
    this._text("l-rest-sub", r && r.attributes.p10 != null ? fill(this._t.range, { a: num(r.attributes.p10, this._lang, 1), b: num(r.attributes.p90, this._lang, 1) }) : "");
    // state of charge: the batteries together, weighted by capacity
    let e = 0, cap = 0;
    for (const { entity, capacity } of live.soc || []) {
      const s = st[entity], v = s ? Number(s.state) : NaN;
      if (Number.isFinite(v) && capacity > 0) { e += v / 100 * capacity; cap += capacity; }
    }
    const soc = cap > 0 ? e / cap : null, bar = this.shadowRoot.getElementById("l-socbar");
    this._text("l-soc", soc === null ? "" : fill(this._t.socNow, { p: `${num(soc * 100, this._lang)} %` }));
    const width = soc === null ? "0%" : `${Math.max(0, Math.min(100, soc * 100)).toFixed(1)}%`;
    if (bar && bar.style.width !== width) bar.style.width = width;
  }

  // ------------------------------------------------------------ parts

  _chart(hours, nowTs) {
    const t = this._t;
    const lit = hours.filter((h) => (h.clear || 0) > 0 || (h.ex || 0) > 0 || (h.fc || 0) > 0);
    if (!lit.length) return "";
    const a = lit[0].ts - 3600, b = lit[lit.length - 1].ts + 2 * 3600;
    // as wide as the card (within bounds), so the labels keep their size on a phone
    const W = Math.round(Math.max(340, Math.min(720, (this.clientWidth || 720) - 32))), H = 200, L = 10, R = 10, T = 14, B = 24;
    const top = Math.max(...lit.map((h) => Math.max(h.clear || 0, h.p90 || 0, h.fc || 0, h.act || 0, h.ex || 0))) / 1000 || 1;
    const step = top > 30 ? 10 : top > 15 ? 5 : top > 6 ? 2 : top > 3 ? 1 : top > 1.5 ? 0.5 : 0.25, ymax = Math.ceil(top / step) * step;
    const x = (ts) => L + (ts - a) / (b - a) * (W - L - R), y = (wh) => T + (1 - wh / 1000 / ymax) * (H - T - B);
    // an hour's mean belongs to the middle of the hour
    const mid = (h) => h.ts + 1800;
    // the look of the day page: monotone smooth curves, which never overshoot an hourly mean
    const xy = (p) => p.map(([ts, v]) => [x(ts), y(v)]);
    const line = (p) => smoothPath(xy(p));
    // only the hours of the time axis: the night hours of the day would draw their zeros left of it
    const shown = hours.filter((h) => mid(h) >= a && mid(h) <= b);
    const pts = (key, list = shown) => list.filter((h) => h[key] != null).map((h) => [mid(h), h[key]]);
    let svg = "", scale = "";
    for (let v = 0; v <= ymax + 1e-9; v += step) {
      svg += `<line x1="${L}" x2="${W - R}" y1="${y(v * 1000).toFixed(1)}" y2="${y(v * 1000).toFixed(1)}" class="${v ? "rail" : "axis"}"/>`;
      // like the dashboard card: inside the right edge, just below their line, without the zero the axis already shows
      if (v) scale += `<text x="${W - R - 2}" y="${(y(v * 1000) + 12).toFixed(1)}" text-anchor="end" class="in">${num(v, this._lang, step < 1 ? 2 : 0)}${v + step > ymax + 1e-9 ? " kW" : ""}</text>`;
    }
    const every = W < 500 ? 4 * 3600 : 7200;
    // a time near an edge is anchored there, so it is not cut off now that the scale no longer takes the left margin
    for (let ts = Math.ceil(a / every) * every; ts <= b; ts += every) {
      const tx = x(ts), anchor = tx < L + 16 ? "start" : tx > W - R - 16 ? "end" : "middle";
      svg += `<text x="${tx.toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${this._hm(ts)}</text>`;
    }
    // production edges at zero, so area and curves rise from the axis and return to it
    const p0 = lit[0].ts, p1 = lit[lit.length - 1].ts + 3600;
    const edged = (p) => (p.length ? [[p0, 0], ...p, [p1, 0]] : p);
    // the lines too stay within production: the night hours at the edges of the axis would retrace the zero line
    const inDay = shown.filter((h) => mid(h) >= p0 && mid(h) <= p1);
    const clear = pts("clear", inDay);
    if (clear.length > 1) svg += `<path d="${line(edged(clear))}" class="clear"/>`;
    const band = shown.filter((h) => h.p10 != null && h.p90 != null);
    if (band.length > 1) {
      const lo = xy(band.map((h) => [mid(h), h.p10])).reverse();
      svg += `<path d="${line(band.map((h) => [mid(h), h.p90]))} L${lo[0][0].toFixed(1)},${lo[0][1].toFixed(1)}${smooth(lo)} Z" class="bandarea"/>`;
    }
    // PV as one area: measured hours, then the expectation for the rest; solid until now, pale after it
    const day = inDay.map((h) => [mid(h), h.act ?? h.ex ?? h.fc ?? 0]);
    if (day.length) {
      const d = line(edged(day)), base = `L${x(p1).toFixed(1)},${y(0).toFixed(1)} L${x(p0).toFixed(1)},${y(0).toFixed(1)} Z`;
      const cut = Math.max(0, Math.min(W, x(nowTs))).toFixed(1);
      svg = `<defs><linearGradient id="ov-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F5A524" stop-opacity=".95"/><stop offset="1" stop-color="#FF6A3D" stop-opacity=".55"/></linearGradient>
        <clipPath id="ov-past"><rect x="0" y="0" width="${cut}" height="${H}"/></clipPath><clipPath id="ov-rest"><rect x="${cut}" y="0" width="${W}" height="${H}"/></clipPath></defs>${svg}`;
      svg += `<path d="${d} ${base}" fill="url(#ov-area)" clip-path="url(#ov-past)"/><path d="${d} ${base}" class="rest" clip-path="url(#ov-rest)"/>`;
      svg += `<path d="${d}" class="edge" clip-path="url(#ov-past)"/>`;
    }
    const fc = pts("fc", inDay);
    if (fc.length > 1) svg += `<path d="${line(edged(fc))}" class="fc"/>`;
    if (nowTs > a && nowTs < b) {
      svg += `<line x1="${x(nowTs).toFixed(1)}" x2="${x(nowTs).toFixed(1)}" y1="${T}" y2="${H - B}" class="now"/>`;
      svg += `<text x="${x(nowTs).toFixed(1)}" y="${T - 3}" text-anchor="middle" class="nowt">${t.now}</text>`;
    }
    // the scale last, so the curves never cover it
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t.today)}">${svg}${scale}</svg>`;
  }

  _consumers() {
    const d = this._data, t = this._t, card = this.shadowRoot.getElementById("c-load");
    const list = d.consumers || [];
    if (card) card.hidden = !list.length && !d.load;
    const win = (w) => (w ? `${this._hm(w.start)}–${this._hm(w.end)} <span class="muted">· ${num(w.pv * 100, this._lang)} % ${t.pv}</span>` : `<span class="muted">${t.noWindow}</span>`);
    const blocks = list.map((c) => `
      <div class="answer ${c.start_now ? "" : "no"}"><span class="ans">${c.start_now ? t.startNow : !c.today && c.now < 0.05 ? t.noSurplus : t.wait}</span>
        <span class="muted small"><b class="plain">${esc(c.name)}</b> · ${fill(t.shareNow, { p: `${num(c.now * 100, this._lang)} %` })}</span></div>
      <div class="rows"><div class="row"><span class="muted">${t.bestToday}</span><span>${win(c.today)}</span></div>
        <div class="row"><span class="muted">${t.bestTomorrow}</span><span>${win(c.tomorrow)}</span></div></div>`).join("");
    let load = "";
    if (d.load) {
      const now = Date.now() / 1000;
      const wh = tsRows(d.load, "wh").filter(([ts]) => ts + 3600 > now && ts < d.day_end).reduce((s, [, v]) => s + v, 0);
      load = `<div class="rows"><div class="row"><span class="muted">${t.loadRest}<br><span class="small">${t.loadRestSub}</span></span><span>${kwh(wh, this._lang)} kWh</span></div></div>`;
    }
    this._set("s-consumers", blocks + load);
  }

  _battery() {
    const d = this._data, t = this._t, b = d.battery, card = this.shadowRoot.getElementById("c-bat");
    if (card) card.hidden = !b;
    if (!b) return;
    const socs = tsRows(b.points, "soc").filter(([ts]) => ts < d.day_end).map(([, v]) => v);
    const max = socs.length ? Math.max(...socs) : null;
    const mark = this.shadowRoot.getElementById("s-socmax"), left = max === null ? "" : `calc(${(Math.min(1, max) * 100).toFixed(1)}% - 1px)`;
    if (mark && mark.style.left !== left) { mark.style.left = left; mark.hidden = max === null; }
    this._text("s-socmax-t", max === null ? "" : fill(t.socMax, { p: `${num(max * 100, this._lang)} %` }));
    const when = (ts) => (ts >= d.day_end ? `${t.tomorrow} ${this._hm(ts)}` : this._hm(ts));
    this._set("s-battery", `<div class="rows">
      <div class="row"><span class="muted">${t.fullAt}</span><span>${b.full ? when(b.full) : `<span class="muted">${t.notFull}</span>`}</span></div>
      <div class="row"><span class="muted">${t.lost}</span><span>${kwh(b.lost_today, this._lang)} kWh</span></div>
      <div class="row"><span class="muted">${t.emptyAt}</span><span>${b.empty ? when(b.empty) : `<span class="muted">${t.notEmpty}</span>`}</span></div></div>`);
  }

  _days() {
    const d = this._data, t = this._t, days = d.days || [];
    if (!days.length) return "";
    const top = Math.max(...days.map((x) => Math.max(x.clear_wh || 0, x.p90 || 0, x.expected_wh || 0, x.wh || 0))) || 1;
    const yy = (wh) => (76 - wh / top * 72).toFixed(1);
    const cells = days.map((x) => {
      const isToday = x === this._today();
      const v = isToday && x.expected_wh != null ? x.expected_wh : x.wh;
      const date = new Date(`${x.date}T12:00:00Z`);
      const name = isToday ? t.todayShort : date.toLocaleDateString(this._lang, { weekday: "short", timeZone: "UTC" });
      const sub = date.toLocaleDateString(this._lang, { day: "numeric", month: "short", timeZone: "UTC" });
      const whisker = x.p10 != null ? `<line x1="17" x2="17" y1="${yy(x.p90)}" y2="${yy(x.p10)}" class="wh"/><line x1="13" x2="21" y1="${yy(x.p90)}" y2="${yy(x.p90)}" class="wh"/><line x1="13" x2="21" y1="${yy(x.p10)}" y2="${yy(x.p10)}" class="wh"/>` : "";
      return `<div class="day ${isToday ? "today" : ""}"><div><div class="dn">${esc(name)}</div><div class="muted small">${esc(sub)}</div></div>
        <svg viewBox="0 0 34 80" aria-hidden="true">${x.clear_wh ? `<rect x="7" y="${yy(x.clear_wh)}" width="20" height="${(76 - yy(x.clear_wh)).toFixed(1)}" rx="5" fill="var(--ov-rail)"/>` : ""}
          <rect x="7" y="${yy(v)}" width="20" height="${(76 - yy(v)).toFixed(1)}" rx="5" fill="url(#ov-grad)"/>${whisker}</svg>
        <div class="kwh">${kwh(v, this._lang)}<span class="muted small"> kWh</span></div>
        <div class="muted small">${x.p10 != null ? `${kwh(x.p10, this._lang, 0)}–${kwh(x.p90, this._lang, 0)}` : "&nbsp;"}</div></div>`;
    }).join("");
    const grad = `<svg width="0" height="0" style="position:absolute"><defs><linearGradient id="ov-grad" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#F5A524"/><stop offset=".55" stop-color="#FF6A3D"/><stop offset="1" stop-color="#8B5CF6"/></linearGradient></defs></svg>`;
    return `${grad}<div class="days" style="--n:${days.length}">${cells}</div>`;
  }

  _yesterday() {
    const t = this._t, acc = this._data.accuracy || {}, y = acc.yesterday;
    const recent = acc.error != null ? `<div class="row"><span class="muted">${fill(t.recent, { n: acc.span })}<br><span class="small">${fill(t.recentSub, { d: acc.days })}</span></span><span>${fill(t.avgErr, { p: `${num(acc.error * 100, this._lang)} %` })}</span></div>` : "";
    if (!y) return `<p class="muted small">${t.noYesterday}</p><div class="rows">${recent}</div>`;
    // the same sign as "so far": actual against forecast, + = more than forecast
    const top = Math.max(y.forecast_wh, y.actual_wh) || 1, dev = y.forecast_wh > 0 ? y.actual_wh / y.forecast_wh - 1 : 0;
    const bar = (label, wh, c) => `<div class="ybar"><span class="muted">${label}</span><div class="track"><div style="width:${(wh / top * 100).toFixed(1)}%;background:${c}"></div></div><b>${kwh(wh, this._lang)} kWh</b></div>`;
    return `<div class="ybars">${bar(t.yFc, y.forecast_wh, "var(--ov-fc)")}${bar(t.yAct, y.actual_wh, "var(--ov-act)")}</div>
      <div class="rows"><div class="row"><span class="muted">${t.deviation}</span><span><span class="${Math.abs(dev) <= PLAN_BAND ? "good" : "warn"}">${dev >= 0 ? "+" : "−"}${num(Math.abs(dev) * 100, this._lang)} %</span>
        <span class="muted">· ${dev >= 0 ? t.tooLow : t.tooHigh}</span>${y.curtailed ? `<br><span class="muted small">${fill(t.curtailed, { n: y.curtailed })}</span>` : ""}</span></div>${recent}</div>`;
  }

  _status() {
    const d = this._data, t = this._t, s = d.status || {}, problems = [];
    if (s.error) problems.push(fill(t.learnError, { e: esc(s.error) }));
    for (const u of s.unavailable || []) problems.push(fill(t.unavailable, { e: `<span class="mono">${escId(u.entity_id)}</span>`, r: esc(t.roles[u.role] || u.role), o: esc(u.owner) }));
    const version = d.version ? `<span class="muted">${fill(t.version, { v: esc(d.version) })}</span>` : "";
    const link = `<button class="more push" data-go="diagnostics">${t.go.diagnostics} →</button>`;
    if (problems.length) {
      return `<div class="statusbox problem"><span class="pill bad">${fill(t.problems, { n: problems.length })}</span>
        <ul>${problems.map((p) => `<li>${p}</li>`).join("")}</ul>${version}${link}</div>`;
    }
    const run = s.running ? t.running : s.last ? fill(t.lastRun, { t: this._when(s.last) }) : t.noRun;
    return `<div class="statusbox"><span class="pill good">${t.ok}</span><span>${esc(run)}</span><span class="muted">·</span>
      <span>${fill(t.arrays, { n: (s.arrays || []).length })}, ${fill(t.sensorsOk, { n: s.sensors || 0 })}</span>
      ${version ? `<span class="muted">·</span>${version}` : ""}${link}</div>`;
  }

  // the entry of today among the days: the one whose day ends with the payload's day_end (a day may be left out)
  _today() { return (this._data.days || []).find((x) => x.start < this._data.day_end && this._data.day_end - x.start <= 25 * 3600) || null; }

  _hm(ts) { return hm(ts, this._lang, this._data.time_zone, { hourCycle: "h23" }); }
  _when(iso) {
    const ts = Date.parse(iso) / 1000, day = (x) => new Date(x * 1000).toLocaleDateString("en-CA", { timeZone: this._data.time_zone });
    return day(ts) === day(Date.now() / 1000) ? this._hm(ts)
      : new Date(iso).toLocaleString(this._lang, { timeZone: this._data.time_zone, dateStyle: "short", timeStyle: "short" });
  }

  _style() {
    const d = this._dark;
    return `<style>
      :host { display: block; --ov-good: ${d ? "#4CC77A" : "#2E9E5B"}; --ov-warn: ${d ? "#E0A43C" : "#C27C0E"}; --ov-bad: ${d ? "#F06A6A" : "#C83A2E"};
        --ov-fc: #FF6A3D; --ov-act: #8B5CF6;
        --ov-rail: ${d ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .split { display: flex; gap: 16px; flex-wrap: wrap; align-items: stretch; }
      .grow { flex: 1 1 280px; min-width: 0; } .grow2 { flex: 1.4 1 380px; min-width: 0; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); display: flex; flex-direction: column; gap: 14px; }
      .card[hidden] { display: none; }
      .pad { padding: 16px; }
      h3 { margin: 0; font-size: 15px; font-weight: 500; }
      .head { display: flex; align-items: center; justify-content: space-between; gap: 8px 12px; flex-wrap: wrap; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; } .unit { font-size: 14px; font-weight: 400; }
      .mono { font-family: var(--code-font-family, monospace); font-size: 12px; }
      .good { color: var(--ov-good); } .warn { color: var(--ov-warn); }
      .figs { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
      .fig { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .big { font-size: 24px; font-weight: 500; font-variant-numeric: tabular-nums; }
      .more { border: 0; background: none; color: var(--secondary-text-color); font: inherit; font-size: 13px; cursor: pointer; padding: 2px 4px; border-radius: 6px; }
      .more:hover { color: var(--primary-color); }
      .push { margin-left: auto; }
      .pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 500;
        color: var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); white-space: nowrap; }
      .pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
      .pill.good { --c: var(--ov-good); } .pill.warn { --c: var(--ov-warn); } .pill.bad { --c: var(--ov-bad); }
      .chart svg { width: 100%; height: auto; display: block; }
      .chart text { fill: var(--secondary-text-color); font-size: 11px; }
      .chart .axis { stroke: var(--divider-color); } .chart .rail { stroke: var(--ov-rail); }
      .chart .clear { fill: none; stroke: var(--secondary-text-color); stroke-width: 1; stroke-dasharray: 1.5 3; opacity: .7; }
      .chart .bandarea { fill: #FF6A3D; fill-opacity: .12; stroke: #FF6A3D; stroke-opacity: .5; stroke-width: .8; stroke-dasharray: 2 2; }
      .chart .rest { fill: #FF6A3D; fill-opacity: .28; }
      .chart .edge { fill: none; stroke: #FF6A3D; stroke-width: 1.6; stroke-linejoin: round; }
      .chart .fc { fill: none; stroke: var(--primary-text-color); stroke-width: 1.6; stroke-dasharray: 5 4; opacity: .75; }
      .chart .now { stroke: var(--primary-text-color); stroke-dasharray: 2 2; opacity: .6; }
      .chart .in { paint-order: stroke; stroke: var(--card-background-color); stroke-width: 3px; stroke-linejoin: round; }
      .chart .nowt { fill: var(--primary-text-color); font-weight: 500; }
      .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12px; color: var(--secondary-text-color); }
      .sw { display: inline-block; width: 14px; height: 4px; border-radius: 3px; vertical-align: middle; margin-right: 6px; }
      .sw.dash { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); opacity: .75; }
      .sw.box { height: 10px; } .sw.rest { background: #FF6A3D; opacity: .28; }
      .sw.band { background: rgba(255,106,61,.12); outline: 1px dashed rgba(255,106,61,.5); outline-offset: -1px; }
      .sw.dot { height: 2px; background: repeating-linear-gradient(90deg, var(--secondary-text-color) 0 1.5px, transparent 1.5px 4px); }
      .rows { display: flex; flex-direction: column; }
      .row { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid var(--divider-color); align-items: baseline; }
      .row:first-child { border-top: 0; }
      .row > :last-child { text-align: right; font-variant-numeric: tabular-nums; }
      .answer { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 14px; padding: 10px 14px; border-radius: 12px; --c: var(--ov-good);
        background: color-mix(in srgb, var(--c) 10%, transparent); border: 1px solid color-mix(in srgb, var(--c) 30%, transparent); }
      .answer.no { --c: var(--ov-warn); }
      .ans { font-size: 18px; font-weight: 500; color: var(--c); }
      b.plain { font-weight: 500; color: var(--primary-text-color); }
      .soc { position: relative; height: 12px; border-radius: 8px; background: var(--ov-rail); overflow: hidden; }
      .socfill { position: absolute; inset: 0 auto 0 0; width: 0; border-radius: 8px; background: linear-gradient(90deg, #22C7E8, var(--ov-good)); transition: width .6s; }
      .socmax[hidden] { display: none; }
      .socmax { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--primary-text-color); opacity: .55; }
      .socl { display: flex; justify-content: space-between; margin-top: -8px; }
      .days { display: grid; grid-template-columns: repeat(var(--n), minmax(58px, 1fr)); gap: 8px; overflow-x: auto; }
      .day { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 4px; border-radius: 12px; border: 1px solid var(--divider-color); text-align: center; min-width: 0; }
      .day.today { border-color: color-mix(in srgb, var(--ov-fc) 55%, transparent); background: color-mix(in srgb, var(--ov-fc) 6%, transparent); }
      .dn { font-weight: 500; font-size: 13px; }
      .day svg { width: 34px; height: 80px; }
      .day .wh { stroke: var(--primary-text-color); stroke-width: 1.5; opacity: .55; }
      .kwh { font-weight: 500; font-variant-numeric: tabular-nums; }
      .ybars { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
      .ybar { display: grid; grid-template-columns: 70px 1fr auto; gap: 10px; align-items: center; font-variant-numeric: tabular-nums; }
      .track { height: 10px; border-radius: 6px; background: var(--ov-rail); overflow: hidden; }
      .track > div { height: 100%; border-radius: 6px; }
      .statusbox { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 12px 16px; border-radius: 12px;
        border: 1px solid var(--divider-color); background: var(--card-background-color); font-size: 13px; }
      .statusbox.problem { border-color: color-mix(in srgb, var(--ov-bad) 45%, transparent); background: color-mix(in srgb, var(--ov-bad) 6%, var(--card-background-color)); }
      .statusbox ul { margin: 0; padding-left: 18px; flex: 1 1 260px; }
    </style>`;
  }
}

if (!customElements.get("noxcast-overview")) customElements.define("noxcast-overview", NoxcastOverview);
