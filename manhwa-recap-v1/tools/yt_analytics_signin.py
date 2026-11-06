"""One-time owner sign-in for read-only YouTube Analytics (F3, 2026-11-06).

Run on your own computer:
    python3 manhwa-recap-v1/tools/yt_analytics_signin.py ~/Downloads/client_secret_XXX.json

- The JSON is a Google Cloud OAuth client of type "Desktop app". Keep it out of the repo.
- A browser opens. Sign in, pick the channel (Flamingo Recap), and allow
  "View YouTube Analytics reports". That is the only permission asked for.
- The grant is saved to ~/.config/flamingo/yt_analytics_<label>.json (owner-only
  permissions). It is never printed.
- Then set it on Railway (this restarts the service, so check the jobs bar first):
    cd ~/dev/manhwa && railway variables --set "YOUTUBE_ANALYTICS_AUTHORIZED_USER_JSON=$(cat ~/.config/flamingo/yt_analytics_flamingo.json)"
  The Scrapper app reads the same variable name and format.

Standard library only. Uses PKCE and a 127.0.0.1 redirect.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    label = sys.argv[2] if len(sys.argv) > 2 else "flamingo"
    c = json.load(open(os.path.expanduser(sys.argv[1])))
    c = c.get("installed") or c.get("web") or c
    cid, csec = c["client_id"], c["client_secret"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab and go back to the terminal.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening the Google sign-in page. If it doesn't open, paste this into the browser:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if got.get("error") or got.get("state") != state:
        sys.exit(f"Sign-in did not complete: {got.get('error') or 'state mismatch'}")
    body = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": csec,
                                   "redirect_uri": redirect, "grant_type": "authorization_code",
                                   "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN, data=body), timeout=30) as r:
        t = json.loads(r.read().decode())
    if not t.get("refresh_token"):
        sys.exit("Google returned no refresh token. Remove the app's access at "
                 "myaccount.google.com/permissions and run this again.")
    if SCOPE not in (t.get("scope") or "").split():
        sys.exit("The Analytics permission was not granted; nothing saved.")
    grant = {"type": "authorized_user", "client_id": cid, "client_secret": csec,
             "refresh_token": t["refresh_token"], "token_uri": TOKEN, "scopes": [SCOPE]}
    d = os.path.expanduser("~/.config/flamingo")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, f"yt_analytics_{label}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(grant, f)
    print(f"Saved the read-only grant to {path} (not printed). Next step is in this script's header.")


if __name__ == "__main__":
    main()
