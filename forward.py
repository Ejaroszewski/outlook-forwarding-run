#!/usr/bin/env python3
"""
GitHub Actions runner: Outlook forwarding via local Playwright + proxy.
- Launches Chromium directly on the runner (no Scrapeless needed).
- 10 inboxes x 100 accounts. Progress in progress.json.
- Env: PROXY_USER, PROXY_PASS
- Args: --start N --limit N
"""
import asyncio, csv, json, os, sys, re, time
import urllib.request, urllib.parse

DESTS = [f"ranksoldier{i}@gmail.com" for i in range(2, 12)]
RULE_NAME = "AutoForward-All"
PROXY_HOST = "thehub.proxy-cheap.com"
PROXY_PORT = "8080"

def get_gmail_access_token(refresh_token, client_id):
    """Exchange refresh_token for Gmail API access token."""
    try:
        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': client_id,
        }).encode()
        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result.get('access_token')
    except Exception as e:
        print(f"    gmail token failed: {str(e)[:60]}", flush=True)
        return None

def get_verification_code_from_gmail(refresh_token, client_id, max_wait=120):
    """Poll recovery Gmail for Microsoft verification code."""
    token = get_gmail_access_token(refresh_token, client_id)
    if not token:
        return None
    headers = {'Authorization': f'Bearer {token}'}
    # Search for Microsoft verification emails
    start = time.time()
    while time.time() - start < max_wait:
        try:
            # List recent messages
            url = 'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=from:microsoft+newer_than:10m&maxResults=5'
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            messages = data.get('messages', [])
            for msg in messages:
                msg_id = msg['id']
                # Get message details
                url2 = f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full'
                req2 = urllib.request.Request(url2, headers=headers)
                with urllib.request.urlopen(req2, timeout=30) as resp2:
                    msg_data = json.loads(resp2.read().decode())
                # Extract body and find code
                body = json.dumps(msg_data)
                # Look for 6-8 digit verification code
                codes = re.findall(r'\b(\d{6,8})\b', body)
                # Filter for codes near "verification" or "security code"
                if 'verif' in body.lower() or 'security code' in body.lower():
                    for code in codes:
                        # Skip years, common numbers
                        if not code.startswith('20') and len(code) >= 6:
                            print(f"    found verification code in Gmail", flush=True)
                            return code
            time.sleep(10)
        except Exception as e:
            print(f"    gmail poll failed: {str(e)[:60]}", flush=True)
            time.sleep(10)
    print(f"    no verification code found in Gmail", flush=True)
    return None

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

