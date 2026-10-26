// Film model: the footage laid end to end (the "source") and the cuts kept from it. Pure functions, no DOM and no
// <video>, so every editing rule lives here and is covered by tests/js/film.test.mjs.
//
// Two coordinate systems:
//   - position: seconds along the source timeline, every valid recording back to back (the editor's x axis)
//   - cut.start / cut.end: seconds inside one recording (what the server stores, see gopro_daily/edits.py)
//
// Operations never mutate: they return a new cuts array (plus the id to select), so undo/redo is a stack of arrays.
// A new editing tool is a new function here plus a command in commands.js.

export const MIN_CUT = 0.2;  // seconds; same as edits.MIN_CUT_SECONDS on the server
const EPS = 0.05;            // same as edits.MERGE_GAP_SECONDS: closer than this counts as touching

let nextId = 1;
const newId = () => `c${nextId++}`;
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// ---------------------------------------------------------------------------------------------- source

/** Lays out every valid recording of a group (film order) and the chapter files inside each one. */
export function buildSource(group) {
  const recordings = [];
  let offset = 0;
  group.recordings.forEach((rec, index) => {
    if (rec.error || !(rec.duration > 0)) return;
    const clips = [];
    let inRec = 0;
    for (const clip of rec.clips || []) {
      if (!(clip.duration > 0)) continue;
      clips.push({ clip, start: inRec, end: inRec + clip.duration, offset: offset + inRec });
      inRec += clip.duration;
    }
    if (!clips.length) return;
    recordings.push({ key: rec.key, rec, index, offset, duration: inRec, clips });
    offset += inRec;
  });
  return { recordings, total: offset, byKey: new Map(recordings.map(r => [r.key, r])) };
}

/** Recording and chapter file under a source position (clamped to the source). */
export function locate(source, position) {
  const p = clamp(position, 0, Math.max(0, source.total - 1e-3));
  const entry = source.recordings.findLast(r => r.offset <= p) || source.recordings[0];
  const t = p - entry.offset;
  const clipIndex = Math.max(0, entry.clips.findLastIndex(c => c.start <= t));
  const clip = entry.clips[clipIndex];
  return { entry, t, clip, clipIndex, clipTime: t - clip.start };
}

/** Film order of the chapter files, for playback: position of each file along the source. */
export const clipList = source => source.recordings.flatMap(entry => entry.clips.map(c => ({ ...c, entry })));

// ---------------------------------------------------------------------------------------------- cuts

/** Cuts from the server ({rec, start, end}) with ids, dropping recordings this scan does not have. */
export function fromServer(source, cuts) {
  return sort(source, (cuts || []).filter(c => source.byKey.has(c.rec)).map(c => ({ id: newId(), ...c })));
}

export const toServer = cuts => cuts.map(({ rec, start, end }) => ({ rec, start, end }));

export const defaultCuts = source => source.recordings.map(r => ({ id: newId(), rec: r.key, start: 0, end: r.duration }));

export const span = (source, cut) => {
  const offset = source.byKey.get(cut.rec).offset;
  return { from: offset + cut.start, to: offset + cut.end };
};

function sort(source, cuts) {
  return [...cuts].sort((a, b) => span(source, a).from - span(source, b).from);
}

export const keptDuration = cuts => cuts.reduce((sum, c) => sum + (c.end - c.start), 0);

export const cutsOf = (cuts, key) => cuts.filter(c => c.rec === key);

export function cutAt(source, cuts, position) {
  return cuts.find(c => {
    const { from, to } = span(source, c);
    return position >= from - 1e-6 && position < to - 1e-6;
  }) || null;
}

/** Start of the first kept range at or after `position`, or null when nothing kept follows. */
export function nextKept(source, cuts, position) {
  for (const c of cuts) {
    const { from, to } = span(source, c);
    if (to - EPS > position) return Math.max(from, position);
  }
  return null;
}

/** Every cut edge and recording boundary, sorted: the points "previous/next edit" jumps between. */
export function editPoints(source, cuts) {
  const points = new Set([0, source.total]);
  for (const r of source.recordings) points.add(r.offset);
  for (const c of cuts) {
    const { from, to } = span(source, c);
    points.add(from);
    points.add(to);
  }
  return [...points].sort((a, b) => a - b);
}

// ---------------------------------------------------------------------------------------------- operations
// Each returns { cuts, select } or null when it does not apply at that position.

