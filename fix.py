"""Verify-and-fix forwarding for accounts previously marked done.

Per account: log in, READ the forwarding settings. If already correct
(enabled + right destination) -> "confirmed", change nothing.
If wrong/missing -> re-apply forwarding via forward.create_rule, then
RE-READ the settings fresh; only a matching read-back counts as fixed:
  confirmed       - was already set correctly
  fixed_confirmed - was wrong, re-applied, read-back now correct
  fix_failed      - re-applied but read-back still wrong (details kept)
  wall            - verification wall present, cannot proceed
  login_failed    - could not log in
Writes fix.json incrementally.
"""
import argparse, asyncio, json
from playwright.async_api import async_playwright
from forward import login, load_accounts, create_rule, dest_for
from verify import read_settings, UAS, VWS, TZS
import random

RESULTS = {}

def save():
    with open("fix.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

def is_correct(s, dest):
    return bool(s) and s.get("enabled") == "true" and (s.get("address") or "").strip().lower() == dest.lower()

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
                s1 = await read_settings(page)
                res["before"] = {"enabled": s1.get("enabled"), "address": s1.get("address"), "wall": s1.get("wall")}
                print(f"[{idx}] BEFORE enabled={s1.get('enabled')} address={s1.get('address')!r} wall={s1.get('wall')}", flush=True)
                if s1.get("wall"):
                    res["status"] = "wall"
                elif is_correct(s1, dest):
                    res["status"] = "confirmed"
                else:
                    made = await create_rule(page, dest, acc)
                    res["create_rule_returned"] = bool(made)
                    s2 = await read_settings(page)
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
