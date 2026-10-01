"""Hard rules for the commodity bot. The AI proposes; these decide. All pure functions."""


def new_state():
    return {"total_pnl": 0.0, "loss_streak": 0, "pause_left": 0, "last_trade_date": "",
            "last_run_date": "", "halted": False, "halt_reason": "", "trades": []}


def can_trade(state, today, cfg):
    if state["halted"]:
        return False, f"halted: {state['halt_reason']}"
    if state["total_pnl"] <= -cfg.max_total_loss:
        return False, f"total loss ₹{-state['total_pnl']:,.0f} reached the ₹{cfg.max_total_loss:,.0f} limit"
    if state["pause_left"] > 0:
        return False, f"cooling off after a losing streak ({state['pause_left']} day(s) left)"
    if state["last_trade_date"] == today:
        return False, "already traded today (max 1 per day)"
    return True, ""


def validate(d, price, atr, today_avg, cfg):
    """Check the AI's proposal against the hard rules. Returns (ok, reason)."""
    side = d.get("direction")
    if side not in ("UP", "DOWN"):
        return False, "AI says NO_TRADE"
    if d.get("confidence", 0) < cfg.min_confidence:
        return False, f"confidence {d.get('confidence')} < {cfg.min_confidence}"
    if d.get("event_risk"):
        return False, "AI flagged event risk tonight"
    entry, stop, target = d["entry_trigger"], d["stop_level"], d["target_level"]
    sign = 1 if side == "UP" else -1
    if not (sign * (target - entry) > 0 and sign * (entry - stop) > 0):
        return False, f"levels inconsistent for {side}: entry {entry}, stop {stop}, target {target}"
    if abs(entry - price) > 0.5 * atr:
        return False, f"entry {entry} is too far from price {price} (> 0.5 x daily move)"
    risk, reward = abs(entry - stop), abs(target - entry)
    lo, hi = cfg.stop_atr_range
    if not lo * atr <= risk <= hi * atr:
        return False, f"stop distance {risk:.0f} outside {lo}-{hi} x daily move ({atr:.0f})"
    if reward < cfg.min_reward_risk * risk:
        return False, f"reward {reward:.0f} < {cfg.min_reward_risk} x risk {risk:.0f}"
    if today_avg is not None and sign * (price - today_avg) < 0:
        return False, f"price data disagrees: price {'below' if side == 'UP' else 'above'} today's average"
    return True, "ok"


def triggered(side, price, level):
    return price >= level if side == "UP" else price <= level


def premium_levels(fill, plan, cfg, delta=0.5):
    """Option stop and target from the futures plan, assuming the option moves ~delta x futures."""
    stop = fill - delta * abs(plan["entry_trigger"] - plan["stop_level"])
    stop = max(stop, fill * (1 - cfg.max_premium_loss_pct), 0.05)
    target = fill + delta * abs(plan["target_level"] - plan["entry_trigger"])
    return round(stop, 2), round(target, 2)


def exit_reason(side, fut_price, opt_price, plan, prem_stop, prem_target, hhmm, cfg):
    if hhmm >= cfg.exit_by:
        return "time"
    if triggered(side, fut_price, plan["target_level"]) or opt_price >= prem_target:
        return "target"
    if (fut_price <= plan["stop_level"] if side == "UP" else fut_price >= plan["stop_level"]) \
            or opt_price <= prem_stop:
        return "stop"
    return None


def record(state, today, pnl, trade, cfg):
    state["total_pnl"] = round(state["total_pnl"] + pnl, 2)
    state["trades"].append(dict(trade, pnl=round(pnl, 2), date=today))
    if pnl < 0:
        state["loss_streak"] += 1
        if state["loss_streak"] >= cfg.losing_streak_pause:
            state["pause_left"], state["loss_streak"] = cfg.pause_days, 0
    else:
        state["loss_streak"] = 0
    if state["total_pnl"] <= -cfg.max_total_loss:
        state["halted"] = True
        state["halt_reason"] = f"total loss ₹{-state['total_pnl']:,.0f} hit the limit"


def new_day(state, today):
    """Called once per weekday run: counts down a cooling-off pause."""
    if state["last_run_date"] != today:
        if state["pause_left"] > 0:
            state["pause_left"] -= 1
        state["last_run_date"] = today
