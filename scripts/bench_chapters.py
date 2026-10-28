"""Benchmark: overlay export of a multi-chapter recording, today's join path vs per-chapter units.

Takes the last N chapters of the longest recording on the card as one recording and exports it twice with the real
pipeline code and the same encoder settings:
  new    process_group: one overlay render per chapter, in parallel, straight from the source
  join   the previous path: join chapters -> GPS from the joined file -> one overlay render -> concat
The new path runs first, so if the page cache keeps the chapters, the join path is the one that benefits.
Each output is checked with `ffmpeg -v error` and AVFoundation (scripts/avcheck.swift).

Logs go to logs/bench-chapters-<timestamp>/: summary.txt, iostat.log, <mode>.log.

    .venv/bin/python scripts/bench_chapters.py [--chapters 2] [--dest /Volumes/TOSHIBA] [--only new|join]
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_pipeline import Sampler, physical_disk  # noqa: E402

from gopro_daily import pipeline  # noqa: E402
from gopro_daily.config import PROJECT_ROOT, load_config  # noqa: E402
from gopro_daily.layout import generate_layout  # noqa: E402
from gopro_daily.scanner import Group, scan  # noqa: E402
from gopro_daily.track import extract_points, write_track  # noqa: E402


def join_export(group: Group, config, ctx, work: Path) -> Path:
    """The overlay path before per-chapter units, for one recording."""
    rec = group.recordings[0]
    work.mkdir(parents=True, exist_ok=True)
    config_dir = work / "config"
    pipeline.write_ffmpeg_profile(config, config_dir)
    joined = pipeline.join_chapters(rec, work / "joined.mp4", config, ctx, rec.key)
    track = None
    if config.overlay.layout_mode == "generated" and config.overlay.journey_map.enabled:
        track = work / "track.json"
        write_track([extract_points(joined, config)], track)
    layout = work / "layout.xml"
    layout.write_text(generate_layout(config.overlay, rec.width, rec.height, str(track) if track else None))
    rendered = pipeline.render_overlay(joined, rec, work / "overlay.mp4", layout, config, ctx, rec.key, config_dir)
    dest = pipeline.output_path(group, config)
    pipeline.concat_products([rendered], dest, config, ctx, rec.duration, keep_telemetry=False)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chapters", type=int, default=2, help="last N chapters of the longest recording (default 2)")
    ap.add_argument("--dest", type=Path, help="external disk folder (default: output.output_dir from config.json)")
    ap.add_argument("--only", choices=["new", "join"], help="run one path only")
    args = ap.parse_args()

    config = load_config()
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    logs = PROJECT_ROOT / "logs" / f"bench-chapters-{stamp}"
    logs.mkdir(parents=True)
    summary = (logs / "summary.txt").open("w")

    def say(msg: str = "") -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        summary.write(line + "\n")
        summary.flush()

    rec = max((r for g in scan(config) for r in g.recordings if not r.error), key=lambda r: r.duration)
    chapters = rec.chapters[-args.chapters:]
    bench = dataclasses.replace(rec, key=f"{rec.key}-bench", chapters=chapters,
                                duration=sum(c.duration for c in chapters))
    footage = bench.duration
    dest_root = (args.dest or Path(config.output.output_dir)) / f"gopro-daily-bench-{stamp}"
    work = pipeline.work_root(config)
    disks = {"sd": physical_disk(Path(config.source.input_dir)), "external": physical_disk(dest_root.parent),
             "work": physical_disk(work)}
    say(f"Logs: {logs}")
    say(f"Recording: {', '.join(c.path.name for c in chapters)}  {footage / 60:.1f} min, "
        f"{sum(c.size for c in chapters) / 1e9:.1f} GB, {rec.width}x{rec.height}, parallel_jobs="
        f"{config.encoding.parallel_jobs}, disks {disks}")
    sampler = Sampler(disks, logs / "iostat.log")
    time.sleep(3)

    results = {}
    for mode in [m for m in ("new", "join") if args.only in (None, m)]:
        out_dir = dest_root / mode
        out_dir.mkdir(parents=True)
        cfg = config.model_copy(deep=True)
        cfg.output.output_dir, cfg.output.overwrite = str(out_dir), True
        group = Group(key=f"bench-{mode}", label=f"bench-{mode}", recordings=[bench])
        log = (logs / f"{mode}.log").open("w")
        ctx = pipeline.Context(log=lambda m: (log.write(f"{datetime.now():%H:%M:%S} {m}\n"), log.flush()),
                               progress=lambda *a: None, cancel_event=threading.Event(), processes=set(),
                               lock=threading.Lock())
        say(f"-> {mode}")
        t0 = time.time()
        if mode == "new":
            out = pipeline.process_group(group, cfg, ctx)
        else:
            job_work = work / "exports" / f"bench-join-{stamp}"
            try:
                out = join_export(group, cfg, ctx, job_work)
            finally:
                subprocess.run(["rm", "-rf", str(job_work)])
        wall = time.time() - t0
        log.close()
        stats = sampler.stats(t0, t0 + wall)
        errors = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
                                capture_output=True, text=True).stderr.strip().splitlines()
        av = subprocess.run(["swift", str(Path(__file__).with_name("avcheck.swift")), str(out)],
                            capture_output=True, text=True).stdout.strip().splitlines()
        results[mode] = wall
        say(f"   {mode}: {wall / 60:.1f} min, {footage / wall:.2f}x realtime, ffmpeg errors={len(errors)}, "
            f"{av[-1] if av else 'avcheck failed'}, {out.stat().st_size / 1e9:.2f} GB")
        say(f"   {stats}")
    sampler.stop()
    if len(results) == 2:
        say(f"new vs join: {results['new'] / 60:.1f} vs {results['join'] / 60:.1f} min "
            f"({(results['new'] / results['join'] - 1) * 100:+.0f} %)")


if __name__ == "__main__":
    main()
