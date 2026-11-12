"use strict";

// ---------------------------------------------------------------------------------------------- settings schema
// Declarative description of the settings form. `path` points into the config JSON returned by /api/config.
const CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"];

const SCHEMA = [
  {
    title: "Source & output", open: true, fields: [
      { path: "source.input_dir", label: "Source folder", type: "folder", hint: "SD card, DCIM folder or any folder with GoPro files" },
      { path: "source.recursive", label: "Include subfolders", type: "bool" },
      { path: "output.output_dir", label: "Output folder", type: "folder" },
      { path: "output.filename_template", label: "File name", type: "text", hint: "Fields: {date} {start} {end} {group} {recordings}" },
      { path: "output.container", label: "Container", type: "select", options: ["mp4", "mov"] },
      { path: "output.overwrite", label: "Overwrite existing files", type: "bool" },
      { path: "output.keep_intermediates", label: "Keep intermediate files", type: "bool" },
      { path: "output.work_dir", label: "Temp folder", type: "folder", hint: "Holds export work files and footage copied to this Mac. Empty = ~/Library/Caches on the internal SSD" },
    ],
  },
  {
    title: "Grouping", fields: [
      { path: "grouping.group_by", label: "One output video per", type: "select", options: [["day", "Day"], ["recording", "Recording"], ["all", "Everything together"]] },
      { path: "grouping.timezone", label: "Timezone", type: "text", hint: "IANA name, e.g. Europe/Madrid. Used for the overlay clock", list: "tz-list" },
      { path: "grouping.day_start_hour", label: "Day starts at hour", type: "number", min: 0, max: 23, hint: "Recordings before this hour count as the previous day" },
      { path: "grouping.time_source", label: "Recording time from", type: "select", options: [["metadata", "Video metadata"], ["file_mtime", "File modification date"]] },
      { path: "grouping.metadata_is_local_time", label: "Metadata time is camera local time", type: "bool", hint: "True for GoPro (it writes local time labelled as UTC)" },
      { path: "grouping.min_recording_seconds", label: "Ignore recordings shorter than (s)", type: "number", min: 0, step: 1 },
      { path: "grouping.continuity_gap_seconds", label: "Continuous if gap under (s)", type: "number", min: 0, step: 0.5, hint: "Recordings this close together count as one uninterrupted session" },
      { path: "source.file_pattern", label: "File name pattern (regex)", type: "text", advanced: true, hint: "Named groups: recording, chapter (letter optional)" },
      { path: "source.dated_pattern", label: "Dated file name pattern (regex)", type: "text", advanced: true, hint: "Named groups: date, time. Empty disables this naming scheme" },
    ],
  },
  {
    title: "Overlay", fields: [
      { path: "overlay.enabled", label: "Add overlay", type: "bool", hint: "Off = lossless join only (very fast)" },
      { path: "overlay.layout_mode", label: "Layout", type: "select", options: [["generated", "Built from the options below"], ["builtin", "Library default dashboard"], ["xml", "Custom XML file"]] },
      { path: "overlay.layout_xml_path", label: "Layout XML", type: "file", showIf: c => c.overlay.layout_mode === "xml" },
      { path: "overlay.margin", label: "Margin (px @1080p)", type: "number", min: 0 },
      { path: "overlay.font", label: "Font file", type: "file" },
      { sub: "Date & time" },
      { path: "overlay.datetime.enabled", label: "Show date & time", type: "bool" },
      { path: "overlay.datetime.corner", label: "Position", type: "corner" },
      { path: "overlay.datetime.date_format", label: "Date format", type: "text", hint: "strftime, e.g. %d/%m/%Y or %A %d %B" },
      { path: "overlay.datetime.time_format", label: "Time format", type: "text" },
      { path: "overlay.datetime.font_size", label: "Font size (px @1080p)", type: "number", min: 8 },
      { path: "overlay.datetime.color", label: "Colour (R,G,B)", type: "text" },
      { sub: "Mini map" },
      { path: "overlay.minimap.enabled", label: "Show moving mini map", type: "bool" },
      { path: "overlay.minimap.corner", label: "Position", type: "corner" },
      { path: "overlay.minimap.size", label: "Size (px @1080p)", type: "number", min: 64 },
      { path: "overlay.minimap.zoom", label: "Zoom (1-19)", type: "number", min: 1, max: 19 },
      { path: "overlay.minimap.opacity", label: "Opacity", type: "number", min: 0, max: 1, step: 0.05 },
      { path: "overlay.minimap.corner_radius", label: "Corner radius", type: "number", min: 0 },
      { path: "overlay.minimap.rotate", label: "Rotate with heading", type: "bool" },
      { sub: "Journey map" },
      { path: "overlay.journey_map.enabled", label: "Show route map", type: "bool" },
      { path: "overlay.journey_map.corner", label: "Position", type: "corner" },
      { path: "overlay.journey_map.size", label: "Size (px @1080p)", type: "number", min: 64 },
      { path: "overlay.journey_map.opacity", label: "Opacity", type: "number", min: 0, max: 1, step: 0.05 },
      { path: "overlay.journey_map.mode", label: "Route line", type: "select", options: [["progressive", "Drawn as you move"], ["static", "Whole route from the start"]] },
      { path: "overlay.journey_map.scope", label: "Route covers", type: "select", options: [["group", "The whole output video (one continuous line for the day)"], ["recording", "Each recording on its own"]] },
      { path: "overlay.journey_map.gap_style", label: "Between recordings", type: "select", options: [["solid", "Solid line"], ["dashed", "Dashed line"], ["none", "No line (jump)"]], showIf: c => c.overlay.journey_map.scope === "group" },
      { path: "overlay.journey_map.upcoming_opacity", label: "Show upcoming route (opacity, 0 = hidden)", type: "number", min: 0, max: 1, step: 0.05, showIf: c => c.overlay.journey_map.mode === "progressive" },
      { path: "overlay.journey_map.line_color", label: "Line colour (R,G,B)", type: "text" },
      { path: "overlay.journey_map.line_width", label: "Line width (px @1080p)", type: "number", min: 1, max: 50 },
      { path: "overlay.journey_map.marker_color", label: "Position marker colour (R,G,B)", type: "text" },
      { path: "overlay.journey_map.marker_size", label: "Position marker size", type: "number", min: 1, max: 50 },
      { sub: "Speed" },
      { path: "overlay.speed.enabled", label: "Show speed", type: "bool" },
      { path: "overlay.speed.corner", label: "Position", type: "corner" },
      { path: "overlay.speed.font_size", label: "Font size (px @1080p)", type: "number", min: 8 },
      { path: "overlay.units_speed", label: "Speed units", type: "select", options: ["kph", "mph", "mps", "knot"] },
      { sub: "Map & GPS" },
      { path: "overlay.map_style", label: "Map style", type: "select", options: () => OPTIONS.map_styles },
      { path: "overlay.map_api_key", label: "Map API key", type: "text", hint: "Only for Thunderforest / Geoapify styles" },
      { path: "overlay.gps_dop_max", label: "Max GPS DOP", type: "number", min: 0, step: 0.5, advanced: true, hint: "Discard GPS points less precise than this" },
      { path: "overlay.gps_speed_max", label: "Max GPS speed (kph)", type: "number", min: 0, advanced: true },
      { path: "overlay.fallback_without_overlay", label: "If a recording has no GPS, keep it without overlay", type: "bool" },
      { path: "overlay.extra_args", label: "Extra gopro-dashboard args", type: "list", advanced: true, hint: "Space separated, e.g. --gpx /path/ride.gpx" },
    ],
  },
  {
    title: "Encoding (macOS)", fields: [
      { path: "encoding.codec", label: "Video encoder", type: "select", options: [
        ["hevc_videotoolbox", "HEVC · Apple GPU (recommended)"], ["h264_videotoolbox", "H.264 · Apple GPU"],
        ["prores_videotoolbox", "ProRes HQ · Apple GPU (huge, for editing)"], ["libx265", "HEVC · CPU (slow, smaller)"], ["libx264", "H.264 · CPU"]] },
      { path: "encoding.rate_control", label: "Rate control", type: "select", options: [["quality", "Constant quality"], ["bitrate", "Target bitrate"]] },
      { path: "encoding.quality", label: "Quality (1-100)", type: "number", min: 1, max: 100, showIf: c => c.encoding.rate_control === "quality" },
      { path: "encoding.bitrate", label: "Bitrate", type: "text", hint: "e.g. 60M", showIf: c => c.encoding.rate_control === "bitrate" },
      { path: "encoding.resolution", label: "Resolution", type: "select", options: [["source", "Same as the footage"], ["1440p", "1440p · about twice as fast from 4K"]],
        hint: "Only with the overlay on and the generated layout. A smaller frame needs less bitrate: 15M at 4K looks like 7M at 1440p" },
      { path: "encoding.hw_decode", label: "Hardware decoding (VideoToolbox)", type: "bool" },
      { path: "encoding.audio", label: "Audio", type: "select", options: [["copy", "Copy original"], ["aac", "Re-encode AAC"]] },
      { path: "encoding.parallel_jobs", label: "Recordings rendered in parallel", type: "number", min: 1, max: 16, hint: "Overlay drawing uses one CPU core per recording" },
      { path: "encoding.ffmpeg_dir", label: "ffmpeg folder", type: "folder", advanced: true, hint: "Empty = use ffmpeg from PATH" },
    ],
  },
];

