# UniFi Protect Air & Smoke

A Home Assistant custom integration that adds the readings and alarms the
built-in UniFi Protect integration does not expose yet:

- **UP-AirQuality**: temperature, humidity, CO₂, PM1 / PM2.5 / PM4 / PM10,
  TVOC, VOC index, NOx index, AQI and vape index (only the metrics your sensor
  reports are created), an **Air quality alert** binary sensor, a **Vape
  detected** binary sensor and an **Air quality event** entity.
- **USL Smoke**: **Smoke** and **Carbon monoxide** binary sensors (from the
  detector's live `smokeStatus`, backed by Protect's alarm events); diagnostic
  sensors for battery low, needs cleaning, smoke / CO sensor fault, end of life,
  self-test running and alarm silenced; raw smoke and CO sensor values; an
  **Alarm** event entity (smoke / CO) and a **Maintenance** event entity.

It is read-only and talks to the Protect console on your LAN: it logs in with a
local UniFi OS account, polls every 60 s, and listens to Protect's live update
stream so alarms show up within a second or two. It runs alongside the built-in
UniFi Protect integration and does not replace it.

## Setup

1. Install through the Marketplace / HACS as a custom repository (category
   *Integration*), then restart Home Assistant.
2. **Settings → Devices & services → Add integration → UniFi Protect Air & Smoke.**
3. Enter the console's IP (e.g. your UNVR), port 443, and a **local** UniFi OS
   user with Protect view access. Leave *Verify SSL* off for the console's
   self-signed certificate.

## Notes

- Devices appear under this integration with the same names as in Protect.
  Since Home Assistant 2026.10 devices are no longer merged across
  integrations, so each sensor shows up once under UniFi Protect and once here.
- Parsing is tolerant: unknown fields or new firmware values are ignored rather
  than failing the whole update.
- **Download diagnostics** on the integration page dumps the redacted raw
  sensor payloads and the last 50 sensor events, which is how to map new
  firmware fields.

## Development

```bash
uv venv -p 3.14 .venv && uv pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```
