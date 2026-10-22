// Noxcast panel look: one constructable stylesheet shared by the panel and its section modules.
// The panel adopts it into its own shadow root and into every section's, after their own styles, so
// sections keep their layout and only gain the common look. Dark mode: the host carries [dark].

const CSS = `
:host {
  --nx-sun: #F5A524; --nx-ember: #FF6A3D; --nx-violet: #8B5CF6; --nx-cyan: #22C7E8;
  --nx-grad: linear-gradient(115deg, var(--nx-sun) 0%, var(--nx-ember) 45%, var(--nx-violet) 100%);
  --nx-surface: color-mix(in srgb, var(--card-background-color, #fff) 84%, transparent);
  --nx-edge: color-mix(in srgb, var(--primary-text-color, #000) 9%, transparent);
  --nx-tint: color-mix(in srgb, var(--primary-text-color, #000) 4%, transparent);
  --nx-shadow: 0 1px 0 rgba(255, 255, 255, .7) inset, 0 18px 40px -24px rgba(40, 24, 80, .35);
  --nx-radius: 20px;
}
:host([dark]) {
  --nx-surface: color-mix(in srgb, var(--card-background-color, #1c1c1c) 72%, transparent);
  --nx-shadow: 0 1px 0 rgba(255, 255, 255, .06) inset, 0 24px 48px -28px rgba(0, 0, 0, .9);
}

/* cards: frosted surface, a hairline that glows from sun to violet, a soft rise on first paint */
.card {
  background: linear-gradient(var(--nx-surface), var(--nx-surface)) padding-box,
    linear-gradient(135deg, color-mix(in srgb, var(--nx-sun) 55%, transparent), var(--nx-edge) 28%,
      var(--nx-edge) 72%, color-mix(in srgb, var(--nx-violet) 55%, transparent)) border-box;
  border: 1px solid transparent; border-radius: var(--nx-radius); box-shadow: var(--nx-shadow);
  -webkit-backdrop-filter: blur(16px) saturate(1.2); backdrop-filter: blur(16px) saturate(1.2);
  animation: nx-rise .55s cubic-bezier(.2, .75, .2, 1) both;
}
.stack > :nth-child(2), .split > :nth-child(2) { animation-delay: .06s; }
.stack > :nth-child(3), .split > :nth-child(3) { animation-delay: .12s; }
.stack > :nth-child(4) { animation-delay: .18s; }
.stack > :nth-child(n+5) { animation-delay: .24s; }
.split > .card, .split > .grow, .split > .grow2 { animation: nx-rise .55s cubic-bezier(.2, .75, .2, 1) both; }
@keyframes nx-rise { from { opacity: 0; transform: translateY(10px) scale(.995); } }

/* headings: small caps label with a gradient tick */
h3 { display: flex; align-items: center; gap: 10px; font-size: 12px; font-weight: 600; letter-spacing: .09em;
  text-transform: uppercase; color: var(--secondary-text-color); margin-bottom: 12px; }
h3::before { content: ""; flex: none; width: 18px; height: 3px; border-radius: 3px; background: var(--nx-grad); }
h2 { font-weight: 600; letter-spacing: -.01em; }

/* key figures: tiles with the large value in the brand gradient */
:host(:not(noxcast-panel)) .figs { gap: 10px; }
:host(:not(noxcast-panel)) .fig { padding: 12px 16px; min-width: 128px; border-radius: 14px; background: var(--nx-tint);
  border: 1px solid var(--nx-edge); position: relative; overflow: hidden; }
:host(:not(noxcast-panel)) .fig::after { content: ""; position: absolute; inset: auto -20% -60% auto; width: 90px; height: 90px;
  border-radius: 50%; background: radial-gradient(closest-side, color-mix(in srgb, var(--nx-ember) 18%, transparent), transparent); pointer-events: none; }
:host(:not(noxcast-panel)) .fig > .muted:first-child { font-size: 11px; letter-spacing: .07em; text-transform: uppercase; }
.big { font-size: 28px; font-weight: 650; letter-spacing: -.02em; line-height: 1.15;
  background: var(--nx-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.big .muted { -webkit-text-fill-color: var(--secondary-text-color); }

/* controls */
button { transition: background-color .2s, color .2s, box-shadow .2s, border-color .2s; }
.chip.on, .btn:hover, .round:hover { border-color: transparent; color: #fff; background: var(--nx-grad);
  box-shadow: 0 8px 20px -10px var(--nx-ember); }
input[type=range] { -webkit-appearance: none; appearance: none; height: 6px; border-radius: 6px; margin: 10px 0; cursor: pointer;
  background: linear-gradient(90deg, color-mix(in srgb, var(--nx-sun) 40%, transparent), color-mix(in srgb, var(--nx-ember) 40%, transparent),
    color-mix(in srgb, var(--nx-violet) 40%, transparent)); }
input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; border-radius: 50%; cursor: grab;
  background: var(--card-background-color, #fff); border: 3px solid var(--nx-ember);
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--nx-ember) 18%, transparent), 0 6px 14px -4px var(--nx-ember); }
input[type=range]::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%; cursor: grab;
  background: var(--card-background-color, #fff); border: 3px solid var(--nx-ember);
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--nx-ember) 18%, transparent), 0 6px 14px -4px var(--nx-ember); }
input[type=range]:active::-webkit-slider-thumb { cursor: grabbing; transform: scale(1.1); }
:focus-visible { outline: 2px solid var(--nx-ember); outline-offset: 2px; }

/* learned maps: thumbnails lift on hover */
.minisvg { border-radius: 12px; border-color: var(--nx-edge); transition: transform .25s, box-shadow .25s; }
.mini:hover .minisvg { transform: translateY(-3px); box-shadow: 0 16px 28px -18px var(--nx-ember); }
.mapcard { padding: 16px; }

/* tables */
table { border-collapse: separate; border-spacing: 0; }
tbody tr { transition: background-color .15s; }
tbody tr:hover td { background: color-mix(in srgb, var(--nx-sun) 7%, transparent); }
th { text-transform: uppercase; letter-spacing: .06em; font-size: 11px; }

/* charts */
.rail, .grid { stroke-dasharray: 2 5; }
.legend i, .sw { border-radius: 4px; }
.help { line-height: 1.6; }

/* ------------------------------------------------------------ the panel frame */
:host(noxcast-panel) {
  background:
    radial-gradient(1100px 520px at -5% -12%, color-mix(in srgb, var(--nx-sun) 16%, transparent), transparent 62%),
    radial-gradient(900px 520px at 105% -8%, color-mix(in srgb, var(--nx-violet) 16%, transparent), transparent 60%),
    radial-gradient(800px 600px at 50% 120%, color-mix(in srgb, var(--nx-cyan) 8%, transparent), transparent 60%),
    var(--primary-background-color);
}
:host(noxcast-panel) .toolbar { position: sticky; top: 0; z-index: 2; border-bottom: 1px solid var(--nx-edge);
  background: color-mix(in srgb, var(--app-header-background-color, var(--primary-background-color)) 70%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(1.3); backdrop-filter: blur(18px) saturate(1.3); }
:host(noxcast-panel) .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; letter-spacing: -.01em;
  background: var(--nx-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.logo { width: 26px; height: 26px; flex: none; }
.navtabs { gap: 6px; padding: 4px; border-radius: 999px; background: var(--nx-tint); border: 1px solid var(--nx-edge); scrollbar-width: none; }
.navtab { border: 0 !important; border-radius: 999px; padding: 6px 14px !important; font-weight: 500; }
.navtab:hover { color: var(--primary-text-color); background: var(--nx-tint); }
.navtab.on { color: #fff; background: var(--nx-grad); box-shadow: 0 8px 22px -10px var(--nx-ember); }

.hero { position: relative; display: flex; align-items: center; gap: 24px; padding: 18px 4px 22px; overflow: hidden; }
.hero-text { flex: 1 1 auto; min-width: 0; animation: nx-rise .6s cubic-bezier(.2, .75, .2, 1) both; }
.hero h1 { margin: 0; font-size: clamp(28px, 4.2vw, 44px); font-weight: 750; letter-spacing: -.03em; line-height: 1.05;
  background: var(--nx-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.hero p { margin: 8px 0 0; max-width: 62ch; color: var(--secondary-text-color); font-size: 15px; line-height: 1.5; }
.orbit { flex: none; width: 150px; height: 96px; opacity: .9; }
.orbit .ring { fill: none; stroke: var(--nx-edge); stroke-width: 1; }
.orbit .arc { fill: none; stroke: url(#nx-arc); stroke-width: 2; stroke-linecap: round; stroke-dasharray: 4 6; animation: nx-dash 14s linear infinite; }
.orbit .sun { fill: var(--nx-sun); filter: drop-shadow(0 0 8px var(--nx-sun)); offset-path: path("M10 86 A65 65 0 0 1 140 86");
  animation: nx-orbit 18s ease-in-out infinite alternate; }
@keyframes nx-dash { to { stroke-dashoffset: -100; } }
@keyframes nx-orbit { from { offset-distance: 8%; } to { offset-distance: 92%; } }
/* phones: smaller header; every table row becomes a block: the first cell as its title, the others as
   "column: value" lines (the labels are set by labelTables from the table head) */
@media (max-width: 640px) {
  .orbit { display: none; } .hero { padding: 6px 2px 14px; }
  .hero h1 { font-size: 26px; } .hero p { font-size: 13px; margin-top: 4px; }
  table, tbody, tr, td { display: block; width: 100%; box-sizing: border-box; }
  thead { display: none; }
  tr { padding: 8px 0; border-top: 1px solid var(--divider-color); }
  tbody tr:first-child { border-top: 0; }
  td { border: 0 !important; padding: 2px 0 !important; text-align: right !important; white-space: normal; overflow: hidden; }
  td::before { content: attr(data-label); float: left; margin-right: 12px; color: var(--secondary-text-color); font-size: 12px; }
  td:first-child { text-align: left !important; font-weight: 500; }
  td:first-child::before { content: none; }
  td:empty { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
`;

