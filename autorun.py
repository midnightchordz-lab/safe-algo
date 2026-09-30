"""Entry point for the scheduled jobs (see install_mac.py).

  python3 autorun.py login   # 9:00 AM: make sure there's a valid token for today
  python3 autorun.py trade   # 3:05 PM: log in if still needed, then run the bot

Sends a macOS notification for anything that needs your attention.
"""
import logging
import os
import subprocess
import sys
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import envfile

envfile.load()
import get_token  # noqa: E402  (needs the .env values loaded first)

IST = ZoneInfo("Asia/Kolkata")


def notify(message, title="Safe-Algo"):
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M} [notify] {message}", flush=True)
    if sys.platform == "darwin":
        safe = message.replace('"', "'").replace("\\", "/")
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe}" with title "{title}" sound name "Glass"'],
                       check=False)


def market_open(now=None):
    now = now or datetime.now(IST)
    return now.weekday() < 5 and dtime(9, 20) <= now.time() <= dtime(15, 25)


def ensure_token(wait_minutes):
    token = get_token.read_token()
    if get_token.token_is_valid(token):
        return token
    notify("Please log in to Upstox (a browser tab just opened) so Safe-Algo can run today.")
    try:
        token = get_token.auto_login(wait_minutes)
    except OSError as e:  # e.g. another login is already waiting on port 5000
        notify(f"Could not start the login helper: {e}")
        return ""
    if token:
        notify("Upstox login OK. The bot will run by itself at 3:05 PM.")
    return token


class Collector(logging.Handler):
    """Keeps the log lines worth a notification: trades, halts and errors."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        msg = record.getMessage()
        if record.levelno >= logging.WARNING or " BUY " in msg or " SELL " in msg or "filled" in msg:
            self.lines.append(msg)


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "trade"
    if job == "login":
        if not ensure_token(wait_minutes=120):
            notify("No Upstox login this morning. I'll ask again at 3:05 PM.")
        return 0

    if not market_open():
        print("Market is closed right now; skipping this run.", flush=True)
        return 0
    token = ensure_token(wait_minutes=15)
    if not token:
        notify("No Upstox login today, so the bot did not run. Nothing was traded.")
        return 1
    if not market_open():
        notify("Logged in too late (market closing). The bot will run tomorrow.")
        return 0

    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    import bot  # imported late so Config picks up the environment above

    collector = Collector()
    logging.getLogger("algo").addHandler(collector)
    try:
        rc = bot.main()
    except Exception as e:  # noqa: BLE001 - any crash must reach the user
        logging.getLogger("algo").exception("Bot crashed")
        notify(f"Bot error: {e}. Nothing further was traded. See algo.log.")
        return 1
    if collector.lines:
        notify(" | ".join(collector.lines)[:230])
    return rc


if __name__ == "__main__":
    sys.exit(main())
