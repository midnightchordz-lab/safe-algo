"""Set Safe-Algo to run on its own on this Mac.

  python3 install_mac.py            # install, LIVE trading
  python3 install_mac.py --paper    # install, practice mode (no real orders)
  python3 install_mac.py --uninstall

Creates two scheduled jobs (launchd), Monday to Friday:
  09:00  opens the Upstox login page if today's token is missing
  15:05  runs the bot (asks for the login again first if you skipped the morning one)
Your Mac must be switched on, awake and online at those times.
"""
import os
import plistlib
import subprocess
import sys

import envfile

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
JOBS = {"com.safealgo.login": ("login", 9, 0), "com.safealgo.trade": ("trade", 15, 5)}
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


def install(live):
    key = os.environ.get("UPSTOX_API_KEY", "")
    secret = os.environ.get("UPSTOX_API_SECRET", "")
    if not key or not secret or "paste-api" in key or "YOUR_API" in key:
        sys.exit("UPSTOX_API_KEY / UPSTOX_API_SECRET are not set in this Terminal. Fix that first.")
    envfile.save({"UPSTOX_API_KEY": key, "UPSTOX_API_SECRET": secret, "ALGO_LIVE": "1" if live else "0"})

    os.makedirs(AGENTS, exist_ok=True)
    for label in OLD_JOBS:
        remove(label)
    for label, (job, hour, minute) in JOBS.items():
        remove(label)
        plist = {
            "Label": label,
            "ProgramArguments": [sys.executable, os.path.join(HERE, "autorun.py"), job],
            "WorkingDirectory": HERE,
            "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
            "StandardOutPath": os.path.join(HERE, f"autorun-{job}.log"),
            "StandardErrorPath": os.path.join(HERE, f"autorun-{job}.log"),
        }
        with open(plist_path(label), "wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["launchctl", "load", "-w", plist_path(label)], check=True)

    print(f"Installed. Mode: {'LIVE (real orders)' if live else 'PAPER (practice)'}")
    print("  Mon-Fri 09:00  Upstox login page opens if needed. Just log in.")
    print("  Mon-Fri 15:05  bot runs by itself; you get a notification if it trades or needs you.")
    print("Keep the Mac on, plugged in and awake at those times.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This installer is for macOS.")
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install(live="--paper" not in sys.argv)
