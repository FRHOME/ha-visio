"""Integration test: two HA Visio instances (Paris, Toulouse) on localhost."""
import asyncio, json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
import aiohttp

A = "http://127.0.0.1:18099"; B = "http://127.0.0.1:28099"
A_PEER = "http://127.0.0.1:18100"; B_PEER = "http://127.0.0.1:28100"
SUP_LOG_B = "/tmp/visio-sup-b.log"
procs = {}
fails = 0


def check(cond, label):
    global fails
    print(("  OK  " if cond else "  FAIL") + " " + label)
    if not cond:
        fails += 1


def start(name, ing, peer, sup):
    procs[name] = subprocess.Popen([os.path.join(HERE, "run.sh"), name, str(ing), str(peer), str(sup)])


async def api(s, base, method, path, body=None, headers=None):
    async with s.request(method, base + path, json=body, headers=headers or {}) as r:
        text = await r.text()
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text


class Browser:
    def __init__(self, s, base, headers):
        self.s, self.base, self.headers, self.msgs = s, base, headers, asyncio.Queue()

    async def open(self):
        self.ws = await self.s.ws_connect(self.base + "/ws", headers=self.headers)
        self.task = asyncio.create_task(self._pump())
        return await self.expect("welcome")

    async def _pump(self):
        async for m in self.ws:
            await self.msgs.put(json.loads(m.data))

    async def expect(self, kind, timeout=8):
        end = time.time() + timeout
        while True:
            m = await asyncio.wait_for(self.msgs.get(), max(0.1, end - time.time()))
            if m["type"] == kind:
                return m

    async def send(self, **m):
        await self.ws.send_json(m)


