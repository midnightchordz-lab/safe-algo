"""Entry point for the scheduled jobs (see install_mac.py).

  python3 autorun.py login   # 9:00 AM: make sure there's a valid token for today
                             # (on the Mac with SAFEALGO_SERVER set: then copy it to the server)
  python3 autorun.py trade   # 2:50 PM: log in if still needed, then run the long-term bot
  python3 autorun.py swing   # 2:57 PM: run the swing bot (uses the token from the jobs above)
  python3 autorun.py commodity  # 6:25 PM: AI + news commodity option bot (runs until ~10:45 PM)

No orders after 3:10 PM, so nothing lands in the closing auction session (from ~3:15 PM).

Sends a macOS notification (on the Mac) and/or an email (if SMTP_USER / SMTP_PASSWORD are set,
e.g. on the server) for anything that needs your attention.
"""
import logging
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage

import envfile

envfile.load()
import get_token  # noqa: E402  (needs the .env values loaded first)
from market_hours import IST, can_place_orders  # noqa: E402


def send_ses(message, title="Safe-Algo"):
    """Email the alert through Amazon SES (on the AWS server; uses the server's IAM role, no password)."""
    to = os.environ.get("NOTIFY_EMAIL", "")
    try:
        import boto3
        boto3.client("sesv2", region_name=os.environ.get("SES_REGION", "ap-south-1")).send_email(
            FromEmailAddress=to, Destination={"ToAddresses": [to]},
            Content={"Simple": {"Subject": {"Data": f"{title}: {message[:70]}"},
                                "Body": {"Text": {"Data": message}}}})
        return True
    except Exception as e:  # noqa: BLE001 - an alert failure must never stop a bot
        print(f"[notify] SES email failed: {e.__class__.__name__} {e}", flush=True)
        return False


def send_email(message, title="Safe-Algo"):
    """Email the alert: Amazon SES if ALERT_VIA=ses (server), else Gmail with an app password (Mac).
    Silent if neither is configured."""
    if os.environ.get("ALERT_VIA") == "ses" and os.environ.get("NOTIFY_EMAIL"):
        return send_ses(message, title)
    user, password = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    if not user or not password:
        return False
    mail = EmailMessage()
    mail["From"], mail["To"] = user, os.environ.get("NOTIFY_EMAIL") or user
    mail["Subject"] = f"{title}: {message[:70]}"
    mail.set_content(message)
    host, errors = os.environ.get("SMTP_HOST", "smtp.gmail.com"), []
    for port in (465, 587):  # SSL first, then STARTTLS if the first is cut off
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                smtp = smtplib.SMTP(host, port, timeout=30)
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            with smtp as session:
                session.login(user, password.replace(" ", ""))
                session.send_message(mail)
            return True
        except smtplib.SMTPAuthenticationError as e:
            print(f"[notify] email login refused (check the Gmail app password): {e.smtp_code}", flush=True)
            return False
        except (OSError, smtplib.SMTPException) as e:
            errors.append(f"port {port}: {e.__class__.__name__} {e}")
    print("[notify] email failed: " + " | ".join(errors), flush=True)
    return False


def notify(message, title="Safe-Algo"):
    print(f"{datetime.now(IST):%Y-%m-%d %H:%M} [notify] {message}", flush=True)
    send_email(message, title)
    if sys.platform == "darwin":
        safe = message.replace('"', "'").replace("\\", "/")
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe}" with title "{title}" sound name "Glass"'],
                       check=False)


def market_open(now=None):
    return can_place_orders(now)


