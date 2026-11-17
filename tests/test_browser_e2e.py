"""End-to-end: two real Chrome pages make a WebRTC call through two HA Visio instances."""
import asyncio, json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
import aiohttp
from playwright.async_api import async_playwright

A = "http://127.0.0.1:18099"; B = "http://127.0.0.1:28099"
SHOTS = "/tmp/visio-shots"
procs = []


def launch():
    subprocess.run(f"rm -rf /tmp/visio-paris /tmp/visio-toulouse; mkdir -p {SHOTS}", shell=True)
    for port, log in ((19000, "/tmp/visio-sup-a.log"), (29000, "/tmp/visio-sup-b.log")):
        procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "fake_supervisor.py"), str(port), log]))
    for name, ing, peer, sup in (("paris", 18099, 18100, 19000), ("toulouse", 28099, 28100, 29000)):
        procs.append(subprocess.Popen([os.path.join(HERE, "run.sh"), name, str(ing), str(peer), str(sup)]))
    time.sleep(2)


async def setup():
    async with aiohttp.ClientSession() as s:
        async def post(base, path, body):
            async with s.post(base + path, json=body) as r:
                assert r.status == 200, await r.text()
                return await r.json()
        # longer ring for the browser test
        await post(B, "/api/endpoints", {"name": "Tablette salon", "kind": "screen", "match_user_agent": "KioskSatellite",
                                         "kiosk_url": "http://127.0.0.1:2325", "open_service": "esphome.ks_tab_open_url"})
        await post(B, "/api/endpoints", {"name": "Papa", "kind": "person", "match_users": ["papa"], "ring_all": False})
        await post(A, "/api/endpoints", {"name": "Lucas", "kind": "person", "match_users": ["lucas"]})
        inv = await post(A, "/api/invite", {"url": "http://127.0.0.1:18100"})
        await post(B, "/api/join", {"code": inv["code"], "url": "http://127.0.0.1:28100"})


async def main():
    await setup()
    await asyncio.sleep(2)
    async with async_playwright() as p:
        browser = await p.chromium.launch(**({"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}), args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        ca = await browser.new_context(viewport={"width": 1000, "height": 700}, locale="fr-FR",
                                       extra_http_headers={"X-Remote-User-Name": "lucas", "X-Remote-User-Display-Name": "Lucas"})
        cb = await browser.new_context(viewport={"width": 1000, "height": 700}, locale="fr-FR",
                                       user_agent="Mozilla/5.0 (Linux; Android 13) KioskSatellite/1.4 Chrome/120")
        pa, pb = await ca.new_page(), await cb.new_page()
        errors = []
        for pg in (pa, pb):
            pg.on("pageerror", lambda e: errors.append(str(e)))
        await pa.goto(A + "/"); await pb.goto(B + "/")
        await pa.wait_for_selector(".target:not([disabled])")
        await pa.screenshot(path=f"{SHOTS}/1_home_paris.png")
        await pb.screenshot(path=f"{SHOTS}/1_home_tablet.png")
        labels = await pa.locator(".target").all_inner_texts()
        print("Paris sees:", labels)
        assert "settings hidden on tablet", await pb.locator("#btnSettings").is_hidden()

        await pa.locator(".target", has_text="Tablette salon").click()
        await pb.wait_for_selector("#in.on", timeout=8000)
        print("Tablet:", await pb.locator("#inTitle").inner_text(), "/", await pb.locator("#inSub").inner_text())
        await pa.screenshot(path=f"{SHOTS}/2_outgoing.png")
        await pb.screenshot(path=f"{SHOTS}/2_incoming.png")
        await pb.click("#btnAccept")
        for pg in (pa, pb):
            await pg.wait_for_function("document.getElementById('remote').videoWidth > 0", timeout=15000)
        dims = [await pg.evaluate("[document.getElementById('remote').videoWidth, document.getElementById('remote').videoHeight]") for pg in (pa, pb)]
        print("Remote video received (A, B):", dims)
        await asyncio.sleep(2)
        await pa.screenshot(path=f"{SHOTS}/3_in_call.png")
        await pa.click("#btnHang")
        await pb.wait_for_selector("#end.on", timeout=8000)
        print("Tablet end screen:", await pb.locator("#endTitle").inner_text())

        # reverse: tablet calls Paris "everyone"
        await pb.wait_for_selector("#home.on", timeout=8000)
        await pb.locator(".target", has_text="Lucas").click()
        await pa.wait_for_selector("#in.on", timeout=8000)
        print("Paris:", await pa.locator("#inTitle").inner_text(), "/", await pa.locator("#inSub").inner_text())
        await pa.click("#btnDecline")
        await pb.wait_for_selector("#end.on", timeout=8000)
        print("Tablet end screen:", await pb.locator("#endTitle").inner_text())

        # settings page
        await pa.wait_for_selector("#home.on", timeout=8000)
        await pa.click("#btnSettings")
        await pa.wait_for_selector("#settings.on")
        await pa.locator("#epList .act").first.click()
        await pa.screenshot(path=f"{SHOTS}/4_settings.png", full_page=True)
        await pa.click("#btnInvite")
        await pa.locator("#pairBox .act", has_text="Créer le code").click()
        await pa.wait_for_selector("#pairBox .code")
        await pa.locator("#pairBox").scroll_into_view_if_needed()
        await pa.screenshot(path=f"{SHOTS}/5_invite.png")
        print("JS errors:", errors or "none")
        await browser.close()
        return not errors


if __name__ == "__main__":
    launch()
    try:
        ok = asyncio.run(main())
    finally:
        for p in procs:
            p.terminate()
    print("E2E", "PASSED" if ok else "FAILED")
