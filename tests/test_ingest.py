import os
from pathlib import Path

from gopro_daily.config import AppConfig
from gopro_daily.edits import Cut, EditStore, FilmEdit
from gopro_daily.ingest import FootageCopy, copy_plan
from gopro_daily.pipeline import work_root
from gopro_daily.scanner import Chapter


def test_copy_keeps_layout_companions_and_mtime_and_skips_copied_files(tmp_path: Path):
    sd, dest = tmp_path / "sd", tmp_path / "work" / "footage" / "sd"
    (sd / "100GOPRO").mkdir(parents=True)
    video, lrv = sd / "100GOPRO" / "GX010030.MP4", sd / "100GOPRO" / "GL010030.LRV"
    video.write_bytes(b"v" * 1000)
    lrv.write_bytes(b"l" * 10)
    os.utime(video, (1_600_000_000, 1_600_000_000))
    chapters = [Chapter(path=video, chapter=1, size=1000, lrv=lrv)]
    done = []

    copier = FootageCopy()
    copier.start(chapters, sd, dest, lambda s, d: done.append((s, d)))
    copier._thread.join()
    assert copier.state["status"] == "done" and copier.state["copied"] == 1010
    assert (dest / "100GOPRO" / "GX010030.MP4").read_bytes() == b"v" * 1000
    assert (dest / "100GOPRO" / "GL010030.LRV").is_file()
    assert (dest / "100GOPRO" / "GX010030.MP4").stat().st_mtime == 1_600_000_000
    assert done == [(sd, dest)]
    assert not list(dest.rglob("*.partial"))

    (dest / "100GOPRO" / "GX010030.MP4").write_bytes(b"x" * 1000)  # same size = already copied, left alone
    copier.start(chapters, sd, dest, lambda s, d: None)
    copier._thread.join()
    assert (dest / "100GOPRO" / "GX010030.MP4").read_bytes() == b"x" * 1000


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
    chapter = Chapter(path=video, chapter=1, size=100)
    monkeypatch.setitem(server.state, "config", config)
    monkeypatch.setitem(server.state, "media", {chapter.media_id: chapter})
    client = TestClient(server.app)

    opened = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda args: opened.append(args))
    assert client.delete("/api/copy").status_code == 400  # the source is the card, nothing to delete
    assert client.post("/api/copy/open").status_code == 404
    assert client.post("/api/copy").status_code == 200
    server.footage_copy._thread.join()
    copy = tmp_path / "work" / "footage" / "sd"
    assert config.source.input_dir == str(copy)
    store.put("day", FilmEdit(recordings=[f"{copy}|X|0030"], cuts=[]))

    assert client.post("/api/copy/open").status_code == 200 and opened[-1] == ["open", str(copy)]
    local = client.get("/api/copy").json()["local"]
    assert local["origin"] == str(sd) and local["origin_exists"] and local["size"] >= 100

    assert client.delete("/api/copy").json()["source"]["input_dir"] == str(sd)
    assert not copy.exists() and video.is_file()
    assert store.get("day").recordings == [f"{sd}|X|0030"]


def test_copy_plan_puts_videos_first_and_proxies_last():
    sd = Path("/sd")
    chapters = [Chapter(path=sd / f"GX0{n}0030.MP4", chapter=n, size=1, lrv=sd / f"GL0{n}0030.LRV", thm=sd / f"GX0{n}0030.THM")
                for n in (2, 1)]
    names = [src.name for src, _ in copy_plan(chapters, sd, Path("/dest"))]
    assert names == ["GX010030.MP4", "GX010030.THM", "GX020030.MP4", "GX020030.THM", "GL010030.LRV", "GL020030.LRV"]


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
