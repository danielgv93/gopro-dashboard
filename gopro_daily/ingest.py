"""Copies scanned footage from the SD card to a local folder, so exports read fast storage instead of the card."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from .scanner import Chapter

CHUNK = 8 * 1024 * 1024
ORIGIN_FILE = ".gopro-daily-source"  # inside each copy: the folder it was copied from, to go back when it is deleted
SPACE_MARGIN = 2 * 1024**3  # leave room for the export work files that follow


class Cancelled(Exception):
    pass


def copy_plan(chapters: list[Chapter], source: Path, dest: Path) -> list[tuple[Path, Path]]:
    """Every chapter plus its .LRV/.THM companions, keeping the folder layout so a rescan finds the same files.

    Videos (with their tiny .THM) come first in recording order and the bulky .LRV proxies last,
    so a cancelled copy still holds whole chapters."""
    ordered = sorted(chapters, key=lambda c: c.path)
    files = [f for c in ordered for f in (c.path, c.thm) if f] + [c.lrv for c in ordered if c.lrv]
    return [(f, dest / f.relative_to(source)) for f in dict.fromkeys(files)]


def _copied(src: Path, dst: Path) -> bool:
    return dst.is_file() and dst.stat().st_size == src.stat().st_size


class FootageCopy:
    """One copy at a time, in a background thread. Safe to resume: files already copied are skipped."""

    def __init__(self) -> None:
        self.state: dict = {"status": "idle"}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, chapters: list[Chapter], source: Path, dest: Path, on_done) -> None:
        if self.running:
            raise ValueError("A copy is already running")
        plan = copy_plan(chapters, source, dest)
        missing = sum(s.stat().st_size for s, d in plan if not _copied(s, d))
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ORIGIN_FILE).write_text(str(source))
        free = shutil.disk_usage(dest).free
        if missing + SPACE_MARGIN > free:
            raise ValueError(f"Not enough space: the copy needs {missing / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
        self._cancel.clear()
        self.state = {"status": "running", "source": str(source), "dest": str(dest), "copied": 0,
                      "total": sum(s.stat().st_size for s, _ in plan), "files": len(plan), "files_done": 0,
                      "current": "", "started": time.time(), "error": ""}
        self._thread = threading.Thread(target=self._run, args=(plan, on_done), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, plan: list[tuple[Path, Path]], on_done) -> None:
        st = self.state
        awake = subprocess.Popen(["caffeinate", "-i"]) if shutil.which("caffeinate") else None
        try:
            for src, dst in plan:
                st["current"] = src.name
                if not _copied(src, dst):
                    self._copy_file(src, dst)
                else:
                    st["copied"] += dst.stat().st_size
                st["files_done"] += 1
            on_done(Path(st["source"]), Path(st["dest"]))
            st["status"] = "done"
        except Cancelled:
            st["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001 - surface anything (card removed, disk full) to the UI
            st["status"], st["error"] = "failed", f"{type(e).__name__}: {e}"
        finally:
            st["current"], st["finished"] = "", time.time()
            if awake:
                awake.terminate()

    def _copy_file(self, src: Path, dst: Path) -> None:
        # A half-copied file never carries the real name, so an interrupted copy is never mistaken for a finished one.
        tmp = dst.with_name(dst.name + ".partial")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with src.open("rb") as fin, tmp.open("wb") as fout:
                while chunk := fin.read(CHUNK):
                    if self._cancel.is_set():
                        raise Cancelled
                    fout.write(chunk)
                    self.state["copied"] += len(chunk)
            shutil.copystat(src, tmp)  # keeps mtime, which the scanner can use as recording time
            tmp.replace(dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def copy_origin(copy: Path) -> Path | None:
    try:
        return Path((copy / ORIGIN_FILE).read_text().strip())
    except OSError:
        return None
