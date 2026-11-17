"""Peer link between HA Visio instances (add-on ↔ add-on).

Each pair of instances shares a secret created at pairing time. They talk over one
WebSocket on the peer port (8100 by default):

1. The listening side sends a challenge (its id + a nonce).
2. The dialing side answers with its id, its own nonce and an HMAC proving it knows
   the secret (or, the very first time, the single-use invitation it was given).
3. The listening side proves it knows the secret too (mutual authentication).
4. Every later frame carries a sequence number and an HMAC under a per-session key,
   so frames cannot be forged, altered or replayed.

Media never goes through here: only call signalling and presence.
Both sides may dial; when two links exist, the one dialed by the smaller id wins.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

from .config import PROTOCOL, VERSION
from .store import Store, new_secret

log = logging.getLogger("visio")

INVITE_TTL = 15 * 60
CODE_PREFIX = "HAV1."
MAX_MSG = 256 * 1024
HANDSHAKE_TIMEOUT = 10


def _mac(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


def _same(a: object, b: str) -> bool:
    return isinstance(a, str) and hmac.compare_digest(a, b)


def encode_code(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_code(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(CODE_PREFIX):
        raise ValueError("not a HA Visio invitation code")
    body = code[len(CODE_PREFIX):]
    body += "=" * (-len(body) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(body))
    except (ValueError, TypeError) as err:
        raise ValueError("damaged invitation code") from err
    for key in ("id", "url", "invite", "secret"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError("incomplete invitation code")
    if data.get("exp", 0) < time.time():
        raise ValueError("invitation code expired")
    return data


class PeerLink:
    """One authenticated WebSocket to a peer instance."""

    def __init__(self, peer_id: str, ws, dialer: str, key: bytes) -> None:
        self.peer_id = peer_id
        self.ws = ws
        self.dialer = dialer
        self.key = key
        self.seq_out = 0
        self.seq_in = 0
        self.lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self.ws.closed

    def _sign(self, seq: int, body: str) -> str:
        return hmac.new(self.key, f"{seq}|{body}".encode(), hashlib.sha256).hexdigest()

    async def send(self, msg: dict) -> bool:
        if self.ws.closed:
            return False
        async with self.lock:
            self.seq_out += 1
            body = json.dumps(msg, separators=(",", ":"))
            frame = {"s": self.seq_out, "m": body, "h": self._sign(self.seq_out, body)}
            try:
                await self.ws.send_str(json.dumps(frame))
                return True
            except Exception:  # noqa: BLE001
                return False

    def unwrap(self, raw: str) -> dict | None:
        try:
            frame = json.loads(raw)
            seq, body = int(frame["s"]), str(frame["m"])
        except (ValueError, KeyError, TypeError):
            return None
        if seq <= self.seq_in or not _same(frame.get("h"), self._sign(seq, body)):
            return None
        self.seq_in = seq
        try:
            msg = json.loads(body)
        except ValueError:
            return None
        return msg if isinstance(msg, dict) else None


class PeerManager:
    def __init__(
        self,
        store: Store,
        opts: dict,
        on_message: Callable[[str, dict], Awaitable[None]],
        on_status: Callable[[str], Awaitable[None]],
        port: int,
    ) -> None:
        self.store = store
        self.opts = opts
        self.on_message = on_message
        self.on_status = on_status
        self.port = port
        self.links: dict[str, PeerLink] = {}
        self.dialers: dict[str, asyncio.Task] = {}
        self.invites: dict[str, dict] = {}
        self.last_error: dict[str, str] = {}
        self.session: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None
        self.running = False

    @property
    def my_id(self) -> str:
        return self.store.instance_id

    @property
    def my_name(self) -> str:
        return self.opts.get("instance_name") or "Home Assistant"

    # --- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self.running = True
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=8))
        app = web.Application()
        app.router.add_get("/peer", self.handle_inbound)
        app.router.add_get("/", self.handle_root)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", self.port).start()
        log.info("Peer link listening on port %s", self.port)
        self.ensure_dialers()

    async def stop(self) -> None:
        self.running = False
        for task in self.dialers.values():
            task.cancel()
        for link in list(self.links.values()):
            await link.ws.close()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    # --- public API ----------------------------------------------------------
    def online(self, peer_id: str) -> bool:
        link = self.links.get(peer_id)
        return bool(link and not link.closed)

    async def send(self, peer_id: str, msg: dict) -> bool:
        link = self.links.get(peer_id)
        if not link or link.closed:
            return False
        return await link.send(msg)

    def hello(self, peer_id: str) -> dict:
        return {
            "type": "hello",
            "name": self.my_name,
            "version": VERSION,
            "endpoints": [
                {k: e[k] for k in ("id", "name", "kind", "icon", "ring_all")}
                for e in self.store.shared_endpoints(peer_id)
            ],
        }

    async def broadcast_directory(self) -> None:
        for peer_id, link in list(self.links.items()):
            await link.send(self.hello(peer_id))

    def create_invite(self, my_url: str) -> dict:
        if not my_url:
            raise ValueError("this instance's peer address is required")
        now = time.time()
        self.invites = {k: v for k, v in self.invites.items() if v["exp"] > now}
        invite, secret, exp = secrets.token_urlsafe(16), new_secret(), int(now + INVITE_TTL)
        self.invites[invite] = {"secret": secret, "exp": exp}
        code = encode_code(
            {"v": PROTOCOL, "id": self.my_id, "name": self.my_name, "url": my_url.rstrip("/"),
             "invite": invite, "secret": secret, "exp": exp}
        )
        return {"code": code, "expires": exp}

    async def join(self, code: str, my_url: str) -> dict:
        data = decode_code(code)
        if data["id"] == self.my_id:
            raise ValueError("this code was created by this instance")
        target = {"id": data["id"], "url": data["url"], "secret": data["secret"]}
        try:
            link, name = await self._dial(target, pair={"invite": data["invite"], "url": my_url})
        except Exception as err:  # noqa: BLE001
            raise ConnectionError(f"cannot reach {data['url']}: {err}") from err
        contact = self.store.add_contact(data["id"], name or data.get("name", ""), data["url"], data["secret"])
        log.info("Paired with %s (%s)", contact["name"], contact["url"])
        asyncio.create_task(self._run(link))
        self.ensure_dialers()
        return contact

    async def remove_contact(self, peer_id: str) -> None:
        link = self.links.get(peer_id)
        if link:
            await link.send({"type": "unpaired"})
            await link.ws.close()
        self.store.delete_contact(peer_id)
        self.ensure_dialers()
        await self.on_status(peer_id)

    def ensure_dialers(self) -> None:
        if not self.running:
            return
        wanted = {c["id"] for c in self.store.contacts}
        for peer_id in list(self.dialers):
            if peer_id not in wanted:
                self.dialers.pop(peer_id).cancel()
        for peer_id in wanted:
            if peer_id not in self.dialers or self.dialers[peer_id].done():
                self.dialers[peer_id] = asyncio.create_task(self._dial_loop(peer_id))

    def status(self, peer_id: str) -> dict:
        return {"online": self.online(peer_id), "error": None if self.online(peer_id) else self.last_error.get(peer_id)}

    # --- inbound ---------------------------------------------------------------
    async def handle_root(self, request: web.Request) -> web.Response:
        return web.json_response({"service": "ha-visio", "protocol": PROTOCOL})

    async def handle_inbound(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MSG)
        await ws.prepare(request)
        nonce_s = secrets.token_hex(16)
        await ws.send_json({"type": "challenge", "v": PROTOCOL, "id": self.my_id, "nonce": nonce_s})
        try:
            msg = await ws.receive_json(timeout=HANDSHAKE_TIMEOUT)
        except Exception:  # noqa: BLE001
            await ws.close()
            return ws
        peer_id, nonce_c, kind = str(msg.get("id", "")), str(msg.get("nonce", "")), msg.get("type")
        secret: str | None = None
        if peer_id and nonce_c and peer_id != self.my_id:
            if kind == "auth":
                contact = self.store.contact(peer_id)
                if contact and _same(msg.get("mac"), _mac(contact["secret"], "auth", nonce_s, peer_id, self.my_id)):
                    secret = contact["secret"]
            elif kind == "pair":
                inv = self.invites.get(str(msg.get("invite", "")))
                if inv and inv["exp"] > time.time() and _same(
                    msg.get("mac"), _mac(inv["secret"], "pair", nonce_s, peer_id, self.my_id)
                ):
                    self.invites.pop(str(msg["invite"]), None)
                    secret = inv["secret"]
                    contact = self.store.add_contact(peer_id, str(msg.get("name", ""))[:60], str(msg.get("url", "")), secret)
                    log.info("Paired with %s (invited)", contact["name"])
                    self.ensure_dialers()
        if secret is None:
            log.warning("Rejected peer connection from %s (id %s…)", request.remote, peer_id[:8])
            await ws.close(code=4401, message=b"unauthorized")
            return ws
        await ws.send_json(
            {"type": "auth_ok", "name": self.my_name, "mac": _mac(secret, "ok", nonce_c, self.my_id, peer_id)}
        )
        link = PeerLink(peer_id, ws, dialer=peer_id, key=self._key(secret, nonce_s, nonce_c))
        await self._run(link)
        return ws

    # --- outbound --------------------------------------------------------------
    @staticmethod
    def _key(secret: str, nonce_s: str, nonce_c: str) -> bytes:
        return hmac.new(secret.encode(), f"key|{nonce_s}|{nonce_c}".encode(), hashlib.sha256).digest()

    async def _dial(self, target: dict, pair: dict | None = None) -> tuple[PeerLink, str]:
        assert self.session
        peer_id, secret = target["id"], target["secret"]
        ws = await self.session.ws_connect(f"{target['url']}/peer", heartbeat=20, max_msg_size=MAX_MSG)
        try:
            ch = await ws.receive_json(timeout=HANDSHAKE_TIMEOUT)
            if ch.get("type") != "challenge" or ch.get("id") != peer_id:
                raise ConnectionError("this address belongs to another instance")
            nonce_s, nonce_c = str(ch.get("nonce", "")), secrets.token_hex(16)
            if pair:
                await ws.send_json({
                    "type": "pair", "id": self.my_id, "name": self.my_name, "url": pair["url"],
                    "invite": pair["invite"], "nonce": nonce_c,
                    "mac": _mac(secret, "pair", nonce_s, self.my_id, peer_id),
                })
            else:
                await ws.send_json({
                    "type": "auth", "id": self.my_id, "nonce": nonce_c,
                    "mac": _mac(secret, "auth", nonce_s, self.my_id, peer_id),
                })
            try:
                ok = await ws.receive_json(timeout=HANDSHAKE_TIMEOUT)
            except TypeError as err:  # closed instead of answering
                raise PermissionError("rejected (unknown or revoked pairing)") from err
            if ok.get("type") != "auth_ok" or not _same(ok.get("mac"), _mac(secret, "ok", nonce_c, peer_id, self.my_id)):
                raise PermissionError("peer failed authentication")
        except BaseException:
            await ws.close()
            raise
        return PeerLink(peer_id, ws, dialer=self.my_id, key=self._key(secret, nonce_s, nonce_c)), str(ok.get("name", ""))

    async def _dial_loop(self, peer_id: str) -> None:
        backoff = 2.0
        while self.running:
            contact = self.store.contact(peer_id)
            if contact is None:
                return
            if self.online(peer_id) or not contact.get("url"):
                backoff = 2.0
                await asyncio.sleep(5)
                continue
            if self.my_id > peer_id:
                await asyncio.sleep(8)  # head start for the preferred dialer
                if self.online(peer_id):
                    continue
            try:
                link, _ = await self._dial(contact)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                msg = str(err) or err.__class__.__name__
                if self.last_error.get(peer_id) != msg:
                    log.warning("Cannot reach %s at %s: %s", contact["name"], contact["url"], msg)
                    self.last_error[peer_id] = msg
                    await self.on_status(peer_id)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 2.0
            await self._run(link)

    # --- link lifecycle ----------------------------------------------------------
    def _register(self, link: PeerLink) -> bool:
        current = self.links.get(link.peer_id)
        if current and not current.closed:
            preferred = min(self.my_id, link.peer_id)
            if link.dialer != preferred or current.dialer == preferred:
                return False
            self.links[link.peer_id] = link
            asyncio.create_task(current.ws.close())
            return True
        self.links[link.peer_id] = link
        return True

    async def _run(self, link: PeerLink) -> None:
        if not self._register(link):
            await link.ws.close()
            return
        self.last_error.pop(link.peer_id, None)
        contact = self.store.contact(link.peer_id)
        log.info("Connected to %s", contact["name"] if contact else link.peer_id)
        try:
            await link.send(self.hello(link.peer_id))
            await self.on_status(link.peer_id)
            async for raw in link.ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                msg = link.unwrap(raw.data)
                if msg is None:
                    log.warning("Dropped invalid frame from %s, closing link", link.peer_id[:8])
                    break
                await self._dispatch(link, msg)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            log.warning("Peer link error: %s", err)
        finally:
            await link.ws.close()
            if self.links.get(link.peer_id) is link:
                del self.links[link.peer_id]
                contact = self.store.contact(link.peer_id)
                log.info("Disconnected from %s", contact["name"] if contact else link.peer_id[:8])
                await self.on_status(link.peer_id)

    async def _dispatch(self, link: PeerLink, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            eps = msg.get("endpoints")
            self.store.set_remote_endpoints(link.peer_id, eps if isinstance(eps, list) else [])
            await self.on_status(link.peer_id)
        elif kind == "unpaired":
            log.info("Peer %s removed the pairing", link.peer_id[:8])
            self.store.delete_contact(link.peer_id)
            self.ensure_dialers()
            await link.ws.close()
        else:
            try:
                await self.on_message(link.peer_id, msg)
            except Exception:  # noqa: BLE001
                log.exception("Error handling %s from peer", kind)
