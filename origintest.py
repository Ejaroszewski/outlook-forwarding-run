"""Isolate why the browser token exchange fails: try the SAME valid
exchange (a) without Origin (control), (b) with a browser Origin on the
consumers endpoint, (c) with a browser Origin on login.live.com's classic
token endpoint, (d) with Origin on the common endpoint. Writes
origintest.json with HTTP status + error code per variant (no secrets).
"""
import csv, json, urllib.request, urllib.parse, urllib.error

rows = list(csv.DictReader(open("accounts.csv", newline="", encoding="utf-8")))
row = rows[31]
ORIGIN = "https://ejaroszewski.github.io"
SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"

def attempt(url, origin):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": row["refresh_token"],
        "client_id": row["client_id"], "scope": SCOPE}).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            d = json.loads(resp.read().decode())
            return {"http": resp.status, "ok": bool(d.get("access_token")),
                    "error": "", "acao": resp.headers.get("Access-Control-Allow-Origin", "")}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
            err = str(d.get("error", "")) + " :: " + str(d.get("error_description", ""))[:110]
        except Exception:
            err = "unparseable"
        return {"http": e.code, "ok": False, "error": err,
                "acao": e.headers.get("Access-Control-Allow-Origin", "") if e.headers else ""}
    except Exception as e:
        return {"http": None, "ok": False, "error": str(e)[:110], "acao": ""}

out = {
    "control_no_origin": attempt("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", None),
    "consumers_with_origin": attempt("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", ORIGIN),
    "live_with_origin": attempt("https://login.live.com/oauth20_token.srf", ORIGIN),
    "common_with_origin": attempt("https://login.microsoftonline.com/common/oauth2/v2.0/token", ORIGIN),
}
json.dump(out, open("origintest.json", "w"), indent=1)
for k, v in out.items():
    print(k, "->", json.dumps(v))
