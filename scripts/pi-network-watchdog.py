#!/usr/bin/env python3
"""
pi-network-watchdog.py — Disables NUT monitoring when the Pi is unreachable.

Runs on miniUbuntu (192.168.22.80). Polls the Pi's NUT server via upsc.
If the Pi is unreachable, SSHes into each NUT client and stops upsmon so
they don't panic-shutdown due to a stale UPS. Re-enables upsmon when the
Pi comes back.

Why this works: if this host is alive and running, power is fine. The Pi
being unreachable means it crashed or lost network — not a power outage.
In a real outage, this host loses power too and can't disable anything,
so clients react normally to Pi's OB/OB LB status.
"""

import logging
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL = 5
PI_UPS = "ups@192.168.22.108"
UPSC_BIN = "/usr/bin/upsc"

# Consecutive failed polls before disabling clients.
# 2 polls * 5s = 10s detection window. Clients have DEADTIME=60s, so
# plenty of margin.
FAIL_THRESHOLD = 2

SSH_BIN = "/usr/bin/ssh"
SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new"]

# Remote NUT clients to manage. Each entry needs:
#   host: SSH destination (user@ip)
#   stop_cmd / start_cmd: shell command to stop/start upsmon
#
# OPNsense: verify service name with `service -e | grep nut` on the box.
# Mac Mini: needs passwordless sudo for launchctl, or SSH as root.
CLIENTS = [
    {
        "name": "OPNsense",
        "host": "root@192.168.22.1",
        "stop_cmd": "service nut_upsmon onestop",
        "start_cmd": "service nut_upsmon onestart",
    },
    {
        "name": "MacMini",
        "host": "gatormc9@192.168.22.12",
        "stop_cmd": "sudo launchctl bootout system /Library/LaunchDaemons/com.networkupstools.upsmon.plist",
        "start_cmd": "sudo launchctl bootstrap system /Library/LaunchDaemons/com.networkupstools.upsmon.plist",
    },
]

# Local upsmon on this host (miniUbuntu).
LOCAL_STOP = ["systemctl", "stop", "nut-monitor"]
LOCAL_START = ["systemctl", "start", "nut-monitor"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pi_watchdog")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def query_pi() -> bool:
    try:
        result = subprocess.run(
            [UPSC_BIN, PI_UPS, "ups.status"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def ssh_cmd(host: str, cmd: str) -> bool:
    try:
        result = subprocess.run(
            [SSH_BIN] + SSH_OPTS + [host, cmd],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def local_cmd(cmd: list[str]) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def disable_all() -> None:
    for client in CLIENTS:
        ok = ssh_cmd(client["host"], client["stop_cmd"])
        status = "stopped" if ok else "FAILED to stop"
        log.warning("  %s upsmon on %s", status, client["name"])

    ok = local_cmd(LOCAL_STOP)
    status = "stopped" if ok else "FAILED to stop"
    log.warning("  %s local upsmon", status)


def enable_all() -> None:
    ok = local_cmd(LOCAL_START)
    status = "started" if ok else "FAILED to start"
    log.info("  %s local upsmon", status)

    for client in CLIENTS:
        ok = ssh_cmd(client["host"], client["start_cmd"])
        status = "started" if ok else "FAILED to start"
        log.info("  %s upsmon on %s", status, client["name"])


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    clients_disabled = False
    consecutive_failures = 0

    def handle_signal(signum, frame):
        nonlocal clients_disabled
        log.info("received signal %s, exiting", signum)
        if clients_disabled:
            log.warning("re-enabling upsmon on all clients before exit")
            enable_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("watchdog started, polling %s every %ds (threshold=%d)",
             PI_UPS, POLL_INTERVAL, FAIL_THRESHOLD)

    while True:
        reachable = query_pi()

        if reachable:
            consecutive_failures = 0
            if clients_disabled:
                log.info("Pi is back — re-enabling upsmon on all clients")
                enable_all()
                clients_disabled = False
        else:
            consecutive_failures += 1

            if consecutive_failures >= FAIL_THRESHOLD and not clients_disabled:
                log.warning(
                    "Pi unreachable for %d polls (%ds), disabling upsmon",
                    consecutive_failures, consecutive_failures * POLL_INTERVAL)
                disable_all()
                clients_disabled = True

            elif clients_disabled and consecutive_failures % 60 == 0:
                log.info("Pi still unreachable (%ds), clients remain disabled",
                         consecutive_failures * POLL_INTERVAL)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("fatal error")
        sys.exit(1)
