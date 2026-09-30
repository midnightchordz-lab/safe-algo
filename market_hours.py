"""NSE trading window used by every bot.

Orders are only placed during continuous trading and stop at ORDER_CUTOFF, leaving a safe
gap before the closing auction session (CAS) that starts around 3:15 PM.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 20)
ORDER_CUTOFF = time(15, 10)


def now_ist():
    return datetime.now(IST)


def can_place_orders(now=None):
    now = now or now_ist()
    return now.weekday() < 5 and OPEN <= now.time() <= ORDER_CUTOFF
