"""AIRCRAFT BY CATEGORY -- a fixed reference table (CODE/LABEL/MAX WEIGHT/
NUM) of the ADS-B emitter categories in aircraft.CATEGORY_LABELS, each row's
NUM cross-referenced against flightlog's local CSV. Replaces the two pie-
chart screens (stats_screen.py, removed 2026-09-12 per user request -- "I
like the tables better than the pie charts"). Same plain-map/no-radar-
layers/yellow-label-white-data/25%-opacity-boxed VCR OSD Mono treatment as
aircraft_screen.py/airline_screen.py.

Deliberately shown in fixed taxonomy order, not sorted by count like the
Aircraft/Airline tables -- this is a legend of what the categories *mean*,
not a ranking. UNKNOWN (A0/B0/C0/C6/C7) and RESERVED (B5) codes are omitted
-- they carry no real weight-class meaning, matching the descriptions the
user actually asked for. C4/C5 (also labeled OBSTACLE, same as C3) are
folded into C3's single "towers, structures" row rather than getting
duplicate rows, per the exact 16-entry list given.
"""
import csv
import os

import pygame

import colors
import flightlog

FONT_SIZE = 22  # matches aircraft_screen.py -- reuses main.py's aircraft_screen_font
BOX_PAD_X = 4
# Measured against the real font (20px intrinsic height at 22pt): 18 boxed
# lines (title + header + 16 data rows) at pad=1 -> 22px/row -> 396px,
# fitting the 403.2px usable height (480 frame, 8% underscan). No blank
# "skip" spacer lines like aircraft_screen.py/airline_screen.py use --
# there isn't vertical room to spare with this many rows.
BOX_PAD_Y = 1
BOX_ALPHA = 64  # 25% opacity, matching every other boxed-text screen
UNDERSCAN_FRACTION = 0.08  # matches aircraft_screen.py

# (code, label, max weight / description). Order and wording per user
# request 2026-09-12, except B4's description shortened ("Ultralight,
# hang-gliders" vs. the fuller "Ultralight aircraft, hang-gliders") to fit
# the MAX WEIGHT column width measured against the real font below.
CATEGORY_TABLE = [
    ("A1", "LIGHT", "< 15,500 lbs"),
    ("A2", "SMALL", "15,500 - 75,000 lbs"),
    ("A3", "LARGE", "75,000 - 300,000 lbs"),
    ("A4", "HI VORTEX", "unusual wake turbulence"),
    ("A5", "HEAVY", "> 300,000 lbs"),
    ("A6", "HIGH SPEED", "> 5g acceleration"),
    ("A7", "HELICOPTER", "helicopters, tilt-rotors"),
    ("B1", "GLIDER", "Gliders, sailplanes"),
    ("B2", "BLIMP", "Airships, blimps, balloons"),
    ("B3", "PARACHUTE", "Skydivers, parachutists"),
    ("B4", "ULTRALIGHT", "Ultralight, hang-gliders"),
    ("B6", "DRONE", "UAV, Drones"),
    ("B7", "ROCKET", "Space vehicles"),
    ("C1", "EMER VEH", "airport vehicle"),
    ("C2", "SERV VEH", "airport vehicle"),
    ("C3", "OBSTACLE", "towers, structures"),
]

# Widths measured against the real VCR OSD Mono 22pt font (13px/char) --
# total row = 2+1+10+1+27+1+4 = 46 chars = 598px, fits the 604.8px usable
# width (720 frame, 8% underscan each side) with a little to spare.
COLUMNS = [
    ("CD", 2, "left"),
    ("LABEL", 10, "left"),
    ("MAX WEIGHT", 27, "left"),
    ("NUM", 4, "right"),
]


def _truncate(s, width):
    return s if len(s) <= width else s[:width]


def _format_row(values):
    """One space between each fixed-width column, matching airline_screen.py."""
    parts = []
    for (_, width, align), value in zip(COLUMNS, values):
        s = _truncate(str(value), width)
        parts.append(s.rjust(width) if align == "right" else s.ljust(width))
    return " ".join(parts)


_flight_cache = {"mtime": None, "rows": []}


def _load_flight_rows():
    """Same mtime-gated re-parse pattern as stats_screen._load_rows used."""
    path = flightlog.LOCAL_LOG_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if mtime != _flight_cache["mtime"]:
        try:
            with open(path, newline="") as f:
                _flight_cache["rows"] = list(csv.DictReader(f))
        except OSError:
            _flight_cache["rows"] = []
        _flight_cache["mtime"] = mtime
    return _flight_cache["rows"]


def _counts_by_label():
    counts = {}
    for row in _load_flight_rows():
        label = row.get("type")
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts


def _draw_boxed_segments(surface, font, segments, pos, align="left"):
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


def render_category_screen(size, color_scheme, font):
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    counts = _counts_by_label()

    y = margin_y
    y += _draw_boxed_line(layer, font, "AIRCRAFT BY CATEGORY", label_color, (w / 2, y), align="center")

    header_text = _format_row([label for label, _, _ in COLUMNS])
    table_width = font.size(header_text)[0]
    table_x = max(margin_x, (w - table_width) / 2)
    y += _draw_boxed_line(layer, font, header_text, label_color, (table_x, y), align="left")

    for code, label, desc in CATEGORY_TABLE:
        row_text = _format_row([code, label, desc, counts.get(label, 0)])
        y += _draw_boxed_line(layer, font, row_text, info_color, (table_x, y), align="left")

    return layer
