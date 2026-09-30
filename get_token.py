"""Get today's Upstox access token.

  python3 get_token.py          # manual: paste the redirected address into Terminal
  python3 get_token.py --auto   # opens the browser and captures the login by itself

Needs UPSTOX_API_KEY and UPSTOX_API_SECRET (environment or .env), and the redirect URL
registered on your Upstox app to match UPSTOX_REDIRECT_URI (default http://127.0.0.1:5000/).
Saves the token to token.txt (git-ignored). Never share this token with anyone.
"""
import os
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import envfile

envfile.load()
API_KEY = os.environ.get("UPSTOX_API_KEY", "")
API_SECRET = os.environ.get("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.environ.get("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")


def login_url():
    return "https://api.upstox.com/v2/login/authorization/dialog?" + urlencode(
        {"response_type": "code", "client_id": API_KEY, "redirect_uri": REDIRECT_URI})


def exchange(code):
    """Swap the one-time login code for an access token and save it. Returns the token."""
    r = requests.post("https://api.upstox.com/v2/login/authorization/token",
                      headers={"Accept": "application/json"},
                      data={"code": code, "client_id": API_KEY, "client_secret": API_SECRET,
                            "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"},
                      timeout=15)
    body = r.json()
    if "access_token" not in body:
        raise RuntimeError(f"Login failed: {body}")
    with open(TOKEN_FILE, "w") as f:
        f.write(body["access_token"])
    os.chmod(TOKEN_FILE, 0o600)
    return body["access_token"]


def read_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def token_is_valid(token):
    if not token:
        return False
    try:
        r = requests.get("https://api.upstox.com/v2/user/profile", timeout=15,
                         headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
        return r.status_code == 200 and r.json().get("status") == "success"
    except (requests.RequestException, ValueError):
        return False


def auto_login(wait_minutes=30, open_browser=True):
    """Open the login page and catch the redirect on 127.0.0.1. Returns the token or ''."""
    target = urlparse(REDIRECT_URI)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code = parse_qs(urlparse(self.path).query).get("code", [""])[0]
            if code:
                try:
                    result["token"] = exchange(code)
                    msg = "Logged in. Safe-Algo will trade on its own today. You can close this tab."
                except Exception as e:  # noqa: BLE001 - show any failure in the browser
                    msg = f"Login failed: {e}"
            else:
                msg = "Waiting for the Upstox login..."
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer((target.hostname, target.port or 80), Handler)
    server.timeout = 5
    if open_browser:
        webbrowser.open(login_url())
    deadline = time.time() + wait_minutes * 60
    try:
        while time.time() < deadline and "token" not in result:
            server.handle_request()
    finally:
        server.server_close()
    return result.get("token", "")


def main():
    if not API_KEY or not API_SECRET:
        sys.exit("Set UPSTOX_API_KEY and UPSTOX_API_SECRET first (see README).")
    if "--auto" in sys.argv:
        token = auto_login()
        sys.exit(0 if token else "No login received in time.")
    print("1) Open this link and log in to Upstox:\n\n   " + login_url() + "\n")
    print("2) After login the browser goes to a page that may not load. That's fine.")
    pasted = input("   Copy the FULL address from the browser bar and paste it here:\n   > ").strip()
    code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]
    try:
        exchange(code)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"\nSaved today's token to {TOKEN_FILE}. It expires around 3:30 AM tomorrow.")


if __name__ == "__main__":
    main()
