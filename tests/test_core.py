import os
import shutil
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from gopro_daily.config import AppConfig
from gopro_daily.layout import generate_layout
from gopro_daily.pipeline import Context, output_path, process_group, video_encode_args
from gopro_daily.scanner import Chapter, Recording, find_chapters, group_recordings, scan


def rec(number: int, start: str | None, width=3840, height=2160, fps="60000/1001") -> Recording:
    return Recording(
        key=f"/sd|H|{number:04d}", directory=Path("/sd"), number=number, letter="H",
        chapters=[Chapter(path=Path(f"/sd/GH01{number:04d}.MP4"), chapter=1, size=100)],
        start=datetime.fromisoformat(start) if start else None, duration=60,
        width=width, height=height, fps=fps, vcodec="h264", pix_fmt="yuvj420p",
    )


# ------------------------------------------------------------------------------------------------ grouping


def test_groups_recordings_by_day_in_chronological_order():
    config = AppConfig()
    groups = group_recordings(
        [rec(3, "2026-07-02T09:00:00"), rec(1, "2026-07-01T18:00:00"), rec(2, "2026-07-01T08:00:00")], config
    )
    assert [g.label for g in groups] == ["2026-07-01", "2026-07-02"]
    assert [r.number for r in groups[0].recordings] == [2, 1]


def test_day_start_hour_moves_night_recordings_to_previous_day():
    config = AppConfig()
    config.grouping.day_start_hour = 5
    groups = group_recordings([rec(1, "2026-07-01T23:00:00"), rec(2, "2026-07-02T02:30:00")], config)
    assert len(groups) == 1 and groups[0].label == "2026-07-01"


@pytest.mark.parametrize("mode,expected", [("recording", 2), ("all", 1)])
def test_alternative_grouping_modes(mode, expected):
    config = AppConfig()
    config.grouping.group_by = mode
    groups = group_recordings([rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:00:00")], config)
    assert len(groups) == expected


def test_mixed_formats_are_flagged():
    groups = group_recordings([rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:00:00", width=1920, height=1080)],
                              AppConfig())
    assert groups[0].homogeneous is False


def test_find_chapters_groups_chapters_of_same_recording(tmp_path: Path):
    for name in ["GH010042.MP4", "GH020042.MP4", "GX010043.MP4", "GL010042.LRV", "GOPR0001.JPG", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    config = AppConfig()
    config.source.input_dir = str(tmp_path)
    found = find_chapters(config)
    assert sorted(len(v) for v in found.values()) == [1, 2]


# ------------------------------------------------------------------------------------------------ layout


def test_generated_layout_is_valid_xml_and_scales_with_resolution():
    config = AppConfig().overlay
    small = ET.fromstring(generate_layout(config, 1920, 1080))
    big = ET.fromstring(generate_layout(config, 3840, 2160))
    map_small = small.find(".//component[@type='moving_map']")
    map_big = big.find(".//component[@type='moving_map']")
    assert int(map_big.get("size")) == 2 * int(map_small.get("size"))
    # top-right corner: the map ends exactly at width - margin
    assert int(map_big.get("x")) + int(map_big.get("size")) == 3840 - 2 * config.margin


def test_widgets_in_same_corner_do_not_overlap():
    config = AppConfig().overlay
    config.journey_map.enabled = True
    config.journey_map.corner = "top-right"
    root = ET.fromstring(generate_layout(config, 1920, 1080))
    mm = root.find(".//component[@type='moving_map']")
    jm = root.find(".//component[@name='journey_map']")
    assert int(jm.get("y")) >= int(mm.get("y")) + int(mm.get("size"))


def test_disabled_widgets_are_not_rendered():
    config = AppConfig().overlay
    config.minimap.enabled = False
    config.datetime.enabled = False
    root = ET.fromstring(generate_layout(config, 1920, 1080))
    assert list(root) == []


# ------------------------------------------------------------------------------------------------ encoding & naming


def test_videotoolbox_hevc_is_tagged_for_quicktime():
    args = video_encode_args(AppConfig())
    assert args[:2] == ["-c:v", "hevc_videotoolbox"]
    assert ["-tag:v", "hvc1"] == args[-2:]
    assert "-prio_speed" in args


def test_bitrate_mode():
    config = AppConfig()
    config.encoding.rate_control = "bitrate"
    config.encoding.bitrate = "80M"
    assert "80M" in video_encode_args(config)


def test_output_filename_template():
    config = AppConfig()
    config.output.output_dir = "/out"
    config.output.filename_template = "GoPro {date} ({recordings})"
    group = group_recordings([rec(1, "2026-07-01T08:00:00"), rec(2, "2026-07-01T09:30:00")], config)[0]
    assert output_path(group, config) == Path("/out/GoPro 2026-07-01 (2).mp4")


def test_invalid_config_values_are_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"grouping": {"timezone": "Nowhere/City"}})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"source": {"file_pattern": r"^G(\d+)"}})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"overlay": {"datetime": {"color": "300,0,0"}}})


