"""HA Visio — signalisation WebRTC + pilotage de la tablette Kiosk Satellite.

Un seul appel à la fois, entre un appelant (toi, depuis ton téléphone) et la
tablette. L'image et le son passent en direct entre les deux appareils
(WebRTC) ; ce serveur ne fait que mettre en relation et piloter la tablette.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("visio")

OPTIONS_FILE = Path(os.environ.get("VISIO_OPTIONS", "/data/options.json"))
STATIC = Path(__file__).parent / "static"
SUPERVISOR = os.environ.get("VISIO_SUPERVISOR_URL", "http://supervisor")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
INGRESS_IP = "172.30.32.2"
DEV_MODE = os.environ.get("VISIO_DEV") == "1"
SENSOR = "sensor.visio"


def load_options() -> dict:
    defaults = {
        "device_name": "Tablette",
        "device_user_agent": "",
        "device_users": [],
        "device_ha_url": "",
        "open_service": "",
        "close_service": "",
        "return_button": "",
        "turn_off_during_call": [],
        "turn_on_during_call": [],
        "ring_duration": 45,
        "ice_servers": ["stun:stun.l.google.com:19302"],
        "turn_username": "",
        "turn_password": "",
    }
    try:
        defaults.update(json.loads(OPTIONS_FILE.read_text()))
    except FileNotFoundError:
        log.warning("Pas de %s, réglages par défaut", OPTIONS_FILE)
    return defaults


OPT = load_options()


class HA:
    """Petit client de l'API Home Assistant via le Supervisor."""

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.slug: str | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        await self.load_slug()

    async def load_slug(self) -> None:
        if self.slug or not self.session:
            return
        try:
            async with self.session.get(f"{SUPERVISOR}/addons/self/info") as r:
                data = (await r.json()).get("data", {})
                self.slug = data.get("slug")
                log.info("Slug de l'app : %s", self.slug)
        except Exception as err:  # noqa: BLE001
            log.warning("Impossible de lire les infos de l'app : %s", err)

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    async def call(self, service: str, data: dict | None = None) -> None:
        if not service or "." not in service or not self.session:
            return
        domain, name = service.split(".", 1)
        try:
            async with self.session.post(
                f"{SUPERVISOR}/core/api/services/{domain}/{name}", json=data or {}
            ) as r:
                if r.status >= 400:
                    log.warning("%s → HTTP %s %s", service, r.status, (await r.text())[:200])
        except Exception as err:  # noqa: BLE001
            log.warning("%s a échoué : %s", service, err)

    async def state(self, entity_id: str) -> str | None:
        if not self.session:
            return None
        try:
            async with self.session.get(f"{SUPERVISOR}/core/api/states/{entity_id}") as r:
                if r.status != 200:
                    return None
                return (await r.json()).get("state")
        except Exception:  # noqa: BLE001
            return None

    async def publish(self, state: str, **attrs) -> None:
        if not self.session:
            return
        body = {
            "state": state,
            "attributes": {"friendly_name": "Visio", "icon": "mdi:phone-in-talk", **attrs},
        }
        try:
            async with self.session.post(f"{SUPERVISOR}/core/api/states/{SENSOR}", json=body):
                pass
        except Exception:  # noqa: BLE001
            pass


class Call:
    """État de l'unique appel."""

    def __init__(self, ha: HA) -> None:
        self.ha = ha
        self.state = "idle"  # idle | ringing | active
        self.caller: web.WebSocketResponse | None = None
        self.caller_name = ""
        self.callee: web.WebSocketResponse | None = None  # tablette qui a décroché
        self.tablets: set[web.WebSocketResponse] = set()  # pages tablette ouvertes
        self.ring_task: asyncio.Task | None = None
        self.saved: dict[str, str] = {}
        self.prep_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    # --- tablette ---------------------------------------------------------
    async def prepare_tablet(self) -> None:
        self.saved = {}
        for ent in OPT["turn_off_during_call"]:
            self.saved[ent] = await self.ha.state(ent) or ""
            domain = ent.split(".")[0]
            await self.ha.call(f"{domain}.turn_off", {"entity_id": ent})
        for ent in OPT["turn_on_during_call"]:
            self.saved[ent] = await self.ha.state(ent) or ""
            domain = ent.split(".")[0]
            await self.ha.call(f"{domain}.turn_on", {"entity_id": ent})
        await self.ha.load_slug()
        if self.ha.slug and OPT["device_ha_url"]:
            url = f"{OPT['device_ha_url'].rstrip('/')}/hassio/ingress/{self.ha.slug}"
            await self.ha.call(OPT["open_service"], {"url": url, "hold_mode": True})

    async def release_tablet(self) -> None:
        if self.prep_task:
            await asyncio.gather(self.prep_task, return_exceptions=True)
        await asyncio.sleep(2)
        await self.ha.call(OPT["close_service"])
        if OPT["return_button"]:
            await self.ha.call("button.press", {"entity_id": OPT["return_button"]})
        for ent, prev in self.saved.items():
            if prev in ("on", "off"):
                domain = ent.split(".")[0]
                await self.ha.call(f"{domain}.turn_{prev}", {"entity_id": ent})
        self.saved = {}

    # --- cycle de vie -------------------------------------------------------
    async def start(self, ws: web.WebSocketResponse, name: str) -> bool:
        async with self.lock:
            if self.state != "idle":
                return False
            self.state = "ringing"
            self.caller = ws
            self.caller_name = name
            self.ring_task = asyncio.create_task(self._ring_timeout())
        log.info("Appel de %s", name)
        await self.ha.publish("ringing", appelant=name)
        await self.broadcast_tablets({"type": "incoming", "from": name})
        self.prep_task = asyncio.create_task(self.prepare_tablet())
        return True

    async def _ring_timeout(self) -> None:
        try:
            await asyncio.sleep(OPT["ring_duration"])
        except asyncio.CancelledError:
            return
        if self.state == "ringing":
            log.info("Pas de réponse")
            await self.end("missed")

    async def accept(self, ws: web.WebSocketResponse) -> None:
        async with self.lock:
            if self.state != "ringing":
                await send(ws, {"type": "ended", "reason": "gone"})
                return
            self.state = "active"
            self.callee = ws
            if self.ring_task:
                self.ring_task.cancel()
        log.info("Décroché")
        await self.ha.publish("active", appelant=self.caller_name)
        await send(self.caller, {"type": "accepted"})
        for t in list(self.tablets):
            if t is not ws:
                await send(t, {"type": "taken"})

    async def end(self, reason: str, by: web.WebSocketResponse | None = None) -> None:
        async with self.lock:
            if self.state == "idle":
                return
            caller, callee, tablets = self.caller, self.callee, set(self.tablets)
            self.state = "idle"
            self.caller = None
            self.callee = None
            if self.ring_task:
                self.ring_task.cancel()
                self.ring_task = None
        log.info("Fin d'appel (%s)", reason)
        msg = {"type": "ended", "reason": reason}
        for peer in {caller, callee, *tablets}:
            if peer is not None and peer is not by:
                await send(peer, msg)
        await self.ha.publish("idle", dernier_motif=reason)
        asyncio.create_task(self.release_tablet())

    def peer_of(self, ws: web.WebSocketResponse) -> web.WebSocketResponse | None:
        if ws is self.caller:
            return self.callee
        if ws is self.callee:
            return self.caller
        return None

    async def broadcast_tablets(self, msg: dict) -> None:
        for t in list(self.tablets):
            await send(t, msg)


