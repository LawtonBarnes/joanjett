"""AIRCRAFT screen -- full data table (CALLSIGN/ALT/SPD/DIST/TRK) of every
tracked aircraft, plus a status footer. One of the screens cycled via
Left/Right (Radar / Aircraft / Settings, per user's confirmed 3-screen
plan). Per user request 2026-08-25: VCR OSD Mono font, yellow-label/
white-data color scheme, drawn over the plain blue map with the compass,
radar rings/sweep, and plane blips/trails all turned off -- "the mockup
has the wrong font ... over the blue map with all the radar layers turned
off."
"""
import pygame

import colors

FONT_SIZE = 22
LINE_HEIGHT = 22  # tighter than info.py's 24 -- a 10-row table + footer
# needs to fit in the vertical budget the 8% underscan leaves
GAP = 8
BOX_PAD_X = 4
BOX_PAD_Y = 2
BOX_ALPHA = 64  # 25% opacity, matching the Info HUD's boxes
UNDERSCAN_FRACTION = 0.08  # matches info.py

MAX_ROWS = 8  # user request 2026-08-25 -- was unbounded (up to MAX_TRACKED=10)

COLUMNS = [
    ("CALLSIGN", 9, "left"),
    ("ALT", 7, "right"),
    ("SPD", 6, "right"),
    ("DIST", 7, "right"),
    ("TRK", 6, "right"),
]


def _format_row(values):
    parts = []
    for (_, width, align), value in zip(COLUMNS, values):
        s = str(value)
        parts.append(s.rjust(width) if align == "right" else s.ljust(width))
    return "".join(parts)


def _draw_boxed_segments(surface, font, segments, pos, align="left"):
    """segments: [(text, color), ...] drawn left-to-right, one shared box
    behind the whole line (same pattern as info.py's helper)."""
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


def render_aircraft_screen(size, live_aircraft, fetch_ok, settings, range_multiplier, sweep_angle_deg, scheme, font):
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    y = margin_y
    y += _draw_boxed_line(layer, font, "AIRCRAFT DATA", label_color, (w / 2, y), align="center")
    y += GAP

    header_text = _format_row([label for label, _, _ in COLUMNS])
    table_width = font.size(header_text)[0]
    table_x = max(margin_x, (w - table_width) / 2)
    y += _draw_boxed_line(layer, font, header_text, label_color, (table_x, y), align="left")
    y += LINE_HEIGHT

    for ac in (live_aircraft or [])[:MAX_ROWS]:
        alt = ac.get("alt_baro")
        gs = ac.get("gs")
        row_text = _format_row(
            [
                ac["callsign"],
                alt if alt is not None else "N/A",
                round(gs) if gs is not None else "N/A",
                f"{ac['dist_nm']:.1f}",
                f"{round(ac['heading_deg'])}°",
            ]
        )
        y += _draw_boxed_line(layer, font, row_text, info_color, (table_x, y), align="left")

    y += GAP
    range_nm = range_multiplier * 4
    countdown = max(0.0, settings.interval_sec * (1 - sweep_angle_deg / 360.0))
    mil_count = sum(1 for a in live_aircraft if a.get("military")) if live_aircraft else 0
    footer_stats = [
        ("STATUS: ", "ACTIVE" if fetch_ok else "NO DATA"),
        ("CONTACTS: ", f"{len(live_aircraft) if live_aircraft else 0} ({mil_count} MIL)"),
        ("RANGE: ", f"{range_nm}NM"),
        ("INTERVAL: ", f"{int(settings.interval_sec)}S"),
        ("NEXT UPDATE: ", f"{countdown:02.0f}S"),
    ]
    for label, value in footer_stats:
        y += _draw_boxed_segments(
            layer, font, [(label, label_color), (value, info_color)], (margin_x, y), align="left"
        )

    return layer