// Each field has one home. Keep the field definitions above independent of navigation.
const ALL_FIELDS = SCHEMA.flatMap(section => section.fields).filter(field => field.path);
const fieldsFor = paths => paths.map(path => ALL_FIELDS.find(field => field.path === path));
// The source folder is chosen in the footage card, next to Scan, so it has no place in the settings panel.
const ESSENTIAL_PATHS = ["output.output_dir", "encoding.resolution", "encoding.rate_control", "encoding.quality", "encoding.bitrate", "grouping.group_by", "overlay.enabled"];
const OUTSIDE_SETTINGS = [...ESSENTIAL_PATHS, "source.input_dir"];
const EXTRA_ADVANCED = new Set(["output.work_dir", "output.keep_intermediates", "grouping.time_source", "grouping.metadata_is_local_time"]);
// Per-widget settings live in the layout editor (next to the frame they affect), not in the settings panel.
const WIDGET_GROUPS = [["datetime", "Date & time"], ["minimap", "Mini map"], ["journey_map", "Journey map"], ["speed", "Speed"]].map(([key, title]) => ({
  id: key, title, fields: ALL_FIELDS.filter(f => f.path.startsWith(`overlay.${key}.`) || (key === "speed" && f.path === "overlay.units_speed")),
}));
const SETTINGS_SECTIONS = [
  { id: "essentials", title: "Main settings", description: "Where your films go and how they look", fields: fieldsFor(ESSENTIAL_PATHS) },
  { id: "appearance", title: "Overlay & appearance", description: "Layout, maps and GPS", groups: [
    { id: "layout", title: "General layout", fields: fieldsFor(["overlay.layout_mode", "overlay.layout_xml_path", "overlay.margin", "overlay.font", "overlay.extra_args"]) },
    { id: "maps", title: "Maps & GPS", fields: fieldsFor(["overlay.map_style", "overlay.map_api_key", "overlay.gps_dop_max", "overlay.gps_speed_max", "overlay.fallback_without_overlay"]) },
  ] },
  { id: "files", title: "Files & grouping", description: "File names, recording times and grouping", fields: ALL_FIELDS.filter(f => /^(source|output|grouping)\./.test(f.path) && !OUTSIDE_SETTINGS.includes(f.path)) },
  { id: "quality", title: "Encoding & performance", description: "Encoder, audio and rendering", fields: ALL_FIELDS.filter(f => f.path.startsWith("encoding.") && !OUTSIDE_SETTINGS.includes(f.path)) },
];

let OPTIONS = { map_styles: ["osm"] };
const TIMEZONES = (Intl.supportedValuesOf && Intl.supportedValuesOf("timeZone")) || [];

// ---------------------------------------------------------------------------------------------- helpers
const $ = sel => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(c));
  return node;
};
const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
const set = (obj, path, value) => {
  const keys = path.split(".");
  keys.slice(0, -1).reduce((o, k) => o[k], obj)[keys.at(-1)] = value;
};
const fmtDuration = s => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(sec).padStart(2, "0")}s`;
};
const fmtSize = b => b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${(b / 1e6).toFixed(0)} MB`;
const fmtTime = iso => iso ? iso.slice(11, 16) : "—";
const fmtClock = iso => iso ? iso.slice(11, 19) : "—";
const fmtGap = s => {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return m ? `${h} h ${m} min` : `${h} h`;
  return m ? `${m} min ${sec} s` : `${sec} s`;
};
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDayHeader(dayIso) {
  // dayIso is a "YYYY-MM-DD" group label (or something else for group_by != "day"); fall back to the raw label.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dayIso);
  if (!m) return dayIso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? (() => { try { return JSON.parse(text); } catch { return text; } })() : null;
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(Array.isArray(detail) ? detail.map(d => `${d.loc.join(".")}: ${d.msg}`).join("\n") : detail || res.statusText);
  }
  return data;
}

function toast(message, kind = "info") {
  const t = el("div", { class: `toast ${kind}` }, message);
  ($("#toast-region") || document.body).append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3000);
}

// ---------------------------------------------------------------------------------------------- settings form
let config = null;
let saveTimer = null;
const openSections = new Set();
let settingsQuery = "";
const normalizeSearch = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function settingsDetails(id, title, subtitle, forceOpen = false) {
  const details = el("details", { "data-settings-id": id, open: forceOpen || openSections.has(id) },
    el("summary", {}, el("span", {}, title), subtitle ? el("small", {}, subtitle) : null));
  details.addEventListener("toggle", () => {
    if (!details.isConnected || settingsQuery) return;
    details.open ? openSections.add(id) : openSections.delete(id);
  });
  return details;
}

function sectionSummary(section) {
  if (section.id === "quality") {
    const codec = config.encoding.codec;
    const name = codec.includes("265") || codec.includes("hevc") ? "HEVC" : codec.includes("264") ? "H.264" : "ProRes";
    return `${name} encoder · ${config.encoding.parallel_jobs} in parallel`;
  }
  if (section.id === "appearance") {
    const count = ["datetime", "minimap", "journey_map", "speed"].filter(key => config.overlay[key].enabled).length;
    return config.overlay.enabled ? `${count} widgets · ${config.overlay.layout_mode} layout` : "Overlay off · layout, maps and GPS";
  }
  return section.description;
}

function renderSettings() {
  const root = $("#settings");
  const scroll = root.scrollTop;
  const focusId = root.contains(document.activeElement) ? document.activeElement.id : null;
  const query = normalizeSearch(settingsQuery.trim());
  root.replaceChildren(el("datalist", { id: "tz-list" }, TIMEZONES.map(tz => el("option", { value: tz }))));
  let matches = 0;
  const appendFields = (parent, fields, context, id) => {
    const visible = fields.filter(f => (query || !f.showIf || f.showIf(config)) && (!query ||
      query.split(/\s+/).every(word => normalizeSearch(`${context} ${f.label} ${f.hint || ""} ${f.path}`).includes(word))));
    const regular = visible.filter(f => !f.advanced && !EXTRA_ADVANCED.has(f.path));
    const advanced = visible.filter(f => f.advanced || EXTRA_ADVANCED.has(f.path));
    regular.forEach(f => parent.append(renderField(f)));
    if (advanced.length) {
      const details = settingsDetails(`${id}-advanced`, "Advanced options", `${advanced.length} ${advanced.length === 1 ? "setting" : "settings"}`, !!query);
      details.className = "settings-advanced";
      advanced.forEach(f => details.append(renderField(f)));
      parent.append(details);
    }
    matches += visible.length;
    return visible.length;
  };
  for (const section of SETTINGS_SECTIONS) {
    if (section.id === "essentials" && !query) {
      // The main settings are always in view, above everything else; search falls back to plain fields.
      root.append(
        el("section", { class: "settings-main", "aria-labelledby": "settings-main-title" },
          el("div", { class: "settings-main-head" }, el("span", { class: "eyebrow" }, "Export"),
            el("h2", { id: "settings-main-title" }, section.title), el("p", {}, section.description)),
          renderEssentials()),
        el("h3", { class: "settings-more-title" }, "More settings", el("small", {}, "Fine-tune when you need to")));
      continue;
    }
    const details = settingsDetails(section.id, section.title, sectionSummary(section), !!query);
    const before = matches;
    if (section.groups) {
      if (!query) details.append(widgetsEntry());
      for (const group of section.groups) {
        const child = settingsDetails(group.id, group.title, "", !!query);
        child.className = "settings-group";
        if (appendFields(child, group.fields, `${section.title} ${group.title}`, group.id)) details.append(child);
      }
    } else appendFields(details, section.fields, section.title, section.id);
    if (matches > before) root.append(details);
  }
  // Settings that live elsewhere still show up in search, with a way to get there.
  const hits = text => query && query.split(/\s+/).every(word => normalizeSearch(text).includes(word));
  const redirects = [];
  const source = ALL_FIELDS.find(f => f.path === SOURCE_PATH);
  if (hits(`${source.label} ${source.hint} footage input ${source.path}`)) {
    redirects.push(redirectNote([el("strong", {}, "Source folder"), " is chosen in Review footage, right next to Scan."], "Show", focusSource));
  }
  for (const group of WIDGET_GROUPS) {
    const found = group.fields.filter(f => hits(`overlay widget ${group.title} ${f.label} ${f.hint || ""} ${f.path}`));
    if (!found.length) continue;
    const text = [el("strong", {}, group.title), ` · ${found.length} ${found.length === 1 ? "setting" : "settings"} in the layout editor.`];
    redirects.push(layoutEditable()
      ? redirectNote(text, "Open", () => openLayoutEditor(group.id))
      : redirectNote([...text, " Turn the overlay on in Main settings to edit them."]));
  }
  matches += redirects.length;
  root.prepend(...redirects);
  if (!matches) root.append(el("p", { class: "settings-empty" }, "No matching settings. Try “map”, “quality” or “folder”."));
  $("#settings-search-status").textContent = query ? `${matches} ${matches === 1 ? "setting" : "settings"} found · including advanced options` : "";
  $("#settings-search-clear").hidden = !settingsQuery;
  syncLayoutThumbs();
  renderExportOverlay();
  root.scrollTop = scroll;
  if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
}