async def create_rule(page, dest, acc):
    """Click Settings gear -> use the optionsModal directly."""
    await page.goto("https://outlook.live.com/mail/0/",
                    timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(10000)
    try:
        settings_btn = page.get_by_role("button", name="Settings").first
        await settings_btn.wait_for(timeout=10000)
        await settings_btn.click(timeout=8000)
        await page.wait_for_timeout(5000)
        print(f"    clicked Settings gear", flush=True)
        # Find the optionsModal
        modal = page.locator('.optionsModal').first
        try:
            await modal.wait_for(timeout=10000)
            print(f"    found optionsModal", flush=True)
        except Exception:
            print(f"    no optionsModal found", flush=True)
            return False
        # Use search box in modal (more reliable than nav clicking)
        # The modal has a search box at the top
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(2000)
            search = None
            for sel in [
                lambda: modal.locator('input[type="search"]').first,
                lambda: modal.locator('input[placeholder*="Search" i]').first,
            ]:
                try:
                    el = sel()
                    await el.wait_for(state="visible", timeout=10000)
                    search = el
                    print(f"    found search box", flush=True)
                    break
                except Exception:
                    continue
            if not search:
                print(f"    no search box found", flush=True)
                return False
            await search.fill("forwarding")
            await page.wait_for_timeout(3000)
            print(f"    searched forwarding", flush=True)
            # Click the Forwarding result
            result = None
            for txt in ["Forwarding and IMAP", "Forwarding"]:
                try:
                    el = modal.get_by_text(txt, exact=False).first
                    await el.wait_for(state="visible", timeout=8000)
                    result = el
                    print(f"    found result: {txt}", flush=True)
                    break
                except Exception:
                    continue
            if not result:
                print(f"    no Forwarding in search results", flush=True)
                return False
            try:
                await result.click(timeout=8000)
            except Exception:
                await result.evaluate("el => el.click()")
            await page.wait_for_timeout(5000)
            print(f"    clicked Forwarding result", flush=True)
        except Exception as e:
            print(f"    search nav failed: {str(e)[:80]}", flush=True)
            return False
        # Check for verification blocker - attempt to verify via recovery email
        try:
            modal_text = await modal.inner_text(timeout=5000)
            if "verify your account" in modal_text.lower() or "sign in and verify" in modal_text.lower():
                print(f"    verification required, attempting via recovery email", flush=True)
                # Look for "send code" or similar button
                send_btn = None
                for txt in ["Send code", "Send verification", "Verify", "Continue"]:
                    try:
                        el = modal.get_by_text(txt, exact=False).first
                        await el.wait_for(state="visible", timeout=5000)
                        send_btn = el
                        print(f"    found: {txt}", flush=True)
                        break
                    except Exception:
                        continue
                if send_btn:
                    try:
                        await send_btn.click(timeout=8000)
                    except Exception:
                        await send_btn.evaluate("el => el.click()")
                    await page.wait_for_timeout(3000)
                    print(f"    code send requested", flush=True)
                    # Poll Gmail for the verification code
                    refresh_token = acc.get('refresh_token', '')
                    client_id = acc.get('client_id', '')
                    if refresh_token and client_id:
                        code = get_verification_code_from_gmail(refresh_token, client_id)
                        if code:
                            # Find code input and enter it
                            code_input = None
                            for sel in [
                                lambda: modal.locator('input[type="text"]').first,
                                lambda: modal.locator('input[inputmode="numeric"]').first,
                            ]:
                                try:
                                    el = sel()
                                    await el.wait_for(state="visible", timeout=8000)
                                    code_input = el
                                    break
                                except Exception:
                                    continue
                            if code_input:
                                await code_input.fill(code)
                                await page.wait_for_timeout(1000)
                                # Click verify/submit
                                for txt in ["Verify", "Submit", "Confirm"]:
                                    try:
                                        btn = modal.get_by_text(txt, exact=False).first
                                        await btn.wait_for(timeout=5000)
                                        await btn.click(timeout=8000)
                                        await page.wait_for_timeout(5000)
                                        print(f"    verification code submitted", flush=True)
                                        break
                                    except Exception:
                                        continue
                            else:
                                print(f"    no code input found", flush=True)
                                return False
                        else:
                            print(f"    could not retrieve verification code", flush=True)
                            return False
                    else:
                        print(f"    no refresh_token/client_id for Gmail", flush=True)
                        return False
                else:
                    print(f"    BLOCKED: no send-code button found", flush=True)
                    return False
        except Exception as e:
            print(f"    verification handling failed: {str(e)[:60]}", flush=True)
            pass
        # Now should be on Forwarding page - find enable toggle (could be switch, not checkbox)
        # Dismiss any overlay first
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        enable_ctrl = None
        for sel in [
            lambda: modal.locator('[role="switch"]').first,
            lambda: modal.locator('button[role="switch"]').first,
            lambda: modal.locator('input[type="checkbox"]').first,
            lambda: modal.get_by_label("Enable forwarding", exact=False).first,
        ]:
            try:
                el = sel()
                await el.wait_for(timeout=8000)
                enable_ctrl = el
                print(f"    found enable control", flush=True)
                break
            except Exception:
                continue
        if not enable_ctrl:
            print(f"    no enable control in modal, dumping modal text", flush=True)
            try:
                txt = await modal.inner_text()
                print(f"    modal: {txt[:600]}", flush=True)
            except Exception:
                pass
            return False
        # Check state and enable if needed
        try:
            # For switch: check aria-checked
            checked = await enable_ctrl.get_attribute("aria-checked")
            if checked == "true":
                print(f"    already enabled", flush=True)
            else:
                # Try is_checked for checkbox
                try:
                    if await enable_ctrl.is_checked():
                        print(f"    already enabled (checkbox)", flush=True)
                    else:
                        await enable_ctrl.click(timeout=8000)
                        await page.wait_for_timeout(2000)
                        print(f"    enabled", flush=True)
                except Exception:
                    await enable_ctrl.click(timeout=8000)
                    await page.wait_for_timeout(2000)
                    print(f"    clicked enable", flush=True)
        except Exception as e:
            print(f"    enable failed: {e}", flush=True)
            return False
        # Fill address
        to_box = modal.locator('input[type="text"], input[type="email"]').first
        await to_box.wait_for(timeout=8000)
        await to_box.fill(dest)
        await page.wait_for_timeout(1500)
        print(f"    filled {dest}", flush=True)
        # Keep a copy
        try:
            checkboxes = await modal.locator('input[type="checkbox"]').all()
            if len(checkboxes) > 1:
                keep_cb = checkboxes[1]
                if not await keep_cb.is_checked():
                    await keep_cb.click(timeout=5000)
                    print(f"    keep-copy checked", flush=True)
        except Exception:
            pass
        # Save
        save_btn = modal.get_by_role("button", name="Save").first
        await save_btn.wait_for(timeout=8000)
        await save_btn.click(timeout=10000)
        await page.wait_for_timeout(5000)
        print(f"    SAVED forwarding to {dest}", flush=True)
        return True
    except Exception as e:
        print(f"    failed: {str(e)[:100]}", flush=True)
        return False

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
                    rok = await create_rule(page, dest, acc)
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
