"""Builds a gopro-dashboard-overlay XML layout from the widget configuration, scaled to the video resolution.

The same geometry (`build_widgets`) feeds both the XML used for rendering and the web preview, so what you
drag in the preview is exactly what gets rendered. Text is measured with PIL and the configured font, the
same way the library draws it.

Widget reference: https://github.com/time4tea/gopro-dashboard-overlay/tree/main/docs/xml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from xml.sax.saxutils import escape, quoteattr

from .config import OverlayConfig, Position

BASE_WIDTH = 1920
# Wide sample values so the box fits any real value of the same format.
SAMPLE_TIME = datetime(2026, 12, 28, 20, 48, 58)
SAMPLE_SPEED = "188"


@lru_cache(maxsize=256)
def _font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return None


def text_metrics(font_path: str, size: int, text: str) -> tuple[int, int, int]:
    """(width, ascent, descent) in pixels, as PIL draws it with anchor 'la' (left, ascender)."""
    font = _font(font_path, size)
    if font is None:
        return round(size * 0.6 * len(text)), round(size * 0.91), round(size * 0.21)
    ascent, descent = font.getmetrics()
    return round(font.getlength(text)), ascent, descent


@dataclass
class Text:
    x: int  # anchor x (left or right edge depending on align)
    y: int  # top (ascender line)
    size: int
    text: str  # sample text for the preview
    align: str
    ascent: int
    xml: str


@dataclass
class Widget:
    key: str  # config key: datetime | minimap | journey_map | speed
    kind: str  # text | moving_map | journey_map
    x: int
    y: int
    w: int
    h: int
    texts: list[Text] = field(default_factory=list)
    xml: str = ""
    props: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "texts": [{k: getattr(t, k) for k in ("x", "y", "size", "text", "align", "ascent")} for t in self.texts],
            "props": self.props,
        }


class _Placer:
    """Places widgets in a corner (stacking them away from the edge) or at a free, dragged position."""

    def __init__(self, width: int, height: int, margin: int, gap: int):
        self.width, self.height, self.margin, self.gap = width, height, margin, gap
        self.used = {"top-left": 0, "top-right": 0, "bottom-left": 0, "bottom-right": 0}

    def place(self, corner: str, position: Position | None, w: int, h: int) -> tuple[int, int]:
        if position is not None:
            x = round(position.x * self.width)
            y = round(position.y * self.height)
            return max(0, min(x, self.width - w)), max(0, min(y, self.height - h))
        offset = self.used[corner]
        x = self.margin if corner.endswith("left") else self.width - self.margin - w
        y = self.margin + offset if corner.startswith("top") else self.height - self.margin - offset - h
        self.used[corner] = offset + h + self.gap
        return x, y


def _attrs(**kwargs) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in kwargs.items())


def build_widgets(overlay: OverlayConfig, width: int, height: int, track_path: str | None = None) -> list[Widget]:
    scale = width / BASE_WIDTH
    s = lambda v: max(1, round(v * scale))  # noqa: E731
    placer = _Placer(width, height, s(overlay.margin), s(16))
    font = overlay.font
    widgets: list[Widget] = []

    dt = overlay.datetime
    if dt.enabled:
        time_size = s(dt.font_size)
        date_size = max(8, round(time_size * 0.55))
        date_text, time_text = SAMPLE_TIME.strftime(dt.date_format), SAMPLE_TIME.strftime(dt.time_format)
        dw, da, _ = text_metrics(font, date_size, date_text)
        tw, ta, td = text_metrics(font, time_size, time_text)
        gap = round(time_size * 0.25)
        w, h = max(dw, tw), date_size + gap + ta + td
        x, y = placer.place(dt.corner, dt.position, w, h)
        align = "right" if dt.corner.endswith("right") else "left"
        ax = x + w if align == "right" else x
        common = dict(align=align, rgb=dt.color, outline=dt.outline, outline_width=max(1, s(2)))
        texts = [
            Text(ax, y, date_size, date_text, align, da,
                 f'<component type="datetime" {_attrs(x=ax, y=y, format=dt.date_format, size=date_size, **common)}/>'),
            Text(ax, y + date_size + gap, time_size, time_text, align, ta,
                 f'<component type="datetime" {_attrs(x=ax, y=y + date_size + gap, format=dt.time_format, size=time_size, **common)}/>'),
        ]
        widgets.append(Widget("datetime", "text", x, y, w, h, texts,
                              props={"color": dt.color, "outline": dt.outline, "outline_width": max(1, s(2))}))

    mm = overlay.minimap
    if mm.enabled:
        size = s(mm.size)
        x, y = placer.place(mm.corner, mm.position, size, size)
        props = dict(size=size, zoom=mm.zoom, corner_radius=s(mm.corner_radius), opacity=mm.opacity, rotate=mm.rotate)
        widgets.append(Widget("minimap", "moving_map", x, y, size, size, props=props,
                              xml=f'<component type="moving_map" name="moving_map" {_attrs(x=x, y=y, **props)}/>'))

    jm = overlay.journey_map
    if jm.enabled:
        size = s(jm.size)
        x, y = placer.place(jm.corner, jm.position, size, size)
        props = dict(size=size, corner_radius=s(jm.corner_radius), opacity=jm.opacity)
        # Our own widget (day_journey_map.py): progressive line over the whole group's track
        extra = dict(
            track=track_path or "", progressive=jm.mode == "progressive", gap_style=jm.gap_style,
            upcoming_opacity=jm.upcoming_opacity, line_rgb=jm.line_color, line_width=s(jm.line_width),
            marker_rgb=jm.marker_color, marker_size=s(jm.marker_size),
        )
        preview_props = {**props, "mode": jm.mode, "upcoming_opacity": jm.upcoming_opacity,
                         "line_color": jm.line_color, "line_width": s(jm.line_width),
                         "marker_color": jm.marker_color, "marker_size": s(jm.marker_size)}
        widgets.append(Widget("journey_map", "journey_map", x, y, size, size, props=preview_props,
                              xml=f'<component type="day_journey_map" name="journey_map" '
                                  f'{_attrs(x=x, y=y, **props, **extra)}/>'))

    sp = overlay.speed
    if sp.enabled:
        value_size = s(sp.font_size)
        unit_size = max(8, round(value_size * 0.3))
        uw, ua, _ = text_metrics(font, unit_size, overlay.units_speed)
        vw, va, vd = text_metrics(font, value_size, SAMPLE_SPEED)
        w, h = max(uw, vw), unit_size + va + vd
        x, y = placer.place(sp.corner, sp.position, w, h)
        align = "right" if sp.corner.endswith("right") else "left"
        ax = x + w if align == "right" else x
        texts = [
            Text(ax, y, unit_size, overlay.units_speed, align, ua,
                 f'<component type="metric_unit" {_attrs(x=ax, y=y, metric="speed", units="speed", size=unit_size, align=align)}>{escape("{:~c}")}</component>'),
            Text(ax, y + unit_size, value_size, SAMPLE_SPEED, align, va,
                 f'<component type="metric" {_attrs(x=ax, y=y + unit_size, metric="speed", units="speed", dp=0, size=value_size, align=align)}/>'),
        ]
        widgets.append(Widget("speed", "text", x, y, w, h, texts,
                              props={"color": "255,255,255", "outline": "0,0,0", "outline_width": 2}))

    return widgets


def widget_xml(widget: Widget) -> str:
    if widget.kind == "text":
        return f'<composite x="0" y="0" name="{widget.key}">\n' + "\n".join(f"  {t.xml}" for t in widget.texts) + "\n</composite>"
    return widget.xml


def generate_layout(overlay: OverlayConfig, width: int, height: int, track_path: str | None = None) -> str:
    body = "\n".join(widget_xml(w) for w in build_widgets(overlay, width, height, track_path))
    return f"<layout>\n{body}\n</layout>\n"
