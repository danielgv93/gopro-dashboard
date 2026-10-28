"""Benchmark: the same overlay export at 4K and at 1080p (CPU scale, GPU scale, GPU scale in 2 parallel segments).

Cuts a SAMPLE-second piece from the SD card once, then for each resolution runs the real overlay renderer
(gopro-dashboard + VideoToolbox) and the final concat onto the external disk. 1080p scales the video in the ffmpeg
filter before the overlay is drawn on it, so the overlay is rendered at 1080p (the layout sizes are defined at 1080p).
`iostat` samples disks and CPU the whole time.

Keeps both exports plus one still frame of each on the external disk, so you can compare them.
Logs go to logs/bench-res-<timestamp>/: summary.txt, stages.json, iostat.log, pipeline.log.

    .venv/bin/python scripts/bench_resolution.py [--sample 120] [--offset 600] [--dest /Volumes/TOSHIBA]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench_pipeline import Sampler, physical_disk  # noqa: E402

from gopro_daily import pipeline  # noqa: E402
from gopro_daily.config import PROJECT_ROOT, load_config  # noqa: E402
from gopro_daily.edits import Piece  # noqa: E402
from gopro_daily.layout import generate_layout  # noqa: E402
from gopro_daily.scanner import probe, scan  # noqa: E402
from gopro_daily.track import extract_points, write_track  # noqa: E402

MB = 1e6
FULL_HD = (1920, 1080)
# label -> (output size or None for the source size, scaler, segments rendered in parallel)
VARIANTS = {"4k": (None, "cpu", 1), "1080p": (FULL_HD, "cpu", 1), "1080p-gpu": (FULL_HD, "gpu", 1),
            "1080p-gpu-x2": (FULL_HD, "gpu", 2)}
FILTERS = {"cpu": "[0:v]scale={w}:{h}:flags=lanczos[v];[v][1:v]overlay",
           # scale on the GPU while frames are still there, then only the small frame goes to the CPU overlay
           "gpu": "[0:v]scale_vt=w={w}:h={h},hwdownload,format=nv12[v];[v][1:v]overlay"}


def keyframe_near(src: Path, t: float) -> float:
    """Lossless cuts snap to keyframes, so segments must start on one or frames would be repeated."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
                          "frame=pts_time", "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout
    return min((float(x) for x in out.split()), key=lambda k: abs(k - t))


