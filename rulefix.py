"""Alternative route: forward via an inbox RULE instead of the
Forwarding setting (which is gated by the recovery-method dialog).

Per account: log in, open Settings > Mail > Rules. If a rule already
forwards to the destination -> verify. Else create one:
name FWD-ALL, condition "Apply to all messages", action "Forward to"
<destination>. Detect whether the recovery dialog blocks saving.
Confirm by fresh reload, then by a fresh-session login (triple check).
Writes rulefix.json incrementally; screenshots for early accounts.
"""
import argparse, asyncio, json, random
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import UAS, VWS, TZS

RESULTS = {}
SHOT_BUDGET = [16]
RULE_NAME = "FWD-ALL"
RULES_URL = "https://outlook.live.com/mail/0/options/mail/rules"

def save():
    with open("rulefix.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

async def shot(page, idx, tag):
    if SHOT_BUDGET[0] > 0:
        try:
            await page.screenshot(path=f"rule-{idx}-{tag}.png")
            SHOT_BUDGET[0] -= 1
        except Exception:
            pass

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def open_rules(page):
    await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        t = await body_text(page)
        if "Add new rule" in t or "rule" in t.lower():
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(4000)

async def rules_state(page, dest):
    """Read the rules list as currently rendered."""
    t = await body_text(page)
    return {
        "has_rule_name": RULE_NAME in t,
        "has_dest": dest.lower() in t.lower(),
        "dialog": "recovery method" in t.lower(),
        "snippet": t[t.lower().find("fwd") - 40:t.lower().find("fwd") + 160] if "fwd" in t.lower() else "",
    }

async def click_text(page, text, timeout=6000):
    for exact in [False, True]:
        try:
            el = page.get_by_text(text, exact=exact).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.click(timeout=6000)
            return True
        except Exception:
            continue
    return False

async def pick_dropdown_option(page, option_text, steps, tag):
    """After a dropdown opens, click the option with this text."""
    for scope_try in range(3):
        try:
            opt = page.get_by_role("option", name=option_text).first
            await opt.wait_for(state="visible", timeout=5000)
            await opt.click(timeout=5000)
            steps[f"{tag}_option"] = option_text
            return True
        except Exception:
            pass
        try:
            el = page.get_by_text(option_text, exact=True).first
            await el.wait_for(state="visible", timeout=3000)
            await el.click(timeout=5000)
            steps[f"{tag}_option_text"] = option_text
            return True
        except Exception:
            pass
        await page.wait_for_timeout(1500)
    return False

async def create_rule(page, dest, idx, steps):
    # Open the editor
    opened = False
    for txt in ["Add new rule", "Add rule"]:
        if await click_text(page, txt):
            opened = True
            steps["editor_open_via"] = txt
            break
    if not opened:
        try:
            btn = page.get_by_role("button", name="Add new rule").first
            await btn.click(timeout=6000)
            opened = True
        except Exception:
            pass
    steps["editor_opened"] = opened
    if not opened:
        return False
    await page.wait_for_timeout(4000)
    await shot(page, idx, "editor")
    # Dump the editor structure for diagnosis
    try:
        dump = await page.evaluate("""() => {
          const out = {buttons: [], inputs: [], selects: []};
          document.querySelectorAll('button').forEach(b => {
            const s = (b.innerText || b.getAttribute('aria-label') || '').trim();
            if (s) out.buttons.push(s.slice(0, 60));
          });
          document.querySelectorAll('input').forEach(i => {
            out.inputs.push({type: i.type, placeholder: i.placeholder || '', aria: i.getAttribute('aria-label') || '', value: i.value || ''});
          });
          document.querySelectorAll('[role="combobox"], select').forEach(s => {
            out.selects.push((s.getAttribute('aria-label') || s.innerText || '').slice(0, 60));
          });
          return out;
        }""")
        steps["editor_dump"] = dump
    except Exception:
        pass
    # Rule name: first visible text input
    try:
        name_box = page.locator('.optionsModal input[type="text"], input[type="text"]').first
        await name_box.wait_for(state="visible", timeout=8000)
        await name_box.click(timeout=4000)
        await name_box.fill(RULE_NAME)
        steps["name_filled"] = True
    except Exception as e:
        steps["name_error"] = str(e)[:80]
    # Condition: open the condition dropdown, choose "Apply to all messages"
    cond = False
    for txt in ["Add a condition", "Select a condition", "Choose a condition"]:
        if await click_text(page, txt, timeout=4000):
            cond = await pick_dropdown_option(page, "Apply to all messages", steps, "cond")
            if cond:
                break
    steps["condition_set"] = cond
    await page.wait_for_timeout(2500)
    # Action: open the action dropdown, choose "Forward to"
    act = False
    for txt in ["Add an action", "Select an action", "Choose an action"]:
        if await click_text(page, txt, timeout=4000):
            act = await pick_dropdown_option(page, "Forward to", steps, "action")
            if act:
                break
    steps["action_set"] = act
    await page.wait_for_timeout(2500)
    await shot(page, idx, "action")
    # Address: a people/address box appears; type dest and commit with Enter
    addr = False
    for sel in ['input[placeholder*="people" i]', 'input[aria-label*="people" i]',
                'input[placeholder*="email" i]', 'input[type="text"]']:
        try:
            boxes = page.locator(sel)
            n = await boxes.count()
            for i in range(n - 1, -1, -1):
                b = boxes.nth(i)
                if await b.is_visible():
                    cur = await b.input_value()
                    if cur.strip() == RULE_NAME:
                        continue
                    await b.click(timeout=4000)
                    await b.type(dest, delay=30)
                    await page.wait_for_timeout(1200)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(1500)
                    steps["address_typed_in"] = sel
                    addr = True
                    break
            if addr:
                break
        except Exception:
            continue
    steps["address_set"] = addr
    await shot(page, idx, "filled")
    # Save
    saved = False
    for scope in [page]:
        try:
            btn = scope.get_by_role("button", name="Save").first
            await btn.wait_for(state="visible", timeout=6000)
            try:
                await btn.click(timeout=8000)
            except Exception:
                await btn.evaluate("el => el.click()")
            saved = True
            break
        except Exception:
            continue
    steps["save_clicked"] = saved
    await page.wait_for_timeout(5000)
    steps["dialog_after_save"] = "recovery method" in (await body_text(page)).lower()
    await shot(page, idx, "saved")
    return saved

async def fresh_verify(idx, acc, dest):
    """New browser + fresh login; read the rules list."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                user_agent=random.choice(UAS), viewport=random.choice(VWS),
                locale="en-US", timezone_id=random.choice(TZS),
                color_scheme="light", device_scale_factor=1)
            page = await ctx.new_page()
            if not await login(page, acc):
                return {"login": False}
            await open_rules(page)
            st = await rules_state(page, dest)
            st["login"] = True
            return st
        finally:
            try:
                await browser.close()
            except Exception:
                pass

async def do_one(idx, acc):
    dest = dest_for(idx)
    res = {"idx": idx, "email": acc["email"], "dest": dest}
    steps = {}
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
            if not ok:
                res["status"] = "login_failed"
            else:
                await open_rules(page)
                try:
                    await page.screenshot(path=f"rule-{idx}-list.png")
                except Exception:
                    pass
                res["list_text"] = (await body_text(page))[:1500]
                st0 = await rules_state(page, dest)
                res["before"] = st0
                print(f"[{idx}] BEFORE rule_name={st0['has_rule_name']} dest={st0['has_dest']}", flush=True)
                if st0["has_dest"]:
                    res["status"] = "rule_exists"
                else:
                    made = await create_rule(page, dest, idx, steps)
                    res["created"] = bool(made)
                    if steps.get("dialog_after_save"):
                        res["status"] = "dialog_blocked"
                    else:
                        await open_rules(page)
                        st1 = await rules_state(page, dest)
                        res["after_reload"] = st1
                        res["status"] = "rule_created" if st1["has_dest"] else "rule_failed"
        except Exception as e:
            res["status"] = "error"
            res["error"] = str(e)[:200]
            print(f"[{idx}] ERROR {res['error']}", flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    # Triple check for positives
    if res.get("status") in ("rule_created", "rule_exists"):
        try:
            fv = await fresh_verify(idx, acc, dest)
            res["triple"] = fv
            if fv.get("login") and fv.get("has_dest"):
                res["triple_checked"] = True
                res["status"] = "rule_confirmed"
            else:
                res["triple_checked"] = False
                res["status"] = "triple_check_failed"
        except Exception as e:
            res["triple_checked"] = False
            res["status"] = "triple_check_failed"
            res["triple_error"] = str(e)[:120]
        print(f"[{idx}] TRIPLE {'PASS' if res.get('triple_checked') else 'FAIL'}", flush=True)
    res["steps"] = steps
    RESULTS[acc["email"]] = res
    save()
    print(f"[{idx}] STATUS {res['status']}", flush=True)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    ap.add_argument("--concurrency", type=int, default=1)
    args = ap.parse_args()
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip()]
    sem = asyncio.Semaphore(max(1, args.concurrency))
    async def guarded(i):
        async with sem:
            await do_one(i, accounts[i])
    await asyncio.gather(*[guarded(i) for i in idxs])
    from collections import Counter
    print("RULEFIX DONE", Counter(r["status"] for r in RESULTS.values()), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
