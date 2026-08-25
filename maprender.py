"""Renders fetched OSM vector elements to a static pygame Surface: black
background, MAP_COLOR lines for roads/water. Rendered once per (location,
range) and reused as a plain blit every frame -- redrawing hundreds/thousands
of line segments per frame would be wasted work for a layer that never moves
on its own (only RANGE/LOCATION changes should ever trigger a re-render)."""

import pygame

import colors
import config
import geo

# Line width in px by OSM subtype -- CRT composite output washes out thin
# anti-aliased lines (user feedback 2026-08-25: "fine lines are getting lost"
# on the real TV even though they looked fine in a PNG snapshot), so this
# switched from pygame.draw.aalines (always 1px, blended) to solid
# pygame.draw.lines at a width per feature's importance. Bumped twice
# (2px/3px/4px -> 3px/4px/5px), then brought back down by 1 across the
# board (2026-08-25) -> 2px/3px/4px.
LINE_WIDTHS = {
    "motorway": 4,
    "trunk": 3,
    "primary": 2,
    "river": 3,
    "canal": 2,
    "pond": 2,
}
DEFAULT_LINE_WIDTH = 2


def render_map_surface(elements, center_lat, center_lon, range_nm, color_scheme="COLORTRAK"):
    surface = pygame.Surface((config.SCREEN_WIDTH, config.SCREEN_HEIGHT))
    surface.fill(colors.rgb(colors.BLACK))

    map_color = colors.rgb(colors.COLOR_SCHEMES[color_scheme]["MAP_COLOR"])

    for el in elements:
        px_points = [
            geo.latlon_to_px(
                lat,
                lon,
                center_lat,
                center_lon,
                range_nm,
                config.CENTER_PX,
                config.OUTER_RADIUS_PX,
            )
            for lat, lon in el["points"]
        ]
        width = LINE_WIDTHS.get(el.get("subtype"), DEFAULT_LINE_WIDTH)
        pygame.draw.lines(surface, map_color, False, px_points, width)
        if width > 2:
            # plain draw.lines leaves gaps at sharp-angle joints for any
            # width >1 (each segment is its own rectangle, no miter/round
            # join) -- filling a circle at every vertex smooths that out.
            # Skipped at width<=2 since the gap is imperceptible there.
            radius = width // 2
            for pt in px_points:
                pygame.draw.circle(surface, map_color, pt, radius)

    return surface
