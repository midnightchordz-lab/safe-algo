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

STAGES = ("no data", "not in uptrend", "no dip", "still below 20-day", "below 50-day", "too quiet/wild", "SIGNAL")


def diagnose(ind, i, cfg):
    """How far a stock got through the pullback screen on bar i (one of STAGES). Logging only."""
    if not _uptrend(ind, i) or i < 1 or ind["ema20"][i - 1] is None or ind["ema20"][i] is None:
        return "not in uptrend" if ind["sma200"][i] is not None else "no data"
    if ind["close"][i - 1] >= ind["ema20"][i - 1]:
        return "no dip"
    if ind["close"][i] <= ind["ema20"][i]:
        return "still below 20-day"
    if ind["close"][i] < ind["sma50"][i]:
        return "below 50-day"
    return "SIGNAL" if _levels(ind, i, cfg) else "too quiet/wild"


def screen_summary(inds, idx, cfg):
    """One line for the log: how many stocks reached each stage today, and the ones to watch
    (in an uptrend and currently below the 20-day, so a close back above would be a signal)."""
    from collections import Counter
    stage = {s: diagnose(inds[s], i, cfg) for s, i in idx.items()}
    n = Counter(stage.values())
    up = len(stage) - n["not in uptrend"] - n["no data"]
    watch = sorted(s for s, i in idx.items()
                   if stage[s] in ("no dip", "still below 20-day")
                   and inds[s]["close"][i] < inds[s]["ema20"][i])
    line = (f"Screen ({cfg.entry} rule shown for pullback): {len(stage)} stocks | {up} in uptrend | "
            f"{n['still below 20-day'] + n['below 50-day'] + n['too quiet/wild'] + n['SIGNAL']} dipped below "
            f"the 20-day yesterday | {n['below 50-day'] + n['too quiet/wild'] + n['SIGNAL']} closed back above | "
            f"{n['SIGNAL']} signal(s)")
    return line, watch


def signal(ind, i, cfg):
    return RULES[cfg.entry](ind, i, cfg)
