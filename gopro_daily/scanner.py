"""Discovers GoPro chapter files, rebuilds recordings and groups them into output videos (per day by default)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import AppConfig


@dataclass
class Chapter:
    path: Path
    chapter: int
    size: int
    duration: float = 0.0
    # Companion files the camera writes next to each chapter: a low-res H.264 proxy (.LRV) and a small JPEG (.THM).
    # Both are optional (exported clips or other cameras don't have them); previews fall back to the chapter itself.
    lrv: Path | None = None
    thm: Path | None = None

    @property
    def media_id(self) -> str:
        """Stable, opaque id used by the preview endpoints so the browser never sends raw file paths."""
        return hashlib.sha1(str(self.path).encode()).hexdigest()[:16]

    def preview_dict(self) -> dict:
        return {"name": self.path.name, "id": self.media_id, "duration": self.duration, "lowres": self.lrv is not None,
                "thm": self.thm is not None}


@dataclass
class Recording:
    key: str
    directory: Path
    number: int
    letter: str
    chapters: list[Chapter]
    start: datetime | None = None  # naive, local wall-clock time
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: str = ""
    vcodec: str = ""
    pix_fmt: str = ""
    has_gpmd: bool = False
    has_audio: bool = False
    gpmd_index: int | None = None
    error: str = ""
    # Where `start` came from: "filename" wins over ffprobe metadata (used for exported/re-muxed clips whose
    # metadata timestamps are unreliable), "metadata" is the normal GoPro case, "mtime" is the last-resort fallback.
    start_source: str = "metadata"
    # Set once two recordings are found to be the same clip (see `_mark_duplicates`); holds the kept file's name.
    duplicate_of: str | None = None
    # Filled in by `_compute_continuity` once recordings are sorted within their group. None for the first
    # recording of a group (no predecessor to compare against) or when either side has no known start time.
    gap_before: float | None = None
    continuous: bool | None = None

    @property
    def size(self) -> int:
        return sum(c.size for c in self.chapters)

    @property
    def end(self) -> datetime | None:
        return self.start + timedelta(seconds=self.duration) if self.start else None

    @property
    def format_signature(self) -> tuple:
        """Recordings with the same signature can be concatenated without re-encoding."""
        return self.width, self.height, self.fps, self.vcodec, self.pix_fmt, self.has_audio

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "directory": str(self.directory),
            "number": self.number,
            "letter": self.letter,
            "chapters": [c.path.name for c in self.chapters],
            "clips": [c.preview_dict() for c in self.chapters],
            "chapter_count": len(self.chapters),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "start_source": self.start_source,
            "duration": self.duration,
            "size": self.size,
            "resolution": f"{self.width}x{self.height}" if self.width else "",
            "fps": self.fps,
            "vcodec": self.vcodec,
            "has_gpmd": self.has_gpmd,
            "error": self.error,
            "duplicate_of": self.duplicate_of,
            "gap_before": self.gap_before,
            "continuous": self.continuous,
        }


@dataclass
class Group:
    key: str
    label: str
    recordings: list[Recording] = field(default_factory=list)
    # Same-clip files (e.g. a macOS " 2" copy) excluded from `recordings` so they're never processed twice.
    duplicates: list[Recording] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(r.duration for r in self.recordings)

    @property
    def size(self) -> int:
        return sum(r.size for r in self.recordings)

    @property
    def homogeneous(self) -> bool:
        return len({r.format_signature for r in self.recordings}) <= 1

    @property
    def session_count(self) -> int:
        """Number of continuous runs (a new session starts after every real pause)."""
        if not self.recordings:
            return 0
        return 1 + sum(1 for r in self.recordings[1:] if r.continuous is False)

    @property
    def total_pause_seconds(self) -> float:
        return sum(r.gap_before for r in self.recordings[1:] if r.continuous is False and r.gap_before is not None)

    def to_dict(self) -> dict:
        starts = [r.start for r in self.recordings if r.start]
        ends = [r.end for r in self.recordings if r.end]
        return {
            "key": self.key,
            "label": self.label,
            "duration": self.duration,
            "size": self.size,
            "homogeneous": self.homogeneous,
            "recordings": [r.to_dict() for r in self.recordings],
            "duplicates": [r.to_dict() for r in self.duplicates],
            "first_start": min(starts).isoformat() if starts else None,
            "last_end": max(ends).isoformat() if ends else None,
            "session_count": self.session_count,
            "total_pause_seconds": self.total_pause_seconds,
        }


def ffprobe_bin(config: AppConfig) -> str:
    return str(Path(config.encoding.ffmpeg_dir) / "ffprobe") if config.encoding.ffmpeg_dir else "ffprobe"


def probe(path: Path, ffprobe: str = "ffprobe") -> dict:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def find_chapters(config: AppConfig) -> dict[str, list[tuple[Path, re.Match, str]]]:
    """Groups files by recording. Each value is a list of (path, regex match, kind) for that recording's chapters;
    `kind` is "native" (GoPro camera naming, possibly several chapters) or "dated" (exported/re-muxed file named
    after its start time, always a single chapter)."""
    root = Path(config.source.input_dir).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Input folder does not exist: {root}")
    native = re.compile(config.source.file_pattern)
    if "recording" not in native.groupindex or "chapter" not in native.groupindex:
        raise ValueError("file_pattern must define the named groups 'recording' and 'chapter'")
    dated = re.compile(config.source.dated_pattern) if config.source.dated_pattern else None
    if dated and ("date" not in dated.groupindex or "time" not in dated.groupindex):
        raise ValueError("dated_pattern must define the named groups 'date' and 'time'")

    walker = os.walk(root) if config.source.recursive else [(str(root), [], os.listdir(root))]
    found: dict[str, list[tuple[Path, re.Match, str]]] = {}
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            match = native.match(name)
            if match:
                letter = match.groupdict().get("letter") or ""
                key = f"{dirpath}|{letter}|{match.group('recording')}"
                found.setdefault(key, []).append((Path(dirpath) / name, match, "native"))
                continue
            match = dated.match(name) if dated else None
            if match:
                key = f"{dirpath}|dated|{name}"  # one file = one recording, so the name alone is unique enough
                found.setdefault(key, []).append((Path(dirpath) / name, match, "dated"))
    return found


def _fill_metadata(recording: Recording, config: AppConfig) -> None:
    ffprobe = ffprobe_bin(config)
    try:
        infos = [probe(c.path, ffprobe) for c in recording.chapters]
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        recording.error = f"ffprobe failed: {getattr(e, 'stderr', e)}".strip()
        return

    first = infos[0]
    for chapter, info in zip(recording.chapters, infos):
        chapter.duration = float(info.get("format", {}).get("duration", 0) or 0)
    recording.duration = sum(c.duration for c in recording.chapters)
    for stream in first.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not recording.width:
            recording.width = int(stream.get("width", 0))
            recording.height = int(stream.get("height", 0))
            recording.fps = stream.get("r_frame_rate", "")
            recording.vcodec = stream.get("codec_name", "")
            recording.pix_fmt = stream.get("pix_fmt", "")
        elif kind == "audio":
            recording.has_audio = True
        elif kind == "data" and stream.get("codec_tag_string") == "gpmd" and not recording.has_gpmd:
            recording.has_gpmd = True
            recording.gpmd_index = int(stream["index"])

    # Dated (exported/re-muxed) files already got their start from the filename in `scan()`; that reading is more
    # trustworthy than their metadata (no gpmd, re-muxed creation_time), so it's never overwritten here.
    if recording.start_source != "filename":
        recording.start = _start_time(recording, first, config)


def _start_time(recording: Recording, info: dict, config: AppConfig) -> datetime | None:
    g = config.grouping
    tz = ZoneInfo(g.timezone)
    if g.time_source == "metadata":
        raw = info.get("format", {}).get("tags", {}).get("creation_time")
        if raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            recording.start_source = "metadata"
            if g.metadata_is_local_time:
                return parsed.replace(tzinfo=None)
            return parsed.astimezone(tz).replace(tzinfo=None)
    # Fallback / explicit: modification time of the first chapter is roughly the end of that chapter.
    recording.start_source = "mtime"
    first = recording.chapters[0].path
    mtime = datetime.fromtimestamp(first.stat().st_mtime, tz=timezone.utc).astimezone(tz).replace(tzinfo=None)
    chapter_duration = float(info.get("format", {}).get("duration", 0) or 0)
    return mtime - timedelta(seconds=chapter_duration)


def _companion(stem: str, directory_files: dict[str, Path], suffix: str) -> Path | None:
    """GoPro names the proxy after the chapter with the second letter swapped to L (GX010024.MP4 -> GL010024.LRV);
    older cameras keep the same stem (GOPR0024.LRV). Lookup is case-insensitive."""
    candidates = [stem]
    if len(stem) == 8 and stem[0] in "gG" and stem[1] in "hHxXpP":
        candidates.insert(0, stem[0] + "L" + stem[2:])
    for name in candidates:
        found = directory_files.get(f"{name}{suffix}".lower())
        if found:
            return found
    return None


def _attach_companions(recordings: list[Recording]) -> None:
    listings: dict[Path, dict[str, Path]] = {}
    for recording in recordings:
        for chapter in recording.chapters:
            directory = chapter.path.parent
            if directory not in listings:
                listings[directory] = {n.lower(): directory / n for n in os.listdir(directory) if not n.startswith(".")}
            files = listings[directory]
            chapter.lrv = _companion(chapter.path.stem, files, ".lrv")
            chapter.thm = _companion(chapter.path.stem, files, ".thm")


def _parse_dated_start(match: re.Match) -> datetime:
    """`YYYYMMDD_HHMMSS[...]` filenames encode the local wall-clock start time (see module docstring)."""
    return datetime.strptime(match.group("date") + match.group("time"), "%Y%m%d%H%M%S")


def scan(config: AppConfig) -> list[Group]:
    found = find_chapters(config)
    recordings: list[Recording] = []
    for key, items in found.items():
        kind = items[0][2]
        if kind == "native":
            chapters = sorted(
                (Chapter(path=p, chapter=int(m.group("chapter")), size=p.stat().st_size) for p, m, _ in items),
                key=lambda c: c.chapter,
            )
            _, letter, number = key.split("|")
            recordings.append(
                Recording(
                    key=key, directory=chapters[0].path.parent, number=int(number), letter=letter, chapters=chapters
                )
            )
        else:  # dated: an exported/re-muxed clip is always a single file = a single chapter recording
            path, match, _ = items[0]
            chapters = [Chapter(path=path, chapter=1, size=path.stat().st_size)]
            recordings.append(
                Recording(
                    key=key, directory=path.parent, number=0, letter="", chapters=chapters,
                    start=_parse_dated_start(match), start_source="filename",
                )
            )

    _attach_companions(recordings)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda r: _fill_metadata(r, config), recordings))

    recordings = [r for r in recordings if r.error or r.duration >= config.grouping.min_recording_seconds]
    _mark_duplicates(recordings)
    return group_recordings(recordings, config)


def _mark_duplicates(recordings: list[Recording]) -> None:
    """Flags exported duplicates of the same clip (e.g. a macOS ' 2' copy): same start, size and duration.
    The file with the shortest name is kept; the other gets `duplicate_of` set to the kept file's name."""
    for i, a in enumerate(recordings):
        if a.duplicate_of or not a.start:
            continue
        for b in recordings[i + 1:]:
            if b.duplicate_of or not b.start:
                continue
            if a.start == b.start and a.size == b.size and abs(a.duration - b.duration) < 0.1:
                name_a, name_b = a.chapters[0].path.name, b.chapters[0].path.name
                keep, drop = (a, b) if (len(name_a), name_a) <= (len(name_b), name_b) else (b, a)
                drop.duplicate_of = keep.chapters[0].path.name
                if drop is a:
                    break  # a was dropped; move on to the next candidate 'a'


