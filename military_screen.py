"""MILITARY FLIGHTS stats screen -- every logged pass readsb flagged as
military (flightlog's military column, dbFlags bit 0), counted by aircraft
type (flightlog's model column, cross-referenced against
data/aircraft_types.csv for a readable name), plus the most recent military
passes. Same plain-map/no-radar-layers/yellow-label-white-data/
25%-opacity-boxed VCR OSD Mono treatment as category_screen.py and
airline_screen.py -- last page of the Left/Right screen cycle, added
2026-10-27.

Only rows from 2026-10-27 on can count: readsb wasn't loading its aircraft
database before then, so every earlier pass was logged military=N whether
it was or not. The footer's SINCE date is the first row with a real model
value, i.e. when this data actually started being captured.
"""
import csv
import os
from pathlib import Path

import pygame

import colors
import flightlog

FONT_SIZE = 22  # matches aircraft_screen.py -- reuses main.py's aircraft_screen_font
LINE_HEIGHT = 22  # matches category_screen.py's tight rhythm
BOX_PAD_X = 4
BOX_PAD_Y = 2
BOX_ALPHA = 64  # 25% opacity, matching every other boxed-text screen
UNDERSCAN_FRACTION = 0.08  # matches aircraft_screen.py

# Two tables share the page: worst case 1 title + 1 blank + (1 header +
# TYPE_ROWS) + 1 blank + (1 header + RECENT_ROWS) + 1 blank + 1 footer =
# 17 lines, the same height category_screen.py's worst case was checked at.
TYPE_ROWS = 6
RECENT_ROWS = 4

SCRIPT_DIR = Path(__file__).resolve().parent
TYPE_NAMES_PATH = SCRIPT_DIR / "data" / "aircraft_types.csv"

NO_MODEL = "N/A"
UNKNOWN_TYPE_NAME = "UNKNOWN TYPE"

TYPE_COLUMNS = [
    ("TYPE", 4, "left"),
    ("AIRCRAFT", 30, "left"),
    ("NUM", 5, "right"),
]
RECENT_COLUMNS = [
    ("DATE", 5, "left"),
    ("TIME", 5, "left"),
    ("CALLSIGN", 8, "left"),
    ("TYPE", 4, "left"),
    ("ALT", 6, "right"),
]


def _truncate(s, width):
    """Cuts at the last word boundary that fits ("GENERAL DYNAMICS F-16
    FIGHTING FALCON" -> "GENERAL DYNAMICS F-16 FIGHTING"), falling back to
    a hard cut only for a single word longer than the column."""
    if len(s) <= width:
        return s
    cut = s[:width + 1].rfind(" ")
    return s[:cut] if cut > 0 else s[:width]


def _format_row(columns, values):
    """One space between each fixed-width column, same as airline_screen.py."""
    parts = []
    for (_, width, align), value in zip(columns, values):
        s = _truncate(str(value), width)
        parts.append(s.rjust(width) if align == "right" else s.ljust(width))
    return " ".join(parts)


_names_cache = {"mtime": None, "table": {}}


def _load_type_names():
    """type -> name, re-read only when the CSV's mtime changes -- same
    caching pattern as airline_screen._load_lookup."""
    try:
        mtime = os.path.getmtime(TYPE_NAMES_PATH)
    except OSError:
        return {}
    if mtime != _names_cache["mtime"]:
        table = {}
        try:
            with open(TYPE_NAMES_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    table[row["type"]] = row["name"]
        except OSError:
            table = {}
        _names_cache["table"] = table
        _names_cache["mtime"] = mtime
    return _names_cache["table"]


_flight_cache = {"mtime": None, "rows": []}


def _load_flight_rows():
    """Same mtime-gated re-parse pattern as airline_screen._load_flight_rows."""
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


def _military_rows():
    """Military passes, oldest first (the log is already date/time sorted
    after every sync; sorted again here anyway since rows appended live
    between syncs aren't guaranteed to land in order)."""
    rows = [r for r in _load_flight_rows() if r.get("military") == "Y" and r.get("date") and r.get("time")]
    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


def _model(row):
    return row.get("model") or NO_MODEL


def _ranked_types(rows):
    """-> up to TYPE_ROWS (code, name, count), descending by count. A
    military pass with no database type still counts, under "----"."""
    names = _load_type_names()
    counts = {}
    for row in rows:
        code = _model(row)
        counts[code] = counts.get(code, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:TYPE_ROWS]
    out = []
    for code, count in ranked:
        if code == NO_MODEL:
            out.append(("----", UNKNOWN_TYPE_NAME, count))
        else:
            out.append((code, names.get(code, code), count))
    return out


def _recent(rows):
    """-> up to RECENT_ROWS row values, newest first."""
    out = []
    for row in reversed(rows[-RECENT_ROWS:]):
        _, mm, dd = row["date"].split("-")
        code = _model(row)
        out.append([
            f"{mm}/{dd}",
            row["time"][:5],
            row.get("callsign") or "",
            "----" if code == NO_MODEL else code,
            row.get("altitude_ft") or "N/A",
        ])
    return out


def _since_date():
    """MM/DD/YYYY of the first row with a real model value (when military
    detection actually started working), or None if there isn't one yet."""
    dates = [r["date"] for r in _load_flight_rows() if r.get("date") and r.get("model") not in (None, "", NO_MODEL)]
    if not dates:
        return None
    yyyy, mm, dd = min(dates).split("-")
    return f"{mm}/{dd}/{yyyy}"


def _draw_boxed_segments(surface, font, segments, pos, align="left"):
    """Same pattern as category_screen.py's helper -- one shared 25%-opacity
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


def _draw_table(layer, font, columns, value_rows, y, w, margin_x, label_color, info_color):
    header_text = _format_row(columns, [label for label, _, _ in columns])
    table_width = font.size(header_text)[0]
    table_x = max(margin_x, (w - table_width) / 2)
    y += _draw_boxed_line(layer, font, header_text, label_color, (table_x, y), align="left")
    for values in value_rows:
        y += _draw_boxed_line(layer, font, _format_row(columns, values), info_color, (table_x, y), align="left")
    return y


def render_military_screen(size, color_scheme, font):
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    rows = _military_rows()

    y = margin_y
    y += _draw_boxed_line(layer, font, "MILITARY FLIGHTS", label_color, (w / 2, y), align="center")
    y += LINE_HEIGHT  # skip a line between title and header, same as category_screen.py

    if not rows:
        y += LINE_HEIGHT
        y += _draw_boxed_line(layer, font, "NO MILITARY FLIGHTS YET", info_color, (w / 2, y), align="center")
    else:
        y = _draw_table(layer, font, TYPE_COLUMNS, _ranked_types(rows), y, w, margin_x, label_color, info_color)
        y += LINE_HEIGHT
        y = _draw_table(layer, font, RECENT_COLUMNS, _recent(rows), y, w, margin_x, label_color, info_color)

    # Footer -- same TOTAL ... SINCE shape as category_screen.py's.
    y += LINE_HEIGHT
    since = _since_date()
    segments = [("TOTAL MILITARY ", label_color), (f"{len(rows)} ", info_color)]
    if since:
        segments += [("SINCE ", label_color), (since, info_color)]
    _draw_boxed_segments(layer, font, segments, (w / 2, y), align="center")

    return layer
