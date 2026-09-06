#!/usr/bin/env python3
"""JOAN JETT -- retro CRT ADS-B radar.

Console/framebuffer plumbing (FrameBuffer, KD_GRAPHICS console attach, evdev
keyboard selector loop, EXIT_GOTO_HOME sentinel) is deliberately copied from
/opt/bars/bars.py's proven pattern rather than reinvented -- see that file's
own module docstring for why direct /dev/fb0 writes are used instead of a
real SDL display surface (this is also the fix for why the old retro-radar
project got retired: it used a real kmsdrm display surface, incompatible
with the vc4-fkms-v3d overlay this Pi needs for composite output).

Map + radar rings/sweep + vignette are wired up. Planes and the Info/
Aircraft/Settings screens are not built yet.

Adding the sweep changed the app from purely event-driven (redraw only on
keypress, like every sibling app) to a real animation loop -- run() now
paces itself to FRAME_INTERVAL regardless of input, since the sweep has to
keep rotating continuously. Input is still handled promptly in between
frames via a short selector timeout.
"""
import fcntl
import mmap
import os
import selectors
import signal
import sys
import time
from pathlib import Path

import evdev
import numpy as np
from evdev import ecodes

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import aircraft  # noqa: E402
import aircraft_screen  # noqa: E402
import colors  # noqa: E402
import compass  # noqa: E402
import config  # noqa: E402
import info  # noqa: E402
import mapdata  # noqa: E402
import maprender  # noqa: E402
import planes  # noqa: E402
import radar  # noqa: E402

VERSION = config.VERSION

FRAME_W, FRAME_H = config.SCREEN_WIDTH, config.SCREEN_HEIGHT
BLACK = colors.rgb(colors.BLACK)
WHITE = colors.rgb(colors.WHITE)

SCRIPT_DIR = Path(__file__).resolve().parent
FONT_PATH = SCRIPT_DIR / "VCR_OSD_MONO_1.001.ttf"
VIGNETTE_PATH = SCRIPT_DIR / "vignette.png"
SPLASH_PATH = SCRIPT_DIR / "splash.png"
SPLASH_SECONDS = 5.0  # same duration as bars.py's splash

FRAME_INTERVAL = 1.0 / 20  # 20fps -- plenty smooth for a slow-rotating sweep,
# cheap on a Pi 3B+ (LOUDNESS already proves continuous full-frame fb0
# writes are fine on this exact hardware)

# Screens cycled via Left/Right (spec's confirmed 3-screen plan: Radar,
# Aircraft, Settings) -- only the first two exist so far.
SCREENS = ["radar", "aircraft"]

# See bars.py -- Home exits with this so a future menu/STRINGS integration
# can jump straight to Health Monitor. No menu.py consumes it yet on this
# Pi (JOAN JETT isn't STRINGS-assigned yet), but matching the convention now
# costs nothing and avoids a rewrite later.
EXIT_GOTO_HOME = 42

KDSETMODE = 0x4B3A
KD_TEXT = 0x00
KD_GRAPHICS = 0x01

FETCH_MARGIN = 1.3  # covers screen corners at ~1.27x range, see geo.py


def find_keyboard_devices():
    devices = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.capabilities().get(ecodes.EV_KEY):
            devices.append(dev)
    if not devices:
        print("No keyboard input device found -- running headless/unattended.", file=sys.stderr)
    return devices