# ------------------------------------------------------------------------------------------------ integration

SAMPLE = os.environ.get("GOPRO_SAMPLE")  # path to a real GoPro MP4 (with GPMF telemetry)


@pytest.mark.skipif(not SAMPLE or not shutil.which("ffmpeg"), reason="set GOPRO_SAMPLE=/path/GH01xxxx.MP4 to run")
def test_end_to_end_with_real_gopro_file(tmp_path: Path):
    sd = tmp_path / "sd"
    sd.mkdir()
    shutil.copy(SAMPLE, sd / "GH010001.MP4")
    config = AppConfig()
    config.source.input_dir, config.output.output_dir = str(sd), str(tmp_path / "out")
    groups = scan(config)
    assert len(groups) == 1
    ctx = Context(log=lambda _: None, progress=lambda *_: None, cancel_event=threading.Event(),
                  processes=set(), lock=threading.Lock())
    out = process_group(groups[0], config, ctx)
    assert out.exists() and out.stat().st_size > 0
    assert not (out.parent / ".gopro-daily-work").exists()


@pytest.mark.skipif(not SAMPLE or not shutil.which("ffmpeg"), reason="set GOPRO_SAMPLE=/path/GH01xxxx.MP4 to run")
def test_end_to_end_edited_film_keeps_only_the_cuts(tmp_path: Path):
    from gopro_daily.edits import Cut, make_edit, plan_pieces
    from gopro_daily.scanner import probe

    sd = tmp_path / "sd"
    sd.mkdir()
    shutil.copy(SAMPLE, sd / "GH010001.MP4")
    config = AppConfig()
    config.source.input_dir, config.output.output_dir = str(sd), str(tmp_path / "out")
    config.overlay.enabled = False
    group = scan(config)[0]
    key = group.recordings[0].key
    pieces = plan_pieces(group, make_edit(group, [Cut(rec=key, start=2, end=6), Cut(rec=key, start=10, end=14)]))
    ctx = Context(log=lambda _: None, progress=lambda *_: None, cancel_event=threading.Event(),
                  processes=set(), lock=threading.Lock())
    out = process_group(group, config, ctx, pieces)
    duration = float(probe(out)["format"]["duration"])
    # lossless cuts start on the keyframe before each in-point: never shorter, at most ~1 s longer per cut
    assert 8 <= duration <= 8 + 2 * 1.2


# ------------------------------------------------------------------------------------------------ free positioning


