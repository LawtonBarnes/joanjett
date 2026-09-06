"""Loads settings.ini. Deliberately dumb/flat -- the eventual Settings screen
layer writes this same file back out, so it needs to stay easy to round-trip."""

import configparser
import os

VERSION = "1.1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.ini")

# Fixed rendering geometry (not user-settable) -- per spec, the radar's outer
# ring sits at a 340px radius from center, clipped top/bottom on a 720x480
# NTSC composite frame. Every layer that projects lat/lon to screen pixels
# shares this same geometry.
SCREEN_WIDTH = 720
SCREEN_HEIGHT = 480
OUTER_RADIUS_PX = 340
CENTER_PX = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)

# NTSC composite 720x480 has non-square pixels: native resolution is 3:2 but
# displayed at 4:3, so pixel aspect ratio (width:height) = (4/3)/(720/480) =
# 8/9. Every layer's angle/lat-lon-to-pixel math assumed square pixels,
# which drew true circles in the framebuffer that came out as vertical
# ovals on the real CRT (horizontally compressed by this same factor).
# Applied as a multiplier on the vertical (y) component only -- geo.py and
# radar.py's shared projection primitives are the two places that need it.
PIXEL_ASPECT_RATIO = 8 / 9

MIN_RANGE_MULTIPLIER = 1
MAX_RANGE_MULTIPLIER = 15  # -> 60NM outer range; safety bound, see Settings.range_multiplier


class Settings:
    def __init__(self, path=SETTINGS_PATH):
        cp = configparser.ConfigParser()
        cp.read(path)

        self.location_name = cp.get("location", "name", fallback="METAL SHOP")
        self.location_lat = cp.getfloat("location", "lat", fallback=32.83475356507972)
        self.location_lon = cp.getfloat("location", "lon", fallback=-96.68858948607502)
        self.location_name_is_custom = cp.getboolean(
            "location", "name_is_custom", fallback=False
        )

        # RANGE is set as an integer multiplier (matches Settings Screen.png's
        # "< 2x >" selector and the spec's ring system: rings sit at
        # multiplier, 2x, 3x, 4x -- so full range_nm is always multiplier*4.
        # MAX_RANGE_MULTIPLIER exists specifically because of the 2026-08-25
        # memory incident -- an unbounded RANGE (e.g. a future AUTO RANGE
        # picking up a very distant aircraft) must not be able to request an
        # arbitrarily large map fetch.
        self.range_multiplier = cp.getint("display", "range_multiplier", fallback=3)
        self.interval_sec = cp.getfloat("display", "interval_sec", fallback=10.0)
        self.drop_shadow = cp.getboolean("display", "drop_shadow", fallback=True)
        self.color_scheme = cp.get("display", "color_scheme", fallback="COLORTRAK")
        self.all_caps = cp.getboolean("display", "all_caps", fallback=False)
        self.max_tracked = cp.getint("display", "max_tracked", fallback=10)
        self.nearest_tracked = cp.getint("display", "nearest_tracked", fallback=5)
        self.trail_length = cp.getint("display", "trail_length", fallback=8)

        # Display-only per the 2026-08-25 decision to drop the live ATC
        # audio stream (unreliable) in favor of a real scanner -- these just
        # tell the user what to tune the scanner to.
        self.atc_icao = cp.get("atc", "icao", fallback="KDAL")
        self.atc_freq = cp.get("atc", "freq", fallback="118.70")
