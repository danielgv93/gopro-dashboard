"""Copies the footage each export needs from the SD card to this Mac, so exports read fast storage instead of the card."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

CHUNK = 8 * 1024 * 1024
ORIGIN_FILE = ".gopro-daily-source"  # inside older whole-card copies: the folder they were copied from
SPACE_MARGIN = 2 * 1024**3  # spare room on top of the estimates


class Cancelled(Exception):
    pass


class Dropped(Exception):
    """The film's export was cancelled while its files were copying."""


def _copied(src: Path, dst: Path) -> bool:
    return dst.is_file() and dst.stat().st_size == src.stat().st_size


class FootageCopy:
    """One copy at a time, in a background thread, one film's files after another."""

    def __init__(self) -> None:
        self.state: dict = {"status": "idle"}
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_films(self, batches: list[list[tuple[Path, Path]]], source: Path, dest: Path, *, before, done,
                    dropped, progress, ended) -> None:
        """Copies the (src, dst) files of each film in turn. before(i) may block until there is room and returns False
        to skip film i; dropped(i) stops a film mid-copy; progress(i, bytes); done(i) once its files are all here;
        ended(status, error) when the copy stops, however it stops."""
        if self.running:
            raise ValueError("A copy is already running")
        self._cancel.clear()
        files = [f for plan in batches for f in plan]
        self.state = {"status": "running", "source": str(source), "dest": str(dest), "copied": 0,
                      "total": sum(s.stat().st_size for s, _ in files), "films": len(batches), "film": 0, "error": ""}
        self._hooks = dict(before=before, done=done, dropped=dropped, progress=progress, ended=ended)
        self._thread = threading.Thread(target=self._run, args=(batches,), daemon=True)
        self._thread.start()

    def wait(self, ready) -> None:
        """Blocks until ready() is true; a cancelled copy stops waiting."""
        while not ready():
            if self._cancel.wait(1):
                raise Cancelled

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, batches) -> None:
        st, h, pending = self.state, self._hooks, []
        awake = subprocess.Popen(["caffeinate", "-i"]) if shutil.which("caffeinate") else None
        try:
            for i, plan in enumerate(batches):
                pending = plan  # copied but not yet handed to its export, which deletes them when it ends
                if not h["before"](i):
                    continue
                st["film"] = i + 1
                try:
                    for src, dst in plan:
                        if not _copied(src, dst):
                            self._copy_file(src, dst, i)
                        else:
                            h["progress"](i, dst.stat().st_size)
                except Dropped:
                    for _, dst in plan:
                        dst.unlink(missing_ok=True)
                    continue
                h["done"](i)
            pending = []
            st["status"] = "done"
        except Cancelled:
            st["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001 - surface anything (card removed, disk full) to the UI
            st["status"], st["error"] = "failed", f"{type(e).__name__}: {e}"
        finally:
            st["finished"] = time.time()
            for _, dst in pending:
                dst.unlink(missing_ok=True)
            h["ended"](st["status"], st["error"])
            if awake:
                awake.terminate()

    def _copy_file(self, src: Path, dst: Path, film: int) -> None:
        # A half-copied file never carries the real name, so an interrupted copy is never mistaken for a finished one.
        tmp = dst.with_name(dst.name + ".partial")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with src.open("rb") as fin, tmp.open("wb") as fout:
                while chunk := fin.read(CHUNK):
                    if self._cancel.is_set():
                        raise Cancelled
                    if self._hooks["dropped"](film):
                        raise Dropped
                    fout.write(chunk)
                    self.state["copied"] += len(chunk)
                    self._hooks["progress"](film, len(chunk))
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
