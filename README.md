# GoPro Daily

Local web app for macOS. It joins GoPro chapter files (`GH01xxxx`, `GH02xxxx`, …) into **one video per day** and can add an overlay with **date/time and a GPS mini map**, using [gopro-dashboard-overlay](https://github.com/time4tea/gopro-dashboard-overlay).

## Quick path

```bash
brew install ffmpeg uv     # once
./run.sh                   # installs dependencies on first run and opens http://127.0.0.1:8765
```

1. **Source & output**: pick the SD card / DCIM folder and an output folder (the *Browse…* buttons open the macOS Finder dialog).
2. **Scan**: check the days, recordings, chapters and GPS indicator.
3. **Process selected**: follow progress, the ETA and the log. You get a macOS notification when each day is ready.

Result: `2026-07-01.mp4`, `2026-07-02.mp4`, … (the name is configurable).

## File names

Two naming schemes are recognised:

- **Native GoPro** (`GXccnnnn.MP4` HEVC, `GHccnnnn.MP4` H.264): `cc` is the chapter (01, 02, …), `nnnn` the file/recording number. The camera splits a long recording into several chapters purely because of file size — chapters of the same `nnnn` are one continuous recording and are always joined losslessly. A new `nnnn` means the user stopped and started again. Native names carry no date: the day and start time come from the MP4 `creation_time` (the camera's local clock), so set the camera clock (the Quik app syncs it).
- **Dated, opt-in** (`YYYYMMDD_HHMMSS.mp4`, typical of phone cameras, not GoPro): off by default so phone clips in the same folder are not mixed into GoPro days; set *Dated file name pattern* to enable it. No chapters, no reliable metadata timestamp (the file has been re-muxed), so the start time is read straight from the filename instead. A macOS duplicate copy (`… 2.mp4`) is detected and greyed out in the scan results instead of being processed twice.

Whichever the source, once every recording has a start time, **continuity** between consecutive recordings of the same day is decided by the gap between one recording's end and the next one's start: at or under `continuity_gap_seconds` (default 3 s, covers clock slack) it's shown as "continuous · no pause"; above it, it's a real pause. This only affects how the scan results are displayed — every recording is still rendered as its own clip inside the day's output video.

## How it works

```
per recording:  chapters ──join (lossless, keeps GPS)──► recording
per kept part:  recording ──cut (lossless, only if edited)──► piece ──overlay (optional)──► result
per day:        results in chronological order ──concat (lossless)──► YYYY-MM-DD.mp4
```

| Decision | Why |
|----------|-----|
| Overlay rendered **per recording**, not per day | Each recording has its own GPS track, so the clock and map stay exact even with hours between recordings. |
| Join with `-c copy`, mapping the `gpmd` stream | No quality loss, and the GPS telemetry is still there for the overlay step. |
| Final concat without re-encoding | Every recording goes through the same encoder. If formats differ (e.g. 4K + 1080p), the day is normalised and re-encoded automatically. |
| Clock uses `TZ=<timezone>` | The library shows GPS time (UTC) in the process timezone, so it matches the configured zone. |
| Recordings without GPS are kept without overlay | Configurable: you can make the whole job fail instead. |

## macOS optimisations

- Hardware **decoding and encoding with VideoToolbox** (HEVC/H.264/ProRes on the Apple GPU).
- HEVC tagged `hvc1`, so it plays in QuickTime, Photos and Finder.
- **1440p output** (`encoding.resolution`): at 4K the single hardware encoder is the limit (~3.9x realtime). Scaling to 1440p on the GPU before the overlay exports at ~7.8x. It never scales up, and it only applies when the overlay is on.
- **Estimates before you export**: every film shows its expected size and export time, and the export is refused up front when the output folder is read-only or a drive is too small (the film is written to the temp folder, then joined in the output folder).
- **Parallel recordings** (`parallel_jobs`): overlay drawing is single-threaded Python, so running several at once uses the cores of Apple Silicon.
- `caffeinate` keeps the Mac awake while there are jobs.
- Temp files go in the output folder by default (same disk, so no copies between volumes).
- The output file keeps the capture date, so Finder and Photos sort it correctly.

## Configuration

Everything is edited from the web UI and saved to `config.json`. Main options:

| Section | Options |
|---------|---------|
| Grouping | per day / per recording / everything together · timezone · hour the day starts (night sessions) · ignore short recordings · continuity gap threshold · native/dated file name regexes |
| Output | name template (`{date}` `{start}` `{end}` `{group}` `{recordings}`) · mp4/mov · overwrite · keep intermediate files |
| Overlay | on/off · generated layout, library default, or **your own XML** · font · margin |
| Widgets | date/time (strftime format, size, colour, corner) · mini map (size, zoom, opacity, rotation) · full route map · speed |
| Map | tile style (OSM, Thunderforest, Geoapify…) · API key · GPS filters (DOP, max speed) |
| Encoding | HEVC/H.264/ProRes on the Apple GPU or x264/x265 on the CPU · constant quality or bitrate · source or 1440p resolution · audio copy/AAC · parallelism |

Sizes are defined for a 1920 px wide frame and scale to the real resolution (2.7K, 4K, 5.3K…).

### Interactive overlay preview

Drag a widget to move it, drag its corner handle to resize it, and use the arrow keys to nudge it (Shift moves it faster). Double-click sends it back to its corner. Hold Alt while dragging to turn off snapping to the margins and the centre.

The preview draws exactly what gets rendered. The server computes each widget's geometry with the same code that writes the render XML (`layout.build_widgets`), measuring the text with the real font through PIL, as the library does. The browser also loads that same font file. A dragged widget is stored as `position` (its top-left corner as a fraction of the frame), so it stays in place at any resolution.

### Route map: one line for the whole day, drawn as you move

The *Route map* widget does not use the library's `journey_map`, which draws the full route of a single video from the first frame. It uses a custom widget, `day_journey_map`:

1. Before rendering, the GPS of **every recording in the output video** is extracted with the library's own loader, DOP/speed filters and smoothing. It goes into one shared track (`group_track.json`).
2. Every recording's overlay draws from that same track with the **same map framing** (the whole day's area). On each frame, the line is drawn up to the current GPS time.
3. So recording 2 starts with recording 1's route already drawn, and in the joined video it reads as one line growing all day.

| Option | Values |
|--------|--------|
| Route line | drawn as you move / whole route from the start |
| Route covers | the whole output video (continuous) / each recording on its own |
| Between recordings | solid / dashed / no line (how the end of one recording joins the next) |
| Show upcoming route | 0 = hidden, >0 = the route still to come, faded |
| Line & marker | colour, width, size |

The widget is added to the library at runtime (`python -m gopro_daily.dashboard` registers it and then runs `gopro-dashboard.py`), so no fork is needed. The line is drawn incrementally on a cached image, so each frame only draws its new segment.

### Footage editor

*Preview & edit* on a day (or a click on any thumbnail) opens the editor. It plays every clip of the film back to back from the camera's low-res `.LRV` proxies, so it is instant even for 4K footage.

- **Split** (S), **Start here / End here** (I / O, mark in / out) at the playhead, or drag the handles of the selected part to trim it.
- **Remove** (Delete) a part, **Put back** (R) a removed one, or switch a whole clip off in the clip strip.
- Undo / redo (⌘Z / ⇧⌘Z), timeline zoom (+ / − / Z, or pinch), frame steps (, / .), jump between edit points (↑ / ↓), speeds 1–16× (1–5).
- Playback skips the removed parts, so you watch the film as it will be exported (P plays everything instead).

Edits are saved as you make them (`edits.json`, next to `config.json`) and applied on export; the source files are never modified. The cuts are lossless: each kept part is copied out of the joined recording without re-encoding, so its start snaps back to the keyframe before it (GoPro writes one about every second). Parts shorter than 5 s are exported without the overlay, because the GPS renderer needs a few seconds of data; the editor flags them with a dashed border.

The editor lives in `static/editor/` as ES modules. `film.js` holds the model and every editing operation as pure functions (tested in `tests/js/`); `commands.js` is the single table of toolbar buttons and shortcuts. A new tool is an operation in `film.js` plus an entry in `commands.js`.

### Other uses

- **Another camera or renamed files**: change the regex (*Show advanced settings*). The native pattern needs the named groups `recording` and `chapter`; the dated pattern needs `date` and `time` (leave it empty to disable that naming scheme entirely).
- **Custom dashboard**: layout mode *Custom XML file* ([widget docs](https://github.com/time4tea/gopro-dashboard-overlay/tree/main/docs/xml)).
- **External GPS data (Strava, Garmin)**: extra arguments, e.g. `--gpx /path/ride.gpx --gpx-merge OVERWRITE`.
- **Join only, no overlay**: turn off *Add overlay*. It is lossless and very fast.

## Development

```bash
uv sync
uv run pytest                                         # unit tests
node --test tests/js                                  # editor model tests
GOPRO_SAMPLE=/path/GH010001.MP4 uv run pytest         # + end-to-end test with a real file
uv run gopro-daily --no-browser --port 8765
```

| File | Responsibility |
|------|----------------|
| `gopro_daily/config.py` | Config model (pydantic) with validation |
| `gopro_daily/scanner.py` | Finds chapters, rebuilds recordings (ffprobe), groups them |
| `gopro_daily/layout.py` | Generates the overlay XML layout, scaled to the resolution |
| `gopro_daily/track.py` | Extracts the GPS of every recording in a group into one shared track |
| `gopro_daily/day_journey_map.py` | Progressive, day-long route map widget (registered into the library) |
| `gopro_daily/dashboard.py` | Runs `gopro-dashboard.py` with the custom widgets registered |
| `gopro_daily/edits.py` | Film edits from the editor: stored cuts → pieces to render |
| `gopro_daily/pipeline.py` | join → cut → overlay → concat, ffmpeg commands and progress |
| `gopro_daily/jobs.py` | Job queue, cancellation, caffeinate, notifications |
| `gopro_daily/server.py` | FastAPI API + static files |
| `gopro_daily/static/` | Web UI (vanilla JS, no build step); `preview.js` is the interactive layout editor, `editor/` the footage editor |

## Limitations

- The overlay re-encodes the video. For 4K60 expect about real time or slower, depending on the number of widgets; the moving map is the most expensive one.
- Map tiles are downloaded from the internet the first time and cached in `.cache/`.
- If the camera clock was wrong, grouping by day uses that date. The overlay clock comes from GPS, so it is always correct.
