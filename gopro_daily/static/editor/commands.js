// Every action of the editor in one table. Toolbar buttons, keyboard shortcuts and the shortcut help line are all
// generated from it, so they cannot drift apart. Adding a tool = an operation in film.js + one entry here.
//
// Entry: { id, label, icon?, hint?, keys (shown to the user), match(event), run(event), enabled?(), pressed?(), bar?, toggle? }
//   bar: the toolbar group it is drawn in ([data-bar] in index.html); no bar = keyboard only.
//   "edit" and "history" buttons show icon + label + key; the others show only the icon (label as tooltip).
//   toggle: drawn as a labelled switch instead of a button. icon and label may be functions of the editor state.

import * as film from "./film.js";

export const SPEEDS = [1, 2, 4, 8, 16];

const plain = e => !e.metaKey && !e.ctrlKey && !e.altKey;
const key = (...names) => e => plain(e) && !e.shiftKey && names.includes(e.key.length === 1 ? e.key.toLowerCase() : e.key);
const shifted = name => e => plain(e) && e.shiftKey && e.key === name;
const mod = (name, shift = false) => e => (e.metaKey || e.ctrlKey) && !e.altKey && e.shiftKey === shift && e.key.toLowerCase() === name;
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl+";

/** @param ed the editor controller (see editor.js): playback, position(), cuts(), selected(), apply(), … */
export function createCommands(ed) {
  const at = () => ed.position();
  const step = () => 5 * Math.max(1, ed.playback.rate / 2);
  const frame = () => 1 / (ed.fps() || 30);
  const editOp = (fn, failure) => () => ed.apply(fn(ed.source(), ed.cuts(), at()), failure);

  return [
    // ------------------------------------------------------------------------------------------ transport
    { id: "prev-clip", bar: "transport", icon: "prev-clip", label: "Previous clip", keys: ["Shift+←"],
      match: shifted("ArrowLeft"), run: () => ed.jumpClip(-1) },
    { id: "play", bar: "transport", icon: () => (ed.playback.paused ? "play" : "pause"),
      label: () => (ed.playback.paused ? "Play" : "Pause"), keys: ["Space", "K"], match: key(" ", "k"), run: () => ed.togglePlay() },
    { id: "next-clip", bar: "transport", icon: "next-clip", label: "Next clip", keys: ["Shift+→"],
      match: shifted("ArrowRight"), run: () => ed.jumpClip(1) },
    { id: "back", label: "Back 5 s", keys: ["←"], match: key("ArrowLeft"), run: () => ed.seek(at() - step()) },
    { id: "forward", label: "Forward 5 s", keys: ["→"], match: key("ArrowRight"), run: () => ed.seek(at() + step()) },
    { id: "frame-back", label: "Previous frame", keys: [","], match: key(","), run: () => ed.seek(at() - frame(), false) },
    { id: "frame-forward", label: "Next frame", keys: ["."], match: key("."), run: () => ed.seek(at() + frame(), false) },
    { id: "prev-edit", label: "Previous edit point", keys: ["↑"], match: key("ArrowUp"),
      run: () => ed.seek(film.editPoints(ed.source(), ed.cuts()).findLast(p => p < at() - 0.25) ?? 0) },
    { id: "next-edit", label: "Next edit point", keys: ["↓"], match: key("ArrowDown"),
      run: () => ed.seek(film.editPoints(ed.source(), ed.cuts()).find(p => p > at() + 0.05) ?? ed.source().total) },
    { id: "speed", label: "Speed", keys: ["1–5"], match: e => plain(e) && /^[1-5]$/.test(e.key),
      run: e => ed.setRate(SPEEDS[Number(e.key) - 1]) },
    { id: "mute", bar: "audio", icon: () => (ed.muted() ? "muted" : "sound"),
      label: () => (ed.muted() ? "Sound off — turn on" : "Sound on — mute"), keys: ["M"], match: key("m"), run: () => ed.toggleMute() },

    // ------------------------------------------------------------------------------------------ editing
    { id: "split", bar: "edit", icon: "split", label: "Split", keys: ["S"], match: key("s"),
      hint: "Cut the part under the playhead in two",
      run: editOp(film.split, "Place the playhead inside a kept part to split it") },
    { id: "mark-in", bar: "edit", icon: "mark-in", label: "Start here", keys: ["I"], match: key("i"),
      hint: "Trim the part under the playhead so it starts at the playhead (mark in)",
      run: editOp(film.setIn, "Nothing to mark here") },
    { id: "mark-out", bar: "edit", icon: "mark-out", label: "End here", keys: ["O"], match: key("o"),
      hint: "Trim the part under the playhead so it ends at the playhead (mark out)",
      run: editOp(film.setOut, "Nothing to mark here") },
    { id: "remove", bar: "edit", icon: "remove", label: "Remove", keys: ["⌫"], match: key("Delete", "Backspace"),
      hint: "Leave the selected part out of the film", enabled: () => !!ed.selected(),
      run: () => ed.apply(film.remove(ed.source(), ed.cuts(), ed.selected()?.id)) },
    { id: "restore", bar: "edit", icon: "restore", label: "Put back", keys: ["R"], match: key("r"),
      hint: "Bring back the removed part under the playhead", enabled: () => !film.cutAt(ed.source(), ed.cuts(), at()),
      run: editOp(film.restore, "The playhead is already on a kept part") },

    // ------------------------------------------------------------------------------------------ history
    { id: "undo", bar: "history", icon: "undo", label: "Undo", keys: [`${MOD}Z`], match: mod("z"),
      enabled: () => ed.history.canUndo, run: () => ed.undo() },
    { id: "redo", bar: "history", icon: "redo", label: "Redo", keys: [`${MOD}⇧Z`], match: mod("z", true),
      enabled: () => ed.history.canRedo, run: () => ed.redo() },

    // ------------------------------------------------------------------------------------------ view
    { id: "skip-removed", bar: "playback-mode", toggle: true, label: "Skip removed parts", keys: ["P"], match: key("p"),
      hint: "On: watch the film as it will be exported. Off: watch everything, removed parts included",
      pressed: () => ed.skipRemoved(), run: () => ed.toggleSkipRemoved() },
    { id: "zoom-out", bar: "zoom", icon: "zoom-out", label: "Zoom out", keys: ["−"], match: key("-"), run: () => ed.timeline.zoomBy(1 / 1.6) },
    { id: "zoom-in", bar: "zoom", icon: "zoom-in", label: "Zoom in", keys: ["+"], match: key("+", "="), run: () => ed.timeline.zoomBy(1.6) },
    { id: "zoom-fit", bar: "zoom", icon: "zoom-fit", label: "Show the whole footage", keys: ["Z"], match: key("z"),
      run: () => ed.timeline.setZoom(1) },
  ];
}

/** Resolves a field that may be a value or a function of the editor state (icon, label). */
export const value = v => (typeof v === "function" ? v() : v);

/** The command a key press triggers, if any. */
export const commandFor = (commands, event) => commands.find(c => c.match(event)) || null;
