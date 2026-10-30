// Noxcast frontend helpers shared by the panel, its section modules and the dashboard cards: the websocket
// call with the payload version, and number and time formatting. Time axes and scales stay with each chart.
// Import it with the importer's own cache buster, so that an update never pairs a new script with an old copy:
//   await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href)
// panel.py must include this file in the version of every script that imports it.

// payload schema the scripts read (WS_SCHEMA_VERSION in const.py): another one means the integration was updated
// while this page was open, and the script asks for a reload instead of misreading the data
export const WS_SCHEMA = 1;

// a websocket request that gives up: one lost while the connection was rebuilt never answers
export function ask(hass, msg, timeoutMs = 20000) {
  return Promise.race([hass.connection.sendMessagePromise(msg),
    new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), timeoutMs))]);
}

// a time series of the payload ({t: [...], name: [...]}) as rows [t, value of each name]
export const tsRows = (s, ...names) => (s ? s.t.map((t, i) => [t, ...names.map((n) => s[n][i])]) : []);

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

export const num = (v, lang, digits = 0) => Number(v).toLocaleString(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const kwh = (wh, lang, digits = 1) => num(wh / 1000, lang, digits);

// hour and minute of a timestamp in seconds; options e.g. { hourCycle: "h23" }
export const hm = (ts, lang, timeZone, options = {}) => new Date(ts * 1000).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", timeZone, ...options });
