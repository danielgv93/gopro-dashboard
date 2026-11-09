"""Processing pipeline for one output group (a day by default).

Overlay off (never touches the encoder):
    Per recording:  join chapters (lossless, keeps GPS telemetry)
    Per piece:      cut the kept range out of the joined recording (lossless, only when the film was edited)
Overlay on (keeps the single hardware encoder busy, see docs/design/per-chapter-export.md):
    Per unit:       cut the kept range out of its chapter file (lossless, only for partial chapters)
                    ->  render overlay (re-encodes), units in parallel, straight from the source
Per group:          concatenate the pieces / units in chronological order (lossless when formats match).

A piece is a kept range of a recording, a unit is a piece's range of one chapter (see edits.py). Unedited films have
one whole-recording piece per recording, and one whole-chapter unit per chapter.

Each recording's GPMD carries its own GPS clock, so the date/time and map stay exact even when there are hours between
recordings of the same day, and across the chapters of one recording.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import PROJECT_ROOT, AppConfig
from .edits import OVERLAY_MIN_SECONDS, Piece, Span, Unit, plan_pieces, plan_units
from .layout import generate_layout
from .scanner import Group, Recording, ffprobe_bin, probe
from .track import extract_points, write_track

PROFILE_NAME = "gopro-daily"
CACHE_DIR = PROJECT_ROOT / ".cache"


class Cancelled(Exception):
    pass


class StepFailed(Exception):
    pass


@dataclass
class Context:
    """What the pipeline needs from the job runner: logging, progress reporting and cancellation."""

    log: Callable[[str], None]
    progress: Callable[[str, str, float], None]  # (task_id, stage, fraction 0..1)
    cancel_event: threading.Event
    processes: set[subprocess.Popen]
    lock: threading.Lock
    step: Callable[[str], None] = lambda _: None  # what the job is doing right now, shown in the export queue

    def cancel(self) -> None:
        self.cancel_event.set()
        with self.lock:
            for proc in self.processes:
                _kill_tree(proc, signal.SIGTERM)


def _kill_tree(proc: subprocess.Popen, sig: int) -> None:
    """Signals the whole process group: gopro-dashboard runs ffmpeg as a child that would otherwise keep rendering
    (and keep our stdout pipe open, so `run` never returns)."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


# --------------------------------------------------------------------------- binaries & encoding options


def ffmpeg_bin(config: AppConfig) -> str:
    return str(Path(config.encoding.ffmpeg_dir) / "ffmpeg") if config.encoding.ffmpeg_dir else "ffmpeg"


def dashboard_bin() -> str:
    local = Path(sys.executable).parent / "gopro-dashboard.py"
    return str(local) if local.exists() else "gopro-dashboard.py"


def dashboard_cmd() -> list[str]:
    """gopro-dashboard.py with this app's custom widgets registered (see dashboard.py)."""
    return [sys.executable, "-m", "gopro_daily.dashboard"]


def input_args(config: AppConfig) -> list[str]:
    return ["-hwaccel", "videotoolbox"] if config.encoding.hw_decode else []


def video_encode_args(config: AppConfig) -> list[str]:
    enc = config.encoding
    args = ["-c:v", enc.codec]
    if enc.codec in ("libx264", "libx265"):
        crf = round(51 - enc.quality * 0.4)
        args += ["-preset", "medium"]
        args += ["-crf", str(crf)] if enc.rate_control == "quality" else ["-b:v", enc.bitrate]
    elif enc.codec == "prores_videotoolbox":
        args += ["-profile:v", "hq"]
    else:
        args += ["-q:v", str(enc.quality)] if enc.rate_control == "quality" else ["-b:v", enc.bitrate]
        # The hardware encoder is the export bottleneck; speed priority measured 1.8x faster at the same size/SSIM.
        args += ["-prio_speed", "1"]
    if enc.codec in ("hevc_videotoolbox", "libx265"):
        args += ["-tag:v", "hvc1"]  # QuickTime / Photos only play HEVC tagged as hvc1
    if enc.codec in ("h264_videotoolbox", "libx264"):
        args += ["-pix_fmt", "yuv420p"]
    return args


