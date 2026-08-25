"""Radar rings (static, folded into the background layer alongside the map)
and the rotating sweep+trail (dynamic, rebuilt fresh every frame).

Screen-angle convention: 0deg = up/north, increasing clockwise -- matches
geo.py's north-up map projection, so a plane heading and the sweep angle
both read naturally against the map underneath.
"""
import math

import pygame

import colors
import config

RING_ANGLE_STEP_DEG = 2  # point density for the ring polyline -- fine enough
# that 2deg-apart segments look like a smooth continuous circle
GAP_CENTER_DEG = 90  # east / screen-right -- spec's "small gap on the right
# side for the distance indicator"
GAP_HALF_ANGLE_DEG = 12
RING_WIDTH = 2  # 2px minimum per user request 2026-08-25 (was dotted, 1px dots)

RING_LABEL_FONT_SIZE = 22  # bumped from 18 (2026-08-25, "not much" per user)
RING_LABEL_BOX_PAD_X = 4
RING_LABEL_BOX_PAD_Y = 2

# Trailing gradient behind the sweep head -- spec: "triangular, wider at the
# outer radius, 1 pixel at the vertex." A fixed *angular* width naturally
# renders as exactly that wedge shape once converted to screen pixels (arc
# length = radius * angle), so this only needs to vary alpha across the
# angular width, not compute a triangle directly.
#
# Each line spans the full radius (center -> edge) at one fixed angle, so
# equal-degree spacing between lines is equal *arc-length* spacing at every
# radius simultaneously -- the physical pixel gap between adjacent lines
# therefore scales with radius (gap_px = OUTER_RADIUS_PX * radians(step)),
# invisible near the center but wide enough at the outer edge to show as
# moire (user feedback 2026-08-25: at the old 40deg/1deg-step, outer gap was
# ~5.9px against a 2px line width). TRAIL_LINE_COUNT is the fixed quantity
# ("keep the same number of lines"); TRAIL_SWEEP_DEG was narrowed 40->14 so
# the derived step (~0.35deg) closes that outer gap to ~2px, matching the
# line width -- the real, necessary tradeoff is a shorter-looking trail
# (about a third of the previous span), since packing the same count of
# full-length radial lines tighter has no way to avoid also shrinking the
# total angular coverage.
TRAIL_SWEEP_DEG = 14
TRAIL_LINE_COUNT = 50  # bumped from 40 (2026-08-25) -- at 40 the outer gap
# was ~2.08px, right at the 2px line width edge (still faintly visible);
# 50 brings it to ~1.66px for a comfortable overlap margin
TRAIL_ANGLE_STEP_DEG = TRAIL_SWEEP_DEG / TRAIL_LINE_COUNT
TRAIL_LINE_WIDTH = 2  # 2px minimum per user request 2026-08-25 (was 1px)
SWEEP_HEAD_WIDTH = 2


def _polar_to_px(radius_px, angle_deg, center_px=config.CENTER_PX):
    theta = math.radians(angle_deg)
    dx = radius_px * math.sin(theta)
    dy = -radius_px * math.cos(theta)
    return (center_px[0] + dx, center_px[1] + dy)


def _in_gap(angle_deg):
    diff = (angle_deg - GAP_CENTER_DEG + 180) % 360 - 180
    return abs(diff) <= GAP_HALF_ANGLE_DEG


def draw_ring_lines(surface, radar_color):
    """Draws just the ring circles onto `surface`. Split out from the NM
    labels (draw_ring_labels) 2026-08-25 so the two can composite on
    opposite sides of the planes layer -- circles stay above planes, but
    the labels move below so a callsign overlapping a ring's NM digits
    doesn't get stepped on (user request).

    Built as a polyline via the same _polar_to_px math as the sweep, not
    pygame.draw.arc (which uses a different angle convention/units -- one
    angle-math implementation for the whole module is one less way to get
    the direction or gap position wrong)."""
    for k in range(1, 5):
        radius = config.OUTER_RADIUS_PX * k / 4
        run = []
        angle = 0.0
        while angle <= 360:
            if not _in_gap(angle):
                run.append(_polar_to_px(radius, angle))
            elif run:
                if len(run) >= 2:
                    pygame.draw.lines(surface, radar_color, False, run, RING_WIDTH)
                run = []
            angle += RING_ANGLE_STEP_DEG
        if len(run) >= 2:
            pygame.draw.lines(surface, radar_color, False, run, RING_WIDTH)


def draw_ring_labels(surface, range_multiplier, radar_color, font):
    """Draws just the NM digit labels (3/6/9/12) -- see draw_ring_lines."""
    for k in range(1, 5):
        radius = config.OUTER_RADIUS_PX * k / 4
        label = str(int(round(range_multiplier * k)))
        text = font.render(label, True, radar_color)
        box = pygame.Surface(
            (text.get_width() + RING_LABEL_BOX_PAD_X * 2, text.get_height() + RING_LABEL_BOX_PAD_Y * 2)
        )
        box.fill(colors.rgb(colors.BLACK))
        box.blit(text, (RING_LABEL_BOX_PAD_X, RING_LABEL_BOX_PAD_Y))
        label_pt = _polar_to_px(radius, GAP_CENTER_DEG)
        surface.blit(box, (label_pt[0] - box.get_width() / 2, label_pt[1] - box.get_height() / 2))


def build_sweep_layer(angle_deg, radar_color, size):
    """-> a fresh SRCALPHA surface with the trail+head, composited onto the
    background layer by the caller. Rebuilt every frame (this is the only
    per-frame drawing work -- everything else is a static blit)."""
    layer = pygame.Surface(size, pygame.SRCALPHA)
    r, g, b = radar_color
    offset = 0.0
    while offset <= TRAIL_SWEEP_DEG:
        alpha = int(255 * (1 - offset / TRAIL_SWEEP_DEG))
        end_pt = _polar_to_px(config.OUTER_RADIUS_PX, angle_deg - offset)
        pygame.draw.line(layer, (r, g, b, alpha), config.CENTER_PX, end_pt, TRAIL_LINE_WIDTH)
        offset += TRAIL_ANGLE_STEP_DEG
    head_pt = _polar_to_px(config.OUTER_RADIUS_PX, angle_deg)
    pygame.draw.line(layer, (r, g, b, 255), config.CENTER_PX, head_pt, SWEEP_HEAD_WIDTH)
    return layer
