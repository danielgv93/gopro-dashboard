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
