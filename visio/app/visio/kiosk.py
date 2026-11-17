"""Drives kiosk screens (Kiosk Satellite or any kiosk controllable by HA services).

On an incoming call: switch off/on the configured entities (remembering their state)
and open the add-on page on the kiosk. When the call ends: close the page, press the
"back to dashboard" button and restore the entities.
"""

from __future__ import annotations

import asyncio
import logging

from .ha import HomeAssistant

log = logging.getLogger("visio")


class Kiosks:
    def __init__(self, ha: HomeAssistant) -> None:
        self.ha = ha
        self.saved: dict[str, dict[str, str]] = {}      # endpoint id → {entity: previous state}
        self.tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def drives(ep: dict) -> bool:
        return bool(ep.get("open_service") or ep.get("turn_off") or ep.get("turn_on"))

    def prepare(self, ep: dict) -> None:
        if not self.drives(ep) or ep["id"] in self.saved:
            return
        self.saved[ep["id"]] = {}
        self.tasks[ep["id"]] = asyncio.create_task(self._prepare(ep))

    async def _prepare(self, ep: dict) -> None:
        saved = self.saved[ep["id"]]
        for action, entities in (("turn_off", ep.get("turn_off", [])), ("turn_on", ep.get("turn_on", []))):
            for ent in entities:
                saved[ent] = await self.ha.state(ent) or ""
                await self.ha.call_service(f"{ent.split('.')[0]}.{action}", {"entity_id": ent})
        await self.ha.load_slug()
        if ep.get("open_service") and ep.get("kiosk_url") and self.ha.slug:
            url = f"{ep['kiosk_url']}/hassio/ingress/{self.ha.slug}"
            await self.ha.call_service(ep["open_service"], {"url": url, "hold_mode": True})
            log.info("Opened call page on %s", ep["name"])

    def release(self, ep: dict, delay: float = 2.0) -> None:
        if ep["id"] not in self.saved:
            return
        saved = self.saved.pop(ep["id"])
        task = self.tasks.pop(ep["id"], None)
        asyncio.create_task(self._release(ep, saved, task, delay))

    async def _release(self, ep: dict, saved: dict, task: asyncio.Task | None, delay: float) -> None:
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(delay)  # let the "call ended" screen show
        if ep.get("close_service"):
            await self.ha.call_service(ep["close_service"])
        if ep.get("return_button"):
            await self.ha.call_service("button.press", {"entity_id": ep["return_button"]})
        for ent, prev in saved.items():
            if prev in ("on", "off"):
                await self.ha.call_service(f"{ent.split('.')[0]}.turn_{prev}", {"entity_id": ent})
        log.info("Released %s", ep["name"])
