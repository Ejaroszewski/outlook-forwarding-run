"""Token liveness sweep: for every account, exchange its stored refresh
token at Microsoft's token endpoint. Records per-account success/failure
and captures the NEW refresh token when Microsoft rotates it, so the
exported sheet always carries the freshest token. Never prints tokens.
Writes tokensweep.json: {"<idx>": {"ok": bool, "err": str, "new_rt": str|""}}
"""
import csv, json, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor

ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"

def check(idx, row):
    try:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": row.get("refresh_token", ""),
            "client_id": row.get("client_id", ""),
            "scope": SCOPE,
        }).encode()
        req = urllib.request.Request(ENDPOINT, data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            d = json.loads(resp.read().decode())
        new_rt = d.get("refresh_token", "")
        return idx, {"ok": True, "err": "", "new_rt": new_rt if new_rt and new_rt != row.get("refresh_token", "") else ""}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            err = str(body.get("error", f"http_{e.code}"))[:60]
        except Exception:
            err = f"http_{e.code}"
        return idx, {"ok": False, "err": err, "new_rt": ""}
    except Exception as e:
        return idx, {"ok": False, "err": str(e)[:60], "new_rt": ""}

def main():
    with open("accounts.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("email")]
    print(f"sweeping {len(rows)} accounts", flush=True)
    results = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for idx, res in ex.map(lambda t: check(*t), enumerate(rows)):
            results[str(idx)] = res
    with open("tokensweep.json", "w") as f:
        json.dump(results, f, indent=1)
    ok = sum(1 for r in results.values() if r["ok"])
    rotated = sum(1 for r in results.values() if r["new_rt"])
    from collections import Counter
    errs = Counter(r["err"] for r in results.values() if not r["ok"])
    print(f"done: {ok}/{len(rows)} tokens alive, {rotated} rotated, errors: {dict(errs)}", flush=True)

main()
