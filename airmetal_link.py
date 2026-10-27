"""Which aircraft AIR METAL (the 2x2 CRT wall on PRODUCTION) is riding
right now, so the radar can circle it.

AIR METAL serves GET /riding -> {"hex": "a1b2c3", "callsign": ...}, or
{"hex": null} in standby. Polled on a background thread so the render loop
never waits on the network; when PRODUCTION is off or running another app
the request just fails and there's no highlight.
"""
import json
import threading
import time
import urllib.request

POLL_SEC = 2.0
TIMEOUT_SEC = 1.5
STALE_SEC = 10.0  # forget the ride if AIR METAL hasn't answered for this long


class AirMetalLink:
    def __init__(self, url):
        self.url = url
        self._hex = None
        self._seen = 0.0
        if url:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=TIMEOUT_SEC) as resp:
                    self._hex = json.load(resp).get("hex")
                self._seen = time.monotonic()
            except (OSError, ValueError, AttributeError):
                pass
            time.sleep(POLL_SEC)

    @property
    def riding_hex(self):
        """The ridden aircraft's hex, or None."""
        if time.monotonic() - self._seen > STALE_SEC:
            return None
        return self._hex
