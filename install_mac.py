"""Set Safe-Algo to run on its own on this Mac.

  python3 install_mac.py               # long-term bot LIVE, swing bot PAPER (practice)
  python3 install_mac.py --swing-live  # long-term bot LIVE, swing bot LIVE
  python3 install_mac.py --paper       # both in practice mode
  python3 install_mac.py --uninstall

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
        "com.safealgo.swing": ("swing", 14, 57)}
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


def install(live, swing_live):
    key = os.environ.get("UPSTOX_API_KEY", "")
    secret = os.environ.get("UPSTOX_API_SECRET", "")
    if not key or not secret or "paste-api" in key or "YOUR_API" in key:
        sys.exit("UPSTOX_API_KEY / UPSTOX_API_SECRET are not set in this Terminal. Fix that first.")
    envfile.save({"UPSTOX_API_KEY": key, "UPSTOX_API_SECRET": secret, "ALGO_LIVE": "1" if live else "0",
                  "SWING_LIVE": "1" if swing_live else "0"})

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

    print("Installed.")
    print(f"  Long-term bot: {'LIVE (real orders)' if live else 'PAPER (practice)'}")
    print(f"  Swing bot:     {'LIVE (real orders)' if swing_live else 'PAPER (practice)'}")
    print("  Mon-Fri 09:00  Upstox login page opens if needed. Just log in.")
    print("  Mon-Fri 14:50  long-term bot runs by itself")
    print("  Mon-Fri 14:57  swing bot runs by itself")
    print("  No orders after 15:10 (clear of the closing auction session)")
    print("You get a notification whenever either bot trades or needs you.")
    print("Keep the Mac on, plugged in and awake at those times.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This installer is for macOS.")
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        paper = "--paper" in sys.argv
        install(live=not paper, swing_live="--swing-live" in sys.argv and not paper)
