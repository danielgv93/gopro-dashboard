"""Background job queue: one output group per job, processed one at a time (recordings inside run in parallel)."""

from __future__ import annotations

import itertools
import queue
import subprocess
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig
from .edits import Piece
from .pipeline import Cancelled, Context, StepFailed, process_group
from .scanner import Group

_ids = itertools.count(1)


@dataclass
class Job:
    group: Group
    config: AppConfig
    pieces: list[Piece]
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"  # queued | running | done | failed | cancelled
    output: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    tasks: dict[str, dict] = field(default_factory=dict)
    logs: deque = field(default_factory=lambda: deque(maxlen=5000))
    log_offset: int = 0  # number of lines dropped from the front of `logs`
    history: deque = field(default_factory=lambda: deque(maxlen=720))  # (elapsed s, footage s done), every 5 s
    ctx: Context | None = None

    def __post_init__(self) -> None:
        overlay = self.config.overlay.enabled
        for piece in self.pieces:
            rec = piece.rec
            label = f"{rec.letter}{rec.number:04d}"
            self.tasks[piece.id] = {
                "label": label if piece.full else f"{label} · {_clock(piece.start)}–{_clock(piece.end)}",
                "rec": rec.key,
                "stage": "pending",
                "progress": 0.0,
                "weight": piece.duration or 1.0,
                "seconds": piece.duration,
                "stages": ["overlay"] if overlay else ["join"],  # overlay renders straight from the chapters, no join
            }
        self.tasks["group"] = {"label": "Final video", "stage": "pending", "progress": 0.0, "weight": 0,
                               "seconds": sum(p.duration for p in self.pieces), "stages": ["concat"]}
        for t in self.tasks.values():
            t.update(speed=0.0, started=None, finished=None, mark=None)

    def log(self, line: str) -> None:
        if len(self.logs) == self.logs.maxlen:
            self.log_offset += 1
        self.logs.append(f"{time.strftime('%H:%M:%S')} {line}")

    def set_progress(self, task: str, stage: str, fraction: float) -> None:
        """`task` is a piece id, or a recording key for steps shared by all the pieces of that recording (join)."""
        targets = [self.tasks[task]] if task in self.tasks else [t for t in self.tasks.values() if t.get("rec") == task]
        now = time.time()
        for t in targets:
            done = fraction * t["seconds"]
            if t["stage"] != stage:  # speed is per stage: joining is a stream copy, overlay is a full encode
                t["speed"], t["mark"] = 0.0, None
            if t["mark"] is None:
                t["mark"] = (now, done)
            elif now - t["mark"][0] >= 1:
                rate = (done - t["mark"][1]) / (now - t["mark"][0])
                t["speed"] = rate if not t["speed"] else 0.7 * t["speed"] + 0.3 * rate
                t["mark"] = (now, done)
            t["started"] = t["started"] or now
            if stage == t["stages"][-1] and fraction >= 1:
                t["finished"] = t["finished"] or now
            t["stage"], t["progress"] = stage, fraction
        if self.started and (not self.history or now - self.started - self.history[-1][0] >= 5):
            self.history.append((round(now - self.started, 1), round(self.overall * self.tasks["group"]["seconds"], 1)))

    @property
    def overall(self) -> float:
        if self.status == "done":
            return 1.0
        recs = [t for k, t in self.tasks.items() if k != "group"]
        total = sum(t["weight"] for t in recs) or 1
        done = 0.0
        for t in recs:
            done += (t["progress"] if t["stage"] in t["stages"] else 0.0) * t["weight"]
        rec_part = done / total
        concat = self.tasks["group"]["progress"] if self.tasks["group"]["stage"] == "concat" else 0
        return min(1.0, rec_part * 0.95 + concat * 0.05)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "group": self.group.label,
            "recordings": len({p.rec.key for p in self.pieces}),
            "duration": sum(p.duration for p in self.pieces),
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "progress": self.overall,
            "parallel": self.config.encoding.parallel_jobs,
            "overlay": self.config.overlay.enabled,
            "history": list(self.history),
            "tasks": [{"id": k, **{x: v[x] for x in ("label", "stage", "progress", "seconds", "speed", "stages",
                                                     "started", "finished")}} for k, v in self.tasks.items()],
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[int, Job] = {}
        self.queue: queue.Queue[Job] = queue.Queue()
        self.current: Job | None = None
        self._caffeinate: subprocess.Popen | None = None
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, group: Group, config: AppConfig, pieces: list[Piece]) -> Job:
        job = Job(group=group, config=config.model_copy(deep=True), pieces=pieces)
        self.jobs[job.id] = job
        self.queue.put(job)
        return job

    def cancel(self, job_id: int) -> None:
        job = self.jobs[job_id]
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
        elif job.status == "running" and job.ctx:
            job.log("Cancelling…")
            job.ctx.cancel()

    def clear_finished(self) -> None:
        for job_id in [j.id for j in self.jobs.values() if j.status in ("done", "failed", "cancelled")]:
            del self.jobs[job_id]

    def _worker(self) -> None:
        while True:
            job = self.queue.get()
            if job.status == "cancelled":
                continue
            self._keep_awake(True)
            self.current = job
            job.status, job.started = "running", time.time()
            job.ctx = Context(log=job.log, progress=job.set_progress, cancel_event=threading.Event(),
                              processes=set(), lock=threading.Lock())
            try:
                job.output = str(process_group(job.group, job.config, job.ctx, job.pieces))
                job.status = "done"
                job.log(f"Done: {job.output}")
                _notify("GoPro Daily", f"{job.group.label} is ready")
            except Cancelled:
                job.status = "cancelled"
                job.log("Cancelled")
            except StepFailed as e:
                job.status, job.error = "failed", str(e)
                job.log(f"FAILED: {e}")
                _notify("GoPro Daily", f"{job.group.label} failed")
            except Exception as e:  # noqa: BLE001 - surface anything to the UI
                job.status, job.error = "failed", f"{type(e).__name__}: {e}"
                job.log(traceback.format_exc())
            finally:
                job.finished = time.time()
                self.current = None
                if self.queue.empty():
                    self._keep_awake(False)

    def _keep_awake(self, on: bool) -> None:
        """macOS: prevent idle sleep while rendering (long overlays can take hours)."""
        if on and (self._caffeinate is None or self._caffeinate.poll() is not None):
            try:
                self._caffeinate = subprocess.Popen(["caffeinate", "-i"])
            except FileNotFoundError:
                self._caffeinate = None
        elif not on and self._caffeinate is not None:
            self._caffeinate.terminate()
            self._caffeinate = None


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m}:{s:02d}"


def _notify(title: str, message: str) -> None:
    script = f"display notification {_as_str(message)} with title {_as_str(title)}"
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def _as_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def reveal_in_finder(path: str) -> None:
    subprocess.Popen(["open", "-R", str(Path(path))])
