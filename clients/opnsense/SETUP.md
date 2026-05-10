# OPNsense NUT Client Setup

## Prerequisites

- OPNsense router with web UI access
- `os-nut` plugin installed (System > Firmware > Plugins)

## Configuration

1. Navigate to **Services > NUT > Settings**
2. Configure:
   - **Service Mode:** `netclient`
   - **Monitor UPS:** `ups`
   - **UPS Server:** `<PI_IP_ADDRESS>`
   - **Port:** `3493`
   - **Username:** `monuser`
   - **Password:** (see your `upsd.users` on the Pi)
   - **Role:** `slave`
   - **Poll Frequency:** `5`
3. **Enable** the service and click **Save**
4. Verify via **Services > NUT > Diagnostics** — should show "Connected"

## Notes

- OPNsense shuts down as a NUT slave. Clients that have already received the
  FSD (Forced Shutdown) command will complete shutdown locally even if they lose
  network connectivity when the router goes down.
- The `os-nut` plugin does not support a custom SHUTDOWNCMD, so test-mode
  cannot be applied here. During drills, either accept router shutdown or
  temporarily disable the NUT service via the web UI.