async def wait_online(s, base, n=1, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        _, st = await api(s, base, "GET", "/api/state")
        if isinstance(st, dict) and sum(c["online"] for c in st["contacts"]) >= n:
            return st
        await asyncio.sleep(0.5)
    return st


async def main():
    async with aiohttp.ClientSession() as s:
        print("Setup endpoints")
        st, tab = await api(s, B, "POST", "/api/endpoints", {
            "name": "Tablette salon", "kind": "screen", "match_user_agent": "KioskSatellite",
            "kiosk_url": "http://127.0.0.1:2325/", "open_service": "esphome.ks_tab_open_url",
            "close_service": "esphome.ks_tab_close_url", "turn_off": "switch.screensaver"})
        check(st == 200 and tab["kiosk_url"] == "http://127.0.0.1:2325", "B: tablet endpoint saved")
        st, lucas = await api(s, A, "POST", "/api/endpoints", {"name": "Lucas", "kind": "person", "match_users": ["lucas"], "icon": "mdi:account"})
        check(st == 200, "A: person endpoint saved")
        st, _ = await api(s, A, "POST", "/api/endpoints", {"name": "x", "match_user_agent": "(("})
        check(st == 400, "invalid regex rejected")

        print("Pairing")
        st, inv = await api(s, A, "POST", "/api/invite", {"url": A_PEER})
        check(st == 200 and inv["code"].startswith("HAV1."), "A: invitation created")
        st, bad = await api(s, B, "POST", "/api/join", {"code": "HAV1.garbage", "url": B_PEER})
        check(st == 400, "B: damaged code rejected")
        st, joined = await api(s, B, "POST", "/api/join", {"code": inv["code"], "url": B_PEER})
        check(st == 200 and joined["name"] == "paris", f"B: joined A ({joined})")
        st, again = await api(s, B, "POST", "/api/join", {"code": inv["code"], "url": B_PEER})
        check(st == 400, "invitation is single-use")
        sa = await wait_online(s, A); sb = await wait_online(s, B)
        check(sa["contacts"] and sa["contacts"][0]["online"], "A sees B online")
        check(sb["contacts"] and sb["contacts"][0]["online"], "B sees A online")
        b_id, a_id = sa["contacts"][0]["id"], sb["contacts"][0]["id"]
        check([e["name"] for e in sa["contacts"][0]["endpoints"]] == ["Tablette salon"], "A got B's endpoints")
        check([e["name"] for e in sb["contacts"][0]["endpoints"]] == ["Lucas"], "B got A's endpoints")
        check("secret" not in json.dumps(sa), "no secret in API output")

        print("Call A → B tablet, answered")
        open(SUP_LOG_B, "w").close()
        caller = Browser(s, A, {"X-Remote-User-Name": "lucas", "X-Remote-User-Display-Name": "Lucas"})
        tablet = Browser(s, B, {"User-Agent": "Mozilla/5.0 KioskSatellite/1.0"})
        other = Browser(s, B, {"User-Agent": "Firefox", "X-Remote-User-Name": "maman"})
        await caller.open(); w = await tablet.open(); await other.open()
        check(w["endpoints"] == [tab["id"]], "tablet browser matched to its endpoint")
        _, st_tab = await api(s, B, "GET", "/api/state", headers={"User-Agent": "KioskSatellite"})
        check("admin" not in st_tab and st_tab["me"]["settings"] is False, "no settings on the wall screen")
        st, _ = await api(s, B, "POST", "/api/endpoints", {"name": "hack"}, headers={"User-Agent": "KioskSatellite"})
        check(st == 403, "settings API refused to the wall screen")
        await caller.send(type="call", contact=b_id, endpoint=tab["id"])
        c = await caller.expect("calling")
        inc = await tablet.expect("incoming")
        check(inc["from"] == "Lucas" and inc["contact"] == "paris", f"tablet rings: {inc['from']} · {inc['contact']}")
        await caller.expect("ringing")
        await asyncio.sleep(0.5)
        sup = [json.loads(l) for l in open(SUP_LOG_B)]
        paths = [x["path"] for x in sup]
        check("/core/api/services/switch/turn_off" in paths, "screensaver switched off")
        opened = [x for x in sup if x["path"].endswith("ks_tab_open_url")]
        check(opened and opened[0]["body"]["url"] == "http://127.0.0.1:2325/hassio/ingress/test_visio", "kiosk page opened")
        check(any(x["path"] == "/core/api/events/ha_visio_incoming" for x in sup), "event ha_visio_incoming fired")
        await tablet.send(type="accept", call=inc["call"])
        await caller.expect("accepted")
        await caller.send(type="signal", call=c["call"], data={"sdp": {"type": "offer", "sdp": "v=0"}})
        sig = await tablet.expect("signal")
        check(sig["data"]["sdp"]["type"] == "offer", "offer relayed A → B")
        await tablet.send(type="signal", call=inc["call"], data={"sdp": {"type": "answer", "sdp": "v=0"}})
        sig = await caller.expect("signal")
        check(sig["data"]["sdp"]["type"] == "answer", "answer relayed B → A")
        await caller.send(type="hangup", call=c["call"])
        e = await tablet.expect("ended")
        check(e["reason"] == "hangup", "tablet told the call ended")
        await asyncio.sleep(2.5)
        paths = [json.loads(l)["path"] for l in open(SUP_LOG_B)]
        check(any(p.endswith("ks_tab_close_url") for p in paths), "kiosk page closed after the call")
        check("/core/api/services/switch/turn_on" in paths, "screensaver restored")

        print("Call B tablet → A everyone, declined")
        await tablet.send(type="call", contact=a_id, endpoint="*")
        await tablet.expect("calling")
        inc = await caller.expect("incoming")
        check(inc["from"] == "Tablette salon" and inc["contact"] == "toulouse", f"Lucas rings: {inc['from']} · {inc['contact']}")
        await caller.send(type="decline", call=inc["call"])
        e = await tablet.expect("ended")
        check(e["reason"] == "declined", "caller told it was declined")

        print("Missed call")
        await caller.send(type="call", contact=b_id, endpoint="*")
        await caller.expect("calling")
        await tablet.expect("incoming")
        e = await caller.expect("ended", timeout=10)
        check(e["reason"] == "missed", "missed after ring duration")
        e = await tablet.expect("ended")
        check(e["reason"] == "missed", "tablet stops ringing")

        print("Busy + unknown endpoint")
        await caller.send(type="call", contact=b_id, endpoint="nope")
        err = await caller.expect("error")
        check(err["code"] == "unavailable", "unknown remote endpoint refused")
        await caller.send(type="call", contact=b_id, endpoint=tab["id"])
        c = await caller.expect("calling")
        inc = await tablet.expect("incoming")
        await tablet.send(type="accept", call=inc["call"])
        await caller.expect("accepted")
        caller2 = Browser(s, A, {"X-Remote-User-Name": "compagne"})
        await caller2.open()
        await caller2.send(type="call", contact=b_id, endpoint=tab["id"])
        e = await caller2.expect("ended")
        check(e["reason"] == "busy", "second caller gets busy")
        print("Browser drops mid-call")
        await tablet.ws.close()
        e = await caller.expect("ended")
        check(e["reason"] == "disconnected", "caller told the other side dropped")

        print("Auth")
        async with s.ws_connect(A_PEER + "/peer") as ws:
            ch = await ws.receive_json()
            await ws.send_json({"type": "auth", "id": "intruder", "nonce": "x", "mac": "00"})
            m = await ws.receive()
            check(m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED), "unknown peer rejected")
        st, _ = await api(s, A, "POST", "/api/invite", {"url": ""})
        check(st == 400, "invite needs an address")

        print("Restart B → reconnects with stored pairing")
        procs["toulouse"].terminate(); procs["toulouse"].wait()
        await asyncio.sleep(1)
        _, sa = await api(s, A, "GET", "/api/state")
        check(not sa["contacts"][0]["online"], "A sees B offline")
        start("toulouse", 28099, 28100, 29000)
        sa = await wait_online(s, A)
        check(sa["contacts"][0]["online"], "A sees B online again")

        print("Sharing filter")
        _, ep2 = await api(s, B, "POST", "/api/endpoints", {"name": "Cuisine", "kind": "screen"})
        await asyncio.sleep(1)
        _, sa = await api(s, A, "GET", "/api/state")
        check(len(sa["contacts"][0]["endpoints"]) == 2, "new endpoint pushed to A")
        await api(s, B, "PATCH", f"/api/contacts/{a_id}", {"share": [ep2["id"]]})
        await asyncio.sleep(1)
        _, sa = await api(s, A, "GET", "/api/state")
        check([e["name"] for e in sa["contacts"][0]["endpoints"]] == ["Cuisine"], "share filter applied")

        print("Unpair from A")
        st, _ = await api(s, A, "DELETE", f"/api/contacts/{b_id}")
        await asyncio.sleep(1)
        _, sb = await api(s, B, "GET", "/api/state")
        check(st == 200 and sb["contacts"] == [], "B dropped the pairing too")


if __name__ == "__main__":
    subprocess.run("rm -rf /tmp/visio-paris /tmp/visio-toulouse", shell=True)
    sup_a = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_supervisor.py"), "19000", "/tmp/visio-sup-a.log"])
    sup_b = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_supervisor.py"), "29000", SUP_LOG_B])
    start("paris", 18099, 18100, 19000); start("toulouse", 28099, 28100, 29000)
    time.sleep(2)
    try:
        asyncio.run(main())
    finally:
        for p in [*procs.values(), sup_a, sup_b]:
            p.terminate()
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
