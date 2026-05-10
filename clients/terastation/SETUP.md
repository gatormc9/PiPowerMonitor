# Buffalo TeraStation NUT Client Setup

## Prerequisites

- Buffalo TeraStation TS3410D (or similar with NUT support)
- Web UI access

## Configuration

1. Log into the TeraStation web UI
2. Navigate to **Management > UPS**
3. Configure:
   - **UPS Type:** Network UPS Tools (NUT)
   - **UPS Server:** `<PI_IP_ADDRESS>`
   - **UPS Name:** `ups`
   - **Username:** `monitor`
   - **Password:** (see your `upsd.users` on the Pi)
4. Save and verify "Connected" status

## Important Notes

- The TeraStation firmware queries both `ups` AND `ups1` automatically.
  Both names must be defined in the Pi's `ups.conf`.
- The TeraStation uses the `monitor` user (master role), not `monuser`.
- It may take 2-3 minutes after a NUT server restart for the TeraStation
  to reconnect.
- There is no way to install a custom shutdown wrapper on the TeraStation.
  During drill tests, the NAS will actually shut down.

## Troubleshooting

If the TeraStation shows "disconnected":
1. Verify NUT port is open: `nc -vz <PI_IP> 3493`
2. Check the Pi NUT server is running: `upsc ups@<PI_IP>`
3. Verify both `ups` and `ups1` are defined in the Pi's `ups.conf`
4. Use tcpdump on the Pi to see TeraStation queries:
   ```bash
   sudo tcpdump -i any -nn 'host <TERASTATION_IP> and tcp port 3493'
   ```