function openSettingsSection(sectionId, groupId, fieldPath) {
  if ($("#layout-dialog").open) $("#layout-dialog").close();
  settingsQuery = "";
  $("#settings-search").value = "";
  openSections.add(sectionId);
  if (groupId) openSections.add(groupId);
  renderSettings();
  const target = fieldPath ? document.getElementById(`f-${fieldPath}`)
    : document.querySelector(`[data-settings-id="${groupId || sectionId}"] > summary`);
  target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  target?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------------------------------------- folders & essentials
// The settings needed before a scan get a dedicated, more visual layout. Search falls back to renderField.
const ICONS = {
  laptop: '<svg viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="10" rx="1.5"/><path d="M3 18.5h18"/></svg>',
  footage: '<svg viewBox="0 0 24 24"><rect x="3" y="6.5" width="13" height="11" rx="2.5"/><path d="M16 10.5l4.2-2.4c.4-.2.8.1.8.5v6.8c0 .4-.4.7-.8.5L16 13.5"/></svg>',
  film: '<svg viewBox="0 0 24 24"><rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8 3.5v17M16 3.5v17M4 8.5h4M4 15.5h4M16 8.5h4M16 15.5h4"/></svg>',
  pencil: '<svg viewBox="0 0 24 24"><path d="M14.5 5.5l4 4M4 20l1-4.5L15.8 4.7a1.4 1.4 0 012 0l1.5 1.5a1.4 1.4 0 010 2L8.5 19 4 20z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>',
  scissors: '<svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/></svg>',
};
const icon = (name, cls = "ess-icon") => {
  const span = el("span", { class: cls, "aria-hidden": "true" });
  span.innerHTML = ICONS[name];
  return span;
};
const SOURCE_PATH = "source.input_dir";
const GROUP_CHOICES = [
  ["day", "Day", "One film for each day, clips joined in order."],
  ["recording", "Recording", "One film per recording, its chapters joined."],
  ["all", "Everything", "All footage joined into a single film."],
];
// Widget names come from the editor definitions (preview.js), so every surface calls them the same.
const WIDGET_NAMES = Object.entries(WIDGETS).map(([key, widget]) => [key, widget.label]);

// "/Volumes/Untitled/DCIM/100GOPRO" -> { name: "100GOPRO", crumbs: ["Untitled", "DCIM"] }, the way Finder would say it.
function describePath(path) {
  const parts = (path || "").split("/").filter(Boolean);
  let root = "Mac", rest = parts, place = "On this Mac";
  if (parts[0] === "Users" && parts.length >= 2) { root = "Home"; rest = parts.slice(2); place = "Your home folder"; }
  else if (parts[0] === "Volumes" && parts.length >= 2) { root = parts[1]; rest = parts.slice(2); place = "External drive"; }
  if (!rest.length) return { name: root === "Mac" ? "/" : root, crumbs: [place] };
  const crumbs = [root, ...rest.slice(0, -1)];
  return { name: rest.at(-1), crumbs: crumbs.length > 3 ? [crumbs[0], "…", ...crumbs.slice(-2)] : crumbs };
}

// One folder control, used for both the source (footage card) and the output (main settings).
// Shows the folder the way a person names it; the full path is one click away for typing.
function folderCard({ path, caption, iconName, emptyName, emptyHint, variant = "", onCommit }) {
  const card = el("div", { class: `ess-folder ${variant}` });
  const commit = v => { if (v !== (get(config, path) || "")) onCommit(v); else paint(false); };
  const pick = async () => {
    const res = await api("POST", "/api/pick", { kind: "folder", prompt: `Choose the ${caption.toLowerCase()} folder` });
    if (res.path) onCommit(res.path);
  };
  const paint = editing => {
    const value = get(config, path) || "";
    const { name, crumbs } = describePath(value);
    card.classList.toggle("is-empty", !value);
    let line;
    if (editing) {
      let done = false;
      const finish = save => { if (done) return; done = true; save ? commit(line.value.trim().replace(/(.)\/+$/, "$1")) : paint(false); };
      line = el("input", { class: "ess-path", type: "text", value, spellcheck: "false", autocomplete: "off",
        placeholder: "/Volumes/…", "aria-label": `${caption} folder path`,
        onkeydown: e => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); },
        onblur: () => finish(true) });
      requestAnimationFrame(() => { line.focus(); line.setSelectionRange(value.length, value.length); line.scrollLeft = line.scrollWidth; });
    } else {
      line = el("button", { class: "ess-crumbs", type: "button", id: `f-${path}`,
        title: value ? `${value}\nClick to type a different path` : "Type a folder path", onclick: () => paint(true) },
        value ? crumbs.flatMap((c, i) => [i ? el("span", { class: "sep", "aria-hidden": "true" }, "›") : null, el("span", {}, c)])
          : el("span", {}, emptyHint),
        icon("pencil", "ess-edit"));
    }
    card.replaceChildren(icon(iconName),
      el("div", { class: "ess-folder-body" },
        el("span", { class: "ess-caption" }, caption),
        el("strong", { class: "ess-folder-name", title: value || undefined }, value ? name : emptyName), line),
      el("button", { class: `button compact ess-change${value ? " quiet" : " primary"}`, type: "button",
        "aria-label": `Choose ${caption.toLowerCase()} folder`, onclick: pick }, value ? "Change" : "Choose…"));
  };
  paint(false);
  return card;
}

// The source folder lives only in the footage card: choosing it is the first half of scanning.
function renderSource() {
  const hasSource = !!config.source.input_dir;
  $("#source-slot").replaceChildren(folderCard({
    path: SOURCE_PATH, caption: "Source", iconName: "footage", variant: "source-bar",
    emptyName: "Where is your footage?", emptyHint: "SD card, DCIM or any folder with GoPro clips",
    onCommit: v => {
      set(config, SOURCE_PATH, v);
      renderSource();
      v ? doScan() : scheduleSave();
    },
  }));
  $("#btn-scan").disabled = !hasSource;
  if (!scanned) {
    const summary = $("#scan-summary");
    summary.hidden = !hasSource;
    summary.className = "empty-state";
    summary.replaceChildren(
      el("span", { class: "empty-icon", "aria-hidden": "true" }, "▣"),
      el("strong", {}, "Ready to scan"),
      el("p", {}, `Scan ${describePath(config.source.input_dir).name} to find and group your GoPro clips.`));
  }
}

// Whole-card copies made by older versions, still usable as the source. New copies are a phase of each export job.
let copyState = { status: "idle" };
let copyTimer = null;

async function pollCopy() {
  clearTimeout(copyTimer);
  try { copyState = await api("GET", "/api/copy"); } catch { return; }
  renderCopy();
  if (copyState.status === "running") copyTimer = setTimeout(pollCopy, 1000);
}

async function deleteCopy() {
  const { size, origin, origin_exists } = copyState.local;
  const question = origin_exists
    ? `Delete the ${fmtSize(size)} copy on this Mac? The source goes back to ${origin}.`
    : `The original folder${origin ? ` (${origin})` : ""} is not available: the card may be removed or formatted, so this copy may be your only one.\n\nDelete the ${fmtSize(size)} copy anyway?`;
  if (!confirm(question)) return;
  try {
    config = await api("DELETE", "/api/copy");
  } catch (e) { return toast(e.message, "error"); }
  toast("Local copy deleted");
  scanned = false; groups = [];
  $("#groups").replaceChildren();
  $("#groups-toolbar").hidden = $("#export-bar").hidden = true;
  renderSource();
  await pollCopy();
  if (config.source.input_dir) doScan();
}

