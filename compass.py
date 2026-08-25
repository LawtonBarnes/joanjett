"""Compass layer -- N/S/E/W cardinal labels in MAP_COLOR, baked into the
static background surface alongside the map and rings (same lifecycle,
only rebuilt on RANGE/LOCATION change). Per spec, sits above the map layer
and below the radar rings/sweep.
"""
import pygame

import colors
import config
import radar  # reuses its _polar_to_px -- one angle-math implementation
# for the whole app, not a second copy that could drift out of sync.

CARDINALS = (("N", 0), ("E", 90), ("S", 180), ("W", 270))

# Centered between the 1st ring (0.25 * OUTER_RADIUS_PX) and 2nd ring
# (0.5 * OUTER_RADIUS_PX) -- per user follow-up 2026-08-25 (the first
# placement, close to the 2nd ring, crowded "E" against that ring's own "6"
# NM label and was hard to read at the original font size).
RADIUS_FRACTION = 0.375

BOX_PAD_X = 6
BOX_PAD_Y = 3


def draw_compass(surface, map_color, font):
    """`font` should already have bold set if a bold variant/synthetic bold
    is available (see main.py -- no dedicated bold TTF exists for VCR OSD
    Mono on this Pi, so this uses pygame's algorithmic font.set_bold())."""
    radius = config.OUTER_RADIUS_PX * RADIUS_FRACTION
    black = colors.rgb(colors.BLACK)
    for label, angle in CARDINALS:
        pt = radar._polar_to_px(radius, angle)
        text = font.render(label, True, map_color)
        box = pygame.Surface((text.get_width() + BOX_PAD_X * 2, text.get_height() + BOX_PAD_Y * 2))
        box.fill(black)
        box.blit(text, (BOX_PAD_X, BOX_PAD_Y))
        surface.blit(box, (pt[0] - box.get_width() / 2, pt[1] - box.get_height() / 2))
