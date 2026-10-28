"""Benchmark: SD card -> external disk, to find what limits an export.

Runs on the real hardware with the real pipeline code, over a sample of one chapter:
  1. raw SD read             (uncached, MB/s)
  2. raw external disk write (uncached + fsync, MB/s)
  3. raw SD -> external copy (what "Copy to Mac" style ingest does, MB/s)
  4. process_group on a SAMPLE-second piece: cut (reads SD) -> overlay (encode) -> concat (writes external disk)
While it runs, `iostat` samples MB/s per disk and CPU every second. Every stage gets its own averages, and the
whole card is projected from them, so the slowest stage is the bottleneck.

Logs go to logs/bench-<timestamp>/: summary.txt, stages.json, iostat.log, pipeline.log.

    .venv/bin/python scripts/bench_pipeline.py [--sample 120] [--io-gb 1] [--dest /Volumes/TOSHIBA]
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gopro_daily import pipeline  # noqa: E402
from gopro_daily.config import PROJECT_ROOT, load_config  # noqa: E402
from gopro_daily.edits import Piece  # noqa: E402
from gopro_daily.ingest import CHUNK  # noqa: E402
from gopro_daily.scanner import Group, scan  # noqa: E402

MB = 1e6


# --------------------------------------------------------------------------- system sampler


def physical_disk(path: Path) -> str:
    """iostat names physical disks (disk0); APFS volumes live on a synthesized one, so follow it to its store."""
    dev = subprocess.run(["df", str(path)], capture_output=True, text=True).stdout.splitlines()[-1].split()[0]
    info = subprocess.run(["diskutil", "info", dev], capture_output=True, text=True).stdout
    if m := re.search(r"APFS Physical Store:\s+(disk\d+)", info):
        return m.group(1)
    return re.match(r"/dev/(disk\d+)", dev).group(1)


class Sampler:
    """Background `iostat -w 1`, timestamped. Samples are (time, {disk: MB/s}, cpu busy %)."""

    def __init__(self, disks: dict[str, str], log: Path) -> None:
        self.names = {d: role for role, d in disks.items()}  # disk -> role, same disk may serve two roles
        self.order = list(dict.fromkeys(disks.values()))
        self.samples: list[tuple[float, dict[str, float], float]] = []
        self.log = log.open("w")
        self.proc = subprocess.Popen(["iostat", "-d", "-C", "-w", "1", *self.order], stdout=subprocess.PIPE, text=True)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout
        for n, line in enumerate(self.proc.stdout):
            now = time.time()
            self.log.write(f"{datetime.fromtimestamp(now):%H:%M:%S} {line}")
            self.log.flush()
            cols = line.split()
            if n < 3 or not cols or not cols[0][0].isdigit():  # headers + the first since-boot average line
                continue
            values = [float(c) for c in cols]
            mbps = {d: values[i * 3 + 2] for i, d in enumerate(self.order)}
            us, sy, _ = values[len(self.order) * 3:]
            self.samples.append((now, mbps, us + sy))

    def stats(self, start: float, end: float) -> dict:
        window = [s for s in self.samples if start <= s[0] <= end + 0.5]
        if not window:
            return {}
        out = {f"{self.names.get(d, d)} ({d}) MB/s": round(sum(s[1][d] for s in window) / len(window), 1)
               for d in self.order}
        out["cpu busy % avg"] = round(sum(s[2] for s in window) / len(window), 1)
        out["cpu busy % max"] = round(max(s[2] for s in window), 1)
        return out

    def stop(self) -> None:
        self.proc.terminate()
        self.log.close()


# --------------------------------------------------------------------------- raw I/O


def _nocache(f) -> None:
    fcntl.fcntl(f.fileno(), fcntl.F_NOCACHE, 1)  # bypass the page cache so we measure the device, not RAM


def raw_read(src: Path, offset: int, size: int) -> int:
    with src.open("rb", buffering=0) as f:
        _nocache(f)
        f.seek(offset)
        done = 0
        while done < size and (chunk := f.read(min(CHUNK, size - done))):
            done += len(chunk)
    return done


def raw_write(dest: Path, size: int) -> int:
    block = os.urandom(CHUNK)
    with dest.open("wb", buffering=0) as f:
        _nocache(f)
        for _ in range(size // CHUNK):
            f.write(block)
        os.fsync(f.fileno())
    return size // CHUNK * CHUNK


def raw_copy(src: Path, offset: int, size: int, dest: Path) -> int:
    with src.open("rb", buffering=0) as fin, dest.open("wb", buffering=0) as fout:
        _nocache(fin), _nocache(fout)
        fin.seek(offset)
        done = 0
        while done < size and (chunk := fin.read(min(CHUNK, size - done))):
            fout.write(chunk)
            done += len(chunk)
        os.fsync(fout.fileno())
    return done


# --------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=float, default=120, help="seconds of footage to export (default 120)")
    ap.add_argument("--io-gb", type=float, default=1, help="GB for each raw I/O test (default 1)")
    ap.add_argument("--dest", type=Path, help="external disk folder (default: output.output_dir from config.json)")
    ap.add_argument("--work-dir", help="override output.work_dir (default: the app's, on the internal SSD)")
    args = ap.parse_args()
    io_bytes = int(args.io_gb * 1e9)

    config = load_config()
    source = Path(config.source.input_dir)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    logs = PROJECT_ROOT / "logs" / f"bench-{stamp}"
    logs.mkdir(parents=True)
    dest = (args.dest or Path(config.output.output_dir)) / f"gopro-daily-bench-{stamp}"
    dest.mkdir(parents=True)
    config.output.output_dir, config.output.overwrite = str(dest), True
    if args.work_dir:
        config.output.work_dir = args.work_dir
    work = pipeline.work_root(config)
    work.mkdir(parents=True, exist_ok=True)

    summary = (logs / "summary.txt").open("w")

    def say(msg: str = "") -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        summary.write(line + "\n")
        summary.flush()

    say(f"Logs: {logs}")
    say(f"Source {source}  ->  dest {dest}  (work dir {work})")
    disks = {"sd": physical_disk(source), "external": physical_disk(dest), "work": physical_disk(work)}
    say(f"Disks: {disks}")

    say("Scanning the card...")
    recordings = [r for g in scan(config) for r in g.recordings if not r.error and r.duration > 0]
    total_seconds = sum(r.duration for r in recordings)
    total_bytes = sum(r.size for r in recordings)
    rec = max(recordings, key=lambda r: r.duration)
    # A middle chapter at an offset: least likely to sit in the page cache from earlier previews/benchmarks.
    chapter = rec.chapters[len(rec.chapters) // 2]
    bitrate = chapter.size / chapter.duration
    offset_s = min(600.0, max(0.0, chapter.duration - args.sample - 5))
    say(f"Card: {len(recordings)} recording(s), {total_seconds / 3600:.2f} h, {total_bytes / 1e9:.1f} GB, "
        f"avg {total_bytes / total_seconds / MB:.1f} MB/s of footage")
    say(f"Sample: {chapter.path.name} ({rec.width}x{rec.height} {rec.fps} {rec.vcodec}, {bitrate / MB:.1f} MB/s), "
        f"{args.sample:.0f} s from {offset_s:.0f} s")

    sampler = Sampler(disks, logs / "iostat.log")
    time.sleep(3)  # let iostat print its headers and since-boot line
    stages: list[dict] = []

    def stage(name: str, fn, footage_seconds: float | None = None) -> None:
        say(f"-> {name}")
        t0 = time.time()
        nbytes = fn()
        t1 = time.time()
        wall = t1 - t0
        row = {"stage": name, "start": t0, "end": t1, "wall_s": round(wall, 1)}
        if nbytes:
            row["bytes"] = nbytes
            row["MB/s"] = round(nbytes / wall / MB, 1)
            footage_seconds = footage_seconds or nbytes / bitrate
        if footage_seconds:
            row["x_realtime"] = round(footage_seconds / wall, 2)  # seconds of footage handled per wall second
        row.update(sampler.stats(t0, t1))
        stages.append(row)
        say("   " + ", ".join(f"{k}={v}" for k, v in row.items() if k not in ("stage", "start", "end")))

    # 1-3. raw I/O, each on its own region of the chapter so none reads cached data
    region = lambda i: int(chapter.size * (0.15 + 0.2 * i))  # noqa: E731
    stage("raw SD read", lambda: raw_read(chapter.path, region(0), io_bytes))
    stage("raw external write", lambda: raw_write(dest / "write-test.bin", io_bytes))
    (dest / "write-test.bin").unlink()
    stage("raw SD -> external copy", lambda: raw_copy(chapter.path, region(1), io_bytes, dest / "copy-test.bin"))
    (dest / "copy-test.bin").unlink()

    # 4. the real export on a one-chapter recording, timing each ffmpeg / dashboard process it starts
    one = dataclasses.replace(rec, key=f"{rec.key}-bench", chapters=[chapter], duration=chapter.duration)
    piece = Piece(one, offset_s, offset_s + args.sample)
    plog = (logs / "pipeline.log").open("w")
    ctx = pipeline.Context(log=lambda m: (plog.write(f"{datetime.now():%H:%M:%S} {m}\n"), plog.flush()),
                           progress=lambda *a: None, cancel_event=threading.Event(), processes=set(),
                           lock=threading.Lock())
    real_run, steps = pipeline.run, []

    def timed_run(cmd, ctx, on_line=None, env=None):
        name = ("overlay (decode+render+encode)" if "gopro_daily.dashboard" in cmd
                else "cut (read SD -> work dir)" if "-ss" in cmd
                else "concat (work dir -> external)" if "concat" in cmd else Path(cmd[0]).name)
        t0 = time.time()
        real_run(cmd, ctx, on_line, env)
        steps.append((name, t0, time.time(), Path(cmd[-1])))

    pipeline.run = timed_run
    say("-> export (process_group)")
    t0 = time.time()
    try:
        out = pipeline.process_group(Group(key="bench", label="bench", recordings=[one]), config, ctx, [piece])
    finally:
        pipeline.run = real_run
        plog.close()
    t1 = time.time()
    for name, s0, s1, path in steps:
        size = path.stat().st_size if path.exists() and path.suffix == ".mp4" else 0
        row = {"stage": name, "start": s0, "end": s1, "wall_s": round(s1 - s0, 1),
               "x_realtime": round(args.sample / (s1 - s0), 2)}
        if size:
            row["out_bytes"], row["out MB/s"] = size, round(size / (s1 - s0) / MB, 1)
        row.update(sampler.stats(s0, s1))
        stages.append(row)
        say("   " + ", ".join(f"{k}={v}" for k, v in row.items() if k not in ("stage", "start", "end")) + f"  [{name}]")
    other = (t1 - t0) - sum(s1 - s0 for _, s0, s1, _ in steps)
    stages.append({"stage": "export other (probe, GPS track, layout)", "wall_s": round(other, 1),
                   "x_realtime": round(args.sample / max(other, 0.01), 2)})
    stages.append({"stage": "export total", "start": t0, "end": t1, "wall_s": round(t1 - t0, 1),
                   "x_realtime": round(args.sample / (t1 - t0), 2), **sampler.stats(t0, t1)})
    say(f"   export total {t1 - t0:.1f} s -> {out} ({out.stat().st_size / 1e6:.0f} MB), "
        f"other {other:.1f} s")
    sampler.stop()

    # projection for the whole card: footage hours / x_realtime of each stage
    say()
    say(f"Projection for the whole card ({total_seconds / 3600:.2f} h of footage, {total_bytes / 1e9:.0f} GB):")
    projected = [(r["stage"], total_seconds / r["x_realtime"]) for r in stages
                 if r.get("x_realtime") and r["stage"] != "export total"]
    for name, secs in sorted(projected, key=lambda p: -p[1]):
        say(f"   {secs / 3600:6.2f} h  {name}")
    export = [p for p in projected if not p[0].startswith("raw")]
    worst = max(export, key=lambda p: p[1])
    share = worst[1] / sum(p[1] for p in export) * 100
    say(f"   export total ~{total_seconds / stages[-1]['x_realtime'] / 3600:.2f} h "
        f"(stages run one after another per recording)")
    say(f"BOTTLENECK: {worst[0]} — {share:.0f}% of export time")
    copy = next(r for r in stages if r["stage"] == "raw SD -> external copy")
    say(f"Plain copy of the card to the external disk: ~{total_bytes / (copy['MB/s'] * MB) / 3600:.2f} h "
        f"at {copy['MB/s']} MB/s")

    (logs / "stages.json").write_text(json.dumps({"card": {"recordings": len(recordings), "seconds": total_seconds,
                                                            "bytes": total_bytes},
                                                   "sample": {"file": str(chapter.path), "offset_s": offset_s,
                                                              "seconds": args.sample, "bitrate": bitrate},
                                                   "disks": disks, "stages": stages}, indent=2))
    summary.close()


if __name__ == "__main__":
    main()
