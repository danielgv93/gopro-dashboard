# Per-chapter export: keep the encoder busy

**Status:** implemented (§2; §3 is a follow-up) · **Scope:** overlay exports (`overlay.enabled = true`) · **Date:** 2026-10-28

Exports with the overlay render one big piece per recording, after joining all its chapters into a single file.
The hardware encoder, which is the bottleneck, waits about 0.8 h per card while that join runs.
This proposal renders **each GoPro chapter as its own unit of work**. The camera already split the video frame-exactly
and gave every chapter its own GPS stream. Units read straight from the source, run on the existing worker pool,
and are joined once at the end.

**Expected result for the measured card** (one 10.8 h recording, 19 chapters, 219 GB, 4K HEVC):

| | Today | Per chapter |
|---|---|---|
| End-to-end, 4K | ~3.4 h | **~2.75 h** (−20 %) |
| End-to-end, 1080p (follow-up, §3) | ~2.3 h if added to today's pipeline | **< 1 h** |
| Temp space on the internal SSD | 219 GB join + 50 GB | ~50 GB |
| Encoder idle time | ~0.8 h | seconds |

---

## 1. Why: what the measurements say

The numbers come from `scripts/bench_pipeline.py` and `scripts/bench_resolution.py` on an M5 Pro, with logs in `logs/bench-*`.

| Fact | Measured | Consequence |
|---|---|---|
| The VT HEVC encoder is one engine | 4K: 129 fps alone, 123 fps total for 2 at once | 4K cannot encode faster; it can only stop waiting |
| The overlay export is encoder-bound | 4.21x realtime vs 4.24x for a pure encode | Python rendering is not the limit |
| SD sequential read | 94 MB/s | The encoder only consumes ~24 MB/s (5.6 MB/s of footage × 4.2) |
| Joining the chapters (stream copy) | 15.4x realtime → ~0.70 h for the card | Pure waiting time for the encoder |
| GPS extraction per chapter | 12.3 s, reads ~100 MB of 12 GB | Cheap, and it can run up front |
| A single 4K HEVC decode stream | ~207 fps; 2 streams 450, 3 streams 660 | At 1080p, parallel units scale ~2x |
| Concat of VT outputs from separate sessions | 0 errors in ffmpeg, plays fully in AVFoundation | Joining units with `-c copy` is safe |
| Concat of VT + x265 (or another encoder) | Broken in AVFoundation (`hvc1`) or unplayable (`hev1`) | **Every unit must come from the same encoder** |

### Today's timeline

```
time →     0h        0.7h                                       3.3h  3.4h
SD         ████████  join 19 chapters → one 219 GB file
CPU                 █ GPS track (minutes)
Encoder              ███████████████████████████████████████████  overlay, one process
External                                                           ██ concat
```

`encoding.parallel_jobs = 2` has no effect here: there is one piece per recording, so there is nothing to run in parallel.

### Proposed timeline

```
time →     0h  ~4 min                                           2.6h  2.75h
CPU        █   GPS of every chapter (parallel) → day track
Worker 1        ▓▓▓ ch01 ▓▓▓ ch03 ▓▓▓ ch05 …   reads the SD directly
Worker 2        ░░░ ch02 ░░░ ch04 ░░░ ch06 …   the two share the single encoder
Encoder         ███████████████████████████████████████████  never idle
External                                                       ██ concat
```

Two workers do **not** double 4K throughput, because they share one engine. They hide each process's startup
(Python imports, GPMD parse, map tiles) behind the other's encoding, so the engine never waits.

---

## 2. Design

### 2.1 New unit of work

```python
@dataclass(frozen=True)
class Unit:
    """A range of ONE chapter file that is rendered on its own. Units of a piece are contiguous."""
    piece: Piece          # the editor range it belongs to (progress and labels)
    chapter: Chapter
    start: float          # seconds inside the chapter file
    end: float
    @property
    def full(self) -> bool: ...   # whole chapter: render the file as is, no cut
```

`plan_units(pieces) -> list[Unit]` is a pure function in `edits.py`. It intersects each piece's range (recording
timeline) with the chapter ranges, which are the cumulative `Chapter.duration` values.

| Case | Units |
|---|---|
| Unedited recording | One `full` unit per chapter, with no cut |
| Piece inside one chapter | One partial unit (`cut_piece` from the chapter file) |
| Piece across a chapter boundary | A partial tail of chapter N + full middle chapters + a partial head of chapter M |
| Unit shorter than `OVERLAY_MIN_SECONDS` (5 s) | Merged with the neighbouring unit of the same piece: the two ranges are cut and joined (`join_chapters`) into one small file. gopro-dashboard crashes below 5 s |
| One-chapter recording | Same as today |

Boundaries between full chapters are exact, because the camera wrote them. Partial units snap their in-point to the
previous keyframe, which is today's `cut_piece` behaviour, unchanged.

### 2.2 Pipeline (`process_group`, overlay path only)

| Step | Today | Proposed |
|---|---|---|
| 1. Prepare | `join_chapters` per recording → one big file | Cut only the partial units (`cut_piece` from the chapter). Full units use the chapter path directly |
| 2. GPS track | `extract_points(joined)` per recording | `extract_points(chapter)` per chapter, run with `parallel`; points concatenated per recording, then `write_track` as today |
| 3. Overlay | One `render_overlay` per piece | One `render_overlay` per unit, on `parallel(…, units)` (`encoding.parallel_jobs` workers) |
| 4. Concat | `concat_products(pieces)` | `concat_products(units in film order)`, unchanged code |