def ssim(export: Path, source: Path, size: tuple[int, int] | None) -> str:
    """SSIM of the export against the source (scaled the same way), over the first 20 s. The overlay counts too."""
    ref = f"scale={size[0]}:{size[1]}:flags=lanczos," if size else ""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-hwaccel", "videotoolbox", "-i", str(export), "-hwaccel", "videotoolbox",
         "-i", str(source), "-t", "20", "-lavfi", f"[1:v]{ref}setpts=PTS-STARTPTS[r];[0:v]setpts=PTS-STARTPTS[e];[e][r]ssim",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    return next((w.removeprefix("All:") for w in out.split() if w.startswith("All:")), "?")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=float, default=120, help="seconds of footage (default 120)")
    ap.add_argument("--offset", type=float, default=600, help="where the sample starts in the chapter, s (default 600)")
    ap.add_argument("--dest", type=Path, help="external disk folder (default: output.output_dir from config.json)")
    args = ap.parse_args()

    config = load_config()
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    logs = PROJECT_ROOT / "logs" / f"bench-res-{stamp}"
    logs.mkdir(parents=True)
    dest = (args.dest or Path(config.output.output_dir)) / f"gopro-daily-bench-res-{stamp}"
    dest.mkdir(parents=True)
    work = pipeline.work_root(config) / "exports" / f"bench-res-{stamp}"
    work.mkdir(parents=True)
    summary = (logs / "summary.txt").open("w")

    def say(msg: str = "") -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        summary.write(line + "\n")
        summary.flush()

    plog = (logs / "pipeline.log").open("w")
    ctx = pipeline.Context(log=lambda m: (plog.write(f"{datetime.now():%H:%M:%S} {m}\n"), plog.flush()),
                           progress=lambda *a: None, cancel_event=threading.Event(), processes=set(),
                           lock=threading.Lock())

    recordings = [r for g in scan(config) for r in g.recordings if not r.error and r.duration > 0]
    rec = max(recordings, key=lambda r: r.duration)
    chapter = rec.chapters[len(rec.chapters) // 2]
    say(f"Logs: {logs}")
    say(f"Exports: {dest}   work dir: {work}")
    say(f"Sample: {chapter.path.name} ({rec.width}x{rec.height} {rec.vcodec}), {args.sample:.0f} s from {args.offset:.0f} s, "
        f"encoder {config.encoding.codec} q={config.encoding.quality}")

    disks = {"sd": physical_disk(chapter.path), "external": physical_disk(dest), "work": physical_disk(work)}
    sampler = Sampler(disks, logs / "iostat.log")
    time.sleep(3)
    stages: list[dict] = []

    def stage(name: str, fn):
        t0 = time.time()
        result = fn()
        t1 = time.time()
        row = {"stage": name, "start": t0, "end": t1, "wall_s": round(t1 - t0, 1),
               "x_realtime": round(args.sample / (t1 - t0), 2), "fps": round(args.sample * 29.97 / (t1 - t0)),
               **sampler.stats(t0, t1)}
        stages.append(row)
        say(f"   {name}: " + ", ".join(f"{k}={v}" for k, v in row.items() if k not in ("stage", "start", "end")))
        return result

    say("-> cut sample from SD (shared by both exports)")
    one = dataclasses.replace(rec, chapters=[chapter], duration=chapter.duration)
    piece = Piece(one, args.offset, args.offset + args.sample)
    src = stage("cut", lambda: pipeline.cut_piece(chapter.path, piece, work / "sample_cut.mp4", config, ctx))
    track = work / "track.json"
    write_track([extract_points(src, config)], track)

    results = {}
    sample_rec = dataclasses.replace(one, duration=args.sample)
    for label, (size, scaler, segments) in VARIANTS.items():
        say(f"-> {label}")
        w, h = size or (rec.width, rec.height)
        config_dir = work / f"config-{label}"
        pipeline.write_ffmpeg_profile(config, config_dir)
        if size:  # scale the video before the overlay is drawn, so the overlay itself is rendered at 1080p
            profiles = config_dir / "ffmpeg-profiles.json"
            profile = json.loads(profiles.read_text())[pipeline.PROFILE_NAME]
            profile["filter"] = FILTERS[scaler].format(w=w, h=h)
            if scaler == "gpu":
                profile["input"] = [*profile["input"], "-hwaccel_output_format", "videotoolbox_vld"]
            profiles.write_text(json.dumps({pipeline.PROFILE_NAME: profile}, indent=2))
        layout = work / f"layout-{label}.xml"
        layout.write_text(generate_layout(config.overlay, w, h, str(track)))
        cfg = config.model_copy(deep=True)
        cfg.overlay.extra_args = [*cfg.overlay.extra_args, "--overlay-size", f"{w}x{h}"]
        parts = [src]
        if segments > 1:
            bounds = [0.0, *(keyframe_near(src, args.sample * i / segments) for i in range(1, segments)), args.sample]
            parts = [pipeline.cut_piece(src, Piece(sample_rec, a, b), work / f"seg{i}_{label}.mp4", config, ctx)
                     for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]

        def render_all():
            with ThreadPoolExecutor(max_workers=segments) as pool:
                return list(pool.map(lambda ip: pipeline.render_overlay(
                    ip[1], one, work / f"overlay_{label}_{ip[0]}.mp4", layout, cfg, ctx, label, config_dir),
                    enumerate(parts)))

        overlaid = stage(f"overlay {label}", render_all)
        out = dest / f"export_{label}.mp4"
        stage(f"concat {label} -> external", lambda: pipeline.concat_products(
            overlaid, out, config, ctx, args.sample, keep_telemetry=False))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(args.sample / 2), "-i", str(out), "-frames:v", "1",
                        str(dest / f"frame_{label}.png")], check=True)
        info = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
        results[label] = {"file": str(out), "resolution": f"{info['width']}x{info['height']}",
                          "MB": round(out.stat().st_size / MB, 1),
                          "Mbps": round(out.stat().st_size * 8 / args.sample / MB, 1),
                          "ssim_vs_source": ssim(out, src, size)}
        say(f"   {label}: {results[label]}")
    sampler.stop()

    say()
    total_h = sum(r.duration for r in recordings) / 3600
    base = next(s for s in stages if s["stage"] == "overlay 4k")["x_realtime"]
    say(f"Overlay speed, and time for the whole card ({total_h:.1f} h of footage):")
    for label in VARIANTS:
        x = next(s for s in stages if s["stage"] == f"overlay {label}")["x_realtime"]
        say(f"   {label:14} {x:5.2f}x realtime  ({x / base:.2f}x vs 4K)  ~{total_h / x:.2f} h")
    say(f"Size: 4K {results['4k']['Mbps']} Mbps, 1080p {results['1080p']['Mbps']} Mbps "
        f"-> whole card 4K ~{results['4k']['Mbps'] * total_h * 3600 / 8 / 1e3:.0f} GB, "
        f"1080p ~{results['1080p']['Mbps'] * total_h * 3600 / 8 / 1e3:.0f} GB")
    say(f"Compare: {dest}")
    (logs / "stages.json").write_text(json.dumps({"sample": {"file": str(chapter.path), "offset_s": args.offset,
                                                              "seconds": args.sample},
                                                   "disks": disks, "stages": stages, "results": results}, indent=2))
    summary.close()
    plog.close()
    if not config.output.keep_intermediates:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
