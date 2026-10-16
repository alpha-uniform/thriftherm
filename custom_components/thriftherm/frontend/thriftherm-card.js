/*
 * Thriftherm card for Home Assistant dashboards.
 *
 * Served by the Thriftherm integration and loaded on every dashboard; add it as
 * "custom:thriftherm-card". It finds the rooms itself from Thriftherm's entities and
 * shows the moment: which heat source runs and why, what needs attention, one row per
 * room, the operating mode. It only shows and presses what Thriftherm already offers.
 *
 * One file, no build step, nothing loaded from the network. The wording comes from the
 * integration (texts.py) over the websocket; codes Home Assistant translates itself are
 * taken from Home Assistant. Icons: Material Design Icons (Pictogrammers, Apache 2.0).
 */

const ICONS = {
  fire: "M17.66 11.2C17.43 10.9 17.15 10.64 16.89 10.38C16.22 9.78 15.46 9.35 14.82 8.72C13.33 7.26 13 4.85 13.95 3C13 3.23 12.17 3.75 11.46 4.32C8.87 6.4 7.85 10.07 9.07 13.22C9.11 13.32 9.15 13.42 9.15 13.55C9.15 13.77 9 13.97 8.8 14.05C8.57 14.15 8.33 14.09 8.14 13.93C8.08 13.88 8.04 13.83 8 13.76C6.87 12.33 6.69 10.28 7.45 8.64C5.78 10 4.87 12.3 5 14.47C5.06 14.97 5.12 15.47 5.29 15.97C5.43 16.57 5.7 17.17 6 17.7C7.08 19.43 8.95 20.67 10.96 20.92C13.1 21.19 15.39 20.8 17.03 19.32C18.86 17.66 19.5 15 18.56 12.72L18.43 12.46C18.22 12 17.66 11.2 17.66 11.2M14.5 17.5C14.22 17.74 13.76 18 13.4 18.1C12.28 18.5 11.16 17.94 10.5 17.28C11.69 17 12.4 16.12 12.61 15.23C12.78 14.43 12.46 13.77 12.33 13C12.21 12.26 12.23 11.63 12.5 10.94C12.69 11.32 12.89 11.7 13.13 12C13.9 13 15.11 13.44 15.37 14.8C15.41 14.94 15.43 15.08 15.43 15.23C15.46 16.05 15.1 16.95 14.5 17.5H14.5Z",
  heatPump: "M19 3H5C3.9 3 3 3.9 3 5V19C3 20.1 3.9 21 5 21H19C20.1 21 21 20.1 21 19V5C21 3.9 20.1 3 19 3M12.75 7.08C13.57 7.2 14.32 7.5 14.95 8L12.75 10.19V7.08M11.25 7.08V10.19L9.05 8C9.68 7.5 10.43 7.2 11.25 7.08M8 9.05L10.19 11.25H7.08C7.2 10.43 7.5 9.68 8 9.05M7.08 12.75H10.19L8 14.95C7.5 14.32 7.2 13.57 7.08 12.75M11.25 16.92C10.43 16.8 9.68 16.5 9.05 16L11.25 13.81V16.92M12 13C11.45 13 11 12.55 11 12S11.45 11 12 11 13 11.45 13 12 12.55 13 12 13M12.75 16.92V13.81L14.95 16C14.32 16.5 13.57 16.8 12.75 16.92M16 14.95L13.81 12.75H16.92C16.8 13.57 16.5 14.32 16 14.95M13.81 11.25L16 9.05C16.5 9.69 16.8 10.44 16.92 11.25H13.81Z",
  boiler: "M8 2C6.89 2 6 2.89 6 4V16C6 17.11 6.89 18 8 18H9V20H6V22H9C10.11 22 11 21.11 11 20V18H13V20C13 21.11 13.89 22 15 22H18V20H15V18H16C17.11 18 18 17.11 18 16V4C18 2.89 17.11 2 16 2H8M12 4.97A2 2 0 0 1 14 6.97A2 2 0 0 1 12 8.97A2 2 0 0 1 10 6.97A2 2 0 0 1 12 4.97M10 14.5H14V16H10V14.5Z",
  hand: "M13 24C9.74 24 6.81 22 5.6 19L2.57 11.37C2.26 10.58 3 9.79 3.81 10.05L4.6 10.31C5.16 10.5 5.62 10.92 5.84 11.47L7.25 15H8V3.25C8 2.56 8.56 2 9.25 2S10.5 2.56 10.5 3.25V12H11.5V1.25C11.5 .56 12.06 0 12.75 0S14 .56 14 1.25V12H15V2.75C15 2.06 15.56 1.5 16.25 1.5C16.94 1.5 17.5 2.06 17.5 2.75V12H18.5V5.75C18.5 5.06 19.06 4.5 19.75 4.5S21 5.06 21 5.75V16C21 20.42 17.42 24 13 24Z",
  window: "M21 20V2H3V20H1V23H23V20M19 4V11H17V4M5 4H7V11H5M5 20V13H7V20M9 20V4H15V20M17 20V13H19V20Z",
  shield: "M12,1L3,5V11C3,16.55 6.84,21.74 12,23C17.16,21.74 21,16.55 21,11V5M11,7H13V13H11M11,15H13V17H11",
  lock: "M12,17A2,2 0 0,0 14,15C14,13.89 13.1,13 12,13A2,2 0 0,0 10,15A2,2 0 0,0 12,17M18,8A2,2 0 0,1 20,10V20A2,2 0 0,1 18,22H6A2,2 0 0,1 4,20V10C4,8.89 4.9,8 6,8H7V6A5,5 0 0,1 12,1A5,5 0 0,1 17,6V8H18M12,3A3,3 0 0,0 9,6V8H15V6A3,3 0 0,0 12,3Z",
  chevron: "M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z",
  minus: "M19,13H5V11H19V13Z",
  plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  restore: "M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3Z",
  alert: "M11,15H13V17H11V15M11,7H13V13H11V7M12,2C6.47,2 2,6.5 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20Z",
  info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
  up: "M13,20H11V8L5.5,13.5L4.08,12.08L12,4.16L19.92,12.08L18.5,13.5L13,8V20Z",
  down: "M11,4H13V16L18.5,10.5L19.92,11.92L12,19.84L4.08,11.92L5.5,10.5L11,16V4Z",
  calendar: "M19,19H5V8H19M16,1V3H8V1H6V3H5C3.89,3 3,3.89 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5C21,3.89 20.1,3 19,3H18V1M17,12H12V17H17V12Z",
  fan: "M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.88,14.34C15.87,18.03 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.96,15.85 2,15.07 2,11.5C2,7 5.56,6.89 6.74,9.26C7.24,10.25 8.29,10.68 9.22,10.87C9.41,10.39 9.73,9.97 10.14,9.65C8.15,5.96 8.94,2 12.5,2Z",
};

