"""Logs every aircraft that comes within LOG_RADIUS_NM of the home
location, one row per pass, to a local CSV plus a durable copy on MP --
see the module docstring on FlightLog for why MP, not wherever JOAN JETT
happens to be running.

Deliberately simple per user request 2026-09-11: no GPS track is kept,
just the closest point of approach (date/time/altitude at minimum
distance) plus callsign/type/squawk as they read at that same moment.
"""
import csv
import datetime
import io
import subprocess
import sys
from pathlib import Path

import aircraft

LOG_RADIUS_NM = 9

SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_LOG_PATH = SCRIPT_DIR / "data" / "flightlog.csv"
LOG_HEADER = ["date", "time", "callsign", "type", "altitude_ft", "squawk", "military"]
# "Y"/"N" from readsb's dbFlags bit 0 (see aircraft.fetch_aircraft), or
# "N/A" for any row logged before this column existed 2026-09-12 -- kept
# distinct from "N" so old rows read as "not measured", not "measured and
# not military". Bit 0 is the only military-adjacent signal readsb
# exposes; there's no separate "government (non-military)" flag to also
# capture here, so a Coast Guard/FAA-type aircraft with no military
# ICAO-address flag won't be caught by this column.

# Dedicated key, authorized on MP with a forced command
# ("cat >> .../joanjett_flightlog.csv") restricted to appending to that
# one file -- see project_joan_jett memory for the setup. Whatever puppet
# JOAN JETT runs on next just needs its own keypair generated and added
# to MP's authorized_keys the same way; the app code itself doesn't care
# which host it's running on. Lives under SCRIPT_DIR rather than a
# user's home directory -- this process can run as either `metalshop`
# (direct tty1 exec) or `root` (via the `sudo openvt` launcher path, see
# bars.py's console-attach notes), so a `~`-relative path would resolve
# differently depending on which one launched it. Never committed to
# git -- see .gitignore.
REMOTE_HOST = "metalshop@192.168.68.77"  # MP (masterofpuppets)
REMOTE_SSH_KEY = str(SCRIPT_DIR / "flightlog_push_key")
# Same reasoning as REMOTE_SSH_KEY above -- a fixed, SCRIPT_DIR-relative
# known_hosts file so host-key trust doesn't depend on which UID's home
# directory the process happens to be using this run.
KNOWN_HOSTS_PATH = str(SCRIPT_DIR / "flightlog_known_hosts")

# Separate, read-only key -- forced to `cat` (not `cat >>`) MP's canonical
# copy, so it can never be used to write or run anything else. Added
# 2026-09-12 when a second SDR-equipped host (production) started running
# JOAN JETT alongside this one: both push every pass to the same MP file,
# so a host that wants to *display* merged stats needs a way to read it
# back. Presence of this file is what opts a host into syncing at all --
# see sync_and_merge() -- so a host that's never had one generated (like
# production right now, which only pushes) just no-ops every launch.
REMOTE_PULL_KEY = SCRIPT_DIR / "flightlog_pull_key"

# Two independent radios can each log their own closest-approach snapshot
# of the same real pass a few seconds apart -- this is how close two
# same-callsign rows need to be to count as "the same event" rather than
# two genuinely separate passes (e.g. the flight school's helicopters
# looping the pattern every 20-90 minutes). Chosen 2026-09-12 as a
# balance -- tight enough to not weld together real repeat visits, loose
# enough to cover any real-world timing gap between two colocated radios'
# own closest-point-of-approach detection for one aircraft.
DEDUP_WINDOW = datetime.timedelta(minutes=5)


def _format_row(rec):
    alt = rec["alt_ft"]
    squawk = rec["squawk"]
    return [
        rec["time"].strftime("%Y-%m-%d"),
        rec["time"].strftime("%H:%M:%S"),
        rec["callsign"],
        aircraft.format_category(rec["category"]),
        str(alt) if alt is not None else "N/A",
        str(squawk) if squawk else "N/A",
        "Y" if rec["military"] else "N",
    ]


