# Control Commands Reference

All commands assume `sudo` where noted. Pi address: `192.168.22.108`.

---

## 1. Service control (Raspberry Pi)

### power-monitor service
```bash
# Status
sudo systemctl status power-monitor.service

# Start / stop / restart
sudo systemctl start power-monitor.service
sudo systemctl stop power-monitor.service
sudo systemctl restart power-monitor.service

# Enable / disable at boot
sudo systemctl enable power-monitor.service
sudo systemctl disable power-monitor.service

# Live logs
sudo journalctl -u power-monitor.service -f

# Last 200 lines
sudo journalctl -u power-monitor.service -n 200 --no-pager

# Logs since boot
sudo journalctl -u power-monitor.service -b
```

### NUT server (upsd) and driver
```bash
# Status
sudo systemctl status nut-server.service
sudo systemctl status nut-driver.service

# Restart whole NUT stack in correct order
sudo systemctl restart nut-driver.service
sudo systemctl restart nut-server.service

# Reload only (after editing upsd.conf / upsd.users)
sudo systemctl reload nut-server.service

# Logs
sudo journalctl -u nut-server.service -f
sudo journalctl -u nut-driver.service -f
```

### Reload after editing `power_monitor.py` or its unit file
```bash
sudo systemctl daemon-reload
sudo systemctl restart power-monitor.service
```

---

## 2. NUT client interaction (any host that can reach the Pi)

### Query current UPS state
```bash
# Full status dump
upsc ups@192.168.22.108

# Just the status field
upsc ups@192.168.22.108 ups.status

# From the Pi itself
upsc ups@localhost
upsc ups1@localhost
```

### List defined UPSes on the server
```bash
upsc -l 192.168.22.108
```

### Authenticated commands (master)
```bash
# Send a known command (rare; dummy-ups exposes few)
upscmd -l ups@192.168.22.108
upscmd -u monitor ups@192.168.22.108 <command>

# Set a variable manually (debug only)
upsrw -s ups.status=OL -u monitor ups@192.168.22.108
```

---

## 3. Manual power-loss simulation

Run on the Pi.

### Trigger simulated AC loss
```bash
touch /tmp/power_fail_test
```
The monitor will detect this on its next poll, write `ups.status: OB`, and
start the 5-minute timer. After 5 minutes it will escalate to `OB LB` and
all clients should shut down.

### Cancel the simulation
```bash
rm /tmp/power_fail_test
```
If `LB` has not yet been signaled, the service writes `OL` immediately. If
`LB` was already written, it stays asserted for `SHUTDOWN_HOLD_SECONDS`
(default 120s) so already-shutting-down clients can finish.

### Short-cycle test (lower grace period temporarily)
```bash
# 1. Edit GRACE_SECONDS to e.g. 30 in /opt/power-monitor/power_monitor.py
sudo nano /opt/power-monitor/power_monitor.py

# 2. Restart and trigger
sudo systemctl restart power-monitor.service
touch /tmp/power_fail_test

# 3. Watch
sudo journalctl -u power-monitor.service -f

# 4. After test, revert GRACE_SECONDS to 300 and restart
sudo systemctl restart power-monitor.service
```

---

## 4. Verification / debug

### Confirm upsd is listening on 3493
```bash
# On the Pi
sudo ss -tlnp | grep 3493

# From any other host
nc -vz 192.168.22.108 3493
```

### Watch dummy-ups state file change in real time
```bash
# On the Pi
watch -n 1 'cat /etc/nut/dummy-ups.dev'
# or
tail -F /etc/nut/dummy-ups.dev
```

### Read GPIO 17 directly (sanity check the hardware)
```bash
# Single read (gpiozero / RPi.GPIO available)
python3 -c "
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
print('GPIO17:', 'HIGH (AC LOST)' if GPIO.input(17) else 'LOW (AC OK)')
GPIO.cleanup()
"

# Continuous (Ctrl-C to stop)
python3 -c "
import RPi.GPIO as GPIO, time
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
try:
    while True:
        print('HIGH (AC LOST)' if GPIO.input(17) else 'LOW (AC OK)')
        time.sleep(1)
finally:
    GPIO.cleanup()
"
```

### Verify NUT protocol from a client
```bash
# Bare NUT protocol (manual)
nc 192.168.22.108 3493
# then type:
LIST UPS
GET VAR ups ups.status
LOGOUT
```

### Confirm a client is configured to shut down
```bash
# On any Ubuntu client
sudo upsc ups@192.168.22.108 ups.status
sudo systemctl status nut-client.service
sudo systemctl status nut-monitor.service
sudo journalctl -u nut-monitor.service -n 100 --no-pager
```

---

## 5. Editing configs (Pi)

### After editing `/etc/nut/ups.conf` (driver definitions)
```bash
sudo systemctl restart nut-driver.service
sudo systemctl restart nut-server.service
```

### After editing `/etc/nut/upsd.conf` or `/etc/nut/upsd.users`
```bash
sudo systemctl reload nut-server.service
# If reload doesn't pick changes up:
sudo systemctl restart nut-server.service
```

### After editing `/etc/nut/nut.conf`
```bash
# Affects mode (server vs client). Full restart is safest.
sudo systemctl restart nut-server.service nut-driver.service
```

### Validate NUT configs without applying
```bash
# upsd will refuse to start with errors, but you can dry-check by trying
# to start it in foreground. Stop the service first, then:
sudo systemctl stop nut-server.service
sudo upsd -D       # foreground, debug; Ctrl-C to exit
sudo systemctl start nut-server.service
```

---

## 6. Network / capture (protocol confirmation)

### Capture NUT traffic on the Pi
```bash
sudo tcpdump -i any -nn -A 'tcp port 3493' -w /tmp/nut.pcap
# Stop with Ctrl-C, then read:
tcpdump -nn -A -r /tmp/nut.pcap
```

### Capture from a specific client (e.g. TeraStation)
```bash
sudo tcpdump -i any -nn -A 'host 192.168.22.11 and tcp port 3493'
```

This is how the original protocol discovery (NUT vs apcupsd) was confirmed
on the TeraStation.

---

## 7. Credential rotation

```bash
# 1. Edit /etc/nut/upsd.users on the Pi, change `password = ...`
sudo nano /etc/nut/upsd.users
sudo systemctl reload nut-server.service

# 2. Update every client to match:
#    - TeraStation web UI  -> UPS settings
#    - OPNsense            -> Services > NUT > settings
#    - Ubuntu hosts        -> /etc/nut/upsmon.conf, then:
sudo systemctl restart nut-monitor.service
```

---

## 8. End-to-end shutdown drill

1. On the Pi:
   ```bash
   touch /tmp/power_fail_test
   sudo journalctl -u power-monitor.service -f
   ```
2. From another host, watch status change:
   ```bash
   watch -n 2 'upsc ups@192.168.22.108 ups.status'
   ```
3. After ~5 minutes, status flips from `OB` to `OB LB`. All clients begin
   shutdown.
4. To abort before LB: `rm /tmp/power_fail_test`.
5. After test: restart any clients you let shut down, and ensure the Pi
   shows `OL` again:
   ```bash
   upsc ups@192.168.22.108 ups.status
   ```
