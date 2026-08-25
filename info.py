"""Info HUD -- top LOCATION line, left STATS panel, right AIRCRAFT panel,
bottom NEARBY line. Every line gets the same opaque-black backdrop box
already used for compass/callsign/ring labels (user request 2026-08-25:
"keep using the black box behind text, it helps keep the letters from
stepping on one another") -- here each line is a LABEL-colored (yellow)
plus INFO-colored (white) pair of segments sharing one box, matching the
two-color look in the original mockup.
"""
import pygame

import colors
import config
import geo

FONT_SIZE = 22  # spec: "Font Size: 22 point"
LINE_HEIGHT = 24  # spec: "22 point Leading" -- line-to-line spacing close to the font size itself
BOX_PAD_X = 4
BOX_PAD_Y = 2
BOX_ALPHA = 64  # 25% opacity (2026-08-25, was 128/50%) -- Info HUD only,
# unlike the fully-opaque boxes behind compass/callsign/ring labels

# Virtual 10% underscan (user request 2026-08-25: text was running off the
# edges of the CRT's bezel) -- HUD text is inset this fraction of the frame
# size from each edge, independent of any real underscan/overscan setting
# in config.txt. Only the text overlay is inset this way; the map/radar/
# planes layers still use the full frame.
UNDERSCAN_FRACTION = 0.08  # was 0.10 (2026-08-25, "a tad" smaller)


def _draw_boxed_segments(surface, font, segments, pos, align="left"):
    """segments: [(text, color), ...] drawn left-to-right on one line, with
    a single black box behind the whole combined line (not one box per
    segment -- a label+value pair reads as one unit)."""
    rendered = [font.render(text, True, color) for text, color in segments]
    total_w = sum(r.get_width() for r in rendered)
    h = max((r.get_height() for r in rendered), default=0)
    box = pygame.Surface((total_w + BOX_PAD_X * 2, h + BOX_PAD_Y * 2), pygame.SRCALPHA)
    black = colors.rgb(colors.BLACK)
    box.fill((*black, BOX_ALPHA))
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


def _format_location_line(settings):
    lat_s = geo.decimal_to_dms(settings.location_lat, "N", "S")
    lon_s = geo.decimal_to_dms(settings.location_lon, "E", "W")
    return f"{settings.location_name} - {lat_s} {lon_s}"


def _panel_line_count(items):
    """items: ("stat", label, value) = 2 lines, ("blank",) = 1 line,
    ("header", [line, ...]) = len(lines)."""
    n = 0
    for item in items:
        if item[0] == "stat":
            n += 2
        elif item[0] == "blank":
            n += 1
        elif item[0] == "header":
            n += len(item[1])
    return n


def _draw_panel(surface, font, items, x, top_y, label_color, info_color, align):
    y = top_y
    for item in items:
        if item[0] == "stat":
            _, label, value = item
            y += _draw_boxed_segments(surface, font, [(label, label_color)], (x, y), align=align)
            y += _draw_boxed_segments(surface, font, [(value, info_color)], (x, y), align=align)
        elif item[0] == "blank":
            y += LINE_HEIGHT
        elif item[0] == "header":
            for line in item[1]:
                y += _draw_boxed_segments(surface, font, [(line, label_color)], (x, y), align=align)


def render_info(size, settings, range_multiplier, sweep_angle_deg, live_aircraft, fetch_ok, scheme, font):
    layer = pygame.Surface(size, pygame.SRCALPHA)
    label_color = colors.rgb(colors.COLOR_SCHEMES[scheme]["LABEL"])
    info_color = colors.rgb(colors.COLOR_SCHEMES[scheme]["INFO"])
    w, h = size
    margin_x = w * UNDERSCAN_FRACTION
    margin_y = h * UNDERSCAN_FRACTION

    # Top line: LOCATION -- orange (not the scheme's LABEL color), per user
    # request 2026-08-25. Single color, per mockup, not a label/value pair.
    header_color = colors.rgb(colors.ORANGE)
    _draw_boxed_segments(
        layer, font, [(_format_location_line(settings), header_color)], (w / 2, margin_y), align="center"
    )

    # Left STATS panel -- label and value stacked on separate lines (not
    # side by side), per user request 2026-08-25 ("Yellow labels and white
    # data stacked so the radar would have more real estate") -- each stat
    # is narrower this way (only as wide as its longer line), leaving more
    # horizontal room for the radar circle in the middle.
    range_nm = range_multiplier * 4
    countdown = max(0.0, settings.interval_sec * (1 - sweep_angle_deg / 360.0))
    mil_count = sum(1 for a in live_aircraft if a.get("military")) if live_aircraft else 0

    stats_items = [
        ("stat", f"ATC ({settings.atc_icao})", settings.atc_freq),
        ("blank",),
        ("stat", "STATUS", "ACTIVE" if fetch_ok else "NO DATA"),
        ("stat", "COUNT", f"{len(live_aircraft) if live_aircraft else 0} ({mil_count} MIL)"),
        ("stat", "RANGE", f"{range_nm}NM"),
        ("stat", "INTVL", f"{int(settings.interval_sec)}S"),
        ("stat", "NXT UPD", f"{countdown:02.0f}S"),
    ]

    # Right AIRCRAFT panel -- "NEAREST"/"AIRCRAFT" stacked as two lines (per
    # user follow-up 2026-08-25, matching the original mockup) + blank line,
    # then stacked label/value pairs same as STATS.
    nearest = live_aircraft[0] if live_aircraft else None
    if nearest:
        alt = nearest.get("alt_baro")
        gs = nearest.get("gs")
        aircraft_stats = [
            ("CALLSIGN", nearest["callsign"]),
            ("ALT", f"{alt}" if alt is not None else "N/A"),
            ("SPEED", f"{round(gs)}" if gs is not None else "N/A"),
            ("DIST", f"{nearest['dist_nm']:.1f}"),
            ("HDG", f"{round(nearest['heading_deg'])}°"),
        ]
    else:
        aircraft_stats = [("AIRCRAFT", "NONE")]
    aircraft_items = [("header", ["NEAREST", "AIRCRAFT"]), ("blank",)] + [
        ("stat", label, value) for label, value in aircraft_stats
    ]

    # Vertically center each side panel independently, equidistant from the
    # header (LOCATION line) and footer (NEARBY line) -- per user request
    # 2026-08-25 -- rather than a fixed top-anchored offset like before.
    header_bottom = margin_y + LINE_HEIGHT
    footer_top = h - margin_y - LINE_HEIGHT
    available_height = footer_top - header_bottom

    stats_top = header_bottom + (available_height - _panel_line_count(stats_items) * LINE_HEIGHT) / 2
    aircraft_top = header_bottom + (available_height - _panel_line_count(aircraft_items) * LINE_HEIGHT) / 2

    _draw_panel(layer, font, stats_items, margin_x, stats_top, label_color, info_color, "left")
    _draw_panel(layer, font, aircraft_items, w - margin_x, aircraft_top, label_color, info_color, "right")

    # Bottom line: NEARBY -- next NEAREST_TRACKED-1 callsigns after the one
    # already shown in the AIRCRAFT panel (spec: "only the next 4 will show
    # on the bottom line").
    nearby = (live_aircraft or [])[1 : settings.nearest_tracked]
    nearby_value = ", ".join(a["callsign"] for a in nearby) if nearby else "NONE"
    _draw_boxed_segments(
        layer, font, [("NEARBY: ", label_color), (nearby_value, info_color)],
        (w / 2, h - margin_y - LINE_HEIGHT), align="center",
    )

    return layer
