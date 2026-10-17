"""Dev-only: render the map layer and save it as a PNG, headless. No fb0, no
console attach, no tty1 -- just for visually verifying the map render itself
before wiring it into the real display pipeline.

    python3 dev_snapshot.py [out.png] [range_nm]

Uses the prebuilt basemap for the configured location if there is one (see
tools/build_basemap.py), otherwise the live per-range fetch, same as the app."""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

import basemap  # noqa: E402
import config  # noqa: E402
import mapdata  # noqa: E402
import maprender  # noqa: E402

pygame.init()

s = config.Settings()
out_path = sys.argv[1] if len(sys.argv) > 1 else "map_snapshot.png"
range_nm = float(sys.argv[2]) if len(sys.argv) > 2 else s.range_multiplier * 4

layers = None
prebuilt = basemap.find_prebuilt(s.location_lat, s.location_lon)
if prebuilt:
    print(f"Using prebuilt basemap {prebuilt}")
    layers = basemap.load_prebuilt(prebuilt)
if layers is None:
    print(f"Fetching map elements for {s.location_name} ({s.location_lat}, {s.location_lon}), "
          f"range {range_nm}NM ...")
    elements = mapdata.fetch_map_elements(s.location_lat, s.location_lon, range_nm * 1.3)
    print(f"Got {len(elements)} way(s).")
    layers = basemap.from_elements(elements, s.location_lat, s.location_lon)

print("Rendering ...")
surface = maprender.render_map_surface(layers, range_nm, s.color_scheme)
pygame.image.save(surface, out_path)
print(f"Saved {out_path}")
