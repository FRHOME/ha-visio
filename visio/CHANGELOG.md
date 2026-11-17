# Changelog

## 0.2.0

Rewrite: calls between Home Assistant instances, in both directions.

- Peer link between add-ons (port 8100) with pairing by one-time invitation code, mutual authentication and signed frames.
- Directory per instance: local screens and people, contacts (paired instances) with presence and per-contact sharing.
- Call a precise screen/person or everyone; first to answer wins, the others stop ringing; busy detection.
- Kiosk control per screen (open/close page, back button, entities switched off/on and restored).
- New page: contacts, call screens, settings (screens & people, pairing), English and French.
- `sensor.ha_visio` and events `ha_visio_incoming`, `ha_visio_answered`, `ha_visio_ended`.
- v0.1 tablet options are migrated into a screen automatically.

## 0.1.0

- First version: WebRTC video call to one tablet, ringtone, Answer / Decline, mic and camera toggles, back to dashboard.
