"""Fetches current aircraft state from readsb. Runs directly on the same Pi
as the SDR dongle/readsb (production, per project convention), so this
reads readsb's own JSON file locally rather than over HTTP.

Distance/bearing are computed here from each aircraft's lat/lon against
JOAN JETT's own LOCATION setting -- deliberately not readsb's r_dst/r_dir,
which are relative to *readsb's* internal site-position config and could
drift out of sync with this app's own settings.
"""
import json

import geo

AIRCRAFT_JSON_PATH = "/run/readsb/aircraft.json"

# ADS-B (DO-260B) emitter category -> up to-10-char display label, per user
# spec 2026-09-11. Lives here (not in aircraft_screen.py, where it was
# first added) since flightlog.py needs the same mapping -- shared within
# this app, unlike the deliberate per-app duplication convention between
# sibling apps (bars/loudness/channel38/joanjett) elsewhere in the fleet.
# Falls back to "UNKNOWN" for anything not in this table (matches the
# A0/B0/C0/C6/C7 "unknown/reserved" entries below).
CATEGORY_LABELS = {
    "A0": "UNKNOWN", "A1": "LIGHT", "A2": "SMALL", "A3": "LARGE",
    "A4": "HI VORTEX", "A5": "HEAVY", "A6": "HIGH SPEED", "A7": "HELICOPTER",
    "B0": "UNKNOWN", "B1": "GLIDER", "B2": "BLIMP", "B3": "PARACHUTE",
    "B4": "ULTRALIGHT", "B5": "RESERVED", "B6": "DRONE", "B7": "ROCKET",
    "C0": "UNKNOWN", "C1": "EMER VEH", "C2": "SERV VEH", "C3": "OBSTACLE",
    "C4": "OBSTACLE", "C5": "OBSTACLE", "C6": "UNKNOWN", "C7": "UNKNOWN",
}


def format_category(category):
    return CATEGORY_LABELS.get(category, "UNKNOWN")


def fetch_aircraft(center_lat, center_lon, max_tracked):
    """-> up to max_tracked aircraft dicts, nearest first, or None on a
    read/parse failure -- distinct from an empty list, which means the
    fetch worked but the sky is genuinely quiet (used for the Info HUD's
    STATUS readout)."""
    try:
        with open(AIRCRAFT_JSON_PATH) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    aircraft = []
    for ac in raw.get("aircraft", []):
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None:
            continue  # no position fix yet (e.g. a mode_s-only contact)
        heading = ac.get("track")
        if heading is None:
            heading = ac.get("true_heading")
        if heading is None:
            continue  # can't orient an arrowhead without a heading
        dist_nm, bearing_deg = geo.distance_bearing_nm(lat, lon, center_lat, center_lon)
        # No fallback to the raw hex address (2026-08-25, user request) --
        # a hex code isn't a real callsign, so an aircraft without a
        # "flight" field (no callsign broadcast yet) reads as UNKNOWN.
        callsign = (ac.get("flight") or "").strip() or "UNKNOWN"
        aircraft.append(
            {
                "hex": ac["hex"],
                "callsign": callsign,
                "lat": lat,
                "lon": lon,
                "dist_nm": dist_nm,
                "bearing_deg": bearing_deg,
                "heading_deg": heading,
                "alt_baro": ac.get("alt_baro"),
                "gs": ac.get("gs"),
                "squawk": ac.get("squawk"),
                "category": ac.get("category"),  # e.g. "A7" = rotorcraft
                # readsb decodes 7500/7600/7700 into this field directly --
                # "none" in normal operation, otherwise one of "general"/
                # "lifeguard"/"minfuel"/"nordo"/"unlawful"/"downed".
                "emergency": ac.get("emergency", "none"),
                # bit 0 of readsb's dbFlags is the common military-aircraft
                # convention; absent entirely on most contacts (civil, or
                # readsb builds without a military DB loaded), so this is a
                # best-effort count, not authoritative.
                "military": bool(ac.get("dbFlags", 0) & 1),
            }
        )
    aircraft.sort(key=lambda a: a["dist_nm"])
    return aircraft[:max_tracked]
