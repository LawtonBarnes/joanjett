"""Planes layer -- tracked aircraft with a connect-the-dots trail and a
callsign-labeled arrowhead. Rendered fresh every frame (dynamic, like the
sweep), composited *above* the vignette so aircraft stay fully visible even
at the screen edges ("so we can see them coming in from the corners").

The spec's standout request -- "it would really be cool if the plane's
location updated as the sweep went across it" -- is implemented literally:
each TrackedPlane only accepts a new position/heading from the latest fetch
when the sweep beam has just passed over that aircraft's current bearing
since the previous frame. Between sweeps it stays frozen at its last-swept
position, same as a real analog radar blip.
"""
from collections import deque

import pygame

import colors
import config
import geo

ARROWHEAD_WIDTH = 10
ARROWHEAD_LENGTH = 20
TRAIL_WIDTH = 3
CALLSIGN_FONT_SIZE = 20  # bumped from 18 (2026-08-25, "a tiny bit")
CALLSIGN_BOX_PAD_X = 4
CALLSIGN_BOX_PAD_Y = 2

# Trail fade -- opaque near the current position, transparent toward the
# oldest end. TRAIL_FULL_OPACITY_SEGMENTS keeps the segments closest to the
# arrowhead fully solid before the fade starts (per user request 2026-08-25).
TRAIL_FULL_OPACITY_SEGMENTS = 2


def _heading_unit_vector(heading_deg):
    import math

    theta = math.radians(heading_deg)
    return (math.sin(theta), -math.cos(theta))  # 0=up/north, clockwise -- same convention as radar.py


def _angle_in_swept_range(angle, start, end):
    """True if `angle` lies on the clockwise arc from `start` to `end`
    (handles the 360->0 wraparound; `end` is always >= `start` in this
    convention, i.e. the arc length, so this stays correct near 360/0)."""
    span = (end - start) % 360
    pos = (angle - start) % 360
    return pos <= span


class TrackedPlane:
    def __init__(self, trail_length):
        self.trail = deque(maxlen=trail_length)  # [(lat, lon), ...], oldest first
        self.callsign = ""
        self.heading_deg = 0.0

    def maybe_update(self, live_ac, swept_from, swept_to):
        if _angle_in_swept_range(live_ac["bearing_deg"], swept_from, swept_to):
            self.trail.append((live_ac["lat"], live_ac["lon"]))
            self.heading_deg = live_ac["heading_deg"]
            self.callsign = live_ac["callsign"]

    @property
    def has_position(self):
        return len(self.trail) > 0


def update_tracked(tracked, live_aircraft, swept_from, swept_to, trail_length):
    """Mutates `tracked` (dict: hex -> TrackedPlane) in place: adds newly
    top-N aircraft, drops ones no longer in the live top-N, and applies
    each remaining one's sweep-gated position update."""
    live_by_hex = {a["hex"]: a for a in live_aircraft}
    for hex_id in list(tracked):
        if hex_id not in live_by_hex:
            del tracked[hex_id]
    for hex_id, live_ac in live_by_hex.items():
        plane = tracked.setdefault(hex_id, TrackedPlane(trail_length))
        plane.maybe_update(live_ac, swept_from, swept_to)


def _draw_faded_trail(layer, px_points, color, width):
    """Each segment gets its own alpha: the segments nearest the current
    position (end of the list) stay fully opaque for
    TRAIL_FULL_OPACITY_SEGMENTS, then fade linearly to transparent by the
    oldest segment -- same per-segment-alpha technique as the sweep trail."""
    n_segments = len(px_points) - 1
    if n_segments < 1:
        return
    r, g, b = color
    for j in range(n_segments):
        segments_from_recent = (n_segments - 1) - j  # 0 = segment nearest the current position
        if segments_from_recent < TRAIL_FULL_OPACITY_SEGMENTS:
            alpha = 255
        else:
            fade_span = max(1, n_segments - TRAIL_FULL_OPACITY_SEGMENTS)
            t = (segments_from_recent - TRAIL_FULL_OPACITY_SEGMENTS + 1) / fade_span
            alpha = max(0, int(255 * (1 - t)))
        pygame.draw.line(layer, (r, g, b, alpha), px_points[j], px_points[j + 1], width)


def render_planes(tracked, center_lat, center_lon, range_nm, plane_color, font, size):
    layer = pygame.Surface(size, pygame.SRCALPHA)

    plane_data = []
    for plane in tracked.values():
        if not plane.has_position:
            continue
        px_points = [
            geo.latlon_to_px(lat, lon, center_lat, center_lon, range_nm, config.CENTER_PX, config.OUTER_RADIUS_PX)
            for lat, lon in plane.trail
        ]
        plane_data.append((plane, px_points))

    # Callsign boxes drawn first, trail+arrowhead drawn after (on top) --
    # per user request 2026-08-25, so an overlapping black box never punches
    # a gap into the trail/arrowhead line.
    for plane, px_points in plane_data:
        if plane.callsign:
            pos = px_points[-1]
            text = font.render(plane.callsign, True, plane_color)
            box = pygame.Surface(
                (text.get_width() + CALLSIGN_BOX_PAD_X * 2, text.get_height() + CALLSIGN_BOX_PAD_Y * 2)
            )
            box.fill(colors.rgb(colors.BLACK))
            box.blit(text, (CALLSIGN_BOX_PAD_X, CALLSIGN_BOX_PAD_Y))
            layer.blit(box, (pos[0] + ARROWHEAD_WIDTH, pos[1] - box.get_height() / 2))

    for plane, px_points in plane_data:
        _draw_faded_trail(layer, px_points, plane_color, TRAIL_WIDTH)

        pos = px_points[-1]
        dx, dy = _heading_unit_vector(plane.heading_deg)
        tip = (pos[0] + dx * ARROWHEAD_LENGTH / 2, pos[1] + dy * ARROWHEAD_LENGTH / 2)
        base_center = (pos[0] - dx * ARROWHEAD_LENGTH / 2, pos[1] - dy * ARROWHEAD_LENGTH / 2)
        perp = (dy, -dx)
        base_l = (base_center[0] + perp[0] * ARROWHEAD_WIDTH / 2, base_center[1] + perp[1] * ARROWHEAD_WIDTH / 2)
        base_r = (base_center[0] - perp[0] * ARROWHEAD_WIDTH / 2, base_center[1] - perp[1] * ARROWHEAD_WIDTH / 2)
        pygame.draw.polygon(layer, plane_color, [tip, base_l, base_r])

    return layer
