"use strict";
/* Interactive overlay preview.
 *
 * Geometry comes from /api/layout-preview, which runs the same code that writes the render layout XML
 * (text measured with the real font), so what you see and move here is what gets rendered.
 * Edits are stored as `position` (fraction of the frame) and size values in the config.
 */

const WIDGETS = {
  datetime: { label: "Date & time", sizePath: "overlay.datetime.font_size", min: 8, max: 400 },
  minimap: { label: "Mini map", sizePath: "overlay.minimap.size", min: 64, max: 2000 },
  journey_map: { label: "Journey map", sizePath: "overlay.journey_map.size", min: 64, max: 2000 },
  speed: { label: "Speed", sizePath: "overlay.speed.font_size", min: 8, max: 400 },
};
const SVG_NS = "http://www.w3.org/2000/svg";

const preview = { w: 0, h: 0, widgets: [], selected: null, drag: null, fontLoaded: null };

function svgEl(parent, tag, attrs = {}, text) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  parent.append(n);
  return n;
}

async function ensureOverlayFont() {
  // Draw preview text with the exact font file used for rendering (falls back to Arial if the browser can't load it).
  const path = config.overlay.font;
  if (preview.fontLoaded === path) return;
  preview.fontLoaded = path;
  try {
    const face = new FontFace("OverlayFont", `url(/api/font?v=${encodeURIComponent(path)})`);
    await face.load();
    for (const f of [...document.fonts]) if (f.family === "OverlayFont") document.fonts.delete(f);
    document.fonts.add(face);
  } catch { /* unsupported font format (e.g. .ttc): keep the Arial fallback */ }
}

async function renderPreview() {
  if (!config) return;
  const [w, h] = $("#preview-res").value.split("x").map(Number);
  Object.assign(preview, { w, h });
  const svg = $("#preview");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.parentElement.style.setProperty("--ar", w / h);

  const fontReady = ensureOverlayFont();
  const data = config.overlay.enabled ? await api("GET", `/api/layout-preview?width=${w}&height=${h}`) : null;
  await fontReady;
  if (preview.drag) return; // a drag started while loading: don't pull the rug

  svg.replaceChildren();
  svgEl(svg, "rect", { x: 0, y: 0, width: w, height: h, class: "frame" });
  preview.widgets = data?.widgets || [];

  const message = !data ? "Overlay disabled"
    : data.mode === "builtin" ? "Library default layout (not editable here)"
    : data.mode === "xml" ? null : !preview.widgets.length ? "No widgets enabled" : null;
  if (message) svgEl(svg, "text", { x: w / 2, y: h / 2, "text-anchor": "middle", "font-size": h / 24, class: "label" }, message);
  if (data?.mode === "xml") drawCustomXml(svg, data.xml);

  svgEl(svg, "g", { id: "guides" });
  for (const wd of preview.widgets) drawWidget(svg, wd);
  renderWidgetPanel();
  syncLayoutThumbs();
}

