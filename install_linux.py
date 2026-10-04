"""Set Safe-Algo to run on its own on the Linux server (AWS), Monday to Friday, India time.

  .venv/bin/python install_linux.py               # long-term LIVE, swing PAPER, commodity PAPER
  .venv/bin/python install_linux.py --swing-live  # swing bot LIVE too
  .venv/bin/python install_linux.py --paper       # everything in practice mode
  .venv/bin/python install_linux.py --commodity-live --commodity-budget 32000 --commodity-max-loss 15000
  .venv/bin/python install_linux.py --test-email  # just send a test email
  .venv/bin/python install_linux.py --uninstall

Asks once for any key that isn't saved in .env yet (typing is hidden for secrets) and saves them
there (owner-only file). Alerts go by email through Gmail with an app password.

The daily Upstox login happens on the Mac (install_mac.py --server ...), which copies the token here.
Schedule (cron, server clock must be Asia/Kolkata):
  09:05  check today's token arrived from the Mac; emails a reminder and waits if not
  14:50  long-term bot   14:57  swing bot   18:25  commodity option bot
Only lines tagged "# safe-algo" in the crontab are touched, so other bots' lines stay.
"""
import getpass
import os
import subprocess
import sys

import envfile

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = "# safe-algo"
JOBS = [("login", 9, 5), ("trade", 14, 50), ("swing", 14, 57), ("commodity", 18, 25)]
SECRETS = {"UPSTOX_API_SECRET", "ANTHROPIC_API_KEY", "SMTP_PASSWORD"}
ASK = [("UPSTOX_API_KEY", "Upstox API key"), ("UPSTOX_API_SECRET", "Upstox API secret"),
       ("ANTHROPIC_API_KEY", "Anthropic API key (for the commodity bot)"),
       ("ANTHROPIC_WORKSPACE_ID", "Anthropic workspace ID (Enter to skip)"),
       ("SMTP_USER", "Gmail address that sends the alerts"),
       ("SMTP_PASSWORD", "Gmail app password (16 letters)"),
       ("NOTIFY_EMAIL", "Email that receives the alerts (Enter = same Gmail)")]


def python():
    venv = os.path.join(HERE, ".venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def cron_lines():
    py = python()
    return [f"{m} {h} * * 1-5 cd {HERE} && {py} autorun.py {job} >> autorun-{job}.log 2>&1 {TAG}"
            for job, h, m in JOBS]


def merged_crontab(existing, add=True):
    """Keep every line not tagged as ours; add our lines (or not, for --uninstall)."""
    keep = [line for line in existing.splitlines() if TAG not in line and line.strip()]
    return "\n".join(keep + (cron_lines() if add else [])) + "\n"


def read_crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else ""


def write_crontab(text):
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def ask_missing():
    for key, label in ASK:
        if os.environ.get(key):
            continue
        value = (getpass.getpass if key in SECRETS else input)(f"{label}: ").strip()
        if key == "NOTIFY_EMAIL" and not value:
            value = os.environ.get("SMTP_USER", "")
        os.environ[key] = value


def flag_value(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        sys.exit(f"{name} needs a value")
    return default


def timezone_ok():
    r = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], capture_output=True, text=True,
                       check=False)
    return r.stdout.strip() == "Asia/Kolkata"


def main():
    envfile.load()
    if "--uninstall" in sys.argv:
        write_crontab(merged_crontab(read_crontab(), add=False))
        print("Removed the Safe-Algo schedule. Nothing will run automatically now.")
        return
    ask_missing()
    import autorun
    if "--test-email" in sys.argv:
        ok = autorun.send_email("Test from your Safe-Algo server. Alerts will arrive like this.", "Safe-Algo")
        print("Test email sent. Check your inbox." if ok else "Email failed: check the Gmail app password.")
        return
    if not timezone_ok():
        sys.exit("Set the server clock to India time first: sudo timedatectl set-timezone Asia/Kolkata")
    paper = "--paper" in sys.argv
    commodity_live = "--commodity-live" in sys.argv and not paper
    budget = flag_value("--commodity-budget", "0")
    if commodity_live and float(budget) <= 0:
        sys.exit("--commodity-live needs --commodity-budget N (the max premium for its one lot).")
    values = {k: os.environ.get(k, "") for k, _ in ASK}
    values.update({"ALGO_LIVE": "0" if paper else "1",
                   "SWING_LIVE": "1" if "--swing-live" in sys.argv and not paper else "0",
                   "COMMODITY_LIVE": "1" if commodity_live else "0", "COMMODITY_MAX_PREMIUM": budget,
                   "COMMODITY_MAX_TOTAL_LOSS": flag_value("--commodity-max-loss", "15000")})
    envfile.save(values)
    write_crontab(merged_crontab(read_crontab()))
    print("Installed on this server.")
    print(f"  Long-term bot: {'PAPER' if paper else 'LIVE (real orders)'}")
    print(f"  Swing bot:     {'LIVE (real orders)' if values['SWING_LIVE'] == '1' else 'PAPER (practice)'}")
    print(f"  Commodity bot: {'LIVE, max premium ₹' + format(float(budget), ',.0f') if commodity_live else 'PAPER (practice)'}")
    print("  Mon-Fri 09:05 token check | 14:50 long-term | 14:57 swing | 18:25 commodity (India time)")
    print("  Alerts go to " + values["NOTIFY_EMAIL"])


if __name__ == "__main__":
    main()
