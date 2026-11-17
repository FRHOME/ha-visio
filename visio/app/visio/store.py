"""Persistent directory: this instance's identity, its local endpoints and its contacts.

Stored as JSON in /data/visio.json (backed up with the add-on). Secrets never leave
this file except inside a pairing code that the user copies by hand.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path

log = logging.getLogger("visio")

ENDPOINT_KINDS = ("screen", "person")

ENDPOINT_DEFAULTS: dict = {
    "name": "",
    "kind": "screen",          # screen = a fixed display (wall tablet…), person = a HA user
    "icon": "mdi:tablet",
    "match_user_agent": "",    # regex on the browser User-Agent
    "match_users": [],         # HA user names / ids
    "ring_all": True,          # rings when a contact calls "everyone here"
    "kiosk_url": "",           # HA URL as seen by the kiosk, e.g. http://127.0.0.1:2325
    "open_service": "",        # e.g. esphome.ks_tablet_open_url
    "close_service": "",       # e.g. esphome.ks_tablet_close_url
    "return_button": "",       # button entity pressed after the call
    "turn_off": [],            # entities switched off during the call, restored after
    "turn_on": [],             # entities switched on during the call, restored after
}

CONTACT_PUBLIC = ("id", "name", "url", "share", "paired_at", "remote_endpoints")


def new_id() -> str:
    return uuid.uuid4().hex


def _clean_list(value) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[\n,]", value)]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def clean_endpoint(raw: dict, existing: dict | None = None) -> dict:
    ep = dict(ENDPOINT_DEFAULTS)
    if existing:
        ep.update(existing)
    for key in ENDPOINT_DEFAULTS:
        if key in raw:
            ep[key] = raw[key]
    ep["id"] = (existing or {}).get("id") or raw.get("id") or new_id()
    ep["name"] = str(ep["name"]).strip()[:60] or "Screen"
    if ep["kind"] not in ENDPOINT_KINDS:
        ep["kind"] = "screen"
    for key in ("icon", "match_user_agent", "kiosk_url", "open_service", "close_service", "return_button"):
        ep[key] = str(ep.get(key) or "").strip()
    ep["kiosk_url"] = ep["kiosk_url"].rstrip("/")
    for key in ("match_users", "turn_off", "turn_on"):
        ep[key] = _clean_list(ep.get(key))
    ep["ring_all"] = bool(ep.get("ring_all"))
    if ep["match_user_agent"]:
        try:
            re.compile(ep["match_user_agent"])
        except re.error as err:
            raise ValueError(f"Invalid User-Agent regex: {err}") from err
    return ep


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {}

    # --- persistence -------------------------------------------------------
    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        except ValueError:
            backup = self.path.with_suffix(f".broken-{int(time.time())}")
            self.path.rename(backup)
            log.error("Directory file was corrupt, moved to %s", backup)
            self.data = {}
        self.data.setdefault("instance_id", new_id())
        self.data.setdefault("endpoints", [])
        self.data.setdefault("contacts", [])
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # --- identity ----------------------------------------------------------
    @property
    def instance_id(self) -> str:
        return self.data["instance_id"]

    # --- endpoints ---------------------------------------------------------
    @property
    def endpoints(self) -> list[dict]:
        return self.data["endpoints"]

    def endpoint(self, ep_id: str) -> dict | None:
        return next((e for e in self.endpoints if e["id"] == ep_id), None)

    def upsert_endpoint(self, raw: dict) -> dict:
        existing = self.endpoint(raw.get("id", "")) if raw.get("id") else None
        ep = clean_endpoint(raw, existing)
        if existing:
            existing.clear()
            existing.update(ep)
        else:
            self.endpoints.append(ep)
        self.save()
        return ep

    def delete_endpoint(self, ep_id: str) -> bool:
        before = len(self.endpoints)
        self.data["endpoints"] = [e for e in self.endpoints if e["id"] != ep_id]
        for c in self.contacts:
            c["share"] = [s for s in c.get("share", []) if s != ep_id]
        self.save()
        return len(self.endpoints) != before

    # --- contacts ----------------------------------------------------------
    @property
    def contacts(self) -> list[dict]:
        return self.data["contacts"]

    def contact(self, contact_id: str) -> dict | None:
        return next((c for c in self.contacts if c["id"] == contact_id), None)

    def add_contact(self, contact_id: str, name: str, url: str, secret: str) -> dict:
        c = self.contact(contact_id)
        if c is None:
            c = {"id": contact_id, "share": [], "paired_at": int(time.time()), "remote_endpoints": []}
            self.contacts.append(c)
        c.update({"name": (name or "Home Assistant")[:60], "url": (url or "").rstrip("/"), "secret": secret})
        self.save()
        return c

    def update_contact(self, contact_id: str, raw: dict) -> dict | None:
        c = self.contact(contact_id)
        if c is None:
            return None
        if "name" in raw:
            c["name"] = str(raw["name"]).strip()[:60] or c["name"]
        if "url" in raw:
            c["url"] = str(raw["url"]).strip().rstrip("/")
        if "share" in raw:
            known = {e["id"] for e in self.endpoints}
            c["share"] = [s for s in _clean_list(raw["share"]) if s in known]
        self.save()
        return c

    def delete_contact(self, contact_id: str) -> bool:
        before = len(self.contacts)
        self.data["contacts"] = [c for c in self.contacts if c["id"] != contact_id]
        self.save()
        return len(self.contacts) != before

    def set_remote_endpoints(self, contact_id: str, endpoints: list[dict]) -> None:
        c = self.contact(contact_id)
        if c is None:
            return
        clean = [
            {
                "id": str(e.get("id", ""))[:64],
                "name": str(e.get("name", ""))[:60],
                "kind": e.get("kind") if e.get("kind") in ENDPOINT_KINDS else "screen",
                "icon": str(e.get("icon", ""))[:40],
                "ring_all": bool(e.get("ring_all", True)),
            }
            for e in endpoints
            if isinstance(e, dict) and e.get("id")
        ][:50]
        if clean != c.get("remote_endpoints"):
            c["remote_endpoints"] = clean
            self.save()

    def shared_endpoints(self, contact_id: str) -> list[dict]:
        """Local endpoints that this contact may see and call."""
        c = self.contact(contact_id)
        if c is None:
            return []
        share = c.get("share") or []
        return [e for e in self.endpoints if not share or e["id"] in share]

    @staticmethod
    def public_contact(c: dict) -> dict:
        return {k: c.get(k) for k in CONTACT_PUBLIC}


def new_secret() -> str:
    return secrets.token_urlsafe(32)
