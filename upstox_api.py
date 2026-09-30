"""Minimal Upstox REST client (only the calls this bot needs).

Endpoints follow the official SDK (github.com/upstox/upstox-python):
  orders       -> https://api-hft.upstox.com/v3/order/place
  everything   -> https://api.upstox.com
"""
import gzip
import io
import json
from datetime import date, timedelta

import requests

API = "https://api.upstox.com"
ORDER_API = "https://api-hft.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class UpstoxError(RuntimeError):
    pass


class Upstox:
    def __init__(self, access_token, timeout=15):
        if not access_token:
            raise UpstoxError("UPSTOX_ACCESS_TOKEN is not set")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.timeout = timeout

    def _req(self, method, url, **kw):
        r = self.s.request(method, url, timeout=self.timeout, **kw)
        try:
            body = r.json()
        except ValueError:
            raise UpstoxError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400 or body.get("status") != "success":
            raise UpstoxError(f"{method} {url} -> HTTP {r.status_code}: {body}")
        return body["data"]

    # --- market data -----------------------------------------------------------
    def daily_closes(self, instrument_key, days=400, include_today=False):
        to = date.today()
        frm = to - timedelta(days=days)
        url = f"{API}/v3/historical-candle/{instrument_key}/days/1/{to.isoformat()}/{frm.isoformat()}"
        candles = self._req("GET", url)["candles"]
        # candles are [timestamp, open, high, low, close, volume, oi], newest first
        today = to.isoformat()
        rows = sorted(candles, key=lambda c: c[0])
        return [c[4] for c in rows if include_today or not c[0].startswith(today)]

    def ltp(self, instrument_keys):
        data = self._req("GET", f"{API}/v3/market-quote/ltp",
                         params={"instrument_key": ",".join(instrument_keys)})
        return {v["instrument_token"]: v["last_price"] for v in data.values()}

    # --- account ---------------------------------------------------------------
    def available_cash(self):
        data = self._req("GET", f"{API}/v2/user/get-funds-and-margin", params={"segment": "SEC"})
        return float(data["equity"]["available_margin"])

    def holdings(self):
        """{instrument_key: quantity} for delivery holdings (incl. T1 shares)."""
        out = {}
        for h in self._req("GET", f"{API}/v2/portfolio/long-term-holdings"):
            out[h["instrument_token"]] = int(h.get("quantity", 0)) + int(h.get("t1_quantity", 0) or 0)
        # Shares bought today show up as positions, not holdings.
        for p in self._req("GET", f"{API}/v2/portfolio/short-term-positions"):
            if p.get("product") == "D" and int(p.get("quantity", 0)) != 0:
                out[p["instrument_token"]] = out.get(p["instrument_token"], 0) + int(p["quantity"])
        return out

    # --- orders ----------------------------------------------------------------
    def place_order(self, instrument_key, side, qty, limit_price, tag="safe-algo"):
        assert side in ("BUY", "SELL") and qty > 0
        body = {
            "quantity": int(qty),
            "product": "D",            # delivery only: no intraday leverage
            "validity": "DAY",
            "price": round(limit_price, 2),
            "tag": tag,
            "instrument_token": instrument_key,
            "order_type": "LIMIT",
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": False,
        }
        data = self._req("POST", f"{ORDER_API}/v3/order/place", data=json.dumps(body))
        return data["order_ids"][0]

    def order_details(self, order_id):
        return self._req("GET", f"{API}/v2/order/details", params={"order_id": order_id})

    def cancel_order(self, order_id):
        return self._req("DELETE", f"{ORDER_API}/v3/order/cancel", params={"order_id": order_id})

    def execute(self, instrument_key, side, qty, limit_price, wait_s=30, poll_s=2):
        """Place a limit order, wait for it, cancel any unfilled remainder.

        Returns (filled_qty, average_price).
        """
        import time
        oid = self.place_order(instrument_key, side, qty, limit_price)
        deadline = time.time() + wait_s
        d = {}
        while time.time() < deadline:
            d = self.order_details(oid)
            if d.get("status") in ("complete", "rejected", "cancelled"):
                break
            time.sleep(poll_s)
        else:
            try:
                self.cancel_order(oid)
            except UpstoxError:
                pass  # may have filled in the meantime
            time.sleep(poll_s)
            d = self.order_details(oid)
        return int(d.get("filled_quantity") or 0), float(d.get("average_price") or 0.0)


def resolve_instrument_keys(symbols, overrides=None):
    """Map NSE trading symbols to Upstox instrument keys using the public instruments file."""
    overrides = overrides or {}
    missing = [s for s in symbols if s not in overrides]
    keys = dict(overrides)
    if missing:
        raw = requests.get(INSTRUMENTS_URL, timeout=30).content
        rows = json.load(gzip.GzipFile(fileobj=io.BytesIO(raw)))
        for row in rows:
            if row.get("segment") == "NSE_EQ" and row.get("trading_symbol") in missing:
                keys[row["trading_symbol"]] = row["instrument_key"]
    not_found = [s for s in symbols if s not in keys]
    if not_found:
        raise UpstoxError(f"Could not find instrument keys for {not_found}")
    return keys
