#!/usr/bin/env python3
"""Builds a detailed, prebuilt basemap for one location -- run once (on a
PC or a Pi), commit the output, and JOAN JET draws every RANGE from it with
no further downloads.

Without a prebuilt basemap for its location, JOAN JET falls back to
mapdata.py's live per-range Overpass fetch (freeways, highways, rivers and
big lakes only) -- this tool is an optional upgrade, not a requirement.

    python3 tools/build_basemap.py                 # location from settings.ini
    python3 tools/build_basemap.py --lat 32.8348 --lon -96.6886

Downloads OpenStreetMap data from the Overpass API in small tiles (never one
huge query -- see mapdata.py's 2026-08-25 incident notes), keeps each layer
only out to the distance where JOAN JET actually shows it, simplifies the
geometry to what a 720x480 CRT can resolve, and writes
data/basemap_<lat>_<lon>.json.gz. Uses only the Python standard library.

Map data (c) OpenStreetMap contributors, available under the Open Database
License (ODbL) -- https://www.openstreetmap.org/copyright
"""

import argparse
import array
import base64
import configparser
import datetime
import gzip
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "JoanJett-ADSB-Radar basemap builder (personal hobby project)"

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
# Raw Overpass responses, kept so a rebuild (e.g. after changing the
# simplification) doesn't re-download anything. Safe to delete; --refresh
# ignores it.
RAW_CACHE_DIR = os.path.join(APP_DIR, "cache", "basemap_raw")

NM_PER_DEG_LAT = 60.0
MAX_RANGE_NM = 60.0  # config.MAX_RANGE_MULTIPLIER * 4
# The screen corners sit ~1.32x the RANGE from center (720x480 frame,
# 340px outer ring, NTSC pixel aspect) -- data has to reach that far.
CORNER_FACTOR = 1.33

# Layer tiers. Each tier is one Overpass union query per tile, kept out to
# the radius where its layers are still drawn: show_max_range (NM) is the
# widest RANGE a layer appears at (basemap.py hides it beyond that), and the
# data only needs to cover that range's screen corners.
TIERS = [
    {
        "name": "wide",
        "show_max_range": MAX_RANGE_NM,
        "query": [
            'way["highway"~"^(motorway|trunk)$"]',
            'way["waterway"~"^(river|canal)$"]',
            'way["natural"="water"]["name"]',
            'relation["natural"="water"]["name"]',
            'way["aeroway"="runway"]',
        ],
    },
    {
        "name": "mid",
        "show_max_range": 32.0,
        "query": [
            'way["highway"="primary"]',
            'way["railway"~"^(rail|light_rail)$"][!"service"]',
        ],
    },
    {
        "name": "near",
        "show_max_range": 16.0,
        "query": [
            'way["highway"="secondary"]',
            'way["aeroway"="taxiway"]',
        ],
    },
    {
        "name": "close",
        "show_max_range": 8.0,
        "query": [
            'way["highway"="tertiary"]',
        ],
    },
]

# County lines come back as relations with member geometry, one query for
# the whole area (there are only a few dozen counties in range).
COUNTY_QUERY = 'relation["boundary"="administrative"]["admin_level"="6"]'

TILE_DEG = 0.75
PAUSE_SEC = 2.0
MAX_TRIES = 5

# Lakes smaller than this (bounding span) are dropped -- farm ponds, mostly.
MIN_LAKE_SPAN_NM = 0.15
QUANT = 1000  # coordinates stored as integer thousandths of a NM


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def settings_location():
    cp = configparser.ConfigParser()
    for name in ("settings.ini", "settings.example.ini"):
        path = os.path.join(APP_DIR, name)
        if cp.read(path):
            return cp.getfloat("location", "lat"), cp.getfloat("location", "lon")
    raise SystemExit("no settings.ini -- pass --lat and --lon")


