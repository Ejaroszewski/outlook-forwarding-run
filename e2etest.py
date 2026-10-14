"""End-to-end proof for the OTP reader page:
1. REST messages call WITH a browser Origin header -> record CORS header.
2. Send a 'Hello' email with a known code (482913) from account idx 32
   to account idx 31 via SMTP XOAUTH2.
3. Wait, then read idx 31's inbox via REST and confirm the hello email
   and the code are fetched. Writes e2etest.json. No secrets printed.
"""
import argparse, base64, csv, json, re, smtplib, time
import urllib.request, urllib.parse, urllib.error
from email.mime.text import MIMEText

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
CODE = "482913"

ap = argparse.ArgumentParser()
ap.add_argument("--target", type=int, default=31)
ap.add_argument("--sender", type=int, default=32)
a = ap.parse_args()
rows = list(csv.DictReader(open("accounts.csv", newline="", encoding="utf-8")))
tgt, snd = rows[a.target], rows[a.sender]
out = {"target_email": tgt["email"], "sender_email": snd["email"]}

def exchange(row, scope, origin=None):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": row["refresh_token"],
        "client_id": row["client_id"], "scope": scope}).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(TOKEN_URL, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode()), dict(resp.headers)

def read_msgs(access, origin=None):
    url = ("https://outlook.office.com/api/v2.0/me/messages?$top=8"
           "&$orderby=ReceivedDateTime%20desc&$select=Subject,From,ReceivedDateTime,BodyPreview")
    headers = {"Authorization": f"Bearer {access}"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode()), dict(resp.headers)

# 1. REST + Origin (browser simulation)
tok, _ = exchange(tgt, "https://outlook.office.com/IMAP.AccessAsUser.All offline_access")
access = tok["access_token"]
_, hdrs = read_msgs(access, origin="https://ejaroszewski.github.io")
out["rest_acao"] = hdrs.get("Access-Control-Allow-Origin", "")

# 2. SMTP hello from sender
try:
    stok, _ = exchange(snd, "https://outlook.office.com/SMTP.Send offline_access")
    saccess = stok["access_token"]
    msg = MIMEText(f"Hello! This is a test message from the team.\n\nYour verification code is {CODE}.\nPlease enter this code to continue.")
    msg["Subject"] = "Hello test - verification code inside"
    msg["From"] = snd["email"]
    msg["To"] = tgt["email"]
    auth = base64.b64encode(f"user={snd['email']}\x01auth=Bearer {saccess}\x01\x01".encode()).decode()
    with smtplib.SMTP("smtp-mail.outlook.com", 587, timeout=60) as s:
        s.ehlo(); s.starttls(); s.ehlo()
        code_, resp = s.docmd("AUTH XOAUTH2 " + auth)
        if code_ != 235:
            raise RuntimeError(f"SMTP auth failed: {code_}")
        s.sendmail(snd["email"], [tgt["email"]], msg.as_string())
    out["smtp"] = "sent"
except Exception as e:
    out["smtp"] = f"failed: {str(e)[:120]}"

# 3. Read back and look for the hello + code
time.sleep(30)
try:
    tok2, _ = exchange(tgt, "https://outlook.office.com/IMAP.AccessAsUser.All offline_access")
    data, _ = read_msgs(tok2["access_token"])
    subjects, found_hello, found_code = [], False, False
    for m in data.get("value", []):
        subj = m.get("Subject", "")
        prev = m.get("BodyPreview", "")
        subjects.append(subj[:60])
        if "Hello test" in subj:
            found_hello = True
        if CODE in subj + " " + prev:
            found_code = True
    out["readback"] = {"subjects": subjects, "hello_found": found_hello, "code_found": found_code}
except Exception as e:
    out["readback"] = {"error": str(e)[:150]}

json.dump(out, open("e2etest.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "readback"}, indent=1))
print("readback:", json.dumps(out.get("readback", {}))[:600])
