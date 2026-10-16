// Noxcast day card: power flow now, today's yield per array with significant effects, house load,
// battery and unused surplus. Plain custom element, no build step.
//
// type: custom:noxcast-card
// pv:
//   entity: sensor.pv_total            # optional, otherwise the sum of the groups
//   groups:
//     - entity: sensor.pv_balcony
//       name: Balkon                   # also the Noxcast array name, unless "array" is given
// grid: { entity: sensor.grid, invert: false }        # positive = import
// home: { entity: sensor.house_load }
// batteries:
//   - { entity: sensor.bat_power, soc: sensor.bat_soc, capacity: 1.9, name: Pro, invert: false }  # positive = charging
// export: false                        # surplus can be fed in; otherwise it counts as unused once batteries are full

const TEXT = {
  de: { pv: "PV", home: "Haus", grid: "Netz", battery: "Akku", tomorrow: "morgen", shade: "Schatten",
    refl: "Reflexion", unused: "ungenutzt", full: "voll", load: "Hauslast", soc: "Akku-Ladestand", lost: "Schattenverlust",
    noData: "Noch keine Noxcast-Daten", kwh: "kWh" },
  en: { pv: "PV", home: "Home", grid: "Grid", battery: "Battery", tomorrow: "tomorrow", shade: "shade",
    refl: "reflection", unused: "unused", full: "full", load: "House load", soc: "Battery level", lost: "Lost to shade",
    noData: "No Noxcast data yet", kwh: "kWh" },
};

const PALETTE = {
  light: { pv: "#F2B233", pvDark: "#C27A06", pvText: "#9A6400", grid: "#2F80C8", home: "#4F5964", bat: "#23874A",
    shade: "#C0392B", refl: "#B07A00", rail: "rgba(0,0,0,0.08)", unused: "#2F80C8", unusedBg: "rgba(47,128,200,0.12)",
    warnText: "#8E2418", warnBg: "#FBE3DE" },
  dark: { pv: "#F2B233", pvDark: "#C58410", pvText: "#F2B233", grid: "#5AA6E8", home: "#B4BCC4", bat: "#4CC77A",
    shade: "#F07058", refl: "#E8B640", rail: "rgba(255,255,255,0.10)", unused: "#5AA6E8", unusedBg: "rgba(90,166,232,0.15)",
    warnText: "#FFB4A6", warnBg: "#4A2620" },
};

const ACTIVE_W = 5;
const REFRESH_MS = 5 * 60 * 1000;

class NoxcastCard extends HTMLElement {
  static getStubConfig() {
    return { pv: { groups: [] }, grid: { entity: "" }, home: { entity: "" }, batteries: [] };
  }

