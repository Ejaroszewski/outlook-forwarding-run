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
    """Try OWA classic options page for forwarding (more reliable URLs)."""
    # Try the classic OWA options page
    for url in [
        "https://outlook.live.com/owa/options.aspx",
        "https://outlook.live.com/mail/0/options/mail/forwarding",
    ]:
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(8000)
            title = await page.title()
            print(f"    tried {url.split('/')[-1]}: title={title[:50]}", flush=True)
            if "microsoft.com" in page.url and "options" not in page.url:
                continue
            # Look for forwarding controls
            try:
                # Classic OWA has "Forwarding" section
                fwd_text = await page.get_by_text("Forwarding", exact=False).first.count()
                if fwd_text > 0:
                    print(f"    found Forwarding text on page", flush=True)
                    break
            except Exception:
                pass
        except Exception as e:
            print(f"    {url} failed: {str(e)[:50]}", flush=True)
            continue
    
    # If we're on a page with forwarding options, try to set it
    try:
        # Look for enable forwarding radio/checkbox
        enable = None
        for sel in [
            lambda: page.get_by_label("Enable forwarding", exact=False).first,
            lambda: page.locator('input[type="radio"]').first,
            lambda: page.locator('input[type="checkbox"]').first,
        ]:
            try:
                el = sel()
                await el.wait_for(timeout=5000)
                enable = el
                break
            except Exception:
                continue
        if not enable:
            print(f"    no forwarding controls found, trying Settings UI", flush=True)
            return await create_rule_via_settings(page, dest)
        
        # Classic OWA flow: select "Forward all emails" radio, fill address, save
        await enable.click(timeout=8000)
        await page.wait_for_timeout(1000)
        # Find address textbox
        addr_box = page.locator('input[type="text"]').first
        await addr_box.wait_for(timeout=8000)
        await addr_box.fill(dest)
        await page.wait_for_timeout(1000)
        # Save
        save_btn = page.get_by_role("button", name="Save").first
        await save_btn.click(timeout=10000)
        await page.wait_for_timeout(5000)
        print(f"    forwarding set via OWA classic", flush=True)
        return True
    except Exception as e:
        print(f"    OWA classic failed: {str(e)[:80]}", flush=True)
        return await create_rule_via_settings(page, dest)