function renderCopy() {
  const st = copyState;
  $("#copy-first-row").hidden = !!st.local || st.status === "running";
  const origin = st.local?.origin ? describePath(st.local.origin) : null;
  $("#copy-slot").replaceChildren(st.local ? el("div", { class: "copy-bar" }, icon("laptop"),
    el("div", { class: "copy-body" }, el("strong", {}, "Using the copy on this Mac"),
      el("span", { class: "muted small" }, `${fmtSize(st.local.size)}${origin ? ` · copied from ${origin.crumbs[0]} › ${origin.name}` : ""}. Delete it once your films are exported.`)),
    el("div", { class: "copy-actions" },
      el("button", { class: "button compact ghost", type: "button", onclick: () => api("POST", "/api/copy/open").catch(e => toast(e.message, "error")) }, "Show in Finder"),
      el("button", { class: "button compact ghost danger", type: "button", onclick: deleteCopy }, "Delete copy"))) : "");
}

// One card at a time: the header steps are the wizard's navigation.
function goToStep(n) {
  document.querySelectorAll(".workspace > .card[data-step]").forEach(card => { card.hidden = card.dataset.step !== String(n); });
  document.querySelectorAll(".steps button").forEach(b => {
    const active = b.dataset.step === String(n);
    b.parentElement.classList.toggle("active", active);
    b.toggleAttribute("aria-current", active);
    if (active) b.setAttribute("aria-current", "step");
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function focusSource() {
  goToStep(1);
  $("#footage-card").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#source-slot .ess-change")?.focus({ preventScroll: true });
}

// The layout editor is a tool, not a workflow step: it lives in a dialog and is only reachable with the overlay on.
const layoutEditable = () => config.overlay.enabled && config.overlay.layout_mode === "generated";

// Optionally lands directly on one widget's settings.
async function openLayoutEditor(widgetKey) {
  if (!config.overlay.enabled) return;
  $("#layout-dialog").showModal();
  await renderPreview(); // handle sizes depend on the on-screen width, which is only known once the dialog is open
  if (typeof widgetKey === "string") openWidgetDetail(widgetKey);
}

function redirectNote(content, action, onclick) {
  return el("div", { class: "settings-note settings-redirect" }, el("span", {}, content),
    action ? el("button", { class: "button quiet compact", type: "button", onclick }, action) : null);
}

// What the Overlay section says about widgets, which are configured in the layout editor.
function widgetsEntry() {
  if (!config.overlay.enabled) return el("p", { class: "settings-note" }, "Overlay is off. Turn it on in Main settings to add widgets such as the clock or maps.");
  if (!layoutEditable()) return el("p", { class: "settings-note" }, "Widgets apply to the generated layout. Choose it under General layout to arrange them.");
  const shown = WIDGET_NAMES.filter(([key]) => config.overlay[key].enabled).map(([, label]) => label);
  return el("div", { class: "settings-widgets" },
    el("span", {}, el("strong", {}, "Widgets"), el("small", {}, shown.length ? shown.join(" · ") : "None shown")),
    el("button", { class: "button secondary compact", type: "button", onclick: () => openLayoutEditor() }, "Edit"));
}

// Mirrors the editor's SVG into every thumbnail, so the miniature is always the real layout.
function syncLayoutThumbs() {
  const source = $("#preview");
  document.querySelectorAll(".layout-thumb svg").forEach(svg => {
    svg.setAttribute("viewBox", source.getAttribute("viewBox") || "0 0 16 9");
    svg.setAttribute("class", "overlay-svg");
    svg.innerHTML = source.innerHTML.replace(/ id="[^"]*"/g, "");
  });
}

function renderEssentials() {
  const field = path => ALL_FIELDS.find(f => f.path === path);
  const update = (path, value) => {
    set(config, path, value);
    scheduleSave(field(path));
  };

  const segmented = (path, choices, labelledby, disabled = false) =>
    el("div", { class: "ess-segmented", role: "radiogroup", "aria-labelledby": labelledby },
      choices.map(([value, label]) => el("label", {},
        el("input", { type: "radio", name: `ess-${path}`, value, disabled, checked: value === get(config, path),
          id: value === get(config, path) ? `f-${path}` : undefined,
          onchange: () => { update(path, value); renderSettings(); } }),
        el("span", {}, label))));
  const groupBy = config.grouping.group_by;
  const overlayOn = config.overlay.enabled;
  const enc = config.encoding;
  // Without the overlay clips are joined as recorded, so the video settings stay editable but read as idle.
  const idle = overlayOn ? "" : " is-idle";
  const idleNote = "Not used while the overlay is off: clips are joined as recorded.";
  const canScale = config.overlay.layout_mode === "generated";
  const prores = enc.codec === "prores_videotoolbox";
  const widgets = WIDGET_NAMES.filter(([key]) => config.overlay[key].enabled).map(([, label]) => label);
  const overlay = el("label", { class: `ess-overlay${overlayOn ? " is-on" : ""}` },
    el("span", { class: "ess-overlay-text" },
      el("strong", {}, "Overlay"),
      el("small", {}, overlayOn ? "Re-encoded with data drawn on every frame." : "Lossless join only · the fastest export.")),
    el("input", { id: "f-overlay.enabled", type: "checkbox", role: "switch", class: "ess-switch", checked: overlayOn,
      onchange: e => { update("overlay.enabled", e.target.checked); renderSettings(); } }));
  const chips = overlayOn
    ? el("div", { class: "ess-chips" }, widgets.length
        ? widgets.map(w => el("span", { class: "ess-chip" }, w))
        : el("span", { class: "ess-chip muted" }, "No widgets enabled"))
    : null;
  const editable = overlayOn && config.overlay.layout_mode === "generated";
  const thumb = editable
    ? el("button", { class: "layout-thumb", type: "button", onclick: openLayoutEditor, "aria-label": "Edit overlay layout" },
        document.createElementNS("http://www.w3.org/2000/svg", "svg"),
        el("span", { class: "layout-thumb-cta" }, "Edit layout"))
    : null;

  return el("div", { class: "ess" },
    el("div", { class: "ess-block" },
      el("h4", { class: "ess-heading" }, "Save films to"),
      folderCard({ path: "output.output_dir", caption: "Output", iconName: "film",
        emptyName: "Not chosen yet", emptyHint: "Type a folder path",
        onCommit: v => { update("output.output_dir", v); renderSettings(); } }),
      destRoom()),
    el("div", { class: `ess-block${idle}` },
      el("h4", { class: "ess-heading", id: "ess-res-title" }, "Resolution"),
      segmented("encoding.resolution", [["source", "Same as footage"], ["1440p", "1440p"]], "ess-res-title", overlayOn && !canScale),
      el("p", { class: "ess-hint" }, !overlayOn ? idleNote
        : !canScale ? "1440p needs the generated overlay layout: this one is tied to the footage size."
        : enc.resolution === "1440p" ? "About twice as fast from 4K. A smaller frame needs less bitrate: 15M at 4K looks like 7M at 1440p."
        : "Films keep the camera's frame size.")),
    el("div", { class: `ess-block${idle}` },
      el("h4", { class: "ess-heading", id: "ess-rate-title" }, "Quality"),
      prores ? null : segmented("encoding.rate_control", [["quality", "Constant quality"], ["bitrate", "Target bitrate"]], "ess-rate-title"),
      el("p", { class: "ess-hint" }, !overlayOn ? idleNote
        : prores ? "ProRes HQ has a fixed quality. Pick another encoder under Encoding & performance to choose one."
        : enc.rate_control === "quality" ? "Same look throughout; the file size depends on the footage."
        : "Predictable file size, whatever the footage."),
      prores ? null : renderField(field(enc.rate_control === "quality" ? "encoding.quality" : "encoding.bitrate"))),
    el("div", { class: "ess-block" },
      el("h4", { class: "ess-heading", id: "ess-group-title" }, "One film per"),
      segmented("grouping.group_by", GROUP_CHOICES, "ess-group-title"),
      el("p", { class: "ess-hint" }, GROUP_CHOICES.find(([v]) => v === groupBy)?.[2] || "")),
    el("div", { class: "ess-block" },
      el("h4", { class: "ess-heading" }, "Overlay"),
      el("div", { class: "ess-overlay-card" }, overlay, thumb, chips)));
}

// idPrefix keeps ids unique when the same field is also rendered elsewhere (e.g. the layout editor).
function renderField(f, idPrefix = "f-") {
  const value = get(config, f.path);
  const id = `${idPrefix}${f.path}`;
  const update = v => {
    set(config, f.path, v);
    for (const section of SETTINGS_SECTIONS) {
      const summary = document.querySelector(`[data-settings-id="${section.id}"] > summary > small`);
      if (summary) summary.textContent = sectionSummary(section);
    }
    scheduleSave(f);
  };
  let input;

  switch (f.type) {
    case "bool":
      return el("label", { class: "check field" },
        el("input", { id, type: "checkbox", checked: !!value, onchange: e => { update(e.target.checked); renderSettings(); } }),
        el("span", {}, f.label, f.hint ? el("small", {}, f.hint) : null));
    case "corner": {
      const base = f.path.replace(/\.corner$/, "");
      const custom = get(config, `${base}.position`);
      input = el("select", { id, onchange: e => {
        if (e.target.value === "custom") return;
        set(config, `${base}.position`, null);
        update(e.target.value); renderSettings(); renderPreview();
      } },
        custom ? el("option", { value: "custom", selected: true }, `Custom (dragged: ${Math.round(custom.x * 100)}%, ${Math.round(custom.y * 100)}%)`) : null,
        CORNERS.map(c => el("option", { value: c, selected: !custom && c === value }, c)));
      break;
    }
    case "select":
      input = el("select", { id, onchange: e => { update(e.target.value); renderSettings(); } },
        (typeof f.options === "function" ? f.options() : f.options).map(o => {
          const [v, label] = Array.isArray(o) ? o : [o, o];
          return el("option", { value: v, selected: v === value }, label);
        }));
      break;
    case "number":
      input = el("input", { id, type: "number", value, min: f.min, max: f.max, step: f.step ?? 1,
        oninput: e => e.target.value !== "" && update(Number(e.target.value)) });
      break;
    case "list":
      input = el("input", { id, type: "text", value: (value || []).join(" "),
        oninput: e => update(e.target.value.trim() ? e.target.value.trim().split(/\s+/) : []) });
      break;
    case "folder":
    case "file":
      input = el("div", { class: "picker" },
        el("input", { id, type: "text", value: value || "", placeholder: f.type === "folder" ? "/Volumes/…" : "/path/to/file",
          oninput: e => update(e.target.value) }),
        el("button", { class: "ghost", type: "button", onclick: async () => {
          const res = await api("POST", "/api/pick", { kind: f.type, prompt: f.label });
          if (res.path) { update(res.path); renderSettings(); }
        } }, "Browse…"));
      break;
    default:
      input = el("input", { id, type: "text", value: value ?? "", list: f.list, oninput: e => update(e.target.value) });
  }
  return el("div", { class: "field" }, el("label", { for: id }, f.label), input,
    f.hint ? el("small", {}, f.hint) : null,
    f.showIf && !f.showIf(config) ? el("small", {}, "Saved for later; inactive with the current options.") : null);
}

function scheduleSave(field) {
  $("#save-state").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      config = await api("PUT", "/api/config", config);
      $("#save-state").textContent = "Settings saved";
      refreshEstimates();
      if (!field || field.path.startsWith("overlay")) renderPreview();
    } catch (e) {
      $("#save-state").textContent = "Invalid setting";
      toast(e.message, "error");
    }
  }, 400);
}