/** Splits the cut under the playhead in two. Both halves stay in the film until one is removed. */
export function split(source, cuts, position) {
  const cut = cutAt(source, cuts, position);
  if (!cut) return null;
  const t = position - source.byKey.get(cut.rec).offset;
  if (t - cut.start < MIN_CUT || cut.end - t < MIN_CUT) return null;
  const left = { ...cut, end: t }, right = { ...cut, id: newId(), start: t };
  return { cuts: cuts.flatMap(c => (c === cut ? [left, right] : [c])), select: right.id };
}

/** Moves one edge of a cut to a position, clamped to its recording, its neighbours and the minimum length. */
export function trim(source, cuts, id, edge, position) {
  const cut = cuts.find(c => c.id === id);
  if (!cut) return null;
  const entry = source.byKey.get(cut.rec);
  const siblings = cutsOf(cuts, cut.rec);
  const i = siblings.indexOf(cut);
  const t = position - entry.offset;
  const next = edge === "start"
    ? { ...cut, start: clamp(t, siblings[i - 1]?.end ?? 0, cut.end - MIN_CUT) }
    : { ...cut, end: clamp(t, cut.start + MIN_CUT, siblings[i + 1]?.start ?? entry.duration) };
  return { cuts: cuts.map(c => (c === cut ? next : c)), select: id };
}

/** Mark in: the cut under the playhead starts here. In a removed part, the next cut of that recording grows back. */
export function setIn(source, cuts, position) {
  const { entry } = locate(source, position);
  const cut = cutAt(source, cuts, position) || cutsOf(cuts, entry.key).find(c => span(source, c).from >= position);
  return cut ? trim(source, cuts, cut.id, "start", position) : null;
}

/** Mark out: the cut under the playhead ends here. In a removed part, the previous cut of that recording grows. */
export function setOut(source, cuts, position) {
  const { entry } = locate(source, position);
  const cut = cutAt(source, cuts, position) || cutsOf(cuts, entry.key).findLast(c => span(source, c).to <= position);
  return cut ? trim(source, cuts, cut.id, "end", position) : null;
}

export function remove(source, cuts, id) {
  const index = cuts.findIndex(c => c.id === id);
  if (index < 0) return null;
  const rest = cuts.filter(c => c.id !== id);
  return { cuts: rest, select: (rest[index] || rest[index - 1])?.id ?? null };
}

/** Puts a removed part back and heals it with the cuts on both sides, so it reads as one range again. */
export function restore(source, cuts, position) {
  if (cutAt(source, cuts, position)) return null;
  const { entry, t } = locate(source, position);
  const siblings = cutsOf(cuts, entry.key);
  const before = siblings.findLast(c => c.end <= t + 1e-6);
  const after = siblings.find(c => c.start >= t - 1e-6);
  const healed = { id: newId(), rec: entry.key, start: before?.start ?? 0, end: after?.end ?? entry.duration };
  const rest = cuts.filter(c => c !== before && c !== after);
  return { cuts: sort(source, [...rest, healed]), select: healed.id };
}

/** Takes a whole recording out of the film, or puts it back whole. */
export function toggleRecording(source, cuts, key) {
  const entry = source.byKey.get(key);
  if (!entry) return null;
  if (cutsOf(cuts, key).length) return { cuts: cuts.filter(c => c.rec !== key), select: null };
  const cut = { id: newId(), rec: key, start: 0, end: entry.duration };
  return { cuts: sort(source, [...cuts, cut]), select: cut.id };
}

/** Ids of cuts whose continuous kept range (touching cuts of a recording joined, as the server renders them) is
 *  shorter than `min` seconds. */
export function shortCuts(source, cuts, min) {
  const short = new Set();
  if (!(min > 0)) return short;
  let run = [];
  const flush = () => {
    if (run.length && run.at(-1).end - run[0].start < min) run.forEach(c => short.add(c.id));
    run = [];
  };
  for (const cut of cuts) {
    const last = run.at(-1);
    if (!last || last.rec !== cut.rec || cut.start - last.end > EPS) flush();
    run.push(cut);
  }
  flush();
  return short;
}

/** True when the cuts are the untouched film: every recording kept whole, no splits. */
export function isDefault(source, cuts) {
  return cuts.length === source.recordings.length
    && source.recordings.every(r => {
      const own = cutsOf(cuts, r.key);
      return own.length === 1 && own[0].start <= EPS && own[0].end >= r.duration - EPS;
    });
}
