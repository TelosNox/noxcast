// Noxcast panel section "Installation": the installation as a flow diagram (PV arrays -> systems -> house -> grid,
// drawn by the backend, flowchart.py) and, for the node clicked, its settings and how far Noxcast already computes
// each: exact, simplified or not yet. There a PV array, a battery or a system can be moved to another system
// ("noxcast/move_part", the same rules as the forms), and admins change a part in the form of its dialog beside the
// diagram ("noxcast/part_form", shown with Home Assistant's ha-form): every change is drawn first
// ("noxcast/part_preview"), saving stores it as the dialog does ("noxcast/part_save"). The dashed "+" boxes of the
// diagram add a part with the same form, drawn in the diagram before it is saved; a system's template is picked on
// its picture. Removing a part shows first what goes with it and the diagram without it ("noxcast/part_remove").
// Plain custom element, no build step. Data comes from the websocket command "noxcast/installation".
const { WS_SCHEMA, ask, errorText, esc } = await import(new URL("./noxcast-common.js" + new URL(import.meta.url).search, import.meta.url).href);

const INST_TEXT = {
  de: {
    outdated: "Noxcast wurde aktualisiert – bitte Seite neu laden",
    refresh: "Aktualisieren", house: "Haushalt", noSystems: "Noch kein System eingetragen.",
    edit: "Ändern: hier mit „Ändern“ oder unter Einstellungen → Geräte & Dienste → Noxcast.",
    change: "Ändern", changeHouse: "Haushalt ändern", saveChange: "Speichern", cancel: "Abbrechen",
    previewHelp: "Das Bild zeigt die Änderung schon. Gespeichert wird erst mit „Speichern“; danach lädt Noxcast neu.",
    changed: "Dieser Teil wurde inzwischen anderswo geändert (z. B. in einem Dialog). Das Bild zeigt jetzt den gespeicherten Stand; ändere ihn bitte noch einmal.",
    noForm: "Das Formular lässt sich hier nicht laden. Ändere den Teil unter Einstellungen → Geräte & Dienste → Noxcast.",
    added: { array: "Neue PV-Gruppe", battery: "Neuer Akku", system: "Neues System", sub: "Neues Teilsystem" },
    addHelp: "Das Bild zeigt den neuen Teil schon, hervorgehoben. Angelegt wird er erst mit „Speichern“; danach lädt Noxcast neu.",
    pickTemplate: "Tippe auf die Vorlage, die zu deinem Gerät passt:",
    remove: "Entfernen", removeAsk: "{name} entfernen?", removeWith: "Mit entfernt wird: {list}.",
    removeWarn: "Seine Entitäten und was Noxcast dafür gelernt hat, werden mit entfernt. Das lässt sich nicht rückgängig machen. Das Bild zeigt die Anlage ohne diesen Teil.",
    removeYes: "Endgültig entfernen", removing: "Entfernt …",
    status: { exact: "rechnet genau", simplified: "rechnet vereinfacht", not_yet: "noch nicht berücksichtigt" },
    inherited: "vom System", fromHouse: "Vorgabe des Haushalts",
    help: "Jede Angabe wird gespeichert, auch wenn Noxcast sie noch nicht verrechnet. Spätere Versionen rechnen sie genauer, ohne dass du etwas neu eintragen musst.",
    templates: { inverter: "Wechselrichter ohne Akku", pv_battery: "PV mit Akku in einem Gerät", ac_battery: "Akku am Hausnetz",
      coupled: "Gekoppelte Systeme" },
    settings: { feed_in: "Einspeisevorgabe",
      one_storage: "Akkus werden als ein Speicher simuliert", output_limit: "Ausgangsgrenze",
      limit_per_phase: "Grenze gilt pro Phase", phase: "Phase", regulation: "Regelung", fixed_power: "Feste Leistung",
      feed_in_rule: "Einspeisung", charges_from: "Akku lädt aus", balance: "Akkus gleichen sich aus",
      sensor_limit: "Jeder Sensor liefert höchstens", full_learning: "PV-Gruppen, deren Lernen Stunden mit vollem Akku auslässt",
      charge_learning: "Stunden mit größter Ladeleistung zählen als „mindestens“",
      held_learning: "Stunden, in denen der Haushalt kaum Strom bezog, zählen als „mindestens“",
      limit_learning: "Stunden mit der Einspeisung an der Grenze zählen als „mindestens“" },
    values: { free: "frei", none: "Nulleinspeisung", all: "volle Leistung", load: "Eigenverbrauch", fixed: "feste Leistung",
      export: "speist den Rest trotzdem ein", never: "speist nie ein",
      own_pv: "eigener PV", house: "dem Haushalt", l1: "L1", l2: "L2", l3: "L3", three: "dreiphasig", yes: "ja", no: "nein",
      chargeMissing: "braucht Sensor Akkuleistung und max. Ladeleistung", gridMissing: "braucht die Netzleistung im Haupteintrag" },
    battery: "Akku", array: "PV-Gruppe", sensors: "{n} Sensoren", sensor: "1 Sensor", sub: "Teilsystem",
    empty: "enthält noch nichts", pick: "Auf einen Knoten im Bild tippen, um seine Einstellungen zu sehen.",
    openEntry: "Einträge öffnen", noSettings: "Keine Einstellungen, die Noxcast verrechnet.",
    belongs: "Gehört zu", batteryBelongs: "Akku gehört zu", alone: "– eigenständig am Haushalt –",
    save: "Zuordnung speichern", saving: "Speichert …", notPossible: "Dorthin kann der Teil nicht.",
    moveHelp: "Noxcast lädt danach neu, wie nach dem Bearbeiten eines Eintrags.",
  },
  en: {
    outdated: "Noxcast was updated – please reload the page",
    refresh: "Refresh", house: "Household", noSystems: "No system entered yet.",
    edit: "Change it here with “Change”, or under Settings → Devices & services → Noxcast.",
    change: "Change", changeHouse: "Change household", saveChange: "Save", cancel: "Cancel",
    previewHelp: "The picture already shows the change. It is stored only with “Save”; Noxcast reloads afterwards.",
    changed: "This part was changed elsewhere meanwhile (e.g. in a dialog). The picture now shows what is stored; please change it again.",
    noForm: "The form cannot be loaded here. Change the part under Settings → Devices & services → Noxcast.",
    added: { array: "New PV array", battery: "New battery", system: "New system", sub: "New subsystem" },
    addHelp: "The picture already shows the new part, highlighted. It is created only with “Save”; Noxcast reloads afterwards.",
    pickTemplate: "Tap the template that fits your device:",
    remove: "Remove", removeAsk: "Remove {name}?", removeWith: "Removed with it: {list}.",
    removeWarn: "Its entities and what Noxcast learned for it are removed too. This cannot be undone. The picture shows the installation without this part.",
    removeYes: "Remove for good", removing: "Removing …",
    status: { exact: "computed exactly", simplified: "computed simplified", not_yet: "not used yet" },
    inherited: "from its system", fromHouse: "the household's rule",
    help: "Every setting is stored, even where Noxcast does not compute it yet. Later versions compute it more precisely without you entering anything again.",
    templates: { inverter: "Inverter without battery", pv_battery: "PV with battery in one device", ac_battery: "Battery at the house grid",
      coupled: "Coupled systems" },
    settings: { feed_in: "Feed-in rule",
      one_storage: "Batteries are simulated as one storage", output_limit: "Output limit",
      limit_per_phase: "Limit holds per phase", phase: "Phase", regulation: "Regulation", fixed_power: "Fixed power",
      feed_in_rule: "Feed-in", charges_from: "Battery charges from", balance: "Batteries balance each other",
      sensor_limit: "Each sensor delivers at most", full_learning: "PV arrays whose learning leaves out hours with the battery full",
      charge_learning: "Hours charging at the maximum count as \"at least\"",
      held_learning: "Hours in which the household imported little count as \"at least\"",
      limit_learning: "Hours with the feed-in at the limit count as \"at least\"" },
    values: { free: "free", none: "zero export", all: "full power", load: "self-consumption", fixed: "fixed power",
      export: "feeds the rest in anyway", never: "never feeds in",
      own_pv: "own PV", house: "the household", l1: "L1", l2: "L2", l3: "L3", three: "three-phase", yes: "yes", no: "no",
      chargeMissing: "needs the battery power sensor and max charge power", gridMissing: "needs the grid power in the main entry" },
    battery: "Battery", array: "PV array", sensors: "{n} sensors", sensor: "1 sensor", sub: "Subsystem",
    empty: "contains nothing yet", pick: "Tap a node in the picture to see its settings.",
    openEntry: "Open entries", noSettings: "No settings Noxcast computes.",
    belongs: "Belongs to", batteryBelongs: "Battery belongs to", alone: "– on its own at the household –",
    save: "Save assignment", saving: "Saving …", notPossible: "The part cannot go there.",
    moveHelp: "Noxcast reloads afterwards, as after editing an entry.",
  },
};

