"""Film edits made in the footage editor: which parts of each recording end up in the output video.

An edit is an edit decision list (EDL): a list of cuts, each one a kept range of a recording measured in seconds from
the start of that recording (all its chapters joined). Everything outside the cuts is left out of the film.

    stored edit (FilmEdit)  --resolve_cuts-->  cuts shown in the editor  --plan_pieces-->  Pieces the pipeline renders
                                                                            --plan_units-->   Units (overlay export)

Edits are keyed by group key and stored next to config.json, so they survive restarts and rescans. They are resolved
against the current scan every time: cuts of recordings that no longer exist are dropped, and recordings that appeared
after the edit was made are kept whole (they were never reviewed, so nothing of them was removed).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .config import PROJECT_ROOT
from .scanner import Chapter, Group, Recording

EDITS_PATH = PROJECT_ROOT / "edits.json"
MIN_CUT_SECONDS = 0.2  # shorter cuts are dropped: a lossless cut cannot be that short anyway
MERGE_GAP_SECONDS = 0.05  # cuts closer than this are one continuous range (e.g. a split nobody removed)
# gopro-dashboard computes speed over a 3 s GPS window and crashes on shorter inputs; measured: 4 s fails, 5 s works.
OVERLAY_MIN_SECONDS = 5.0


class Cut(BaseModel):
    rec: str  # Recording.key
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Cut:
        if self.end <= self.start:
            raise ValueError("a cut must end after it starts")
        return self


class FilmEdit(BaseModel):
    # Recording keys the film had when it was edited. Recordings not listed here are new and are kept whole.
    recordings: list[str] = []
    cuts: list[Cut] = []


@dataclass(frozen=True, eq=False)
class Piece:
    """One continuous range of one recording that goes into the output video, in film order."""

    rec: Recording
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def full(self) -> bool:
        """True when the piece is the whole recording, so the pipeline can skip cutting it."""
        return self.start <= MERGE_GAP_SECONDS and self.end >= self.rec.duration - MERGE_GAP_SECONDS

    @property
    def id(self) -> str:
        return self.rec.key if self.full else f"{self.rec.key}@{self.start:.2f}"


@dataclass(frozen=True, eq=False)
class Span:
    """A range of one chapter file, in seconds inside that file."""

    chapter: Chapter
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def full(self) -> bool:
        return self.start <= MERGE_GAP_SECONDS and self.end >= self.chapter.duration - MERGE_GAP_SECONDS


@dataclass(frozen=True, eq=False)
class Unit:
    """What one overlay render works on: usually one chapter range. Units of a piece are contiguous, in film order.

    A unit has several spans only when a range shorter than OVERLAY_MIN_SECONDS was merged into its neighbour."""

    piece: Piece
    spans: tuple[Span, ...]

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.spans)

    @property
    def full(self) -> bool:
        """True when the unit is a whole chapter, so the pipeline renders the chapter file as is."""
        return len(self.spans) == 1 and self.spans[0].full


# ----------------------------------------------------------------------------- resolving


def _valid_recordings(group: Group) -> list[Recording]:
    return [r for r in group.recordings if not r.error and r.duration > 0]


def default_cuts(group: Group) -> list[Cut]:
    return [Cut(rec=r.key, start=0, end=r.duration) for r in _valid_recordings(group)]


def resolve_cuts(group: Group, edit: FilmEdit | None) -> list[Cut]:
    """The edit applied to the current scan, in film order. Splits are kept (touching cuts are not merged) so the
    editor shows them again; clamping and dropping only removes what cannot be rendered."""
    if edit is None:
        return default_cuts(group)
    known = set(edit.recordings)
    by_rec: dict[str, list[Cut]] = {}
    for cut in edit.cuts:
        by_rec.setdefault(cut.rec, []).append(cut)
    cuts: list[Cut] = []
    for rec in _valid_recordings(group):
        if rec.key not in known and rec.key not in by_rec:
            cuts.append(Cut(rec=rec.key, start=0, end=rec.duration))
            continue
        for cut in sorted(by_rec.get(rec.key, []), key=lambda c: c.start):
            start, end = max(0.0, cut.start), min(rec.duration, cut.end)
            if cuts and cuts[-1].rec == rec.key:
                start = max(start, cuts[-1].end)  # overlapping cuts would duplicate footage
            if end - start >= MIN_CUT_SECONDS:
                cuts.append(Cut(rec=rec.key, start=start, end=end))
    return cuts


def make_edit(group: Group, cuts: list[Cut]) -> FilmEdit:
    """Stores the editor's cuts together with the recordings they were decided against."""
    return FilmEdit(recordings=[r.key for r in _valid_recordings(group)], cuts=cuts)


