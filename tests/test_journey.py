"""Progressive, day-long journey map widget."""

import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

from gopro_daily.config import AppConfig
from gopro_daily.day_journey_map import DayJourneyMap
from gopro_daily.layout import generate_layout
from gopro_daily.track import load_track, write_track

RED = (255, 0, 0)
T0 = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc).timestamp()


class At:
    x = y = 0


class Entry:
    def __init__(self, t):
        self.dt = dt.datetime.fromtimestamp(t, dt.timezone.utc)


def blank_renderer(map_):
    return Image.new("RGBA", map_.size, (230, 230, 230, 255))  # no tiles, no network


def segment(t0, lat0, lon0, n=100):
    return [(t0 + i, lat0 + 0.00005 * i, lon0 + 0.00005 * i) for i in range(n)]


def make_track(tmp_path: Path):
    rec_a = segment(T0, 40.41, -3.69)
    rec_b = segment(T0 + 3600, 40.416, -3.684)  # one hour later, continues the route
    write_track([rec_b, rec_a], tmp_path / "t.json")  # order must not matter
    return rec_a, rec_b, load_track(tmp_path / "t.json")


def red_pixels(widget, t) -> int:
    widget.entry = lambda: Entry(t)
    img = Image.new("RGBA", (widget.size, widget.size), (0, 0, 0, 0))
    widget.draw(img, ImageDraw.Draw(img))
    return sum(1 for px in img.getdata() if px[:3] == RED)


def widget(track, **kw):
    return DayJourneyMap(at=At(), entry=None, track=track, renderer=blank_renderer, size=200, **kw)


def test_track_is_sorted_by_time_and_keeps_recording_segments(tmp_path):
    rec_a, _, track = make_track(tmp_path)
    assert [p[0] for p in track] == sorted(p[0] for p in track)
    assert track[0][:3] == rec_a[0] and track[0][3] == 1  # segment id = position in the given list


def test_line_grows_as_time_advances(tmp_path):
    rec_a, _, track = make_track(tmp_path)
    w = widget(track)
    counts = [red_pixels(w, rec_a[i][0]) for i in (0, 30, 60, 99)]
    assert counts[0] < counts[1] < counts[2] < counts[3]


def test_next_recording_starts_with_the_previous_route_already_drawn(tmp_path):
    rec_a, rec_b, track = make_track(tmp_path)
    end_of_a = red_pixels(widget(track), rec_a[-1][0])
    start_of_b = red_pixels(widget(track), rec_b[0][0])  # a fresh widget, as in the next recording's render
    end_of_b = red_pixels(widget(track), rec_b[-1][0])
    assert start_of_b >= end_of_a
    assert end_of_b > start_of_b


def test_static_mode_shows_whole_route_from_first_frame(tmp_path):
    rec_a, rec_b, track = make_track(tmp_path)
    w = widget(track, progressive=False)
    assert red_pixels(w, rec_a[0][0]) >= red_pixels(widget(track), rec_b[-1][0]) * 0.95


def test_no_points_draws_map_without_failing(tmp_path):
    assert red_pixels(widget([]), T0) == 0


def test_layout_uses_progressive_widget_with_group_track():
    config = AppConfig().overlay
    config.journey_map.enabled = True
    root = ET.fromstring(generate_layout(config, 1920, 1080, "/tmp/group_track.json"))
    jm = root.find(".//component[@name='journey_map']")
    assert jm.get("type") == "day_journey_map"
    assert jm.get("track") == "/tmp/group_track.json"
    assert jm.get("progressive") == "True"