// ---------------------------------------------------------------------------------------------- scan
let groups = [];
let scanned = false;

// Size and export time of each film with the current settings and edits, and the room left in the output folder.
let estimates = { films: {}, destination: null };
let estimateSeq = 0;
const fmtEstimate = e => [e?.size ? `≈ ${fmtSize(e.size)}` : null, e?.seconds ? `≈ ${fmtGap(e.seconds)} to export` : null]
  .filter(Boolean).join(" · ");

function destRoom() {
  const d = estimates.destination;
  return el("p", { class: `ess-hint${d && !d.writable ? " warn" : ""}`, id: "dest-room" },
    !d ? "" : d.writable ? `${fmtSize(d.free)} free` : "Cannot write here: drive unplugged or read-only (macOS mounts NTFS read-only)");
}

async function refreshEstimates() {
  const seq = ++estimateSeq;
  let fresh;
  try { fresh = await api("GET", "/api/estimate"); } catch { return; }
  if (seq !== estimateSeq) return;  // a newer request is on its way: this answer has older settings
  estimates = fresh;
  document.querySelectorAll(".day-estimate").forEach(n => { n.textContent = fmtEstimate(estimates.films[n.dataset.key]); });
  $("#dest-room")?.replaceWith(destRoom());
  updateSelection();
}

async function doScan() {
  const btn = $("#btn-scan");
  const feedback = $("#scan-feedback");
  feedback.hidden = true;
  btn.disabled = true; btn.textContent = "Scanning footage…";
  btn.setAttribute("aria-busy", "true");
  try {
    clearTimeout(saveTimer);
    config = await api("PUT", "/api/config", config);
    groups = (await api("POST", "/api/scan")).groups;
    scanned = true;
    renderGroups();
  } catch (e) {
    feedback.textContent = e.message;
    feedback.hidden = false;
    toast(e.message, "error");
  } finally {
    btn.disabled = false; btn.textContent = "⌕  Scan folder";
    btn.removeAttribute("aria-busy");
  }
}

function renderGroups() {
  const root = $("#groups");
  root.replaceChildren();
  const recs = groups.flatMap(g => g.recordings);
  const chapters = recs.reduce((n, r) => n + r.chapters.length, 0);
  const duration = groups.reduce((s, g) => s + g.duration, 0);
  const size = groups.reduce((s, g) => s + g.size, 0);
  const summary = $("#scan-summary");
  const toolbar = $("#groups-toolbar");

  if (!groups.length) {
    pollCopy();
    summary.hidden = false;
    summary.className = "empty-state";
    summary.replaceChildren(
      el("span", { class: "empty-icon", "aria-hidden": "true" }, "!"),
      el("strong", {}, "No GoPro footage found"),
      el("p", {}, "Check the source folder and file-name pattern, then scan again."));
    toolbar.hidden = true;
    $("#export-bar").hidden = true;
    updateSelection();
    return;
  }

  summary.hidden = false;
  summary.className = "summary-stats";
  summary.replaceChildren(
    renderStat(groups.length, groups.length === 1 ? "film" : "films"),
    renderStat(recs.length, recs.length === 1 ? "recording" : "recordings"),
    renderStat(chapters, chapters === 1 ? "chapter file" : "chapter files"),
    renderStat(fmtDuration(duration), "total duration"),
    renderStat(fmtSize(size), "source size"));
  toolbar.hidden = false;
  $("#export-bar").hidden = false;
  $("#group-filter").value = "";
  groups.forEach((g, index) => root.append(renderDayCard(g, index)));
  updateSelection();
  refreshEstimates();
  pollCopy();
}

function renderStat(value, label) {
  return el("div", { class: "stat" }, el("strong", {}, String(value)), el("span", {}, label));
}

function renderDayCard(g, index) {
  const warnings = [];
  if (!g.homogeneous) warnings.push("mixed formats → will re-encode");
  const noGps = g.recordings.filter(r => !r.has_gpmd).length;
  if (noGps && config.overlay.enabled) warnings.push(`${noGps} without GPS stream`);
  const errors = g.recordings.filter(r => r.error).length;
  if (errors) warnings.push(`${errors} unreadable`);
  const edited = g.edit?.edited;

  const search = [g.label, ...g.recordings.flatMap(r => r.chapters)].join(" ").toLowerCase();
  const clips = g.recordings.length, sessions = g.session_count;
  return el("details", { class: "day", open: index === 0, "data-search": search, "data-key": g.key },
    el("summary", {},
      el("input", { type: "checkbox", class: "group-select", "data-key": g.key, checked: true,
        "aria-label": `Select ${fmtDayHeader(g.label)}`, onclick: e => e.stopPropagation(), onchange: updateSelection }),
      el("div", { class: "day-head" },
        el("div", { class: "day-title-row" },
          el("strong", { class: "day-title" }, fmtDayHeader(g.label)),
          edited ? el("span", { class: "badge day-edited", title: "Edited in the editor; the edits are applied on export" },
            `Edited · film ${fmtDuration(g.edit.kept_duration)}`) : null,
          warnings.length ? el("span", { class: "badge warn" }, warnings.join(" · ")) : null),
        el("div", { class: "day-facts" },
          dayFact(fmtDuration(g.duration), "recorded"),
          dayFact(clips, clips === 1 ? "clip" : "clips"),
          dayFact(sessions, sessions === 1 ? "session" : "sessions"),
          g.total_pause_seconds ? dayFact(fmtGap(g.total_pause_seconds), "paused", "pause") : null,
          dayFact(fmtSize(g.size), ""),
          el("span", { class: "day-fact day-estimate", "data-key": g.key }, fmtEstimate(estimates.films[g.key]))),
        renderTimeline(g)),
      el("button", { class: `button preview-day${edited ? " edited" : ""}`, type: "button",
        title: edited ? "Reopen the editor to change the cuts" : "Watch every clip back to back, trim and remove parts",
        onclick: e => { e.preventDefault(); e.stopPropagation(); openEditor(g); } },
        icon(edited ? "scissors" : "play", "preview-day-icon"),
        el("span", {}, edited ? "Edit film" : "Preview & edit"))),
    renderRecordingList(g));
}

