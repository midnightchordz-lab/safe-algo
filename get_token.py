"""Get today's Upstox access token (run once each morning before 3 PM).

  python get_token.py

Needs UPSTOX_API_KEY and UPSTOX_API_SECRET set, and the redirect URL registered on
your Upstox app to match UPSTOX_REDIRECT_URI (default http://127.0.0.1:5000/).
Saves the token to token.txt (git-ignored). Never share this token with anyone.
"""
import os
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests

API_KEY = os.environ.get("UPSTOX_API_KEY", "")
API_SECRET = os.environ.get("UPSTOX_API_SECRET", "")
REDIRECT_URI = os.environ.get("UPSTOX_REDIRECT_URI", "http://127.0.0.1:5000/")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")


def main():
    if not API_KEY or not API_SECRET:
        sys.exit("Set UPSTOX_API_KEY and UPSTOX_API_SECRET first (see README).")
    url = "https://api.upstox.com/v2/login/authorization/dialog?" + urlencode(
        {"response_type": "code", "client_id": API_KEY, "redirect_uri": REDIRECT_URI})
    print("1) Open this link and log in to Upstox:\n\n   " + url + "\n")
    print("2) After login the browser goes to a page that may not load. That's fine.")
    pasted = input("   Copy the FULL address from the browser bar and paste it here:\n   > ").strip()
    code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]

    r = requests.post("https://api.upstox.com/v2/login/authorization/token",
                      headers={"Accept": "application/json"},
                      data={"code": code, "client_id": API_KEY, "client_secret": API_SECRET,
                            "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code"},
                      timeout=15)
    body = r.json()
    if "access_token" not in body:
        sys.exit(f"Login failed: {body}")
    with open(TOKEN_FILE, "w") as f:
        f.write(body["access_token"])
    print(f"\nSaved today's token to {TOKEN_FILE}. It expires around 3:30 AM tomorrow.")


if __name__ == "__main__":
    main()
