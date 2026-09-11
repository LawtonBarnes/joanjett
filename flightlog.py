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
import subprocess
import sys
from pathlib import Path

import aircraft

LOG_RADIUS_NM = 9

SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_LOG_PATH = SCRIPT_DIR / "data" / "flightlog.csv"
LOG_HEADER = ["date", "time", "callsign", "type", "altitude_ft", "squawk"]

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
