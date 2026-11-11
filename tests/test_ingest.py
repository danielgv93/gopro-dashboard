import os
from pathlib import Path
from types import SimpleNamespace

from gopro_daily.config import AppConfig
from gopro_daily.edits import Cut, EditStore, FilmEdit
from gopro_daily.ingest import ORIGIN_FILE, FootageCopy
from gopro_daily.pipeline import work_root
from gopro_daily.scanner import Chapter, Group, Recording


def test_copy_goes_film_by_film_keeps_mtime_and_drops_cancelled_films(tmp_path: Path):
    sd, dest = tmp_path / "sd", tmp_path / "work" / "exports" / "source-1"
    (sd / "100GOPRO").mkdir(parents=True)
    a, b = sd / "100GOPRO" / "GX010030.MP4", sd / "100GOPRO" / "GX010031.MP4"
    a.write_bytes(b"v" * 1000)
    b.write_bytes(b"w" * 10)
    os.utime(a, (1_600_000_000, 1_600_000_000))
    batches = [[(a, dest / "100GOPRO" / a.name)], [(b, dest / "100GOPRO" / b.name)]]
    calls, copied = [], {0: 0, 1: 0}

    def progress(i, n):
        copied[i] += n

    copier = FootageCopy()
    copier.start_films(batches, sd, dest, before=lambda i: calls.append(("before", i)) or True,
                       done=lambda i: calls.append(("done", i)), dropped=lambda i: i == 1, progress=progress,
                       ended=lambda status, error: calls.append((status, error)))
    copier._thread.join()
    # film 2's export was cancelled: its file is dropped and it never counts as copied
    assert calls == [("before", 0), ("done", 0), ("before", 1), ("done", "")]
    assert copied == {0: 1000, 1: 0} and not (dest / "100GOPRO" / b.name).exists()
    assert (dest / "100GOPRO" / a.name).stat().st_mtime == 1_600_000_000
    assert not list(dest.rglob("*.partial"))


def test_edits_follow_footage_to_its_new_folder(tmp_path: Path):
    store = EditStore(tmp_path / "edits.json")
    store.put("2026-07-01", FilmEdit(recordings=["/Volumes/SD/DCIM|X|0030", "/Volumes/SD2|X|0031"],
                                     cuts=[Cut(rec="/Volumes/SD/DCIM|X|0030", start=0, end=5)]))
    store.move_footage("/Volumes/SD", "/local/SD")
    edit = EditStore(tmp_path / "edits.json").get("2026-07-01")
    assert edit.recordings == ["/local/SD/DCIM|X|0030", "/Volumes/SD2|X|0031"]  # SD2 is another folder
    assert edit.cuts[0].rec == "/local/SD/DCIM|X|0030"


def test_work_root_defaults_to_caches_and_honours_setting():
    config = AppConfig()
    assert work_root(config) == Path.home() / "Library/Caches/gopro-daily-work"
    config.output.work_dir = "~/gopro-work"
    assert work_root(config) == Path.home() / "gopro-work"


def test_delete_copy_removes_it_and_points_source_back_at_the_card(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from gopro_daily import server

    sd = tmp_path / "sd"
    sd.mkdir()
    video = sd / "GX010030.MP4"
    video.write_bytes(b"v" * 100)
    config = AppConfig()
    config.source.input_dir, config.output.work_dir = str(sd), str(tmp_path / "work")
    store = EditStore(tmp_path / "edits.json")
    monkeypatch.setattr(server, "save_config", lambda c: None)
    monkeypatch.setattr(server, "edits", store)
    monkeypatch.setattr(server, "footage_copy", FootageCopy())
    monkeypatch.setitem(server.state, "config", config)
    client = TestClient(server.app)

    opened = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda args: opened.append(args))
    assert client.delete("/api/copy").status_code == 400  # the source is the card, nothing to delete
    assert client.post("/api/copy/open").status_code == 404
    copy = tmp_path / "work" / "footage" / "sd"  # a whole-card copy made by an older version
    copy.mkdir(parents=True)
    (copy / video.name).write_bytes(video.read_bytes())
    (copy / ORIGIN_FILE).write_text(str(sd))
    config.source.input_dir = str(copy)
    store.put("day", FilmEdit(recordings=[f"{copy}|X|0030"], cuts=[]))

    assert client.post("/api/copy/open").status_code == 200 and opened[-1] == ["open", str(copy)]
    local = client.get("/api/copy").json()["local"]
    assert local["origin"] == str(sd) and local["origin_exists"] and local["size"] >= 100

    assert client.delete("/api/copy").json()["source"]["input_dir"] == str(sd)
    assert not copy.exists() and video.is_file()
    assert store.get("day").recordings == [f"{sd}|X|0030"]