function drawWidget(svg, wd) {
  const g = svgEl(svg, "g", { class: `widget${preview.selected === wd.key ? " selected" : ""}`, "data-key": wd.key });
  const p = wd.props;
  if (wd.kind === "moving_map" || wd.kind === "journey_map") {
    svgEl(g, "rect", { x: wd.x, y: wd.y, width: wd.w, height: wd.h, rx: p.corner_radius, class: wd.kind === "moving_map" ? "map" : "journey", opacity: p.opacity });
    if (wd.kind === "moving_map") {
      svgEl(g, "circle", { cx: wd.x + wd.w / 2, cy: wd.y + wd.h / 2, r: Math.max(3, wd.w / 30), class: "dot" });
    } else {
      // sample route: in progressive mode only the first part is drawn (upcoming part faded or hidden)
      const { x, y, w: s } = wd;
      const d = `M${x + s * .2} ${y + s * .75} Q ${x + s * .4} ${y + s * .2} ${x + s * .6} ${y + s * .5} T ${x + s * .85} ${y + s * .25}`;
      const line = { d, fill: "none", stroke: `rgb(${p.line_color})`, "stroke-width": p.line_width, "stroke-linecap": "round", "stroke-linejoin": "round" };
      if (p.mode === "progressive") {
        if (p.upcoming_opacity > 0) svgEl(g, "path", { ...line, opacity: p.upcoming_opacity });
        const done = svgEl(g, "path", { ...line, pathLength: 100, "stroke-dasharray": "55 100" });
        const len = done.getTotalLength();
        const at = done.getPointAtLength(len * 0.55);
        svgEl(g, "circle", { cx: at.x, cy: at.y, r: p.marker_size, fill: `rgb(${p.marker_color})`, stroke: "#000" });
      } else {
        svgEl(g, "path", line);
        const at = { x: x + s * .85, y: y + s * .25 };
        svgEl(g, "circle", { cx: at.x, cy: at.y, r: p.marker_size, fill: `rgb(${p.marker_color})`, stroke: "#000" });
      }
    }
  } else {
    for (const t of wd.texts) {
      svgEl(g, "text", {
        x: t.x, y: t.y + t.ascent, "font-size": t.size, class: "overlay-text",
        "text-anchor": t.align === "right" ? "end" : "start",
        fill: `rgb(${p.color})`, stroke: `rgb(${p.outline})`, "stroke-width": 2 * p.outline_width,
      }, t.text);
    }
  }
  // constant on-screen size (~16px) whatever the preview resolution
  const handle = 16 * preview.w / Math.max(1, $("#preview").getBoundingClientRect().width);
  svgEl(g, "rect", { x: wd.x, y: wd.y, width: wd.w, height: wd.h, class: "hit", "stroke-width": Math.max(1, preview.w / 700) });
  svgEl(g, "rect", { x: wd.x + wd.w - handle / 2, y: wd.y + wd.h - handle / 2, width: handle, height: handle, class: "handle" });
  const tag = svgEl(g, "text", { x: wd.x, y: wd.y - preview.h / 120, "font-size": Math.max(12, preview.h / 50), class: "tag" }, WIDGETS[wd.key].label);
  if (wd.y < preview.h / 25) tag.setAttribute("y", wd.y + wd.h + preview.h / 35);
}

function drawCustomXml(svg, xml) {
  // Read-only outline of a user-provided layout: positions only, no editing (we don't rewrite user files).
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  const walk = (node, ox, oy) => {
    for (const c of node.children) {
      const x = ox + Number(c.getAttribute("x") || 0), y = oy + Number(c.getAttribute("y") || 0);
      if (c.tagName === "composite" || c.tagName === "translate" || c.tagName === "frame") { walk(c, x, y); continue; }
      const size = Number(c.getAttribute("size") || 16);
      const type = c.getAttribute("type") || "";
      if (type.includes("map")) svgEl(svg, "rect", { x, y, width: size, height: size, class: "map", opacity: 0.7 });
      else svgEl(svg, "text", { x, y: y + size * 0.9, "font-size": size, class: "overlay-text", fill: "#fff",
        "text-anchor": c.getAttribute("align") === "right" ? "end" : "start" }, c.textContent || type);
    }
  };
  walk(doc.documentElement, 0, 0);
}

// ------------------------------------------------------------------------------------------------ interaction

