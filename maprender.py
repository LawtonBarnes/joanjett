"""Renders the map layer (basemap.Layers) to a static pygame Surface --
rendered once per (location, range) and reused as a plain blit every frame,
since the map never moves on its own.

Pure blue on black only -- the map is background and should fall away
behind the cyan planes (user, 2026-10-16: lighter/steel blues were too busy
and competed with the planes). Layers differ only in blue *intensity*, from
very dark (streets, lake water) up to full 0000FF (freeways, runways).
Lines stay 2px or wider -- 1px lines wash out on the CRT (user feedback
2026-08-25).
"""

import math

import numpy as np
import pygame

import colors
import config

# Draw order is bottom to top. Each layer: style, color(s), width.
#   line  -- solid polyline
#   glow  -- wide dim underlay + bright core, for a phosphor-trace look
#   dash  -- dashed polyline (dash, gap in px)
#   dot   -- dotted polyline (dot spacing in px)
#   fill  -- filled polygon with an outline (lakes)
#   max_range -- hide the layer when RANGE is wider than this (NM)
#   far       -- overrides used when RANGE is wider than FAR_RANGE, so busy
#                layers quiet down as the view widens
# Layers missing from the map data (e.g. secondary roads on the live-fetch
# path) are simply skipped.
STYLES = {
    "COLORTRAK": [
        ("lake",      {"style": "fill", "fill": "000034", "color": "000088", "width": 2}),
        ("county",    {"style": "dash", "color": "000048", "width": 2, "dash": (7, 9)}),
        ("tertiary",  {"style": "line", "color": "000050", "width": 2}),
        ("secondary", {"style": "line", "color": "000068", "width": 2, "far": {"color": "000050"}}),
        ("rail",      {"style": "dot",  "color": "000090", "width": 2, "spacing": 7}),
        ("canal",     {"style": "line", "color": "000090", "width": 2}),
        ("river",     {"style": "line", "color": "0000A8", "width": 3, "far": {"color": "000078", "width": 2}}),
        ("primary",   {"style": "line", "color": "0000B4", "width": 2}),
        ("trunk",     {"style": "glow", "color": "0000D8", "glow": "000048", "width": 3, "glow_width": 7}),
        ("motorway",  {"style": "glow", "color": "0000FF", "glow": "000058", "width": 3, "glow_width": 9}),
        ("taxiway",   {"style": "line", "color": "000070", "width": 2}),
        ("runway",    {"style": "line", "color": "0000FF", "width": 4, "max_range": 32, "far": {"width": 3}}),
    ],
}

FAR_RANGE = 12

# Smallest lake drawn, as its on-screen span in pixels at the current range.
MIN_LAKE_PX = 4

# The visible area in NM is a little wider than RANGE horizontally (the
# 720px frame is wider than the 340px-radius ring) and narrower vertically;
# features are culled by bounding box against it, with a small margin.
_VIEW_X = (config.SCREEN_WIDTH / 2) / config.OUTER_RADIUS_PX * 1.05
_VIEW_Y = (config.SCREEN_HEIGHT / 2) / (config.OUTER_RADIUS_PX * config.PIXEL_ASPECT_RATIO) * 1.05

# Round joints (a filled circle per vertex, closing the notches pygame
# leaves at sharp corners of wide lines) only pay off on wide, bright
# lines -- on 2-3px lines and the dark glow underlay they're invisible, and
# drawing one per vertex was most of the render time on a Pi 3B.
ROUND_JOIN_MIN_WIDTH = 4


class _Points:
    def __init__(self, coords):
        self.coords = coords


def _px_list(layer, ppn):
    """Whole layer to screen pixels in one numpy pass, then one tolist() --
    features are sliced out of the plain list (per-feature numpy slicing
    and conversion was most of the render time on a Pi 3B)."""
    px = np.empty_like(layer.coords)
    px[:, 0] = config.CENTER_PX[0] + layer.coords[:, 0] * ppn
    px[:, 1] = config.CENTER_PX[1] - layer.coords[:, 1] * (ppn * config.PIXEL_ASPECT_RATIO)
    return px.tolist()


def _visible_parts(layer, view_x, view_y):
    b = layer.bbox
    return (b[:, 2] >= -view_x) & (b[:, 0] <= view_x) & (b[:, 3] >= -view_y) & (b[:, 1] <= view_y)


def _solid(surface, color, px, width):
    pygame.draw.lines(surface, color, False, px, width)
    if width >= ROUND_JOIN_MIN_WIDTH:
        r = width // 2
        for pt in px:
            pygame.draw.circle(surface, color, pt, r)


