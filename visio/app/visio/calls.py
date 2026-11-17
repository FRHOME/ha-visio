"""Calls between a local browser and a browser on a peer instance.

The server only relays signalling: browser ⇄ this add-on ⇄ peer add-on ⇄ browser.
Audio and video flow directly between the two browsers (WebRTC).

States of a call: ringing → active → (removed). The callee side owns the ring
timeout; the caller side keeps a safety timeout in case the peer never answers.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from aiohttp import web

from .ha import HomeAssistant
from .kiosk import Kiosks
from .peers import PeerManager
from .store import Store

log = logging.getLogger("visio")

END_REASONS = {"hangup", "declined", "missed", "busy", "unavailable", "offline", "disconnected", "cancelled"}


@dataclass(eq=False)
class Client:
    """A browser page connected to the ingress WebSocket."""

    ws: web.WebSocketResponse
    user_id: str
    display: str
    endpoints: set[str]
    call_id: str | None = None

    async def send(self, msg: dict) -> None:
        if self.ws.closed:
            return
        try:
            await self.ws.send_json(msg)
        except Exception:  # noqa: BLE001
            pass


@dataclass(eq=False)
class Call:
    id: str
    peer_id: str
    direction: str                      # "out" (we called) | "in" (they called)
    remote_name: str                    # who/what is on the other side
    state: str = "ringing"
    client: Client | None = None        # local browser carrying the media
    targets: list[str] = field(default_factory=list)   # local endpoints ringing (incoming)
    ringing: set[Client] = field(default_factory=set)
    timer: asyncio.Task | None = None
    started: float = field(default_factory=time.time)
    answered: float = 0.0


class CallManager:
    def __init__(self, store: Store, opts: dict, ha: HomeAssistant, kiosks: Kiosks) -> None:
        self.store = store
        self.opts = opts
        self.ha = ha
        self.kiosks = kiosks
        self.peers: PeerManager | None = None
        self.clients: set[Client] = set()
        self.calls: dict[str, Call] = {}

    @property
    def ring_duration(self) -> int:
        return int(self.opts.get("ring_duration") or 45)

    # --- directory view for browsers ------------------------------------------
    def contacts_view(self) -> list[dict]:
        assert self.peers
        out = []
        for c in self.store.contacts:
            st = self.peers.status(c["id"])
            out.append({
                "id": c["id"], "name": c["name"], "online": st["online"], "error": st["error"],
                "endpoints": c.get("remote_endpoints") or [],
            })
        return out

    async def push_directory(self, _peer_id: str | None = None) -> None:
        msg = {"type": "directory", "contacts": self.contacts_view()}
        for client in list(self.clients):
            await client.send(msg)

    # --- helpers -----------------------------------------------------------------
    def endpoint_busy(self, ep_id: str) -> bool:
        if any(c.call_id and ep_id in c.endpoints for c in self.clients):
            return True
        for call in self.calls.values():
            if call.direction == "in" and ep_id in call.targets:
                return True
        return False

    def _caller_label(self, client: Client) -> str:
        for ep_id in client.endpoints:
            ep = self.store.endpoint(ep_id)
            if ep and ep["kind"] == "screen":
                return ep["name"]
        return client.display

    async def publish(self) -> None:
        calls = list(self.calls.values())
        if any(c.state == "active" for c in calls):
            state = "active"
        elif any(c.direction == "in" for c in calls):
            state = "ringing"
        elif calls:
            state = "calling"
        else:
            state = "idle"
        await self.ha.publish(
            state,
            calls=[
                {"id": c.id, "direction": c.direction, "state": c.state, "remote": c.remote_name,
                 "contact": (self.store.contact(c.peer_id) or {}).get("name", "")}
                for c in calls
            ],
        )

    def _timer(self, call: Call, seconds: int, reason: str) -> None:
        async def fire() -> None:
            await asyncio.sleep(seconds)
            if call.state == "ringing" and self.calls.get(call.id) is call:
                await self.end(call, reason, notify_peer=True)

        call.timer = asyncio.create_task(fire())

    # --- browser side -------------------------------------------------------------
    async def add_client(self, client: Client) -> None:
        self.clients.add(client)
        for call in self.calls.values():
            if call.direction == "in" and call.state == "ringing" and client.endpoints & set(call.targets):
                call.ringing.add(client)
                await client.send(self._incoming_msg(call))

    async def remove_client(self, client: Client) -> None:
        self.clients.discard(client)
        for call in list(self.calls.values()):
            call.ringing.discard(client)
            if call.client is client:
                await self.end(call, "disconnected", notify_peer=True)

    async def from_client(self, client: Client, msg: dict) -> None:
        kind = msg.get("type")
        call = self.calls.get(str(msg.get("call", "")))
        if kind == "call":
            await self.place_call(client, str(msg.get("contact", "")), str(msg.get("endpoint", "*")))
        elif call is None:
            if kind in ("accept", "decline", "hangup"):
                await client.send({"type": "ended", "call": msg.get("call"), "reason": "cancelled"})
        elif kind == "accept":
            await self.accept(call, client)
        elif kind == "decline" and call.direction == "in" and call.state == "ringing" and client in call.ringing:
            await self.end(call, "declined", notify_peer=True)
        elif kind == "hangup" and (call.client is client or (call.direction == "in" and client in call.ringing)):
            if call.state == "active":
                reason = "hangup"
            else:
                reason = "declined" if call.direction == "in" else "cancelled"
            await self.end(call, reason, notify_peer=True)
        elif kind == "signal" and call.client is client and call.state == "active":
            assert self.peers
            await self.peers.send(call.peer_id, {"type": "call.signal", "call": call.id, "data": msg.get("data")})

    async def place_call(self, client: Client, contact_id: str, endpoint: str) -> None:
        assert self.peers
        contact = self.store.contact(contact_id)
        if client.call_id:
            await client.send({"type": "error", "code": "busy_local"})
            return
        if contact is None:
            await client.send({"type": "error", "code": "unknown_contact"})
            return
        if not self.peers.online(contact_id):
            await client.send({"type": "error", "code": "offline"})
            return
        remote = next((e for e in contact.get("remote_endpoints") or [] if e["id"] == endpoint), None)
        if endpoint != "*" and remote is None:
            await client.send({"type": "error", "code": "unavailable"})
            return
        call = Call(
            id=uuid.uuid4().hex, peer_id=contact_id, direction="out",
            remote_name=remote["name"] if remote else contact["name"], client=client,
        )
        self.calls[call.id] = call
        client.call_id = call.id
        ok = await self.peers.send(contact_id, {
            "type": "call.invite", "call": call.id, "endpoint": endpoint,
            "from": {"name": self._caller_label(client), "instance": self.peers.my_name},
        })
        if not ok:
            await self.end(call, "offline", notify_peer=False)
            return
        log.info("Calling %s / %s", contact["name"], call.remote_name)
        await client.send({"type": "calling", "call": call.id, "contact": contact["name"], "target": call.remote_name})
        self._timer(call, self.ring_duration + 15, "missed")
        await self.publish()

    async def accept(self, call: Call, client: Client) -> None:
        assert self.peers
        if call.direction != "in" or call.state != "ringing" or client not in call.ringing or client.call_id:
            await client.send({"type": "ended", "call": call.id, "reason": "cancelled"})
            return
        call.state = "active"
        call.answered = time.time()
        call.client = client
        client.call_id = call.id
        if call.timer:
            call.timer.cancel()
        others = [c for c in call.ringing if c is not client]
        call.ringing = set()
        for other in others:
            await other.send({"type": "taken", "call": call.id})
        answered = [ep for ep in call.targets if ep in client.endpoints]
        for ep_id in call.targets:
            if ep_id not in answered and (ep := self.store.endpoint(ep_id)):
                self.kiosks.release(ep, delay=0)
        call.targets = answered
        await self.peers.send(call.peer_id, {"type": "call.accept", "call": call.id})
        log.info("Call from %s answered", call.remote_name)
        await self.ha.fire_event("ha_visio_answered", {"call_id": call.id, "from": call.remote_name})
        await self.publish()

    # --- peer side ------------------------------------------------------------------
    async def from_peer(self, peer_id: str, msg: dict) -> None:
        kind = msg.get("type")
        call_id = str(msg.get("call", ""))
        call = self.calls.get(call_id)
        if call is not None and call.peer_id != peer_id:
            return  # never let a peer touch someone else's call
        if kind == "call.invite":
            await self.incoming(peer_id, call_id, msg)
        elif call is None:
            return
        elif kind == "call.ringing" and call.direction == "out" and call.client:
            await call.client.send({"type": "ringing", "call": call.id})
        elif kind == "call.accept" and call.direction == "out" and call.state == "ringing":
            call.state = "active"
            call.answered = time.time()
            if call.timer:
                call.timer.cancel()
            if call.client:
                await call.client.send({"type": "accepted", "call": call.id})
            await self.publish()
        elif kind == "call.signal" and call.state == "active" and call.client:
            await call.client.send({"type": "signal", "call": call.id, "data": msg.get("data")})
        elif kind == "call.end":
            reason = msg.get("reason") if msg.get("reason") in END_REASONS else "hangup"
            await self.end(call, reason, notify_peer=False)

    async def incoming(self, peer_id: str, call_id: str, msg: dict) -> None:
        assert self.peers
        if not call_id or call_id in self.calls:
            return
        contact = self.store.contact(peer_id) or {"name": "?"}
        shared = self.store.shared_endpoints(peer_id)
        wanted = str(msg.get("endpoint", "*"))
        if wanted == "*":
            targets = [e for e in shared if e["ring_all"]]
        else:
            targets = [e for e in shared if e["id"] == wanted]
        if not targets:
            await self.peers.send(peer_id, {"type": "call.end", "call": call_id, "reason": "unavailable"})
            return
        free = [e for e in targets if not self.endpoint_busy(e["id"])]
        if not free:
            await self.peers.send(peer_id, {"type": "call.end", "call": call_id, "reason": "busy"})
            return
        sender = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        name = str(sender.get("name") or contact["name"])[:60]
        call = Call(id=call_id, peer_id=peer_id, direction="in", remote_name=name, targets=[e["id"] for e in free])
        self.calls[call_id] = call
        log.info("Incoming call from %s (%s) → %s", name, contact["name"], ", ".join(e["name"] for e in free))
        for ep in free:
            self.kiosks.prepare(ep)
        target_set = set(call.targets)
        for client in list(self.clients):
            if client.endpoints & target_set and not client.call_id:
                call.ringing.add(client)
                await client.send(self._incoming_msg(call))
        await self.peers.send(peer_id, {"type": "call.ringing", "call": call_id})
        self._timer(call, self.ring_duration, "missed")
        await self.ha.fire_event("ha_visio_incoming", {
            "call_id": call_id, "from": name, "contact": contact["name"],
            "endpoints": [e["name"] for e in free], "endpoint_ids": call.targets,
        })
        await self.publish()

    def _incoming_msg(self, call: Call) -> dict:
        contact = self.store.contact(call.peer_id) or {"name": ""}
        return {"type": "incoming", "call": call.id, "from": call.remote_name, "contact": contact["name"]}

    async def peer_down(self, peer_id: str) -> None:
        """A peer link dropped: ringing calls cannot complete. Active media keeps flowing."""
        assert self.peers
        if self.peers.online(peer_id):
            return
        for call in list(self.calls.values()):
            if call.peer_id == peer_id and call.state == "ringing":
                await self.end(call, "offline", notify_peer=False)

    # --- teardown ---------------------------------------------------------------------
    async def end(self, call: Call, reason: str, notify_peer: bool) -> None:
        if self.calls.get(call.id) is not call:
            return
        del self.calls[call.id]
        if call.timer:
            call.timer.cancel()
        log.info("Call %s with %s ended (%s)", call.direction, call.remote_name, reason)
        msg = {"type": "ended", "call": call.id, "reason": reason}
        for client in {call.client, *call.ringing}:
            if client is not None:
                if client.call_id == call.id:
                    client.call_id = None
                await client.send(msg)
        if notify_peer and self.peers:
            await self.peers.send(call.peer_id, {"type": "call.end", "call": call.id, "reason": reason})
        for ep_id in call.targets:
            if ep := self.store.endpoint(ep_id):
                self.kiosks.release(ep)
        contact = self.store.contact(call.peer_id) or {"name": ""}
        await self.ha.fire_event("ha_visio_ended", {
            "call_id": call.id, "direction": call.direction, "reason": reason,
            "remote": call.remote_name, "contact": contact["name"],
            "duration": int(time.time() - call.answered) if call.answered else 0,
        })
        await self.publish()
