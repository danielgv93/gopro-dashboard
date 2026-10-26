// Footage editor: watch every clip of a film and decide what goes into it. The controller that ties together
//
//   film.js           the model (source layout + kept cuts) and every editing operation, pure
//   history.js        undo/redo of cuts
//   playback.js       one <video> playing the source, addressed by source position
//   timeline-view.js  the zoomable track with cuts and trim handles
//   clips-view.js     the clip strip with keep/leave-out switches
//   commands.js       toolbar buttons + keyboard shortcuts
//
// Edits are saved to the server as they happen and applied on export; the source files are never touched.

import * as film from "./film.js";
import { History } from "./history.js";
import { Playback } from "./playback.js";
import { TimelineView } from "./timeline-view.js";
import { ClipsView } from "./clips-view.js";
import { SPEEDS, commandFor, createCommands, value } from "./commands.js";
import { icon } from "./icons.js";
import { app, fmtPos, wallClock } from "./shared.js";

const { el } = app;
const SAVE_DELAY = 500;
const LABELLED_BARS = new Set(["edit", "history"]);  // editing tools show icon + name + key; the rest icon only

export function createEditor(dialog) {
  const q = sel => dialog.querySelector(sel);
  const video = q("#editor-video");
  const playback = new Playback(video);
  const history = new History([]);

  const state = {
    group: null,
    source: null,
    selectedId: null,
    draft: null,        // cuts while a trim handle is being dragged (committed to history on release)
    skipRemoved: true,  // playback jumps over removed parts, so you watch the film as it will be exported
    wasKept: false,     // whether the last playing tick was on a kept part (skipping only triggers on leaving one)
    scrubbing: false,
    hintTimer: 0,
    saveTimer: 0,
    saving: Promise.resolve(),
  };

  const cuts = () => state.draft ?? history.present;
  const selected = () => cuts().find(c => c.id === state.selectedId) || null;
  const position = () => playback.position;

  // ------------------------------------------------------------------------------------------ controller API
  const ed = {
    playback, history,
    source: () => state.source,
    cuts, selected, position,
    fps: () => parseFps(playback.clips[playback.index]?.entry.rec.fps),
    skipRemoved: () => state.skipRemoved,
    muted: () => video.muted,

    /** Commits the result of a film operation ({cuts, select} or null) as one undoable step. */
    apply(result, failure = "") {
      if (!result) return failure && hint(failure);
      history.push(result.cuts);
      state.selectedId = result.select;
      changed();
    },
    undo() { if (history.undo()) { state.selectedId = null; changed(); } },
    redo() { if (history.redo()) { state.selectedId = null; changed(); } },

    seek(p, autoplay) {
      playback.seek(Math.min(Math.max(0, p), state.source.total), autoplay);
      state.wasKept = !!film.cutAt(state.source, cuts(), p);
    },
    togglePlay() {
      // At the end of the film, play starts over from the first kept part.
      if (playback.paused && state.skipRemoved && film.nextKept(state.source, cuts(), position()) === null) {
        const first = cuts()[0];
        if (first) return ed.seek(film.span(state.source, first).from, true);
      }
      playback.toggle();
    },
    jumpClip(delta) {
      const recs = state.source.recordings;
      const { entry, t } = playback.location;
      // "Previous" first rewinds to the start of the current clip, like any media player.
      if (delta < 0 && t > 3) return ed.seek(entry.offset);
      const target = recs[recs.indexOf(entry) + delta];
      if (target) ed.seek(target.offset);
    },
    setRate(rate) { playback.setRate(rate); renderSpeeds(); },
    toggleMute() { video.muted = !video.muted; renderTransport(); },
    toggleSkipRemoved() {
      state.skipRemoved = !state.skipRemoved;
      hint(state.skipRemoved ? "Playing the film as it will be exported" : "Playing everything, removed parts included");
      renderToolbars();
    },
  };

  const timeline = new TimelineView(q("#editor-timeline"), {
    seek(p, { scrubbing }) {
      state.scrubbing = scrubbing;
      if (p !== null) ed.seek(p, false);
    },
    select(id) { state.selectedId = id; renderEdits(); },
    trim(id, edge, p) {
      const result = film.trim(state.source, history.present, id, edge, p);
      if (!result) return;
      if (!state.draft) playback.pause();  // trimming shows still frames; playing on would fight the handle
      state.draft = result.cuts;
      state.selectedId = id;
      renderEdits();
      const cut = result.cuts.find(c => c.id === id);
      const { from, to } = film.span(state.source, cut);
      playback.seek(edge === "start" ? from : Math.max(from, to - 0.04), false);  // show the frame being trimmed to
    },
    trimEnd() {
      if (!state.draft) return;
      const draft = state.draft;
      state.draft = null;
      history.push(draft);
      changed();
    },
  });
  ed.timeline = timeline;

  const clipsView = new ClipsView(q("#editor-clips"), {
    jump: entry => ed.seek(entry.offset, true),
    toggle: entry => ed.apply(film.toggleRecording(state.source, cuts(), entry.key)),
  });

  const commands = createCommands(ed);

  // ------------------------------------------------------------------------------------------ rendering
  function changed() {
    renderEdits();
    scheduleSave();
  }

  function renderEdits() {
    const current = cuts();
    const overlayMin = app.config().overlay.enabled ? app.options().overlay_min_seconds || 0 : 0;
    const short = film.shortCuts(state.source, current, overlayMin);
    timeline.render(current, state.selectedId, {
      short, shortNote: `Shorter than ${overlayMin} s: exported without the overlay (the GPS renderer needs more)`,
    });
    const kept = film.keptDuration(current);
    const keptClips = new Set(current.map(c => c.rec)).size;
    const total = state.source.recordings.length;
    q("#editor-meta").textContent = [
      `Film ${app.fmtDuration(kept)} of ${app.fmtDuration(state.source.total)} filmed`,
      `${keptClips} of ${total} ${total === 1 ? "clip" : "clips"}`,
      short.size ? `${short.size} ${short.size === 1 ? "part" : "parts"} too short for the overlay` : "",
    ].filter(Boolean).join(" · ");
    q("#editor-reset").disabled = film.isDefault(state.source, current) && !state.group.edit?.edited;
    renderToolbars();
    renderTransport();
  }

  // Runs on every playback tick (every frame while playing): keep it to text and style updates.
  function renderTransport() {
    if (!state.source) return;
    const p = position();
    const { entry, t, clip, clipIndex } = playback.location;
    const kept = !!film.cutAt(state.source, cuts(), p);
    timeline.setPlayhead(p, !playback.paused);
    q("#editor-time").textContent = `${fmtPos(p, playback.paused)} / ${fmtPos(state.source.total)}`;
    q("#editor-clock").textContent = wallClock(entry.rec, t);
    q("#editor-removed").hidden = kept;
    const number = state.source.recordings.indexOf(entry) + 1;
    const chapter = entry.clips.length > 1 ? ` · file ${clipIndex + 1}/${entry.clips.length}` : "";
    q("#editor-clip").textContent =
      `Clip ${number}/${state.source.recordings.length} · ${clip.clip.name}${chapter}${clip.clip.lowres ? "" : " · full resolution"}`;
    clipsView.render(cuts(), entry.key);
    refreshCommands();
  }

  // Cheap enough for every frame: only touches the DOM when an icon, label or enabled state actually changes.
  function refreshCommands() {
    for (const node of dialog.querySelectorAll("[data-command]")) {
      const c = commands.find(command => command.id === node.dataset.command);
      if (c.enabled) node.disabled = !c.enabled();
      const name = value(c.icon);
      if (name && node.dataset.icon !== name) {
        node.dataset.icon = name;
        node.querySelector(".ed-svg")?.replaceWith(icon(name));
      }
      const label = value(c.label);
      if (node.dataset.label !== label) {
        node.dataset.label = label;
        node.title = `${c.hint || label} (${c.keys.join(" / ")})`;
        if (!LABELLED_BARS.has(c.bar)) node.setAttribute("aria-label", label);
      }
    }
  }

  function renderToolbars() {
    for (const root of dialog.querySelectorAll("[data-bar]")) {
      root.replaceChildren(...commands.filter(c => c.bar === root.dataset.bar).map(c => (c.toggle ? commandSwitch(c) : commandButton(c))));
    }
    refreshCommands();
  }

  // A mouse click leaves the control focused, and Space would then press it again instead of playing.
  const run = c => e => { c.run(e); if (e.detail > 0) e.currentTarget.blur(); };

  function commandButton(c) {
    const labelled = LABELLED_BARS.has(c.bar);
    const pressed = c.pressed?.();
    return el("button", {
      type: "button", class: `${labelled ? "button compact ed-tool" : "icon-button ed-icon-button"}${c.id === "play" ? " play" : ""}`,
      "data-command": c.id, "aria-pressed": pressed === undefined ? undefined : String(pressed), onclick: run(c),
    }, icon(value(c.icon)), labelled ? [el("span", {}, value(c.label)), el("kbd", { "aria-hidden": "true" }, c.keys[0])] : null);
  }

  function commandSwitch(c) {
    return el("label", { class: "ed-switch", title: `${c.hint} (${c.keys.join(" / ")})` },
      el("input", { type: "checkbox", role: "switch", class: "ess-switch small", checked: c.pressed(), onclick: run(c) }),
      el("span", {}, value(c.label)));
  }

  function renderSpeeds() {
    q("#editor-speeds").replaceChildren(...SPEEDS.map((s, i) => el("button", {
      type: "button", class: s === playback.rate ? "active" : "", "aria-pressed": String(s === playback.rate),
      title: `Speed ${s}× (key ${i + 1})`, onclick: () => ed.setRate(s),
    }, `${s}×`)));
  }

  function renderShortcuts() {
    const shown = commands.filter(c => !c.bar || c.bar === "edit");
    q("#editor-keys").textContent = shown.map(c => `${c.keys.join("/")} ${value(c.label).toLowerCase()}`).join(" · ");
  }

  function hint(text) {
    const node = q("#editor-hint");
    node.textContent = text;
    clearTimeout(state.hintTimer);
    state.hintTimer = setTimeout(() => { node.textContent = ""; }, 3500);
  }

  // ------------------------------------------------------------------------------------------ playback rules
  playback.addEventListener("tick", () => {
    if (!state.source) return;
    if (!playback.paused && state.skipRemoved && !state.scrubbing) {
      const kept = !!film.cutAt(state.source, cuts(), position());
      if (state.wasKept && !kept) {
        const next = film.nextKept(state.source, cuts(), position());
        if (next === null) playback.pause();
        else playback.seek(next, true);
        return;  // the jump emits its own tick
      }
      state.wasKept = kept;
    }
    renderTransport();
  });
  playback.addEventListener("end", () => renderTransport());

  // ------------------------------------------------------------------------------------------ saving
  function scheduleSave() {
    clearTimeout(state.saveTimer);
    q("#editor-save").textContent = "Saving…";
    state.saveTimer = setTimeout(save, SAVE_DELAY);
  }

  function save() {
    clearTimeout(state.saveTimer);
    state.saveTimer = 0;
    const group = state.group, source = state.source, current = history.present;
    const url = `/api/groups/${encodeURIComponent(group.key)}/edit`;
    // Saves run one after another so an older request can never overwrite a newer edit.
    state.saving = state.saving.then(async () => {
      try {
        const summary = film.isDefault(source, current)
          ? await app.api("DELETE", url)
          : await app.api("PUT", url, { cuts: film.toServer(current) });
        group.edit = summary;
        document.dispatchEvent(new CustomEvent("film-edit", { detail: { group } }));
        if (state.group === group) {
          q("#editor-save").textContent = summary.edited ? "Saved · applied on export" : "No edits";
          q("#editor-reset").disabled = !summary.edited;
        }
      } catch (e) {
        if (state.group === group) q("#editor-save").textContent = "Not saved";
        app.toast(`Could not save the edit: ${e.message}`, "error");
      }
    });
    return state.saving;
  }

  // ------------------------------------------------------------------------------------------ wiring
  q("#editor-reset").onclick = () => {
    if (!confirm("Put every clip back whole? You can undo this.")) return;
    ed.apply({ cuts: film.defaultCuts(state.source), select: null });
  };
  video.addEventListener("click", () => ed.togglePlay());
  // On the document, not the dialog: clicks on the timeline or the video leave the focus on <body>. The dialog is
  // modal, so while it is open nothing else on the page can be receiving these keys.
  document.addEventListener("keydown", e => {
    if (!dialog.open || e.defaultPrevented) return;
    if (e.target.closest?.("input, select, textarea")) return;
    if ((e.key === " " || e.key === "Enter") && e.target.closest("button")) return;  // the focused button handles it
    const command = commandFor(commands, e);
    if (!command || (command.enabled && !command.enabled())) return;
    e.preventDefault();
    command.run(e);
  });
  dialog.addEventListener("close", () => {
    if (state.saveTimer) save();
    playback.unload();
    state.group = null;
  });
  renderShortcuts();

  // ------------------------------------------------------------------------------------------ public
  return {
    /** Opens a film (a scanned group) at the recording with index `recIndex` in group.recordings. */
    open(group, recIndex = 0) {
      const source = film.buildSource(group);
      if (!source.recordings.length) return app.toast("Nothing to preview in this film", "error");
      Object.assign(state, { group, source, selectedId: null, draft: null, wasKept: false, scrubbing: false });
      history.reset(group.edit ? film.fromServer(source, group.edit.cuts) : film.defaultCuts(source));
      q("#editor-title").textContent = app.fmtDayHeader(group.label);
      q("#editor-save").textContent = group.edit?.edited ? "Edited · applied on export" : "";
      q("#editor-hint").textContent = "";
      playback.setSource(source);
      timeline.setSource(source);
      clipsView.setSource(source);
      renderSpeeds();
      if (!dialog.open) dialog.showModal();
      renderEdits();
      const start = source.recordings.find(r => r.index >= recIndex) || source.recordings[0];
      const cut = film.cutsOf(cuts(), start.key)[0];
      ed.seek(cut ? film.span(source, cut).from : start.offset, true);
    },
  };
}

function parseFps(value) {
  const [a, b] = String(value || "").split("/").map(Number);
  return a && b ? a / b : a || 0;
}