function dayFact(value, label, tone = "") {
  return el("span", { class: `day-fact${tone ? ` ${tone}` : ""}` }, el("b", {}, String(value)), label ? ` ${label}` : null);
}

/** Re-renders one day card after its film was edited, keeping it open/selected as it was. */
function refreshDayCard(g) {
  const old = document.querySelector(`.day[data-key="${CSS.escape(g.key)}"]`);
  if (!old) return;
  const card = renderDayCard(g, groups.indexOf(g));
  card.open = old.open;
  card.hidden = old.hidden;
  card.querySelector(".group-select").checked = old.querySelector(".group-select").checked;
  old.replaceWith(card);
  refreshEstimates();
}

function renderTimeline(g) {
  if (!g.first_start || !g.last_end) return null;
  const t0 = Date.parse(g.first_start), total = Math.max(1, Date.parse(g.last_end) - t0);
  const pct = ms => Math.min(100, Math.max(0, (ms / total) * 100));
  const blocks = g.recordings.filter(r => r.start).map(r => {
    const left = pct(Date.parse(r.start) - t0);
    const width = Math.max(0.4, pct(r.duration * 1000));
    const title = `${r.chapters.join(", ")}\n${fmtClock(r.start)}–${fmtClock(r.end)} · ${fmtDuration(r.duration)}`;
    return el("div", { class: `tl-block${r.error ? " err" : ""}`, style: `left:${left}%;width:${width}%`, title });
  });
  return el("div", { class: "day-span" },
    el("span", { class: "day-span-time" }, fmtClock(g.first_start)),
    el("div", { class: "timeline" }, blocks),
    el("span", { class: "day-span-time" }, fmtClock(g.last_end)));
}

function renderRecordingList(g) {
  const rows = [];
  g.recordings.forEach((r, i) => {
    if (i > 0) {
      const label = r.continuous ? "continuous · no pause" : r.gap_before != null ? `pause · ${fmtGap(r.gap_before)}` : "gap (unknown)";
      rows.push(el("div", { class: `rec-sep ${r.continuous ? "cont" : "pause"}` }, el("span", {}, label)));
    }
    rows.push(renderRecordingRow(r, false, () => openEditor(g, i), recordingEdit(g, r)));
  });
  if (g.duplicates.length) rows.push(el("div", { class: "rec-sep dup" }, el("span", {}, "duplicates · skipped")));
  for (const d of g.duplicates) rows.push(renderRecordingRow(d, true));
  return el("div", { class: "rec-list" }, rows);
}

/** How the editor changed a recording: null when untouched, else { removed, note }. */
function recordingEdit(g, r) {
  if (!g.edit?.edited || r.error) return null;
  const cuts = g.edit.cuts.filter(c => c.rec === r.key);
  const kept = cuts.reduce((sum, c) => sum + c.end - c.start, 0);
  if (!cuts.length) return { removed: true, note: "left out of the film" };
  return kept < r.duration - 0.05 ? { removed: false, note: `trimmed to ${fmtDuration(kept)}` } : null;
}

function renderRecordingRow(r, duplicate = false, onPreview = null, edit = null) {
  const chapterNote = r.chapter_count > 1 ? `${r.chapter_count} chapters, joined seamlessly` : null;
  return el("div", { class: `rec-row${duplicate ? " duplicate" : ""}${r.error ? " err" : ""}${edit?.removed ? " removed" : ""}` },
    renderThumb(r, onPreview),
    el("div", { class: "rec-main" },
      el("div", { class: "rec-headline" },
        el("span", { class: "rec-time" }, r.end ? `${fmtClock(r.start)} – ${fmtClock(r.end)}` : fmtClock(r.start)),
        el("span", { class: "rec-dur" }, fmtDuration(r.duration)),
        edit ? el("span", { class: "rec-edit" }, edit.note) : null),
      el("div", { class: "rec-name" }, r.chapters.join(", "),
        chapterNote ? el("span", { class: "rec-note" }, ` · ${chapterNote}`) : null,
        duplicate ? el("span", { class: "rec-note" }, ` · duplicate of ${r.duplicate_of}`) : null)),
    r.error
      ? el("span", { class: "rec-meta" }, r.error)
      : el("div", { class: "rec-meta" },
          el("span", { class: "rec-tags" },
            el("span", { class: "tag", title: r.resolution }, resLabel(r.resolution)),
            el("span", { class: "tag" }, (r.vcodec || "").toUpperCase()),
            el("span", { class: "tag" }, fps(r.fps)),
            el("span", { class: `tag${r.has_gpmd ? " ok" : " off"}` }, r.has_gpmd ? "GPS" : "no GPS")),
          el("span", { class: "rec-size" }, fmtSize(r.size))));
}

const RES_LABELS = { 5312: "5.3K", 4000: "4K", 3840: "4K", 2704: "2.7K", 1920: "1080p", 1280: "720p" };
const resLabel = res => RES_LABELS[parseInt(res, 10)] || res || "?";

const fps = r => {
  const [a, b] = (r || "").split("/").map(Number);
  return a && b ? `${(a / b).toFixed(2).replace(/\.00$/, "")}fps` : "";
};

async function processSelected() {
  const keys = [...document.querySelectorAll(".group-select:checked")].map(c => c.dataset.key);
  if (!keys.length) return toast("Select at least one video", "error");
  const btn = $("#btn-process");
  btn.disabled = true;
  btn.textContent = "Adding to queue…";
  try {
    clearTimeout(saveTimer);
    config = await api("PUT", "/api/config", config);
    const copyFirst = !$("#copy-first-row").hidden && $("#copy-first").checked;
    await api("POST", "/api/jobs", { keys, copy_first: copyFirst });
    toast(`${keys.length} ${keys.length === 1 ? "film" : "films"} added to the export queue${copyFirst ? ": each one copies its footage to this Mac first" : ""}`);
    pollCopy();
    pollJobs();
    goToStep(2);
  } catch (e) {
    toast(e.message, "error");
    refreshEstimates();
  } finally {
    btn.textContent = "Process selected";
    updateSelection();
  }
}

function updateSelection() {
  const checks = [...document.querySelectorAll(".group-select")];
  const selected = checks.filter(c => c.checked).length;
  const all = $("#select-all");
  all.checked = checks.length > 0 && selected === checks.length;
  all.indeterminate = selected > 0 && selected < checks.length;
  $("#selection-count").textContent = checks.length ? `${selected} of ${checks.length} selected` : "";
  const picked = checks.filter(c => c.checked).map(c => estimates.films[c.dataset.key]).filter(Boolean);
  const total = { size: picked.reduce((s, e) => s + (e.size || 0), 0), seconds: picked.reduce((s, e) => s + (e.seconds || 0), 0) };
  const d = estimates.destination;
  const blocked = selected > 0 && !!d && (!d.writable || total.size > d.free);
  $("#btn-process").disabled = selected === 0;  // `blocked` only warns: the server checks again when you submit
  $("#btn-process").textContent = selected ? `Process ${selected} selected` : "Process selected";
  $("#process-title").textContent = selected ? `${selected} ${selected === 1 ? "film" : "films"} ready` : "Ready to export?";
  $("#process-hint").textContent = !selected ? "Select at least one film from the scanned footage."
    : !blocked ? fmtEstimate(total)
    : d.writable ? `Needs ≈ ${fmtSize(total.size)}, but the output folder has ${fmtSize(d.free)} free` : "The output folder cannot be written";
  $("#process-hint").classList.toggle("warn", blocked);
  renderExportOverlay();
}

function renderExportOverlay() {
  const line = $("#process-overlay");
  if (!config.overlay.enabled) return line.replaceChildren("No overlay · lossless join, the fastest export");
  const widgets = WIDGET_NAMES.filter(([key]) => config.overlay[key].enabled).map(([, label]) => label);
  line.replaceChildren(`Overlay · ${widgets.length ? widgets.join(", ") : "no widgets"}`,
    config.overlay.layout_mode === "generated"
      ? el("button", { class: "link-button", type: "button", onclick: openLayoutEditor }, "Edit layout") : null);
}

