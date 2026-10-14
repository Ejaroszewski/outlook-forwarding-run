"""Second delivery test: send the hello via the Outlook REST sendmail
endpoint (instead of SMTP), then check the target's Inbox AND Junk folder
via REST. Writes sendtest2.json.
"""
import csv, json, time, urllib.request, urllib.parse, urllib.error

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
CODE = "739184"
rows = list(csv.DictReader(open("accounts.csv", newline="", encoding="utf-8")))
tgt, snd = rows[31], rows[32]

def exchange(row, scope):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": row["refresh_token"],
        "client_id": row["client_id"], "scope": scope}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode())

out = {"target": tgt["email"], "sender": snd["email"], "code": CODE}
try:
    stok = exchange(snd, "https://outlook.office.com/Mail.Send offline_access")
    payload = {"Message": {
        "Subject": "Hello test two - code inside",
        "Body": {"ContentType": "Text", "Content": f"Hello again! Your verification code is {CODE}. Enter it to continue."},
        "ToRecipients": [{"EmailAddress": {"Address": tgt["email"]}}]},
        "SaveToSentItems": "true"}
    req = urllib.request.Request("https://outlook.office.com/api/v2.0/me/sendmail",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {stok['access_token']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        out["rest_send_status"] = resp.status
except urllib.error.HTTPError as e:
    out["rest_send_status"] = e.code
    try:
        out["rest_send_error"] = json.loads(e.read().decode()).get("error", {}).get("message", "")[:120]
    except Exception:
        out["rest_send_error"] = ""
except Exception as e:
    out["rest_send_status"] = None
    out["rest_send_error"] = str(e)[:120]

time.sleep(45)
ttok = exchange(tgt, "https://outlook.office.com/IMAP.AccessAsUser.All offline_access")
def folder_msgs(fid):
    url = (f"https://outlook.office.com/api/v2.0/me/mailfolders/{fid}/messages?$top=8"
           "&$orderby=ReceivedDateTime%20desc&$select=Subject,ReceivedDateTime,BodyPreview")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {ttok['access_token']}"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode()).get("value", [])
for fid in ("inbox", "junkemail"):
    try:
        ms = folder_msgs(fid)
        out[fid] = [{"subject": m.get("Subject", "")[:60],
                     "code_found": CODE in (m.get("Subject", "") + " " + m.get("BodyPreview", "")),
                     "hello2_found": "Hello test two" in m.get("Subject", "")} for m in ms]
    except Exception as e:
        out[fid] = {"error": str(e)[:100]}
json.dump(out, open("sendtest2.json", "w"), indent=1)
print(json.dumps(out)[:900])