const DOMAIN = "thriftherm";
// what the integration names a room's entities and the system's (translation keys)
const ROOM_KEYS = ["room_target", "room_demand", "room_window_open", "room_heating_allowed", "room_boost",
  "room_drying", "room_comfort_temp", "room_setback_temp", "room_thermostat_plan"];
// what a tapped room can offer; the card's option hidden_controls leaves any of them out
const CONTROLS = ["now", "comfort", "setback", "quick_heat_up", "dry_now", "schedule"];
const OVERRIDE_MIN = 5; // the range of the set_override action
const OVERRIDE_MAX = 30;
// decision reasons that only describe normal operation; the others are worth a line
const ORDINARY_REASONS = new Set(["hot_water_priority", "mode_off", "mode_boiler_only", "mode_heat_pump_only",
  "cop_above_break_even", "heat_pump_cheaper", "cop_below_break_even", "room_below_target", "mode_away", "no_demand",
  "heat_pump_runs_through", "heat_pump_plan_only"]);
// only for a card that cannot reach the integration at all
const RETRY_MS = 30000; // after a failed request for the wording
const FALLBACK = {
  de: "Thriftherm ist nicht eingerichtet, oder seine Entitäten sind deaktiviert.",
  en: "Thriftherm is not set up, or its entities are disabled.",
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;
const fill = (template, values) => String(template ?? "").replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
const sentence = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : "");
const num = (value) => (value === null || value === undefined || value === "" || Number.isNaN(Number(value)) ? null : Number(value));

const formatters = new Map();
function formatter(Kind, locale, options) {
  const key = `${Kind.name}|${locale}|${JSON.stringify(options)}`;
  if (!formatters.has(key)) formatters.set(key, new Kind(locale, options));
  return formatters.get(key);
}

// ------------------------------------------------------------------ wording from the integration
const textCache = new Map();
function loadTexts(hass) {
  const language = hass.language || "en";
  if (!textCache.has(language)) {
    const promise = hass.callWS({ type: `${DOMAIN}/card_texts`, language }).catch((err) => {
      textCache.delete(language); // try again with the next update
      throw err;
    });
    textCache.set(language, promise);
  }
  return textCache.get(language);
}

// ------------------------------------------------------------------ finding Thriftherm's entities
function discover(hass) {
  const system = {};
  const rooms = [];
  const byDevice = new Map();
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.platform !== DOMAIN || !entry.translation_key) continue;
    if (ROOM_KEYS.includes(entry.translation_key)) {
      if (!entry.device_id) continue;
      if (!byDevice.has(entry.device_id)) byDevice.set(entry.device_id, {});
      byDevice.get(entry.device_id)[entry.translation_key] = entry.entity_id;
    } else if (!entry.translation_key.startsWith("room_") || entry.translation_key === "room_control") {
      system[entry.translation_key] = entry.entity_id;
    }
  }
  let complete = true;
  for (const [deviceId, ids] of byDevice) {
    const target = ids.room_target && hass.states[ids.room_target];
    // restored after a restart, before Thriftherm runs: no room key yet, look again with the next update
    if (!target || !target.attributes.room_key) {
      complete = false;
      continue;
    }
    const device = hass.devices?.[deviceId];
    const deviceName = device?.name_by_user || device?.name || "";
    const name = deviceName.replace(/^Thriftherm\s+/i, "") || target.attributes.friendly_name || ids.room_target;
    const key = target.attributes.room_key;
    const entities = {};
    for (const k of ROOM_KEYS) if (ids[k]) entities[k.replace(/^room_/, "")] = ids[k];
    rooms.push({ key, name, ids: entities });
  }
  return { system, rooms, complete };
}

function orderedRooms(rooms, config) {
  const order = config.room_order || [];
  const position = (room) => {
    const index = order.indexOf(room.key);
    return index === -1 ? order.length + rooms.indexOf(room) : index;
  };
  return [...rooms].sort((a, b) => position(a) - position(b));
}

