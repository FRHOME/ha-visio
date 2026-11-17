# HA Visio

Video calls between Home Assistant instances, on any screen that shows Home Assistant — browser, companion app, or wall tablet in kiosk mode (Kiosk Satellite…). No app to install on the devices, no account, no cloud: the add-ons pair with each other, and audio/video flow peer-to-peer (WebRTC).

*Appels vidéo entre instances Home Assistant, sur n'importe quel écran HA (navigateur, app compagnon, tablette murale en kiosque). Rien à installer sur les appareils, ni compte ni cloud. Interface en français et en anglais.*

## Features

- Install the same add-on on each Home Assistant, pair them once with a one-time code.
- Each instance lists its contacts and what can be called there: wall screens, people.
- Call a precise screen or person, or everyone; Answer / Decline on any HA screen.
- Kiosk screens are woken up, the call page opens, the screensaver is paused, then everything goes back to the dashboard.
- Calls in both directions; presence; per-contact sharing.
- `sensor.ha_visio` and `ha_visio_*` events for automations.

## Installation

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FFRHOME%2Fha-visio)

Or **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add `https://github.com/FRHOME/ha-visio`, then install **HA Visio**.

Setup, network and security notes: [visio/DOCS.md](visio/DOCS.md).

## Development

```
cd visio/app
VISIO_DEV=1 VISIO_DATA=/tmp/visio-a VISIO_PORT=18099 VISIO_PEER_PORT=18100 python3 .
```

`VISIO_DEV=1` accepts requests from outside ingress. Contributions welcome.

## License

MIT