function toSvgPoint(evt) {
  const svg = $("#preview");
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function snapCandidates(size, total, margin) {
  return [margin, total - margin - size, (total - size) / 2, 0, total - size];
}

function snap(value, size, total, margin, threshold) {
  let best = null;
  for (const c of snapCandidates(size, total, margin)) {
    if (Math.abs(value - c) <= threshold && (best === null || Math.abs(value - c) < Math.abs(value - best))) best = c;
  }
  return best;
}

function drawGuides(xs, ys) {
  const g = $("#guides");
  if (!g) return;
  g.replaceChildren();
  const sw = Math.max(1, preview.w / 800);
  for (const x of xs) svgEl(g, "line", { x1: x, y1: 0, x2: x, y2: preview.h, class: "guide", "stroke-width": sw });
  for (const y of ys) svgEl(g, "line", { x1: 0, y1: y, x2: preview.w, y2: y, class: "guide", "stroke-width": sw });
}

function onPointerDown(evt) {
  const g = evt.target.closest(".widget");
  if (evt.button !== 0) return;
  if (!g) { select(null); return; } // clicking empty frame deselects
  const wd = preview.widgets.find(w => w.key === g.dataset.key);
  select(wd.key);
  const start = toSvgPoint(evt);
  preview.drag = { wd, g, start, mode: evt.target.classList.contains("handle") ? "resize" : "move", dx: 0, dy: 0, k: 1, moved: false };
  $("#preview").setPointerCapture(evt.pointerId);
  $("#preview").focus({ preventScroll: true });
  evt.preventDefault();
}

function onPointerMove(evt) {
  const d = preview.drag;
  if (!d) return;
  const p = toSvgPoint(evt);
  const { wd } = d;
  const margin = Math.round(config.overlay.margin * preview.w / 1920);
  const threshold = preview.w / 100;
  d.moved = d.moved || Math.hypot(p.x - d.start.x, p.y - d.start.y) > preview.w / 400;
  if (!d.moved) return;

  if (d.mode === "move") {
    let x = Math.min(Math.max(0, wd.x + p.x - d.start.x), preview.w - wd.w);
    let y = Math.min(Math.max(0, wd.y + p.y - d.start.y), preview.h - wd.h);
    const guidesX = [], guidesY = [];
    if (!evt.altKey) { // hold Alt to move freely without snapping
      const sx = snap(x, wd.w, preview.w, margin, threshold);
      const sy = snap(y, wd.h, preview.h, margin, threshold);
      if (sx !== null) { x = sx; guidesX.push(sx === (preview.w - wd.w) / 2 ? preview.w / 2 : sx < preview.w / 2 ? sx : sx + wd.w); }
      if (sy !== null) { y = sy; guidesY.push(sy === (preview.h - wd.h) / 2 ? preview.h / 2 : sy < preview.h / 2 ? sy : sy + wd.h); }
    }
    d.dx = x - wd.x; d.dy = y - wd.y;
    d.g.setAttribute("transform", `translate(${d.dx} ${d.dy})`);
    drawGuides(guidesX, guidesY);
  } else {
    // uniform scale from the top-left corner, keeping the widget inside the frame
    const kx = (wd.w + p.x - d.start.x) / wd.w, ky = (wd.h + p.y - d.start.y) / wd.h;
    const maxK = Math.min((preview.w - wd.x) / wd.w, (preview.h - wd.y) / wd.h);
    const info = WIDGETS[wd.key];
    const base = get(config, info.sizePath);
    d.k = Math.min(Math.max(Math.max(kx, ky), info.min / base, 0.05), maxK, info.max / base);
    d.g.setAttribute("transform", `translate(${wd.x} ${wd.y}) scale(${d.k}) translate(${-wd.x} ${-wd.y})`);
    renderToolbar(Math.round(base * d.k));
  }
}

function onPointerUp() {
  const d = preview.drag;
  if (!d) return;
  preview.drag = null;
  drawGuides([], []);
  if (!d.moved) return;
  const { wd } = d;
  const base = wd.key === "minimap" ? "overlay.minimap" : `overlay.${wd.key}`;
  if (d.mode === "move") {
    setPosition(base, wd.x + d.dx, wd.y + d.dy);
  } else {
    const info = WIDGETS[wd.key];
    set(config, info.sizePath, Math.round(get(config, info.sizePath) * d.k));
    setPosition(base, wd.x, wd.y); // keep the top-left corner where it is
  }
  commitLayoutChange();
}

function setPosition(base, x, y) {
  const round = v => Math.round(v * 10000) / 10000;
  set(config, `${base}.position`, { x: round(x / preview.w), y: round(y / preview.h) });
}

async function commitLayoutChange() {
  // Save right away (no debounce): the preview geometry is computed by the server from the saved config.
  clearTimeout(saveTimer);
  renderSettings();
  try {
    config = await api("PUT", "/api/config", config);
    $("#save-state").textContent = "Settings saved";
  } catch (e) {
    toast(e.message, "error");
  }
  renderSettings();
  await renderPreview();
}

function select(key) {
  preview.selected = key;
  document.querySelectorAll("#preview .widget").forEach(g => g.classList.toggle("selected", g.dataset.key === key));
  renderToolbar();
}

function resetToCorner(key) {
  set(config, `overlay.${key}.position`, null);
  commitLayoutChange();
}

function onKeyDown(evt) {
  const wd = preview.widgets.find(w => w.key === preview.selected);
  if (!wd) return;
  const step = (evt.shiftKey ? 20 : 2) * preview.w / 1920;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[evt.key]) {
    const [dx, dy] = moves[evt.key];
    const x = Math.min(Math.max(0, wd.x + dx), preview.w - wd.w), y = Math.min(Math.max(0, wd.y + dy), preview.h - wd.h);
    wd.x = x; wd.y = y;
    setPosition(`overlay.${wd.key}`, x, y);
    commitLayoutChange();
    evt.preventDefault();
  } else if (evt.key === "Escape") {
    select(null);
  }
}

