"""Instrument lookup and the price-data read used by the commodity bot."""
import gzip
import io
import json
from datetime import datetime

import requests

from market_hours import IST
from swing.indicators import atr, ema
from upstox_api import API, Candle, UpstoxError

FILES = {
    "MCX": "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz",
    "NSE": "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
}
NOT_COMMODITY = {"NSE_EQ", "NSE_FO", "NSE_INDEX", "NCD_FO", "BSE_EQ", "BSE_FO", "BSE_INDEX"}


def is_commodity(row, exchange):
    seg = row.get("segment", "")
    if exchange == "MCX":
        return seg == "MCX_FO"
    return (seg.startswith("NSE") or seg.startswith("NCO")) and seg not in NOT_COMMODITY


def load_rows(exchange, name):
    raw = requests.get(FILES[exchange], timeout=60).content
    rows = json.load(gzip.GzipFile(fileobj=io.BytesIO(raw)))
    return [r for r in rows if is_commodity(r, exchange)
            and r.get("trading_symbol", "").upper().split(" ")[0] == name.upper()]


def nearest_future(rows):
    futs = sorted((r for r in rows if r.get("instrument_type") == "FUT"), key=lambda r: r.get("expiry") or 0)
    return futs[0] if futs else None


def expiry_dt(row):
    e = row.get("expiry")
    return datetime.fromtimestamp(e / 1000, IST) if isinstance(e, (int, float)) else None


def pick_option(rows, side, futures_price, now, min_days):
    """At-the-money call (UP) or put (DOWN) on the nearest expiry at least `min_days` away."""
    kind = "CE" if side == "UP" else "PE"
    opts = [r for r in rows if r.get("instrument_type") == kind and expiry_dt(r)
            and (expiry_dt(r) - now).days >= min_days]
    if not opts:
        return None
    first = min(r["expiry"] for r in opts)
    same = [r for r in opts if r["expiry"] == first]
    return min(same, key=lambda r: abs((r.get("strike_price") or 0) - futures_price))


def units_per_lot(row):
    return max(float(row.get("lot_size") or 1), float(row.get("qty_multiplier") or 1))


def technicals(up, fut):
    """Daily trend/momentum/volatility and today's 15-minute action for a futures contract."""
    key = fut["instrument_key"]
    to = datetime.now(IST).date()
    frm = to.replace(year=to.year - 1)
    rows = up._req("GET", f"{API}/v3/historical-candle/{key}/days/1/{to.isoformat()}/{frm.isoformat()}")["candles"]
    daily = [Candle(c[0][:10], c[1], c[2], c[3], c[4], c[5]) for c in sorted(rows, key=lambda c: c[0])]
    try:
        intra = up._req("GET", f"{API}/v3/historical-candle/intraday/{key}/minutes/15")["candles"]
        intra = [Candle(c[0][11:16], c[1], c[2], c[3], c[4], c[5]) for c in sorted(intra, key=lambda c: c[0])]
    except UpstoxError:
        intra = []
    if len(daily) < 15:
        raise UpstoxError(f"only {len(daily)} daily candles for {fut['trading_symbol']}")
    price = up.ltp([key]).get(key) or (intra[-1].close if intra else daily[-1].close)
    closes = [c.close for c in daily] + ([] if daily[-1].date == to.isoformat() else [price])
    fast, slow = (20, 50) if len(closes) >= 60 else (10, 20) if len(closes) >= 25 else (5, 10)
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - 14, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - 14, len(closes))]
    t = {
        "contract": fut["trading_symbol"],
        "price": round(price, 2),
        f"avg_{fast}d": round(ema(closes, fast)[-1], 2),
        f"avg_{slow}d": round(ema(closes, slow)[-1], 2),
        "change_5d_pct": round((price / closes[-6] - 1) * 100, 2),
        "change_20d_pct": round((price / closes[-min(21, len(closes))] - 1) * 100, 2),
        "range_20d": [min(c.low for c in daily[-20:]), max(c.high for c in daily[-20:])],
        "rsi14": round(100 - 100 / (1 + sum(gains) / max(sum(losses), 1e-9)), 1),
        "atr14": round(atr(daily, 14)[-1], 2),
    }
    if intra:
        t["today"] = {"open": intra[0].open, "high": max(c.high for c in intra),
                      "low": min(c.low for c in intra),
                      "avg": round(sum(c.close for c in intra) / len(intra), 2),
                      "last_hour_15m": [c.close for c in intra[-4:]]}
    return t
