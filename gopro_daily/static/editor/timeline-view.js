// Timeline: the whole source on a zoomable, scrollable track. Recordings are drawn dimmed and hatched (what would be
// left out); kept cuts are drawn on top. The selected cut gets trim handles. The view owns no state: it renders what
// it is given and reports gestures through callbacks, so the editor stays the single source of truth.
//
//   .tl-scroll            horizontal scroll
//     .tl-canvas          width = zoom × 100%; every child is positioned in % of the source
//       .tl-ruler         time ticks
//       .tl-lane          .tl-rec (one per recording) + .tl-cut (one per kept cut)
//       .tl-playhead

import { span } from "./film.js";
import { app, fmtPos, mediaUrl } from "./shared.js";

const { el } = app;
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
const MIN_TICK_PX = 84;
export const ZOOM_MIN = 1, ZOOM_MAX = 400;

export class TimelineView {
  /**
   * @param root element to render into
   * @param handlers { seek(position|null, {scrubbing}), select(id|null), trim(id, edge, position), trimEnd() }
   */
  constructor(root, handlers) {
    this.root = root;
    this.handlers = handlers;
    this.zoom = 1;
    this.follow = true;
    this.scroll = el("div", { class: "tl-scroll" });
    this.canvas = el("div", { class: "tl-canvas" });
    this.ruler = el("div", { class: "tl-ruler", "aria-hidden": "true" });
    this.lane = el("div", { class: "tl-lane" });
    this.playhead = el("div", { class: "tl-playhead", "aria-hidden": "true" });
    this.canvas.append(this.ruler, this.lane, this.playhead);
    this.scroll.append(this.canvas);
    root.replaceChildren(this.scroll);
    root.setAttribute("role", "slider");
    root.setAttribute("aria-label", "Timeline: position in the footage");
    root.tabIndex = 0;
    this.#wirePointer();
    this.scroll.addEventListener("wheel", e => this.#wheel(e), { passive: false });
    new ResizeObserver(() => this.#renderRuler()).observe(this.scroll);
  }

  setSource(source) {
    this.source = source;
    this.zoom = 1;
    this.canvas.style.width = "100%";
    this.recs = source.recordings.map((entry, i) => {
      const r = entry.rec;
      const pause = i > 0 && r.continuous === false;
      return el("div", {
        class: `tl-rec${pause ? " after-pause" : ""}`, style: this.#box(entry.offset, entry.duration) + thumbs(entry),
        title: `${app.fmtClock(r.start)} · ${app.fmtDuration(entry.duration)}${pause && r.gap_before != null ? ` · after a ${app.fmtGap(r.gap_before)} pause` : ""}`,
      });
    });
    this.#renderRuler();
  }

  /** Draws the cuts. `short` is a Set of cut ids to flag, with `shortNote` explaining why (see film.shortCuts). */
  render(cuts, selectedId, { short = new Set(), shortNote = "" } = {}) {
    const blocks = cuts.map(cut => {
      const { from, to } = span(this.source, cut);
      const isShort = short.has(cut.id);
      const selected = cut.id === selectedId;
      const entry = this.source.byKey.get(cut.rec);
      const block = el("div", {
        class: `tl-cut${selected ? " selected" : ""}${isShort ? " short" : ""}`, "data-id": cut.id,
        style: this.#box(from, to - from) + thumbs(entry),
        title: `Kept ${fmtPos(to - from, true)} · ${fmtPos(from, true)} → ${fmtPos(to, true)}${isShort ? `\n${shortNote}` : ""}`,
      });
      if (selected) {
        block.append(
          el("span", { class: "tl-handle start", "data-edge": "start", title: "Drag to trim the start (I at the playhead)" }),
          el("span", { class: "tl-handle end", "data-edge": "end", title: "Drag to trim the end (O at the playhead)" }));
      }
      return block;
    });
    this.lane.replaceChildren(...this.recs, ...blocks);
  }

  setPlayhead(position, playing) {
    const total = this.source?.total || 1;
    this.playhead.style.left = `${(position / total) * 100}%`;
    this.root.setAttribute("aria-valuenow", String(Math.round(position)));
    this.root.setAttribute("aria-valuetext", fmtPos(position));
    if (playing && this.follow && this.zoom > 1) this.reveal(position);
  }

  /** Scrolls so a position is visible, keeping some context before it. */
  reveal(position) {
    const x = this.#x(position), { scrollLeft, clientWidth } = this.scroll;
    if (x < scrollLeft || x > scrollLeft + clientWidth - 24) this.scroll.scrollLeft = x - clientWidth * 0.2;
  }

  /** Zooms keeping `anchor` (a source position, default the playhead) under the same screen x. */
  setZoom(zoom, anchor) {
    zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom));
    if (!this.source || zoom === this.zoom) return;
    const position = anchor ?? this.#playheadPosition();
    const screenX = this.#x(position) - this.scroll.scrollLeft;
    this.zoom = zoom;
    this.canvas.style.width = `${zoom * 100}%`;
    this.scroll.scrollLeft = this.#x(position) - screenX;
    this.#renderRuler();
  }