def _walk(px, on, off):
    """Yields the (start, end) sub-segments of a polyline that fall in the
    'on' part of an on/off pattern measured along its length."""
    period = on + off
    pos = 0.0
    for (x0, y0), (x1, y1) in zip(px, px[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        t = 0.0
        while t < seg:
            phase = pos % period
            if phase < on:
                step = min(on - phase, seg - t)
                a, b = t / seg, (t + step) / seg
                yield ((x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b))
            else:
                step = min(period - phase, seg - t)
            t += step
            pos += step


def _lines(surface, style, cols, polylines):
    kind = style["style"]
    width = style["width"]
    if kind == "line":
        for px in polylines:
            _solid(surface, cols["color"], px, width)
    elif kind == "glow":
        # all glows first, then all cores -- freeways are mapped as two
        # side-by-side one-way roads, and drawing glow+core per road let
        # the second road's glow cover the first one's core
        for px in polylines:
            _solid(surface, cols["glow"], px, style["glow_width"])
        for px in polylines:
            _solid(surface, cols["color"], px, width)
    elif kind == "dash":
        on, off = style["dash"]
        for px in polylines:
            for a, b in _walk(px, on, off):
                pygame.draw.line(surface, cols["color"], a, b, width)
    elif kind == "dot":
        r = max(1, width // 2 + 1)
        for px in polylines:
            for a, _ in _walk(px, 1, style["spacing"] - 1):
                pygame.draw.circle(surface, cols["color"], a, r)


def _lakes(surface, style, cols, layer, px, visible, ppn, black):
    span = np.maximum(layer.bbox[:, 2] - layer.bbox[:, 0], layer.bbox[:, 3] - layer.bbox[:, 1]) * ppn
    first = np.concatenate([[0], np.cumsum(layer.rings)[:-1]]).tolist()
    offs = layer.offsets.tolist()
    counts = layer.rings.tolist()
    for lake, ring0 in enumerate(first):
        if not visible[ring0] or span[ring0] < MIN_LAKE_PX:
            continue
        rings = [px[offs[r]:offs[r + 1]] for r in range(ring0, ring0 + counts[lake])]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            continue
        outline, *islands = rings
        pygame.draw.polygon(surface, cols["fill"], outline)
        for island in islands:
            pygame.draw.polygon(surface, black, island)
        for ring in rings:
            pygame.draw.lines(surface, cols["color"], True, ring, style["width"])


def render_map_surface(layers, range_nm, color_scheme="COLORTRAK"):
    surface = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    black = colors.rgb(colors.BLACK)
    surface.fill(black)
    ppn = config.OUTER_RADIUS_PX / range_nm
    view_x, view_y = range_nm * _VIEW_X, range_nm * _VIEW_Y
    styles = STYLES.get(color_scheme, STYLES["COLORTRAK"])

    for name, style in styles:
        layer = layers.layers.get(name)
        if layer is None or not len(layer.coords):
            continue
        if range_nm > layers.show_max_range.get(name, float("inf")):
            continue  # detail layer hidden at this range (prebuilt maps only)
        if range_nm > style.get("max_range", float("inf")):
            continue
        if range_nm > FAR_RANGE and "far" in style:
            style = {**style, **style["far"]}
        cols = {k: colors.rgb(style[k]) for k in ("color", "fill", "glow") if k in style}
        visible = _visible_parts(layer, view_x, view_y)
        if not visible.any():
            continue
        if style["style"] == "fill":
            _lakes(surface, style, cols, layer, _px_list(layer, ppn), visible, ppn, black)
            continue
        # Only the visible features' points get converted -- at close range
        # most of a layer (e.g. ~50k secondary road segments) is off screen.
        idx = np.flatnonzero(visible)
        starts = layer.offsets[idx]
        lens = layer.offsets[idx + 1] - starts
        keep = lens >= 2
        starts, lens = starts[keep], lens[keep]
        if not len(lens):
            continue
        ends = np.cumsum(lens)
        point_idx = np.repeat(starts - (ends - lens), lens) + np.arange(ends[-1])
        sub = _px_list(_Points(layer.coords[point_idx]), ppn)
        bounds = np.concatenate([[0], ends]).tolist()
        polylines = [sub[bounds[k]:bounds[k + 1]] for k in range(len(lens))]
        _lines(surface, style, cols, polylines)

    return surface