class FrameBuffer:
    """Copied from bars.py -- see that file for the full explanation."""

    def __init__(self, dev="/dev/fb0"):
        sys_dir = Path("/sys/class/graphics") / Path(dev).name
        self.width, self.height = (int(x) for x in (sys_dir / "virtual_size").read_text().split(","))
        self.bpp = int((sys_dir / "bits_per_pixel").read_text())
        self.stride = int((sys_dir / "stride").read_text())
        self.bypp = self.bpp // 8
        self.row_bytes = self.width * self.bypp
        size = self.stride * self.height
        self.fd = os.open(dev, os.O_RDWR)
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
        if self.bpp not in (16, 32):
            raise RuntimeError(f"Unsupported framebuffer depth: {self.bpp}bpp")

    def write_surface(self, surface):
        if surface.get_size() != (self.width, self.height):
            surface = pygame.transform.scale(surface, (self.width, self.height))
        arr = pygame.surfarray.pixels3d(surface).transpose(1, 0, 2)
        if self.bpp == 16:
            r = arr[:, :, 0].astype(np.uint16) >> 3
            g = arr[:, :, 1].astype(np.uint16) >> 2
            b = arr[:, :, 2].astype(np.uint16) >> 3
            raw = ((r << 11) | (g << 5) | b).astype("<u2").tobytes()
        else:
            alpha = np.zeros((self.height, self.width, 1), dtype=np.uint8)
            raw = np.concatenate([arr[:, :, ::-1], alpha], axis=2).astype(np.uint8).tobytes()

        if self.stride == self.row_bytes:
            self.mm.seek(0)
            self.mm.write(raw)
        else:
            for y in range(self.height):
                self.mm.seek(y * self.stride)
                self.mm.write(raw[y * self.row_bytes : (y + 1) * self.row_bytes])

    def close(self):
        self.mm.close()
        os.close(self.fd)


SPLASH_IMAGE_OFFSET_Y = -40  # shifts the splash art up from dead-center to
# leave room for the VERSION/LOADING text underneath it
SPLASH_TEXT_GAP = 12  # below the image, before the first text line
SPLASH_LINE_GAP = 4  # between the two text lines