  setConfig(config) {
    if (!config || !config.pv) throw new Error("pv is required");
    this._config = config;
    if (this._hass) this._renderAll();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (first) { this._renderAll(); this._refresh(); return; }
    const sig = this._signature();
    if (sig !== this._sig) { this._sig = sig; this._renderFlow(); }
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._refresh(), REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  get _t() { return TEXT[(this._hass.language || "en").slice(0, 2)] || TEXT.en; }
  get _p() { return this._hass.themes && this._hass.themes.darkMode ? PALETTE.dark : PALETTE.light; }
  get _lang() { return this._hass.language || "en"; }
  get _tz() { return this._hass.config.time_zone; }

  // ------------------------------------------------------------ live values (W, canonical signs)

  _watts(entityId, invert) {
    const s = entityId && this._hass.states[entityId];
    if (!s) return null;
    let v = parseFloat(s.state);
    if (!isFinite(v)) return null;
    const unit = (s.attributes.unit_of_measurement || "W").toLowerCase();
    if (unit === "kw") v *= 1000;
    return invert ? -v : v;
  }

  _soc(entityId) {
    const s = entityId && this._hass.states[entityId];
    const v = s ? parseFloat(s.state) : NaN;
    return isFinite(v) ? v : null;
  }

  _live() {
    const c = this._config;
    const groups = (c.pv.groups || []).map((g) => ({ name: g.name || g.entity, w: Math.max(0, this._watts(g.entity, g.invert) || 0) }));
    let pv = c.pv.entity ? this._watts(c.pv.entity, c.pv.invert) : null;
    if (pv === null) pv = groups.reduce((a, g) => a + g.w, 0);
    const bats = (c.batteries || []).map((b) => ({ name: b.name || b.entity, w: this._watts(b.entity, b.invert) || 0,
      soc: this._soc(b.soc), cap: Number(b.capacity) || 0 }));
    const capSum = bats.reduce((a, b) => a + b.cap, 0);
    const socAll = capSum ? bats.reduce((a, b) => a + (b.soc ?? 0) * b.cap, 0) / capSum
      : (bats.length ? bats.reduce((a, b) => a + (b.soc ?? 0), 0) / bats.length : null);
    return {
      pv: Math.max(0, pv), groups,
      home: Math.max(0, this._watts(c.home && c.home.entity, c.home && c.home.invert) || 0),
      grid: this._watts(c.grid && c.grid.entity, c.grid && c.grid.invert) || 0,
      bat: bats.reduce((a, b) => a + b.w, 0), bats, socAll,
    };
  }

  _signature() {
    const c = this._config, ids = [c.pv.entity, ...(c.pv.groups || []).map((g) => g.entity), c.grid && c.grid.entity,
      c.home && c.home.entity, ...(c.batteries || []).flatMap((b) => [b.entity, b.soc])].filter(Boolean);
    return ids.map((i) => this._hass.states[i] && this._hass.states[i].state).join("|") + (this._hass.themes && this._hass.themes.darkMode);
  }

  // ------------------------------------------------------------ data for the day band

  async _refresh() {
    if (!this._hass) return;
    try {
      const today = await this._hass.connection.sendMessagePromise({ type: "noxcast/today" });
      const stats = await this._history();
      this._data = { today, ...stats };
      this._error = null;
    } catch (err) {
      this._error = String((err && err.message) || err);
    }
    this._renderBand();
  }

  async _history() {
    const c = this._config, now = new Date();
    const dayStart = this._localMidnight(now);
    const ids = [c.home && c.home.entity, ...(c.batteries || []).map((b) => b.soc)].filter(Boolean);
    if (!ids.length) return { stats: {}, dayStart };
    const res = await this._hass.connection.sendMessagePromise({
      type: "recorder/statistics_during_period", start_time: new Date(dayStart - 7 * 86400000).toISOString(),
      end_time: now.toISOString(), statistic_ids: ids, period: "hour", types: ["mean"],
    });
    return { stats: res, dayStart };
  }

  _localMidnight(d) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: this._tz, hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(d).map((p) => [p.type, p.value]));
    const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
    const offset = asUtc - d.getTime();
    return Date.UTC(+parts.year, +parts.month - 1, +parts.day) - offset;
  }

  // ------------------------------------------------------------ rendering

  _renderAll() {
    const P = this._p;
    this.shadowRoot.innerHTML = `<style>
      ha-card { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
      .muted { color: var(--secondary-text-color); }
      .row { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; }
      .big { font-size: 18px; font-weight: 500; }
      .warn { font-size: 12px; font-weight: 500; padding: 2px 6px; border-radius: 4px; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--secondary-text-color); }
      .legend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
      .axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); font-family: var(--code-font-family, monospace); }
      .lane { position: relative; height: 10px; margin-top: 3px; }
      .lane .base { position: absolute; left: 0; right: 0; top: 4px; height: 2px; background: ${P.rail}; }
      .lane .fx { position: absolute; top: 1px; height: 8px; border-radius: 2px; }
      .lane .name { position: absolute; right: 0; top: -3px; font-size: 10px; color: var(--secondary-text-color);
        background: var(--card-background-color, var(--ha-card-background)); padding-left: 4px; }
      svg { display: block; width: 100%; height: auto; overflow: visible; }
      .divider { border-top: 1px solid var(--divider-color); padding-top: 8px; }
    </style>
    <ha-card><div id="flow"></div><div id="band" class="divider"></div></ha-card>`;
    this._sig = this._signature();
    this._renderFlow();
    this._renderBand();
  }

  _renderFlow() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("flow");
    if (!el) return;
    const P = this._p, t = this._t, v = this._live();
    // allocation of the flows, all values >= 0
    const pvHome = Math.min(v.pv, v.home);
    const charge = Math.max(v.bat, 0), discharge = Math.max(-v.bat, 0);
    const pvBat = Math.min(v.pv - pvHome, charge);
    const pvGrid = Math.max(v.pv - pvHome - pvBat, 0);
    const batHome = Math.min(discharge, v.home - pvHome);
    const gridHome = Math.max(v.home - pvHome - batHome, 0);
    const gridBat = Math.max(charge - pvBat, 0);
    const batGrid = Math.max(discharge - batHome, 0);
    const paths = {
      pvHome: ["M204 62 L204 146", pvHome, P.pv, false],
      pvBat: ["M212 62 L212 78 Q212 88 222 88 L352 88", pvBat, P.pv, false],
      pvGrid: ["M196 62 L196 66 Q196 76 186 76 L52 76", pvGrid, P.pv, false],
      gridHome: ["M52 88 L186 88 Q196 88 196 98 L196 146", gridHome, P.grid, false],
      batHome: ["M352 100 L222 100 Q212 100 212 110 L212 146", batHome, P.bat, false],
      gridBat: ["M52 82 L352 82", Math.max(gridBat, batGrid), gridBat >= batGrid ? P.grid : P.bat, batGrid > gridBat],
    };
    const w = (p) => Math.max(2, Math.min(9, p / 45)).toFixed(1);
    let lines = "";
    for (const [d, p, color, reverse] of Object.values(paths)) {
      if (p < ACTIVE_W) { lines += `<path d="${d}" fill="none" stroke="${P.rail}" stroke-width="2" stroke-linecap="round"></path>`; continue; }
      const dur = Math.max(0.8, 3 - p / 400).toFixed(2);
      lines += `<path d="${d}" fill="none" stroke="${color}" stroke-width="${w(p)}" stroke-opacity="0.85" stroke-linecap="round" stroke-linejoin="round"></path>
        <circle r="3.5" fill="${color}"><animateMotion dur="${dur}s" repeatCount="indefinite" path="${d}"${reverse ? ` keyPoints="1;0" keyTimes="0;1" calcMode="linear"` : ""}></animateMotion></circle>`;
    }
    const fmtW = (x) => `${Math.round(x).toLocaleString(this._lang)} W`;
    const op = (x) => (Math.abs(x) < ACTIVE_W ? 0.35 : 1);
    const groups = v.groups.map((g) => `${this._esc(g.name)} ${Math.round(g.w)}`).join(" · ");
    const bats = v.bats.slice(0, 3).map((b, i) => `<text x="402" y="${138 + i * 14}" text-anchor="end" font-size="11" class="muted" fill="currentColor">${this._esc(b.name)} ${b.w > 0 ? "+" : ""}${Math.round(b.w)} · ${b.soc === null ? "–" : Math.round(b.soc)} %</text>`).join("");
    const gridLabel = v.grid > 0 ? `${fmtW(v.grid)} ↓` : v.grid < 0 ? `${fmtW(-v.grid)} ↑` : "0 W";
    el.innerHTML = `<svg viewBox="0 0 408 ${v.bats.length > 1 ? 196 : 186}" role="img" aria-label="${t.pv} ${fmtW(v.pv)}, ${t.home} ${fmtW(v.home)}, ${t.grid} ${gridLabel}, ${t.battery} ${fmtW(v.bat)}">
      ${lines}
      <g opacity="${op(v.pv)}">
        <path d="M196 22l8-8l8 8M204 14v18M193 30h22" fill="none" stroke="${P.pv}" stroke-width="2" stroke-linecap="round"></path>
        <text x="204" y="54" text-anchor="middle" font-size="17" font-weight="500" fill="${P.pvText}">${fmtW(v.pv)}</text>
        ${groups ? `<text x="204" y="8" text-anchor="middle" font-size="11" fill="${P.pvText}">${groups}</text>` : ""}
      </g>
      <g opacity="${op(v.grid)}">
        <path d="M22 96l6-22l6 22M25 86h6M20 96h16" fill="none" stroke="${P.grid}" stroke-width="2" stroke-linecap="round"></path>
        <text x="6" y="122" font-size="15" font-weight="500" fill="${P.grid}">${gridLabel}</text>
      </g>
      <g opacity="${op(v.bat)}">
        <rect x="364" y="74" width="20" height="26" rx="3" fill="none" stroke="${P.bat}" stroke-width="2"></rect>
        <rect x="369" y="70" width="10" height="4" rx="1" fill="${P.bat}"></rect>
        <rect x="367" y="${97 - 20 * (v.socAll ?? 0) / 100}" width="14" height="${20 * (v.socAll ?? 0) / 100}" rx="1" fill="${P.bat}"></rect>
        <text x="402" y="122" text-anchor="end" font-size="15" font-weight="500" fill="${P.bat}">${v.bat > 0 ? "+" : ""}${fmtW(v.bat)}${v.socAll === null ? "" : ` · ${Math.round(v.socAll)} %`}</text>
      </g>
      ${v.bats.length > 1 ? `<g color="var(--secondary-text-color)">${bats}</g>` : ""}
      <path d="M190 160l14-12l14 12M193 158v16h22v-16" fill="none" stroke="${P.home}" stroke-width="2" stroke-linejoin="round"></path>
      <text x="204" y="192" text-anchor="middle" font-size="15" font-weight="500" fill="var(--primary-text-color)">${fmtW(v.home)}</text>
    </svg>`;
  }

  _renderBand() {
    const el = this.shadowRoot && this.shadowRoot.getElementById("band");
    if (!el) return;
    const t = this._t, P = this._p;
    if (this._error) { el.innerHTML = `<div class="muted">${this._esc(this._error)}</div>`; return; }
    if (!this._data) { el.innerHTML = `<div class="muted">…</div>`; return; }
    const arrays = this._arrays();
    if (!arrays.length) { el.innerHTML = `<div class="muted">${t.noData}</div>`; return; }

    const now = Date.now() / 1000;
    const hours = [...new Set(arrays.flatMap((a) => a.expected.filter(([, v]) => v > 1).map(([h]) => h)))].sort((a, b) => a - b);
    if (!hours.length) { el.innerHTML = `<div class="muted">${t.noData}</div>`; return; }
    const t0 = hours[0], t1 = hours[hours.length - 1] + 3600;
    const W = 408, H = 120;
    const val = (series, h) => { const p = series.find(([x]) => x === h); return p ? p[1] : 0; };
    const allH = []; for (let h = t0; h < t1; h += 3600) allH.push(h);

    // load: measured hourly means today, the rest from the mean of the last days at the same hour
    const { load, socPast } = this._loadAndSoc(allH, now);
    const pvTot = (h) => arrays.reduce((a, arr) => a + val(arr.expected, h), 0);
    const lossTot = (h) => arrays.reduce((a, arr) => a + val(arr.loss, h), 0);
    const peak = Math.max(...allH.map((h) => Math.max(pvTot(h) + lossTot(h), load[h] || 0)), 100);
    const x = (ts) => ((ts - t0) / (t1 - t0)) * W;
    const y = (p) => H - 1 - (p / peak) * (H - 6);
    const pts = (f) => [[t0, 0], ...allH.map((h) => [h + 1800, f(h)]), [t1, 0]];
    const px = (series, fy) => series.map(([a, b]) => [x(a), fy(b)]);
    const area = (lo, hi) => {
      const top = px(pts(hi), y), bot = px(pts(lo).reverse(), y);
      return `M${top[0][0].toFixed(1)},${top[0][1].toFixed(1)}${curve(top)} L${bot[0][0].toFixed(1)},${bot[0][1].toFixed(1)}${curve(bot)} Z`;
    };
    const nowX = Math.max(0, Math.min(W, x(now)));

    // stacked arrays, darker first
    let layers = "", base = () => 0;
    arrays.forEach((arr, i) => {
      const lo = base, hi = ((b) => (h) => b(h) + val(arr.expected, h))(base);
      const color = i % 2 ? P.pv : P.pvDark;
      layers += `<path d="${area(lo, hi)}" fill="${color}" fill-opacity="0.35"></path><path d="${area(lo, hi)}" fill="${color}" clip-path="url(#nx-past)"></path>`;
      base = hi;
    });
    const loss = `<path d="${area(pvTot, (h) => pvTot(h) + lossTot(h))}" fill="${P.shade}" fill-opacity="0.3" stroke="${P.shade}" stroke-width="1" stroke-dasharray="2 2"></path>`;

    // battery: from the present state of charge forward with expected PV minus expected load
    const c = this._config, bats = c.batteries || [];
    const cap = bats.reduce((a, b) => a + (Number(b.capacity) || 0), 0) * 1000;
    const live = this._live();
    let soc = live.socAll !== null && cap ? live.socAll / 100 * cap : null, fullAt = null, unusedWh = 0;
    const socFuture = [], unusedAt = {};
    if (soc !== null) {
      socFuture.push([now, soc / cap]);
      for (const h of allH) {
        if (h + 3600 <= now) continue;
        const share = Math.min(1, (h + 3600 - Math.max(h, now)) / 3600);
        const surplus = (pvTot(h) - (load[h] || 0)) * share;
        if (surplus > 0) {
          const add = Math.min(cap - soc, surplus);
          soc += add;
          const rest = surplus - add;
          if (!c.export && rest > 0) { unusedAt[h] = rest / share; unusedWh += rest; }
          if (fullAt === null && soc >= cap - 1) fullAt = h + 3600 * (add / Math.max(surplus, 1));
        } else soc = Math.max(0, soc + surplus);
        socFuture.push([h + 3600, soc / cap]);
      }
    }
    const ys = (f) => H - 1 - f * (H - 12);
    const loadLine = (from, to, dash) => {
      const p = allH.filter((h) => h + 1800 >= from && h + 1800 <= to && load[h] !== undefined);
      return p.length > 1 ? `<path d="${line(p.map((h) => [x(h + 1800), y(load[h])]))}" fill="none" stroke="${P.home}" stroke-width="1.8"${dash ? ` stroke-dasharray="4 3"` : ""}></path>` : "";
    };
    const socLine = (series, dash) => series.length > 1
      ? `<path d="${line(px(series, ys))}" fill="none" stroke="${P.bat}" stroke-width="2"${dash ? ` stroke-dasharray="4 3"` : ""}></path>` : "";
    const unusedArea = Object.keys(unusedAt).length
      ? `<path d="${area((h) => load[h] || 0, (h) => (load[h] || 0) + (unusedAt[h] || 0))}" fill="url(#nx-unused)"></path>` : "";
    const fullMark = fullAt !== null
      ? `<circle cx="${x(fullAt).toFixed(1)}" cy="${ys(1).toFixed(1)}" r="4" fill="var(--card-background-color, #fff)" stroke="${P.bat}" stroke-width="2"></circle>
         <text x="${Math.min(W - 60, Math.max(0, x(fullAt) - 30)).toFixed(1)}" y="${(ys(1) + 15).toFixed(1)}" font-size="10" font-weight="500" fill="${P.bat}" stroke="var(--card-background-color, #fff)" stroke-width="3" paint-order="stroke">${t.full} ${this._hm(fullAt)}</text>` : "";

    const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t.pv}, ${t.load}, ${t.soc}">
      <defs>
        <clipPath id="nx-plot"><rect x="0" y="0" width="${W}" height="${H}"></rect></clipPath>
        <clipPath id="nx-past"><rect x="0" y="0" width="${nowX.toFixed(1)}" height="${H}"></rect></clipPath>
        <pattern id="nx-unused" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="${P.unusedBg}"></rect><line x1="0" y1="0" x2="0" y2="6" stroke="${P.unused}" stroke-width="2.5"></line></pattern>
      </defs>
      ${layers}${loss}${unusedArea}
      <g clip-path="url(#nx-plot)">${loadLine(t0, now, false)}${loadLine(now - 3600, t1, true)}</g>
      <g clip-path="url(#nx-plot)">${socLine(socPast, false)}${socLine(socFuture, true)}</g>${fullMark}
      <line x1="0" y1="${H - 0.5}" x2="${W}" y2="${H - 0.5}" stroke="var(--divider-color)"></line>
      ${now > t0 && now < t1 ? `<line x1="${nowX.toFixed(1)}" y1="0" x2="${nowX.toFixed(1)}" y2="${H}" stroke="var(--primary-text-color)" stroke-width="1.5"></line>` : ""}
    </svg>`;

    const lanes = arrays.map((arr) => {
      const fx = arr.periods.filter((p) => p.end > t0 && p.start < t1).map((p) => {
        const a = Math.max(p.start, t0), b = Math.min(p.end, t1);
        const color = p.type === "shade" ? P.shade : P.refl;
        const title = `${p.type === "shade" ? t.shade : t.refl} ${this._hm(p.start)}–${this._hm(p.end)} · ${p.type === "shade" ? "−" : "+"}${this._kwh(p.wh)} ${t.kwh}`;
        return `<div class="fx" title="${this._esc(title)}" style="left:${(x(a) / W * 100).toFixed(1)}%;width:${((x(b) - x(a)) / W * 100).toFixed(1)}%;background:${color}"></div>`;
      }).join("");
      return `<div class="lane"><div class="base"></div>${fx}<span class="name">${this._esc(arr.label)}</span></div>`;
    }).join("");

    const ticks = [];
    for (let h = t0; h <= t1; h += 3600 * Math.max(1, Math.ceil((t1 - t0) / 3600 / 6))) ticks.push(`<span>${this._hm(h).slice(0, 2)}</span>`);
    const done = allH.reduce((a, h) => a + (h + 3600 <= now ? pvTot(h) : h < now ? pvTot(h) * (now - h) / 3600 : 0), 0);
    const total = allH.reduce((a, h) => a + pvTot(h), 0);
    const lost = allH.reduce((a, h) => a + lossTot(h), 0);
    const tomorrow = arrays.reduce((a, arr) => a + arr.tomorrow_wh, 0);
    el.innerHTML = `${svg}${lanes}<div class="axis">${ticks.join("")}</div>
      <div class="legend">
        <span><svg width="16" height="6" aria-hidden="true"><line x1="0" y1="3" x2="16" y2="3" stroke="${P.home}" stroke-width="1.8" stroke-dasharray="4 3"></line></svg>${t.load}</span>
        ${cap ? `<span><svg width="16" height="6" aria-hidden="true"><line x1="0" y1="3" x2="16" y2="3" stroke="${P.bat}" stroke-width="2"></line></svg>${t.soc}</span>` : ""}
        ${unusedWh > 0 ? `<span><svg width="12" height="10" aria-hidden="true"><rect width="12" height="10" fill="${P.unusedBg}" stroke="${P.unused}"></rect></svg>${t.unused}</span>` : ""}
        ${lost > 0 ? `<span><span style="width:10px;height:10px;background:${P.shade};opacity:.5"></span>${t.lost}</span>` : ""}
      </div>
      <div class="row">
        <span><span class="big">${this._kwh(done)}</span> / ${this._kwh(total)} ${t.kwh}</span>
        ${lost >= 50 ? `<span style="font-size:12px;color:${P.shade}">${t.shade} −${this._kwh(lost)} ${t.kwh}</span>` : ""}
        ${unusedWh >= 50 ? `<span class="warn" style="color:${P.warnText};background:${P.warnBg}">${this._kwh(unusedWh)} ${t.kwh} ${t.unused}</span>` : ""}
        <span class="muted">${t.tomorrow} <b style="color:var(--primary-text-color);font-weight:500">${this._kwh(tomorrow)}</b></span>
      </div>`;
  }

  // arrays of today in the configured order (by group name / array), else all
  _arrays() {
    const all = this._data.today.arrays.map((a) => ({ ...a, label: a.name }));
    const groups = this._config.pv.groups || [];
    if (!groups.length) return all;
    const out = [];
    for (const g of groups) {
      const key = (g.array || g.name || "").toLowerCase();
      const a = all.find((x) => x.name.toLowerCase() === key);
      if (a) out.push({ ...a, label: g.name || a.name });
    }
    return out.length ? out : all;
  }

  _loadAndSoc(allH, now) {
    const c = this._config, stats = this._data.stats || {};
    const homeId = c.home && c.home.entity;
    const load = {}, socPast = [];
    const homeStats = (stats[homeId] || []).map((r) => [r.start / 1000, r.mean]);
    const scale = homeId && this._hass.states[homeId] && (this._hass.states[homeId].attributes.unit_of_measurement || "").toLowerCase() === "kw" ? 1000 : 1;
    const today = this._data.dayStart / 1000;
    const byHour = {};
    for (const [ts, m] of homeStats) {
      if (m === null) continue;
      if (ts >= today) load[ts] = m * scale;
      else (byHour[(ts - today + 7 * 86400) % 86400] ||= []).push(m * scale);
    }
    for (const h of allH) {
      if (load[h] !== undefined && h + 3600 <= now) continue;
      const past = byHour[(h - today) % 86400];
      if (h <= now && h + 3600 > now) load[h] = this._live().home;
      else if (past && past.length) load[h] = past.reduce((a, b) => a + b, 0) / past.length;
    }
    const bats = c.batteries || [];
    const cap = bats.reduce((a, b) => a + (Number(b.capacity) || 0), 0);
    if (cap) {
      for (const h of allH) {
        if (h + 3600 > now) break;
        let sum = 0, ok = true;
        for (const b of bats) {
          const r = (stats[b.soc] || []).find((s) => s.start / 1000 === h);
          if (!r || r.mean === null) { ok = false; break; }
          sum += r.mean * (Number(b.capacity) || 0);
        }
        if (ok) socPast.push([h + 1800, sum / cap / 100]);
      }
      const live = this._live();
      if (live.socAll !== null) socPast.push([now, live.socAll / 100]);
    }
    return { load, socPast };
  }

  _hm(ts) {
    return new Date(ts * 1000).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", timeZone: this._tz });
  }

  _kwh(wh) {
    return (wh / 1000).toLocaleString(this._lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  _esc(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
}

// Smooth path through points (pixels) without overshoot: monotone cubic (Fritsch-Carlson), as " C..." segments.
function curve(p) {
  const n = p.length, f = (v) => v.toFixed(1);
  if (n < 3) return p.slice(1).map(([a, b]) => ` L${f(a)},${f(b)}`).join("");
  const m = [], t = [];
  for (let k = 0; k < n - 1; k++) m.push((p[k + 1][1] - p[k][1]) / (p[k + 1][0] - p[k][0] || 1e-9));
  t.push(m[0]);
  for (let k = 1; k < n - 1; k++) t.push(m[k - 1] * m[k] <= 0 ? 0 : (m[k - 1] + m[k]) / 2);
  t.push(m[n - 2]);
  for (let k = 0; k < n - 1; k++) {
    if (m[k] === 0) { t[k] = 0; t[k + 1] = 0; continue; }
    const a = t[k] / m[k], b = t[k + 1] / m[k], s = a * a + b * b;
    if (s > 9) { const tau = 3 / Math.sqrt(s); t[k] = tau * a * m[k]; t[k + 1] = tau * b * m[k]; }
  }
  let out = "";
  for (let k = 0; k < n - 1; k++) {
    const dx = (p[k + 1][0] - p[k][0]) / 3;
    out += ` C${f(p[k][0] + dx)},${f(p[k][1] + t[k] * dx)} ${f(p[k + 1][0] - dx)},${f(p[k + 1][1] - t[k + 1] * dx)} ${f(p[k + 1][0])},${f(p[k + 1][1])}`;
  }
  return out;
}

function line(p) {
  return `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}${curve(p)}`;
}

customElements.define("noxcast-card", NoxcastCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "noxcast-card", name: "Noxcast", description: "Power flow, today's yield per array, effects and surplus", preview: false });
