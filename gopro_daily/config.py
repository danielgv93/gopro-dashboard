"""Application configuration: every knob of the pipeline lives here and is persisted as JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]

# GoPro naming: G + encoding letter (H=AVC, X=HEVC, L=low-res, P=photo/other) + 2-digit chapter + 4-digit recording.
# See https://community.gopro.com/s/article/GoPro-Camera-File-Naming-Convention
DEFAULT_FILE_PATTERN = r"^G(?P<letter>[HXL])(?P<chapter>\d{2})(?P<recording>\d{4})\.(?i:mp4)$"

# Exported/re-muxed files (e.g. by the Quik app, or a phone): "YYYYMMDD_HHMMSS.mp4", optionally with a macOS
# " 2" duplicate-copy suffix. No chapters — every file is its own recording; see scanner.py for details.
DEFAULT_DATED_PATTERN = r"^(?P<date>\d{8})_(?P<time>\d{6})(?P<copy> \d+)?\.(?i:mp4)$"


class SourceConfig(BaseModel):
    input_dir: str = ""
    recursive: bool = True
    # Regex with named groups `recording` and `chapter` (optional `letter`). Lets the app work with renamed files
    # or other cameras that split long recordings in chapters.
    file_pattern: str = DEFAULT_FILE_PATTERN
    # Regex with named groups `date` and `time` for exported/re-muxed files named after their start time.
    # Empty string disables recognition of this second naming scheme.
    dated_pattern: str = ""  # opt-in, e.g. DEFAULT_DATED_PATTERN for phone-style YYYYMMDD_HHMMSS names

    @field_validator("file_pattern")
    @classmethod
    def _valid_pattern(cls, v: str) -> str:
        groups = re.compile(v).groupindex
        if "recording" not in groups or "chapter" not in groups:
            raise ValueError("the pattern needs the named groups (?P<recording>...) and (?P<chapter>...)")
        return v

    @field_validator("dated_pattern")
    @classmethod
    def _valid_dated_pattern(cls, v: str) -> str:
        if not v:
            return v
        groups = re.compile(v).groupindex
        if "date" not in groups or "time" not in groups:
            raise ValueError("the pattern needs the named groups (?P<date>...) and (?P<time>...)")
        return v


class GroupingConfig(BaseModel):
    # day: one video per calendar day | recording: one video per recording | all: everything into one video
    group_by: Literal["day", "recording", "all"] = "day"
    # Where the recording start time comes from.
    time_source: Literal["metadata", "file_mtime"] = "metadata"
    # GoPro cameras store *local* camera time tagged as UTC. Keep True for GoPro; set False if your camera writes real UTC.
    metadata_is_local_time: bool = True
    timezone: str = "Europe/Madrid"
    # Recordings that start before this hour belong to the previous day (handy for night sessions).
    day_start_hour: int = Field(0, ge=0, le=23)
    # Skip accidental micro-recordings.
    min_recording_seconds: float = Field(0, ge=0)
    # Two consecutive recordings (within a day) are considered one continuous session when the gap between them
    # is at most this many seconds. Covers clock slack between a recording's filename and its real start.
    continuity_gap_seconds: float = Field(3.0, ge=0)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone '{v}'") from e
        return v


class OutputConfig(BaseModel):
    output_dir: str = ""
    # Available fields: {date} {start} {end} {group} {recordings}
    filename_template: str = "{date}"
    container: Literal["mp4", "mov"] = "mp4"
    overwrite: bool = False
    keep_intermediates: bool = False
    # Temporary files. Empty = <output_dir>/.gopro-daily-work (same volume = fast renames, no cross-disk copies).
    work_dir: str = ""


def _rgb(v: str) -> str:
    v = v.replace(" ", "")
    if not re.fullmatch(r"\d{1,3},\d{1,3},\d{1,3}(,\d{1,3})?", v) or any(int(x) > 255 for x in v.split(",")):
        raise ValueError("use R,G,B with values 0-255")
    return v


class Position(BaseModel):
    """Free position set by dragging in the preview: top-left of the widget as a fraction of the frame.

    Fractions keep the placement consistent across resolutions (1080p, 2.7K, 4K...)."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class DateTimeWidget(BaseModel):
    enabled: bool = True
    corner: Corner = "top-left"
    position: Position | None = None
    date_format: str = "%d/%m/%Y"
    time_format: str = "%H:%M:%S"
    font_size: int = Field(32, ge=8, le=400, description="Size of the time line at 1920px width; scales with video")
    color: str = "255,255,255"
    outline: str = "0,0,0"

    @field_validator("color", "outline")
    @classmethod
    def _valid_rgb(cls, v: str) -> str:
        return _rgb(v)


