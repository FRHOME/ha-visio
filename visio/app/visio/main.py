"""Entry point: wires the directory, the peer link, the calls and the ingress page."""

from __future__ import annotations

import logging

from aiohttp import web

import asyncio

from .calls import CallManager
from .card import install_card
from .config import DATA_DIR, INGRESS_PORT, PEER_PORT, VERSION, load_options
from .ha import HomeAssistant
from .kiosk import Kiosks
from .peers import PeerManager
from .store import Store
from .web import IngressApp

log = logging.getLogger("visio")

LEGACY_KEYS = ("device_name", "device_user_agent", "device_users", "device_ha_url", "open_service", "close_service")


def migrate_legacy(store: Store, opts: dict) -> None:
    """v0.1 kept a single tablet in the options: turn it into a local endpoint once."""
    if store.endpoints or not any(opts.get(k) for k in LEGACY_KEYS[1:]):
        return
    store.upsert_endpoint({
        "name": opts.get("device_name") or "Tablet",
        "kind": "screen",
        "match_user_agent": opts.get("device_user_agent", ""),
        "match_users": opts.get("device_users", []),
        "kiosk_url": opts.get("device_ha_url", ""),
        "open_service": opts.get("open_service", ""),
        "close_service": opts.get("close_service", ""),
        "return_button": opts.get("return_button", ""),
        "turn_off": opts.get("turn_off_during_call", []),
        "turn_on": opts.get("turn_on_during_call", []),
    })
    log.info("Migrated the v0.1 tablet settings into a screen")


def make_app() -> web.Application:
    opts = load_options()
    logging.basicConfig(
        level=getattr(logging, str(opts.get("log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    store = Store(DATA_DIR / "visio.json")
    store.load()
    migrate_legacy(store, opts)

    ha = HomeAssistant()
    calls = CallManager(store, opts, ha, Kiosks(ha))

    async def on_status(peer_id: str) -> None:
        await calls.peer_down(peer_id)
        await calls.push_directory()

    peers = PeerManager(store, opts, on_message=calls.from_peer, on_status=on_status, port=PEER_PORT)
    calls.peers = peers

    app = IngressApp(store, opts, ha, peers, calls).build()

    async def startup(_app: web.Application) -> None:
        log.info("HA Visio %s — instance %s (%s…)", VERSION, peers.my_name, store.instance_id[:8])
        await ha.start()
        await peers.start()
        await calls.publish()
        if opts.get("install_card", True):
            asyncio.create_task(install_card(ha))
        _app["republish"] = asyncio.create_task(republish())

    async def republish() -> None:
        # states set through the API vanish when Core restarts: keep the sensor alive
        while True:
            await asyncio.sleep(60)
            await calls.publish()

    async def cleanup(_app: web.Application) -> None:
        if "republish" in _app:
            _app["republish"].cancel()
        await peers.stop()
        await ha.stop()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def run() -> None:
    web.run_app(make_app(), host="0.0.0.0", port=INGRESS_PORT, access_log=None)
