"""INTERNATIONAL FLIGHTS / MAJOR DOMESTIC AIRLINES stats screens -- built
from flightlog's local CSV, cross-referenced against the 123atc.com
3-letter-ID/callsign/country lookup table (data/callsigns_with_country.csv)
to resolve each tracked callsign's airline name and country. Same plain-map/
no-radar-layers/yellow-label-white-data/25%-opacity-boxed VCR OSD Mono
treatment as aircraft_screen.py and stats_screen.py -- pages 5 and 6 of the
Left/Right screen cycle, added 2026-09-12.
"""
import csv
import os
from pathlib import Path

import pygame

import colors
import flightlog

FONT_SIZE = 22  # matches aircraft_screen.py -- reuses main.py's aircraft_screen_font
LINE_HEIGHT = 22  # matches aircraft_screen.py's tight rhythm, needed to fit 12 rows
BOX_PAD_X = 4
BOX_PAD_Y = 2
BOX_ALPHA = 64  # 25% opacity, matching every other boxed-text screen
UNDERSCAN_FRACTION = 0.08  # matches aircraft_screen.py

MAX_ROWS = 12  # user request 2026-09-12

SCRIPT_DIR = Path(__file__).resolve().parent
LOOKUP_PATH = SCRIPT_DIR / "data" / "callsigns_with_country.csv"

DOMESTIC_COUNTRY = "United States"

# AIRLINE gets more room on the domestic table since it doesn't need to
# share the line with a COUNTRY column.
AIRLINE_WIDTH_INTL = 20
COUNTRY_WIDTH = 14
AIRLINE_WIDTH_DOMESTIC = 30

INTL_COLUMNS = [
    ("SGN", 3, "left"),
    ("AIRLINE", AIRLINE_WIDTH_INTL, "left"),
    ("COUNTRY", COUNTRY_WIDTH, "left"),
    ("NUM", 4, "right"),
]
DOMESTIC_COLUMNS = [
    ("SGN", 3, "left"),
    ("AIRLINE", AIRLINE_WIDTH_DOMESTIC, "left"),
    ("NUM", 4, "right"),
]


def _truncate(s, width):
    return s if len(s) <= width else s[:width]


def _format_row(columns, values):
    """One space between each fixed-width column, per user's explicit
    request -- unlike aircraft_screen.py's table, which relies purely on
    column padding for spacing."""
    parts = []
    for (_, width, align), value in zip(columns, values):
        s = _truncate(str(value), width)
        parts.append(s.rjust(width) if align == "right" else s.ljust(width))
    return " ".join(parts)


_lookup_cache = {"mtime": None, "table": {}}


def _load_lookup():
    """sgn -> (airline, country), re-read only when the CSV's mtime changes
    -- same caching pattern as stats_screen._load_rows. This particular file
    is a static reference table, not a live-growing log, but staying
    consistent costs nothing and is safe against a future in-place edit."""
    try:
        mtime = os.path.getmtime(LOOKUP_PATH)
    except OSError:
        return {}
    if mtime != _lookup_cache["mtime"]:
        table = {}
        try:
            with open(LOOKUP_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    table[row["3-Letter ID"]] = (row["Call Sign"], row["Country"])
        except OSError:
            table = {}
        _lookup_cache["table"] = table
        _lookup_cache["mtime"] = mtime
    return _lookup_cache["table"]


_flight_cache = {"mtime": None, "rows": []}


def _load_flight_rows():
    """Same mtime-gated re-parse pattern as stats_screen._load_rows."""
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


def _bucket(mode):
    """-> top MAX_ROWS list of (sgn, airline, country, count), sorted
    descending by count. mode: 'international' or 'domestic'. Only
    callsigns whose first 3 characters match a known airline 3-letter ID
    (from the lookup table) count as an airline at all -- general-aviation
    N-number tails and generic ATC callsigns (TOWER/APPROACH/etc, which
    carry no real country on the source site) aren't airlines and don't
    belong on either list."""
    lookup = _load_lookup()
    rows = _load_flight_rows()
    counts = {}
    for row in rows:
        sgn = (row.get("callsign") or "")[:3]
        hit = lookup.get(sgn)
        if not hit:
            continue
        airline, country = hit
        if country == "(None)":
            continue
        is_domestic = country == DOMESTIC_COUNTRY
        if mode == "domestic" and not is_domestic:
            continue
        if mode == "international" and is_domestic:
            continue
        key = (sgn, airline.upper(), country.upper())
        counts[key] = counts.get(key, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:MAX_ROWS]
    return [(sgn, airline, country, count) for (sgn, airline, country), count in ranked]


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


def render_airline_screen(size, mode, color_scheme, font):
    """mode: 'international' or 'domestic'."""
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    columns = INTL_COLUMNS if mode == "international" else DOMESTIC_COLUMNS
    title = "INTERNATIONAL FLIGHTS" if mode == "international" else "MAJOR DOMESTIC AIRLINES"
    ranked = _bucket(mode)

    y = margin_y
    y += LINE_HEIGHT  # skip a line, matches aircraft_screen.py's title spacing
    y += _draw_boxed_line(layer, font, title, label_color, (w / 2, y), align="center")
    y += LINE_HEIGHT  # skip 1 line

    header_text = _format_row(columns, [label for label, _, _ in columns])
    table_width = font.size(header_text)[0]
    table_x = max(margin_x, (w - table_width) / 2)
    y += _draw_boxed_line(layer, font, header_text, label_color, (table_x, y), align="left")

    if not ranked:
        y += LINE_HEIGHT
        _draw_boxed_line(layer, font, "NO DATA YET", info_color, (w / 2, y), align="center")
    else:
        for sgn, airline, country, count in ranked:
            values = [sgn, airline, country, count] if mode == "international" else [sgn, airline, count]
            row_text = _format_row(columns, values)
            y += _draw_boxed_line(layer, font, row_text, info_color, (table_x, y), align="left")

    return layer
