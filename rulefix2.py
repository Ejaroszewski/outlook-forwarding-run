"""Rule-based forwarding, v2 — built on the probed editor structure.
Per account: login, open Settings > Mail > Rules. If a rule already
forwards to the destination -> verify only. Else create:
name FWD-ALL, condition "Apply to all messages", action "Forward to"
<destination>. Detect whether the recovery dialog blocks saving.
Confirm by fresh reload, then by a fresh-session login (triple check).
Writes rulefix2.json incrementally.
"""
import argparse, asyncio, json, re
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import UAS, VWS, TZS

RESULTS = {}
SHOT_BUDGET = [24]
RULE_NAME = "FWD-ALL"
RULES_URL = "https://outlook.live.com/mail/0/options/mail/rules"

def save():
    with open("rulefix2.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

async def shot(page, idx, tag):
    if SHOT_BUDGET[0] > 0:
        try:
            await page.screenshot(path=f"rule2-{idx}-{tag}.png")
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
    for _ in range(25):
        if "Add rule" in await body_text(page):
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(2500)

async def rules_state(page, dest):
    t = await body_text(page)
    return {
        "has_rule_name": RULE_NAME in t,
        "has_dest": dest.lower() in t.lower(),
        "dialog": "recovery method" in t.lower(),
    }

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

async def dismiss_dialog(page):
    """If the recovery-method dialog overlays the page, dismiss with Not now."""
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

async def pick_combo(page, combo_aria, option_part, steps, tag):
    """Open a Fluent combobox, click the option, and VERIFY the combo now
    shows the option. The recovery dialog can appear at any moment and
    swallow the interaction, so: dismiss before, retry the whole pick,
    verify after."""
    for attempt in range(3):
        await dismiss_dialog(page)
        try:
            combo = page.locator(f'[role="combobox"][aria-label*="{combo_aria}" i]').first
            await combo.wait_for(state="visible", timeout=8000)
            await combo.click(timeout=6000)
        except Exception as e:
            steps[f"{tag}_combo_error_{attempt}"] = str(e)[:80]
            await page.wait_for_timeout(1500)
            continue
        await page.wait_for_timeout(1800)
        try:
            opts = await page.evaluate("""() => {
              const seen = [];
              document.querySelectorAll('[role="option"], [role="menuitem"]').forEach(o => {
                const s = (o.innerText || '').trim();
                if (s) seen.push(s.slice(0, 60));
              });
              return seen;
            }""")
            steps[f"{tag}_options"] = opts[:30]
        except Exception:
            pass
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
            steps[f"{tag}_pick_failed_{attempt}"] = True
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1200)
            continue
        await page.wait_for_timeout(1200)
        # verify the combo now displays the chosen option
        try:
            shown = await combo.inner_text()
        except Exception:
            shown = ""
        steps[f"{tag}_shown_{attempt}"] = shown[:60]
        if option_part.lower() in shown.lower():
            steps[f"{tag}_picked"] = option_part
            await dismiss_dialog(page)
            return True
        await page.wait_for_timeout(1000)
    return False