def test_dragged_position_is_relative_to_frame_and_consistent_across_resolutions():
    from gopro_daily.config import Position
    from gopro_daily.layout import build_widgets

    config = AppConfig().overlay
    config.minimap.position = Position(x=0.5, y=0.25)
    for w, h in [(1920, 1080), (3840, 2160)]:
        mm = next(x for x in build_widgets(config, w, h) if x.key == "minimap")
        assert (mm.x, mm.y) == (w // 2, h // 4)
        xml = ET.fromstring(generate_layout(config, w, h)).find(".//component[@type='moving_map']")
        assert (int(xml.get("x")), int(xml.get("y"))) == (mm.x, mm.y)


def test_dragged_position_is_clamped_inside_the_frame():
    from gopro_daily.config import Position
    from gopro_daily.layout import build_widgets

    config = AppConfig().overlay
    config.minimap.position = Position(x=1.0, y=1.0)
    mm = next(x for x in build_widgets(config, 1920, 1080) if x.key == "minimap")
    assert mm.x + mm.w == 1920 and mm.y + mm.h == 1080


def test_dragged_widget_leaves_corner_stack():
    from gopro_daily.config import Position
    from gopro_daily.layout import build_widgets

    config = AppConfig().overlay
    config.journey_map.enabled, config.journey_map.corner = True, "top-right"
    config.minimap.position = Position(x=0.1, y=0.5)
    jm = next(x for x in build_widgets(config, 1920, 1080) if x.key == "journey_map")
    assert jm.y == config.margin  # takes the top of the corner, the minimap no longer occupies it


def test_text_box_matches_font_metrics():
    from gopro_daily.layout import build_widgets, text_metrics

    config = AppConfig().overlay
    dt = next(x for x in build_widgets(config, 1920, 1080) if x.key == "datetime")
    width, _, _ = text_metrics(config.font, config.datetime.font_size, dt.texts[1].text)
    assert dt.w >= width


# ------------------------------------------------------------------------------------------------ per-chapter export


def test_overlay_export_renders_each_chapter_and_concats_in_film_order(tmp_path: Path, monkeypatch):
    from gopro_daily import pipeline
    from gopro_daily.edits import Cut, make_edit, plan_pieces

    chapters = []
    for n, d in enumerate([30, 40, 20], 1):
        (tmp_path / f"GH{n:02d}0001.MP4").write_bytes(b"")
        chapters.append(Chapter(path=tmp_path / f"GH{n:02d}0001.MP4", chapter=n, size=100, duration=d))
    r = rec(1, "2026-07-01T08:00:00")
    r.chapters, r.duration, r.has_gpmd = chapters, 90, True
    config = AppConfig()
    config.output.output_dir, config.output.work_dir = str(tmp_path / "out"), str(tmp_path / "work")
    config.overlay.journey_map.enabled = False
    group = group_recordings([r], config)[0]

    rendered, cut, concat = [], [], []

    def render(src, rec_, dest, layout, config_, ctx, task, config_dir):
        rendered.append(src)
        ctx.progress(task, "overlay", 0.5)
        return dest

    monkeypatch.setattr(pipeline, "render_overlay", render)
    monkeypatch.setattr(pipeline, "cut_piece", lambda src, span, dest, *_: cut.append((src.name, span.start, span.end)) or dest)
    monkeypatch.setattr(pipeline, "concat_products",
                        lambda products, dest, *a, **k: concat.append(products) or dest.write_bytes(b""))
    progress: list[tuple] = []
    ctx = Context(log=lambda _: None, progress=lambda *a: progress.append(a), cancel_event=threading.Event(),
                  processes=set(), lock=threading.Lock())

    process_group(group, config, ctx)
    assert set(rendered) == {c.path for c in chapters}  # whole chapters straight from the source, no join, no cut
    assert cut == []
    assert [p.name for p in concat[0]] == ["H0001_000_overlay.mp4", "H0001_001_overlay.mp4", "H0001_002_overlay.mp4"]
    overlay = [f for task, stage, f in progress if stage == "overlay"]
    assert overlay == sorted(overlay) and overlay[-1] == 1.0  # the piece moves forward through its chapters

    rendered.clear(), concat.clear()
    config.output.overwrite = True
    process_group(group, config, ctx, plan_pieces(group, make_edit(group, [Cut(rec=r.key, start=20, end=80)])))
    assert sorted(cut) == [("GH010001.MP4", 20, 30), ("GH030001.MP4", 0, 10)]
    assert {p.name for p in rendered} == {"H0001_000_cut0.mp4", "GH020001.MP4", "H0001_002_cut0.mp4"}


def test_overlay_profile_ends_with_the_video(tmp_path: Path):
    # plain `overlay` outlives the video when the 10 fps overlay stream is longer: +1 frame at every unit join
    import json

    from gopro_daily.pipeline import PROFILE_NAME, write_ffmpeg_profile

    write_ffmpeg_profile(AppConfig(), tmp_path)
    profile = json.loads((tmp_path / "ffmpeg-profiles.json").read_text())[PROFILE_NAME]
    assert profile["filter"] == "[1:v]tpad=stop=-1:stop_mode=clone[ov];[0:v][ov]overlay=shortest=1"


def test_1440p_scales_the_video_before_the_overlay(tmp_path: Path):
    import json

    from gopro_daily.pipeline import (PROFILE_NAME, input_args, output_size, profile_name, scale_filter,
                                      write_ffmpeg_profile)

    config = AppConfig()
    config.encoding.resolution = "1440p"
    uhd, hd, tall = rec(1, None), rec(2, None, width=1920, height=1080), rec(3, None, width=2160, height=3840)
    assert output_size(uhd, config) == (2560, 1440)
    assert output_size(hd, config) is None  # never scales up
    assert output_size(tall, config) == (1440, 2560)
    assert output_size(uhd, AppConfig()) is None
    custom = config.model_copy(deep=True)
    custom.overlay.layout_mode = "xml"
    assert output_size(uhd, custom) is None  # a hand-written layout is tied to the frame size it was made for

    write_ffmpeg_profile(config, tmp_path, [uhd, hd])
    profiles = json.loads((tmp_path / "ffmpeg-profiles.json").read_text())
    assert set(profiles) == {PROFILE_NAME, profile_name(uhd, config)} and profile_name(hd, config) == PROFILE_NAME
    scaled = profiles[profile_name(uhd, config)]
    assert scaled["input"] == ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
    assert scaled["filter"] == ("[0:v]scale_vt=w=2560:h=1440,hwdownload,format=nv12[v];"
                                "[1:v]tpad=stop=-1:stop_mode=clone[ov];[v][ov]overlay=shortest=1")

    deep = rec(4, None)
    deep.pix_fmt = "yuv420p10le"
    assert scale_filter(deep, (2560, 1440), config).endswith("format=p010le")
    config.encoding.hw_decode = False
    assert scale_filter(uhd, (2560, 1440), config) == "scale=2560:1440" and input_args(config, scaled=True) == []


def test_estimate_of_size_and_time():
    from gopro_daily.edits import Piece
    from gopro_daily.pipeline import ENCODE_SPEED, estimate

    r = rec(1, None)
    r.duration, r.chapters[0].size = 3600, 20_000_000_000
    pieces = [Piece(r, 0, 1800)]
    config = AppConfig()
    assert estimate(pieces, config) == (None, 1800 / ENCODE_SPEED["source"])  # constant quality: size unknown

    config.encoding.rate_control, config.encoding.bitrate = "bitrate", "15M"
    assert estimate(pieces, config)[0] == round(1800 * (15e6 + 192e3) / 8)
    config.encoding.resolution = "1440p"
    assert estimate(pieces, config)[1] == 1800 / ENCODE_SPEED["1440p"]

    config.overlay.enabled = False  # lossless join: the kept half of the source
    assert estimate(pieces, config)[0] == 10_000_000_000


def test_preflight_stops_a_read_only_or_full_destination(tmp_path: Path, monkeypatch):
    from collections import namedtuple

    from gopro_daily import pipeline

    config = AppConfig()
    config.output.output_dir, config.output.work_dir = str(tmp_path / "not-created-yet"), str(tmp_path / "work")
    free = lambda n: monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda _: namedtuple("U", "free")(n))  # noqa: E731

    free(100e9)
    pipeline.preflight(config, 40e9, 40e9)  # same drive: parts + film = 80 GB
    with pytest.raises(pipeline.StepFailed, match="Not enough space"):
        pipeline.preflight(config, 60e9, 60e9)
    (tmp_path / "work").mkdir()  # destination and work folder on different drives: each holds the film once
    stat = os.stat
    monkeypatch.setattr(pipeline.os, "stat", lambda p, **k: namedtuple("S", "st_dev st_mode")(str(p), stat(p, **k).st_mode))
    pipeline.preflight(config, 60e9, 60e9)
    monkeypatch.undo()
    monkeypatch.setattr(pipeline.os, "access", lambda *_: False)
    with pytest.raises(pipeline.StepFailed, match="Cannot write"):
        pipeline.preflight(config, 0, 0)