def _copy_first_server(tmp_path: Path, monkeypatch, free: int):
    from gopro_daily import server
    from gopro_daily.jobs import Job

    sd = tmp_path / "sd"
    sd.mkdir()
    groups = {}
    for n, day in ((30, "day1"), (31, "day2"), (32, "skipped")):
        video = sd / f"GX01{n:04d}.MP4"
        video.write_bytes(b"v" * 100)
        rec = Recording(key=f"{sd}|X|{n:04d}", directory=sd, number=n, letter="X",
                        chapters=[Chapter(path=video, chapter=1, size=100, lrv=sd / "x.LRV")])
        groups[day] = Group(key=day, label=day, recordings=[rec])
    config = AppConfig()
    config.source.input_dir, config.output.work_dir = str(sd), str(tmp_path / "work")
    config.output.output_dir = str(tmp_path / "out")
    submitted = []

    def submit(group, config, pieces, cleanup=None, copy=None):  # queued, never run: the test plays the worker
        submitted.append(Job(group=group, config=config, pieces=[], cleanup=cleanup, copy=copy))
        return submitted[-1]

    monkeypatch.setattr(server, "edits", EditStore(tmp_path / "edits.json"))
    monkeypatch.setattr(server, "footage_copy", FootageCopy())
    monkeypatch.setattr(server, "plan_pieces", lambda g, e: [SimpleNamespace(rec=r) for r in g.recordings])
    monkeypatch.setattr(server, "estimate", lambda pieces, c: (0, 0))
    monkeypatch.setattr(server, "preflight", lambda *a: None)
    monkeypatch.setattr(server, "room", lambda folder: (tmp_path, free))
    monkeypatch.setattr(server, "SPACE_MARGIN", 0)
    monkeypatch.setattr(server.jobs, "submit", submit)
    monkeypatch.setitem(server.state, "config", config)
    monkeypatch.setitem(server.state, "groups", groups)
    return server, submitted


def test_copy_first_queues_every_film_with_a_copy_phase_and_waits_for_room(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    server, submitted = _copy_first_server(tmp_path, monkeypatch, free=150)  # room for one film at a time
    client = TestClient(server.app)

    assert client.post("/api/jobs", json={"keys": ["day1", "day2"], "copy_first": True}).json() == []
    assert [j.group.label for j in submitted] == ["day1", "day2"]  # both queued at once, the unselected day is not
    first, second = submitted
    copier = server.footage_copy
    copier.wait(lambda: second.copy["state"] == "waiting")
    assert first.copy["state"] == "done" and first.copy["copied"] == first.copy["total"] == 100
    assert first.copy["started"] > 0  # the UI's copy speed is measured from it
    assert first.summary()["copy"]["state"] == "done"
    chapter = first.group.recordings[0].chapters[0]
    assert chapter.path.is_file() and chapter.path.is_relative_to(tmp_path / "work" / "exports") and chapter.lrv is None
    assert first.group.recordings[0].key.startswith(str(tmp_path / "sd"))  # editor cuts still match

    first.cleanup()  # film 1 exported: its copy goes, making room for film 2
    first.finished = 1
    copier._thread.join()
    assert second.copy["state"] == "done" and not chapter.path.exists()
    assert second.group.recordings[0].chapters[0].path.is_file()


def test_copy_first_skips_a_cancelled_film_and_fails_the_rest_when_the_card_goes(tmp_path: Path, monkeypatch):
    import threading

    import pytest
    from fastapi.testclient import TestClient

    from gopro_daily.jobs import _wait_for_copy
    from gopro_daily.pipeline import Context, StepFailed

    server, submitted = _copy_first_server(tmp_path, monkeypatch, free=150)
    keys = ["day1", "day2", "skipped"]
    assert TestClient(server.app).post("/api/jobs", json={"keys": keys, "copy_first": True}).status_code == 200
    first, second, third = submitted
    copier = server.footage_copy
    copier.wait(lambda: second.copy["state"] == "waiting")

    second.status, second.finished = "cancelled", 1  # cancelled while queued: never copied
    copier.wait(lambda: third.copy["state"] == "waiting")
    (tmp_path / "sd" / "GX010032.MP4").unlink()  # the card is pulled before film 3 copies
    first.finished = 1
    copier._thread.join()
    assert second.copy["state"] == "waiting" and third.copy["state"] == "failed"
    assert "FileNotFoundError" in third.copy["error"]

    third.ctx = Context(log=lambda _: None, progress=lambda *_: None, cancel_event=threading.Event(),
                        processes=set(), lock=threading.Lock())
    with pytest.raises(StepFailed, match="Copying the footage to this Mac failed"):
        _wait_for_copy(third)  # what the worker does before exporting it


def test_copy_first_refuses_when_the_biggest_selected_film_does_not_fit(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    server, submitted = _copy_first_server(tmp_path, monkeypatch, free=99)
    res = TestClient(server.app).post("/api/jobs", json={"keys": ["day1"], "copy_first": True})
    assert res.status_code == 400 and "Not enough space" in res.json()["detail"] and not submitted


def test_clear_cache_deletes_export_work_files_but_keeps_footage(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from gopro_daily import server

    config = AppConfig()
    config.output.work_dir = str(tmp_path / "work")
    (tmp_path / "work" / "exports" / "day-1").mkdir(parents=True)
    (tmp_path / "work" / "exports" / "day-1" / "piece.mp4").write_bytes(b"x" * 50)
    (tmp_path / "work" / "footage").mkdir()
    monkeypatch.setitem(server.state, "config", config)
    client = TestClient(server.app)

    assert client.get("/api/cache").json()["size"] == 50
    assert client.delete("/api/cache").json()["size"] == 0
    assert not (tmp_path / "work" / "exports").exists() and (tmp_path / "work" / "footage").is_dir()


def test_cancelling_a_queued_job_runs_its_cleanup():
    from gopro_daily.jobs import Job, JobManager

    manager, cleaned = JobManager(), []
    job = Job(group=Group(key="day", label="day"), config=AppConfig(), pieces=[], cleanup=lambda: cleaned.append(1))
    manager.jobs[job.id] = job  # never put on the queue, so the worker leaves it queued
    manager.cancel(job.id)
    assert job.status == "cancelled" and cleaned == [1] and job.finished