let sheet = null;

export function adoptTheme(root) {
  if (!root || !("adoptedStyleSheets" in root)) return;
  if (!sheet) {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
  }
  if (!root.adoptedStyleSheets.includes(sheet)) root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
  if (!observed.has(root)) {                     // label the cells of every table the section renders
    observed.add(root);
    new MutationObserver(() => labelTables(root)).observe(root, { childList: true, subtree: true });
    labelTables(root);
  }
}

const observed = new WeakSet();

// each cell gets the text of its column head, shown in front of the value when rows become blocks on phones
function labelTables(root) {
  for (const table of root.querySelectorAll("table")) {
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    for (const tr of table.querySelectorAll("tbody tr")) {
      [...tr.children].forEach((td, i) => { if (heads[i] && td.dataset.label !== heads[i]) td.dataset.label = heads[i]; });
    }
  }
}

// the mark next to the brand: a sun on its arc above the horizon
export const LOGO = `<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="nx-logo" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#F5A524"></stop><stop offset=".5" stop-color="#FF6A3D"></stop><stop offset="1" stop-color="#8B5CF6"></stop></linearGradient></defs>
  <path d="M4 22a12 12 0 0 1 24 0" fill="none" stroke="url(#nx-logo)" stroke-width="2.4" stroke-linecap="round"></path>
  <circle cx="22.5" cy="12.5" r="4" fill="url(#nx-logo)"></circle><path d="M2 26h28" stroke="url(#nx-logo)" stroke-width="2.4" stroke-linecap="round"></path></svg>`;

// decoration of the page header: the sun travelling along its daily arc
export const ORBIT = `<svg class="orbit" viewBox="0 0 150 96" aria-hidden="true"><defs><linearGradient id="nx-arc" x1="0" x2="1">
  <stop offset="0" stop-color="#F5A524"></stop><stop offset=".5" stop-color="#FF6A3D"></stop><stop offset="1" stop-color="#8B5CF6"></stop></linearGradient></defs>
  <path class="ring" d="M2 86h146"></path><path class="arc" d="M10 86 A65 65 0 0 1 140 86"></path><circle class="sun" r="6"></circle></svg>`;