async def send(ws: web.WebSocketResponse | None, msg: dict) -> None:
    if ws is None or ws.closed:
        return
    try:
        await ws.send_json(msg)
    except Exception:  # noqa: BLE001
        pass


def is_tablet(request: web.Request) -> bool:
    forced = request.query.get("role")
    if forced in ("tablette", "appelant"):
        return forced == "tablette"
    ua = request.headers.get("User-Agent", "")
    if OPT["device_user_agent"] and re.search(OPT["device_user_agent"], ua):
        return True
    users = {u.lower() for u in OPT["device_users"]}
    who = {
        request.headers.get("X-Remote-User-Name", "").lower(),
        request.headers.get("X-Remote-User-Display-Name", "").lower(),
        request.headers.get("X-Remote-User-Id", "").lower(),
    }
    return bool(users & who)


def display_name(request: web.Request) -> str:
    return (
        request.headers.get("X-Remote-User-Display-Name")
        or request.headers.get("X-Remote-User-Name")
        or "Quelqu'un"
    )


@web.middleware
async def only_ingress(request: web.Request, handler):
    if not DEV_MODE and request.remote != INGRESS_IP:
        return web.Response(status=403, text="Accès uniquement via Home Assistant")
    return await handler(request)


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


async def config(request: web.Request) -> web.Response:
    ice = []
    for url in OPT["ice_servers"]:
        entry: dict = {"urls": url}
        if url.startswith("turn") and OPT["turn_username"]:
            entry["username"] = OPT["turn_username"]
            entry["credential"] = OPT["turn_password"]
        ice.append(entry)
    return web.json_response(
        {
            "role": "tablette" if is_tablet(request) else "appelant",
            "nom": display_name(request),
            "device_name": OPT["device_name"],
            "ice": ice,
        },
        headers={"Cache-Control": "no-store"},
    )


async def websocket(request: web.Request) -> web.WebSocketResponse:
    call: Call = request.app["call"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    tablet = is_tablet(request)
    name = display_name(request)
    if tablet:
        call.tablets.add(ws)
    await send(ws, {"type": "welcome", "role": "tablette" if tablet else "appelant", "state": call.state})
    if tablet and call.state == "ringing":
        await send(ws, {"type": "incoming", "from": call.caller_name})

    try:
        async for raw in ws:
            if raw.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(raw.data)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "call" and not tablet:
                if not await call.start(ws, name):
                    await send(ws, {"type": "busy"})
            elif kind == "accept" and tablet:
                await call.accept(ws)
            elif kind == "decline" and tablet and call.state == "ringing":
                await call.end("declined", by=None)
            elif kind == "hangup" and (ws is call.caller or ws is call.callee):
                await call.end("hangup", by=ws)
            elif kind == "signal" and call.state == "active":
                await send(call.peer_of(ws), {"type": "signal", "data": msg.get("data")})
    finally:
        call.tablets.discard(ws)
        if ws is call.caller or ws is call.callee:
            await call.end("disconnected", by=ws)
    return ws


async def on_startup(app: web.Application) -> None:
    await app["ha"].start()
    await app["ha"].publish("idle")


async def on_cleanup(app: web.Application) -> None:
    await app["ha"].stop()


def make_app() -> web.Application:
    app = web.Application(middlewares=[only_ingress])
    ha = HA()
    app["ha"] = ha
    app["call"] = Call(ha)
    app.router.add_get("/", index)
    app.router.add_get("/config.json", config)
    app.router.add_get("/ws", websocket)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=int(os.environ.get("VISIO_PORT", "8099")))
