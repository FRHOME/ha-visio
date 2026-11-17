"""End-to-end: the dashboard module (ha-visio.js) in two fake HA frontends, real WebRTC in Chrome."""
import asyncio, os, subprocess, sys, time
import aiohttp
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
A = "http://127.0.0.1:18099"; B = "http://127.0.0.1:28099"
FA = "http://127.0.0.1:18300/"; FB = "http://127.0.0.1:28300/"
SHOTS = "/tmp/visio-shots"
procs = []


def launch():
    subprocess.run(f"rm -rf /tmp/visio-paris /tmp/visio-toulouse; mkdir -p {SHOTS}", shell=True)
    for port, log in ((19000, "/tmp/visio-sup-a.log"), (29000, "/tmp/visio-sup-b.log")):
        procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "fake_supervisor.py"), str(port), log]))
    for name, ing, peer, sup in (("paris", 18099, 18100, 19000), ("toulouse", 28099, 28100, 29000)):
        procs.append(subprocess.Popen([os.path.join(HERE, "run.sh"), name, str(ing), str(peer), str(sup)]))
    procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "fake_frontend.py"), "18300", "18099", "lucas"]))
    procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "fake_frontend.py"), "28300", "28099", "tab"]))
    time.sleep(2.5)


async def setup():
    async with aiohttp.ClientSession() as s:
        async def post(base, path, body):
            async with s.post(base + path, json=body) as r:
                assert r.status == 200, await r.text()
                return await r.json()
        await post(B, "/api/endpoints", {"name": "Tablette", "kind": "screen", "match_user_agent": "KioskSatellite", "turn_off": ["switch.screensaver"]})
        await post(B, "/api/endpoints", {"name": "Papa", "kind": "person", "match_users": ["papa"]})
        await post(A, "/api/endpoints", {"name": "Lucas", "kind": "person", "match_users": ["lucas"]})
        inv = await post(A, "/api/invite", {"url": "http://127.0.0.1:18100"})
        await post(B, "/api/join", {"code": inv["code"], "url": "http://127.0.0.1:28100"})


async def main():
    await setup()
    await asyncio.sleep(2)
    errors = []
    async with async_playwright() as p:
        kw = {"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}
        browser = await p.chromium.launch(**kw, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        ca = await browser.new_context(viewport={"width": 1100, "height": 720}, locale="fr-FR")
        cb = await browser.new_context(viewport={"width": 1100, "height": 720}, locale="fr-FR", user_agent="Mozilla/5.0 (Linux; Android 13) KioskSatellite/1.4 Chrome/120")
        pa, pb = await ca.new_page(), await cb.new_page()
        for pg in (pa, pb):
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("requestfailed", lambda r: errors.append("failed " + r.url))
            pg.on("response", lambda r: r.status >= 400 and not r.url.endswith("favicon.ico") and errors.append(f"{r.status} {r.url}"))
        await pa.goto(FA); await pb.goto(FB)
        await pa.locator("ha-visio-card button[data-e]:not([disabled])").first.wait_for(timeout=15000)
        await pb.locator("ha-visio-card button[data-e]:not([disabled])").first.wait_for(timeout=15000)
        await pa.screenshot(path=f"{SHOTS}/c1_card_paris.png")
        print("Paris card buttons:", await pa.locator("ha-visio-card button").all_inner_texts())

        print("Call Paris → Toulouse tablet")
        await pa.locator("ha-visio-card button", has_text="Tablette").click()
        await pb.locator("ha-visio-overlay #answer").wait_for(timeout=8000)
        await pa.locator("ha-visio-overlay .banner").wait_for()
        await asyncio.sleep(0.6)
        await pa.screenshot(path=f"{SHOTS}/c2_calling.png")
        await pb.screenshot(path=f"{SHOTS}/c2_incoming_banner.png")
        print("Banner:", (await pb.locator("ha-visio-overlay .who").inner_text()).replace("\n", " / "))
        box = await pb.locator("ha-visio-overlay .banner").bounding_box()
        print("Banner size vs screen: %dx%d of 1100x720" % (box["width"], box["height"]))
        # the dashboard stays usable while ringing
        await pb.locator(".tile", has_text="Météo").click(timeout=2000)
        print("Dashboard still clickable while ringing: yes")
        await pb.locator("ha-visio-overlay #answer").click()
        for pg in (pa, pb):
            await pg.wait_for_function("(() => { const v = document.querySelector('ha-visio-overlay').shadowRoot.querySelector('video.remote'); return v && v.videoWidth > 0; })()", timeout=15000)
        print("Remote video flowing on both sides")
        await asyncio.sleep(1.5)
        await pb.screenshot(path=f"{SHOTS}/c3_call_window.png")
        await pb.locator("ha-visio-overlay #shrink").click()
        await asyncio.sleep(0.8)
        await pb.screenshot(path=f"{SHOTS}/c4_call_mini.png")
        await pb.locator("ha-visio-overlay #mic").count()
        await pa.locator("ha-visio-overlay #hangup").click()
        await pb.locator("ha-visio-overlay .note").wait_for(timeout=8000)
        print("Toulouse after hangup:", await pb.locator("ha-visio-overlay .note").first.inner_text())

        print("Call Toulouse → Paris (Lucas), declined")
        await asyncio.sleep(3.2)
        await pb.locator("ha-visio-card button", has_text="Lucas").click()
        await pa.locator("ha-visio-overlay #decline").wait_for(timeout=8000)
        print("Paris banner:", (await pa.locator("ha-visio-overlay .who").inner_text()).replace("\n", " / "))
        await pa.locator("ha-visio-overlay #decline").click()
        await pb.locator("ha-visio-overlay .note", has_text="refusé").wait_for(timeout=8000)
        print("Toulouse sees: Appel refusé")
        await browser.close()
    print("JS errors:", errors or "none")
    return not errors


if __name__ == "__main__":
    launch()
    try:
        ok = asyncio.run(main())
    finally:
        for pr in procs:
            pr.terminate()
    print("CARD E2E", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)