def audio_encode_args(config: AppConfig) -> list[str]:
    return ["-c:a", "copy"] if config.encoding.audio == "copy" else ["-c:a", "aac", "-b:a", "192k"]


def write_ffmpeg_profile(config: AppConfig, config_dir: Path) -> None:
    """gopro-dashboard reads encoder settings from <config-dir>/ffmpeg-profiles.json."""
    config_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        PROFILE_NAME: {
            "input": input_args(config),
            # The 10 fps overlay stream usually ends after the video, and plain `overlay` then repeats the last video
            # frame (+1 frame and an audio gap at every unit join). Padding it forever + shortest=1 ends at the video.
            "filter": "[1:v]tpad=stop=-1:stop_mode=clone[ov];[0:v][ov]overlay=shortest=1",
            "output": [*video_encode_args(config), *audio_encode_args(config)],
        }
    }
    (config_dir / "ffmpeg-profiles.json").write_text(json.dumps(profile, indent=2))


# --------------------------------------------------------------------------- process runner

_FFMPEG_TIME = re.compile(r"out_time_us=(\d+)")
_DASHBOARD_PCT = re.compile(r"\[\s*(\d+)%\]")


def run(
    cmd: list[str],
    ctx: Context,
    on_line: Callable[[str], None] | None = None,
    env: dict | None = None,
) -> None:
    if ctx.cancel_event.is_set():
        raise Cancelled()
    ctx.log("$ " + " ".join(_quote(c) for c in cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, bufsize=0,
        start_new_session=True,  # own process group, so cancel reaches its children too
    )
    with ctx.lock:
        ctx.processes.add(proc)
    tail: list[str] = []
    buffer = b""
    try:
        assert proc.stdout
        while chunk := proc.stdout.read(4096):
            buffer += chunk
            *lines, buffer = re.split(rb"[\r\n]", buffer)
            for raw in lines:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if on_line:
                    on_line(line)
                if not line.startswith(("frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time",
                                        "dup_frames", "drop_frames", "speed=", "progress=", "Render:")):
                    ctx.log(line)
                    tail = (tail + [line])[-15:]
        code = proc.wait()
    finally:
        _kill_tree(proc, signal.SIGKILL)  # also reaps children left behind by a finished parent
        with ctx.lock:
            ctx.processes.discard(proc)
    if ctx.cancel_event.is_set():
        raise Cancelled()
    if code != 0:
        program = cmd[2] if cmd[1:2] == ["-m"] else Path(cmd[0]).name
        raise StepFailed(f"{program} exited with code {code}: " + " | ".join(tail[-5:]))


def _quote(arg: str) -> str:
    return f"'{arg}'" if (" " in arg or not arg) else arg


def ffmpeg_progress(ctx: Context, task: str, stage: str, duration: float) -> Callable[[str], None]:
    def handle(line: str) -> None:
        if m := _FFMPEG_TIME.search(line):
            ctx.progress(task, stage, min(1.0, int(m.group(1)) / 1e6 / max(duration, 0.001)))

    return handle


# --------------------------------------------------------------------------- steps


def join_chapters(rec: Recording, dest: Path, config: AppConfig, ctx: Context, task: str) -> Path:
    """Lossless chapter concatenation that keeps the GPMF (GPS) stream, like gopro-join.py does."""
    if len(rec.chapters) == 1:
        return rec.chapters[0].path
    list_file = dest.with_suffix(".txt")
    list_file.write_text("".join(f"file '{_escape_concat(c.path)}'\n" for c in rec.chapters))
    maps = ["-map", "0:v:0", "-map", "0:a?"]
    if rec.gpmd_index is not None:
        maps += ["-map", f"0:{rec.gpmd_index}"]
    run(
        [ffmpeg_bin(config), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-map_metadata", "0", *maps, "-copy_unknown", "-c", "copy", str(dest)],
        ctx,
        ffmpeg_progress(ctx, task, "join", rec.duration),
    )
    list_file.unlink(missing_ok=True)
    return dest


def cut_piece(src: Path, piece: Piece | Span, dest: Path, config: AppConfig, ctx: Context) -> Path:
    """Lossless cut of a piece out of its joined recording (or a span out of its chapter), keeping the GPS stream.

    Streams are copied, so the in-point snaps back to the keyframe before it (GoPro writes one about every second)
    and the out-point is exact. `-avoid_negative_ts make_zero` turns that pre-roll into real frames instead of an
    edit list, so every later step (overlay, concat) sees the frames a player shows, starting with the GPS data."""
    maps = ["-map", "0:v:0", "-map", "0:a?"]
    if (gpmd := _gpmd_index(probe(src, ffprobe_bin(config)))) is not None:
        maps += ["-map", f"0:{gpmd}", "-copy_unknown"]
    run(
        [ffmpeg_bin(config), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1",
         "-ss", f"{piece.start:.3f}", "-i", str(src), "-t", f"{piece.duration:.3f}",
         "-map_metadata", "0", *maps, "-c", "copy", "-avoid_negative_ts", "make_zero", str(dest)],
        ctx,
    )  # no progress: a stream copy takes seconds, and reporting it would restart the finished "join" bar
    return dest


def render_overlay(src: Path, rec: Recording, dest: Path, layout_xml: Path, config: AppConfig, ctx: Context,
                   task: str, config_dir: Path) -> Path:
    ov = config.overlay
    cmd = [
        *dashboard_cmd(),
        "--config-dir", str(config_dir),
        "--cache-dir", str(CACHE_DIR),
        "--profile", PROFILE_NAME,
        "--font", ov.font,
        "--units-speed", ov.units_speed,
        "--map-style", ov.map_style,
        "--gps-dop-max", str(ov.gps_dop_max),
        "--gps-speed-max", str(ov.gps_speed_max),
    ]
    if ffdir := config.encoding.ffmpeg_dir:
        cmd += ["--ffmpeg-dir", ffdir]
    if ov.map_api_key:
        cmd += ["--map-api-key", ov.map_api_key]
    if ov.layout_mode == "builtin":
        cmd += ["--layout", "default"]
    else:
        cmd += ["--layout", "xml", "--layout-xml", str(layout_xml)]
    cmd += [*ov.extra_args, str(src), str(dest)]

    # The library formats GPS (UTC) time with the process' local timezone -> drive it through TZ.
    env = {**os.environ, "TZ": config.grouping.timezone, "PYTHONUNBUFFERED": "1",
           # progressbar2 stays silent without a TTY; force it so we can parse the render percentage
           "PROGRESSBAR_IS_TERMINAL": "1", "PROGRESSBAR_ENABLE_COLORS": "0", "PROGRESSBAR_MINIMUM_UPDATE_INTERVAL": "1"}

    def handle(line: str) -> None:
        if m := _DASHBOARD_PCT.search(line):
            ctx.progress(task, "overlay", int(m.group(1)) / 100)

    run(cmd, ctx, handle, env=env)
    return dest


def encode_plain(src: Path, dest: Path, config: AppConfig, ctx: Context, task: str, duration: float) -> Path:
    """Re-encodes without overlay, with the overlay's encoder. A unit that falls back must not be a copy of the camera's
    stream: a file that mixes it with VideoToolbox output does not play in Apple apps."""
    run([ffmpeg_bin(config), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *input_args(config),
         "-i", str(src), "-map", "0:v:0", "-map", "0:a?", *video_encode_args(config), *audio_encode_args(config),
         str(dest)], ctx, ffmpeg_progress(ctx, task, "overlay", duration))
    return dest


def concat_products(products: list[Path], dest: Path, config: AppConfig, ctx: Context, total: float,
                    keep_telemetry: bool, progress: Callable[[str], None] | None = None) -> None:
    ffmpeg = ffmpeg_bin(config)
    infos = [probe(p, ffprobe_bin(config)) for p in products]
    signatures = {_signature(i) for i in infos}
    progress = progress or ffmpeg_progress(ctx, "group", "concat", total)
    tmp = dest.with_name(dest.stem + ".partial" + dest.suffix)

    if len(signatures) == 1:
        list_file = dest.with_name(dest.stem + ".concat.txt")
        list_file.write_text("".join(f"file '{_escape_concat(p)}'\n" for p in products))
        maps = ["-map", "0:v:0", "-map", "0:a?"]
        gpmd = _gpmd_index(infos[0])
        if keep_telemetry and gpmd is not None and all(_gpmd_index(i) is not None for i in infos):
            maps += ["-map", f"0:{gpmd}", "-copy_unknown"]
        run([ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-map_metadata", "0", *maps, "-c", "copy", str(tmp)], ctx, progress)
        list_file.unlink(missing_ok=True)
    else:
        # Different resolutions/codecs/fps: normalise everything to the first recording's format.
        ctx.log("Recordings have different formats -> re-encoding the group (slower).")
        ref = next(s for s in infos[0]["streams"] if s["codec_type"] == "video")
        w, h, fps = ref["width"], ref["height"], ref["r_frame_rate"]
        has_audio = all(any(s["codec_type"] == "audio" for s in i["streams"]) for i in infos)
        inputs, filters, labels = [], [], ""
        for n, p in enumerate(products):
            inputs += [*input_args(config), "-i", str(p)]
            filters.append(
                f"[{n}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps={fps},format=yuv420p[v{n}]"
            )
            labels += f"[v{n}]" + (f"[{n}:a:0]" if has_audio else "")
        filters.append(f"{labels}concat=n={len(products)}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else ""))
        audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if has_audio else []
        run([ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *inputs,
             "-filter_complex", ";".join(filters), "-map", "[v]", *video_encode_args(config), *audio, str(tmp)],
            ctx, progress)
    tmp.replace(dest)


def _signature(info: dict) -> tuple:
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    return (video.get("codec_name"), video.get("width"), video.get("height"), video.get("r_frame_rate"),
            video.get("pix_fmt"), audio.get("codec_name"), audio.get("sample_rate"), audio.get("channels"))


def _gpmd_index(info: dict) -> int | None:
    return next((int(s["index"]) for s in info["streams"] if s.get("codec_tag_string") == "gpmd"), None)


def _escape_concat(path: Path) -> str:
    return str(path).replace("'", "'\\''")


# --------------------------------------------------------------------------- orchestration


def output_path(group: Group, config: AppConfig) -> Path:
    starts = [r.start for r in group.recordings if r.start]
    first, last = (min(starts), max(starts)) if starts else (None, None)
    fmt = lambda d: d.strftime("%Y-%m-%d_%H%M") if d else ""  # noqa: E731
    name = config.output.filename_template.format(
        date=first.date().isoformat() if first else group.label,
        start=fmt(first),
        end=fmt(last),
        group=group.label,
        recordings=len(group.recordings),
    )
    name = re.sub(r"[/:\\]", "-", name).strip() or group.label
    return Path(config.output.output_dir).expanduser() / f"{name}.{config.output.container}"


def process_group(group: Group, config: AppConfig, ctx: Context, pieces: list[Piece] | None = None) -> Path:
    """Renders the film. `pieces` are the kept ranges from the editor; None renders every recording whole."""
    dest = output_path(group, config)
    if dest.exists() and not config.output.overwrite:
        raise StepFailed(f"{dest} already exists (enable 'overwrite' to replace it)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Fail before hours of rendering, e.g. an NTFS drive that macOS mounts read-only.
    if not os.access(dest.parent, os.W_OK):
        raise StepFailed(f"Cannot write to {dest.parent} (read-only drive? macOS mounts NTFS read-only)")

    for r in group.recordings:
        if r.error:
            ctx.log(f"Skipping {r.key}: {r.error}")
    pieces = plan_pieces(group, None) if pieces is None else pieces
    if not pieces:
        raise StepFailed("Nothing to export: no valid recordings, or every clip was removed in the editor")
    recordings = list({p.rec.key: p.rec for p in pieces}.values())

    base_work = work_root(config) / "exports"
    work = base_work / f"{group.key}-{datetime.now():%Y%m%d%H%M%S}"
    work.mkdir(parents=True, exist_ok=True)
    config_dir = work / "config"
    overlay_on = config.overlay.enabled
    if overlay_on:
        write_ffmpeg_profile(config, config_dir)
        CACHE_DIR.mkdir(exist_ok=True)

    errors: list[Exception] = []

    def parallel(fn, items):
        """Runs fn over items in parallel; the first real failure cancels the siblings and is re-raised."""
        def guarded(item):
            try:
                return fn(item)
            except Exception as e:
                if not isinstance(e, Cancelled) and not errors:
                    errors.append(e)
                    ctx.cancel()  # the group cannot be completed anyway
                raise
        try:
            with ThreadPoolExecutor(max_workers=config.encoding.parallel_jobs) as pool:
                return list(pool.map(guarded, items))
        except Cancelled:
            if errors:
                raise errors[0] from None
            raise

    name_of = lambda rec: f"{rec.letter}{rec.number:04d}"  # noqa: E731
    piece_name = lambda i, p: name_of(p.rec) if p.full else f"{name_of(p.rec)}_{i:02d}"  # noqa: E731

    try:
        if overlay_on:
            products = _overlay_units(pieces, recordings, work, config_dir, config, ctx, parallel)
        else:
            # 1. join chapters (lossless), only for recordings that still have something in the film -----------
            def join(rec: Recording) -> Path:
                ctx.log(f"[{name_of(rec)}] {len(rec.chapters)} chapter(s), {rec.duration / 60:.1f} min")
                return join_chapters(rec, work / f"{name_of(rec)}_joined.mp4", config, ctx, rec.key)

            ctx.step(f"Joining the chapters of {len(recordings)} recording(s)")
            joined = dict(zip([r.key for r in recordings], parallel(join, recordings)))

            # 2. cut the kept ranges out of the joined recordings (lossless) -----------------------------------
            def cut(item: tuple[int, Piece]) -> Path:
                i, piece = item
                src = joined[piece.rec.key]
                if not piece.full:
                    ctx.log(f"[{name_of(piece.rec)}] keeping {piece.start:.1f}s–{piece.end:.1f}s")
                    src = cut_piece(src, piece, work / f"{piece_name(i, piece)}_cut.mp4", config, ctx)
                ctx.progress(piece.id, "join", 1.0)
                return src

            if not all(p.full for p in pieces):
                ctx.step("Cutting the kept ranges")
            products = parallel(cut, list(enumerate(pieces)))

        ctx.step(f"Joining {len(products)} part(s) into the final video")
        ctx.log(f"Concatenating {len(products)} part(s) -> {dest}")
        concat_products(products, dest, config, ctx, sum(p.duration for p in pieces), keep_telemetry=not overlay_on)
        ctx.progress("group", "concat", 1.0)
        _copy_times(recordings[0].chapters[0].path, dest)
    finally:
        ctx.step("Cleaning up temporary files")
        _cleanup(work, base_work, config)
    return dest


def _overlay_units(pieces: list[Piece], recordings: list[Recording], work: Path, config_dir: Path, config: AppConfig,
                   ctx: Context, parallel) -> list[Path]:
    """Renders the overlay per chapter unit, in parallel and straight from the source files. Returns the rendered
    units in film order (`parallel` keeps the order of its items)."""
    units = plan_units(pieces)
    name_of = lambda u: f"{u.piece.rec.letter}{u.piece.rec.number:04d}"  # noqa: E731
    unit_name = lambda i, u: f"{name_of(u)}_{i:03d}"  # noqa: E731

    # 1. cut only the partial units (lossless); whole chapters are rendered from the chapter file as is ----------
    def prepare(item: tuple[int, Unit]) -> Path:
        i, unit = item
        if unit.full:
            return unit.spans[0].chapter.path
        name = unit_name(i, unit)
        ctx.log(f"[{name}] keeping " + ", ".join(f"{s.chapter.path.name} {s.start:.1f}s–{s.end:.1f}s" for s in unit.spans))
        # Every span is cut, even a whole chapter, so all parts share one stream layout for the concat demuxer.
        parts = [cut_piece(s.chapter.path, s, work / f"{name}_cut{n}.mp4", config, ctx) for n, s in enumerate(unit.spans)]
        if len(parts) == 1:
            return parts[0]
        dest = work / f"{name}_cut.mp4"
        concat_products(parts, dest, config, ctx, unit.duration, keep_telemetry=True, progress=lambda _: None)
        return dest

    for rec in recordings:
        ctx.log(f"[{rec.letter}{rec.number:04d}] {len(rec.chapters)} chapter(s), {rec.duration / 60:.1f} min")
    if not all(u.full for u in units):
        ctx.step("Cutting the kept ranges")
    sources = parallel(prepare, list(enumerate(units)))

    # 2. GPS track shared by the whole group (continuous, progressive journey map) -------------------------------
    tracks: dict[str, Path] = {}
    if config.overlay.layout_mode == "generated" and config.overlay.journey_map.enabled:
        tracks = build_tracks(recordings, work, config, ctx)

    # 3. overlay per unit; its progress is summed into its piece's task, which is what the job view shows ---------
    done: dict[int, float] = {}  # unit index -> footage seconds rendered
    finished: set[int] = set()

    def report(i: int, unit: Unit, fraction: float) -> None:
        done[i] = min(1.0, fraction) * unit.duration
        siblings = [j for j, u in enumerate(units) if u.piece is unit.piece]
        if all(j in finished for j in siblings):
            ctx.progress(unit.piece.id, "overlay", 1.0)  # exact, so the task is marked finished
        else:
            ctx.progress(unit.piece.id, "overlay", min(1.0, sum(done.get(j, 0.0) for j in siblings) / unit.piece.duration))

    def overlay(item: tuple[int, Unit, Path]) -> Path:
        i, unit, src = item
        rec, name = unit.piece.rec, unit_name(i, unit)
        uctx = replace(ctx, progress=lambda _task, _stage, fraction: report(i, unit, fraction))

        def plain(reason: str) -> Path:
            ctx.log(f"[{name}] {reason} -> keeping it without overlay")
            return encode_plain(src, work / f"{name}_plain.mp4", config, uctx, name, unit.duration)

        if not rec.has_gpmd and config.overlay.fallback_without_overlay:
            rendered = plain("no GPS telemetry stream")
        elif unit.duration < OVERLAY_MIN_SECONDS:
            message = f"cut shorter than {OVERLAY_MIN_SECONDS:g} s: too little GPS for the overlay renderer"
            if not config.overlay.fallback_without_overlay:
                raise StepFailed(f"[{name}] {message}")
            rendered = plain(message)
        else:
            layout_xml = work / f"{name}_layout.xml"
            if config.overlay.layout_mode == "xml":
                shutil.copy(Path(config.overlay.layout_xml_path).expanduser(), layout_xml)
            else:
                track = tracks.get(rec.key)
                layout_xml.write_text(
                    generate_layout(config.overlay, rec.width, rec.height, str(track) if track else None))
            try:
                rendered = render_overlay(src, rec, work / f"{name}_overlay.mp4", layout_xml, config, uctx, name,
                                          config_dir)
            except StepFailed as e:
                if not config.overlay.fallback_without_overlay:
                    raise
                rendered = plain(f"overlay failed ({e})")
        finished.add(i)
        report(i, unit, 1.0)
        return rendered

    ctx.step(f"Drawing the overlay on {len(units)} chapter clip(s), {config.encoding.parallel_jobs} at a time")
    return parallel(overlay, [(i, u, src) for (i, u), src in zip(enumerate(units), sources)])


def work_root(config: AppConfig) -> Path:
    """Parent of all temporary files: export work in exports/, local footage copies in footage/.

    Defaults to ~/Library/Caches, not the system temp dir: macOS purges files there after ~3 days unused,
    and copied footage keeps the SD card's old timestamps, so it would vanish while still being edited."""
    return Path(config.output.work_dir).expanduser() if config.output.work_dir else Path.home() / "Library/Caches/gopro-daily-work"


def _cleanup(work: Path, base_work: Path, config: AppConfig) -> None:
    if config.output.keep_intermediates:
        return
    shutil.rmtree(work, ignore_errors=True)
    try:
        base_work.rmdir()
    except OSError:
        pass


def _gps_worker_init() -> None:
    """Pool.terminate only signals the worker: take its ffmpeg/ffprobe down with it."""
    os.setsid()
    signal.signal(signal.SIGTERM, lambda *_: os.killpg(0, signal.SIGKILL))


def build_tracks(recordings: list[Recording], work: Path, config: AppConfig, ctx: Context) -> dict[str, Path]:
    """Extracts every chapter's GPS (in parallel) and writes the track file(s) the journey map widget draws from."""
    chapters = [(rec, ch) for rec in recordings if rec.has_gpmd for ch in rec.chapters]
    ctx.step(f"Reading GPS for the journey map · 0/{len(chapters)} chapters")
    by_rec: dict[str, list] = {}
    # Processes, not threads: parsing GPMD is pure-Python CPU work (~7 s a chapter), so threads queue on the GIL
    # and cancel had to wait ~1 min for them. Leaving the `with` terminates the workers at once.
    with multiprocessing.get_context("spawn").Pool(config.encoding.parallel_jobs, initializer=_gps_worker_init) as pool:
        pending = [pool.apply_async(extract_points, (ch.path, config)) for _, ch in chapters]
        for i, ((rec, chapter), result) in enumerate(zip(chapters, pending), 1):
            while not result.ready():
                if ctx.cancel_event.is_set():
                    raise Cancelled()
                result.wait(0.25)
            try:
                by_rec.setdefault(rec.key, []).extend(result.get())
            except Exception as e:  # noqa: BLE001 - a broken GPS stream must not kill the whole day
                ctx.log(f"[{rec.letter}{rec.number:04d}] could not read GPS of {chapter.path.name} for the journey map: {e}")
            ctx.step(f"Reading GPS for the journey map · {i}/{len(chapters)} chapters")
    segments = [by_rec.get(rec.key, []) for rec in recordings]
    for rec, points in zip(recordings, segments):
        if rec.has_gpmd:
            ctx.log(f"[{rec.letter}{rec.number:04d}] {len(points)} GPS points for the journey map")
    if config.overlay.journey_map.scope == "recording":
        tracks = {}
        for rec, points in zip(recordings, segments):
            tracks[rec.key] = work / f"{rec.letter}{rec.number:04d}_track.json"
            write_track([points], tracks[rec.key])
        return tracks
    group_track = work / "group_track.json"
    total = write_track(segments, group_track)
    ctx.log(f"Journey map: continuous track of {total} points across {len(recordings)} recording(s)")
    return {rec.key: group_track for rec in recordings}


def _copy_times(src: Path, dest: Path) -> None:
    """Give the output the capture date so Finder/Photos sort it correctly."""
    stat = src.stat()
    os.utime(dest, (stat.st_atime, stat.st_mtime))
