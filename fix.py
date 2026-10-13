"""Verify-and-fix forwarding for accounts previously marked done.

Per account: log in, READ the forwarding settings (forensic dump).
If already correct (Enable forwarding ON + address == destination)
-> "confirmed", change nothing.
If wrong/missing -> set it with a flow built for the current panel
(label-click the Enable forwarding toggle, fill the address that
appears, tick Keep-a-copy, Save), then RE-READ the settings from a
fresh page load. Only a matching read-back counts:
  confirmed       - was already set correctly
  fixed_confirmed - was wrong, re-applied, read-back now correct
  fix_failed      - re-applied but read-back still wrong (dump kept)
  wall            - verification wall present, cannot proceed
  login_failed    - could not log in
Writes fix.json incrementally; screenshots kept for the first accounts.
"""
import argparse, asyncio, json, random
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import read_settings, read_settings_dump, interpret, UAS, VWS, TZS

RESULTS = {}
SHOT_BUDGET = [6]  # screenshots for the first few accounts only

def save():
    with open("fix.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

def is_correct(s, dest):
    return bool(s) and s.get("enabled") == "true" and (s.get("address") or "").strip().lower() == dest.lower()

async def maybe_shot(page, idx, tag):
    if SHOT_BUDGET[0] > 0:
        try:
            await page.screenshot(path=f"fix-{idx}-{tag}.png")
            SHOT_BUDGET[0] -= 1
        except Exception:
            pass

async def set_forwarding(page, dest, idx):
    """Drive the current Forwarding panel. Returns dict of step outcomes."""
    steps = {}
    await page.goto("https://outlook.live.com/mail/0/options/mail/forwarding",
                    wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        try:
            t = await page.inner_text("body")
        except Exception:
            t = ""
        if "Enable forwarding" in t:
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(3000)
    modal = page.locator(".optionsModal").first
    # 1) Click the "Enable forwarding" label/toggle
    clicked = False
    for make in [
        lambda: modal.get_by_text("Enable forwarding", exact=False).first,
        lambda: page.get_by_text("Enable forwarding", exact=False).first,
    ]:
        try:
            el = make()
            await el.wait_for(state="visible", timeout=8000)
            await el.click(timeout=8000)
            clicked = True
            break
        except Exception:
            continue
    steps["toggle_clicked"] = clicked
    if not clicked:
        return steps
    await page.wait_for_timeout(2500)
    # Confirm the toggle actually turned on before continuing
    dump = await page.evaluate("""() => {
      const cbs = [];
      document.querySelectorAll('input[type="checkbox"]').forEach(el => {
        const w = el.closest('label') || el.parentElement;
        cbs.push({checked: el.checked, label: w ? (w.innerText||'').slice(0,60) : ''});
      });
      return cbs;
    }""")
    on = any(c["checked"] and "nable forwarding" in (c["label"] or "") for c in dump)
    steps["toggle_on_after_click"] = on
    if not on:
        # Fallback: JS-click the checkbox input itself
        try:
            await page.evaluate("""() => {
              const els = document.querySelectorAll('input[type="checkbox"]');
              for (const el of els) {
                const w = el.closest('label') || el.parentElement;
                if (w && (w.innerText||'').includes('Enable forwarding')) { el.click(); return true; }
              }
              return false;
            }""")
            await page.wait_for_timeout(2500)
            dump = await page.evaluate("""() => {
              const cbs = [];
              document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                const w = el.closest('label') || el.parentElement;
                cbs.push({checked: el.checked, label: w ? (w.innerText||'').slice(0,60) : ''});
              });
              return cbs;
            }""")
            on = any(c["checked"] and "nable forwarding" in (c["label"] or "") for c in dump)
            steps["toggle_on_after_js"] = on
        except Exception as e:
            steps["toggle_js_error"] = str(e)[:100]
    if not on:
        return steps
    # 2) Fill the address input that appears in the panel
    filled = False
    try:
        box = modal.locator('input[type="text"], input[type="email"]').first
        await box.wait_for(state="visible", timeout=10000)
        await box.click(timeout=5000)
        await box.fill(dest)
        await page.wait_for_timeout(800)
        val = await box.input_value()
        steps["address_field_value"] = val
        filled = val.strip().lower() == dest.lower()
        await page.keyboard.press("Tab")
    except Exception as e:
        steps["fill_error"] = str(e)[:100]
    steps["address_filled"] = filled
    if not filled:
        return steps
    # 3) Keep a copy of forwarded messages (if the checkbox is offered)
    try:
        cbs = modal.locator('input[type="checkbox"]')
        n = await cbs.count()
        for i in range(n):
            cb = cbs.nth(i)
            lab = await cb.evaluate("""el => {
              const w = el.closest('label') || el.parentElement;
              return w ? (w.innerText||'') : '';
            }""")
            if "keep a copy" in lab.lower() and not await cb.is_checked():
                await cb.evaluate("el => el.click()")
                steps["keep_copy_ticked"] = True
    except Exception:
        pass
    await maybe_shot(page, idx, "beforesave")
    # 4) Save — clicking Save pops a Microsoft recovery-method dialog
    # ("Keep access to your account with a recovery method") that BLOCKS
    # the save from completing. Dismiss it with "Not now", then save
    # again if the panel still shows unsaved changes. Loop up to 3 times.
    async def dismiss_recovery_dialog():
        try:
            t = await page.inner_text("body")
        except Exception:
            return False
        if "recovery method" not in t.lower():
            return False
        for scope in [page, modal]:
            try:
                btn = scope.get_by_role("button", name="Not now").first
                await btn.wait_for(state="visible", timeout=4000)
                try:
                    await btn.click(timeout=5000)
                except Exception:
                    await btn.evaluate("el => el.click()")
                await page.wait_for_timeout(2500)
                return True
            except Exception:
                continue
        return False

    save_attempts = 0
    dialog_seen = False
    for attempt in range(3):
        clicked_save = False
        for scope in [modal, page]:
            try:
                btn = scope.get_by_role("button", name="Save").first
                await btn.wait_for(state="visible", timeout=6000)
                try:
                    await btn.click(timeout=8000)
                except Exception:
                    await btn.evaluate("el => el.click()")
                clicked_save = True
                break
            except Exception:
                continue
        if not clicked_save:
            break  # no Save button visible -> changes already committed
        save_attempts += 1
        await page.wait_for_timeout(3000)
        if await dismiss_recovery_dialog():
            dialog_seen = True
        await page.wait_for_timeout(2000)
    steps["save_attempts"] = save_attempts
    steps["recovery_dialog_seen"] = dialog_seen
    await maybe_shot(page, idx, "aftersave")
    return steps

async def fix_one(idx, acc):
    dest = dest_for(idx)
    res = {"idx": idx, "email": acc["email"], "dest": dest}
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
                s1 = await read_settings(page, idx)
                res["before"] = {"enabled": s1.get("enabled"), "address": s1.get("address"), "wall": s1.get("wall")}
                print(f"[{idx}] BEFORE enabled={s1.get('enabled')} address={s1.get('address')!r} wall={s1.get('wall')}", flush=True)
                if s1.get("wall"):
                    res["status"] = "wall"
                elif is_correct(s1, dest):
                    res["status"] = "confirmed"
                else:
                    res["steps"] = await set_forwarding(page, dest, idx)
                    s2 = await read_settings(page, idx)
                    res["after"] = {"enabled": s2.get("enabled"), "address": s2.get("address"), "wall": s2.get("wall")}
                    print(f"[{idx}] AFTER enabled={s2.get('enabled')} address={s2.get('address')!r}", flush=True)
                    res["status"] = "fixed_confirmed" if is_correct(s2, dest) else "fix_failed"
        except Exception as e:
            res["status"] = "error"
            res["error"] = str(e)[:200]
            print(f"[{idx}] ERROR {res['error']}", flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    RESULTS[acc["email"]] = res
    save()
    print(f"[{idx}] STATUS {res['status']}", flush=True)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    args = ap.parse_args()
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip()]
    for idx in idxs:
        await fix_one(idx, accounts[idx])
    from collections import Counter
    print("FIX DONE", Counter(r["status"] for r in RESULTS.values()), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
