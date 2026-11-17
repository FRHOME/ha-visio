"""Small client for the local Home Assistant, through the Supervisor proxy.

Uses only the add-on's own SUPERVISOR_TOKEN: no user token is ever needed.
"""

from __future__ import annotations

import logging

import aiohttp

from .config import SUPERVISOR_TOKEN, SUPERVISOR_URL

log = logging.getLogger("visio")

SENSOR = "sensor.ha_visio"


class HomeAssistant:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self.slug: str | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        await self.load_slug()

    async def stop(self) -> None:
        if self.session:
            await self.session.close()

    async def _get(self, path: str) -> dict | None:
        if not self.session:
            return None
        try:
            async with self.session.get(f"{SUPERVISOR_URL}{path}") as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception as err:  # noqa: BLE001
            log.debug("GET %s failed: %s", path, err)
            return None

    async def _post(self, path: str, body: dict) -> bool:
        if not self.session:
            return False
        try:
            async with self.session.post(f"{SUPERVISOR_URL}{path}", json=body) as r:
                if r.status >= 400:
                    log.warning("POST %s → HTTP %s %s", path, r.status, (await r.text())[:200])
                    return False
                return True
        except Exception as err:  # noqa: BLE001
            log.warning("POST %s failed: %s", path, err)
            return False

    async def load_slug(self) -> None:
        if self.slug:
            return
        data = await self._get("/addons/self/info")
        if data:
            self.slug = data.get("data", {}).get("slug")
            log.info("Add-on slug: %s", self.slug)

    async def host_ips(self) -> list[str]:
        """IPv4 addresses of the host, to suggest a peer URL."""
        data = await self._get("/network/info")
        ips: list[str] = []
        for iface in (data or {}).get("data", {}).get("interfaces", []):
            if not iface.get("enabled", True) or not iface.get("connected", True):
                continue
            for addr in (iface.get("ipv4") or {}).get("address", []) or []:
                ips.append(addr.split("/")[0])
        return ips

    async def call_service(self, service: str, data: dict | None = None) -> bool:
        if not service or "." not in service:
            return False
        domain, name = service.split(".", 1)
        return await self._post(f"/core/api/services/{domain}/{name}", data or {})

    async def state(self, entity_id: str) -> str | None:
        data = await self._get(f"/core/api/states/{entity_id}")
        return (data or {}).get("state")

    async def fire_event(self, event: str, data: dict) -> None:
        await self._post(f"/core/api/events/{event}", data)

    async def publish(self, state: str, **attrs) -> None:
        await self._post(
            f"/core/api/states/{SENSOR}",
            {"state": state, "attributes": {"friendly_name": "HA Visio", "icon": "mdi:phone-in-talk", **attrs}},
        )