def _compute_continuity(recordings: list[Recording], config: AppConfig) -> None:
    """Sets `gap_before`/`continuous` on each recording of an already start-sorted list. The first recording (no
    predecessor) and any recording next to one with an unknown start are left at None."""
    threshold = config.grouping.continuity_gap_seconds
    prev: Recording | None = None
    for r in recordings:
        if prev is None or not prev.start or not r.start:
            r.gap_before, r.continuous = None, None
        else:
            r.gap_before = (r.start - prev.end).total_seconds()
            r.continuous = r.gap_before <= threshold
        prev = r


def group_key(recording: Recording, config: AppConfig) -> tuple[str, str]:
    g = config.grouping
    if g.group_by == "all":
        return "all", "All recordings"
    if g.group_by == "recording" or recording.start is None:
        label = f"{recording.letter}{recording.number:04d}" if recording.letter else recording.chapters[0].path.stem
        stamp = recording.start.strftime("%Y-%m-%d_%H%M%S") if recording.start else label
        return stamp, stamp
    day = (recording.start - timedelta(hours=g.day_start_hour)).date()
    return day.isoformat(), day.isoformat()


def group_recordings(recordings: list[Recording], config: AppConfig) -> list[Group]:
    groups: dict[str, Group] = {}
    for rec in recordings:
        key, label = group_key(rec, config)
        group = groups.setdefault(key, Group(key=key, label=label))
        (group.duplicates if rec.duplicate_of else group.recordings).append(rec)
    for group in groups.values():
        group.recordings.sort(key=lambda r: (r.start or datetime.min, r.number))
        group.duplicates.sort(key=lambda r: (r.start or datetime.min, r.number))
        _compute_continuity(group.recordings, config)
    return sorted(groups.values(), key=lambda g: g.key)
