"""Loads settings.ini. Deliberately dumb/flat -- the eventual Settings screen
layer writes this same file back out, so it needs to stay easy to round-trip."""

import configparser
import os

VERSION = "1.9"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.ini")

FB_SIZE_PATH = "/sys/class/graphics/fb0/virtual_size"


def _detect_display():
    """-> (mode, fb_w, fb_h). mode is "hd" or "sd" -- from settings.ini
    [display] display = auto|sd|hd (env JOANJETT_DISPLAY overrides, for
    headless snapshots off the Pi). auto = HD on a 16:9-ish framebuffer
    (HIDEF's 1920x1080), SD everywhere else. Decided once at import, so
    every module's geometry constants below agree."""
    try:
        with open(FB_SIZE_PATH) as f:
            fb_w, fb_h = (int(x) for x in f.read().split(","))
    except (OSError, ValueError):
        # no framebuffer (headless snapshot off the Pi): JOANJETT_FB=1920x1080
        fb_w, fb_h = (int(x) for x in os.environ.get("JOANJETT_FB", "720x480").split("x"))
    cp = configparser.ConfigParser()
    cp.read(SETTINGS_PATH)
    want = os.environ.get("JOANJETT_DISPLAY") or cp.get("display", "display", fallback="auto")
    want = want.strip().lower()
    if want == "auto":
        want = "hd" if fb_w / fb_h >= 1.6 and fb_h >= 720 else "sd"
    return ("hd" if want == "hd" else "sd"), fb_w, fb_h


DISPLAY_MODE, FB_WIDTH, FB_HEIGHT = _detect_display()
HD = DISPLAY_MODE == "hd"

# Fixed rendering geometry (not user-settable) -- per spec, the radar's outer
# ring sits at a 340px radius from center, clipped top/bottom on a 720x480
# NTSC composite frame. Every layer that projects lat/lon to screen pixels
# shares this same geometry.
#
# NTSC composite 720x480 has non-square pixels: native resolution is 3:2 but
# displayed at 4:3, so pixel aspect ratio (width:height) = (4/3)/(720/480) =
# 8/9. Every layer's angle/lat-lon-to-pixel math assumed square pixels,
# which drew true circles in the framebuffer that came out as vertical
# ovals on the real CRT (horizontally compressed by this same factor).
# Applied as a multiplier on the vertical (y) component only -- geo.py and
# radar.py's shared projection primitives are the two places that need it.
#
# HD (JOAN JET HD, 2026-11-25, HIDEF's 1080i tube): the framebuffer's own
# size with square pixels. The bullseye keeps the SD look -- same size
# relative to the picture height (clipped top and bottom the same way) --
# and everything else (fonts, lines, boxes) scales by UI_SCALE. The wider
# 16:9 frame is what gives the scope its extra room: the side panels sit
# at the same fraction of the (now wider) width, out past the old 4:3 edge.
if HD:
    SCREEN_WIDTH, SCREEN_HEIGHT = FB_WIDTH, FB_HEIGHT
    PIXEL_ASPECT_RATIO = 1.0
    UI_SCALE = SCREEN_HEIGHT / 480
    OUTER_RADIUS_PX = round(340 * 8 / 9 * UI_SCALE)
else:
    SCREEN_WIDTH, SCREEN_HEIGHT = 720, 480
    PIXEL_ASPECT_RATIO = 8 / 9
    UI_SCALE = 1.0
    OUTER_RADIUS_PX = 340
CENTER_PX = (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)


def px(n):
    """An SD pixel size (font, pad, offset) at this display's scale."""
    return n if not HD else max(1, round(n * UI_SCALE))


def lw(n):
    """An SD line width at this display's scale -- never under 2 px in HD
    (1080i flickers on 1-px lines)."""
    return n if not HD else max(2, round(n * UI_SCALE))

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

        # AIR METAL's ride (2026-10-27): the aircraft the 2x2 wall on
        # PRODUCTION is riding gets a yellow circle on the radar. Empty
        # turns it off.
        self.airmetal_url = cp.get("airmetal", "url", fallback="http://192.168.68.71:8430/riding")

        # Where aircraft come from (2026-11-25): readsb's file when this Pi
        # has the receiver, else the receiver's file over HTTP -- P4's
        # tar1090, the same feed AIR METAL reads. Only a copy reading the
        # local file logs passes.
        self.feed_file = cp.get("feed", "file", fallback="/run/readsb/aircraft.json")
        self.feed_url = cp.get("feed", "url", fallback="http://192.168.68.70/tar1090/data/aircraft.json")

        # Who logs passes: "app" (this screen, while JOAN JET is showing --
        # a single Pi on its own) or "service" (logger.py's background
        # service on the receiver, so passes are logged whatever's on screen;
        # the app then never logs).
        self.logged_by = cp.get("flightlog", "logged_by", fallback="app").strip().lower()

# Overlay text (the yellow/white/orange HUD and table screens) is exactly
# 2x SD in HD -- a touch smaller than UI_SCALE, for breathing room in the
# corners, where HIDEF's Samsung SlimFit gets soft (user, 2026-11-25).
OVERLAY_TEXT_SCALE = 2


def text_px(n):
    """An SD size in the HUD/table text overlay at this display's scale."""
    return n if not HD else n * OVERLAY_TEXT_SCALE