// ------------------------------------------------------------------ the card
class ThrifthermCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._open = new Set(); // rooms tapped open
    this._compare = false; // the comparison of the heat sources tapped open
    this._pending = {}; // numbers stepped but not yet confirmed by Home Assistant
    this._pendingSeen = {}; // the state each one was stepped from
    this._overrideRooms = {}; // a stepped room target: its entity -> the room, sent as a change by hand
    this._signature = "";
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
    this.shadowRoot.addEventListener("focusout", (ev) => this._onLeave(ev));
  }

  static getConfigElement() {
    return document.createElement("thriftherm-card-editor");
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._signature = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._texts && !this._loading && !(this._failedAt > Date.now() - RETRY_MS)) {
      this._loading = true;
      loadTexts(hass)
        .then((t) => { this._texts = t; this._failedAt = 0; })
        .catch(() => { this._failedAt = Date.now(); })
        .finally(() => { this._loading = false; this._signature = ""; this._render(); });
    }
    if (this._texts && this._texts.language !== (hass.language?.toLowerCase().startsWith("de") ? "de" : "en")) {
      this._texts = null; // the person switched the language
      this.hass = hass;
      return;
    }
    this._render();
  }

  getCardSize() {
    return 3 + (this._model?.rooms.length || 3);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  // ---------------------------------------------------------------- helpers bound to hass
  // an entity Home Assistant cannot reach counts as missing: the card never shows "unavailable"
  _s(entityId) {
    const s = entityId ? this._hass.states[entityId] : undefined;
    return s && s.state !== "unavailable" ? s : undefined;
  }

  _sys(key) {
    return this._s(this._model.system[key]);
  }

  _t(key, values) {
    return fill(this._texts.card[key] ?? key, values || {});
  }

  _label(code) {
    if (code === null || code === undefined) return "";
    return this._texts.labels[code] || String(code).replace(/_/g, " ");
  }

  _state(stateObj) {
    return stateObj && stateObj.state !== "unknown" ? this._hass.formatEntityState(stateObj) : "";
  }

  _attr(stateObj, attribute) {
    const value = stateObj?.attributes?.[attribute];
    if (value === null || value === undefined || value === "") return "";
    const text = this._hass.formatEntityAttributeValue(stateObj, attribute);
    return text === value ? this._label(value) : text; // a code Home Assistant has no translation for
  }

  _locale() {
    const locale = this._hass.locale || {};
    switch (locale.number_format) {
      case "decimal_comma": return "de";
      case "comma_decimal": return "en-US";
      case "space_comma": return "fr";
      case "system": return undefined;
      default: return locale.language || this._hass.language;
    }
  }

  _num(value, digits = 1) {
    return formatter(Intl.NumberFormat, this._locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  }

  _temp(value) {
    return value === null ? "" : `${this._num(value, 1)} °C`;
  }

  _timeZone() {
    return this._hass.locale?.time_zone === "server" ? this._hass.config?.time_zone : undefined;
  }

  _time(tsSeconds) {
    const date = new Date(tsSeconds * 1000);
    const lang = this._hass.locale?.language || this._hass.language;
    const timeZone = this._timeZone();
    const day = formatter(Intl.DateTimeFormat, "en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
    const options = { hour: "2-digit", minute: "2-digit", timeZone };
    if (day.format(date) !== day.format(new Date())) options.weekday = "short";
    return formatter(Intl.DateTimeFormat, lang, options).format(date);
  }

  // ---------------------------------------------------------------- rendering
  _render() {
    if (!this._hass) return;
    if (this._failedAt && !this._texts) {
      // with Thriftherm's entities there, a failed request is passing (a restart): wait for the next try
      const installed = Object.values(this._hass.entities || {}).some((e) => e.platform === DOMAIN);
      if (!installed) {
        const lang = this._hass.language?.toLowerCase().startsWith("de") ? "de" : "en";
        this._paint(`<div class="empty">${esc(FALLBACK[lang])}</div>`);
      }
      return;
    }
    if (!this._texts) return;
    if (this._entities !== this._hass.entities || this._devices !== this._hass.devices || !this._model?.complete) {
      this._entities = this._hass.entities;
      this._devices = this._hass.devices;
      this._model = discover(this._hass);
      const ids = this._watchedSystem();
      for (const room of this._watchedRooms()) ids.push(...Object.values(room.ids));
      this._watched = ids;
    }
    // a stepped number is shown as stepped until Home Assistant reports it (or reports something else)
    for (const [id, v] of Object.entries(this._pending)) {
      const s = this._hass.states[id];
      if (!s || (num(s.state) === v) || (this._pendingSeen[id] && this._pendingSeen[id] !== s.last_updated)) {
        delete this._pending[id];
        delete this._pendingSeen[id];
        delete this._overrideRooms[id];
      }
    }
    const sources = this._watchedRooms().map((r) => this._hass.states[r.ids.target]?.attributes.temperature_entity).filter(Boolean);
    const signature = [
      JSON.stringify(this._config), [...this._open].join(","), this._compare, JSON.stringify(this._pending),
      this._hass.language, ...[...this._watched, ...sources].map((id) => { const s = this._hass.states[id]; return s ? s.last_updated + s.state : "-"; }),
    ].join("|");
    if (signature === this._signature) return;
    if (this._editing()) {
      this._deferred = true; // repainted when the field is left
      return;
    }
    this._signature = signature;

    const m = this._model;
    if (!m.rooms.length && !Object.keys(m.system).length) {
      this._paint(`<div class="empty">${esc(this._t("not_found"))}</div>`);
      return;
    }
    this._paint(this._paintAll());
  }

  _paintAll() {
    const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
    return title + this._sourceBlock() + this._warnings() + this._roomsBlock() + this._modeBlock();
  }

  // the entities whose changes repaint the card
  _watchedSystem() {
    return [...Object.values(this._model.system)];
  }

  _watchedRooms() {
    return this._model.rooms;
  }

  // a dropdown or field in use: repainting would close it or drop what is being typed
  _editing() {
    const active = this.shadowRoot.activeElement;
    return !!active && (active.tagName === "SELECT" || active.tagName === "INPUT");
  }

  _paint(body) {
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="card">${body}</div></ha-card>`;
  }

  // 1. heat source in a sentence, the comparison on a tap
  _sourceBlock() {
    const hp = this._sys("midea_command");
    const boiler = this._sys("boiler_command");
    if (!hp && !boiler) return ""; // neither a heat pump nor a boiler to tell about
    const hpHeats = hp?.state === "heat";
    const boilerHeats = boiler?.state === "heat";
    let line;
    if (hpHeats && boilerHeats) line = this._t("both_heat");
    else if (hpHeats) line = this._t("heat_pump_heats");
    else if (boilerHeats) line = this._t("boiler_heats");
    else line = this._t("nothing_heats");

    let sub = "";
    const hpCost = num(this._sys("midea_cost_per_kwh_thermal")?.state);
    const centralCost = num(this._sys("gas_cost_per_kwh_thermal")?.state);
    if (hp && hpCost !== null && centralCost !== null) {
      sub = this._t("cost_line", {
        heat_pump: this._num(hpCost * 100), central: this._num(centralCost * 100),
        central_name: this._t(boiler ? "gas" : "district"),
      });
    } else if (hp && hpCost !== null) {
      sub = this._t("cost_per_kwh", { value: this._num(hpCost * 100) });
    } else if (!hp && boiler) {
      sub = sentence(this._attr(boiler, "reason")); // without a heat pump: why the boiler does what it does
    }
    const sourceIcon = hpHeats ? icon("heatPump", "hp") : boilerHeats ? icon("fire", "flame") : icon(hp ? "heatPump" : "boiler", "muted");
    return `
      <button class="source" data-action="compare" aria-expanded="${this._compare}">
        ${sourceIcon}
        <span class="source-text"><span class="headline">${esc(line)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</span>
        ${icon("chevron", `chevron${this._compare ? " open" : ""}`)}
      </button>
      ${this._compare ? this._comparison() : ""}`;
  }

  _comparison() {
    const hp = this._sys("midea_command");
    const boiler = this._sys("boiler_command");
    const columns = [];
    if (boiler) {
      const flow = num(this._sys("boiler_flow_setpoint")?.state);
      const cost = num(this._sys("gas_cost_per_kwh_thermal")?.state);
      const blockers = (boiler.attributes.blockers || []).map((b) => this._label(b));
      columns.push(this._column("boiler", this._t("boiler"), boiler, [
        flow !== null ? this._t("flow_setpoint", { value: this._temp(flow) }) : "",
        cost !== null ? this._t("cost_per_kwh", { value: this._num(cost * 100) }) : "",
      ], blockers.length ? this._t("blocked", { reason: blockers.join(", ") }) : ""));
    }
    if (hp) {
      const used = num(hp.attributes.decision_cop);
      const measured = num(this._sys("midea_cop")?.state);
      const cost = num(this._sys("midea_cost_per_kwh_thermal")?.state);
      let cop = used !== null ? this._t("cop_used", { value: this._num(used, 2) }) : "";
      if (measured !== null) cop += `${cop ? ", " : ""}${this._t("cop_measured", { value: this._num(measured, 2) })}`;
      const blocked = this._sys("midea_blocked");
      const blockers = (hp.attributes.blockers || []).map((b) => this._label(b));
      let lock = "";
      if (blocked?.state === "on") lock = this._t("blocked", { reason: this._attr(blocked, "reason") });
      else if (blockers.length) lock = this._t("blocked", { reason: blockers.join(", ") });
      columns.push(this._column("heatPump", this._t("heat_pump"), hp, [
        cop, cost !== null ? this._t("cost_per_kwh", { value: this._num(cost * 100) }) : "",
      ], lock));
    }
    if (!boiler && hp) {
      const cost = num(this._sys("gas_cost_per_kwh_thermal")?.state);
      if (cost !== null) {
        columns.push(`<div class="col"><div class="col-head">${icon("boiler")}<span>${esc(this._t("district_heating"))}</span></div>
          <div class="fact">${esc(this._t("cost_per_kwh", { value: this._num(cost * 100) }))}</div></div>`);
      }
    }
    const footer = [];
    const breakEven = num(this._sys("break_even_cop")?.state);
    if (hp && breakEven !== null) footer.push(this._t("break_even", { value: this._num(breakEven, 2) }));
    const learning = this._sys("learning_state");
    if (boiler && learning) footer.push(this._t("learning", { state: this._learning(learning) }));
    return `<div class="compare">
      <div class="compare-title">${esc(this._t("compare_title"))}</div>
      <div class="cols">${columns.join("")}</div>
      ${footer.length ? `<div class="compare-foot">${footer.map(esc).join(" · ")}</div>` : ""}
    </div>`;
  }

  _column(iconName, name, command, facts, lock) {
    // "blocked" says nothing the lock line below does not say better
    const why = lock && command.attributes.reason === "blocked" ? "" : this._attr(command, "reason");
    return `<div class="col">
      <div class="col-head">${icon(iconName)}<span>${esc(name)}</span></div>
      <div class="does">${esc(this._state(command))}</div>
      ${why ? `<div class="why">${esc(why)}</div>` : ""}
      ${facts.filter(Boolean).map((f) => `<div class="fact">${esc(f)}</div>`).join("")}
      ${lock ? `<div class="lock">${icon("lock")}<span>${esc(lock)}</span></div>` : ""}
    </div>`;
  }

  _learning(learning) {
    const state = this._state(learning);
    const codes = learning.attributes.paused_because || [];
    if (!codes.length) return state;
    const clauses = this._texts.paused_because;
    const why = [...new Set(codes.map((c) => (c.startsWith("safety_") ? clauses.safety : clauses[c] || this._label(c))))];
    return this._t("because", { state, why: why.join(this._t("and")) });
  }

  // 2. warnings, only when there are some
  _warnings() {
    const out = [];
    const names = Object.fromEntries(this._model.rooms.map((r) => [r.key, r.name]));
    const roomName = (key) => names[key] || key;
    const covered = new Set(["room_window_open"]);

    const safety = this._sys("safety_state");
    if (safety && safety.state !== "ok" && safety.state !== "unavailable" && safety.state !== "unknown") {
      const issues = [...new Set((safety.attributes.issues || []).map((issue) => {
        const [code, detail] = String(issue).split(":");
        const template = this._texts.safety_issues[code];
        return template ? fill(template, { room: roomName(detail), code: detail }) : "";
      }).filter(Boolean))];
      const frost = (safety.attributes.frost_rooms || []).map(roomName);
      const parts = [...issues];
      if (frost.length) parts.push(this._t("frost", { rooms: frost.join(", ") }));
      out.push(this._warning("error", "shield", `${this._t("safety")}${parts.length ? `: ${parts.join(", ")}` : ""}`));
      covered.add("safety_fallback").add("frost_protection");
    }

    const blocked = this._sys("midea_blocked");
    if (blocked?.state === "on") {
      out.push(this._warning("warning", "lock", this._t("heat_pump_blocked", { reason: this._attr(blocked, "reason") })));
      covered.add("heat_pump_blocked").add("heat_pump_outdoor_too_cold");
    }

    const hidden = new Set(this._config.hidden_rooms || []); // a room hidden on purpose does not nag about its window
    for (const room of this._model.rooms) {
      if (!hidden.has(room.key) && this._s(room.ids.window_open)?.state === "on" && this._s(room.ids.heating_allowed)?.state === "off") {
        out.push(this._warning("info", "window", fill(this._texts.templates.room_window_open, { room: room.name })));
      }
    }

    const override = this._sys("override_active");
    const hp = this._sys("midea_command");
    const hand = [];
    if (override?.state === "on") {
      for (const [key, value] of Object.entries(override.attributes.overrides || {})) {
        const until = num(value?.until_ts);
        hand.push(until !== null ? `${roomName(key)} ${this._t("until", { time: this._time(until) })}` : roomName(key));
      }
    }
    const hpByHand = hp && (hp.attributes.reason === "manual_takeover" || hp.attributes.manual_by);
    if (hand.length || hpByHand) {
      const text = [hand.length ? this._t("override", { rooms: hand.join(", ") }) : "", hpByHand ? this._t("heat_pump_by_hand") : ""]
        .filter(Boolean).join(". ");
      const button = this._model.system.resume_automatic
        ? `<button class="btn" data-action="resume">${icon("restore")}<span>${esc(this._t("back_to_automatic"))}</span></button>` : "";
      out.push(this._warning("warning", "hand", text, button));
    }

    const decision = this._sys("decision_reason");
    const codes = decision?.attributes.reason_codes || [];
    if (codes.some((code) => !ORDINARY_REASONS.has(code) && !covered.has(code))) {
      const reasons = this._reasons(decision);
      reasons.codes.forEach((code, i) => {
        if (!ORDINARY_REASONS.has(code) && !covered.has(code) && reasons.texts[i]) {
          out.push(this._warning("warning", "alert", sentence(reasons.texts[i])));
        }
      });
    }
    return out.length ? `<div class="warnings">${out.join("")}</div>` : "";
  }

  // the attribute holds the reasons in the server's language; the card asks for the viewer's
  _reasons(decision) {
    const stamp = `${decision.last_updated}|${this._hass.language}`;
    if (this._reasonCache?.stamp === stamp) return this._reasonCache;
    if (this._reasonAsked !== stamp) {
      this._reasonAsked = stamp;
      this._hass.callWS({ type: `${DOMAIN}/decision_reasons`, language: this._hass.language })
        .then((r) => { this._reasonCache = { stamp, codes: r.codes, texts: r.texts }; this._signature = ""; this._render(); })
        .catch(() => {});
    }
    return { codes: [], texts: [] };
  }

  _warning(kind, iconName, text, extra = "") {
    return `<div class="warn ${kind}">${icon(iconName)}<span class="warn-text">${esc(text)}</span>${extra}</div>`;
  }

  // 3. one row per room
  _roomsBlock() {
    const hidden = new Set(this._config.hidden_rooms || []);
    const rooms = orderedRooms(this._model.rooms, this._config).filter((r) => !hidden.has(r.key));
    if (!rooms.length) return "";
    return `<div class="rooms">${rooms.map((r) => this._room(r)).join("")}</div>`;
  }

  _room(room) {
    const target = this._s(room.ids.target);
    if (!target) return "";
    const a = target.attributes;
    const current = this._measured(a.temperature_entity);
    const setpoint = num(target.state);
    const windowOpen = this._s(room.ids.window_open)?.state === "on";
    const allowed = this._s(room.ids.heating_allowed)?.state !== "off";
    const demandState = this._s(room.ids.demand);
    const demand = num(demandState?.state);
    const byHand = a.reason === "override" || !!a.override_by;
    const boilerHeats = this._sys("boiler_command")?.state === "heat" && demandState?.attributes.boiler_call === "calling";
    const hp = this._sys("midea_command");
    const hpServes = hp?.state === "heat" && (hp.attributes.served_rooms || []).includes(room.key);

    const badges = [
      boilerHeats ? icon("fire", "flame") : "",
      hpServes ? icon("heatPump", "hp") : "",
      byHand ? icon("hand", "hand") : "",
      windowOpen ? icon("window", "window") : "",
    ].join("");

    let reason;
    if (windowOpen) reason = this._t("window_open");
    else if (byHand) {
      const until = num(a.override_until_ts);
      reason = until !== null ? `${this._t("by_hand")}, ${this._t("until", { time: this._time(until) })}` : this._t("by_hand");
    } else if (a.quick_heat_up) reason = this._t("quick_heat_up");
    else if (!allowed && (demandState?.attributes.issues || []).includes("thermostat_silent")) reason = this._t("thermostat_silent");
    else if (!allowed && this._s(room.ids.thermostat_plan)?.attributes.reason === "thermostat_off") reason = this._label("thermostat_off");
    else if (demandState?.attributes.boiler_call === "stalled" && num(demandState.attributes.calls_again_below) !== null) {
      // reached within a few tenths and hardly rising any more: the boiler stays off until the room cools
      reason = this._t("stalled", { temp: this._temp(num(demandState.attributes.calls_again_below)) });
    } else reason = this._attr(target, "reason");

    const open = this._open.has(room.key);
    const bar = allowed && demand !== null && demand > 0
      ? `<div class="bar" role="presentation"><div style="width:${Math.min(100, Math.max(4, demand))}%"></div></div>` : "";
    const head = `<span class="room-name"><span class="name">${esc(room.name)}</span><span class="badges">${badges}</span></span>
        <span class="temps">${current !== null ? `<span class="current">${esc(this._num(current))}°</span>` : ""}${setpoint !== null ? `<span class="target">→ ${esc(this._num(setpoint))}°</span>` : ""}</span>
        <span class="reason">${esc(reason)}</span>
        ${bar}`;
    // the room card shows the room always open: its head is no button to tap
    const headBlock = this._single
      ? `<div class="room-head">${head}</div>`
      : `<button class="room-head" data-action="room" data-room="${esc(room.key)}" aria-expanded="${open}">${head}</button>`;
    return `<div class="room${allowed ? "" : " off"}${open ? " open" : ""}">
      ${headBlock}
      ${open ? this._roomDetail(room, byHand) : ""}
    </div>`;
  }

  // the room temperature where Thriftherm reads it: the room sensor, or the valve standing in for it
  _measured(entityId) {
    const s = this._s(entityId);
    if (!s) return null;
    return entityId.startsWith("climate.") ? num(s.attributes.current_temperature) : num(s.state);
  }

  _roomDetail(room, byHand) {
    const parts = [];
    const shown = (control) => !(this._config.hidden_controls || []).includes(control);
    // the target right now, changed by hand: Thriftherm keeps it for the usual time of a change by hand
    const target = this._s(room.ids.target);
    if (shown("now") && target && num(target.state) !== null) {
      const value = this._pending[target.entity_id] ?? num(target.state);
      parts.push(`<div class="stepper">
        <span class="step-label">${esc(this._t("now"))}</span>
        <button class="round" data-action="override" data-entity="${esc(target.entity_id)}" data-room="${esc(room.key)}" data-delta="-1" aria-label="−">${icon("minus")}</button>
        <span class="step-value">${esc(this._temp(value))}</span>
        <button class="round" data-action="override" data-entity="${esc(target.entity_id)}" data-room="${esc(room.key)}" data-delta="1" aria-label="+">${icon("plus")}</button>
      </div>`);
    }
    for (const kind of ["comfort_temp", "setback_temp"]) {
      if (!shown(kind === "comfort_temp" ? "comfort" : "setback")) continue;
      const s = this._s(room.ids[kind]);
      if (!s || num(s.state) === null) continue;
      const value = this._pending[s.entity_id] ?? num(s.state);
      parts.push(`<div class="stepper">
        <span class="step-label">${esc(this._t(kind === "comfort_temp" ? "comfort" : "setback"))}</span>
        <button class="round" data-action="step" data-entity="${esc(s.entity_id)}" data-delta="-1" aria-label="−">${icon("minus")}</button>
        <span class="step-value">${esc(this._temp(value))}</span>
        <button class="round" data-action="step" data-entity="${esc(s.entity_id)}" data-delta="1" aria-label="+">${icon("plus")}</button>
      </div>`);
    }
    const buttons = [];
    if (shown("quick_heat_up") && room.ids.boost && this._s(room.ids.boost)) {
      buttons.push(`<button class="btn" data-action="press" data-entity="${esc(room.ids.boost)}">${icon("fire")}<span>${esc(this._t("quick_heat_up"))}</span></button>`);
    }
    if (shown("dry_now") && room.ids.drying && this._s(room.ids.drying)) {
      buttons.push(`<button class="btn" data-action="press" data-entity="${esc(room.ids.drying)}">${icon("fan")}<span>${esc(this._t("dry_now"))}</span></button>`);
    }
    const schedule = target?.attributes.schedule_entity;
    if (shown("schedule") && schedule && this._s(schedule)) {
      buttons.push(`<button class="btn" data-action="more-info" data-entity="${esc(schedule)}">${icon("calendar")}<span>${esc(this._t("schedule"))}</span></button>`);
    }
    if (byHand) {
      buttons.push(`<button class="btn" data-action="room-resume" data-room="${esc(room.key)}">${icon("restore")}<span>${esc(this._t("back_to_automatic"))}</span></button>`);
    }
    if (buttons.length) parts.push(`<div class="buttons">${buttons.join("")}</div>`);
    return `<div class="detail">${parts.join("")}</div>`;
  }

  // 4. operating mode
  _modeBlock() {
    const mode = this._sys("mode");
    if (!mode) return "";
    const options = (mode.attributes.options || []).map((o) =>
      `<option value="${esc(o)}"${o === mode.state ? " selected" : ""}>${esc(this._hass.formatEntityState(mode, o))}</option>`).join("");
    let away = "";
    const ret = this._sys("away_return");
    if (mode.state === "away" && ret) {
      away = `<label class="mode-row"><span>${esc(this._t("return"))}</span>
        <input type="datetime-local" data-action="return" data-entity="${esc(ret.entity_id)}" data-sent="${esc(this._localInput(ret.state))}" value="${esc(this._localInput(ret.state))}"></label>`;
    }
    return `<div class="mode">
      <label class="mode-row"><span>${esc(this._t("operating_mode"))}</span>
        <select data-action="mode" data-entity="${esc(mode.entity_id)}">${options}</select></label>
      ${away}
    </div>`;
  }

  // the planned return as the date field shows it: in Home Assistant's time zone, which set_value assumes
  _localInput(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const parts = Object.fromEntries(formatter(Intl.DateTimeFormat, "en-CA", {
      timeZone: this._hass.config?.time_zone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date).map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  }

  // ---------------------------------------------------------------- actions
  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el || !this._hass) return;
    const action = el.dataset.action;
    if (action === "compare") {
      this._compare = !this._compare;
    } else if (action === "room") {
      const key = el.dataset.room;
      if (this._open.has(key)) this._open.delete(key); else this._open.add(key);
    } else if (action === "resume") {
      this._hass.callService("button", "press", {}, { entity_id: this._model.system.resume_automatic });
    } else if (action === "room-resume") {
      this._hass.callService(DOMAIN, "clear_override", { room: el.dataset.room });
    } else if (action === "press") {
      this._hass.callService("button", "press", {}, { entity_id: el.dataset.entity });
    } else if (action === "step") {
      this._step(el.dataset.entity, Number(el.dataset.delta));
    } else if (action === "override") {
      this._overrideRooms[el.dataset.entity] = el.dataset.room;
      this._step(el.dataset.entity, Number(el.dataset.delta));
    } else if (action === "more-info") {
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: el.dataset.entity }, bubbles: true, composed: true }));
      return;
    } else {
      return;
    }
    this._render();
  }

  _step(entityId, direction) {
    const s = this._s(entityId);
    if (!s) return;
    const room = this._overrideRooms[entityId];
    const step = num(s.attributes.step) || 0.5;
    const min = num(s.attributes.min) ?? (room ? OVERRIDE_MIN : -Infinity);
    const max = num(s.attributes.max) ?? (room ? OVERRIDE_MAX : Infinity);
    const base = this._pending[entityId] ?? num(s.state);
    const value = Math.min(max, Math.max(min, Math.round((base + direction * step) / step) * step));
    if (value === base) return;
    this._pending[entityId] = value;
    this._pendingSeen[entityId] = s.last_updated;
    clearTimeout(this._stepTimer);
    // a few taps in a row send one value
    this._stepTimer = setTimeout(() => {
      for (const [id, v] of Object.entries(this._pending)) {
        const room = this._overrideRooms[id];
        if (room) this._hass.callService(DOMAIN, "set_override", { room, temperature: v });
        else this._hass.callService("number", "set_value", { value: v }, { entity_id: id });
      }
    }, 600);
  }

  _onChange(ev) {
    const el = ev.target;
    if (!this._hass || el.dataset?.action !== "mode") return;
    this._hass.callService("select", "select_option", { option: el.value }, { entity_id: el.dataset.entity });
    el.blur(); // the choice is made: let the card follow the new mode
  }

  // the return goes out once, when the field is left: "change" fires after every part of the date typed
  _onLeave(ev) {
    const el = ev.target;
    if (this._hass && el.dataset?.action === "return" && el.value && el.value !== el.dataset.sent) {
      el.dataset.sent = el.value;
      this._hass.callService("datetime", "set_value", { datetime: `${el.value.replace("T", " ")}:00` }, { entity_id: el.dataset.entity });
    }
    if (this._deferred) {
      this._deferred = false;
      setTimeout(() => this._render(), 0); // after the focus has moved on
    }
  }
}

// ------------------------------------------------------------------ visual editor: title, rooms shown, their order
class ThrifthermCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._texts) {
      loadTexts(hass).then((t) => { this._texts = t; this._render(); }).catch(() => {});
    }
    if (first) this._render();
  }

  _rooms() {
    return this._hass ? orderedRooms(discover(this._hass).rooms, this._config) : [];
  }

  _render() {
    if (!this._hass || !this._texts) return;
    const t = (key) => this._texts.card[key] ?? key;
    const hidden = new Set(this._config.hidden_rooms || []);
    const rooms = this._rooms();
    const rows = rooms.map((r, i) => `<div class="row">
      <label><input type="checkbox" data-action="show" data-room="${esc(r.key)}"${hidden.has(r.key) ? "" : " checked"}> ${esc(r.name)}</label>
      <span class="spacer"></span>
      <button data-action="move" data-room="${esc(r.key)}" data-delta="-1" title="${esc(t("editor_up"))}" ${i === 0 ? "disabled" : ""}>${icon("up")}</button>
      <button data-action="move" data-room="${esc(r.key)}" data-delta="1" title="${esc(t("editor_down"))}" ${i === rooms.length - 1 ? "disabled" : ""}>${icon("down")}</button>
    </div>`).join("");
    this.shadowRoot.innerHTML = `<style>${EDITOR_STYLE}</style>
      ${this._titleField(t)}
      <div class="field"><span>${esc(t("editor_rooms"))}</span>${rows}</div>
      ${this._controlsField(t, "editor_controls")}`;
  }

  _titleField(t) {
    return `<label class="field"><span>${esc(t("editor_title"))}</span><input type="text" data-action="title" value="${esc(this._config.title || "")}"></label>`;
  }

  // what a tapped room (or the room card) shows: one checkbox per control
  _controlsField(t, labelKey) {
    const hidden = new Set(this._config.hidden_controls || []);
    const rows = CONTROLS.map((c) => `<div class="row">
      <label><input type="checkbox" data-action="control" data-control="${esc(c)}"${hidden.has(c) ? "" : " checked"}> ${esc(t(c))}</label>
    </div>`).join("");
    return `<div class="field"><span>${esc(t(labelKey))}</span>${rows}</div>`;
  }

  _emit(config) {
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
    this._render();
  }

  _onClick(ev) {
    const el = ev.target.closest("button[data-action=move]");
    if (!el) return;
    const keys = this._rooms().map((r) => r.key);
    const from = keys.indexOf(el.dataset.room);
    const to = from + Number(el.dataset.delta);
    if (from < 0 || to < 0 || to >= keys.length) return;
    [keys[from], keys[to]] = [keys[to], keys[from]];
    this._emit({ ...this._config, room_order: keys });
  }

  _onChange(ev) {
    const el = ev.target;
    if (el.dataset.action === "title") {
      const config = { ...this._config, title: el.value };
      if (!el.value) delete config.title;
      this._emit(config);
    } else if (el.dataset.action === "show") {
      const hidden = new Set(this._config.hidden_rooms || []);
      if (el.checked) hidden.delete(el.dataset.room); else hidden.add(el.dataset.room);
      const config = { ...this._config, hidden_rooms: [...hidden] };
      if (!hidden.size) delete config.hidden_rooms;
      this._emit(config);
    } else if (el.dataset.action === "control") {
      const hidden = new Set(this._config.hidden_controls || []);
      if (el.checked) hidden.delete(el.dataset.control); else hidden.add(el.dataset.control);
      const config = { ...this._config, hidden_controls: CONTROLS.filter((c) => hidden.has(c)) };
      if (!hidden.size) delete config.hidden_controls;
      this._emit(config);
    }
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .card { padding: 12px 16px 16px; color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, inherit); }
  .title { font-size: 1.3rem; font-weight: 500; padding: 4px 0 8px; }
  .empty { color: var(--secondary-text-color); padding: 8px 0; }
  button { font: inherit; color: inherit; }
  .icon { width: 20px; height: 20px; flex: none; fill: currentColor; }
  .flame { color: var(--state-climate-heat-color, #ff8100); }
  .hp { color: var(--state-climate-heat_cool-color, var(--primary-color)); }
  .muted { color: var(--secondary-text-color); }
  .hand { color: var(--warning-color, #ffa600); }
  .window { color: var(--info-color, #039be5); }

  .source { display: flex; align-items: center; gap: 12px; width: 100%; padding: 8px 0; background: none; border: 0; text-align: start; cursor: pointer; }
  .source > .icon { width: 28px; height: 28px; }
  .source-text { display: flex; flex-direction: column; flex: 1; min-width: 0; }
  .headline { font-size: 1.15rem; font-weight: 500; }
  .sub { color: var(--secondary-text-color); font-size: 0.9rem; }
  .chevron { color: var(--secondary-text-color); transition: transform 0.2s; }
  .chevron.open { transform: rotate(180deg); }

  .compare { margin: 4px 0 8px; padding: 12px; border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,0.08)); }
  .compare-title { font-weight: 500; margin-bottom: 8px; }
  .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
  .col { display: flex; flex-direction: column; gap: 2px; font-size: 0.9rem; min-width: 0; }
  .col-head { display: flex; align-items: center; gap: 6px; font-weight: 500; font-size: 1rem; }
  .does { font-weight: 500; }
  .why, .fact { color: var(--secondary-text-color); }
  .lock { display: flex; gap: 4px; align-items: flex-start; color: var(--warning-color, #ffa600); }
  .lock .icon { width: 16px; height: 16px; margin-top: 2px; }
  .compare-foot { margin-top: 10px; font-size: 0.85rem; color: var(--secondary-text-color); }

  .warnings { display: flex; flex-direction: column; gap: 6px; margin: 4px 0 8px; }
  .warn { --c: var(--warning-color, #ffa600); display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 12px;
    border-radius: 10px; border-inline-start: 4px solid var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); }
  .warn > .icon { color: var(--c); }
  .warn.error { --c: var(--error-color, #db4437); }
  .warn.info { --c: var(--info-color, #039be5); }
  .warn-text { flex: 1 1 180px; min-width: 0; }

  .rooms { border-top: 1px solid var(--divider-color); margin-top: 4px; }
  .room { border-bottom: 1px solid var(--divider-color); }
  .room.off .room-head { opacity: 0.55; }
  .room-head { display: grid; grid-template-columns: 1fr auto; grid-template-areas: "name temps" "reason reason" "bar bar";
    align-items: baseline; column-gap: 12px; width: 100%; padding: 10px 0; background: none; border: 0; text-align: start; cursor: pointer; }
  .room-name { grid-area: name; display: flex; align-items: center; gap: 6px; min-width: 0; }
  .name { font-weight: 500; font-size: 1.05rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badges { display: inline-flex; gap: 2px; }
  .badges .icon { width: 18px; height: 18px; }
  .temps { grid-area: temps; white-space: nowrap; }
  .current { font-size: 1.6rem; font-weight: 400; }
  .target { color: var(--secondary-text-color); margin-inline-start: 6px; }
  .reason { grid-area: reason; color: var(--secondary-text-color); font-size: 0.9rem; }
  .bar { grid-area: bar; height: 4px; border-radius: 2px; margin-top: 6px; background: var(--divider-color); overflow: hidden; }
  .bar > div { height: 100%; background: var(--state-climate-heat-color, #ff8100); border-radius: 2px; }

  .detail { display: flex; flex-direction: column; gap: 10px; padding: 0 0 12px; }
  .stepper { display: flex; align-items: center; gap: 8px; }
  .step-label { flex: 1; }
  .step-value { min-width: 72px; text-align: center; font-weight: 500; }
  .round { width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--divider-color); background: none; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center; color: var(--primary-color); }
  .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 18px; border: 1px solid var(--divider-color);
    background: var(--ha-card-background, var(--card-background-color, transparent)); color: var(--primary-color); cursor: pointer; font-size: 0.9rem; }
  .btn .icon { width: 18px; height: 18px; }

  .mode { display: flex; flex-direction: column; gap: 8px; padding-top: 12px; }
  .mode-row { display: flex; align-items: center; gap: 12px; }
  .mode-row > span { flex: 1; }
  select, input { font: inherit; color: var(--primary-text-color); background: var(--ha-card-background, var(--card-background-color, transparent));
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 8px; max-width: 60%; }
  button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .rooms.single { border: 0; margin: 0; }
  .rooms.single .room { border: 0; }
  .rooms.single .room-head { cursor: default; }
`;

const EDITOR_STYLE = `
  :host { display: block; color: var(--primary-text-color); }
  .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
  .field > span { font-weight: 500; }
  select, input[type=text] { font: inherit; padding: 8px; border-radius: 6px; border: 1px solid var(--divider-color); color: inherit; background: transparent; }
  .row { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--divider-color); }
  .spacer { flex: 1; }
  button { background: none; border: 0; cursor: pointer; color: var(--primary-color); padding: 4px; }
  button[disabled] { color: var(--disabled-text-color, #bdbdbd); cursor: default; }
  .icon { width: 20px; height: 20px; fill: currentColor; }
`;

// ------------------------------------------------------------------ one room, for a room's own page
class ThrifthermRoomCard extends ThrifthermCard {
  constructor() {
    super();
    this._single = true;
  }

  static getConfigElement() {
    return document.createElement("thriftherm-room-card-editor");
  }

  static getStubConfig(hass) {
    const first = hass ? orderedRooms(discover(hass).rooms, {})[0] : undefined;
    return first ? { room: first.key } : {};
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: 6, min_columns: 4 };
  }

  _render() {
    if (this._config.room) this._open = new Set([this._config.room]); // always open: the room's controls are the point
    super._render();
  }

  _watchedSystem() {
    // the room row's badges: does the boiler or the heat pump heat it
    return [this._model.system.boiler_command, this._model.system.midea_command].filter(Boolean);
  }

  _watchedRooms() {
    return this._model.rooms.filter((r) => r.key === this._config.room);
  }

  _paintAll() {
    if (!this._config.room) return `<div class="empty">${esc(this._t("room_choose"))}</div>`;
    const room = this._model.rooms.find((r) => r.key === this._config.room);
    if (!room) return `<div class="empty">${esc(this._t("room_not_found", { room: this._config.room }))}</div>`;
    const title = this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : "";
    return `${title}<div class="rooms single">${this._room(room)}</div>`;
  }
}

class ThrifthermRoomCardEditor extends ThrifthermCardEditor {
  _render() {
    if (!this._hass || !this._texts) return;
    const t = (key) => this._texts.card[key] ?? key;
    const rooms = this._rooms();
    const options = rooms.map((r) =>
      `<option value="${esc(r.key)}"${r.key === this._config.room ? " selected" : ""}>${esc(r.name)}</option>`).join("");
    // no room yet, or one that is gone: an empty choice first, so that picking any room is a change
    const known = rooms.some((r) => r.key === this._config.room);
    this.shadowRoot.innerHTML = `<style>${EDITOR_STYLE}</style>
      <label class="field"><span>${esc(t("editor_room"))}</span><select data-action="room">${known ? "" : "<option selected></option>"}${options}</select></label>
      ${this._titleField(t)}
      ${this._controlsField(t, "editor_controls_room")}`;
  }

  _onChange(ev) {
    const el = ev.target;
    if (el.dataset.action === "room") this._emit({ ...this._config, room: el.value });
    else super._onChange(ev);
  }
}

if (!customElements.get("thriftherm-card")) customElements.define("thriftherm-card", ThrifthermCard);
if (!customElements.get("thriftherm-card-editor")) customElements.define("thriftherm-card-editor", ThrifthermCardEditor);
if (!customElements.get("thriftherm-room-card")) customElements.define("thriftherm-room-card", ThrifthermRoomCard);
if (!customElements.get("thriftherm-room-card-editor")) customElements.define("thriftherm-room-card-editor", ThrifthermRoomCardEditor);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "thriftherm-room-card")) {
  window.customCards.push({
    type: "thriftherm-room-card",
    name: "Thriftherm room",
    description: "One room: temperature, target and reason, change it by hand, quick heat-up, drying, schedule. / Ein Raum: Temperatur, Soll und Grund, von Hand ändern, Schnell aufheizen, Trocknen, Zeitplan.",
    preview: true,
    documentationURL: "https://github.com/alpha-uniform/thriftherm",
  });
}
if (!window.customCards.some((c) => c.type === "thriftherm-card")) {
  window.customCards.push({
    type: "thriftherm-card",
    name: "Thriftherm",
    description: "Heating at a glance: heat source, rooms, operating mode. / Heizung auf einen Blick: Wärmequelle, Räume, Betriebsmodus.",
    preview: true,
    documentationURL: "https://github.com/alpha-uniform/thriftherm",
  });
}
