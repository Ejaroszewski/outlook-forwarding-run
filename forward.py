#!/usr/bin/env python3
"""
GitHub Actions runner: Outlook forwarding via Scrapeless cloud browser.
- Uses Scrapeless SDK to create sessions (works from GitHub network).
- 10 inboxes x 100 accounts. Progress in progress.json.
- Env: SCRAPELESS_KEY, PROXY_USER, PROXY_PASS
- Args: --start N --limit N
"""
import asyncio, csv, json, os, sys, urllib.parse

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
    # FIDO skip attempts
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
    await page.goto("https://outlook.live.com/mail/0/options/mail/rules",
                    timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(8000)
    if "microsoft.com" in page.url:
        return False
    if RULE_NAME in await page.content():
        return True
    try:
        await page.get_by_role("button", name="Add new rule").first.click(timeout=30000)
        await page.wait_for_timeout(2000)
        dialog = page.get_by_role("dialog").first
        name_box = dialog.get_by_label("Rule name")
        if not await name_box.count():
            name_box = dialog.locator('input[type="text"]').first
        await name_box.first.fill(RULE_NAME)
        await dialog.get_by_role("combobox").first.click(timeout=10000)
        await page.wait_for_timeout(1000)
        await page.get_by_role("option", name="Apply to all messages").first.click(timeout=10000)
        await page.wait_for_timeout(1000)
        await dialog.get_by_role("combobox").nth(1).click(timeout=10000)
        await page.wait_for_timeout(1000)
        await page.get_by_role("option", name="Forward to").first.click(timeout=10000)
        await page.wait_for_timeout(1500)
        to_box = dialog.locator('input[type="text"]').last
        await to_box.fill(dest)
        await page.wait_for_timeout(1500)
        await to_box.press("Enter")
        await page.wait_for_timeout(1000)
        await dialog.get_by_role("button", name="Save").first.click(timeout=10000)
        await page.wait_for_timeout(5000)
    except Exception as e:
        print(f"    rule failed: {str(e)[:80]}", flush=True)
        return False
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    return RULE_NAME in await page.content()

async def run_one(idx, acc, key, proxy_url, sem, progress):
    from playwright.async_api import async_playwright
    from scrapeless import Scrapeless
    from scrapeless.types import ICreateBrowser
    email = acc["email"]
    dest = dest_for(idx)
    async with sem:
        if progress.get(email) == "done":
            return
        print(f"[{idx}] {email} -> {dest}", flush=True)
        try:
            client = Scrapeless({"api_key": key})
            cfg = ICreateBrowser(
                session_name=f"acct_{idx}",
                session_ttl=600,
                proxy_country="US",
            )
            # add custom proxy if provided
            session = client.browser.create(cfg).__dict__
            ws_url = session.get("browser_ws_endpoint", "")
            if proxy_url:
                # append proxy_url param
                sep = "&" if "?" in ws_url else "?"
                ws_url = ws_url + sep + "proxy_url=" + urllib.parse.quote(proxy_url, safe="")
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(ws_url, timeout=90000)
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
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
    key = os.environ.get("SCRAPELESS_KEY", "")
    puser = os.environ.get("PROXY_USER", "")
    ppass = os.environ.get("PROXY_PASS", "")
    proxy_url = f"http://{puser}:{ppass}@{PROXY_HOST}:{PROXY_PORT}" if puser else ""
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
        await run_one(i, a, key, proxy_url, sem, progress)
    await asyncio.gather(*(gated(i, a) for i, a in subset))
    done = sum(1 for v in progress.values() if v == "done")
    print(f"FINISHED: {done} done / {len(progress)} attempted", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