  zoomBy(factor, anchor) { this.setZoom(this.zoom * factor, anchor); }

  // ------------------------------------------------------------------------------------------ internals

  #box(from, length) {
    const total = this.source.total || 1;
    return `left:${(from / total) * 100}%;width:${(length / total) * 100}%;`;
  }

  #x(position) { return (position / (this.source.total || 1)) * this.canvas.clientWidth; }

  #positionAt(clientX) {
    const box = this.canvas.getBoundingClientRect();
    return Math.min(Math.max(0, ((clientX - box.left) / box.width) * this.source.total), this.source.total);
  }

  #playheadPosition() { return (parseFloat(this.playhead.style.left) / 100 || 0) * this.source.total; }

  #renderRuler() {
    if (!this.source) return;
    const width = this.canvas.clientWidth || 1;
    const pxPerSecond = width / (this.source.total || 1);
    const step = TICK_STEPS.find(s => s * pxPerSecond >= MIN_TICK_PX) || TICK_STEPS.at(-1);
    const ticks = [];
    for (let t = 0; t <= this.source.total; t += step) {
      ticks.push(el("span", { class: "tl-tick", style: `left:${(t / this.source.total) * 100}%` }, fmtPos(t)));
    }
    this.ruler.replaceChildren(...ticks);
  }

  #wheel(e) {
    // Pinch (ctrl+wheel on trackpads) or ⌘/Ctrl + wheel zooms around the pointer; plain vertical wheel scrolls sideways.
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      this.zoomBy(Math.exp(-e.deltaY * 0.01), this.#positionAt(e.clientX));
    } else if (this.zoom > 1 && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      e.preventDefault();
      this.scroll.scrollLeft += e.deltaY;
    }
  }

  #wirePointer() {
    let drag = null;  // { kind: "scrub" } | { kind: "trim", id, edge }
    this.canvas.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      const handle = e.target.closest(".tl-handle");
      const block = e.target.closest(".tl-cut");
      this.canvas.setPointerCapture(e.pointerId);
      this.root.focus({ preventScroll: true });
      this.follow = false;
      if (handle) {
        drag = { kind: "trim", id: block.dataset.id, edge: handle.dataset.edge };
        this.handlers.trim(drag.id, drag.edge, this.#positionAt(e.clientX));
      } else {
        drag = { kind: "scrub" };
        this.handlers.select(block ? block.dataset.id : null);
        this.handlers.seek(this.#positionAt(e.clientX), { scrubbing: true });
      }
      e.preventDefault();
    });
    this.canvas.addEventListener("pointermove", e => {
      if (!drag) return;
      const position = this.#positionAt(e.clientX);
      if (drag.kind === "trim") this.handlers.trim(drag.id, drag.edge, position);
      else this.handlers.seek(position, { scrubbing: true });
    });
    const end = () => {
      if (drag?.kind === "trim") this.handlers.trimEnd();
      if (drag) this.handlers.seek(null, { scrubbing: false });
      drag = null;
      this.follow = true;
    };
    this.canvas.addEventListener("pointerup", end);
    this.canvas.addEventListener("pointercancel", end);
  }
}

// A recording's thumbnail repeated along its block: a cheap filmstrip look without decoding any video.
const thumbs = entry => `background-image:url("${mediaUrl(entry.rec.clips[0], "thumb")}");`;