class FlightLog:
    def __init__(self):
        self._in_progress = {}  # hex -> {callsign, category, min_dist_nm, alt_ft, squawk, time}
        self._pending_pushes = []  # Popen handles for fire-and-forget MP pushes, reaped opportunistically
        LOCAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not LOCAL_LOG_PATH.exists():
            with open(LOCAL_LOG_PATH, "w", newline="") as f:
                csv.writer(f).writerow(LOG_HEADER)

    def update(self, live_aircraft, now=None):
        """Call once per frame with the same live_aircraft list already
        fetched for display (aircraft.fetch_aircraft's output) -- doesn't
        fetch its own data. now= is a test-injection hook only."""
        now = now or datetime.datetime.now()
        still_close = set()
        for ac in live_aircraft or []:
            if ac["dist_nm"] > LOG_RADIUS_NM:
                continue
            still_close.add(ac["hex"])
            rec = self._in_progress.get(ac["hex"])
            if rec is None or ac["dist_nm"] < rec["min_dist_nm"]:
                self._in_progress[ac["hex"]] = {
                    "callsign": ac["callsign"],
                    "category": ac.get("category"),
                    "min_dist_nm": ac["dist_nm"],
                    "alt_ft": ac.get("alt_baro"),
                    "squawk": ac.get("squawk"),
                    "military": ac.get("military", False),
                    "time": now,
                }
        # A pass finalizes the instant its aircraft is no longer within
        # LOG_RADIUS_NM this frame -- either it flew back out past the
        # radius while still visible, or it dropped off ADS-B entirely
        # (out of range/landed/lost signal) while still close. Both read
        # the same way here: its hex just isn't in still_close anymore.
        for hex_id in list(self._in_progress):
            if hex_id not in still_close:
                self._finalize(hex_id)

    def _finalize(self, hex_id):
        rec = self._in_progress.pop(hex_id)
        row = _format_row(rec)
        self._write_local(row)
        self._push_to_mp(row)

    def _write_local(self, row):
        with open(LOCAL_LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow(row)

    def _push_to_mp(self, row):
        """Fire-and-forget: appends the same row to MP's copy over SSH
        (forced-command key, see module docstring) without blocking the
        render loop if MP or the network is briefly unreachable -- the
        local file (just written) is the safety net either way."""
        self._reap_pushes()
        line = ",".join(row) + "\n"
        try:
            proc = subprocess.Popen(
                [
                    "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", f"UserKnownHostsFile={KNOWN_HOSTS_PATH}",
                    "-i", REMOTE_SSH_KEY, REMOTE_HOST,
                ],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            print(f"flightlog: push to MP failed to start: {exc}", file=sys.stderr)
            return
        try:
            proc.stdin.write(line.encode())
            proc.stdin.close()
        except OSError as exc:
            print(f"flightlog: push to MP failed mid-write: {exc}", file=sys.stderr)
        self._pending_pushes.append(proc)

    def _reap_pushes(self):
        still_running = []
        for proc in self._pending_pushes:
            if proc.poll() is None:
                still_running.append(proc)
            elif proc.returncode != 0:
                print(f"flightlog: push to MP exited {proc.returncode}", file=sys.stderr)
        self._pending_pushes = still_running


def _parse_dt(row):
    return datetime.datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M:%S")


def _valid_row(row):
    """Guards against a malformed line in either CSV (a short/garbled row
    reads back from csv.DictReader with most fields None) breaking the
    whole merge -- skip it instead of crashing sync_and_merge()."""
    try:
        _parse_dt(row)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(row.get("callsign"))


def _pick_representative(cluster):
    """Prefer a row with a real (non-N/A) squawk; among ties (both real or
    both N/A), the earliest chronologically -- per user's exact 2026-09-12
    request for how to resolve two radios' differing snapshots of the same
    real pass."""
    if len(cluster) == 1:
        return cluster[0]
    with_squawk = [r for r in cluster if r["squawk"] != "N/A"]
    candidates = with_squawk or cluster
    return min(candidates, key=_parse_dt)


def _dedup(rows):
    """Collapse rows that are the same callsign and fall within
    DEDUP_WINDOW of the previous kept row in that callsign's own
    chronological sequence -- see DEDUP_WINDOW's comment for why. Rows are
    clustered by consecutive gap, not by distance from the cluster's first
    row, so a real cross-radio duplicate pair (normally just 2 rows,
    seconds apart) merges cleanly without needing every row in a longer
    same-day sequence to be within the window of the *first* one."""
    valid = [r for r in rows if _valid_row(r)]
    by_callsign = {}
    for row in valid:
        by_callsign.setdefault(row["callsign"], []).append(row)

    merged = []
    for group in by_callsign.values():
        group.sort(key=_parse_dt)
        cluster = [group[0]]
        for row in group[1:]:
            if _parse_dt(row) - _parse_dt(cluster[-1]) <= DEDUP_WINDOW:
                cluster.append(row)
            else:
                merged.append(_pick_representative(cluster))
                cluster = [row]
        merged.append(_pick_representative(cluster))

    merged.sort(key=_parse_dt)
    return merged


def _fetch_remote_rows():
    """-> list of row-dicts from MP's canonical copy, or None on any
    failure (unreachable, timeout, bad key, unparsable). The forced
    command on the other end ignores whatever we'd otherwise pass -- it
    always just cats the one file, so there's no command argument here."""
    try:
        result = subprocess.run(
            [
                "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={KNOWN_HOSTS_PATH}",
                "-i", str(REMOTE_PULL_KEY), REMOTE_HOST,
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"flightlog: pull from MP failed: {exc}", file=sys.stderr, flush=True)
        return None
    if result.returncode != 0:
        print(f"flightlog: pull from MP exited {result.returncode}: {result.stderr.strip()}",
              file=sys.stderr, flush=True)
        return None
    try:
        return list(csv.DictReader(io.StringIO(result.stdout)))
    except csv.Error as exc:
        print(f"flightlog: pull from MP returned unparsable data: {exc}", file=sys.stderr, flush=True)
        return None


def sync_and_merge():
    """Best-effort, called once at launch (main.py, before the app starts
    reading LOCAL_LOG_PATH for its stats screens). Pulls MP's canonical
    copy -- every pass either radio has ever pushed -- merges it with this
    host's own local rows (covers a push that was lost to a network
    hiccup and never reached MP), collapses cross-radio duplicates of the
    same real pass, and rewrites the local CSV so the existing
    local-file-only stats-screen code needs no changes.

    No-ops entirely, leaving the local file exactly as it was, if this
    host was never set up to sync (no REMOTE_PULL_KEY -- true for
    production right now, which only pushes) or if MP is unreachable.
    Must never raise or block startup for longer than the SSH timeout
    above."""
    if not REMOTE_PULL_KEY.exists():
        return
    remote_rows = _fetch_remote_rows()
    if remote_rows is None:
        return

    try:
        with open(LOCAL_LOG_PATH, newline="") as f:
            local_rows = list(csv.DictReader(f))
    except OSError:
        local_rows = []

    try:
        merged = _dedup(local_rows + remote_rows)
    except (KeyError, ValueError) as exc:
        print(f"flightlog: merge failed, leaving local log untouched: {exc}", file=sys.stderr, flush=True)
        return

    tmp_path = LOCAL_LOG_PATH.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(LOG_HEADER)
        for row in merged:
            writer.writerow([row[col] for col in LOG_HEADER])
    tmp_path.replace(LOCAL_LOG_PATH)  # atomic -- never leaves a half-written local log
    print(f"flightlog: synced with MP -- {len(local_rows)} local + {len(remote_rows)} remote "
          f"-> {len(merged)} merged rows", flush=True)
