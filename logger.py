#!/usr/bin/env python3
"""JOAN JET's flight logger as its own background service, on the Pi with
the receiver (P4) -- so passes are logged whatever that tube is showing,
not only while JOAN JET is on screen (2026-11-25).

Once a second: readsb's local file -> flightlog.FlightLog, the same
pass detection, local CSV, push to MP and periodic re-sync as the app's
own logging. It only ever reads the LOCAL file: a logger fed over the
network would log another receiver's passes a second time. Install with
joanjett-logger.service and set `[flightlog] logged_by = service` in
settings.ini, which stops the on-screen app logging too (it still
re-syncs MP's log for its stats screens).

Stopping (poweroff, systemctl stop) finalizes the passes in progress, so
an aircraft overhead at shutdown still gets its row.
"""
import signal
import sys
import time

import aircraft
import config
import flightlog

POLL_SEC = 1.0
# Every aircraft in readsb's file, not the app's nearest-N display list --
# flightlog only keeps the ones inside its LOG_RADIUS_NM anyway.
MAX_AIRCRAFT = 1000

stop = False


def _on_signal(signum, frame):
    global stop
    stop = True


def main():
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    s = config.Settings()
    feed = aircraft.Feed(s.feed_file, url="")  # local only, never the network
    log = flightlog.FlightLog()
    flightlog.sync_and_merge()
    print(f"joanjett-logger: logging from {s.feed_file}", flush=True)
    was_ok = None
    while not stop:
        live = aircraft.fetch_aircraft(feed, s.location_lat, s.location_lon, MAX_AIRCRAFT)
        ok = live is not None
        if ok != was_ok:
            print("joanjett-logger: " + ("receiving" if ok else f"no fresh data in {s.feed_file}"), flush=True)
            was_ok = ok
        log.update(live or [])
        time.sleep(POLL_SEC)
    log.update([])  # nothing is close any more: every pass in progress gets its row
    time.sleep(2)  # let the fire-and-forget pushes to MP get out
    log._reap_pushes()
    print("joanjett-logger: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
