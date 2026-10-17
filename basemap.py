"""Map geometry for the radar background, from one of two sources:

  * a prebuilt basemap (data/basemap_<lat>_<lon>.json.gz, made once by
    tools/build_basemap.py) -- full detail, no downloads at all; or
  * mapdata.py's live per-range Overpass fetch -- the original basic map
    (freeways, highways, rivers, big lakes), used anywhere no prebuilt
    basemap exists for the configured location.

Either way the result is the same structure for maprender.py: per layer,
every point of every feature in one (N, 2) numpy array of east/north NM
offsets from the map center, with feature start offsets and bounding boxes
alongside -- whole-layer numpy operations, not per-feature Python loops,
are what keep rendering ~100k features fast enough on a Pi 3B.
"""

import base64
import glob
import gzip
import json
import math
import os
import sys

import numpy as np

import geo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")

# A prebuilt basemap is used only if its center is this close to the
# configured location (~0.03 NM) -- anything else means the location was
# changed in Settings, and the map has to come from the live fetch.
CENTER_MATCH_DEG = 0.0005

# Fallback-path subtypes -> basemap layer names.
_FALLBACK_LAYER = {"pond": "lake"}

_cache = {}


class Layer:
    """coords: (N, 2) float32 NM offsets; offsets: start index of each
    part (feature, or ring for lakes) plus a final end; bbox: (P, 4)
    minx, miny, maxx, maxy per part. Lakes also have rings: ring count per
    lake (first ring is the outline, the rest are islands)."""

    def __init__(self, coords, offsets, rings=None):
        self.coords = coords
        self.offsets = offsets
        self.rings = rings
        starts = offsets[:-1]
        if len(starts):
            self.bbox = np.stack([
                np.minimum.reduceat(coords[:, 0], starts),
                np.minimum.reduceat(coords[:, 1], starts),
                np.maximum.reduceat(coords[:, 0], starts),
                np.maximum.reduceat(coords[:, 1], starts),
            ], axis=1)
        else:
            self.bbox = np.zeros((0, 4), dtype=np.float32)


class Layers:
    """layers[name] -> Layer; show_max_range is the widest RANGE (NM) each
    layer is drawn at (prebuilt maps only)."""

    def __init__(self, layers, show_max_range, source):
        self.layers = layers
        self.show_max_range = show_max_range
        self.source = source


def find_prebuilt(lat, lon):
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "basemap_*.json.gz"))):
        name = os.path.basename(path)[len("basemap_"):-len(".json.gz")]
        try:
            blat, blon = (float(v) for v in name.split("_"))
        except ValueError:
            continue
        if abs(blat - lat) <= CENTER_MATCH_DEG and abs(blon - lon) <= CENTER_MATCH_DEG:
            return path
    return None


def _int32(b64):
    return np.frombuffer(base64.b64decode(b64), dtype="<i4")


def load_prebuilt(path):
    """-> Layers, or None if the file is unreadable or an unknown format
    (then the caller falls back to the live fetch rather than no map)."""
    if path in _cache:
        return _cache[path]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            raw = json.load(f)
        if raw.get("format") != 2:
            raise ValueError(f"unsupported basemap format {raw.get('format')}")
        q = float(raw.get("quant", 1000))
        layers = {}
        for name, packed in raw["layers"].items():
            coords = (_int32(packed["coords"]).reshape(-1, 2) / q).astype(np.float32)
            offsets = _int32(packed["offsets"]).astype(np.int64)
            rings = _int32(packed["rings"]).astype(np.int64) if "rings" in packed else None
            layers[name] = Layer(coords, offsets, rings)
    except (OSError, ValueError, KeyError) as exc:
        print(f"basemap: can't read {path}: {exc}", file=sys.stderr, flush=True)
        return None
    result = Layers(layers, raw.get("show_max_range", {}), "prebuilt")
    _cache[path] = result
    return result


def from_elements(elements, center_lat, center_lon):
    """Live-fetch path: mapdata.fetch_map_elements() output -> Layers."""
    kx = geo.NM_PER_DEG_LAT * math.cos(math.radians(center_lat))
    parts = {}
    for el in elements:
        name = _FALLBACK_LAYER.get(el.get("subtype"), el.get("subtype", "primary"))
        pts = np.asarray(el["points"], dtype=np.float64)
        nm = np.empty((len(pts), 2), dtype=np.float32)
        nm[:, 0] = (pts[:, 1] - center_lon) * kx
        nm[:, 1] = (pts[:, 0] - center_lat) * geo.NM_PER_DEG_LAT
        if name == "lake" and not np.array_equal(nm[0], nm[-1]):
            # mapdata decimates long ways, so a lake outline may no longer
            # end where it started -- close it for the fill.
            nm = np.vstack([nm, nm[:1]])
        parts.setdefault(name, []).append(nm)
    layers = {}
    for name, arrays in parts.items():
        offsets = np.cumsum([0] + [len(a) for a in arrays]).astype(np.int64)
        rings = np.ones(len(arrays), dtype=np.int64) if name == "lake" else None
        layers[name] = Layer(np.concatenate(arrays), offsets, rings)
    return Layers(layers, {}, "live")
