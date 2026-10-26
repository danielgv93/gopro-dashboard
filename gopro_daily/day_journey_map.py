"""`day_journey_map`: a journey map whose route line grows as the video plays, across all recordings of a day.

gopro-dashboard-overlay's own `journey_map` draws the full route of *one* video from the first frame. This
widget instead reads the shared group track (see track.py) and, on every frame, draws the line up to the
current GPS time. Since every recording of the day uses the same track and the same map framing, the line
continues seamlessly from one recording to the next in the final joined video.

It is registered into the library's XML layout factory at runtime (see dashboard.py); no fork needed.
"""

from __future__ import annotations

import math
from bisect import bisect_right

from PIL import Image, ImageDraw

MIN_SPAN_DEG = 0.002  # avoid absurd zoom when the whole day happens in a few metres


class DayJourneyMap:
    def __init__(self, at, entry, track, renderer, size=256, corner_radius=0, opacity=0.8,
                 line_rgb=(255, 0, 0), line_width=4, marker_rgb=(0, 0, 255), marker_size=6,
                 progressive=True, gap_style="solid", upcoming_opacity=0.0, padding=0.08):
        self.at, self.entry, self.renderer, self.size = at, entry, renderer, size
        self.track = track  # [(unix time, lat, lon, segment)]
        self.times = [p[0] for p in track]
        self.line_rgb, self.line_width = tuple(line_rgb), max(1, line_width)
        self.marker_rgb, self.marker_size = tuple(marker_rgb), marker_size
        self.progressive, self.gap_style = progressive, gap_style
        self.upcoming_opacity, self.padding = upcoming_opacity, padding

        from gopro_overlay.widgets.map import MaybeRoundedBorder

        self.border = MaybeRoundedBorder(size=size, corner_radius=corner_radius, opacity=opacity)
        self.map = None
        self.pixels: list[tuple[float, float]] = []
        self.base: Image.Image | None = None
        self.trail: Image.Image | None = None
        self.drawn = 0  # number of track points already drawn on `trail`

    # ------------------------------------------------------------------------------------------ setup

    def _init_maybe(self) -> None:
        if self.base is not None:
            return
        import gopro_overlay.vendor.geotiler as geotiler

        if self.track:
            lats = [p[1] for p in self.track]
            lons = [p[2] for p in self.track]
            min_lat, max_lat, min_lon, max_lon = min(lats), max(lats), min(lons), max(lons)
        else:
            min_lat = max_lat = min_lon = max_lon = 0.0
        # square-ish extent with padding so the line doesn't touch the border
        span = max(max_lat - min_lat, max_lon - min_lon, MIN_SPAN_DEG) * (1 + 2 * self.padding)
        c_lat, c_lon = (min_lat + max_lat) / 2, (min_lon + max_lon) / 2
        self.map = geotiler.Map(
            extent=(c_lon - span / 2, c_lat - span / 2, c_lon + span / 2, c_lat + span / 2),
            size=(self.size, self.size),
        )
        if self.map.zoom > 18:
            self.map.zoom = 18

        self.pixels = [self.map.rev_geocode((lon, lat)) for _, lat, lon, _ in self.track]
        self.base = self.renderer(self.map).convert("RGBA")

        if self.upcoming_opacity > 0 or not self.progressive:
            # full route: faint preview of what's coming (progressive) or the whole line (static)
            layer = Image.new("RGBA", self.base.size, (0, 0, 0, 0))
            self._draw_segments(ImageDraw.Draw(layer), 0, len(self.pixels))
            if self.progressive:
                alpha = layer.getchannel("A").point(lambda a: int(a * self.upcoming_opacity))
                layer.putalpha(alpha)
            self.base.alpha_composite(layer)
        self.trail = self.base.copy()
        self.drawn = 0 if self.progressive else len(self.pixels)

    # ------------------------------------------------------------------------------------------ drawing

    def _draw_segments(self, draw: ImageDraw.ImageDraw, start: int, end: int) -> None:
        """Draws the line through points [start, end), connecting to point start-1 when start > 0."""
        w, r = self.line_width, self.line_width / 2
        for i in range(max(1, start), end):
            p0, p1 = self.pixels[i - 1], self.pixels[i]
            if self.track[i - 1][3] != self.track[i][3]:  # jump between two recordings
                if self.gap_style == "none":
                    continue
                if self.gap_style == "dashed":
                    self._dashed(draw, p0, p1)
                    continue
            draw.line([p0, p1], fill=self.line_rgb, width=w)
            draw.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill=self.line_rgb)  # round joints
        if start == 0 and end > 0:
            p = self.pixels[0]
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=self.line_rgb)

    def _dashed(self, draw, p0, p1) -> None:
        length = math.dist(p0, p1)
        dash = max(4, self.line_width * 2)
        steps = int(length // dash)
        for k in range(0, steps, 2):
            a, b = k * dash / length, min(1, (k + 1) * dash / length)
            draw.line([(p0[0] + (p1[0] - p0[0]) * a, p0[1] + (p1[1] - p0[1]) * a),
                       (p0[0] + (p1[0] - p0[0]) * b, p0[1] + (p1[1] - p0[1]) * b)],
                      fill=self.line_rgb, width=self.line_width)

    def _position_at(self, t: float, idx: int) -> tuple[float, float] | None:
        """Marker position, interpolated between track points so it moves smoothly at video frame rate."""
        if not self.pixels or idx == 0:
            return None
        if idx >= len(self.pixels):
            return self.pixels[-1]
        t0, t1 = self.times[idx - 1], self.times[idx]
        if self.track[idx - 1][3] != self.track[idx][3] or t1 <= t0:
            return self.pixels[idx - 1]
        f = (t - t0) / (t1 - t0)
        (x0, y0), (x1, y1) = self.pixels[idx - 1], self.pixels[idx]
        return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f

    def draw(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        self._init_maybe()
        entry = self.entry()
        t = entry.dt.timestamp() if entry is not None and entry.dt is not None else float("-inf")
        idx = bisect_right(self.times, t)

        if self.progressive:
            if idx < self.drawn:  # time went backwards (shouldn't happen): redraw from scratch
                self.trail, self.drawn = self.base.copy(), 0
            if idx > self.drawn:
                self._draw_segments(ImageDraw.Draw(self.trail), self.drawn, idx)
                self.drawn = idx

        frame = self.trail.copy()
        fdraw = ImageDraw.Draw(frame)
        pos = self._position_at(t, idx)
        if pos is not None:
            if self.progressive and 0 < idx < len(self.pixels):  # close the gap between last point and marker
                fdraw.line([self.pixels[idx - 1], pos], fill=self.line_rgb, width=self.line_width)
            s = self.marker_size
            fdraw.ellipse([pos[0] - s, pos[1] - s, pos[0] + s, pos[1] + s], fill=self.marker_rgb, outline=(0, 0, 0))
        image.alpha_composite(self.border.rounded(frame), (self.at.x, self.at.y))


def register() -> None:
    """Adds `<component type="day_journey_map" .../>` to gopro-dashboard-overlay's XML layouts."""
    from gopro_overlay import layout_xml
    from gopro_overlay.layout_xml import at, attrib, battrib, fattrib, iattrib, rgbattr

    from .track import load_track

    def create_day_journey_map(self, element, entry, **kwargs):
        return DayJourneyMap(
            at=at(element),
            entry=entry,
            track=load_track(attrib(element, "track")),
            renderer=self.renderer,
            size=iattrib(element, "size", d=256),
            corner_radius=iattrib(element, "corner_radius", d=0),
            opacity=fattrib(element, "opacity", d=0.8),
            line_rgb=rgbattr(element, "line_rgb", d=(255, 0, 0)),
            line_width=iattrib(element, "line_width", d=4),
            marker_rgb=rgbattr(element, "marker_rgb", d=(0, 0, 255)),
            marker_size=iattrib(element, "marker_size", d=6),
            progressive=battrib(element, "progressive", d=True),
            gap_style=attrib(element, "gap_style", d="solid"),
            upcoming_opacity=fattrib(element, "upcoming_opacity", d=0.0),
        )

    layout_xml.Widgets.create_day_journey_map = create_day_journey_map