def show_splash(fb, font, version_text, range_nm):
    """Blocking splash at launch, centered at native resolution -- NOT
    scaled to fit (unlike bars.py's splash, which is scaled since its
    source art matches the frame's aspect ratio). JOAN JETT's splash.png is
    500x281, well under the 720x480 frame, and scaling it up would blur a
    small image (caught live 2026-08-25 before ever shipping this version).
    Any failure (no splash.png deployed, bad image) just skips straight to
    normal startup rather than taking the app down over a cosmetic feature.

    Image is shifted up (SPLASH_IMAGE_OFFSET_Y) to leave room for two
    centered orange (colors.ORANGE) status lines underneath -- VERSION and
    the current map-load range, per user request."""
    if not SPLASH_PATH.exists():
        return
    try:
        img = pygame.image.load(str(SPLASH_PATH)).convert()
    except (pygame.error, OSError) as exc:
        print(f"Splash load failed: {exc}", file=sys.stderr)
        return
    canvas = pygame.Surface((FRAME_W, FRAME_H))
    canvas.fill(BLACK)
    img_w, img_h = img.get_size()
    img_x = (FRAME_W - img_w) // 2
    img_y = (FRAME_H - img_h) // 2 + SPLASH_IMAGE_OFFSET_Y
    canvas.blit(img, (img_x, img_y))

    text_color = colors.rgb(colors.ORANGE)
    line1 = font.render(f"VERSION {version_text}", True, text_color)
    line2 = font.render(f"LOADING MAPS {range_nm}NM...", True, text_color)
    y = img_y + img_h + SPLASH_TEXT_GAP
    canvas.blit(line1, ((FRAME_W - line1.get_width()) // 2, y))
    y += line1.get_height() + SPLASH_LINE_GAP
    canvas.blit(line2, ((FRAME_W - line2.get_width()) // 2, y))

    fb.write_surface(canvas)
    time.sleep(SPLASH_SECONDS)


class JoanJettApp:
    def __init__(self):
        self._quit_requested = False
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        pygame.init()
        pygame.display.set_mode((FRAME_W, FRAME_H))  # headless (dummy driver); needed for font/Surface ops

        self.fb = FrameBuffer()

        self.kbd_devices = find_keyboard_devices()
        self.selector = selectors.DefaultSelector()
        for dev in self.kbd_devices:
            self.selector.register(dev, selectors.EVENT_READ)

        self.settings = config.Settings()
        self.range_multiplier = self.settings.range_multiplier
        self.status_font = pygame.font.Font(str(FONT_PATH), 24)
        self.ring_label_font = pygame.font.Font(str(FONT_PATH), radar.RING_LABEL_FONT_SIZE)
        # Fixed at 36, not derived from RING_LABEL_FONT_SIZE*2 -- that used
        # to double as "compass is 2x the ring-label size," but bumping the
        # ring-label size later (2026-08-25) shouldn't silently drag the
        # already-tuned/approved compass size along with it.
        self.compass_font = pygame.font.Font(str(FONT_PATH), 36)
        # Bold (synthetic, via set_bold -- no dedicated bold VCR OSD Mono
        # file exists on this Pi) was added 2026-08-25, then removed again
        # same day per follow-up user request.
        self.callsign_font = pygame.font.Font(str(FONT_PATH), planes.CALLSIGN_FONT_SIZE)
        self.info_font = pygame.font.Font(str(FONT_PATH), info.FONT_SIZE)

        self.tracked_planes = {}  # hex -> planes.TrackedPlane
        self._prev_sweep_angle = 0.0
        self._latest_aircraft = []
        self._aircraft_fetch_ok = False
        self.current_screen = "radar"
        self.aircraft_screen_font = pygame.font.Font(str(FONT_PATH), aircraft_screen.FONT_SIZE)

        # vignette.png is solid black with a *varying alpha channel*
        # (transparent center -> opaque edges), not an RGB gradient -- found
        # live 2026-08-25 (a BLEND_MULT approach, assuming a white-center
        # grayscale mask, produced solid black everywhere: convert() drops
        # alpha and the underlying RGB is black at every pixel including
        # center). convert_alpha() + a normal blit is the correct way to
        # composite this asset.
        self.vignette_surface = pygame.image.load(str(VIGNETTE_PATH)).convert_alpha()
        if self.vignette_surface.get_size() != (FRAME_W, FRAME_H):
            self.vignette_surface = pygame.transform.smoothscale(self.vignette_surface, (FRAME_W, FRAME_H))

        self.map_surface = pygame.Surface((FRAME_W, FRAME_H))
        self.map_surface.fill(BLACK)
        self.background_surface = self.map_surface  # map + compass
        self.rings_surface = pygame.Surface((FRAME_W, FRAME_H), pygame.SRCALPHA)  # its own layer, see rebuild_map
        self._radar_color = colors.rgb(colors.COLOR_SCHEMES[self.settings.color_scheme]["RADAR"])
        self._plane_color = colors.rgb(colors.COLOR_SCHEMES[self.settings.color_scheme]["PLANES"])
        self.error_message = None
        self.error_message_until = 0
        self.pending_exit_code = 0
        self.sweep_start_time = time.monotonic()

        self.tty_fd = None
        self.console_graphics_mode = False
        try:
            self.tty_fd = os.open("/dev/tty", os.O_RDWR)
            fcntl.ioctl(self.tty_fd, KDSETMODE, KD_GRAPHICS)
            self.console_graphics_mode = True
        except OSError as exc:
            print(f"Console graphics mode not available: {exc}", file=sys.stderr)

        show_splash(self.fb, self.status_font, VERSION, self.range_multiplier * 4)

    def _handle_signal(self, signum, frame):
        self._quit_requested = True

    def show_loading(self):
        canvas = pygame.Surface((FRAME_W, FRAME_H))
        canvas.fill(BLACK)
        text = self.status_font.render(
            f"LOADING MAP -- {self.range_multiplier * 4}NM ...", True, WHITE
        )
        canvas.blit(text, ((FRAME_W - text.get_width()) // 2, (FRAME_H - text.get_height()) // 2))
        self.fb.write_surface(canvas)

    def rebuild_map(self):
        """Returns True/False (success). On failure self.map_surface/
        background_surface are left untouched -- caller decides whether to
        keep them (see change_range)."""
        self.show_loading()
        s = self.settings
        range_nm = self.range_multiplier * 4
        try:
            elements = mapdata.fetch_map_elements(
                s.location_lat, s.location_lon, range_nm * FETCH_MARGIN
            )
        except (mapdata.MapFetchError, OSError) as exc:
            print(f"Map fetch failed: {exc}", file=sys.stderr, flush=True)
            self.error_message = f"MAP UNAVAILABLE AT {self.range_multiplier}X"
            self.error_message_until = time.monotonic() + 3.0
            return False
        self.map_surface = maprender.render_map_surface(
            elements, s.location_lat, s.location_lon, range_nm, s.color_scheme
        )
        radar_color = colors.rgb(colors.COLOR_SCHEMES[s.color_scheme]["RADAR"])
        map_color = colors.rgb(colors.COLOR_SCHEMES[s.color_scheme]["MAP_COLOR"])
        # background_surface is map+compass+ring NM labels -- the labels
        # were moved here (under planes) 2026-08-25 so an overlapping
        # callsign wins instead of the label stepping on it, while the ring
        # *circles* stay on their own layer (rings_surface) composited
        # above planes as before (layer order: Map+labels, Planes, Rings,
        # Vignette, bottom to top).
        self.background_surface = self.map_surface.copy()
        compass.draw_compass(self.background_surface, map_color, self.compass_font)
        radar.draw_ring_labels(self.background_surface, self.range_multiplier, radar_color, self.ring_label_font)
        self.rings_surface = pygame.Surface((FRAME_W, FRAME_H), pygame.SRCALPHA)
        radar.draw_ring_lines(self.rings_surface, radar_color)
        self._radar_color = radar_color
        self._plane_color = colors.rgb(colors.COLOR_SCHEMES[s.color_scheme]["PLANES"])
        return True

    def sweep_angle(self):
        elapsed = time.monotonic() - self.sweep_start_time
        period = max(self.settings.interval_sec, 0.1)
        return (elapsed / period * 360.0) % 360.0

    def update_planes(self, current_angle):
        s = self.settings
        live = aircraft.fetch_aircraft(s.location_lat, s.location_lon, s.max_tracked)
        self._aircraft_fetch_ok = live is not None
        self._latest_aircraft = live or []
        planes.update_tracked(
            self.tracked_planes, self._latest_aircraft, self._prev_sweep_angle, current_angle, s.trail_length
        )
        self._prev_sweep_angle = current_angle

    def _render_radar_screen(self, angle):
        # Layer order (bottom to top), per user request 2026-08-25: Map,
        # Planes, Radar (rings+sweep), Vignette -- a deliberate change from
        # the original spec order (Map, Radar, Vignette, Planes), so radar
        # now sits above planes and both get vignetted at the edges,
        # instead of planes staying bright above the vignette.
        canvas = self.background_surface.copy()

        range_nm = self.range_multiplier * 4
        planes_layer = planes.render_planes(
            self.tracked_planes, self.settings.location_lat, self.settings.location_lon,
            range_nm, self._plane_color, self.callsign_font, (FRAME_W, FRAME_H),
        )
        canvas.blit(planes_layer, (0, 0))

        canvas.blit(self.rings_surface, (0, 0))
        sweep_layer = radar.build_sweep_layer(angle, self._radar_color, (FRAME_W, FRAME_H))
        canvas.blit(sweep_layer, (0, 0))

        canvas.blit(self.vignette_surface, (0, 0))

        # Info HUD sits on top of everything, including the vignette --
        # per spec it's the topmost layer, and text needs to stay fully
        # legible everywhere regardless of how the layers below it are
        # ordered/darkened.
        info_layer = info.render_info(
            (FRAME_W, FRAME_H), self.settings, self.range_multiplier, angle,
            self._latest_aircraft, self._aircraft_fetch_ok, self.settings.color_scheme, self.info_font,
        )
        canvas.blit(info_layer, (0, 0))
        return canvas

    def _render_aircraft_screen(self, angle):
        # Plain map, no compass/rings/sweep/planes -- per user request
        # 2026-08-25 ("the blue map with all the radar layers turned off").
        # Vignette still applies (a display/lens effect, not a "radar
        # layer"), and the sweep keeps running in the background regardless
        # of which screen is showing (angle is still computed/passed in),
        # so switching back to Radar shows continuity.
        canvas = self.map_surface.copy()
        canvas.blit(self.vignette_surface, (0, 0))
        table_layer = aircraft_screen.render_aircraft_screen(
            (FRAME_W, FRAME_H), self._latest_aircraft, self._aircraft_fetch_ok, self.settings,
            self.range_multiplier, angle, self.settings.color_scheme, self.aircraft_screen_font,
        )
        canvas.blit(table_layer, (0, 0))
        return canvas

    def render(self):
        angle = self.sweep_angle()
        self.update_planes(angle)

        if self.current_screen == "aircraft":
            canvas = self._render_aircraft_screen(angle)
        else:
            canvas = self._render_radar_screen(angle)

        if self.error_message and time.monotonic() < self.error_message_until:
            text = self.status_font.render(self.error_message, True, WHITE, (128, 0, 0))
            canvas.blit(text, ((FRAME_W - text.get_width()) // 2, FRAME_H - 40))
        self.fb.write_surface(canvas)

    def change_range(self, step):
        new_val = self.range_multiplier + step
        new_val = max(config.MIN_RANGE_MULTIPLIER, min(config.MAX_RANGE_MULTIPLIER, new_val))
        if new_val == self.range_multiplier:
            return False
        old_val = self.range_multiplier
        self.range_multiplier = new_val
        if not self.rebuild_map():
            # Fetch failed (e.g. hit the safety cap) -- revert to the range
            # that's actually reflected in the still-displayed map, rather
            # than silently showing a blank map at a range that never loaded.
            self.range_multiplier = old_val
        return True

    def cycle_screen(self, step):
        idx = SCREENS.index(self.current_screen)
        self.current_screen = SCREENS[(idx + step) % len(SCREENS)]

    def handle_keycode(self, code):
        """Returns True if the app should redraw, "quit"/"quit_home" to exit."""
        if code in (ecodes.KEY_HOMEPAGE, ecodes.KEY_HOME):
            return "quit_home"
        elif code in (ecodes.KEY_Q, ecodes.KEY_ESC, ecodes.KEY_BACK, ecodes.KEY_COMPOSE):
            return "quit"
        elif code == ecodes.KEY_UP:
            return self.change_range(1)
        elif code == ecodes.KEY_DOWN:
            return self.change_range(-1)
        elif code == ecodes.KEY_LEFT:
            self.cycle_screen(-1)
        elif code == ecodes.KEY_RIGHT:
            self.cycle_screen(1)
        return False

    def run(self):
        try:
            self.rebuild_map()
            self.sweep_start_time = time.monotonic()
            self._prev_sweep_angle = 0.0
            running = True
            next_frame = time.monotonic()
            while running and not self._quit_requested:
                timeout = max(0.0, next_frame - time.monotonic())
                for key, _ in self.selector.select(timeout=timeout):
                    device = key.fileobj
                    for event in device.read():
                        if event.type != ecodes.EV_KEY or event.value != 1:
                            continue
                        result = self.handle_keycode(event.code)
                        if result == "quit":
                            running = False
                        elif result == "quit_home":
                            running = False
                            self.pending_exit_code = EXIT_GOTO_HOME
                    if not running:
                        break
                if not running:
                    break
                now = time.monotonic()
                if now >= next_frame:
                    self.render()
                    # Paced from `now`, not from the old `next_frame` target,
                    # so falling behind (e.g. a slow map fetch just finished)
                    # doesn't cause a burst of catch-up frames afterward.
                    next_frame = now + FRAME_INTERVAL
        finally:
            self.fb.close()
            if self.console_graphics_mode:
                fcntl.ioctl(self.tty_fd, KDSETMODE, KD_TEXT)
                os.write(self.tty_fd, b"\033[2J\033[H")
            if self.tty_fd is not None:
                os.close(self.tty_fd)
            pygame.quit()

        sys.exit(self.pending_exit_code)


def main():
    JoanJettApp().run()


if __name__ == "__main__":
    main()
