#!/usr/bin/env python3
"""
power_monitor.py — AC power loss detection via GPIO, drives NUT dummy-ups.

Hardware:
  - Raspberry Pi 4 Model B
  - GPIO 17 (BCM) input, internal pull-down enabled
  - 2N2222 NPN transistor circuit fed from a 5V wall wart
      * Wall wart present (AC OK) -> transistor conducts -> GPIO 17 LOW
      * Wall wart unpowered (AC LOST) -> transistor off  -> GPIO 17 HIGH
  - 5V from wall wart MUST NOT reach GPIO directly (3.3V max).
    Transistor provides level conversion.

Behavior:
  - Polls GPIO 17 once per second.
  - Debounces transitions (N consecutive matching reads required).
  - On confirmed AC loss: writes ups.status = "OB" (On Battery) to the
    NUT dummy-ups state file.
  - After GRACE_SECONDS (default 300s = 5 min) of continuous AC loss:
    writes ups.status = "OB LB" (On Battery + Low Battery) to trigger
    NUT clients (TeraStation, OPNsense, Ubuntu hosts) to shut down.
  - On AC restore at any point before LB is latched: writes "OL"
    (On Line) and resets the timer. If LB has already been signaled,
    we keep LB asserted for SHUTDOWN_HOLD_SECONDS so clients that have
    started shutdown actually finish; this avoids a flap-rescue race.
  - Manual test trigger: `touch /tmp/power_fail_test` simulates AC loss
    regardless of GPIO state. `rm /tmp/power_fail_test` clears it.

Output:
  - Writes /etc/nut/dummy-ups.dev (the file `ups.conf` points its
    dummy-ups driver at). Driver picks up changes on its next poll.
  - Logs to stdout/stderr (journald via systemd).

Run as root (or a user in the gpio group with write access to the
dummy-ups state file).
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("RPi.GPIO not installed. Run: sudo apt install python3-rpi.gpio",
          file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GPIO_PIN = 17                       # BCM numbering
POLL_INTERVAL_SEC = 1.0
DEBOUNCE_SAMPLES = 3                # consecutive matching reads to accept
GRACE_SECONDS = 300                 # 5 minutes before signaling low battery
SHUTDOWN_HOLD_SECONDS = 120         # keep LB asserted this long after trigger

DUMMY_UPS_FILE = Path("/etc/nut/dummy-ups.dev")
TEST_TRIGGER_FILE = Path("/tmp/power_fail_test")
SAFE_STANDBY_DELAY = 30             # seconds after LB hold before entering standby

# Polarity: AC LOST when GPIO reads HIGH (per circuit description in memory)
AC_LOST_LEVEL = GPIO.HIGH

# Human-friendly UPS metadata written into the state file alongside status.
# These values are also visible to NUT clients via `upsc`.
STATIC_VARS = {
    "device.mfr":          "DIY",
    "device.model":        "GPIO-AC-Monitor",
    "device.serial":       "rpi4-gpio17",
    "device.type":         "ups",
    "ups.mfr":             "DIY",
    "ups.model":           "GPIO-AC-Monitor",
    "ups.vendorid":        "0000",
    "ups.productid":       "0000",
    "battery.charge":      "100",
    "battery.charge.low":  "20",
    "battery.runtime":     "600",      # nominal; only meaningful at OB
    "battery.runtime.low": "120",
    "input.voltage":       "120.0",
    "input.frequency":     "60.0",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("power_monitor")


# ---------------------------------------------------------------------------
# State file writer
# ---------------------------------------------------------------------------

def write_state(status: str) -> None:
    """
    Atomically write the dummy-ups .dev file with the given ups.status.
    dummy-ups format is one `key: value` per line.
    """
    lines = [f"{k}: {v}" for k, v in STATIC_VARS.items()]
    lines.append(f"ups.status: {status}")
    body = "\n".join(lines) + "\n"

    tmp = DUMMY_UPS_FILE.with_suffix(".dev.tmp")
    tmp.write_text(body)
    os.replace(tmp, DUMMY_UPS_FILE)
    log.info("wrote ups.status=%s to %s", status, DUMMY_UPS_FILE)


# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

def read_ac_lost() -> bool:
    """True if AC power appears to be lost right now (one sample)."""
    if TEST_TRIGGER_FILE.exists():
        return True
    return GPIO.input(GPIO_PIN) == AC_LOST_LEVEL


def debounced_ac_lost(current: bool) -> bool:
    """
    Take DEBOUNCE_SAMPLES samples spaced by POLL_INTERVAL_SEC/DEBOUNCE_SAMPLES,
    return True only if all samples agree with `current==True` (AC lost) or
    False only if all agree with AC present. If samples disagree, fall back
    to the previous reported value by returning `current` unchanged.
    """
    interval = POLL_INTERVAL_SEC / DEBOUNCE_SAMPLES
    samples = []
    for _ in range(DEBOUNCE_SAMPLES):
        samples.append(read_ac_lost())
        time.sleep(interval)
    if all(samples):
        return True
    if not any(samples):
        return False
    return current  # bouncing -> hold previous


# ---------------------------------------------------------------------------
# Safe-standby mode
# ---------------------------------------------------------------------------

def enter_safe_standby() -> None:
    """
    Enter safe-standby after clients have shut down.

    Stops NUT services, syncs and remounts the filesystem read-only, then polls
    GPIO until AC returns. If power dies while in standby, the read-only fs
    ensures zero corruption risk; the Pi boots clean on power restore.
    """
    log.critical("Entering safe-standby: stopping services, going read-only")

    subprocess.run(["systemctl", "stop", "nut-server", "nut-driver@ups",
                    "nut-driver@ups1"], check=False)

    subprocess.run(["sync"], check=False)

    result = subprocess.run(["mount", "-o", "remount,ro", "/"], check=False)
    if result.returncode != 0:
        log.warning("Could not remount read-only; filesystem was synced")

    # Poll GPIO with no disk writes — safe regardless of power state
    while True:
        if not read_ac_lost():
            break
        time.sleep(1)

    # AC restored — recover
    subprocess.run(["mount", "-o", "remount,rw", "/"], check=False)
    subprocess.run(["systemctl", "start", "nut-driver@ups", "nut-driver@ups1",
                    "nut-server"], check=False)
    log.info("AC RESTORED during safe-standby, services restarted")
    write_state("OL")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def handle_sigterm(signum, frame):
        log.info("received signal %s, exiting", signum)
        # On clean exit, leave status as OL so we don't trigger shutdowns.
        try:
            write_state("OL")
        finally:
            GPIO.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # Initial state: assume AC OK until proven otherwise.
    write_state("OL")
    ac_lost = False
    ac_lost_since = None        # monotonic timestamp of confirmed AC loss
    lb_signaled_at = None       # monotonic timestamp when LB was first written

    log.info("power_monitor started; GPIO%d, grace=%ds", GPIO_PIN, GRACE_SECONDS)

    while True:
        new_state = debounced_ac_lost(ac_lost)
        now = time.monotonic()

        # --- transition: AC was OK, now lost -------------------------------
        if new_state and not ac_lost:
            ac_lost = True
            ac_lost_since = now
            lb_signaled_at = None
            write_state("OB")
            log.warning("AC LOSS detected; grace timer started (%ds)",
                        GRACE_SECONDS)

        # --- transition: AC was lost, now restored -------------------------
        elif not new_state and ac_lost:
            # If we already latched LB, hold it briefly so clients finish.
            if lb_signaled_at is not None:
                hold_remaining = SHUTDOWN_HOLD_SECONDS - (now - lb_signaled_at)
                if hold_remaining > 0:
                    log.warning(
                        "AC restored but LB hold active for %.0fs more",
                        hold_remaining)
                    time.sleep(POLL_INTERVAL_SEC)
                    continue
            ac_lost = False
            ac_lost_since = None
            lb_signaled_at = None
            write_state("OL")
            log.info("AC RESTORED")

        # --- still on battery: check grace expiry --------------------------
        elif ac_lost and lb_signaled_at is None:
            elapsed = now - ac_lost_since
            if elapsed >= GRACE_SECONDS:
                lb_signaled_at = now
                write_state("OB LB")
                log.critical(
                    "GRACE EXPIRED after %.0fs; signaling LOW BATTERY (OB LB) "
                    "-- NUT clients should now begin shutdown",
                    elapsed)
            else:
                # log progress every ~30s
                if int(elapsed) % 30 == 0:
                    log.info("on battery; %ds elapsed, %ds until shutdown",
                             int(elapsed), GRACE_SECONDS - int(elapsed))

        # --- LB was signaled: enter safe-standby after hold ---------------
        elif ac_lost and lb_signaled_at is not None:
            hold_elapsed = now - lb_signaled_at
            standby_threshold = SHUTDOWN_HOLD_SECONDS + SAFE_STANDBY_DELAY
            if hold_elapsed >= standby_threshold:
                log.critical(
                    "Clients have had %.0fs to shut down; entering safe-standby",
                    hold_elapsed)
                enter_safe_standby()
                # Returned from standby — AC is back, reset state
                ac_lost = False
                ac_lost_since = None
                lb_signaled_at = None

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("fatal error")
        try:
            GPIO.cleanup()
        except Exception:
            pass
        sys.exit(1)
