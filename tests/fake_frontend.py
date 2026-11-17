"""Fake Home Assistant frontend for testing ha-visio.js.

Serves a dashboard page with a <home-assistant> element whose `hass` mimics the real
one (callWS for ingress sessions, states with sensor.ha_visio), and proxies
/api/hassio_ingress/TOKEN/* to an add-on instance like the Supervisor does, adding
the X-Remote-User-* headers.

usage: fake_frontend.py PORT ADDON_PORT USER_NAME
"""
import os, sys
import aiohttp
from aiohttp import web

PORT, ADDON, USER = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "visio", "app", "static")
ENTRY = "/api/hassio_ingress/TOKEN"

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Fake HA</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;background:#111318;color:#e1e1e1;font-family:Roboto,system-ui,sans-serif;
--primary-text-color:#e1e1e1;--secondary-text-color:#9b9b9b;--primary-color:#03a9f4;--secondary-background-color:#2a2d33;--divider-color:#2f3238}
ha-card{display:block;background:#1c1f24;border-radius:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;padding:16px;max-width:1000px}
.tile{background:#1c1f24;border-radius:12px;padding:16px;height:80px}</style>
<script>
customElements.define('home-assistant', class extends HTMLElement {});
customElements.define('ha-card', class extends HTMLElement {});
customElements.define('ha-icon', class extends HTMLElement {});
</script></head><body>
<home-assistant></home-assistant>
<div class="grid"><div class="tile">Salon 21°</div><div class="tile">Lumières</div><div id="slot"></div><div class="tile">Météo</div></div>
<script type="module">
const ha = document.querySelector('home-assistant');
ha.hass = {
  language: 'fr', locale: { language: 'fr' },
  states: { 'sensor.ha_visio': { state: 'idle', attributes: { ingress_entry: '__ENTRY__' } } },
  callWS: async (m) => {
    window.__ws = (window.__ws || []).concat(m.endpoint);
    if (m.endpoint === '/ingress/session') return { session: 'sess-' + Math.random().toString(36).slice(2) };
    if (m.endpoint === '/ingress/validate_session') return {};
    throw new Error('unexpected ' + JSON.stringify(m));
  },
};
await import('/local/ha-visio/ha-visio.js?v=test&slug=test_visio');
const card = document.createElement('ha-visio-card');
card.setConfig({});
card.hass = ha.hass;
document.getElementById('slot').appendChild(card);
</script></body></html>""".replace("__ENTRY__", ENTRY)


async def page(request):
    return web.Response(text=PAGE, content_type="text/html")


async def module(request):
    return web.FileResponse(os.path.join(STATIC, "ha-visio.js"), headers={"Cache-Control": "no-store"})


async def ingress(request):
    if "ingress_session" not in request.cookies:
        return web.Response(status=401, text="no ingress session")
    path = request.match_info["tail"]
    headers = {"X-Remote-User-Name": USER, "X-Remote-User-Display-Name": USER.capitalize(),
               "User-Agent": request.headers.get("User-Agent", "")}
    url = f"http://127.0.0.1:{ADDON}/{path}"
    async with aiohttp.ClientSession() as s:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            ws_out = web.WebSocketResponse()
            await ws_out.prepare(request)
            async with s.ws_connect(url, headers=headers) as ws_in:
                async def up():
                    async for m in ws_out:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await ws_in.send_str(m.data)
                    await ws_in.close()
                import asyncio
                task = asyncio.create_task(up())
                async for m in ws_in:
                    if m.type == aiohttp.WSMsgType.TEXT:
                        await ws_out.send_str(m.data)
                task.cancel()
            return ws_out
        async with s.request(request.method, url, headers=headers, data=await request.read()) as r:
            return web.Response(status=r.status, body=await r.read(), content_type=r.content_type)


app = web.Application()
app.router.add_get("/", page)
app.router.add_get("/local/ha-visio/ha-visio.js", module)
app.router.add_route("*", ENTRY + "/{tail:.*}", ingress)
web.run_app(app, port=PORT, print=None)