async def create_rule_via_settings(page, dest):
    """Fallback: navigate via Settings gear UI."""
    await page.goto("https://outlook.live.com/mail/0/",
                    timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(8000)
    try:
        # Debug: dump top bar HTML to find Settings gear
        try:
            # Try multiple top bar selectors
            for sel in ['header', '[role="banner"]', '#topbar', '.topbar']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        html = await el.inner_html()
                        print(f"    topbar ({sel}) HTML: {html[:800]}", flush=True)
                        break
                except Exception:
                    continue
            # Also dump all buttons with their labels
            all_btns = await page.locator('button').all()
            print(f"    total buttons on page: {len(all_btns)}", flush=True)
            for i, b in enumerate(all_btns[:20]):
                try:
                    lbl = await b.get_attribute("aria-label") or await b.get_attribute("title") or await b.inner_text() or "?"
                    lbl = lbl.strip()[:40]
                    if lbl and lbl != "?":
                        print(f"    btn {i}: {lbl}", flush=True)
                except Exception:
                    pass
        except Exception as e:
            print(f"    button debug failed: {e}", flush=True)
        settings_btn = None
        for selector in [
            lambda: page.get_by_role("button", name="Settings"),
            lambda: page.get_by_role("button", name="Cài đặt"),
        ]:
            try:
                btns = selector()
                cnt = await btns.count()
                for i in range(cnt):
                    b = btns.nth(i)
                    try:
                        lbl = await b.get_attribute("aria-label") or ""
                        # Skip account/profile buttons
                        if "@" in lbl or "account" in lbl.lower() or "tài khoản" in lbl.lower():
                            print(f"    skip account btn: {lbl[:40]}", flush=True)
                            continue
                        await b.wait_for(timeout=3000)
                        settings_btn = b
                        print(f"    using Settings: {lbl[:40]}", flush=True)
                        break
                    except Exception:
                        continue
                if settings_btn:
                    break
            except Exception:
                continue
        if not settings_btn:
            print(f"    could not find Settings button", flush=True)
            return False
        await settings_btn.click(timeout=10000)
        await page.wait_for_timeout(3000)
        # Dump what's in the settings panel for debugging
        try:
            panel_text = await page.locator('[role="dialog"], [role="complementary"], aside').first.inner_text(timeout=5000)
            print(f"    settings panel text: {panel_text[:300]}", flush=True)
        except Exception:
            pass
        # Try View all Outlook settings (English + Vietnamese)
        for txt in ["View all Outlook settings", "Xem tất cả", "Cài đặt Outlook"]:
            try:
                view_all = page.get_by_text(txt, exact=False).first
                await view_all.wait_for(timeout=3000)
                await view_all.click(timeout=8000)
                await page.wait_for_timeout(4000)
                print(f"    clicked: {txt}", flush=True)
                break
            except Exception:
                continue
        # Try search (English + Vietnamese)
        try:
            for placeholder in ["Search Outlook settings", "Search settings", "Search", "Tìm kiếm", "Tìm"]:
                try:
                    search_box = page.get_by_placeholder(placeholder).first
                    await search_box.wait_for(timeout=3000)
                    await search_box.fill("forwarding")
                    await page.wait_for_timeout(3000)
                    break
                except Exception:
                    continue
            # Click Forwarding result (English + Vietnamese)
            fwd_clicked = False
            for txt in ["Forwarding", "Chuyển tiếp"]:
                try:
                    fwd_result = page.get_by_text(txt, exact=False).first
                    await fwd_result.click(timeout=5000)
                    await page.wait_for_timeout(4000)
                    print(f"    clicked: {txt}", flush=True)
                    fwd_clicked = True
                    break
                except Exception:
                    continue
            if not fwd_clicked:
                print(f"    settings search failed", flush=True)
                return False
        except Exception as e:
            print(f"    settings search failed", flush=True)
            return False
        # Enable forwarding
        enable_cb = page.locator('input[type="checkbox"]').first
        await enable_cb.wait_for(timeout=10000)
        if not await enable_cb.is_checked():
            await enable_cb.click(timeout=10000)
            await page.wait_for_timeout(2000)
        to_box = page.locator('input[type="text"]').first
        await to_box.wait_for(timeout=8000)
        await to_box.fill(dest)
        await page.wait_for_timeout(1500)
        save_btn = None
        for name in ["Save", "Lưu"]:
            try:
                btn = page.get_by_role("button", name=name).first
                await btn.wait_for(timeout=5000)
                save_btn = btn
                break
            except Exception:
                continue
        if not save_btn:
            print(f"    no Save button found", flush=True)
            return False
        await save_btn.click(timeout=10000)
        await page.wait_for_timeout(5000)
        print(f"    forwarding saved", flush=True)
        return True
    except Exception as e:
        print(f"    settings UI failed: {str(e)[:80]}", flush=True)
        return False

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
    # Try without proxy first (proxy-cheap gateway is unreliable)
    # Set USE_PROXY=1 to enable proxy
    use_proxy = os.environ.get("USE_PROXY", "0") == "1"
    puser = os.environ.get("PROXY_USER", "")
    ppass = os.environ.get("PROXY_PASS", "")
    proxy_cfg = None
    if use_proxy and puser:
        proxy_cfg = {
            "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
            "username": puser,
            "password": ppass,
        }
        print(f"Using proxy: {PROXY_HOST}", flush=True)
    else:
        print(f"Running WITHOUT proxy (direct connection)", flush=True)
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
