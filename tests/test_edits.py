from datetime import datetime
from pathlib import Path

from gopro_daily.config import AppConfig
from gopro_daily.edits import Cut, EditStore, FilmEdit, make_edit, plan_pieces, resolve_cuts
from gopro_daily.jobs import Job
from gopro_daily.scanner import Chapter, Group, Recording, group_recordings


def rec(number: int, start: str, duration: float = 60) -> Recording:
    return Recording(
        key=f"/sd|X|{number:04d}", directory=Path("/sd"), number=number, letter="X",
        chapters=[Chapter(path=Path(f"/sd/GX01{number:04d}.MP4"), chapter=1, size=100, duration=duration)],
        start=datetime.fromisoformat(start), duration=duration, width=1920, height=1080, fps="30/1",
    )


def film(*recordings: Recording) -> Group:
    return group_recordings(list(recordings), AppConfig())[0]


A, B = "/sd|X|0001", "/sd|X|0002"


def test_unedited_film_renders_every_recording_whole():
    group = film(rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:00:00"))
    pieces = plan_pieces(group, None)
    assert [(p.rec.key, p.start, p.end, p.full) for p in pieces] == [(A, 0, 60, True), (B, 0, 60, True)]


def test_removed_recording_and_trimmed_range():
    group = film(rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:00:00"))
    edit = make_edit(group, [Cut(rec=B, start=10, end=25)])
    pieces = plan_pieces(group, edit)
    assert [(p.rec.key, p.start, p.end, p.full) for p in pieces] == [(B, 10, 25, False)]
    assert pieces[0].id != B  # a partial piece gets its own progress task


def test_split_without_removal_is_rendered_as_one_piece():
    group = film(rec(1, "2026-07-01T08:00:00"))
    edit = make_edit(group, [Cut(rec=A, start=0, end=20), Cut(rec=A, start=20, end=60)])
    assert len(resolve_cuts(group, edit)) == 2  # the editor still shows the split
    assert [(p.start, p.end, p.full) for p in plan_pieces(group, edit)] == [(0, 60, True)]


def test_cuts_are_clamped_sorted_and_never_overlap():
    group = film(rec(1, "2026-07-01T08:00:00"))
    edit = make_edit(group, [Cut(rec=A, start=30, end=90), Cut(rec=A, start=5, end=40), Cut(rec=A, start=1, end=1.1)])
    # the 0.1 s cut is too short to render; the overlapping part of 30–90 is trimmed and its tail clamped to 60
    assert [(c.start, c.end) for c in resolve_cuts(group, edit)] == [(5, 40), (40, 60)]


def test_recordings_found_after_editing_are_kept_whole_and_missing_ones_dropped():
    group = film(rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:00:00"))
    edit = FilmEdit(recordings=[A, "/gone|X|0009"], cuts=[Cut(rec=A, start=0, end=10), Cut(rec="/gone|X|0009", start=0, end=5)])
    assert [(c.rec, c.start, c.end) for c in resolve_cuts(group, edit)] == [(A, 0, 10), (B, 0, 60)]


def test_everything_removed_plans_nothing():
    group = film(rec(1, "2026-07-01T08:00:00"))
    assert plan_pieces(group, make_edit(group, [])) == []


def test_job_tasks_follow_pieces_and_join_progress_reaches_every_piece_of_a_recording():
    group = film(rec(1, "2026-07-01T08:00:00"))
    edit = make_edit(group, [Cut(rec=A, start=0, end=10), Cut(rec=A, start=30, end=40)])
    job = Job(group=group, config=AppConfig(), pieces=plan_pieces(group, edit))
    assert [t["label"] for k, t in job.tasks.items() if k != "group"] == ["X0001 · 0:00–0:10", "X0001 · 0:30–0:40"]
    job.set_progress(A, "join", 0.5)
    assert all(t["progress"] == 0.5 for k, t in job.tasks.items() if k != "group")
    assert job.summary()["duration"] == 20


def test_job_progress_is_zero_before_any_task_starts():
    group = film(rec(1, "2026-07-01T08:00:00"))
    job = Job(group=group, config=AppConfig(), pieces=plan_pieces(group, None))  # overlay on: two stages
    assert job.overall == 0


def test_store_round_trip(tmp_path: Path):
    path = tmp_path / "edits.json"
    store = EditStore(path)
    store.put("2026-07-01", FilmEdit(recordings=[A], cuts=[Cut(rec=A, start=1, end=2)]))
    assert EditStore(path).get("2026-07-01").cuts[0].end == 2
    store.delete("2026-07-01")
    assert EditStore(path).get("2026-07-01") is None