// Left column of the layout editor: every widget with an on/off switch; the selected one unfolds its controls.
// Same config flags as the settings panel, saved through commitLayoutChange so every surface stays in sync.
const WIDGET_ICONS = {
  datetime: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  minimap: '<svg viewBox="0 0 24 24"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0113 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  journey_map: '<svg viewBox="0 0 24 24"><circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M7.8 17.2C13 15.5 8.5 9.5 13 8.2c1.5-.5 2.7-1 3.3-1.4"/></svg>',
  speed: '<svg viewBox="0 0 24 24"><path d="M4.5 16.5a8 8 0 1115 0"/><path d="M12 14l4-5"/><circle cx="12" cy="14.5" r="1.2"/></svg>',
};
const CORNER_LABELS = { "top-left": "Top left", "top-right": "Top right", "bottom-left": "Bottom left", "bottom-right": "Bottom right" };

async function setWidgetEnabled(key, on) {
  set(config, `overlay.${key}.enabled`, on);
  preview.selected = on ? key : (preview.selected === key ? null : preview.selected); // select what you just added
  await commitLayoutChange();
  $(`#widget-panel [data-switch="${key}"]`)?.focus();
}

// The column navigates in place: widget list <-> one widget's full settings. `panelView` is the open widget.
function renderWidgetPanel(direction) {
  const root = $("#widget-panel");
  // Picking another widget on the frame while a detail is open follows it.
  if (preview.panelView && preview.selected && preview.selected !== preview.panelView) preview.panelView = preview.selected;
  if (preview.panelView) return renderWidgetDetail(preview.panelView, direction);
  root.dataset.view = "list";
  const keys = Object.keys(WIDGETS);
  const shown = keys.filter(key => config.overlay[key].enabled).length;
  root.replaceChildren(el("div", { class: `wp-view${direction ? ` enter-${direction}` : ""}` },
    el("div", { class: "wp-head" }, el("strong", {}, "Widgets"), el("span", {}, `${shown} of ${keys.length} shown`)),
    el("div", { class: "wp-list" }, keys.map(renderWidgetRow)),
    el("p", { class: "wp-foot" }, "Select a widget here or on the frame to adjust it.")));
}

function openWidgetDetail(key) {
  preview.panelView = key;
  if (config.overlay[key].enabled) {
    preview.selected = key;
    document.querySelectorAll("#preview .widget").forEach(g => g.classList.toggle("selected", g.dataset.key === key));
  }
  renderWidgetPanel("forward");
  $("#widget-panel .wp-back")?.focus();
}

// `silent` is used when the dialog closes: reset the view without re-rendering or moving focus.
function closeWidgetDetail(silent) {
  const key = preview.panelView;
  if (!key) return;
  preview.panelView = null;
  if (silent) return renderWidgetPanel();
  renderWidgetPanel("back");
  $(`#widget-panel [data-detail="${key}"]`)?.focus();
}

// Every setting of one widget, rendered with the same field controls as the settings panel.
function widgetDetailFields(key) {
  const group = WIDGET_GROUPS.find(g => g.id === key);
  const skip = new Set([`overlay.${key}.enabled`, `overlay.${key}.corner`, WIDGETS[key].sizePath]); // shown above
  return group.fields.filter(f => !skip.has(f.path) && (!f.showIf || f.showIf(config)));
}

function renderWidgetDetail(key, direction) {
  const root = $("#widget-panel");
  // Saving re-renders the preview (and this view): keep focus, caret and scroll where the user left them.
  const active = root.contains(document.activeElement) ? document.activeElement : null;
  const focus = active && { id: active.id, sel: active.selectionStart ?? null, selEnd: active.selectionEnd ?? null };
  const scroll = root.dataset.view === key ? root.scrollTop : 0;
  root.dataset.view = key;

  const info = WIDGETS[key];
  const on = !!config.overlay[key].enabled;
  const iconEl = el("span", { class: "wp-icon", "aria-hidden": "true" });
  iconEl.innerHTML = WIDGET_ICONS[key];
  const back = el("button", { class: "wp-nav wp-back", type: "button", onclick: () => closeWidgetDetail() },
    el("span", { "aria-hidden": "true" }, "‹"), "Widgets");
  root.replaceChildren(el("div", { class: `wp-view wp-detail${direction ? ` enter-${direction}` : ""}` },
    back,
    el("div", { class: "wp-detail-title" }, iconEl, el("strong", {}, info.label),
      el("input", { type: "checkbox", role: "switch", class: "ess-switch small", checked: on, "data-switch": key,
        "aria-label": `Show ${info.label.toLowerCase()}`, onchange: e => setWidgetEnabled(key, e.target.checked) })),
    on ? renderWidgetControls(key, false) : el("p", { class: "wp-note" }, "Hidden from the overlay. You can still prepare its settings."),
    el("div", { class: "wp-fields" }, widgetDetailFields(key).map(f => f.sub ? null : renderField(f, "wd-")))));

  root.scrollTop = scroll;
  if (focus?.id) {
    const target = document.getElementById(focus.id);
    target?.focus({ preventScroll: true });
    if (target && focus.sel != null) try { target.setSelectionRange(focus.sel, focus.selEnd); } catch { /* not a text input */ }
  }
}