Three rules:

- **Order:** the units are submitted in film order. `ThreadPoolExecutor.map` keeps that order in the result, and
  `concat_products` needs it.
- **Overlay off:** the path stays as it is today (join plus lossless concat). It never touches the encoder, so it has
  nothing to gain.
- **Source reads:** units read the source folder directly (SD or local copy). There is no join file, so the 219 GB of
  temp space goes away.

### 2.3 Files touched

| File | Change |
|---|---|
| `gopro_daily/edits.py` | `Unit`, `plan_units()` (pure, unit-tested) |
| `gopro_daily/pipeline.py` | Overlay path of `process_group` works on units; `build_tracks` extracts per chapter |
| `gopro_daily/jobs.py` | Unit progress is aggregated into its piece's task (see 2.4) |
| `tests/test_edits.py` | `plan_units` cases from the table in 2.1 |
| `tests/test_core.py` | New: `process_group` with `pipeline.run` stubbed, checking one render per chapter and concat in film order. Existing `GOPRO_SAMPLE` end-to-end tests: add a 2-chapter recording |
| `scripts/bench_pipeline.py` | Measure a 2-chapter recording end-to-end; add the AVFoundation check |

No config, API or UI change: the job view still shows one row per piece.

### 2.4 Progress

Job tasks stay keyed by `piece.id` (the UI is unchanged). Each unit reports
`fraction × unit.duration`. The piece's progress is `Σ unit seconds done / piece.duration`, reported through
`ctx.progress(piece.id, "overlay", …)`. With 2 workers, a piece moves smoothly through its chapters.

### 2.5 Failure handling

| Situation | Behaviour |
|---|---|
| A unit's overlay fails, `fallback_without_overlay` on | **Re-encode that unit with the same encoder and no overlay**, never copy the source. Mixing the camera's stream with VT output in one file breaks playback in Apple apps (see §1). ⚠ The same risk exists today when one recording falls back; fix it in the same change |
| A unit fails, fallback off | `parallel` cancels the siblings and the job fails, as today |
| SD removed mid-export | The job fails. The SD is now read for ~2.6 h instead of ~0.7 h, so the UI should suggest "Copy to Mac" for long films (follow-up, not in this change) |
| Cancel | Unchanged: `Context.cancel` terminates every running process |

---

## 3. Follow-up: 1080p output (separate change, built on units)

At 1080p the encoder is no longer the limit: decoding a single 4K stream is (~207 fps). Units in parallel fix that.

- Config: `output.resolution: "source" | "1080p"`.
- Profile for 1080p: input adds `-hwaccel_output_format videotoolbox_vld`, and the filter becomes
  `[0:v]scale_vt=w=1920:h=1080,hwdownload,format=nv12[v];[v][1:v]overlay`, with `--overlay-size 1920x1080` and the
  layout generated for 1920×1080.
- Measured overlay speed: 7.7x with 1 unit and **13.7x with 2 units in parallel**, versus 4.2x for 4K.
- The default quality for 1080p goes higher: VT `q 65` gives only 2.5 Mbps at 1080p. q70–75 costs no speed, since
  the encoder is not the limit.

---

## 4. Verification plan

- [ ] `plan_units` unit tests: unedited, inside one chapter, across a boundary, a tail under 5 s, one-chapter recording.
- [ ] Frame count of the output = sum of the chapters' frames (unedited recording).
- [ ] The output decodes fully in AVFoundation (`playable=true`, reader `completed`) and with `ffmpeg -v error` (0 lines).
- [ ] A/V stay in sync at chapter joins: check the audio/video PTS at each boundary, and listen at one join.
- [ ] The journey map line is continuous across chapter joins, and the clock never jumps.
- [ ] Benchmark on a 2–3 chapter recording: total ≈ GPS + footage/4.2x + concat; `iostat` shows SD reads below 30 MB/s.
- [ ] Peak temp space ≈ the overlay outputs only; no `*_joined.mp4` file.

## 5. Open questions

| Question | How to settle it |
|---|---|
| How long does one gopro-dashboard process take to start (per unit)? | Time the start of `render_overlay` until its first progress line. If it is > 30 s, 3 workers may be better than 2 |
| Is there an audio click or gap at chapter joins after the concat copy? | **Settled.** Plain `overlay` repeated the last frame when the 10 fps overlay stream outlived the video: +1 frame and a ~33 ms audio gap per join. The profile now uses `[1:v]tpad=stop=-1:stop_mode=clone[ov];[0:v][ov]overlay=shortest=1`, so every unit ends with its video: frames = source, audio continuous at the join |
| Should very long chapters be split further for 1080p parallelism on short recordings? | Only if a 1-chapter recording is common. The segment muxer (`-f segment`) splits without duplicating frames (verified) |

## Out of scope

- A hybrid VT + CPU encode. It is measured at +40 % throughput, but joining the results is unplayable in Apple apps.
- Two encode engines: they only exist on M-series Max/Ultra chips.
- Changing the overlay-off export.
