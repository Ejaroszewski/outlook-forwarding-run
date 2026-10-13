"""Verify-only deep check: fresh login, open the Rules list, click the
FWD-ALL rule to open it, and report whether the destination is inside."""
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
    ap.add_argument("--index", type=int, required=True)
    args = ap.parse_args()
    accounts = load_accounts()
    acc = accounts[args.index]
    dest = dest_for(args.index)
    out = {"email": acc["email"], "dest": dest}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(user_agent=UAS[0], viewport=VWS[0], timezone_id=TZS[0])
        page = await ctx.new_page()
        out["login"] = await login(page, acc)
        if out["login"]:
            await page.goto(RULES_URL, wait_until="domcontentloaded", timeout=60000)
            for _ in range(25):
                if "Add rule" in await body_text(page):
                    break
                await page.wait_for_timeout(3000)
            await page.wait_for_timeout(2500)
            t = await body_text(page)
            out["list_has_name"] = "FWD-ALL" in t
            out["list_has_dest"] = dest.lower() in t.lower()
            try:
                await page.screenshot(path=f"verify-{args.index}-list.png")
            except Exception:
                pass
            # open the rule
            opened = False
            for fn in (
                lambda: page.get_by_text("FWD-ALL").first.click(timeout=6000),
                lambda: page.get_by_role("button", name="FWD-ALL").first.click(timeout=6000),
            ):
                try:
                    await fn()
                    opened = True
                    break
                except Exception:
                    continue
            out["rule_opened"] = opened
            await page.wait_for_timeout(3500)
            t2 = await body_text(page)
            out["open_has_dest"] = dest.lower() in t2.lower()
            out["open_has_forward_to"] = "forward to" in t2.lower()
            out["open_tail"] = t2[-700:]
            try:
                await page.screenshot(path=f"verify-{args.index}-open.png")
            except Exception:
                pass
        await browser.close()
    with open("ruleverify.json", "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out)[:300])

asyncio.run(main())