def plan_pieces(group: Group, edit: FilmEdit | None) -> list[Piece]:
    """What the pipeline renders: resolved cuts with adjacent ranges of the same recording merged into one piece.

    Pieces follow the recordings' chronological order, which the overlay's GPS clock and journey map rely on."""
    recordings = {r.key: r for r in group.recordings}
    pieces: list[Piece] = []
    for cut in resolve_cuts(group, edit):
        rec = recordings[cut.rec]
        last = pieces[-1] if pieces else None
        if last and last.rec is rec and cut.start - last.end <= MERGE_GAP_SECONDS:
            pieces[-1] = Piece(rec, last.start, cut.end)
        else:
            pieces.append(Piece(rec, cut.start, cut.end))
    return pieces


def plan_units(pieces: list[Piece]) -> list[Unit]:
    """Splits every piece at its recording's chapter boundaries, so each chapter renders on its own (in parallel,
    straight from the source). Ranges shorter than OVERLAY_MIN_SECONDS are merged into a neighbour of the same piece,
    because the overlay renderer crashes on them."""
    units: list[Unit] = []
    for piece in pieces:
        groups: list[list[Span]] = []
        offset = 0.0
        for chapter in piece.rec.chapters:
            start, end = max(piece.start, offset), min(piece.end, offset + chapter.duration)
            if end - start > MERGE_GAP_SECONDS:
                groups.append([Span(chapter, start - offset, end - offset)])
            offset += chapter.duration
        while len(groups) > 1:
            short = next((i for i, g in enumerate(groups) if sum(s.duration for s in g) < OVERLAY_MIN_SECONDS), None)
            if short is None:
                break
            lo = short - 1 if short > 0 else 0  # merge into the previous unit, or into the next one for the first
            groups[lo:lo + 2] = [groups[lo] + groups[lo + 1]]
        units += [Unit(piece, tuple(g)) for g in groups]
    return units


def edit_summary(group: Group, edit: FilmEdit | None) -> dict:
    """What the UI needs to show a film's edit state."""
    cuts = resolve_cuts(group, edit)
    return {
        "cuts": [c.model_dump() for c in cuts],
        "edited": edit is not None,
        "kept_duration": sum(c.end - c.start for c in cuts),
    }


# ----------------------------------------------------------------------------- storage


class EditStore:
    """Film edits by group key, persisted as JSON. Thread-safe: API handlers run in a thread pool."""

    def __init__(self, path: Path = EDITS_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._edits: dict[str, FilmEdit] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                self._edits = {k: FilmEdit.model_validate(v) for k, v in raw.items()}
            except (ValueError, TypeError):
                self._edits = {}  # a corrupt file must not stop the app; the edits are simply lost

    def get(self, key: str) -> FilmEdit | None:
        return self._edits.get(key)

    def put(self, key: str, edit: FilmEdit) -> None:
        with self._lock:
            self._edits[key] = edit
            self._save()

    def delete(self, key: str) -> None:
        with self._lock:
            if self._edits.pop(key, None) is not None:
                self._save()

    def move_footage(self, old_root: str, new_root: str) -> None:
        """Keeps edits after the footage is copied elsewhere: recording keys start with their folder path."""
        def fix(key: str) -> str:
            moved = key.startswith(old_root) and key[len(old_root):len(old_root) + 1] in ("/", "|")
            return new_root + key[len(old_root):] if moved else key
        with self._lock:
            for edit in self._edits.values():
                edit.recordings = [fix(k) for k in edit.recordings]
                for cut in edit.cuts:
                    cut.rec = fix(cut.rec)
            self._save()

    def _save(self) -> None:
        data = {k: v.model_dump() for k, v in self._edits.items()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)
