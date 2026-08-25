"""Fetches road/water vector geometry from the Overpass API for the map layer.

Deliberately NOT raster OSM tiles -- we want clean line geometry we draw and
color ourselves (blue on black, no labels), which a recolored raster tile
can't give us. Location only changes via the Settings screen, so results are
cached to disk indefinitely per (location, radius) rather than refetched
per frame or per run.
"""

import hashlib
import json
import math
import os

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "JoanJett-ADSB-Radar/0.1 (personal hobby project)"

# Freeways only, by default. INCIDENT 2026-08-25: the first version of this
# query requested way["highway"] unconditionally (every tag value, including
# residential/service/footway/track) and only filtered by ROAD_HIGHWAY_TYPES
# *after* downloading -- across a ~17NM radius of the DFW metroplex that
# pulled down an enormous payload and drove production into a resource
# exhaustion state that killed SSH (banner-exchange timeouts, ping still
# fine). The filter has to happen in the Overpass query itself so the huge
# payload never gets downloaded/parsed in the first place. Easy to widen
# later (e.g. add primary/secondary) once this is proven safe at a small
# radius -- widen one step at a time, not back to "every highway tag."
# Link/ramp variants (motorway_link/trunk_link) dropped 2026-08-25: measured
# live at DFW, they were 4413 of 11736 elements (37%) -- short interchange
# ramp segments that read as tangled clutter at freeway interchanges rather
# than the clean through-lines in the mockup. Through-lanes only.
# "primary" added 2026-08-25 per user request (numbered state/US highways are
# typically tagged highway=primary in OSM) to fill in a bit more detail --
# still no _link/ramp variants, and widening one step at a time as planned.
ROAD_HIGHWAY_TYPES = ("motorway", "trunk", "primary")

# Same problem as ROAD_HIGHWAY_TYPES: an unrestricted waterway["waterway"]
# query matches every mapped ditch/drain/intermittent stream, not just real
# rivers -- found live 2026-08-25 when the first successful render turned out
# to be almost entirely a dense creek/drainage network (dendritic branching),
# not roads, because North Texas has huge numbers of tiny mapped streams.
# Restricting to river/canal only fixes both the visual (matches the mockup's
# sparse, deliberate line work) and cuts a large chunk of payload size.
WATERWAY_TYPES = ("river", "canal")

# Hard safety cap -- abort rather than let a too-wide query silently hang the
# box again. Sized from real measurement on production 2026-08-25: a 12NM
# freeways+water fetch around DFW (an unusually freeway-dense metro) peaked at
# 136MiB RSS against ~620MiB available -- comfortably safe. 20MB raw response
# leaves real headroom above that while still blocking a repeat of the
# original incident (the unfiltered query hit 24MB+ just for the *filtered*
# version at 16.8NM; the original unfiltered-tags query was far larger still).
# This cap matters most as a backstop against AUTO RANGE picking something
# huge (a single far-out aircraft could in principle push RANGE well past the
# radii tested here) -- not just normal settings-screen use.
# Raised 2026-08-25 after the 26MB cap tripped on the very first live RANGE
# increase (12NM -> 16NM default+1 step, 30MB response) and silently blanked
# the map -- measured real peak RSS for that exact fetch at 233.1MiB (18098
# elements) against ~660MiB available, comfortably safe. 40MB leaves further
# headroom for one or two more range steps. Still a hard cap, not unlimited
# -- main.py additionally handles a cap trip gracefully now (rolls back to
# the previous range + shows an error, instead of silently going blank) as
# defense in depth, since any fixed cap will eventually be hit again by a
# large enough RANGE (AUTO RANGE especially).
MAX_RESPONSE_BYTES = 40 * 1024 * 1024

# Ways longer than this many points get decimated (every Nth point kept) --
# cuts memory/render cost for long freeways with dense node spacing, with a
# barely-visible cost to line smoothness on a CRT.
MAX_POINTS_PER_WAY = 40

