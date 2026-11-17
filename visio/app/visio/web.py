"""Ingress side: the page, the browser WebSocket and the small settings API."""

from __future__ import annotations

import json
import logging
import re

import aiohttp
from aiohttp import web

from .calls import CallManager, Client
from .config import DEV_MODE, INGRESS_IP, PEER_PORT, STATIC_DIR, VERSION, ice_servers
from .ha import HomeAssistant
from .peers import PeerManager
from .store import Store

log = logging.getLogger("visio")


# --- who is this browser? ------------------------------------------------------------
def identify(request: web.Request, store: Store) -> tuple[str, str, set[str]]:
    h = request.headers
    user_id = h.get("X-Remote-User-Id", "")
    names = {v.lower() for v in (user_id, h.get("X-Remote-User-Name", ""), h.get("X-Remote-User-Display-Name", "")) if v}
    display = h.get("X-Remote-User-Display-Name") or h.get("X-Remote-User-Name") or "Home Assistant"
    ua = h.get("User-Agent", "")
    forced = request.query.get("as", "")
    matched: set[str] = set()
    for ep in store.endpoints:
        if forced:
            if ep["id"] == forced:
                matched.add(ep["id"])
            continue
        if ep["match_user_agent"] and re.search(ep["match_user_agent"], ua):
            matched.add(ep["id"])
        elif names & {u.lower() for u in ep["match_users"]}:
            matched.add(ep["id"])
    return user_id, display, matched


def is_kiosk(store: Store, endpoints: set[str]) -> bool:
    """Wall screens get the calling UI but not the settings."""
    return any((ep := store.endpoint(e)) and ep["kind"] == "screen" and ep["open_service"] for e in endpoints)


@web.middleware
async def only_ingress(request: web.Request, handler):
    if not DEV_MODE and request.remote != INGRESS_IP:
        return web.Response(status=403, text="Only reachable through Home Assistant")
    return await handler(request)


class IngressApp:
    def __init__(self, store: Store, opts: dict, ha: HomeAssistant, peers: PeerManager, calls: CallManager) -> None:
        self.store, self.opts, self.ha, self.peers, self.calls = store, opts, ha, peers, calls

    def build(self) -> web.Application:
        app = web.Application(middlewares=[only_ingress])
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/ws", self.websocket)
        r.add_get("/api/state", self.state)
        r.add_post("/api/endpoints", self.save_endpoint)
        r.add_delete("/api/endpoints/{id}", self.delete_endpoint)
        r.add_post("/api/invite", self.invite)
        r.add_post("/api/join", self.join)
        r.add_patch("/api/contacts/{id}", self.update_contact)
        r.add_delete("/api/contacts/{id}", self.delete_contact)
        r.add_static("/static", STATIC_DIR, show_index=False)
        return app

    # --- helpers ---------------------------------------------------------------------
    def _guard(self, request: web.Request) -> None:
        _, _, eps = identify(request, self.store)
        if is_kiosk(self.store, eps):
            raise web.HTTPForbidden(text="Settings are not available on wall screens")

    @staticmethod
    async def _body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text="JSON body expected")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="JSON object expected")
        return data

    async def _suggested_urls(self) -> list[str]:
        urls = [self.opts["peer_url"].rstrip("/")] if self.opts.get("peer_url") else []
        for ip in await self.ha.host_ips():
            u = f"http://{ip}:{PEER_PORT}"
            if u not in urls:
                urls.append(u)
        return urls

    def _contacts_admin(self) -> list[dict]:
        out = []
        for c in self.store.contacts:
            pub = Store.public_contact(c)
            pub.update(self.peers.status(c["id"]))
            out.append(pub)
        return out

    # --- routes ------------------------------------------------------------------------
    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def state(self, request: web.Request) -> web.Response:
        user_id, display, eps = identify(request, self.store)
        kiosk = is_kiosk(self.store, eps)
        body = {
            "version": VERSION,
            "instance": {"id": self.store.instance_id, "name": self.peers.my_name},
            "me": {"display": display, "user_id": user_id, "endpoints": sorted(eps),
                   "user_agent": request.headers.get("User-Agent", ""), "settings": not kiosk},
            "ice": ice_servers(self.opts),
            "ring_duration": self.calls.ring_duration,
            "contacts": self.calls.contacts_view(),
        }
        if not kiosk:
            body["admin"] = {
                "endpoints": self.store.endpoints,
                "contacts": self._contacts_admin(),
                "suggested_urls": await self._suggested_urls(),
                "peer_port": PEER_PORT,
            }
        return web.json_response(body, headers={"Cache-Control": "no-store"})

    async def save_endpoint(self, request: web.Request) -> web.Response:
        self._guard(request)
        try:
            ep = self.store.upsert_endpoint(await self._body(request))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err))
        await self.peers.broadcast_directory()
        return web.json_response(ep)

    async def delete_endpoint(self, request: web.Request) -> web.Response:
        self._guard(request)
        if not self.store.delete_endpoint(request.match_info["id"]):
            raise web.HTTPNotFound()
        await self.peers.broadcast_directory()
        return web.json_response({"ok": True})

    async def invite(self, request: web.Request) -> web.Response:
        self._guard(request)
        body = await self._body(request)
        try:
            return web.json_response(self.peers.create_invite(str(body.get("url", "")).strip()))
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err))

    async def join(self, request: web.Request) -> web.Response:
        self._guard(request)
        body = await self._body(request)
        try:
            contact = await self.peers.join(str(body.get("code", "")), str(body.get("url", "")).strip())
        except (ValueError, ConnectionError, PermissionError) as err:
            raise web.HTTPBadRequest(text=str(err))
        await self.calls.push_directory()
        return web.json_response(Store.public_contact(contact))

    async def update_contact(self, request: web.Request) -> web.Response:
        self._guard(request)
        contact = self.store.update_contact(request.match_info["id"], await self._body(request))
        if contact is None:
            raise web.HTTPNotFound()
        self.peers.ensure_dialers()
        if self.peers.online(contact["id"]):
            await self.peers.send(contact["id"], self.peers.hello(contact["id"]))
        await self.calls.push_directory()
        return web.json_response(Store.public_contact(contact))

    async def delete_contact(self, request: web.Request) -> web.Response:
        self._guard(request)
        if not self.store.contact(request.match_info["id"]):
            raise web.HTTPNotFound()
        await self.peers.remove_contact(request.match_info["id"])
        return web.json_response({"ok": True})

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=256 * 1024)
        await ws.prepare(request)
        user_id, display, eps = identify(request, self.store)
        client = Client(ws=ws, user_id=user_id, display=display, endpoints=eps)
        await client.send({"type": "welcome", "contacts": self.calls.contacts_view(), "endpoints": sorted(eps)})
        await self.calls.add_client(client)
        try:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    msg = json.loads(raw.data)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self.calls.from_client(client, msg)
        finally:
            await self.calls.remove_client(client)
        return ws
