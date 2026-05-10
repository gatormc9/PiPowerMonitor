# PiPowerMonitor

GPIO-based AC power loss detection for Raspberry Pi, driving NUT (Network UPS Tools) to trigger graceful shutdowns across a home network.

## How It Works

A 5V wall wart plugged into raw mains (not the UPS) feeds a 2N2222 NPN transistor circuit connected to GPIO 17. When mains power fails, the transistor stops conducting and GPIO goes HIGH. The Pi detects this and:

1. **Immediately** writes `ups.status: OB` (On Battery) to NUT
2. **After 5 minutes** of sustained outage, writes `OB LB` (Low Battery)
3. NUT clients (NAS, servers, router) see LB and begin graceful shutdown
4. **After clients shut down**, the Pi enters safe-standby (read-only filesystem) and waits for AC to return

```
AC mains → 5V wall wart → 2N2222 transistor → GPIO 17
                                                  ↓
                                          power_monitor.py
                                                  ↓
                                         NUT dummy-ups driver
                                                  ↓
                               NUT clients (TCP 3493) shut down
```

## Features

- **Debounced GPIO** — requires multiple consecutive matching reads to avoid false triggers
- **Grace period** — configurable delay (default 5 min) before signaling low battery
- **Shutdown hold** — keeps LB asserted briefly so clients that started shutting down finish
- **Safe-standby** — remounts filesystem read-only after clients shut down; zero corruption risk
- **Auto-recovery** — if AC returns during standby, Pi resumes automatically
- **Manual test trigger** — `touch /tmp/power_fail_test` simulates AC loss without hardware
- **Test-mode wrapper** — `touch /tmp/nut-test-mode` on clients prevents real shutdowns during drills
- **Pi network watchdog** — secondary host monitors Pi availability; disables NUT clients if Pi goes down to prevent false shutdowns

## Quick Start

### Pi Server Setup

```bash
sudo mkdir -p /opt/power-monitor
sudo cp power_monitor.py /opt/power-monitor/
sudo cp pi-server/power-monitor.service /etc/systemd/system/
sudo cp pi-server/ups.conf /etc/nut/
sudo cp pi-server/upsd.conf /etc/nut/
sudo cp pi-server/nut.conf /etc/nut/
sudo cp pi-server/upsd.users.example /etc/nut/upsd.users  # edit passwords!
sudo cp pi-server/dummy-ups.dev.initial /etc/nut/dummy-ups.dev

sudo systemctl daemon-reload
sudo systemctl enable --now power-monitor.service
sudo systemctl restart nut-driver.service nut-server.service
```

### Client Setup

See `clients/` directory for per-platform instructions:
- `clients/linux/` — Ubuntu/Debian NUT client
- `clients/macos/` — macOS via Homebrew
- `clients/opnsense/SETUP.md` — OPNsense web UI
- `clients/terastation/SETUP.md` — Buffalo TeraStation web UI

### Testing

```bash
# Quick detection test (no shutdowns):
touch /tmp/power_fail_test    # Pi signals OB
rm /tmp/power_fail_test       # Pi signals OL (cancel before 5 min!)

# Full drill with test-mode (clients log but don't shut down):
# On each client: touch /tmp/nut-test-mode
# On Pi: touch /tmp/power_fail_test
# Wait for OB LB cycle, verify /var/log/nut-test.log on clients
# On Pi: rm /tmp/power_fail_test
# Clean up: rm /tmp/nut-test-mode on clients
```

## Hardware

- Raspberry Pi 4 Model B
- 5V USB wall wart (plugged into raw mains, NOT the UPS)
- 2N2222 NPN transistor
- Resistors for base current limiting and GPIO protection
- GPIO 17 (BCM), internal pull-down enabled

See `docs/hardware-wiring.md` for circuit diagram and BOM.

## Configuration

Key parameters in `power_monitor.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `GPIO_PIN` | 17 | BCM pin number |
| `GRACE_SECONDS` | 300 | Seconds on battery before LB signal |
| `SHUTDOWN_HOLD_SECONDS` | 120 | Seconds to hold LB after trigger |
| `SAFE_STANDBY_DELAY` | 30 | Extra seconds after hold before entering standby |
| `DEBOUNCE_SAMPLES` | 3 | Consecutive matching reads required |

## Pi Network Watchdog

A secondary host (miniUbuntu) runs `pi-network-watchdog.py` to protect against false shutdowns when the Pi is unreachable (crash, network issue). If the Pi's NUT server stops responding, the watchdog SSHes into each NUT client and disables upsmon. When the Pi comes back, it re-enables upsmon.

This works because if the watchdog host is alive, power is fine. In a real outage, the watchdog host loses power too and can't disable anything — clients react normally.

See `scripts/pi-network-watchdog.py` for configuration and `clients/linux/pi-network-watchdog.service` for the systemd unit.

## License

MIT
