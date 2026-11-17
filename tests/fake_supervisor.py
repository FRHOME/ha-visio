"""Fake Supervisor: records service calls/events/states, answers states as 'on'."""
import json, sys
from aiohttp import web
LOG = sys.argv[2]
async def rec(request):
    body = await request.json() if request.can_read_body else {}
    with open(LOG, "a") as f:
        f.write(json.dumps({"path": request.path, "body": body}) + "\n")
    return web.json_response({})
async def state(request):
    return web.json_response({"state": "on"})
async def info(request):
    return web.json_response({"data": {"slug": "test_visio"}})
async def net(request):
    return web.json_response({"data": {"interfaces": [{"enabled": True, "connected": True, "ipv4": {"address": ["192.168.10.5/24"]}}]}})
app = web.Application()
app.router.add_get("/addons/self/info", info)
app.router.add_get("/network/info", net)
app.router.add_get("/core/api/states/{e}", state)
app.router.add_post("/core/api/{tail:.*}", rec)
web.run_app(app, port=int(sys.argv[1]), print=None)
