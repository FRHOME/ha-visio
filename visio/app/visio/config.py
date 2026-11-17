"""Add-on options (read once from /data/options.json) and runtime paths."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("visio")

DATA_DIR = Path(os.environ.get("VISIO_DATA", "/data"))
OPTIONS_FILE = Path(os.environ.get("VISIO_OPTIONS", str(DATA_DIR / "options.json")))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

SUPERVISOR_URL = os.environ.get("VISIO_SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
INGRESS_IP = "172.30.32.2"
DEV_MODE = os.environ.get("VISIO_DEV") == "1"

INGRESS_PORT = int(os.environ.get("VISIO_PORT", "8099"))
PEER_PORT = int(os.environ.get("VISIO_PEER_PORT", "8100"))

VERSION = "0.2.0"
PROTOCOL = 1

DEFAULTS: dict = {
    "instance_name": "Home Assistant",
    "peer_url": "",
    "ring_duration": 45,
    "ice_servers": ["stun:stun.l.google.com:19302"],
    "turn_username": "",
    "turn_password": "",
    "log_level": "info",
}


def load_options() -> dict:
    opts = dict(DEFAULTS)
    try:
        opts.update(json.loads(OPTIONS_FILE.read_text()))
    except FileNotFoundError:
        log.warning("No %s, using defaults", OPTIONS_FILE)
    except ValueError as err:
        log.error("Invalid %s: %s", OPTIONS_FILE, err)
    return opts


def ice_servers(opts: dict) -> list[dict]:
    out = []
    for url in opts.get("ice_servers") or []:
        entry: dict = {"urls": url}
        if url.startswith(("turn:", "turns:")) and opts.get("turn_username"):
            entry["username"] = opts["turn_username"]
            entry["credential"] = opts.get("turn_password", "")
        out.append(entry)
    return out