function renderWidgetRow(key) {
  const info = WIDGETS[key];
  const on = !!config.overlay[key].enabled;
  const selected = on && preview.selected === key;
  const iconEl = el("span", { class: "wp-icon", "aria-hidden": "true" });
  iconEl.innerHTML = WIDGET_ICONS[key];
  const row = el("div", { class: `wp-row${on ? "" : " is-off"}${selected ? " is-selected" : ""}` },
    el("div", { class: "wp-row-head" },
      el("button", { class: "wp-select", type: "button", disabled: !on, "aria-expanded": String(selected),
        onclick: () => { select(selected ? null : key); $("#preview").focus({ preventScroll: true }); } },
        iconEl, el("span", {}, info.label)),
      el("input", { type: "checkbox", role: "switch", class: "ess-switch small", checked: on, "data-switch": key,
        "aria-label": `Show ${info.label.toLowerCase()}`, onchange: e => setWidgetEnabled(key, e.target.checked) })));
  if (selected) row.append(renderWidgetControls(key));
  return row;
}

function renderWidgetControls(key, withLink = true) {
  const info = WIDGETS[key];
  const base = `overlay.${key}`;
  const pos = get(config, `${base}.position`);
  const corner = get(config, `${base}.corner`);
  return el("div", { class: "wp-controls" },
    el("div", { class: "wp-control" },
      el("span", { class: "wp-label" }, "Position"),
      el("div", { class: "wp-position" },
        el("div", { class: "corner-picker", role: "group", "aria-label": "Snap to corner" },
          CORNERS.map(c => el("button", { type: "button", class: `corner-cell ${c}${!pos && c === corner ? " active" : ""}`,
            title: CORNER_LABELS[c], "aria-label": `Snap to ${CORNER_LABELS[c].toLowerCase()} corner`,
            "aria-pressed": String(!pos && c === corner),
            onclick: () => { set(config, `${base}.corner`, c); resetToCorner(key); } }))),
        el("span", { class: "wp-position-text" }, pos
          ? [el("strong", {}, "Custom"), `x ${(pos.x * 100).toFixed(1)}% · y ${(pos.y * 100).toFixed(1)}%`]
          : [el("strong", {}, CORNER_LABELS[corner] || corner), "Snapped to the corner"]))),
    el("label", { class: "wp-control" },
      el("span", { class: "wp-label" }, "Size"),
      el("span", { class: "wp-size" },
        el("input", { id: "widget-size", type: "number", value: get(config, info.sizePath), min: info.min, max: info.max,
          onchange: e => {
            const v = Math.min(info.max, Math.max(info.min, Number(e.target.value) || info.min));
            set(config, info.sizePath, v);
            commitLayoutChange();
          } }),
        el("small", {}, "px at 1080p"))),
    withLink ? el("button", { class: "wp-nav wp-forward", type: "button", "data-detail": key, onclick: () => openWidgetDetail(key),
      "aria-label": `All ${info.label.toLowerCase()} settings` }, "All settings", el("span", { "aria-hidden": "true" }, "›")) : null);
}

// While resizing by dragging, only the size field changes: no need to rebuild the panel under the pointer.
function renderToolbar(liveSize) {
  if (liveSize != null && $("#widget-size")) { $("#widget-size").value = liveSize; return; }
  renderWidgetPanel();
}

document.addEventListener("DOMContentLoaded", () => {
  // Esc steps back one level (detail -> list) before it closes the editor. Handled on keydown because Chrome
  // only lets a dialog's `cancel` be prevented after user activation. Esc inside an open <select> is left alone.
  $("#layout-dialog").addEventListener("keydown", evt => {
    if (evt.key !== "Escape" || !preview.panelView || evt.target.tagName === "SELECT") return;
    evt.preventDefault();
    closeWidgetDetail();
  }, true);
  const svg = $("#preview");
  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", onPointerUp);
  svg.addEventListener("pointercancel", onPointerUp);
  svg.addEventListener("keydown", onKeyDown);
  svg.addEventListener("dblclick", evt => {
    const g = evt.target.closest(".widget");
    if (g) resetToCorner(g.dataset.key);
  });
});
