"""Trend-following signal on daily closes.

Rule, per ETF:
  * ENTER when close > SMA(trend_sma) AND close > close `momentum_days` ago.
  * EXIT  when close < SMA(trend_sma) OR close <= peak_since_entry * (1 - trailing_stop_pct).

The idea is simple on purpose: sit in the market while it trends up, sit in cash
when it doesn't. This avoids most of long bear markets, which is what destroys
small accounts.
"""


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def entry_signal(closes, cfg):
    if len(closes) < max(cfg.trend_sma, cfg.momentum_days + 1):
        return False
    avg = sma(closes, cfg.trend_sma)
    return closes[-1] > avg and closes[-1] > closes[-1 - cfg.momentum_days]


def exit_signal(closes, peak, cfg):
    """Returns a reason string if the position should be closed, else None."""
    price = closes[-1]
    if price <= peak * (1 - cfg.trailing_stop_pct):
        return "trailing_stop"
    avg = sma(closes, cfg.trend_sma)
    if avg is not None and price < avg:
        return "below_trend"
    return None