def bbox_for(lat, lon, radius_nm):
    dlat = radius_nm / NM_PER_DEG_LAT
    dlon = radius_nm / (NM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def tiles(bbox):
    s, w, n, e = bbox
    rows = max(1, math.ceil((n - s) / TILE_DEG))
    cols = max(1, math.ceil((e - w) / TILE_DEG))
    dlat, dlon = (n - s) / rows, (e - w) / cols
    for r in range(rows):
        for c in range(cols):
            yield (s + r * dlat, w + c * dlon, s + (r + 1) * dlat, w + (c + 1) * dlon)


def overpass(query, refresh=False):
    cache_file = os.path.join(RAW_CACHE_DIR, hashlib.sha1(query.encode()).hexdigest()[:20] + ".json.gz")
    if not refresh and os.path.exists(cache_file):
        with gzip.open(cache_file, "rt", encoding="utf-8") as f:
            return json.load(f)
    raw = _overpass_fetch(query)
    os.makedirs(RAW_CACHE_DIR, exist_ok=True)
    with gzip.open(cache_file + ".tmp", "wt", encoding="utf-8") as f:
        json.dump(raw, f)
    os.replace(cache_file + ".tmp", cache_file)
    time.sleep(PAUSE_SEC)
    return raw


def _overpass_fetch(query):
    data = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            wait = PAUSE_SEC * 5 * attempt
            log(f"  overpass error ({exc}); retry {attempt}/{MAX_TRIES} in {wait:.0f}s")
            time.sleep(wait)
    raise SystemExit("Overpass kept failing -- try again later")


def union_query(selectors, bbox):
    b = "{:.5f},{:.5f},{:.5f},{:.5f}".format(*bbox)
    body = "\n".join(f"  {sel}({b});" for sel in selectors)
    return f"[out:json][timeout:120];\n(\n{body}\n);\nout geom;"


class Projector:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.kx = NM_PER_DEG_LAT * math.cos(math.radians(lat))

    def __call__(self, pt):
        return ((pt["lon"] - self.lon) * self.kx, (pt["lat"] - self.lat) * NM_PER_DEG_LAT)


def tolerance(x, y):
    # Half a screen pixel at the closest RANGE where this point can be on
    # screen at all: a point d NM out only appears once RANGE >= d/1.33,
    # where one pixel is RANGE/340 NM.
    d = math.hypot(x, y)
    return max(0.004, d / (CORNER_FACTOR * 340.0) * 0.5)


def simplify(points):
    """Douglas-Peucker with a distance-adaptive tolerance (see tolerance())."""
    if len(points) <= 2:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = points[i], points[j]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        tol = min(tolerance(ax, ay), tolerance(bx, by))
        worst, worst_k = -1.0, None
        for k in range(i + 1, j):
            px, py = points[k]
            if seg < 1e-9:
                # closed ring (lake outline): both ends are the same point,
                # so measure from that point instead of a zero-length line
                dist = math.hypot(px - ax, py - ay)
            else:
                dist = abs(dx * (ay - py) - dy * (ax - px)) / seg
            if dist > worst:
                worst, worst_k = dist, k
        if worst_k is not None and worst > tol:
            keep[worst_k] = True
            stack.append((i, worst_k))
            stack.append((worst_k, j))
    return [p for p, k in zip(points, keep) if k]


def flat(points):
    out = []
    for x, y in points:
        out.append(round(x * QUANT))
        out.append(round(y * QUANT))
    return out


def classify(tags):
    hw = tags.get("highway")
    if hw in ("motorway", "trunk", "primary", "secondary", "tertiary"):
        return hw
    ww = tags.get("waterway")
    if ww in ("river", "canal"):
        return ww
    if tags.get("natural") == "water":
        return "lake"
    aw = tags.get("aeroway")
    if aw in ("runway", "taxiway"):
        return aw
    if tags.get("railway") in ("rail", "light_rail"):
        return "rail"
    return None


def join_rings(ways):
    """Joins multipolygon member ways (lists of points) into closed rings."""
    rings, open_ = [], [list(w) for w in ways if len(w) >= 2]
    while open_:
        ring = open_.pop()
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for k, w in enumerate(open_):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                open_.pop(k)
                changed = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def span(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def within(points, radius):
    return any(abs(x) <= radius and abs(y) <= radius for x, y in points)


def build(lat, lon, refresh=False):
    proj = Projector(lat, lon)
    layers = {}
    seen = set()
    counts = {}

    def add(layer, item):
        layers.setdefault(layer, []).append(item)
        counts[layer] = counts.get(layer, 0) + 1

    for tier in TIERS:
        radius = tier["show_max_range"] * CORNER_FACTOR
        tile_list = list(tiles(bbox_for(lat, lon, radius)))
        log(f"tier {tier['name']}: {radius:.0f} NM radius, {len(tile_list)} tiles")
        for n, tb in enumerate(tile_list, 1):
            raw = overpass(union_query(tier["query"], tb), refresh)
            elements = raw.get("elements", [])
            log(f"  tile {n}/{len(tile_list)}: {len(elements)} elements")
            for el in elements:
                key = (el["type"], el["id"])
                if key in seen:
                    continue
                seen.add(key)
                layer = classify(el.get("tags", {}))
                if layer is None:
                    continue
                if el["type"] == "way":
                    pts = [proj(p) for p in el.get("geometry", [])]
                    if len(pts) < 2 or not within(pts, radius):
                        continue
                    if layer == "lake":
                        if pts[0] != pts[-1] or span(pts) < MIN_LAKE_SPAN_NM:
                            continue
                        add("lake", [flat(simplify(pts))])
                    else:
                        add(layer, flat(simplify(pts)))
                elif el["type"] == "relation" and layer == "lake":
                    outers = [[proj(p) for p in m["geometry"]] for m in el.get("members", [])
                              if m.get("type") == "way" and m.get("role") == "outer" and "geometry" in m]
                    inners = [[proj(p) for p in m["geometry"]] for m in el.get("members", [])
                              if m.get("type") == "way" and m.get("role") == "inner" and "geometry" in m]
                    holes = [flat(simplify(r)) for r in join_rings(inners) if span(r) >= MIN_LAKE_SPAN_NM]
                    for ring in join_rings(outers):
                        if span(ring) >= MIN_LAKE_SPAN_NM and within(ring, radius):
                            add("lake", [flat(simplify(ring))] + holes)

    radius = MAX_RANGE_NM * CORNER_FACTOR
    log("county lines")
    raw = overpass(union_query([COUNTY_QUERY], bbox_for(lat, lon, radius)), refresh)
    border_ways = set()
    for el in raw.get("elements", []):
        for m in el.get("members", []):
            if m.get("type") != "way" or "geometry" not in m or m["ref"] in border_ways:
                continue
            border_ways.add(m["ref"])
            pts = [proj(p) for p in m["geometry"]]
            if len(pts) >= 2 and within(pts, radius):
                add("county", flat(simplify(pts)))

    return {
        "format": 2,
        "center": [lat, lon],
        "max_range_nm": MAX_RANGE_NM,
        "quant": QUANT,
        "built": datetime.date.today().isoformat(),
        "attribution": "Map data (c) OpenStreetMap contributors, ODbL",
        "show_max_range": {
            **{lyr: t["show_max_range"] for t in TIERS for lyr in
               {"wide": ("motorway", "trunk", "river", "canal", "lake", "runway"),
                "mid": ("primary", "rail"),
                "near": ("secondary", "taxiway"),
                "close": ("tertiary",)}[t["name"]]},
            "county": MAX_RANGE_NM,
        },
        "layers": {name: pack(name, items) for name, items in layers.items()},
    }, counts


def _b64(values):
    a = array.array("i", values)
    if sys.byteorder != "little":
        a.byteswap()
    return base64.b64encode(a.tobytes()).decode("ascii")


def pack(layer, items):
    """Packs a layer as base64 little-endian int32 blocks, so the app can
    load it straight into numpy instead of parsing ~a million JSON numbers
    (12.8 s on a Pi 3B with plain JSON lists):
      coords  -- every point of every feature, x0,y0,x1,y1,... (thousandths of a NM)
      offsets -- point index where each feature starts, plus a final end index
    Lakes are made of rings (outline + islands), so for them offsets index
    rings and "rings" gives how many rings each lake has."""
    coords, offsets = [], [0]
    rings = []
    for item in items:
        for part in (item if layer == "lake" else [item]):
            coords.extend(part)
            offsets.append(len(coords) // 2)
        if layer == "lake":
            rings.append(len(item))
    packed = {"coords": _b64(coords), "offsets": _b64(offsets)}
    if layer == "lake":
        packed["rings"] = _b64(rings)
    return packed


def output_path(lat, lon):
    return os.path.join(DATA_DIR, f"basemap_{lat:.4f}_{lon:.4f}.json.gz")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--refresh", action="store_true", help="re-download instead of reusing cached raw responses")
    args = ap.parse_args()
    if (args.lat is None) != (args.lon is None):
        ap.error("--lat and --lon go together")
    lat, lon = (args.lat, args.lon) if args.lat is not None else settings_location()

    start = time.monotonic()
    basemap, counts = build(lat, lon, args.refresh)
    os.makedirs(DATA_DIR, exist_ok=True)
    path = output_path(lat, lon)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(basemap, f, separators=(",", ":"))
    os.replace(tmp, path)
    log("")
    for layer in sorted(counts):
        log(f"  {layer:10s} {counts[layer]:6d}")
    log(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB) in {time.monotonic() - start:.0f}s")


if __name__ == "__main__":
    main()
