"""Continuity, duplicate detection and dated (exported) file support."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from gopro_daily.config import DEFAULT_DATED_PATTERN, AppConfig
from gopro_daily.scanner import Chapter, Recording, find_chapters, group_recordings, _mark_duplicates


def rec(number: int, start: str | None, name: str | None = None, duration: float = 60, size: int = 1000,
        letter: str = "H") -> Recording:
    name = name or f"GH01{number:04d}.MP4"
    return Recording(
        key=f"/sd|{name}", directory=Path("/sd"), number=number, letter=letter,
        chapters=[Chapter(path=Path(f"/sd/{name}"), chapter=1, size=size)],
        start=datetime.fromisoformat(start) if start else None, duration=duration,
    )


# ------------------------------------------------------------------------------------------------ dated file names


def test_dated_pattern_recognizes_exported_files_and_copies(tmp_path: Path):
    for name in ["20260911_121946.mp4", "20260917_172129.mp4", "20260917_172129 2.mp4", "GX013737.MP4"]:
        (tmp_path / name).write_bytes(b"")
    config = AppConfig()
    config.source.input_dir = str(tmp_path)
    config.source.dated_pattern = DEFAULT_DATED_PATTERN
    found = find_chapters(config)
    kinds = sorted(kind for items in found.values() for _, _, kind in items)
    assert kinds == ["dated", "dated", "dated", "native"]


def test_dated_pattern_is_opt_in(tmp_path: Path):
    for name in ["20260911_121946.mp4", "GX013737.MP4"]:
        (tmp_path / name).write_bytes(b"")
    config = AppConfig()
    config.source.input_dir = str(tmp_path)
    kinds = [kind for items in find_chapters(config).values() for _, _, kind in items]
    assert kinds == ["native"]


def test_scan_takes_start_from_filename_for_dated_files(tmp_path, monkeypatch):
    from gopro_daily import scanner

    name = "20260911_121946.mp4"
    (tmp_path / name).write_bytes(b"")
    config = AppConfig()
    config.source.input_dir = str(tmp_path)
    config.source.dated_pattern = DEFAULT_DATED_PATTERN

    # Simulated ffprobe output: metadata says something completely different from the filename (re-muxed clip).
    def fake_probe(path, ffprobe="ffprobe"):
        return {
            "format": {"duration": "16.9", "tags": {"creation_time": "2026-09-11T10:20:05Z"}},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30/1",
                         "codec_name": "h264", "pix_fmt": "yuv420p"}],
        }

    monkeypatch.setattr(scanner, "probe", fake_probe)
    groups = scanner.scan(config)
    assert len(groups) == 1
    [r] = groups[0].recordings
    assert r.start_source == "filename"
    assert r.start == datetime(2026, 9, 11, 12, 19, 46)


# ------------------------------------------------------------------------------------------------ continuity


def test_gap_and_continuity_under_threshold():
    config = AppConfig()
    config.grouping.continuity_gap_seconds = 3.0
    groups = group_recordings(
        [rec(1, "2026-07-01T12:25:44", duration=14.2), rec(2, "2026-07-01T12:26:09")], config
    )
    a, b = groups[0].recordings
    assert a.gap_before is None and a.continuous is None
    assert b.gap_before == pytest.approx(10.8, abs=0.05)
    assert b.continuous is False  # 10.8s > 3s threshold: a real pause


def test_continuity_gap_seconds_threshold_controls_continuous_flag():
    config = AppConfig()
    config.grouping.continuity_gap_seconds = 15.0
    groups = group_recordings(
        [rec(1, "2026-07-01T12:25:44", duration=14.2), rec(2, "2026-07-01T12:26:09")], config
    )
    b = groups[0].recordings[1]
    assert b.continuous is True  # same 10.8s gap, now under the raised threshold


def test_small_negative_gap_from_clock_slack_is_still_continuous():
    # Native chapters or clips whose real start is a second or two before the nominal boundary.
    config = AppConfig()
    groups = group_recordings(
        [rec(1, "2026-07-01T12:00:00", duration=60), rec(2, "2026-07-01T12:00:59")], config
    )
    b = groups[0].recordings[1]
    assert b.gap_before == pytest.approx(-1.0, abs=0.05)
    assert b.continuous is True


def test_native_chapters_still_group_as_one_recording_with_chapter_count(tmp_path: Path):
    for name in ["GH010042.MP4", "GH020042.MP4"]:
        (tmp_path / name).write_bytes(b"")
    config = AppConfig()
    config.source.input_dir = str(tmp_path)
    config.source.dated_pattern = DEFAULT_DATED_PATTERN
    found = find_chapters(config)
    assert len(found) == 1
    [(key, items)] = found.items()
    assert len(items) == 2
    r = rec(42, "2026-07-01T08:00:00")
    r.chapters.append(Chapter(path=Path("/sd/GH020042.MP4"), chapter=2, size=1000))
    assert r.to_dict()["chapter_count"] == 2


# ------------------------------------------------------------------------------------------------ duplicates


def test_duplicate_file_is_flagged_and_excluded_from_recordings():
    config = AppConfig()
    original = rec(1, "2026-09-17T17:21:29", name="20260917_172129.mp4", duration=30.0, size=5_000_000)
    copy = rec(1, "2026-09-17T17:21:29", name="20260917_172129 2.mp4", duration=30.05, size=5_000_000)
    recordings = [copy, original]  # order shouldn't matter
    _mark_duplicates(recordings)
    [group] = group_recordings(recordings, config)
    assert [r.chapters[0].path.name for r in group.recordings] == ["20260917_172129.mp4"]
    assert [r.chapters[0].path.name for r in group.duplicates] == ["20260917_172129 2.mp4"]
    assert group.duplicates[0].duplicate_of == "20260917_172129.mp4"
    d = group.to_dict()
    assert d["duplicates"][0]["duplicate_of"] == "20260917_172129.mp4"
    assert len(d["recordings"]) == 1


def test_recordings_with_different_size_are_not_duplicates():
    config = AppConfig()
    a = rec(1, "2026-09-17T17:21:29", name="a.mp4", duration=30.0, size=100)
    b = rec(2, "2026-09-17T17:21:29", name="b.mp4", duration=30.0, size=200)
    recordings = [a, b]
    _mark_duplicates(recordings)
    [group] = group_recordings(recordings, config)
    assert len(group.recordings) == 2
    assert group.duplicates == []


# ------------------------------------------------------------------------------------------------ day boundary


def test_day_start_hour_moves_00_08_clip_to_previous_day():
    config = AppConfig()
    config.grouping.day_start_hour = 1
    groups = group_recordings([rec(1, "2026-07-02T00:08:00")], config)
    assert len(groups) == 1
    assert groups[0].label == "2026-07-01"



def test_companion_files_are_matched_case_insensitively():
    from gopro_daily.scanner import _companion

    files = {n.lower(): Path(f"/sd/{n}") for n in ["GL010024.LRV", "GX010024.THM", "GOPR0007.LRV", "gh020031.thm"]}
    assert _companion("GX010024", files, ".lrv") == Path("/sd/GL010024.LRV")  # HERO6+: 2nd letter becomes L
    assert _companion("GX010024", files, ".thm") == Path("/sd/GX010024.THM")  # thumbnail keeps the chapter's name
    assert _companion("GOPR0007", files, ".lrv") == Path("/sd/GOPR0007.LRV")  # older cameras keep the stem
    assert _companion("GH020031", files, ".thm") == Path("/sd/gh020031.thm")
    assert _companion("GX019999", files, ".lrv") is None
