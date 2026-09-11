"""Pie-chart stats screens (BY CALLSIGN / BY TYPE), built from flightlog's
local CSV. Pages 3 and 4 of the Left/Right screen cycle. Same background/
box-text/font treatment as aircraft_screen.py (plain map, no radar layers,
25%-opacity boxed VCR OSD Mono text) -- pie sits dead center like the
radar circle, with a two-column legend wrapped around its sides like the
Info HUD's STATS/AIRCRAFT panels wrap the RADAR screen's circle, and a
footer line matching the AIRCRAFT screen's footer-stat style.
"""
import csv
import os
from colorsys import hsv_to_rgb

import pygame

import colors
import config
import flightlog
import radar

FONT_SIZE = 22  # matches aircraft_screen.py/info.py
LINE_HEIGHT = 24  # matches info.py's rhythm -- this screen has far fewer
# lines than the AIRCRAFT table, no need for that screen's tighter 22px
BOX_PAD_X = 4
BOX_PAD_Y = 2
BOX_ALPHA = 64  # 25% opacity, matching every other boxed-text screen
UNDERSCAN_FRACTION = 0.08  # matches aircraft_screen.py/info.py

# Leaves enough safe-area width on both sides for a legend line as long as
# "HELICOPTER 100%" (14 chars, the worst case) without crowding the circle
# -- 720px frame, 8% margin each side, radius 110 leaves ~182px per side,
# comfortably over 14 chars at VCR OSD Mono 22pt's measured 13px/char.
PIE_RADIUS_PX = 110
PIE_ANGLE_STEP_DEG = 3  # arc point density -- same idea as radar.py's ring polyline
OTHER_THRESHOLD_PCT = 3.0  # slices below this fold into one OTHER wedge
OTHER_COLOR = (110, 110, 110)  # neutral grey -- not a real category, so it
# deliberately doesn't get a rainbow hue like the real slices do

SLICE_SATURATION = 0.85
SLICE_VALUE = 1.0


def _callsign_bin(callsign):
    """First 3 characters identify the airline (SWA/AAL/DAL/...); a US
    N-number tail (N followed by a digit -- general aviation, never an
    airline code) is grouped as a single 'N' bucket instead of fragmenting
    into one bucket per tail number. UNKNOWN (no broadcast callsign, see
    aircraft.py) stays its own bucket rather than folding into either."""
    if not callsign or callsign == "UNKNOWN":
        return "UNKNOWN"
    if len(callsign) > 1 and callsign[0] == "N" and callsign[1].isdigit():
        return "N"
    return callsign[:3]


_cache = {"mtime": None, "rows": []}


def _load_rows():
    """Re-parses the local flightlog CSV only when its mtime has changed
    since the last call -- this screen would otherwise re-read/parse a
    file that only grows over weeks/months on every single frame while
    parked here (20fps)."""
    path = flightlog.LOCAL_LOG_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if mtime != _cache["mtime"]:
        try:
            with open(path, newline="") as f:
                _cache["rows"] = list(csv.DictReader(f))
        except OSError:
            _cache["rows"] = []
        _cache["mtime"] = mtime
    return _cache["rows"]


def _bucket(rows, mode):
    """-> (ordered list of (label, count, color) -- real slices sorted
    largest-first, then OTHER last if any were folded in, total_count).
    mode is 'callsign' or 'type'."""
    counts = {}
    for row in rows:
        key = _callsign_bin(row["callsign"]) if mode == "callsign" else row["type"]
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return [], 0

    kept = []
    other_count = 0
    for label, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        if 100 * count / total < OTHER_THRESHOLD_PCT:
            other_count += count
        else:
            kept.append((label, count))

    slices = []
    n = len(kept)
    for i, (label, count) in enumerate(kept):
        hue = i / n if n else 0
        color = tuple(int(c * 255) for c in hsv_to_rgb(hue, SLICE_SATURATION, SLICE_VALUE))
        slices.append((label, count, color))
    if other_count:
        slices.append(("OTHER", other_count, OTHER_COLOR))
    return slices, total


def _draw_pie(surface, slices, total):
    if total == 0:
        return
    center = config.CENTER_PX
    start_angle = 0.0
    for _, count, color in slices:
        end_angle = start_angle + 360.0 * count / total
        points = [center]
        angle = start_angle
        while angle < end_angle:
            points.append(radar._polar_to_px(PIE_RADIUS_PX, angle))
            angle += PIE_ANGLE_STEP_DEG
        points.append(radar._polar_to_px(PIE_RADIUS_PX, end_angle))
        if len(points) >= 3:
            pygame.draw.polygon(surface, color, points)
            pygame.draw.polygon(surface, colors.rgb(colors.BLACK), points, 2)
        start_angle = end_angle


def _draw_boxed_segments(surface, font, segments, pos, align="left"):
    """Same pattern as aircraft_screen.py's helper -- one shared 25%-opacity
    box behind a left-to-right run of (text, color) pairs."""
    rendered = [font.render(text, True, color) for text, color in segments]
    total_w = sum(r.get_width() for r in rendered)
    h = max((r.get_height() for r in rendered), default=0)
    box = pygame.Surface((total_w + BOX_PAD_X * 2, h + BOX_PAD_Y * 2), pygame.SRCALPHA)
    box.fill((*colors.rgb(colors.BLACK), BOX_ALPHA))
    x = BOX_PAD_X
    for r in rendered:
        box.blit(r, (x, BOX_PAD_Y))
        x += r.get_width()

    bx, by = pos
    if align == "right":
        bx -= box.get_width()
    elif align == "center":
        bx -= box.get_width() / 2
    surface.blit(box, (bx, by))
    return box.get_height()


def _draw_boxed_line(surface, font, text, color, pos, align="left"):
    return _draw_boxed_segments(surface, font, [(text, color)], pos, align)


def render_stats_screen(size, mode, color_scheme, font):
    """mode: 'callsign' or 'type'."""
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    rows = _load_rows()
    slices, total = _bucket(rows, mode)

    title = "AIRCRAFT LOG BY CALLSIGN" if mode == "callsign" else "AIRCRAFT LOG BY TYPE"
    y = margin_y + LINE_HEIGHT
    _draw_boxed_line(layer, font, title, label_color, (w / 2, y), align="center")

    if total == 0:
        _draw_boxed_line(layer, font, "NO DATA YET", info_color, (w / 2, h / 2), align="center")
    else:
        _draw_pie(layer, slices, total)
        left_x, right_x = margin_x, w - margin_x
        left_y = right_y = margin_y + LINE_HEIGHT * 3
        for i, (label, count, color) in enumerate(slices):
            pct = 100 * count / total
            text = f"{label} {pct:.0f}%"
            if i % 2 == 0:
                _draw_boxed_line(layer, font, text, color, (left_x, left_y), align="left")
                left_y += LINE_HEIGHT
            else:
                _draw_boxed_line(layer, font, text, color, (right_x, right_y), align="right")
                right_y += LINE_HEIGHT

    footer_y = h - margin_y - LINE_HEIGHT
    _draw_boxed_segments(
        layer, font,
        [("TOTAL AIRCRAFT TRACKED ", label_color), (str(total), info_color)],
        (w / 2, footer_y), align="center",
    )
    return layer
