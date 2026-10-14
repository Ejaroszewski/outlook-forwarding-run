"""Probe: what does the 'Sign in and verify your account to create a
forwarding rule' gate actually require? One account. Fills the rule
editor like rulefix2, clicks the Sign in link, then observes the flow
step by step (screenshots + text). Enters the password if asked.
If a code-entry step appears, it only RECORDS it (channel, masked
destination, inputs) and stops. If the flow returns to the editor with
Save enabled, it saves and verifies by reload.
Writes probe.json + probe-*.png.
"""
import argparse, asyncio, json, re
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import UAS, VWS, TZS

RULE_NAME = "FWD-ALL"
RULES_URL = "https://outlook.live.com/mail/0/options/mail/rules"
LOG = []

def rec(tag, **kw):
    e = {"tag": tag}
    e.update(kw)
    LOG.append(e)
    print(json.dumps(e)[:400], flush=True)
    with open("probe.json", "w") as f:
        json.dump(LOG, f, indent=1)

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

_shots = [0]
async def shot(page, tag):
    _shots[0] += 1
    try:
        await page.screenshot(path=f"probe-{_shots[0]:02d}-{tag}.png")
    except Exception:
        pass

async def open_rules(page):
    await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=60000)
    for _ in range(25):
        if "Add rule" in await body_text(page):
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(2500)

async def dismiss_dialog(page):
    try:
        t = (await body_text(page)).lower()
        if "recovery method" not in t:
            return False
        for fn in (
            lambda: page.get_by_role("button", name="Not now").first.click(timeout=5000),
            lambda: page.get_by_text("Not now").first.click(timeout=5000),
        ):
            try:
                await fn()
                await page.wait_for_timeout(1800)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False

async def open_editor(page):
    for fn in (
        lambda: page.get_by_role("button", name="Add rule").first.click(timeout=6000),
        lambda: page.get_by_text("Add rule").first.click(timeout=6000),
    ):
        try:
            await fn()
            break
        except Exception:
            continue
    for _ in range(15):
        t = await body_text(page)
        if "Select a condition" in t and "Select an action" in t:
            return True
        await page.wait_for_timeout(2000)
    return False

async def pick_combo(page, combo_aria, option_part):
    for attempt in range(3):
        await dismiss_dialog(page)
        try:
            combo = page.locator(f'[role="combobox"][aria-label*="{combo_aria}" i]').first
            await combo.wait_for(state="visible", timeout=8000)
            await combo.click(timeout=6000)
        except Exception:
            await page.wait_for_timeout(1500)
            continue
        await page.wait_for_timeout(1800)
        picked = False
        for fn in (
            lambda: page.get_by_role("option", name=re.compile(re.escape(option_part), re.I)).first.click(timeout=5000),
            lambda: page.get_by_text(option_part, exact=False).first.click(timeout=4000),
        ):
            try:
                await fn()
                picked = True
                break
            except Exception:
                continue
        if not picked:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1200)
            continue
        await page.wait_for_timeout(1200)
        try:
            shown = await combo.inner_text()
        except Exception:
            shown = ""
        if option_part.lower() in shown.lower():
            await dismiss_dialog(page)
            return True
        await page.wait_for_timeout(1000)
    return False

async def fill_editor(page, dest):
    if not await open_editor(page):
        rec("editor_failed")
        return False
    await dismiss_dialog(page)
    try:
        name_box = page.locator('input[placeholder="Name your rule"]').first
        await name_box.wait_for(state="visible", timeout=8000)
        await name_box.click(timeout=4000)
        await name_box.fill(RULE_NAME)
    except Exception as e:
        rec("name_error", err=str(e)[:100])
    if not await pick_combo(page, "conditional", "Apply to all messages"):
        rec("cond_failed")
        return False
    await page.wait_for_timeout(2000)
    await dismiss_dialog(page)
    if not await pick_combo(page, "action", "Forward to"):
        rec("act_failed")
        return False
    await page.wait_for_timeout(2500)
    await dismiss_dialog(page)
    # address
    combo = page.locator('[role="combobox"][aria-label*="action" i]').first
    cb = await combo.bounding_box()
    if not cb:
        rec("no_combo_box")
        return False
    await page.mouse.click(cb["x"] + cb["width"] + 130, cb["y"] + cb["height"] / 2)
    await page.wait_for_timeout(900)
    await page.keyboard.type(dest, delay=40)
    await page.wait_for_timeout(1800)
    for attempt in range(5):
        await dismiss_dialog(page)
        tnow = await body_text(page)
        low = tnow.lower()
        if dest.lower() in low and "use this address" not in low:
            break
        clicked = False
        for fn in (
            lambda: page.get_by_role("button", name=f"Use this address: {dest}").first.click(timeout=4000),
            lambda: page.get_by_role("button", name=re.compile(r"Use this address", re.I)).first.click(timeout=4000),
            lambda: page.get_by_text(re.compile(r"Use this address", re.I)).first.click(timeout=4000),
        ):
            try:
                await fn()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            await page.mouse.click(cb["x"] + cb["width"] + 130, cb["y"] + cb["height"] / 2)
            await page.wait_for_timeout(500)
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(dest, delay=60)
        await page.wait_for_timeout(2000)
    t = await body_text(page)
    rec("editor_filled", verify_wall=("verify your account" in t.lower()),
        tail=t[-500:])
    await shot(page, "filled")
    return True

