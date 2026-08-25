"""Shared lat/lon <-> screen-pixel projection, used by every layer (map, radar
rings, planes) so they all agree on where a given coordinate lands on screen.

Flat-earth approximation centered on the configured LOCATION -- fine at the
tens-of-NM scale this app operates at, no need for a real map projection."""

import math

NM_PER_DEG_LAT = 60.0


def latlon_to_nm(lat, lon, center_lat, center_lon):
    """-> (east_nm, north_nm) offset from center."""
    north_nm = (lat - center_lat) * NM_PER_DEG_LAT
    east_nm = (lon - center_lon) * NM_PER_DEG_LAT * math.cos(math.radians(center_lat))
    return east_nm, north_nm


def nm_to_px(east_nm, north_nm, px_per_nm, center_px):
    x = center_px[0] + east_nm * px_per_nm
    y = center_px[1] - north_nm * px_per_nm  # screen y grows downward; north is up
    return x, y


def px_per_nm(range_nm, outer_radius_px):
    return outer_radius_px / range_nm


def latlon_to_px(lat, lon, center_lat, center_lon, range_nm, center_px, outer_radius_px):
    e, n = latlon_to_nm(lat, lon, center_lat, center_lon)
    return nm_to_px(e, n, px_per_nm(range_nm, outer_radius_px), center_px)


def decimal_to_dms(value, positive_dir, negative_dir):
    """32.834... -> 'N 32°50'05\"' -- spec's LOCATION display format.
    Degrees has no leading zeros; minutes/seconds keep the conventional
    2-digit zero-pad (2026-08-25: dropped only the degrees padding, per
    user request -- 032/096 read oddly, but 05'/09" is standard DMS)."""
    direction = positive_dir if value >= 0 else negative_dir
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = int(round((minutes_full - minutes) * 60))
    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        degrees += 1
    return f"{direction} {degrees}°{minutes:02d}'{seconds:02d}\""


def distance_bearing_nm(lat, lon, center_lat, center_lon):
    """Great-circle distance (NM) and bearing (deg, 0=N/clockwise) from center.
    Used later by the Planes/Info layers -- computed locally rather than trusting
    readsb's own site-position config to stay in sync with this app's settings."""
    R_NM = 3440.065
    lat1, lon1, lat2, lon2 = map(math.radians, (center_lat, center_lon, lat, lon))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    dist_nm = 2 * R_NM * math.asin(math.sqrt(a))
    bearing = math.degrees(
        math.atan2(
            math.sin(dlon) * math.cos(lat2),
            math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon),
        )
    )
    return dist_nm, (bearing + 360) % 360