class MapWidget(BaseModel):
    enabled: bool = True
    corner: Corner = "top-right"
    position: Position | None = None
    size: int = Field(256, ge=64, le=2000, description="Size at 1920px width; scales with video")
    zoom: int = Field(16, ge=1, le=19)
    opacity: float = Field(0.8, ge=0, le=1)
    corner_radius: int = Field(35, ge=0)
    rotate: bool = True


class JourneyMapWidget(BaseModel):
    enabled: bool = False
    corner: Corner = "bottom-right"
    position: Position | None = None
    size: int = Field(256, ge=64, le=2000)
    opacity: float = Field(0.8, ge=0, le=1)
    corner_radius: int = Field(35, ge=0)
    # progressive: the line is drawn as you move | static: whole route visible from the start
    mode: Literal["progressive", "static"] = "progressive"
    # group: one continuous route for the whole output video (e.g. the day) | recording: each recording on its own
    scope: Literal["group", "recording"] = "group"
    # How to join the end of one recording with the start of the next one
    gap_style: Literal["solid", "dashed", "none"] = "solid"
    # >0 shows the part of the route still to come, faded (progressive mode only)
    upcoming_opacity: float = Field(0.0, ge=0, le=1)
    line_color: str = "255,0,0"
    line_width: int = Field(4, ge=1, le=50, description="At 1920px width; scales with video")
    marker_color: str = "0,0,255"
    marker_size: int = Field(6, ge=1, le=50)

    @field_validator("line_color", "marker_color")
    @classmethod
    def _valid_rgb(cls, v: str) -> str:
        return _rgb(v)


class SpeedWidget(BaseModel):
    enabled: bool = False
    corner: Corner = "bottom-left"
    position: Position | None = None
    font_size: int = Field(64, ge=8, le=400)


class OverlayConfig(BaseModel):
    enabled: bool = True
    # generated: built from the widgets below | builtin: library's default layout | xml: your own layout file
    layout_mode: Literal["generated", "builtin", "xml"] = "generated"
    layout_xml_path: str = ""
    margin: int = Field(32, ge=0, description="Distance to the frame edge at 1920px width")
    font: str = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    datetime: DateTimeWidget = DateTimeWidget()
    minimap: MapWidget = MapWidget()
    journey_map: JourneyMapWidget = JourneyMapWidget()
    speed: SpeedWidget = SpeedWidget()
    map_style: str = "osm"
    map_api_key: str = ""
    units_speed: str = "kph"
    gps_dop_max: float = 10
    gps_speed_max: float = 200
    # When a recording has no usable GPS the overlay step fails; fall back to the plain video instead of aborting.
    fallback_without_overlay: bool = True
    extra_args: list[str] = []


class EncodingConfig(BaseModel):
    # Only used when re-encoding (overlay on, or recordings with different formats). Joining alone is lossless.
    codec: Literal["hevc_videotoolbox", "h264_videotoolbox", "prores_videotoolbox", "libx265", "libx264"] = (
        "hevc_videotoolbox"
    )
    rate_control: Literal["quality", "bitrate"] = "quality"
    quality: int = Field(65, ge=1, le=100, description="VideoToolbox -q:v (1-100) or x264/x265 CRF mapping")
    bitrate: str = "60M"
    hw_decode: bool = True
    audio: Literal["copy", "aac"] = "copy"
    # Overlay rendering is single-threaded Python per recording: running several recordings in parallel uses the cores.
    parallel_jobs: int = Field(2, ge=1, le=16)
    ffmpeg_dir: str = ""


class AppConfig(BaseModel):
    source: SourceConfig = SourceConfig()
    grouping: GroupingConfig = GroupingConfig()
    output: OutputConfig = OutputConfig()
    overlay: OverlayConfig = OverlayConfig()
    encoding: EncodingConfig = EncodingConfig()


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if path.exists():
        return AppConfig.model_validate_json(path.read_text())
    return AppConfig()


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))
