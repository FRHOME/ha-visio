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
    return web.json_response({"data": {"slug": "test_visio", "ingress_entry": "/api/hassio_ingress/TOKEN"}})
async def net(request):
    return web.json_response({"data": {"interfaces": [{"enabled": True, "connected": True, "ipv4": {"address": ["192.168.10.5/24"]}}]}})
RESOURCES = []
async def core_ws(request):
    ws = web.WebSocketResponse(); await ws.prepare(request)
    await ws.send_json({"type": "auth_required"})
    auth = await ws.receive_json()
    await ws.send_json({"type": "auth_ok" if auth.get("access_token") is not None else "auth_invalid"})
    async for m in ws:
        cmd = json.loads(m.data); res = None
        if cmd["type"] == "lovelace/resources": res = RESOURCES
        elif cmd["type"] == "lovelace/resources/create":
            res = {"id": str(len(RESOURCES) + 1), "type": cmd["res_type"], "url": cmd["url"]}; RESOURCES.append(res)
        elif cmd["type"] == "lovelace/resources/update":
            for r in RESOURCES:
                if r["id"] == cmd["resource_id"]: r["url"] = cmd["url"]; res = r
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": "ws:" + cmd["type"], "body": cmd}) + "\n")
        await ws.send_json({"id": cmd["id"], "type": "result", "success": True, "result": res})
    return ws
app = web.Application()
app.router.add_get("/core/websocket", core_ws)
app.router.add_get("/addons/self/info", info)
app.router.add_get("/network/info", net)
app.router.add_get("/core/api/states/{e}", state)
app.router.add_post("/core/api/{tail:.*}", rec)
web.run_app(app, port=int(sys.argv[1]), print=None)
