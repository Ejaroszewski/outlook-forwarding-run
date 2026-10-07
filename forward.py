#!/usr/bin/env python3
"""
GitHub Actions runner: Outlook forwarding via local Playwright + proxy.
- Launches Chromium directly on the runner (no Scrapeless needed).
- 10 inboxes x 100 accounts. Progress in progress.json.
- Env: PROXY_USER, PROXY_PASS
- Args: --start N --limit N
"""
import asyncio, csv, json, os, sys

DESTS = [f"ranksoldier{i}@gmail.com" for i in range(2, 12)]
RULE_NAME = "AutoForward-All"
PROXY_HOST = "thehub.proxy-cheap.com"
PROXY_PORT = "8080"

def dest_for(idx):
    return DESTS[idx // 100]

def load_accounts():
    accs = []
    with open("accounts.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("email"):
                accs.append(row)
    return accs

def load_progress():
    if os.path.exists("progress.json"):
        return json.load(open("progress.json"))
    return {}

def save_progress(p):
    json.dump(p, open("progress.json", "w"), indent=1)

async def login(page, acc):
    email = acc["email"]
    await page.goto("https://login.live.com/", timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    try:
        await page.locator("input#usernameEntry").click(timeout=15000)
    except Exception:
        pass
    await page.keyboard.type(email, delay=25)
    await page.locator('button[data-testid="primaryButton"]').click()
    await page.wait_for_timeout(7000)
    if "verify" in (await page.title()).lower():
        try:
            await page.get_by_text("Use your password").click(timeout=8000)
            await page.wait_for_timeout(5000)
        except Exception:
            return False
    try:
        pwi = page.locator('input[type="password"]').first
        await pwi.wait_for(timeout=15000)
        await pwi.click()
        await page.keyboard.type(acc["password"], delay=35)
        await page.wait_for_timeout(800)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(9000)
    except Exception:
        return False
    if "privacynotice" in page.url:
        try:
            await page.get_by_role("button", name="OK").first.click(timeout=8000)
            await page.wait_for_timeout(4000)
        except Exception:
            pass
    if "fido" in page.url:
        for txt in ("Skip", "Cancel", "Not now"):
            try:
                await page.get_by_text(txt, exact=False).first.click(timeout=3000)
                await page.wait_for_timeout(2000)
                break
            except Exception:
                continue
    try:
        nb = page.locator("#idBtn_Back")
        if await nb.count() and await nb.first.is_visible(timeout=4000):
            await nb.first.click()
            await page.wait_for_timeout(2500)
    except Exception:
        pass
    t = (await page.title()).lower()
    if "login.live.com" in page.url and ("password" in t or "sign in" in t):
        return False
    return True

async def create_rule(page, dest):
    """Use Settings > Mail > Forwarding (simpler than Rules page)."""
    await page.goto("https://outlook.live.com/mail/0/options/mail/forwarding",
                    timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(8000)
    if "microsoft.com" in page.url:
        print(f"    forwarding page bounced to microsoft.com", flush=True)
        return False
    try:
        # Enable forwarding checkbox
        enable_cb = None
        for selector in [
            lambda: page.get_by_label("Enable forwarding").first,
            lambda: page.get_by_text("Enable forwarding", exact=False).first,
            lambda: page.locator('input[type="checkbox"]').first,
        ]:
            try:
                el = selector()
                await el.wait_for(timeout=8000)
                enable_cb = el
                break
            except Exception:
                continue
        if not enable_cb:
            print(f"    could not find Enable forwarding checkbox", flush=True)
            print(f"    title: {await page.title()}, url: {page.url}", flush=True)
            return False
        # Check if already enabled
        is_checked = await enable_cb.is_checked() if await enable_cb.get_attribute("type") == "checkbox" else False
        if not is_checked:
            await enable_cb.click(timeout=10000)
            await page.wait_for_timeout(2000)
        # Find the "Forward to" textbox
        to_box = None
        for selector in [
            lambda: page.get_by_label("Forward my email to").first,
            lambda: page.get_by_placeholder("Email address").first,
            lambda: page.locator('input[type="text"]').first,
            lambda: page.locator('input[type="email"]').first,
        ]:
            try:
                el = selector()
                await el.wait_for(timeout=8000)
                to_box = el
                break
            except Exception:
                continue
        if not to_box:
            print(f"    could not find forward-to textbox", flush=True)
            return False
        await to_box.fill(dest)
        await page.wait_for_timeout(1500)
        # Keep a copy checkbox
        try:
            keep_cb = page.get_by_label("Keep a copy of forwarded messages").first
            await keep_cb.wait_for(timeout=5000)
            if not await keep_cb.is_checked():
                await keep_cb.click(timeout=5000)
                await page.wait_for_timeout(1000)
        except Exception:
            print(f"    keep-a-copy checkbox not found, continuing", flush=True)
        # Save button
        save_btn = None
        for selector in [
            lambda: page.get_by_role("button", name="Save").first,
            lambda: page.get_by_text("Save", exact=True).first,
        ]:
            try:
                el = selector()
                await el.wait_for(timeout=8000)
                save_btn = el
                break
            except Exception:
                continue
        if not save_btn:
            print(f"    could not find Save button", flush=True)
            return False
        await save_btn.click(timeout=10000)
        await page.wait_for_timeout(5000)
    except Exception as e:
        print(f"    forwarding setup failed: {str(e)[:80]}", flush=True)
        return False
    # Verify by reloading and checking the value
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    content = await page.content()
    return dest in content

async def run_one(idx, acc, proxy_cfg, sem, progress):
    from playwright.async_api import async_playwright
    email = acc["email"]
    dest = dest_for(idx)
    async with sem:
        if progress.get(email) == "done":
            return
        print(f"[{idx}] {email} -> {dest}", flush=True)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    proxy=proxy_cfg,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                page = await ctx.new_page()
                ok = await login(page, acc)
                if not ok:
                    progress[email] = "login_failed"
                    print(f"[{idx}] login FAILED", flush=True)
                else:
                    rok = await create_rule(page, dest)
                    progress[email] = "done" if rok else "rule_failed"
                    print(f"[{idx}] rule {'CREATED' if rok else 'FAILED'}", flush=True)
                await browser.close()
        except Exception as e:
            progress[email] = f"error: {str(e)[:80]}"
            print(f"[{idx}] ERROR {str(e)[:100]}", flush=True)
        save_progress(progress)

async def main():
    puser = os.environ.get("PROXY_USER", "")
    ppass = os.environ.get("PROXY_PASS", "")
    proxy_cfg = None
    if puser:
        proxy_cfg = {
            "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
            "username": puser,
            "password": ppass,
        }
    accounts = load_accounts()
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else len(accounts)
    start = int(args[args.index("--start") + 1]) if "--start" in args else 0
    subset = list(enumerate(accounts[start:start + limit], start=start))
    print(f"total={len(accounts)} running={len(subset)} start={start}", flush=True)
    progress = load_progress()
    sem = asyncio.Semaphore(2)
    async def gated(i, a):
        await asyncio.sleep((i % 2) * 15)
        await run_one(i, a, proxy_cfg, sem, progress)
    await asyncio.gather(*(gated(i, a) for i, a in subset))
    done = sum(1 for v in progress.values() if v == "done")
    print(f"FINISHED: {done} done / {len(progress)} attempted", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
