"""Indicators computed once per symbol over the whole candle history (lists aligned by index)."""


def sma(values, n):
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values, n):
    out, k, prev = [None] * len(values), 2 / (n + 1), None
    for i, v in enumerate(values):
        if i == n - 1:
            prev = sum(values[:n]) / n
        elif i >= n:
            prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def atr(candles, n=14):
    trs = []
    for i, c in enumerate(candles):
        prev_close = candles[i - 1].close if i else c.close
        trs.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    out, prev = [None] * len(candles), None
    for i, tr in enumerate(trs):  # Wilder's smoothing
        if i == n - 1:
            prev = sum(trs[:n]) / n
        elif i >= n:
            prev = (prev * (n - 1) + tr) / n
        out[i] = prev
    return out


def prior_high(values, n):
    """Highest value of the n bars before i (excluding i)."""
    return [max(values[i - n:i]) if i >= n else None for i in range(len(values))]


def roc(values, n):
    return [values[i] / values[i - n] - 1 if i >= n else None for i in range(len(values))]


def compute(candles):
    closes = [c.close for c in candles]
    return {
        "close": closes,
        "low": [c.low for c in candles],
        "sma50": sma(closes, 50),
        "sma200": sma(closes, 200),
        "ema20": ema(closes, 20),
        "atr14": atr(candles, 14),
        "high20": prior_high(closes, 20),
        "roc126": roc(closes, 126),
    }