def test_cancel_stops_a_step_whose_child_keeps_running():
    import subprocess
    import sys
    import time

    from gopro_daily.pipeline import Cancelled, run

    ctx = Context(log=lambda _: None, progress=lambda *_: None, cancel_event=threading.Event(),
                  processes=set(), lock=threading.Lock())
    # like gopro-dashboard -> ffmpeg: the child inherits stdout and outlives a terminated parent
    cmd = [sys.executable, "-c", "import subprocess, time; subprocess.Popen(['sleep', '30']); time.sleep(30)"]
    threading.Timer(0.5, ctx.cancel).start()
    started = time.time()
    with pytest.raises(Cancelled):
        run(cmd, ctx)
    assert time.time() - started < 5
    assert subprocess.run(["pgrep", "-f", "sleep 30"], capture_output=True).returncode == 1  # no orphan left



def test_cancel_stops_the_gps_read_at_once(tmp_path: Path):
    import subprocess
    import time

    from gopro_daily.pipeline import Cancelled, build_tracks

    os.mkfifo(tmp_path / "GH010001.MP4")  # nothing ever writes it, so reading its GPS blocks like a slow chapter
    r = rec(1, "2026-07-01T08:00:00")
    r.chapters, r.has_gpmd = [Chapter(path=tmp_path / "GH010001.MP4", chapter=1, size=100, duration=30)], True
    ctx = Context(log=lambda _: None, progress=lambda *_: None, cancel_event=threading.Event(),
                  processes=set(), lock=threading.Lock())
    threading.Timer(0.5, ctx.cancel).start()
    started = time.time()
    with pytest.raises(Cancelled):
        build_tracks([r], tmp_path, AppConfig(), ctx)
    assert time.time() - started < 3
    time.sleep(0.2)
    assert subprocess.run(["pgrep", "-f", str(tmp_path)], capture_output=True).returncode == 1  # no ffprobe left


def test_kill_tree_ignores_a_group_of_zombies():
    import signal
    import subprocess
    import time

    from gopro_daily.pipeline import _kill_tree

    proc = subprocess.Popen(["true"], start_new_session=True)
    time.sleep(0.3)  # exited, not reaped yet: macOS killpg raises EPERM here
    _kill_tree(proc, signal.SIGTERM)
    proc.wait()


def test_gps_read_gets_more_than_the_45_s_of_gopro_overlay(monkeypatch):
    from datetime import timedelta

    from gopro_overlay.ffmpeg import FFMPEG

    from gopro_daily.track import lift_gps_timeout

    seen = []
    monkeypatch.setattr(FFMPEG, "stream", lambda self, args, cb, timeout=None: seen.append(timeout))
    lift_gps_timeout()
    monkeypatch.setattr(FFMPEG, "stream", FFMPEG.stream)  # so the patched method is undone after the test
    FFMPEG().stream([], cb=None, timeout=timedelta(seconds=45))
    assert seen == [timedelta(minutes=15)]
