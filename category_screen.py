"""AIRCRAFT BY CATEGORY -- a table (CODE/LABEL/MAX WEIGHT/NUM) of the
ADS-B emitter categories in aircraft.CATEGORY_LABELS, each row's NUM
cross-referenced against flightlog's local CSV. Replaces the two pie-
chart screens (stats_screen.py, removed 2026-09-12 per user request -- "I
like the tables better than the pie charts"). Same plain-map/no-radar-
layers/yellow-label-white-data/25%-opacity-boxed VCR OSD Mono treatment as
aircraft_screen.py/airline_screen.py.

Sorted descending by NUM and capped at MAX_ROWS, same ranking convention
as the Aircraft/Airline tables (changed 2026-09-12 from the original fixed
taxonomy order, per user follow-up request). Categories with 0 detections
are skipped entirely. UNKNOWN (A0/B0/C0/C6/C7) and RESERVED (B5) codes are
omitted from CATEGORY_TABLE altogether -- they carry no real weight-class
meaning, matching the descriptions the user actually asked for. C4/C5
(also labeled OBSTACLE, same as C3) are folded into C3's single "TOWERS,
STRUCTURES" row rather than getting duplicate rows, per the exact
16-entry list given.
"""
import csv
import os

import pygame

import colors
import flightlog

FONT_SIZE = 22  # matches aircraft_screen.py -- reuses main.py's aircraft_screen_font
LINE_HEIGHT = 22  # matches airline_screen.py -- used only for the title/header skip
BOX_PAD_X = 4
BOX_PAD_Y = 2  # matches aircraft_screen.py/airline_screen.py -- MAX_ROWS=12 leaves
# enough vertical room now that 0-count rows are skipped, no need for the
# tighter pad=1 the original all-16-rows version needed.
BOX_ALPHA = 64  # 25% opacity, matching every other boxed-text screen
UNDERSCAN_FRACTION = 0.08  # matches aircraft_screen.py

MAX_ROWS = 12  # user request 2026-09-12

# (code, label, max weight / description). Labels/descriptions shortened
# again 2026-09-12 per user request for terser abbreviations across the
# app (A7's label changed HELICOPTER -> ROTOR here too, see the matching
# rename in aircraft.CATEGORY_LABELS -- flightlog.csv rows logged under
# the old "HELICOPTER" text were migrated to "ROTOR" at the same time so
# _counts_by_label()'s exact-string match keeps counting them).
CATEGORY_TABLE = [
    ("A1", "LIGHT", "<15.5K LBS"),
    ("A2", "SMALL", "15-75K LBS"),
    ("A3", "LARGE", "75-300K LBS"),
    ("A4", "HI VORTEX", "HI WAKE TURB"),
    ("A5", "HEAVY", ">300K LBS"),
    ("A6", "HIGH SPEED", ">5G ACCEL"),
    ("A7", "ROTOR", "HELICOPTER"),
    ("B1", "GLIDER", "OR SAILPLANE"),
    ("B2", "BLIMP", "OR HOT AIR BAL"),
    ("B3", "PARACHUTE", "SKYDIVERS"),
    ("B4", "ULTRALIGHT", "OR HANG GLIDER"),
    ("B6", "DRONE", "OR UAV"),
    ("B7", "ROCKET", "SPACECRAFT"),
    ("C1", "EMER VEH", "AIRPORT SURF"),
    ("C2", "SERV VEH", "AIRPORT SURF"),
    ("C3", "OBSTACLE", "STRUCTURE"),
]

# Calculated, not hand-measured, so a future edit to CATEGORY_TABLE's
# descriptions can't silently drift out of sync with a stale literal --
# whatever the longest desc string is, that's the column width (currently
# 14, from "OR HOT AIR BAL"/"OR HANG GLIDER"). NUM is fixed at 5 digits
# (not calculated) per user request 2026-09-12, since real counts are
# still 2-3 digits today but should have headroom for 5 once a lot more
# data has been collected, rather than needing another width bump later.
MAX_WEIGHT_WIDTH = max(len(desc) for _, _, desc in CATEGORY_TABLE)

COLUMNS = [
    ("CD", 2, "left"),
    ("TYPE", 10, "left"),
    ("MAX WEIGHT", MAX_WEIGHT_WIDTH, "left"),
    ("NUM", 5, "right"),
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


def _ranked_rows():
    """-> up to MAX_ROWS (code, label, desc, count) tuples, descending by
    count, 0-count categories dropped entirely."""
    counts = _counts_by_label()
    rows = [
        (code, label, desc, counts.get(label, 0))
        for code, label, desc in CATEGORY_TABLE
        if counts.get(label, 0) > 0
    ]
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows[:MAX_ROWS]


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

    ranked = _ranked_rows()

    y = margin_y
    y += _draw_boxed_line(layer, font, "AIRCRAFT BY CATEGORY", label_color, (w / 2, y), align="center")
    y += LINE_HEIGHT  # skip a line between title and header, per user request

    header_text = _format_row([label for label, _, _ in COLUMNS])
    table_width = font.size(header_text)[0]
    table_x = max(margin_x, (w - table_width) / 2)
    y += _draw_boxed_line(layer, font, header_text, label_color, (table_x, y), align="left")

    if not ranked:
        y += LINE_HEIGHT
        _draw_boxed_line(layer, font, "NO DATA YET", info_color, (w / 2, y), align="center")
    else:
        for code, label, desc, count in ranked:
            row_text = _format_row([code, label, desc, count])
            y += _draw_boxed_line(layer, font, row_text, info_color, (table_x, y), align="left")

    return layer
