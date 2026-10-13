"""Read-only forwarding verification — forensic edition.

For each sampled account index: log in, open the Forwarding settings,
and dump EVERY interactive control on the page (switches, checkboxes,
inputs, with labels and values) plus a screenshot. Changes NOTHING.
Writes verify.json incrementally.
"""
import argparse, asyncio, json, random
from playwright.async_api import async_playwright
from forward import login, load_accounts

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
]
VWS = [{"width": 1366, "height": 768}, {"width": 1440, "height": 900},
       {"width": 1536, "height": 864}, {"width": 1920, "height": 1080}]
TZS = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"]

RESULTS = {}

def save():
    with open("verify.json", "w") as f:
        json.dump(RESULTS, f, indent=1)

DUMP_JS = """() => {
  const out = {switches: [], checkboxes: [], inputs: [], buttons: [], text: ''};
  out.text = (document.body ? document.body.innerText : '').slice(0, 8000);
  document.querySelectorAll('[role="switch"]').forEach(el => {
    out.switches.push({aria_checked: el.getAttribute('aria-checked'),
                       aria_label: el.getAttribute('aria-label'),
                       visible: !!(el.offsetWidth || el.offsetHeight)});
  });
  document.querySelectorAll('input[type="checkbox"]').forEach(el => {
    let lab = '';
    const wrap = el.closest('label') || el.parentElement;
    if (wrap) lab = (wrap.innerText || '').slice(0, 100);
    out.checkboxes.push({checked: el.checked, label: lab,
                         aria_label: el.getAttribute('aria-label'),
                         visible: !!(el.offsetWidth || el.offsetHeight)});
  });
  document.querySelectorAll('input').forEach(el => {
    if (el.type === 'checkbox' || el.type === 'radio' || el.type === 'hidden') return;
    out.inputs.push({type: el.type, value: el.value,
                     placeholder: el.placeholder || '',
                     aria_label: el.getAttribute('aria-label') || '',
                     visible: !!(el.offsetWidth || el.offsetHeight)});
  });
  return out;
}"""

def interpret(dump):
    """Extract {enabled, address, wall} from a forensic dump."""
    out = {"enabled": None, "address": None, "wall": False}
    if not dump:
        return out
    text = (dump.get("text") or "").lower()
    out["wall"] = "verify your account to forward" in text
    sw = [s for s in dump.get("switches", []) if s.get("visible")]
    if sw:
        out["enabled"] = sw[0].get("aria_checked")
    else:
        for cb in dump.get("checkboxes", []):
            lab = (cb.get("label") or "") + " " + (cb.get("aria_label") or "")
            if "enable forwarding" in lab.lower():
                out["enabled"] = "true" if cb.get("checked") else "false"
                break
    for inp in dump.get("inputs", []):
        v = (inp.get("value") or "").strip()
        if "@" in v:
            out["address"] = v
            break
    return out

async def read_settings(page, idx=0):
    dump = await read_settings_dump(page, idx)
    s = interpret(dump)
    s["raw"] = {"switches": dump.get("switches"), "checkboxes": dump.get("checkboxes"),
                "inputs": dump.get("inputs")}
    return s

async def read_settings_dump(page, idx):
    await page.goto("https://outlook.live.com/mail/0/options/mail/forwarding",
                    wait_until="domcontentloaded", timeout=60000)
    # Wait until the forwarding panel content shows up (up to ~60s)
    for _ in range(20):
        try:
            t = await page.inner_text("body")
        except Exception:
            t = ""
        if "Enable forwarding" in t or "Forwarding and IMAP" in t:
            break
        await page.wait_for_timeout(3000)
    await page.wait_for_timeout(6000)
    data = await page.evaluate(DUMP_JS)
    try:
        await page.screenshot(path=f"shot-{idx}.png", full_page=False)
    except Exception as e:
        print(f"    screenshot failed: {str(e)[:80]}", flush=True)
    return data

async def check_one(idx, acc, token):
    res = {"idx": idx, "email": acc["email"]}
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
            if ok:
                try:
                    dump = await read_settings_dump(page, idx)
                    res["dump"] = dump
                    res["settings"] = interpret(dump)
                    s = res["settings"]
                    print(f"[{idx}] SETTINGS enabled={s['enabled']} address={s['address']!r} wall={s['wall']} | switches={dump['switches']} inputs={dump['inputs']}", flush=True)
                except Exception as e:
                    res["dump_error"] = str(e)[:200]
                    print(f"[{idx}] DUMP ERROR {res['dump_error']}", flush=True)
        except Exception as e:
            res["error"] = str(e)[:200]
            print(f"[{idx}] ERROR {res['error']}", flush=True)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
    RESULTS[acc["email"]] = res
    save()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", required=True)
    ap.add_argument("--token", default="")
    args = ap.parse_args()
    accounts = load_accounts()
    idxs = [int(x) for x in args.indices.split(",") if x.strip()]
    for idx in idxs:
        await check_one(idx, accounts[idx], args.token)
    print("VERIFY DONE", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