async def dump_inputs(page, tag):
    try:
        inputs = await page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('input, button, a').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            const s = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
            out.push({tag: el.tagName, type: el.type || '', text: s.slice(0, 60),
                      id: el.id || '', name: el.name || ''});
          });
          return out.slice(0, 40);
        }""")
        rec(tag, elements=inputs)
    except Exception as e:
        rec(tag, err=str(e)[:100])

async def explore_signin(ctx, page, acc):
    """Click the Sign in link in the editor and follow what happens."""
    pages_before = list(ctx.pages)
    clicked = False
    for fn in (
        lambda: page.get_by_role("link", name="Sign in").first.click(timeout=5000),
        lambda: page.get_by_text("Sign in", exact=True).first.click(timeout=5000),
        lambda: page.get_by_role("button", name="Sign in").first.click(timeout=5000),
    ):
        try:
            await fn()
            clicked = True
            break
        except Exception:
            continue
    rec("signin_click", clicked=clicked)
    if not clicked:
        await dump_inputs(page, "signin_not_found")
        await shot(page, "nosignin")
        return
    last_sig = ""
    password_done = False
    for step in range(24):
        await page.wait_for_timeout(5000)
        # track every page in the context
        all_pages = list(ctx.pages)
        active = all_pages[-1] if all_pages else page
        try:
            url = active.url
        except Exception:
            url = "?"
        t = await body_text(active)
        sig = (url + "|" + t[:200])
        if sig != last_sig:
            last_sig = sig
            rec(f"step_{step}", url=url, text=t[:900])
            await shot(active, f"step{step}")
            await dump_inputs(active, f"inputs_{step}")
        low = t.lower()
        # password prompt?
        if not password_done:
            try:
                pw = active.locator('input[type="password"]').first
                if await pw.is_visible(timeout=1000):
                    await pw.fill(acc["password"])
                    rec("password_filled", step=step)
                    password_done = True
                    for fn in (
                        lambda: active.get_by_role("button", name=re.compile(r"sign in|next", re.I)).first.click(timeout=5000),
                        lambda: active.locator('input[type="submit"]').first.click(timeout=5000),
                    ):
                        try:
                            await fn()
                            rec("password_submitted", step=step)
                            break
                        except Exception:
                            continue
                    continue
            except Exception:
                pass
        # code entry step? observe only
        if any(k in low for k in ("enter the code", "verification code", "we sent a code", "enter code", "security code")):
            rec("CODE_STEP_REACHED", url=url, text=t[:1200])
            await shot(active, "codestep")
            await dump_inputs(active, "code_inputs")
            return
        # back in outlook editor?
        if "outlook.live.com" in url and "add an action" in low:
            try:
                btn = active.get_by_role("button", name="Save").first
                en = await btn.is_enabled()
            except Exception:
                en = None
            rec("back_in_editor", save_enabled=en, step=step)
            if en:
                try:
                    await btn.click(timeout=8000)
                except Exception:
                    await btn.evaluate("el => el.click()")
                await active.wait_for_timeout(4000)
                rec("saved_after_verify", tail=(await body_text(active))[-300:])
            return
        if "outlook.live.com" in url and "add rule" in low and step > 2:
            rec("back_at_rules_list", step=step)
            return
    rec("explore_timeout")

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, required=True)
    args = ap.parse_args()
    idx = args.index
    accounts = load_accounts()
    acc = accounts[idx]
    dest = dest_for(idx, acc["email"])
    rec("start", idx=idx, email=acc["email"], dest=dest)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(user_agent=UAS[idx % len(UAS)], viewport=VWS[idx % len(VWS)], timezone_id=TZS[idx % len(TZS)])
        page = await ctx.new_page()
        ok = await login(page, acc)
        rec("login", ok=ok)
        if ok:
            await open_rules(page)
            t0 = await body_text(page)
            rec("rules_before", has_dest=(dest.lower() in t0.lower()))
            if dest.lower() not in t0.lower():
                if await fill_editor(page, dest):
                    t = await body_text(page)
                    if "verify your account" in t.lower():
                        await explore_signin(ctx, page, acc)
                    else:
                        rec("no_verify_wall")
                    # final state check
                    await open_rules(page)
                    t2 = await body_text(page)
                    rec("final_rules_state", has_rule=(RULE_NAME in t2), has_dest=(dest.lower() in t2.lower()))
        await browser.close()
    rec("done")

asyncio.run(main())