function filterGroups() {
  const query = $("#group-filter").value.trim().toLowerCase();
  document.querySelectorAll(".day").forEach(day => { day.hidden = !!query && !day.dataset.search.includes(query); });
}

function toggleAllGroups() {
  const days = [...document.querySelectorAll(".day:not([hidden])")];
  const shouldOpen = days.some(day => !day.open);
  days.forEach(day => { day.open = shouldOpen; });
  $("#btn-expand-groups").textContent = shouldOpen ? "Collapse all" : "Expand all";
}

// ---------------------------------------------------------------------------------------------- jobs
let pollTimer = null;

async function pollJobs() {
  clearTimeout(pollTimer);
  let jobs = [];
  try { jobs = await api("GET", "/api/jobs"); } catch { /* server restarting */ }
  renderJobs(jobs);
  const active = jobs.some(j => j.status === "running" || j.status === "queued");
  pollTimer = setTimeout(pollJobs, active ? 1000 : 5000);
}

// Leftover export work files; sized only when the number of active jobs changes, not on every poll.
let cacheActive = null;
async function refreshCache(active) {
  cacheActive = active;
  const button = $("#btn-cache");
  if (active) return void (button.hidden = true);
  try {
    const { size } = await api("GET", "/api/cache");
    button.hidden = !size;
    button.textContent = `Clear cache · ${fmtSize(size)}`;
    button.dataset.size = fmtSize(size);
  } catch { button.hidden = true; }
}

async function clearCache() {
  if (!confirm(`Delete ${$("#btn-cache").dataset.size} of leftover export work files? Exported films and footage copied to this Mac are kept.`)) return;
  try { await api("DELETE", "/api/cache"); toast("Cache cleared"); } catch (e) { toast(e.message, "error"); }
  refreshCache(cacheActive);
}

function renderJobs(jobs) {
  const root = $("#jobs");
  const clear = $("#btn-clear");
  clear.disabled = !jobs.some(j => ["done", "failed", "cancelled"].includes(j.status));
  const active = jobs.filter(j => j.status === "running" || j.status === "queued").length;
  if (active !== cacheActive) refreshCache(active);
  $("#step-jobs-count").hidden = !active;
  $("#step-jobs-count").textContent = active;
  if (!jobs.length) {
    root.className = "empty-state compact-empty";
    root.replaceChildren(
      el("span", { class: "empty-icon", "aria-hidden": "true" }, "↗"),
      el("strong", {}, "No exports yet"),
      el("p", {}, "Completed and active jobs will appear here."));
    return;
  }
  root.className = "";
  root.replaceChildren(...jobs.map(j => {
    const pct = Math.round(j.progress * 100);
    const elapsed = j.started ? ((j.finished || Date.now() / 1000) - j.started) : 0;
    const eta = j.status === "running" && j.progress > 0.02 ? elapsed / j.progress - elapsed : null;
    return el("div", { class: `job ${j.status}` },
      el("div", { class: "job-head" },
        el("strong", {}, j.group),
        el("span", { class: `badge ${j.status}` }, j.cancelling ? "stopping" : j.status),
        el("span", { class: "job-meta" }, `${j.recordings} rec · ${fmtDuration(j.duration)}`),
        j.status === "queued" && j.copy ? el("span", { class: "job-meta" }, copyNote(j.copy)) : null,
        el("span", { class: "spacer" }),
        el("span", { class: "job-meta" }, j.started ? `${fmtDuration(elapsed)} elapsed${eta ? ` · ~${fmtDuration(eta)} left` : ""}` : ""),
        el("button", { class: "ghost", onclick: () => openLog(j) }, "View log"),
        j.status === "done" ? el("button", { class: "ghost", onclick: () => api("POST", "/api/reveal", { path: j.output }) }, "Show in Finder") : null,
        (j.status === "running" || j.status === "queued") ? el("button", { class: "ghost danger", disabled: j.cancelling, onclick: async e => { e.currentTarget.disabled = true; e.currentTarget.textContent = "Stopping…"; await api("POST", `/api/jobs/${j.id}/cancel`); pollJobs(); } }, j.cancelling ? "Stopping…" : "Cancel") : null),
      j.status === "running" ? renderJobLive(j, elapsed, eta)
        : el("div", { class: "bar" }, el("div", { style: `width:${j.status === "queued" && j.copy ? Math.round(copyFrac(j.copy) * 100) : pct}%` })),
      j.status === "done" ? renderJobDone(j, elapsed) : null,
      j.error ? el("div", { class: "error" }, j.error) : null,
      j.output && j.status === "done" ? el("div", { class: "muted small output-path" }, j.output) : null);
  }));
}

const PHASES = { copy: "Copy to Mac", join: "Join chapters", overlay: "Draw overlay", concat: "Final video" };
const copyFrac = c => c.total ? c.copied / c.total : 1;
// Bytes per second over the last ~8 s at each sample (they are 2 s apart): follows a card that slows down, which an
// average since the start would hide for minutes.
function copySpeeds(c) {
  const h = c.history || [], out = [];
  for (let i = 1; i < h.length; i++) {
    let s = i - 1;
    while (s > 0 && h[i][0] - h[s][0] < 8) s--;
    out.push((h[i][1] - h[s][1]) / (h[i][0] - h[s][0]));
  }
  return out;
}
const copySpeed = c => copySpeeds(c).at(-1) || 0;
const copyLeft = c => copySpeed(c) ? (c.total - c.copied) / copySpeed(c) : null;
const copyNote = c => ({
  pending: "Copies to this Mac after the films before it",
  waiting: "Waiting for room on this Mac to copy",
  copying: `Copying to this Mac · ${Math.round(copyFrac(c) * 100)}%${copySpeed(c) ? ` · ${fmtSize(copySpeed(c))}/s · ~${fmtDuration(copyLeft(c))} left` : ""}`,
  done: "Copied to this Mac · waiting its turn",
  failed: "The copy to this Mac failed",
})[c.state];
const fmtSpeed = x => x >= 10 ? `${Math.round(x)}×` : `${x.toFixed(1)}×`;
const svgBox = (cls, markup, label) => {
  const box = el("div", { class: cls, role: "img", "aria-label": label });
  box.innerHTML = markup;  // numbers only, built below
  return box;
};

// Footage seconds per wall-clock second over ~30 s of history (samples are 5 s apart). The overlay reports whole
// percents (~20 s of footage per tick), so the rate is measured between samples where the footage moved: a fixed
// window would alternate between bursts and zeros. A stall longer than the window still drops the rate.
function throughput(history, window = 30) {
  const out = [], moved = [0];  // indexes of samples where the footage done changed
  for (let i = 1; i < history.length; i++) {
    if (history[i][1] !== history[i - 1][1]) moved.push(i);
    const e = moved.at(-1);
    const s = moved.findLast(k => history[e][0] - history[k][0] >= window) ?? 0;
    const end = history[i][0] - history[e][0] >= window ? i : e;
    const [t0, d0] = history[s], [t1] = history[end], d1 = history[e][1];
    if (t1 > t0) out.push(Math.max(0, (d1 - d0) / (t1 - t0)));
  }
  return out;
}