class NoxcastInstallation extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (ev) => {
      const path = ev.composedPath();
      if (path.some((n) => n.dataset && n.dataset.refresh)) { this._load(); return; }
      const save = path.find((n) => n.dataset && n.dataset.save);
      if (save) { this._move(save.dataset.save); return; }
      const edit = path.find((n) => n.dataset && n.dataset.edit);
      if (edit) { this._startEdit(edit.dataset.entry, edit.dataset.edit); return; }
      const remove = path.find((n) => n.dataset && n.dataset.remove);
      if (remove) { this._askRemove(remove.dataset.entry, remove.dataset.remove); return; }
      const act = path.find((n) => n.dataset && n.dataset.act);
      if (act) {
        if (act.dataset.act === "save-part") this._savePart();
        else if (act.dataset.act === "remove-yes") this._remove();
        else this._stopEdit();
        return;
      }
      const template = path.find((n) => n.getAttribute && n.getAttribute("data-template"));
      if (template && this._edit) { this._changed({ ...this._edit.input, template: template.getAttribute("data-template") }); return; }
      if (this._edit || this._removal) return;  // the part being changed stays chosen until saved or cancelled
      const add = path.find((n) => n.getAttribute && n.getAttribute("data-add"));
      const flow = path.find((n) => n.classList && n.classList.contains("flow"));
      if (add && flow && this._admin) {
        const [kind, to] = add.getAttribute("data-add").split(":");
        this._startEdit(flow.dataset.entry, null, kind, to || null);
        return;
      }
      if (path.some((n) => n.dataset && n.dataset.entries)) {
        history.pushState(null, "", "/config/integrations/integration/noxcast");
        window.dispatchEvent(new CustomEvent("location-changed"));
        return;
      }
      const node = path.find((n) => n.getAttribute && n.getAttribute("data-node"));
      if (node) { this._sel = node.getAttribute("data-node"); this._render(); }
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    if (first) this._load();
  }

  connectedCallback() { if (this._hass && this._data) this._load(); }

  get _t() { return INST_TEXT[(this._hass.language || "en").slice(0, 2)] || INST_TEXT.en; }
  get _lang() { return this._hass.language || "en"; }
  get _admin() { return !(this._hass.user && !this._hass.user.is_admin); }

  async _load() {
    try {
      const data = await ask(this._hass, { type: "noxcast/installation", language: this._lang });
      if (data.v !== WS_SCHEMA) { this._data = null; this._error = this._t.outdated; this._render(); return; }
      this._data = data;
      this._error = null;
    } catch (err) {
      this._error = errorText(err, this._hass.language);
      clearTimeout(this._retry);                // Noxcast may still be starting after a restart
      this._retry = setTimeout(() => { if (this.isConnected) this._load(); }, 10000);
    }
    this._render();
  }

  // the moves changed in the selects of the detail panel, one after the other; the answer is the new installation
  async _move(entryId) {
    const changed = [...this.shadowRoot.querySelectorAll("select[data-move]")].filter((s) => s.value !== s.dataset.now);
    if (!changed.length || this._saving) return;
    // the choices stay in the selects while saving and after an error
    this._pending = Object.fromEntries(changed.map((s) => [s.dataset.move, s.value]));
    this._saving = true;
    this._moveError = null;
    this._render();
    let data = null;
    try {
      for (const s of changed) {
        data = await ask(this._hass, { type: "noxcast/move_part", entry_id: entryId, part: s.dataset.move,
          to: s.value, language: this._lang });
        delete this._pending[s.dataset.move];
      }
    } catch (err) {
      this._moveError = err && err.code === "not_possible" ? this._t.notPossible : errorText(err, this._hass.language);
    }
    // the answer holds this entry only: keep the others, and what an earlier move already saved
    this._merge(data);
    this._saving = false;
    this._render();
  }

  // ha-form comes with Home Assistant's frontend, but only once a page has used it: a card editor loads it
  async _ensureForm() {
    if (customElements.get("ha-form")) return true;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      if (card.constructor.getConfigElement) await card.constructor.getConfigElement();
    } catch (err) { /* waited for below */ }
    await Promise.race([customElements.whenDefined("ha-form"), new Promise((r) => setTimeout(r, 5000))]);
    return !!customElements.get("ha-form");
  }

  // the form of a part (or "house"), as its dialog has it; without a part, the form of a new one of this kind in
  // the system "to" (a new system: its parent, none on the top level)
  async _startEdit(entryId, part, kind, to) {
    const t = this._t;
    this._editError = null;
    let form;
    try {
      if (!(await this._ensureForm())) throw new Error(t.noForm);
      if (this._hass.loadBackendTranslation) await this._hass.loadBackendTranslation("selector", "noxcast");
      form = await ask(this._hass, { type: "noxcast/part_form", entry_id: entryId, part, kind, to, language: this._lang });
    } catch (err) {
      this._editError = errorText(err, this._hass.language);
      this._render();
      return;
    }
    this._edit = { entryId, part, form, input: form.data, errors: {}, diagram: null, saving: false };
    this._form = this._makeForm(form);
    this._sel = part || "draft";
    this._render();
    if (!part) this._preview();                 // the new part shows in the diagram right away
  }

  // the parts of a request that say which part is edited: its id, or the kind and place of a new one
  _which(e) { return { entry_id: e.entryId, part: e.part, kind: e.form.kind, to: e.form.to, language: this._lang }; }

  _makeForm(form) {
    const f = document.createElement("ha-form");
    f.hass = this._hass;
    this._formTexts(f, form);
    f.addEventListener("value-changed", (ev) => { ev.stopPropagation(); this._changed(ev.detail.value); });
    return f;
  }

  _formTexts(f, form) {
    f.schema = form.schema;
    f.data = form.data;
    f.computeLabel = (s) => form.labels[s.name] || form.sections[s.name] || s.name;
    f.computeHelper = (s) => form.helpers[s.name] || "";
    f.computeError = (err) => form.errors[err] || err;
    f.localizeValue = (key) => this._hass.localize(`component.noxcast.selector.${key}`);
  }

  _changed(value) {
    const e = this._edit;
    if (!e) return;
    const before = e.input;
    e.input = value;
    this._form.data = value;
    // another template brings other settings, filled in by it: the form is built anew, as in the dialog
    if (e.form.kind === "system" && value.template !== before.template) { this._retemplate(); return; }
    clearTimeout(this._previewTimer);
    this._previewTimer = setTimeout(() => this._preview(), 300);
  }

  async _retemplate() {
    const e = this._edit;
    const draft = { name: e.input.name, template: e.input.template };
    if (e.input.parent !== undefined) draft.parent = e.input.parent;
    try {
      const form = await ask(this._hass, { type: "noxcast/part_form", ...this._which(e), draft });
      if (this._edit !== e) return;
      e.form = { ...form, revision: e.form.revision };
      e.input = form.data;
      this._formTexts(this._form, e.form);
    } catch (err) { e.base = errorText(err, this._hass.language); }
    this._render();                             // the picture of the templates marks the new one
    this._preview();
  }

  // the diagram with the form's input, and its problems; only the diagram and the messages are drawn again, so the
  // form keeps its focus while typing
  async _preview() {
    const e = this._edit;
    const seq = (this._seq = (this._seq || 0) + 1);
    let r;
    try {
      r = await ask(this._hass, { type: "noxcast/part_preview", ...this._which(e), input: e.input });
    } catch (err) { return; }
    if (seq !== this._seq || this._edit !== e) return;
    e.diagram = r.diagram;
    this._showErrors(r.errors);
    const flow = this.shadowRoot.querySelector(`.flow[data-entry="${CSS.escape(e.entryId)}"]`);
    if (flow) flow.innerHTML = this._selStyle() + r.diagram;
  }

  _showErrors(errors) {
    const e = this._edit;
    const { base, ...fields } = errors || {};
    e.errors = fields;
    e.base = base ? (base === "changed" ? this._t.changed : e.form.errors[base] || base) : null;
    this._form.error = fields;
    const msg = this.shadowRoot.querySelector(".edit .error");
    if (msg) msg.textContent = e.base || "";
  }

  async _savePart() {
    const e = this._edit;
    if (!e || e.saving) return;
    e.saving = true;
    this._render();
    let r;
    try {
      r = await ask(this._hass, { type: "noxcast/part_save", ...this._which(e), input: e.input,
        revision: e.form.revision });
    } catch (err) {
      e.saving = false;
      e.base = errorText(err, this._hass.language);
      this._render();
      return;
    }
    e.saving = false;
    if (r.errors) {
      if (r.errors.base === "changed") { this._stopEdit(); this._editError = this._t.changed; this._load(); return; }
      this._render();
      this._showErrors(r.errors);
      return;
    }
    this._merge(r);
    if (!e.part) this._sel = null;              // the new part's id comes with the reload; nothing chosen meanwhile
    this._stopEdit();
  }

  _stopEdit() {
    clearTimeout(this._previewTimer);
    this._edit = null;
    this._form = null;
    this._removal = null;
    this._render();
  }

  // what removing a part takes with it, shown with the diagram without it before anything is removed
  async _askRemove(entryId, part) {
    this._editError = null;
    try {
      const r = await ask(this._hass, { type: "noxcast/part_remove", entry_id: entryId, part, language: this._lang });
      this._removal = { entryId, ...r, busy: false };
    } catch (err) { this._editError = errorText(err, this._hass.language); }
    this._render();
  }

  async _remove() {
    const r = this._removal;
    if (!r || r.busy) return;
    r.busy = true;
    this._render();
    let data;
    try {
      data = await ask(this._hass, { type: "noxcast/part_remove", entry_id: r.entryId, part: r.part, confirm: true,
        revision: r.revision, language: this._lang });
    } catch (err) {
      r.busy = false;
      r.error = errorText(err, this._hass.language);
      this._render();
      return;
    }
    if (data.errors) { this._stopEdit(); this._editError = this._t.changed; this._load(); return; }
    this._merge(data);
    this._sel = null;
    this._stopEdit();
  }

  // an answer holds the entry it changed only: keep the others
  _merge(data) {
    if (!data || data.v !== WS_SCHEMA || !this._data) return;
    const fresh = Object.fromEntries(data.entries.map((x) => [x.entry_id, x]));
    this._data = { ...this._data, entries: this._data.entries.map((x) => fresh[x.entry_id] || x) };
  }

  _selStyle() {
    return this._sel ? `<style>[data-node="${CSS.escape(this._sel)}"] > rect { stroke: var(--primary-color);
      stroke-width: 2.4; }</style>` : "";
  }

  // the form in place of the settings while a part is changed
  _editor(e) {
    const t = this._t, ed = this._edit;
    const pick = ed.form.templates ? `<p class="help">${t.pickTemplate}</p><div class="templates">
      <style>.templates [data-template="${CSS.escape(ed.input.template || "")}"] > rect:first-child {
        stroke: var(--primary-color); stroke-width: 2.4; }</style>${ed.form.templates}</div>` : "";
    return `<div class="edit">${pick}<div class="slot"></div>
      <div class="error small">${esc(ed.base || "")}</div>
      <p class="help">${ed.part ? t.previewHelp : t.addHelp}</p>
      <div class="row"><button class="chip primary" data-act="save-part" ${ed.saving ? "disabled" : ""}>${
        ed.saving ? t.saving : t.saveChange}</button><button class="chip" data-act="cancel">${t.cancel}</button></div></div>`;
  }

  // a select for where a part belongs, only where it has somewhere else to go and the user may change it
  _belongs(e, part, label) {
    const m = (e.moves || {})[part];
    const user = this._hass.user;
    if (!m || (user && !user.is_admin)) return "";
    if (!m.to.some(([id]) => id !== m.now)) return "";
    const t = this._t;
    const chosen = (this._pending || {})[part] ?? m.now;
    const opts = m.to.map(([id, name]) => `<option value="${esc(id)}" ${id === chosen ? "selected" : ""}>${
      id ? esc(name) : t.alone}</option>`).join("");
    return `<label class="move"><span class="name">${label}</span>
      <select data-move="${esc(part)}" data-now="${esc(m.now)}">${opts}</select></label>`;
  }

  _w(v) { return `${Number(v).toLocaleString(this._lang, { maximumFractionDigits: 0 })} W`; }

  _value(setting, v) {
    const t = this._t.values;
    if (v === true) return t.yes;
    if (v === false) return t.no;
    if (Array.isArray(v)) return v.map((x) => this._w(x)).join(", ");
    if (["output_limit", "fixed_power"].includes(setting)) return this._w(v);
    if (setting === "charge_learning") return v == null ? t.chargeMissing : this._w(v);
    if (setting === "held_learning" && v == null) return t.gridMissing;
    if (setting === "limit_learning") return v == null ? t.gridMissing : this._w(v);
    if (["feed_in", "feed_in_rule"].includes(setting) && typeof v === "number") return `≤ ${this._w(v)}`;
    if (v && typeof v === "object" && "pct" in v)
      return `≤ ${Number(v.pct).toLocaleString(this._lang)} %${v.w ? ` (${this._w(v.w)})` : ""}`;
    return t[v] || esc(String(v));
  }

  // the settings of one node, each with how far it counts
  _settings(node) {
    const t = this._t;
    const rows = this._effects.filter((e) => e.node === node);
    if (!rows.length) return "";
    return `<ul class="settings">${rows.map((e) => `<li><span class="name">${t.settings[e.setting] || esc(e.setting)}</span>
      <span class="val">${this._value(e.setting, e.value)}</span>${e.inherited ? `<span class="name">(${e.inherited === "house" ? t.fromHouse : t.inherited})</span>` : ""}
      <span class="st ${e.status}" title="${t.status[e.status]}"><span class="dot"></span>${t.status[e.status]}</span></li>`).join("")}</ul>`;
  }

  // the node clicked in the diagram: house, a system (with subsystems), its battery, or a PV array
  _find(e, id) {
    const t = this._t;
    if (id === "house") return { title: t.house, sub: "", ids: ["house"] };
    const walk = (list, parent) => {
      for (const s of list) {
        if (s.id === id) {
          const sub = [parent ? `${t.sub} · ${parent.name}` : "", t.templates[s.template] || "",
            s.battery ? `${t.battery}: ${s.battery.name}` : ""].filter(Boolean).join(" · ");
          return { title: s.name, sub, ids: [s.id], moves: [[s.id, t.belongs], ...(s.battery ? [[s.battery.id, t.batteryBelongs]] : [])] };
        }
        if (s.battery && s.battery.id === id) {
          return { title: s.battery.name, sub: `${t.battery} · ${s.name}`, ids: [id], moves: [[id, t.batteryBelongs]] };
        }
        const a = s.arrays.find((x) => x.id === id);
        if (a) return { title: a.name, sub: `${t.array} · ${s.name} · ${a.sensors === 1 ? t.sensor : t.sensors.replace("{n}", a.sensors)}`, ids: [a.id], moves: [[a.id, t.belongs]] };
        const found = walk(s.children, s);
        if (found) return found;
      }
      return null;
    };
    return walk(e.systems, null);
  }

  _detail(e, sel) {
    const t = this._t;
    const ed = this._edit && this._edit.entryId === e.entry_id ? this._edit : null;
    if (ed && !ed.part) {
      const kind = ed.form.kind === "system" && ed.form.to ? "sub" : ed.form.kind;
      return `<div class="head"><span class="title">${t.added[kind] || ""}</span></div>${this._editor(e)}`;
    }
    const rm = this._removal && this._removal.entryId === e.entry_id ? this._removal : null;
    if (rm) {
      return `<div class="head"><span class="title">${esc(t.removeAsk.replace("{name}", rm.name))}</span></div>
        ${rm.inside.length ? `<p>${esc(t.removeWith.replace("{list}", rm.inside.join(", ")))}</p>` : ""}
        <p class="help">${t.removeWarn}</p>
        ${rm.error ? `<div class="error small">${esc(rm.error)}</div>` : ""}
        <div class="row actions"><button class="chip danger" data-act="remove-yes" ${rm.busy ? "disabled" : ""}>${
          rm.busy ? t.removing : t.removeYes}</button><button class="chip" data-act="cancel">${t.cancel}</button></div>`;
    }
    const node = sel && this._find(e, sel);
    if (!node) return `<div class="muted small">${t.pick}</div>`;
    const rows = node.ids.map((id) => this._settings(id)).join("");
    const admin = this._admin;
    if (ed) return `<div class="head"><span class="title">${esc(node.title)}</span></div>${this._editor(e)}`;
    const moves = (node.moves || []).map(([id, label]) => this._belongs(e, id, label)).join("");
    const change = admin ? `<button class="chip" data-edit="${esc(node.ids[0])}" data-entry="${esc(e.entry_id)}">${
      node.ids[0] === "house" ? this._t.changeHouse : this._t.change}</button>${node.ids[0] === "house" ? ""
      : `<button class="chip" data-remove="${esc(node.ids[0])}" data-entry="${esc(e.entry_id)}">${t.remove}</button>`}` : "";
    return `<div class="head"><span class="title">${esc(node.title)}</span>${change}</div>
      ${this._editError ? `<div class="error small">${esc(this._editError)}</div>` : ""}
      ${node.sub ? `<div class="muted small">${esc(node.sub)}</div>` : ""}
      ${rows || `<div class="muted small" style="margin-top:6px">${t.noSettings}</div>`}
      ${moves ? `<div class="moves">${moves}
        <div class="row"><button class="chip" data-save="${esc(e.entry_id)}" ${this._saving ? "disabled" : ""}>${
          this._saving ? t.saving : t.save}</button><span class="muted small">${t.moveHelp}</span></div>
        ${this._moveError ? `<div class="error small">${esc(this._moveError)}</div>` : ""}</div>` : ""}`;
  }

  _render() {
    const t = this._t;
    let body;
    if (this._error) body = `<section class="card pad">${esc(this._error)}</section>`;
    else if (!this._data) body = `<div class="pad muted">…</div>`;
    else body = this._data.entries.map((e) => {
      this._effects = e.effects || [];
      const editing = this._edit && this._edit.entryId === e.entry_id;
      const removing = this._removal && this._removal.entryId === e.entry_id;
      const diagram = (removing && this._removal.diagram) || (editing && this._edit.diagram) || e.diagram || "";
      // the "+" boxes only for admins, and not while a part is changed or removed
      const handles = this._admin && !this._edit && !this._removal ? "" : "<style>[data-add] { display: none; }</style>";
      return `<section class="card pad">
        ${e.systems.length || this._admin ? `<div class="flow" data-entry="${esc(e.entry_id)}">${this._selStyle()}${handles}${diagram}</div>`
          : `<div class="muted">${t.noSystems}</div>`}
        <div class="detail">${this._detail(e, e.systems.length ? this._sel : "house")}</div>
        <p class="help">${t.help} ${t.edit}</p>
        <button class="chip" data-entries="1">${t.openEntry}</button></section>`;
    }).join("");
    this.shadowRoot.innerHTML = `${this._style()}<div class="stack">
      <div class="bar"><button class="chip" data-refresh="1">${t.refresh}</button></div>${body}</div>`;
    // the same form element each time, so that what is typed and the focus stay
    const slot = this.shadowRoot.querySelector(".edit .slot");
    if (slot && this._form) { slot.appendChild(this._form); this._form.error = this._edit.errors; }
  }

  _style() {
    const d = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    return `<style>
      :host { display: block; --nx-good: ${d ? "#4CC77A" : "#2E9E5B"}; --nx-warn: ${d ? "#E0A43C" : "#C98A12"};
        --nx-off: ${d ? "#8A8F98" : "#8A8F98"}; }
      .stack { display: flex; flex-direction: column; gap: 16px; }
      .bar { display: flex; justify-content: flex-end; }
      .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); }
      .pad { padding: 16px; }
      .muted { color: var(--secondary-text-color); } .small { font-size: 12px; }
      .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
      .head ha-icon { --mdc-icon-size: 20px; align-self: center; }
      .title { font-size: 15px; font-weight: 500; }
      .flow { overflow-x: auto; --nx-text: var(--primary-text-color); --nx-muted: var(--secondary-text-color);
        --nx-line: var(--secondary-text-color); --nx-node: var(--secondary-background-color, var(--card-background-color));
        --nx-frame: var(--secondary-text-color); --nx-font: var(--ha-font-family-body, var(--paper-font-body1_-_font-family));
        --nx-card: var(--card-background-color);
        ${d ? "--nx-pv: #3d2f16; --nx-pv-line: #e0a43c; --nx-sys: #15362c; --nx-sys-line: #4cc7a0; --nx-limit: #43231a; --nx-limit-line: #e88a68; --nx-bat: #1a2747; --nx-bat-line: #7d9ae6;"
            : "--nx-pv: #fbf1df; --nx-pv-line: #c98a12; --nx-sys: #e3f4ee; --nx-sys-line: #2e9e7b; --nx-limit: #fae8e2; --nx-limit-line: #d0603a; --nx-bat: #e8eefb; --nx-bat-line: #4b6fc4;"} }
      .flow svg { display: block; width: 100%; max-width: 862px; height: auto; }
      .detail { border-top: 1px solid var(--divider-color); margin-top: 12px; padding-top: 12px; }
      .settings { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
      .settings li { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
      .settings .name { color: var(--secondary-text-color); }
      .settings .val { font-weight: 500; }
      .st { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--secondary-text-color); }
      .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: var(--nx-off); }
      .st.exact .dot { background: var(--nx-good); } .st.simplified .dot { background: var(--nx-warn); }
      .moves { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; font-size: 13px; }
      .move { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; }
      .move .name { color: var(--secondary-text-color); }
      .move select { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
        background: var(--card-background-color); color: var(--primary-text-color); max-width: 100%; }
      .moves .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
      .error { color: var(--error-color, #db4437); }
      .chip:disabled { opacity: .6; cursor: default; }
      .chip.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
      .head .chip:first-of-type { margin-left: auto; }
      .chip.danger { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: #fff; }
      .row.actions { display: flex; gap: 8px; margin-top: 8px; }
      .templates svg { display: block; width: 100%; max-width: 680px; height: auto; }
      .edit { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; max-width: 560px; }
      .edit .row { display: flex; gap: 8px; }
      .help { color: var(--secondary-text-color); font-size: 12px; margin: 12px 0 0; line-height: 1.45; }
      .chip { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); border-radius: 16px;
        padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
    </style>`;
  }
}

if (!customElements.get("noxcast-installation")) customElements.define("noxcast-installation", NoxcastInstallation);
