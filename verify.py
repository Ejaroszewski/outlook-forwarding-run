"""Read-only forwarding verification.

For each sampled account index: log in, open the Forwarding settings,
READ BACK the current state (toggle on/off, address in the box, keep-copy),
and optionally search Inbox/Junk for a test token. Changes NOTHING.
Writes verify.json incrementally.
"""
import argparse, asyncio, json, random
from playwright.async_api import async_playwright
from forward import login, load_accounts

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]
VWS = [{"width": 1366, "height": 768}, {"width": 1440, "height": 900},
       {"width": 1536, "height": 864}, {"width": 1920, "height": 1080}]
TZS = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"]

RESULTS = {}

def save():
    with open("verify.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

async def read_settings(page):
    out = {"enabled": None, "address": None, "keep_copy": None, "wall": False}
    await page.goto("https://outlook.live.com/mail/0/options/mail/forwarding",
                    wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(12000)
    modal = None
    for sel in ["[class*='optionsModal']", "[role='dialog']", ".ms-Modal"]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                modal = loc
                break
        except Exception:
            pass
    if modal is None:
        modal = page.locator("body")
    try:
        text = (await modal.inner_text())[:3000]
    except Exception:
        text = ""
    out["wall"] = "verify your account to forward" in text.lower()
    out["panel_text"] = text[:400]
    try:
        sw = modal.locator('button[role="switch"], [role="switch"]').first
        if await sw.count() > 0:
            out["enabled"] = await sw.get_attribute("aria-checked")
    except Exception as e:
        out["enabled_err"] = str(e)[:120]
    try:
        inp = modal.locator('input[type="text"], input[type="email"]').first
        if await inp.count() > 0:
            out["address"] = await inp.input_value()
    except Exception as e:
        out["address_err"] = str(e)[:120]
    try:
        cbs = await modal.locator('input[type="checkbox"]').all()
        if cbs:
            out["keep_copy"] = await cbs[-1].is_checked()
    except Exception:
        pass
    return out

async def find_token(page, token):
    found = []
    for name, url in [("inbox", "https://outlook.live.com/mail/0/inbox/"),
                      ("junk", "https://outlook.live.com/mail/0/junkemail/")]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(9000)
            body = await page.inner_text("body")
            if token in body:
                found.append(name)
        except Exception as e:
            print(f"    token search error {name}: {str(e)[:100]}", flush=True)
    return found

async def check_one(idx, acc, token):
    res = {"idx": idx, "email": acc["email"]}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                user_agent=random.choice(UAS), viewport=random.choice(VWS),
                locale="en-US", timezone_id=random.choice(TZS),
                color_scheme="light", device_scale_factor=1)
            await ctx.clear_cookies()
            page = await ctx.new_page()
            ok = await login(page, acc)
            res["login"] = bool(ok)
            print(f"[{idx}] {acc['email']} login={'OK' if ok else 'FAILED'}", flush=True)
            if ok:
                try:
                    res["settings"] = await read_settings(page)
                    s = res["settings"]
                    print(f"[{idx}] SETTINGS enabled={s['enabled']} address={s['address']!r} keep_copy={s['keep_copy']} wall={s['wall']}", flush=True)
                except Exception as e:
                    res["settings_error"] = str(e)[:200]
                    print(f"[{idx}] SETTINGS ERROR {res['settings_error']}", flush=True)
                if token:
                    res["token_found_in"] = await find_token(page, token)
                    print(f"[{idx}] TOKEN found in: {res['token_found_in']}", flush=True)
        except Exception as e:
            res["error"] = str(e)[:200]
            print(f"[{idx}] ERROR {res['error']}", flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    RESULTS[acc["email"]] = res
    save()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    ap.add_argument("--token", default="")
    args = ap.parse_args()
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip()]
    for idx in idxs:
        await check_one(idx, accounts[idx], args.token)
    print("VERIFY DONE", json.dumps({e: (r.get("settings") or {}).get("enabled") for e, r in RESULTS.items()}), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
