"""Proof test for the OTP reader page:
1) exchange the account's refresh token (IMAP scope), decode token claims
2) try reading newest messages via Outlook REST API (HTTPS)
3) try Microsoft Graph with a Graph-scoped exchange
4) IMAP XOAUTH2 control test
5) CORS probe: does the token endpoint answer a browser Origin?
Writes resttest.json. Never prints token values.
"""
import argparse, base64, csv, imaplib, json, re, urllib.request, urllib.parse, urllib.error

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
OUT = {}

def exchange(rt, cid, scope):
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
                                   "client_id": cid, "scope": scope}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())

def claims(at):
    try:
        p = at.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return {"aud": d.get("aud"), "scp": d.get("scp")}
    except Exception:
        return {}

def get(url, at):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)[:100]}

def codes(text):
    return re.findall(r"\b(\d{4,8})\b", text or "")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, required=True)
    args = ap.parse_args()
    with open("accounts.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("email")]
    acc = rows[args.index]
    OUT["email"] = acc["email"]

    # 1) exchange with IMAP scope
    try:
        tok = exchange(acc["refresh_token"], acc["client_id"],
                       "https://outlook.office.com/IMAP.AccessAsUser.All offline_access")
        at = tok["access_token"]
        OUT["exchange_imap_scope"] = {"ok": True, "claims": claims(at)}
        OUT["rotated_again"] = bool(tok.get("refresh_token") and tok["refresh_token"] != acc["refresh_token"])
    except Exception as e:
        OUT["exchange_imap_scope"] = {"ok": False, "err": str(e)[:150]}
        at = None

    # 2) Outlook REST messages
    if at:
        st, d = get("https://outlook.office.com/api/v2.0/me/messages?$top=5&$orderby=ReceivedDateTime%20desc&$select=Subject,From,ReceivedDateTime,BodyPreview", at)
        msgs = []
        for m in (d.get("value", []) if isinstance(d, dict) else []):
            frm = ((m.get("From") or {}).get("EmailAddress") or {}).get("Address", "")
            msgs.append({"subject": m.get("Subject", "")[:80], "from": frm,
                         "at": m.get("ReceivedDateTime", ""),
                         "codes": codes(m.get("Subject", "") + " " + m.get("BodyPreview", ""))})
        OUT["rest_messages"] = {"status": st, "count": len(msgs), "messages": msgs,
                                "err": (d.get("error") or {}).get("message", "")[:120] if isinstance(d, dict) else ""}

    # 3) Graph attempt
    try:
        gtok = exchange(acc["refresh_token"], acc["client_id"], "https://graph.microsoft.com/Mail.Read offline_access")
        gat = gtok["access_token"]
        st, d = get("https://graph.microsoft.com/v1.0/me/messages?$top=5&$select=subject,from,receivedDateTime,bodyPreview", gat)
        OUT["graph_messages"] = {"status": st, "count": len(d.get("value", [])) if isinstance(d, dict) else 0,
                                 "err": (d.get("error") or {}).get("message", "")[:120] if isinstance(d, dict) else ""}
    except Exception as e:
        OUT["graph_messages"] = {"status": None, "err": str(e)[:150]}

    # 4) IMAP control
    if at:
        try:
            auth = f"user={acc['email']}\x01auth=Bearer {at}\x01\x01"
            imap = imaplib.IMAP4_SSL("outlook.office365.com", 993, timeout=30)
            imap.authenticate("XOAUTH2", lambda x: auth.encode())
            imap.select("INBOX", readonly=True)
            typ, data = imap.search(None, "ALL")
            ids = data[0].split()
            subs = []
            for mid in ids[-3:][::-1]:
                t2, d2 = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                raw = d2[0][1].decode("utf-8", "replace") if d2 and d2[0] else ""
                subs.append(raw.replace("\r\n", " ")[:160])
            OUT["imap"] = {"ok": True, "inbox_count": len(ids), "newest_headers": subs}
            imap.logout()
        except Exception as e:
            OUT["imap"] = {"ok": False, "err": str(e)[:150]}

    # 5) CORS probe on the token endpoint
    try:
        data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": acc["refresh_token"],
                                       "client_id": acc["client_id"],
                                       "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"}).encode()
        req = urllib.request.Request(TOKEN_URL, data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "Origin": "https://ejaroszewski.github.io"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            OUT["cors_probe"] = {"acao": resp.headers.get("Access-Control-Allow-Origin")}
    except urllib.error.HTTPError as e:
        OUT["cors_probe"] = {"acao": e.headers.get("Access-Control-Allow-Origin"), "http": e.code}
    except Exception as e:
        OUT["cors_probe"] = {"err": str(e)[:100]}

    with open("resttest.json", "w") as f:
        json.dump(OUT, f, indent=1)
    print(json.dumps(OUT, indent=1)[:2000], flush=True)

main()
