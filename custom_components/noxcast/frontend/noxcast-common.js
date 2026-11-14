// Noxcast frontend helpers shared by the panel, its section modules and the dashboard cards: the websocket
// call with the payload version, and number and time formatting. Time axes and scales stay with each chart.
// Import it with the importer's own cache buster, so that an update never pairs a new script with an old copy:
//   await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href)
// panel.py must include this file in the version of every script that imports it.

// payload schema the scripts read (WS_SCHEMA_VERSION in const.py): another one means the integration was updated
// while this page was open, and the script asks for a reload instead of misreading the data
export const WS_SCHEMA = 3;

// a websocket request that gives up: one lost while the connection was rebuilt never answers
export function ask(hass, msg, timeoutMs = 20000) {
  return Promise.race([hass.connection.sendMessagePromise(msg),
    new Promise((_, reject) => setTimeout(() => reject(new Error(TIMEOUT)), timeoutMs))]);
}
const TIMEOUT = "timeout";

// a request without answer mostly means Home Assistant is starting or Noxcast is learning after a change of its
// settings (the recorder answers the page only after the learning queries); the page asks again by itself
const NO_ANSWER = {
  de: "Noxcast antwortet gerade nicht. Das passiert meist, während Home Assistant startet oder Noxcast nach einer Änderung der Einstellungen neu lernt. Die Seite versucht es gleich wieder.",
  en: "Noxcast is not answering right now. This usually happens while Home Assistant starts or Noxcast relearns after a change of its settings. The page tries again shortly.",
};

// an error of a request as text for the user
export function errorText(err, lang) {
  const text = String((err && err.message) || err);
  return text === TIMEOUT ? NO_ANSWER[(lang || "en").slice(0, 2)] || NO_ANSWER.en : text;
}

// a time series of the payload ({t: [...], name: [...]}) as rows [t, value of each name]
export const tsRows = (s, ...names) => (s ? s.t.map((t, i) => [t, ...names.map((n) => s[n][i])]) : []);

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
// an entity ID as HTML that can wrap after its dots and underscores; copying it still gives the plain ID
export const escId = (s) => esc(s).replace(/[._]/g, "$&<wbr>");

export const num = (v, lang, digits = 0) => Number(v).toLocaleString(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const kwh = (wh, lang, digits = 1) => num(wh / 1000, lang, digits);
// power as [number, unit]: whole watts below 1 kW, where a decimal of kW would hide the difference; kW above
// (decided on the rounded watts, so 999.7 W reads 1,00 kW, not 1.000 W; "|| 0" keeps -0.3 W from reading -0 W)
export const powerParts = (w, lang) => {
  const r = Math.round(w) || 0;
  return Math.abs(r) >= 1000 ? [num(w / 1000, lang, 2), "kW"] : [num(r, lang), "W"];
};
// joined by a no-break space, so a line never ends between number and unit
export const power = (w, lang) => powerParts(w, lang).join(" ");

// hour and minute of a timestamp in seconds; options e.g. { hourCycle: "h23" }
export const hm = (ts, lang, timeZone, options = {}) => new Date(ts * 1000).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", timeZone, ...options });

// Smooth path through points (pixels) as " C..." segments: monotone cubic (Fritsch-Carlson), so the curve never
// overshoots its points; between two hourly means it stays within them, and clear sky stays the upper bound.
export function smooth(p) {
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
// the whole path: move to the first point, then smooth through the rest
export const smoothPath = (p) => `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}${smooth(p)}`;
