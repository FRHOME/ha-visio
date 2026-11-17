# HA Visio

Video calls between Home Assistant instances — for example between your home and your parents' home — on any screen that shows Home Assistant: a browser, the companion app, or a wall tablet in kiosk mode. Nothing to install on the devices, no account, no cloud.

Install the same add-on on each Home Assistant. Each one keeps its own directory: the **screens and people** that can be called here, and the **contacts** (other Home Assistant instances) it is paired with.

## How it works

```
 browser (Paris) ⇄ HA Visio Paris ⇄ ── peer link :8100 ── ⇄ HA Visio Toulouse ⇄ wall tablet (Toulouse)
        └──────────────────── audio + video, peer-to-peer (WebRTC) ────────────────────┘
```

- Browsers only ever talk to **their own** Home Assistant, through ingress. No token, no cross-site login.
- The two add-ons keep one authenticated connection between them (peer link). It carries presence and call signalling only.
- Audio and video go straight from one browser to the other (WebRTC).

## Setup

### 1. On each Home Assistant

1. Add the repository `https://github.com/FRHOME/ha-visio`, install **HA Visio**.
2. In **Configuration**, set **Name of this Home Assistant** (e.g. `Paris`). Leave the rest as is.
3. Start it and enable **Show in sidebar**.
4. Open **Visio → ⚙️ → Screens & people here** and add what can be called here:
   - a **Screen** (wall tablet, kiosk): recognised by its browser User-Agent (open Visio on the tablet once and use *Use this browser*, or type e.g. `KioskSatellite`);
   - a **Person**: recognised by their Home Assistant user name.

### 2. Pair two instances (once)

1. On the first one: **⚙️ → Invite a Home Assistant**. Check the proposed address of *this* Home Assistant (its IP + port `8100`), then **Create the code**.
2. On the other one: **⚙️ → I have a code**, paste it, check *its* own address, then **Pair**.

The code is valid 15 minutes and works once. Each side now lists the other as a contact, online when the peer link is up. Removing a contact removes the pairing on both sides.

Per contact you can choose **what it may call here** (everything by default).

### 3. Call

Open **Visio**: each contact shows its screens and people. Tap one, or **Everyone** to ring every screen/person marked *Rings when a contact calls “Everyone”*. The first to answer takes the call; the others stop ringing.

## Kiosk screens (Kiosk Satellite…)

For a screen, open **Kiosk control** to let HA Visio wake it up on an incoming call:

| Field | Example | Role |
|---|---|---|
| Home Assistant URL as seen by the kiosk | `http://127.0.0.1:2325` | Kiosk Satellite serves HA on localhost, which browsers treat as a secure context: required for the camera. |
| Service that opens a page | `esphome.ks_tablette_open_url` | Opens the Visio page when a call comes in. |
| Service that closes the page | `esphome.ks_tablette_close_url` | Called when the call ends. |
| Button pressed after the call | `button.ks_tablette_dashboard` | Back to the dashboard. |
| Switched off / on during the call | `switch.ks_tablette_screensaver` | Restored to their previous state afterwards. |

Wall screens with kiosk control get the calling page but not the settings.

## Network

- Each add-on listens on port **8100** (peer link). It must be reachable from the other instance: same LAN, VPN, or UniFi Site Magic. The two sites must use different IP ranges.
- WebRTC media needs the browsers of both sites to reach each other directly: true on a site-to-site VPN. STUN/TURN servers are optional; add a `turn:` server only for networks without such a VPN.
- Camera and microphone need a secure context: Home Assistant opened over **HTTPS** in browsers and the companion app, or `http://127.0.0.1…` on Kiosk Satellite.

## Security

- Pairing secret created by the add-on, stored only in `/data/visio.json` (included in add-on backups), and shown once inside the invitation code.
- Mutual challenge–response authentication on every peer connection; every frame is signed and sequence-numbered (no tampering, no replay). Frames are not encrypted: run the peer link inside your LAN/VPN, not on the open Internet.
- A peer can only reach the screens and people you share with it, and only through calls you answer.

## Automations

| | |
|---|---|
| `sensor.ha_visio` | `idle`, `calling`, `ringing` or `active`; attribute `calls` lists the current calls. |
| Event `ha_visio_incoming` | `call_id`, `from`, `contact`, `endpoints`, `endpoint_ids` — e.g. raise the tablet volume, send a notification. |
| Event `ha_visio_answered` | `call_id`, `from`. |
| Event `ha_visio_ended` | `call_id`, `direction`, `reason` (`hangup`, `declined`, `missed`, `busy`, `unavailable`, `offline`, `disconnected`, `cancelled`), `remote`, `contact`, `duration`. |

## Options

| Option | Default | |
|---|---|---|
| `instance_name` | `Home` | Name shown to the other instances. |
| `peer_url` | empty | Address of this instance for the others (`http://IP:8100`). Proposed automatically when empty. |
| `ring_duration` | `45` | Seconds before “No answer”. |
| `ice_servers` | Google STUN | STUN/TURN URLs. |
| `turn_username` / `turn_password` | empty | Only for a `turn:` server. |
| `log_level` | `info` | |
