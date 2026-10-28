"""Builds data/aircraft_types.csv (ICAO type designator -> readable name)
from readsb's aircraft database, for military_screen.py's AIRCRAFT column.

    python3 tools/build_type_names.py /usr/local/share/readsb-db/aircraft.csv.gz

The database (tar1090-db, https://github.com/wiedehopf/tar1090-db/tree/csv)
is ';'-separated: hex;registration;type;flags;description;year;owner. Its
description is per-airframe and not always spelled the same way (e.g.
"NORTHROP T-38 Talon" vs "Northrop T-38C Talon"), so each type code gets
its most common description, uppercased. The output is committed, so a
host without the database (or a fresh clone in another city) still gets
names -- only rebuild it when new types start showing up as bare codes.
Stdlib only, same as build_basemap.py.
"""
import collections
import csv
import gzip
import sys
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "aircraft_types.csv"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    names = collections.defaultdict(collections.Counter)
    with gzip.open(sys.argv[1], "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split(";")
            if len(parts) < 5 or not parts[2] or not parts[4].strip():
                continue
            names[parts[2].strip().upper()][parts[4].strip().upper()] += 1
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["type", "name"])
        for code in sorted(names):
            writer.writerow([code, names[code].most_common(1)[0][0]])
    print(f"wrote {len(names)} types to {OUT_PATH}")


if __name__ == "__main__":
    main()
