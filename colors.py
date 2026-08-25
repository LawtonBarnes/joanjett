"""Named color constants for JOAN JETT, so app code never has to write raw pygame tuples."""

GREEN = "00FF00"
BLUE = "0000FF"
RED = "FF0000"
CYAN = "00FFFF"
MAGENTA = "FF00FF"
YELLOW = "FFFF00"
BLACK = "000000"
WHITE = "FFFFFF"
ORANGE = "FFA500"  # not part of the 8 named spec colors -- used for the Info HUD's LOCATION header only


def rgb(hex_color):
    """'RRGGBB' -> (r, g, b) tuple, the only place hex strings get parsed."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


# Only COLORTRAK (the default scheme) is filled in for now -- it's the only one
# the map layer needs. GREEN_MANALISHI/AMBER_ALERT/SYNTHWAVE/CMYK were described
# tersely in the spec ("all green text over blue map", "magenta and green", etc.)
# and get fleshed out for real during the Settings-screen pass, once they're
# actually selectable and easy to check on screen.
COLOR_SCHEMES = {
    "COLORTRAK": {
        "BACKGROUND": BLUE,
        "LABEL": YELLOW,
        "INFO": WHITE,
        "CALLSIGN": CYAN,
        "PLANES": CYAN,
        "RADAR": GREEN,
        "ALERTS": RED,
        "MAP_COLOR": BLUE,
    },
}
