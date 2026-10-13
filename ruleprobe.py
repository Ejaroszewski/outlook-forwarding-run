"""Probe: open the Rules editor on one account and dump what it contains.
Imports the shared login/account helpers; creates nothing, saves nothing.
"""
import argparse, asyncio, json
from playwright.async_api import async_playwright
from forward import login, load_accounts, dest_for
from verify import UAS, VWS, TZS

RULES_URL = "https://outlook.live.com/mail/0/options/mail/rules"

async def body_text(page):
    try:
        return await page.inner_text("body")
    except Exception:
        return ""

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts-file", required=True)
    ap.add_argument("--encrypted-key", required=True)
    ap.add_argument("--index", type=int, required=True)
    args = ap.parse_args()
    accounts = load_accounts(args.accounts_file, args.encrypted_key)
    acc = accounts[args.index]
    dest = dest_for(args.index, acc)
    out = {"email": acc["email"], "dest": dest}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(user_agent=UAS[0], viewport=VWS[0], timezone_id=TZS[0])
        page = await ctx.new_page()
        ok = await login(page, acc)
        out["login"] = ok
        if ok:
            await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=60000)
            for _ in range(20):
                if "Add rule" in await body_text(page):
                    break
                await page.wait_for_timeout(3000)
            await page.wait_for_timeout(3000)
            clicked = False
            attempts = {}
            for label, fn in [
                ("role_button", lambda: page.get_by_role("button", name="Add rule").first.click(timeout=6000)),
                ("text", lambda: page.get_by_text("Add rule").first.click(timeout=6000)),
            ]:
                try:
                    await fn()
                    clicked = True
                    attempts[label] = "ok"
                    break
                except Exception as e:
                    attempts[label] = str(e)[:100]
            out["click_attempts"] = attempts
            out["editor_clicked"] = clicked
            await page.wait_for_timeout(4000)
            try:
                await page.screenshot(path=f"probe-{args.index}-editor.png")
            except Exception:
                pass
            out["editor_text"] = (await body_text(page))[:2500]
            try:
                dump = await page.evaluate("""() => {
                  const out = {buttons: [], inputs: [], combos: []};
                  document.querySelectorAll('button').forEach(b => {
                    const s = (b.innerText || b.getAttribute('aria-label') || '').trim();
                    if (s) out.buttons.push(s.slice(0, 60));
                  });
                  document.querySelectorAll('input').forEach(i => {
                    out.inputs.push({type: i.type, ph: i.placeholder || '', aria: i.getAttribute('aria-label') || '', val: i.value || ''});
                  });
                  document.querySelectorAll('[role="combobox"]').forEach(s => {
                    out.combos.push((s.getAttribute('aria-label') || s.innerText || '').slice(0, 60));
                  });
                  return out;
                }""")
                out["dom"] = dump
            except Exception as e:
                out["dom_error"] = str(e)[:120]
        await browser.close()
    with open("probe.json", "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out)[:400])

asyncio.run(main())
