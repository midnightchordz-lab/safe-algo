"""Entry rules. Each returns (stop, target, score) for bar i, or None.

Both rules only buy stocks in an uptrend (price above the 200-day average, 50-day above
200-day). The stop sits 1.5 x ATR below the entry; the target is 3x that distance above,
so every trade risks 1 to make 3.
"""


def _uptrend(ind, i):
    c, s50, s200 = ind["close"][i], ind["sma50"][i], ind["sma200"][i]
    return None not in (s50, s200, ind["atr14"][i], ind["roc126"][i]) and c > s200 and s50 > s200


def _levels(ind, i, cfg):
    price, a = ind["close"][i], ind["atr14"][i]
    if not 0.01 <= a / price <= 0.05:  # skip stocks that barely move or swing wildly
        return None
    stop = price - cfg.atr_stop_mult * a
    target = price + cfg.reward_risk * (price - stop)
    return stop, target, ind["roc126"][i]


def breakout(ind, i, cfg):
    if not _uptrend(ind, i) or ind["high20"][i] is None:
        return None
    if ind["close"][i] <= ind["high20"][i]:
        return None
    return _levels(ind, i, cfg)


def pullback(ind, i, cfg):
    if not _uptrend(ind, i) or i < 1 or ind["ema20"][i - 1] is None:
        return None
    reclaimed = ind["close"][i - 1] < ind["ema20"][i - 1] and ind["close"][i] > ind["ema20"][i]
    if not reclaimed or ind["close"][i] < ind["sma50"][i]:
        return None
    return _levels(ind, i, cfg)


RULES = {"breakout": breakout, "pullback": pullback}


def signal(ind, i, cfg):
    return RULES[cfg.entry](ind, i, cfg)
