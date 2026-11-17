"""Installs the dashboard module (ha-visio.js) into Home Assistant.

Copies it to /config/www/ha-visio/ and registers it as a Lovelace resource, so the
incoming-call banner works on every dashboard and <ha-visio-card> is available.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import STATIC_DIR, VERSION
from .ha import HomeAssistant

log = logging.getLogger("visio")

HA_CONFIG = Path(os.environ.get("VISIO_HA_CONFIG", "/homeassistant"))
URL_BASE = "/local/ha-visio/ha-visio.js"


async def install_card(ha: HomeAssistant, config_dir: Path = HA_CONFIG) -> None:
    if not config_dir.is_dir():
        log.warning("Home Assistant config folder not mapped: dashboard card not installed")
        return
    src = (STATIC_DIR / "ha-visio.js").read_bytes()
    dst = config_dir / "www" / "ha-visio" / "ha-visio.js"
    if not (config_dir / "www").is_dir():
        log.warning("%s/www did not exist: Home Assistant must be restarted once to serve the card", config_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or dst.read_bytes() != src:
        dst.write_bytes(src)
        log.info("Dashboard module written to %s", dst)
    await ha.load_slug()
    url = f"{URL_BASE}?v={VERSION}&slug={ha.slug or ''}"
    try:
        (resources,) = await ha.core_ws({"type": "lovelace/resources"})
        mine = [r for r in resources or [] if str(r.get("url", "")).split("?")[0] == URL_BASE]
        if mine and mine[0].get("url") == url:
            return
        if mine:
            await ha.core_ws({"type": "lovelace/resources/update", "resource_id": mine[0]["id"], "res_type": "module", "url": url})
            log.info("Dashboard resource updated: %s", url)
        else:
            await ha.core_ws({"type": "lovelace/resources/create", "res_type": "module", "url": url})
            log.info("Dashboard resource added: %s", url)
    except Exception as err:  # noqa: BLE001
        log.warning("Could not register the dashboard resource (%s). Add it by hand: %s (JavaScript module)", err, url)