def push_token():
    """Mac side of the server setup: copy today's token to the server over SSH. True if copied."""
    server = os.environ.get("SAFEALGO_SERVER", "")
    if not server:
        return False
    key = os.path.expanduser(os.environ.get("SAFEALGO_SSH_KEY", "~/.ssh/safe-algo-mumbai.pem"))
    r = subprocess.run(["scp", "-q", "-p", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                        get_token.TOKEN_FILE, f"{server}:safe-algo/token.txt"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        notify(f"Logged in, but could not send the token to the server: {r.stderr.strip()[:150]}. "
               "Run `python3 autorun.py login` on the Mac again.")
        return False
    notify("Upstox login sent to the server. The bots will run there today; the Mac can sleep.")
    return True


def wait_for_pushed_token(wait_minutes):
    """Server side: no browser here, so wait for the Mac to send today's token."""
    import time
    notify("No Upstox login yet today. On your Mac, log in to Upstox (or run `python3 autorun.py login` "
           f"in ~/safe-algo). I'll wait up to {wait_minutes} minutes.", "Safe-Algo login")
    deadline = time.time() + wait_minutes * 60
    while time.time() < deadline:
        time.sleep(60)
        token = get_token.read_token()
        if get_token.token_is_valid(token):
            return token
    return ""


def ensure_token(wait_minutes):
    token = get_token.read_token()
    if get_token.token_is_valid(token):
        return token
    if sys.platform != "darwin":
        return wait_for_pushed_token(wait_minutes)
    notify("Please log in to Upstox (a browser tab just opened) so Safe-Algo can run today.")
    try:
        token = get_token.auto_login(wait_minutes)
    except OSError as e:  # e.g. another login is already waiting on port 5000
        notify(f"Could not start the login helper: {e}")
        return ""
    if token:
        notify("Upstox login OK. The bots will run by themselves this afternoon.")
    return token


class Collector(logging.Handler):
    """Keeps the log lines worth a notification: trades, halts and errors."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        msg = record.getMessage()
        if (record.levelno >= logging.WARNING or " BUY " in msg or " SELL " in msg
                or " EXIT " in msg or "filled" in msg):
            self.lines.append(msg)


def run_swing():
    """2:57 PM job. Doesn't open its own login (the 9:00/2:50 jobs do); waits briefly for one."""
    import time
    if not market_open():
        print("Market is closed right now; skipping the swing run.", flush=True)
        return 0
    token = get_token.read_token()
    for _ in range(12):
        if get_token.token_is_valid(token) or not market_open():
            break
        time.sleep(60)
        token = get_token.read_token()
    if not get_token.token_is_valid(token):
        notify("Swing bot skipped today: no Upstox login. Its stops and targets stay active at Upstox.")
        return 1
    if not market_open():
        return 0
    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    import swing.bot as swing_bot

    collector = Collector()
    logging.getLogger("swing").addHandler(collector)
    try:
        rc = swing_bot.main()
    except Exception as e:  # noqa: BLE001 - any crash must reach the user
        logging.getLogger("swing").exception("Swing bot crashed")
        notify(f"Swing bot error: {e}. Existing stops/targets stay active. See swing.log.", "Safe-Algo Swing")
        return 1
    if collector.lines:
        notify(" | ".join(collector.lines)[:230], "Safe-Algo Swing")
    return rc


def run_commodity():
    """6:25 PM job: the commodity option bot. Uses today's token; asks for a login if missing."""
    if datetime.now(IST).weekday() > 4:
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        notify("Commodity bot skipped: ANTHROPIC_API_KEY is not set (re-run install_mac.py).", "Safe-Algo Commodity")
        return 1
    token = ensure_token(wait_minutes=15)
    if not token:
        notify("Commodity bot skipped tonight: no Upstox login.", "Safe-Algo Commodity")
        return 1
    os.environ["UPSTOX_ACCESS_TOKEN"] = token
    import commodity.bot as commodity_bot

    try:
        return commodity_bot.main(notify=lambda m: notify(m, "Safe-Algo Commodity"))
    except Exception as e:  # noqa: BLE001 - any crash must reach the user
        logging.getLogger("commodity").exception("Commodity bot crashed")
        notify(f"Commodity bot error: {e}. Check the Upstox app for any open position. See commodity.log.",
               "Safe-Algo Commodity")
        return 1


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "trade"
    if job == "swing":
        return run_swing()
    if job == "commodity":
        return run_commodity()
    if job == "login":
        if not ensure_token(wait_minutes=120):
            notify("No Upstox login this morning. I'll ask again at 2:50 PM.")
        elif sys.platform == "darwin":
            push_token()
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
