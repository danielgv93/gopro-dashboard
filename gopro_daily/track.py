"""GPS track of a whole output group (a day), shared by every recording's overlay.

The journey map of each recording is drawn from this common track, so the route keeps growing across
recordings and the map framing is identical in all of them: in the final video it looks like one line.

Points are loaded with gopro-dashboard-overlay's own loader and GPS filters (DOP, max speed) and smoothed
the same way the dashboard smooths positions, so the line matches what the other widgets show.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig

POINTS_PER_SECOND = 5  # GoPro GPS is 10-18 Hz; 5 Hz is plenty for a line and keeps the track small


def lift_gps_timeout() -> None:
    """gopro-overlay hardcodes 45 s for ffmpeg to pull a chapter's GPS track. Straight from an SD card one 12 GB
    chapter takes ~35 s on its own and minutes when several are read at once, so every overlay failed.
    Call once per process: it replaces FFMPEG.stream for everything gopro-overlay runs afterwards."""
    from datetime import timedelta

    from gopro_overlay.ffmpeg import FFMPEG

    stream = FFMPEG.stream
    FFMPEG.stream = lambda self, args, cb, timeout=None: stream(self, args, cb, timeout=timedelta(minutes=15))


def extract_points(video: Path, config: AppConfig) -> list[tuple[float, float, float]]:
    """(unix time, lat, lon) of every GPS-locked sample in the video."""
    from datetime import timedelta

    from gopro_overlay import gpmd_filters, timeseries_process
    from gopro_overlay.ffmpeg import FFMPEG
    from gopro_overlay.ffmpeg_gopro import FFMPEGGoPro
    from gopro_overlay.gpmf import GPS_FIXED_VALUES
    from gopro_overlay.loading import GoproLoader
    from gopro_overlay.units import units

    ffmpeg_dir = Path(config.encoding.ffmpeg_dir) if config.encoding.ffmpeg_dir else None
    loader = GoproLoader(
        ffmpeg_gopro=FFMPEGGoPro(FFMPEG(ffmpeg_dir)),
        units=units,
        gps_lock_filter=gpmd_filters.standard(
            dop_max=config.overlay.gps_dop_max,
            speed_max=units.Quantity(config.overlay.gps_speed_max, "kph"),
        ),
    )
    framemeta = loader.load(video).framemeta
    locked = lambda e: e.gpsfix in GPS_FIXED_VALUES  # noqa: E731
    framemeta.process(timeseries_process.process_ses("point", lambda i: i.point, alpha=0.45), filter_fn=locked)

    points = []
    for entry in framemeta.items(step=timedelta(seconds=1 / POINTS_PER_SECOND)):
        if entry.point is not None and entry.gpsfix in GPS_FIXED_VALUES:
            points.append((entry.dt.timestamp(), round(entry.point.lat, 7), round(entry.point.lon, 7)))
    return points


def write_track(segments: list[list[tuple[float, float, float]]], dest: Path) -> int:
    """Stores the group track. Each recording is a segment, so gaps between recordings can be styled."""
    points = sorted((t, lat, lon, seg) for seg, pts in enumerate(segments) for t, lat, lon in pts)
    dest.write_text(json.dumps({"points": points}))
    return len(points)


def load_track(path: Path) -> list[tuple[float, float, float, int]]:
    return [tuple(p) for p in json.loads(Path(path).read_text())["points"]]