# natural=water has no size filter in Overpass QL without a much more complex
# query, so small ponds get dropped client-side by bounding-box span instead.
# Measured live 2026-08-25: 2487 of 2549 natural=water ways around DFW (97.6%)
# were under this span -- almost all farm/stock ponds, not real lakes.
MIN_WATER_SPAN_DEG = 0.02  # ~1.2NM at this latitude

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")


class MapFetchError(Exception):
    pass


def _cache_path(lat, lon, radius_nm):
    # Filter sets are part of the key -- otherwise changing ROAD_HIGHWAY_TYPES/
    # WATERWAY_TYPES (as happened 2026-08-25, fixing the creek-network bug)
    # would silently keep serving a stale cached response from the old query.
    key = (
        f"{lat:.4f}_{lon:.4f}_{radius_nm:.1f}_"
        f"{','.join(ROAD_HIGHWAY_TYPES)}_{','.join(WATERWAY_TYPES)}"
    )
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"map_{digest}.json")


def _bbox(lat, lon, radius_nm):
    lat_delta = radius_nm / 60.0
    lon_delta = radius_nm / (60.0 * math.cos(math.radians(lat)))
    south, north = lat - lat_delta, lat + lat_delta
    west, east = lon - lon_delta, lon + lon_delta
    return south, west, north, east


def _build_query(lat, lon, radius_nm):
    south, west, north, east = _bbox(lat, lon, radius_nm)
    bbox = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    highway_regex = "^(" + "|".join(ROAD_HIGHWAY_TYPES) + ")$"
    waterway_regex = "^(" + "|".join(WATERWAY_TYPES) + ")$"
    return f"""
[out:json][timeout:25];
(
  way["highway"~"{highway_regex}"]({bbox});
  way["waterway"~"{waterway_regex}"]({bbox});
  way["natural"="water"]({bbox});
);
out geom;
""".strip()


def _decimate(points):
    if len(points) <= MAX_POINTS_PER_WAY:
        return points
    step = math.ceil(len(points) / MAX_POINTS_PER_WAY)
    decimated = points[::step]
    if decimated[-1] != points[-1]:
        decimated.append(points[-1])
    return decimated


def fetch_map_elements(lat, lon, radius_nm, use_cache=True):
    """-> list of {"kind": "road"|"water", "points": [(lat, lon), ...]}"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = _cache_path(lat, lon, radius_nm)

    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            raw = json.load(f)
    else:
        query = _build_query(lat, lon, radius_nm)
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=45,
            stream=True,
        )
        resp.raise_for_status()
        content = resp.content  # already fully buffered by requests; check size before json.loads
        if len(content) > MAX_RESPONSE_BYTES:
            raise MapFetchError(
                f"Overpass response was {len(content)} bytes (cap {MAX_RESPONSE_BYTES}) -- "
                f"reduce radius_nm or narrow ROAD_HIGHWAY_TYPES before retrying"
            )
        raw = json.loads(content)
        with open(cache_file, "w") as f:
            json.dump(raw, f)

    elements = []
    for el in raw.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        tags = el.get("tags", {})
        is_pond_candidate = tags.get("natural") == "water"
        kind = "water" if ("waterway" in tags or is_pond_candidate) else "road"
        # Specific OSM tag value preserved (not just road/water) so the
        # renderer can weight line width by importance -- motorway thicker
        # than primary, etc. "pond" is its own natural=water subtype, kept
        # distinct from river/canal even though both render as "water".
        if kind == "water":
            subtype = tags.get("waterway") or "pond"
        else:
            subtype = tags.get("highway", "road")
        points = [(pt["lat"], pt["lon"]) for pt in el["geometry"]]
        if len(points) < 2:
            continue
        if is_pond_candidate:
            lats = [p[0] for p in points]
            lons = [p[1] for p in points]
            span = max(max(lats) - min(lats), max(lons) - min(lons))
            if span < MIN_WATER_SPAN_DEG:
                continue
        elements.append({"kind": kind, "subtype": subtype, "points": _decimate(points)})
    return elements
