#!/bin/bash
# nut-shutdown.sh — Test-mode-aware shutdown wrapper for NUT upsmon.
#
# Install to /usr/local/bin/nut-shutdown.sh on each NUT client.
# Set SHUTDOWNCMD in upsmon.conf to point here.
#
# To enable test mode (prevents actual shutdown):
#   touch /tmp/nut-test-mode
#
# To disable test mode (real shutdowns resume):
#   rm /tmp/nut-test-mode

if [ -f /tmp/nut-test-mode ]; then
    logger -t NUT-TEST "SHUTDOWN WOULD EXECUTE NOW (test mode active)"
    echo "$(date): SHUTDOWN BLOCKED - test mode" >> /var/log/nut-test.log
    exit 0
fi

/sbin/shutdown -h +0
