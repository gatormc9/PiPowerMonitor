#!/usr/bin/env python3
"""
nut-fallback-watchdog.py — Fallback NUT relay for Pi power monitor.

Runs on a secondary host (e.g. miniUbuntu) alongside a local NUT server
with a dummy-ups driver. Polls the Pi's NUT server and mirrors its status
to the local dummy-ups state file. If the Pi is unreachable, reports OL
(this host is alive, so power is fine — the Pi is just down).

During a real outage, mirrors OB/OB LB so clients shut down correctly.
After mirroring OB LB, waits for other clients to finish shutting down,
then shuts down this host last (before the Pi enters safe-standby).

Clients should MONITOR both the Pi's UPS and this fallback's UPS with
MINSUPPLIES 1, so losing either server alone doesn't trigger shutdown.
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

POLL_INTERVAL = 5
PI_UPS = "ups@192.168.22.108"
STATE_FILE = Path("/etc/nut/dummy-fallback.seq")
UPSC_BIN = "/usr/bin/upsc"

# How long to wait after mirroring OB LB before shutting ourselves down.
# Other clients need time to finish their shutdowns first.
SELF_SHUTDOWN_DELAY = 180

STATIC_VARS = {
    "device.mfr":          "DIY",
    "device.model":        "Fallback-Relay",
    "device.serial":       "fallback-miniubuntu",
    "device.type":         "ups",
    "ups.mfr":             "DIY",
    "ups.model":           "Fallback-Relay",
    "ups.vendorid":        "0000",
    "ups.productid":       "0000",
    "battery.charge":      "100",
    "battery.charge.low":  "20",
    "battery.runtime":     "600",
    "battery.runtime.low": "120",
    "input.voltage":       "120.0",
    "input.frequency":     "60.0",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nut_fallback")


def write_state(status: str) -> None:
    on_battery = status != "OL"
    overrides = {"input.voltage": "0.0"} if on_battery else {}
    lines = [f"{k}: {overrides.get(k, v)}" for k, v in STATIC_VARS.items()]
    lines.append(f"ups.status: {status}")
    body = "\n".join(lines) + "\n"

    tmp = STATE_FILE.with_suffix(".dev.tmp")
    tmp.write_text(body)
    os.replace(tmp, STATE_FILE)


def query_pi_status() -> str | None:
    """Query the Pi's UPS status. Returns status string or None if unreachable."""
    try:
        result = subprocess.run(
            [UPSC_BIN, PI_UPS, "ups.status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def main() -> int:
    write_state("OL")
    log.info("fallback watchdog started, polling %s every %ds", PI_UPS, POLL_INTERVAL)

    last_status = "OL"
    pi_unreachable_since = None
    lb_mirrored_at = None

    while True:
        pi_status = query_pi_status()

        if pi_status is not None:
            pi_unreachable_since = None

            if pi_status != last_status:
                log.info("Pi status changed: %s -> %s", last_status, pi_status)
                write_state(pi_status)
                last_status = pi_status

                if "LB" in pi_status and lb_mirrored_at is None:
                    lb_mirrored_at = time.monotonic()
                    log.warning("OB LB mirrored; self-shutdown in %ds", SELF_SHUTDOWN_DELAY)

                if pi_status == "OL":
                    lb_mirrored_at = None

        else:
            # Pi unreachable — we're alive, so power is fine
            if pi_unreachable_since is None:
                pi_unreachable_since = time.monotonic()
                log.warning("Pi unreachable, reporting OL (this host is alive)")

            if last_status != "OL":
                write_state("OL")
                last_status = "OL"
                lb_mirrored_at = None

            elapsed = time.monotonic() - pi_unreachable_since
            if int(elapsed) % 60 == 0 and int(elapsed) > 0:
                log.info("Pi still unreachable for %ds, holding OL", int(elapsed))

        # Self-shutdown after delay once LB is mirrored
        if lb_mirrored_at is not None:
            elapsed = time.monotonic() - lb_mirrored_at
            if elapsed >= SELF_SHUTDOWN_DELAY:
                log.critical("Self-shutdown delay expired (%.0fs), shutting down", elapsed)
                subprocess.run(["/sbin/shutdown", "-h", "+0"], check=False)
                sys.exit(0)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("fatal error")
        sys.exit(1)