async def create_rule(page, dest, idx, steps):
    if not await open_editor(page):
        steps["editor_opened"] = False
        return False
    steps["editor_opened"] = True
    steps["dialog_at_editor"] = await dismiss_dialog(page)
    await shot(page, idx, "editor")
    # Name
    try:
        name_box = page.locator('input[placeholder="Name your rule"]').first
        await name_box.wait_for(state="visible", timeout=8000)
        await name_box.click(timeout=4000)
        await name_box.fill(RULE_NAME)
        steps["name_filled"] = True
    except Exception as e:
        steps["name_error"] = str(e)[:100]
    # Condition
    steps["condition_set"] = await pick_combo(page, "conditional", "Apply to all messages", steps, "cond")
    await page.wait_for_timeout(2000)
    steps["dialog_after_cond"] = await dismiss_dialog(page)
    # Action
    steps["action_set"] = await pick_combo(page, "action", "Forward to", steps, "act")
    await page.wait_for_timeout(2500)
    steps["dialog_after_action"] = await dismiss_dialog(page)
    await shot(page, idx, "action")
    if not steps.get("condition_set") or not steps.get("action_set"):
        steps["abort"] = "condition or action not set after retries"
        return False
    # Address: the recipient box sits in the same row as the action
    # combobox, immediately to its right. Find it by geometry.
    addr = False
    try:
        combo = page.locator('[role="combobox"][aria-label*="action" i]').first
        cb = await combo.bounding_box()
        steps["combo_box"] = cb
        cands = await page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('input').forEach(i => {
            const r = i.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) out.push({
              type: i.type, ph: i.placeholder || '', aria: i.getAttribute('aria-label') || '',
              x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
              val: i.value || ''
            });
          });
          return out;
        }""")
        steps["address_candidates"] = cands
        target = None
        if cb:
            for c in cands:
                if abs(c["y"] - cb["y"]) <= 12 and c["x"] > cb["x"] + cb["width"] - 10:
                    if "Search" in (c["ph"] or "") or "Name your rule" in (c["ph"] or ""):
                        continue
                    target = c
                    break
        if target:
            await page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
        elif cb:
            # No input in the DOM yet: the recipient well is a container
            # that mounts its input on click. Click its center.
            cx = cb["x"] + cb["width"] + 130
            cy = cb["y"] + cb["height"] / 2
            steps["address_container_click"] = {"x": cx, "y": cy}
            await page.mouse.click(cx, cy)
        else:
            raise RuntimeError("no action combobox box")
        await page.wait_for_timeout(900)
        # Whatever input the click mounted should now be focused.
        await page.keyboard.type(dest, delay=40)
        await page.wait_for_timeout(1800)
        steps["address_typed"] = True
        # Commit loop: the picker shows a "Use this address: <addr>" button.
        # Clicking the real button commits the recipient as a pill; an
        # uncommitted address is discarded on blur, so verify and retry.
        committed = False
        for attempt in range(4):
            tnow = await body_text(page)
            low = tnow.lower()
            if dest.lower() in low and "use this address" not in low:
                committed = True
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
            steps[f"commit_attempt_{attempt}"] = "clicked" if clicked else "no_button"
            if not clicked and cb:
                await page.mouse.click(cb["x"] + cb["width"] + 130, cb["y"] + cb["height"] / 2)
                await page.wait_for_timeout(500)
                await page.keyboard.press("Control+a")
                await page.keyboard.type(dest, delay=40)
            await page.wait_for_timeout(2000)
        tnow = await body_text(page)
        steps["use_this_still_showing"] = "use this address" in tnow.lower()
        steps["address_committed"] = bool(committed or (dest.lower() in tnow.lower() and not steps["use_this_still_showing"]))
        addr = True
    except Exception as e:
        steps["address_error"] = str(e)[:150]
    steps["address_set"] = addr
    steps["dialog_after_address"] = await dismiss_dialog(page)
    t = await body_text(page)
    steps["dest_visible_before_save"] = dest.lower() in t.lower()
    await shot(page, idx, "filled")
    # Save
    saved = False
    try:
        btn = page.get_by_role("button", name="Save").first
        await btn.wait_for(state="visible", timeout=8000)
        steps["save_enabled"] = await btn.is_enabled()
        try:
            await btn.click(timeout=8000)
        except Exception:
            await btn.evaluate("el => el.click()")
        saved = True
    except Exception as e:
        steps["save_error"] = str(e)[:100]
    steps["save_clicked"] = saved
    await page.wait_for_timeout(3000)
    if await dismiss_dialog(page):
        steps["dialog_after_save_dismissed"] = True
        # The save click may have been swallowed by the dialog; retry once
        # if the editor is still open with an enabled Save.
        try:
            btn = page.get_by_role("button", name="Save").first
            if await btn.is_enabled():
                await btn.click(timeout=6000)
                steps["save_clicked_retry"] = True
                await page.wait_for_timeout(3000)
                await dismiss_dialog(page)
        except Exception:
            pass
    await page.wait_for_timeout(2000)
    t = await body_text(page)
    steps["dialog_after_save"] = "recovery method" in t.lower()
    steps["after_save_text_tail"] = t[-400:]
    await shot(page, idx, "saved")
    return saved

async def deep_state(page, dest):
    """List check + open the rule to read the recipient inside it.
    The collapsed list does not render the address, so has_dest from the
    list alone is not enough."""
    t = await body_text(page)
    st = {
        "has_rule_name": RULE_NAME in t,
        "has_dest": dest.lower() in t.lower(),
        "dialog": "recovery method" in t.lower(),
    }
    if st["has_rule_name"] and not st["has_dest"]:
        opened = False
        for fn in (
            lambda: page.get_by_text(RULE_NAME).first.click(timeout=6000),
            lambda: page.get_by_role("button", name=RULE_NAME).first.click(timeout=6000),
        ):
            try:
                await fn()
                opened = True
                break
            except Exception:
                continue
        if opened:
            await page.wait_for_timeout(3500)
            t2 = await body_text(page)
            st["has_dest"] = dest.lower() in t2.lower()
            st["opened_rule"] = True
            try:
                await page.get_by_role("button", name="Discard").first.click(timeout=5000)
            except Exception:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
            await page.wait_for_timeout(1500)
    return st

async def fresh_verify(idx, acc, dest):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(user_agent=UAS[idx % len(UAS)], viewport=VWS[idx % len(VWS)], timezone_id=TZS[idx % len(TZS)])
        page = await ctx.new_page()
        try:
            if not await login(page, acc):
                return {"login": False}
            await open_rules(page)
            st = await deep_state(page, dest)
            st["login"] = True
            return st
        finally:
            await browser.close()

async def do_one(p, idx, acc):
    dest = dest_for(idx, acc["email"])
    res = {"idx": idx, "email": acc["email"], "dest": dest}
    RESULTS[acc["email"]] = res
    save()
    browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = await browser.new_context(user_agent=UAS[idx % len(UAS)], viewport=VWS[idx % len(VWS)], timezone_id=TZS[idx % len(TZS)])
    page = await ctx.new_page()
    try:
        ok = await login(page, acc)
        res["login"] = ok
        if not ok:
            res["status"] = "login_failed"
            return
        await open_rules(page)
        st0 = await rules_state(page, dest)
        res["before"] = st0
        if st0["has_dest"]:
            res["status"] = "rule_exists"
        else:
            steps = {}
            made = await create_rule(page, dest, idx, steps)
            res["steps"] = steps
            res["created"] = bool(made)
            await open_rules(page)
            st1 = await deep_state(page, dest)
            res["after_reload"] = st1
            res["status"] = "rule_created" if st1["has_dest"] else "rule_failed"
        save()
    except Exception as e:
        res["status"] = "error"
        res["error"] = str(e)[:200]
    finally:
        await browser.close()
    if res.get("status") in ("rule_created", "rule_exists"):
        res["triple"] = await fresh_verify(idx, acc, dest)
        res["confirmed"] = bool(res["triple"].get("has_dest"))
        if res["confirmed"]:
            res["status"] = "confirmed"
    save()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip() != ""]
    sem = asyncio.Semaphore(args.concurrency)
    async with async_playwright() as p:
        async def guarded(idx):
            async with sem:
                await do_one(p, idx, accounts[idx])
        await asyncio.gather(*(guarded(i) for i in idxs))
    save()
    conf = sum(1 for r in RESULTS.values() if r.get("status") == "confirmed")
    print(f"done: {len(RESULTS)} accounts, {conf} triple-confirmed")

asyncio.run(main())
