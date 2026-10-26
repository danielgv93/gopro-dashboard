// Editor model tests. Run with: node --test tests/js
import assert from "node:assert/strict";
import { test } from "node:test";

import * as film from "../../gopro_daily/static/editor/film.js";

// Two recordings: A (60 s, two chapter files of 30 s) and B (40 s, one file), B after a pause.
const group = {
  recordings: [
    { key: "A", duration: 60, clips: [{ id: "a1", duration: 30 }, { id: "a2", duration: 30 }] },
    { key: "X", duration: 10, error: "unreadable", clips: [] },
    { key: "B", duration: 40, clips: [{ id: "b1", duration: 40 }] },
  ],
};
const source = film.buildSource(group);
const ranges = cuts => cuts.map(c => [c.rec, +c.start.toFixed(2), +c.end.toFixed(2)]);

test("source lays valid recordings end to end and skips unreadable ones", () => {
  assert.equal(source.total, 100);
  assert.deepEqual(source.recordings.map(r => [r.key, r.offset]), [["A", 0], ["B", 60]]);
  const at = film.locate(source, 45);
  assert.deepEqual([at.entry.key, at.clip.clip.id, at.clipTime], ["A", "a2", 15]);
  assert.equal(film.locate(source, 75).entry.key, "B");
});

test("split keeps both halves and selects the right one", () => {
  const { cuts, select } = film.split(source, film.defaultCuts(source), 20);
  assert.deepEqual(ranges(cuts), [["A", 0, 20], ["A", 20, 60], ["B", 0, 40]]);
  assert.equal(cuts.find(c => c.id === select).start, 20);
  assert.equal(film.split(source, cuts, 20.1), null, "too close to an edge");
});

test("removing a split part leaves a hole that playback skips", () => {
  let cuts = film.split(source, film.defaultCuts(source), 20).cuts;
  cuts = film.split(source, cuts, 30).cuts;
  cuts = film.remove(source, cuts, film.cutAt(source, cuts, 25).id).cuts;
  assert.deepEqual(ranges(cuts), [["A", 0, 20], ["A", 30, 60], ["B", 0, 40]]);
  assert.equal(film.cutAt(source, cuts, 25), null);
  assert.equal(film.nextKept(source, cuts, 25), 30);
  assert.equal(film.keptDuration(cuts), 90);
});

test("trim is clamped by the recording, the neighbours and the minimum length", () => {
  let cuts = film.split(source, film.defaultCuts(source), 20).cuts;
  const right = cuts[1];
  cuts = film.trim(source, cuts, right.id, "start", 5).cuts;  // cannot overlap the left half
  assert.equal(cuts[1].start, 20);
  cuts = film.trim(source, cuts, right.id, "end", 95).cuts;   // cannot spill into recording B
  assert.equal(cuts[1].end, 60);
  cuts = film.trim(source, cuts, right.id, "end", 0).cuts;
  assert.equal(cuts[1].end, 20 + film.MIN_CUT);
});

test("mark in/out trim the cut under the playhead", () => {
  let cuts = film.setIn(source, film.defaultCuts(source), 70).cuts;
  cuts = film.setOut(source, cuts, 90).cuts;
  assert.deepEqual(ranges(cuts), [["A", 0, 60], ["B", 10, 30]]);
  // In the removed head of B, mark in grows B back to the playhead.
  cuts = film.setIn(source, cuts, 65).cuts;
  assert.deepEqual(ranges(cuts).at(-1), ["B", 5, 30]);
});

test("restore heals a hole into one range", () => {
  let cuts = film.split(source, film.defaultCuts(source), 20).cuts;
  cuts = film.split(source, cuts, 30).cuts;
  cuts = film.remove(source, cuts, film.cutAt(source, cuts, 25).id).cuts;
  cuts = film.restore(source, cuts, 25).cuts;
  assert.deepEqual(ranges(cuts), [["A", 0, 60], ["B", 0, 40]]);
  assert.equal(film.restore(source, cuts, 25), null, "nothing removed there");
});

test("a whole recording can be left out and put back", () => {
  let cuts = film.toggleRecording(source, film.defaultCuts(source), "A").cuts;
  assert.deepEqual(ranges(cuts), [["B", 0, 40]]);
  cuts = film.toggleRecording(source, cuts, "A").cuts;
  assert.deepEqual(ranges(cuts), [["A", 0, 60], ["B", 0, 40]]);
  assert.ok(film.isDefault(source, cuts));
});

test("short pieces are measured the way the server renders them: touching cuts count as one", () => {
  let cuts = film.split(source, film.defaultCuts(source), 2).cuts;  // 0–2 and 2–60 touch: one 60 s piece
  assert.equal(film.shortCuts(source, cuts, 5).size, 0);
  cuts = film.remove(source, cuts, cuts[1].id).cuts;                // 0–2 alone
  assert.deepEqual([...film.shortCuts(source, cuts, 5)], [cuts[0].id]);
});

test("server cuts of recordings missing from the scan are ignored", () => {
  const cuts = film.fromServer(source, [{ rec: "B", start: 1, end: 2 }, { rec: "gone", start: 0, end: 5 }]);
  assert.deepEqual(ranges(cuts), [["B", 1, 2]]);
  assert.deepEqual(film.toServer(cuts), [{ rec: "B", start: 1, end: 2 }]);
});