function renderJobLive(j, elapsed, eta) {
  const copying = j.copy && j.copy.state !== "done";  // the first phase: the export starts once the footage is here
  const progress = copying ? copyFrac(j.copy) : j.progress;
  const pct = Math.round(progress * 100);
  const pieces = j.tasks.filter(t => t.id !== "group");
  const final = j.tasks.find(t => t.id === "group");
  const phases = [...(j.copy ? ["copy"] : []), j.overlay ? "overlay" : "join", "concat"];  // overlay renders straight from the chapters
  // clips that fall back to no overlay never report it, so the final concat closes every earlier phase
  const isDone = t => t.finished || (t.id !== "group" && final.stage === "concat");
  const isWorking = t => !isDone(t) && t.stage !== "pending" && t.progress < 1;  // stage done = waiting for the final concat
  const phaseDone = ph => ph === "copy" ? !copying : ph === "concat" ? final.finished : final.stage === "concat" ||
    pieces.every(t => t.stages.indexOf(t.stage) > t.stages.indexOf(ph) || (t.stage === ph && t.progress >= 1));
  const current = phases.findIndex(ph => !phaseDone(ph));
  const speeds = throughput(j.history);
  const speed = speeds.length ? speeds.slice(-3).reduce((a, b) => a + b, 0) / Math.min(3, speeds.length) : 0;

  const r = 34, c = 2 * Math.PI * r;
  const ring = svgBox("job-ring", `<svg viewBox="0 0 80 80"><circle cx="40" cy="40" r="${r}" class="ring-track"/>` +
    `<circle cx="40" cy="40" r="${r}" class="ring-fill" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - progress)}"/></svg>` +
    `<b>${pct}<small>%</small></b>`, `${pct}% done`);

  const stat = (value, label, cls = "") => el("div", { class: `job-stat ${cls}` }, el("b", {}, value), el("span", {}, label));
  const stats = copying ? el("div", { class: "job-stats" },
    stat(fmtDuration(elapsed), "Elapsed"),
    stat(copyLeft(j.copy) ? `~${fmtDuration(copyLeft(j.copy))}` : "—", "Copy left"),
    stat(copySpeed(j.copy) ? `${fmtSize(copySpeed(j.copy))}/s` : "—", "Copy speed", "accent"),
    stat(fmtSize(j.copy.copied), `of ${fmtSize(j.copy.total)} copied`)) : el("div", { class: "job-stats" },
    stat(fmtDuration(elapsed), "Elapsed"),
    stat(eta ? `~${fmtDuration(eta)}` : "—", "Left"),
    stat(speed ? fmtSpeed(speed) : "—", "Speed · footage/time", "accent"),
    stat(fmtDuration(j.progress * final.seconds), `of ${fmtDuration(final.seconds)} footage`));

  const stepper = el("ol", { class: "job-phases" }, phases.map((ph, i) =>
    el("li", { class: i < current || current < 0 ? "done" : i === current ? "current" : "", "aria-current": i === current ? "step" : null },
      el("span", { class: "phase-dot", "aria-hidden": "true" }, i < current || current < 0 ? "✓" : i + 1), PHASES[ph])));

  const spark = copying ? sparkChart(copySpeeds(j.copy), "Copy speed", v => `${fmtSize(v)}/s`)
    : sparkChart(speeds, "Throughput", fmtSpeed, " realtime");

  const lane = t => {
    const idx = t.stages.indexOf(t.stage);
    const state = isDone(t) ? "done" : isWorking(t) ? "running" : "pending";
    return el("div", { class: `lane ${state}` },
      el("span", { class: "lane-label", title: t.label }, t.label),
      el("div", { class: "lane-bar" }, t.stages.map((st, i) => {
        const fill = idx > i || state === "done" ? 1 : idx === i ? t.progress : 0;
        return el("div", { class: `seg seg-${st} ${idx === i && state === "running" ? "live" : ""}`, title: `${PHASES[st]} ${Math.round(fill * 100)}%` },
          el("i", { style: `width:${fill * 100}%` }));
      })),
      el("span", { class: "lane-pct" }, state === "done" ? "✓" : `${idx >= 0 ? Math.round(t.progress * 100) : 0}%`),
      el("span", { class: "lane-speed" }, state === "running" && t.speed > 0 ? fmtSpeed(t.speed) : ""),
      el("span", { class: "lane-stage" }, state === "running" ? PHASES[t.stage] : state === "done" ? (t.finished ? fmtDuration(t.finished - t.started) : "Done") : "Waiting"));
  };
  const running = pieces.filter(isWorking).length;
  const finished = pieces.filter(isDone).length;

  return el("div", { class: "job-live" },
    el("div", { class: "job-hero" }, ring, el("div", { class: "job-hero-main" }, stepper,
      copying || j.activity ? el("p", { class: "job-activity", "aria-live": "polite" }, el("span", {}, "Now"),
        copying ? copyNote(j.copy) : j.activity) : null, stats)),
    spark,
    el("div", { class: "lanes" },
      el("div", { class: "lanes-head" },
        el("span", {}, "Clips"),
        el("span", { class: "lanes-legend" },
          el("span", {}, el("i", { class: "dot run" }), `${running} working`),
          el("span", {}, el("i", { class: "dot ok" }), `${finished}/${pieces.length} done`),
          el("span", {}, `${j.parallel} at a time`))),
      pieces.map(lane),
      lane(final)));
}

function sparkChart(speeds, title, fmt, unit = "") {
  if (speeds.length < 2) return null;
  // the lossless join runs far faster than the overlay; cap the scale so it does not flatten the rest
  const sorted = [...speeds].sort((a, b) => a - b), max = sorted.at(-1);
  const top = Math.max(0.1, Math.min(max, sorted[Math.floor(sorted.length / 2)] * 1.6)), w = 300, h = 44;
  const pts = speeds.map((v, i) => `${(i / (speeds.length - 1) * w).toFixed(1)},${(h - 2 - Math.min(v, top) / top * (h - 6)).toFixed(1)}`);
  return el("div", { class: "job-spark" },
    el("div", { class: "spark-head" }, el("span", {}, title), el("span", {}, `peak ${fmt(max)} · now ${fmt(speeds.at(-1))}`)),
    svgBox("spark-chart", `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<polygon class="spark-area" points="0,${h} ${pts.join(" ")} ${w},${h}"/>` +
      `<polyline class="spark-line" points="${pts.join(" ")}" vector-effect="non-scaling-stroke"/></svg>`,
      `${title} over time, now ${fmt(speeds.at(-1))}${unit}`));
}

function renderJobDone(j, elapsed) {
  const final = j.tasks.find(t => t.id === "group");
  return el("div", { class: "job-stats done" },
    el("div", { class: "job-stat" }, el("b", {}, fmtDuration(elapsed)), el("span", {}, "Took")),
    el("div", { class: "job-stat" }, el("b", {}, fmtDuration(final.seconds)), el("span", {}, "Footage")),
    el("div", { class: "job-stat accent" }, el("b", {}, elapsed ? fmtSpeed(final.seconds / elapsed) : "—"), el("span", {}, "Average speed")));
}

let logTimer = null;
async function openLog(job) {
  const dialog = $("#log-dialog"), body = $("#log-body");
  $("#log-title").textContent = `Log · ${job.group}`;
  body.textContent = "";
  let next = 0;
  const tick = async () => {
    if (!dialog.open) return;
    const res = await api("GET", `/api/jobs/${job.id}/log?since=${next}`);
    const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    body.textContent += res.lines.map(l => l + "\n").join("");
    next = res.next;
    if (atBottom) body.scrollTop = body.scrollHeight;
    logTimer = setTimeout(tick, 1000);
  };
  dialog.showModal();
  clearTimeout(logTimer);
  tick();
}

async function openDoctor() {
  const list = $("#doctor-body");
  list.replaceChildren(el("li", {}, "Checking…"));
  $("#doctor-dialog").showModal();
  const checks = await api("GET", "/api/doctor");
  list.replaceChildren(...checks.map(c => el("li", { class: c.ok ? "ok" : "bad" },
    el("span", {}, c.ok ? "✓" : "✗"), el("strong", {}, c.name), el("small", {}, c.detail))));
}

// ---------------------------------------------------------------------------------------------- boot
async function boot() {
  [config, OPTIONS] = await Promise.all([api("GET", "/api/config"), api("GET", "/api/options")]);
  renderSettings();
  renderSource();
  renderPreview();
  pollJobs();
  pollCopy();
  $("#btn-scan").onclick = doScan;
  $("#btn-process").onclick = processSelected;
  document.querySelectorAll(".steps button").forEach(b => { b.onclick = () => goToStep(b.dataset.step); });
  $("#btn-back").onclick = () => goToStep(1);
  goToStep(1);
  refreshEstimates();
  window.addEventListener("focus", refreshEstimates);  // a drive may have been plugged in meanwhile
  $("#btn-clear").onclick = async () => { await api("DELETE", "/api/jobs"); pollJobs(); };
  $("#btn-cache").onclick = clearCache;
  $("#btn-doctor").onclick = openDoctor;
  $("#btn-reset").onclick = async () => {
    if (!confirm("Reset all settings to defaults (folders are kept)?")) return;
    config = await api("POST", "/api/config/reset");
    renderSettings(); renderSource(); renderPreview();
  };
  $("#preview-res").onchange = renderPreview;
  $("#select-all").onchange = e => {
    document.querySelectorAll(".group-select").forEach(c => { c.checked = e.target.checked; });
    updateSelection();
  };
  $("#group-filter").oninput = filterGroups;
  $("#btn-expand-groups").onclick = toggleAllGroups;
  document.addEventListener("film-edit", e => refreshDayCard(e.detail.group));
  $("#settings-search").oninput = e => { settingsQuery = e.target.value; renderSettings(); $("#settings").scrollTop = 0; };
  const clearSearch = () => { settingsQuery = ""; $("#settings-search").value = ""; renderSettings(); $("#settings-search").focus(); };
  $("#settings-search-clear").onclick = clearSearch;
  $("#settings-search").onkeydown = e => { if (e.key === "Escape") clearSearch(); };
}

boot().catch(e => toast(e.message, "error"));
