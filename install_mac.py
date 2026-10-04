"""Set Safe-Algo to run on its own on this Mac.

  python3 install_mac.py               # long-term bot LIVE, swing bot PAPER (practice)
  python3 install_mac.py --swing-live  # long-term bot LIVE, swing bot LIVE
  python3 install_mac.py --paper       # both in practice mode
  python3 install_mac.py --uninstall
  python3 install_mac.py --server ubuntu@65.0.244.16
      # the bots run on the server: this Mac only does the 9:00 login and sends the token there

Commodity option bot (AI + news, 6:25 PM): PAPER by default. To trade it live:
  python3 install_mac.py --commodity-live --commodity-budget 32000 --commodity-max-loss 15000
  (budget = max premium for its one lot; max-loss = total loss that halts it; needs ANTHROPIC_API_KEY)

Creates three scheduled jobs (launchd), Monday to Friday:
  09:00  opens the Upstox login page if today's token is missing
  14:50  runs the long-term bot (asks for the login again first if you skipped the morning one)
  14:57  runs the swing bot
No orders are placed after 15:10, clear of the closing auction session.
Your Mac must be switched on, awake and online at those times.
"""
import os
import plistlib
import subprocess
import sys

import envfile

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
JOBS = {"com.safealgo.login": ("login", 9, 0), "com.safealgo.trade": ("trade", 14, 50),
        "com.safealgo.swing": ("swing", 14, 57), "com.safealgo.commodity": ("commodity", 18, 25)}
OLD_JOBS = ["com.safealgo.morninglogin"]  # the earlier Terminal-window login reminder


def plist_path(label):
    return os.path.join(AGENTS, label + ".plist")


def remove(label):
    subprocess.run(["launchctl", "unload", "-w", plist_path(label)], capture_output=True, check=False)
    if os.path.exists(plist_path(label)):
        os.remove(plist_path(label))


def uninstall():
    for label in list(JOBS) + OLD_JOBS:
        remove(label)
    print("Removed the Safe-Algo schedule. Nothing will run automatically now.")


def flag_value(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        sys.exit(f"{name} needs a number")
    return default


def install_login_only(server):
    """Server mode: the Mac keeps only the 9:00 login, then copies the token to the server."""
    values = {k: os.environ.get(k, "") for k in ("UPSTOX_API_KEY", "UPSTOX_API_SECRET")}
    if not all(values.values()):
        sys.exit("UPSTOX_API_KEY / UPSTOX_API_SECRET are not in .env. Run the normal install first.")
    values.update(SAFEALGO_SERVER=server,
                  SAFEALGO_SSH_KEY=os.environ.get("SAFEALGO_SSH_KEY", "~/.ssh/safe-algo-mumbai.pem"))
    envfile.save(values)
    os.makedirs(AGENTS, exist_ok=True)
    for label in list(JOBS) + OLD_JOBS:
        remove(label)
    label, (job, hour, minute) = "com.safealgo.login", JOBS["com.safealgo.login"]
    plist = {"Label": label, "ProgramArguments": [sys.executable, os.path.join(HERE, "autorun.py"), job],
             "WorkingDirectory": HERE,
             "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
             "StandardOutPath": os.path.join(HERE, "autorun-login.log"),
             "StandardErrorPath": os.path.join(HERE, "autorun-login.log")}
    with open(plist_path(label), "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", "-w", plist_path(label)], check=True)
    print("Installed (server mode).")
    print("  Mon-Fri 09:00  Upstox login page opens on this Mac; after you log in, the token goes to")
    print(f"                 {server}. The bots no longer run on this Mac.")


def install(live, swing_live, commodity_live=False, commodity_budget="0", commodity_max_loss="15000"):
    key = os.environ.get("UPSTOX_API_KEY", "")
    secret = os.environ.get("UPSTOX_API_SECRET", "")
    if not key or not secret or "paste-api" in key or "YOUR_API" in key:
        sys.exit("UPSTOX_API_KEY / UPSTOX_API_SECRET are not set in this Terminal. Fix that first.")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if commodity_live and (not anthropic_key or float(commodity_budget) <= 0):
        sys.exit("--commodity-live needs ANTHROPIC_API_KEY set in this Terminal and --commodity-budget N.")
    envfile.save({"UPSTOX_API_KEY": key, "UPSTOX_API_SECRET": secret, "ALGO_LIVE": "1" if live else "0",
                  "SWING_LIVE": "1" if swing_live else "0", "ANTHROPIC_API_KEY": anthropic_key,
                  "ANTHROPIC_WORKSPACE_ID": os.environ.get("ANTHROPIC_WORKSPACE_ID", ""),
                  "COMMODITY_LIVE": "1" if commodity_live else "0", "COMMODITY_MAX_PREMIUM": commodity_budget,
                  "COMMODITY_MAX_TOTAL_LOSS": commodity_max_loss})

    os.makedirs(AGENTS, exist_ok=True)
    for label in OLD_JOBS:
        remove(label)
    for label, (job, hour, minute) in JOBS.items():
        remove(label)
        plist = {
            "Label": label,
            "ProgramArguments": (["/usr/bin/caffeinate", "-i"] if job == "commodity" else [])
            + [sys.executable, os.path.join(HERE, "autorun.py"), job],
            "WorkingDirectory": HERE,
            "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
            "StandardOutPath": os.path.join(HERE, f"autorun-{job}.log"),
            "StandardErrorPath": os.path.join(HERE, f"autorun-{job}.log"),
        }
        with open(plist_path(label), "wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["launchctl", "load", "-w", plist_path(label)], check=True)

    print("Installed.")
    print(f"  Long-term bot: {'LIVE (real orders)' if live else 'PAPER (practice)'}")
    print(f"  Swing bot:     {'LIVE (real orders)' if swing_live else 'PAPER (practice)'}")
    print("  Mon-Fri 09:00  Upstox login page opens if needed. Just log in.")
    print("  Mon-Fri 14:50  long-term bot runs by itself")
    print("  Mon-Fri 14:57  swing bot runs by itself")
    print("  No orders after 15:10 (clear of the closing auction session)")
    print(f"  Mon-Fri 18:25  commodity option bot: {'LIVE' if commodity_live else 'PAPER'}"
          + (f" (max premium ₹{float(commodity_budget):,.0f}, halts after ₹{float(commodity_max_loss):,.0f} total loss)"
             if commodity_live else "") + ("" if anthropic_key else "  [ANTHROPIC_API_KEY missing: it will skip]"))
    print("You get a notification whenever either bot trades or needs you.")
    print("Keep the Mac on, plugged in and awake at those times.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This installer is for macOS.")
    if "--uninstall" in sys.argv:
        uninstall()
    elif "--server" in sys.argv:
        envfile.load()
        install_login_only(flag_value("--server", ""))
    else:
        envfile.load()  # keep the saved keys, so re-installing doesn't need them typed into this Terminal
        paper = "--paper" in sys.argv
        install(live=not paper, swing_live="--swing-live" in sys.argv and not paper,
                commodity_live="--commodity-live" in sys.argv and not paper,
                commodity_budget=flag_value("--commodity-budget", "0"),
                commodity_max_loss=flag_value("--commodity-max-loss", "15000"))
