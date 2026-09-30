"""Pure decision logic for the swing bot, shared by the backtest, paper and live modes.

State (JSON-friendly):
  {"cash", "positions": {sym: {"qty", "entry", "stop", "target", "entry_date", "bars", "gtt_id"}},
   "halted", "halt_reason", "loss_streak", "pause_left", "trades": [...]}
"""
import math

from swing.strategy import signal


def new_state(cfg):
    return {"cash": cfg.budget, "positions": {}, "halted": False, "halt_reason": "",
            "loss_streak": 0, "pause_left": 0, "trades": []}


def buy_cost(value, cfg):
    return cfg.brokerage_per_order + value * cfg.other_charges_pct


def sell_cost(value, cfg):
    return cfg.brokerage_per_order + cfg.dp_charge_per_sell + value * cfg.other_charges_pct


def equity(state, prices):
    return state["cash"] + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in state["positions"].items())


def check_exit(pos, candle, cfg):
    """How a position would exit during `candle` (a day after entry). Returns (price, reason) or None.

    Mirrors the broker-held GTT: stop and target trigger intraday. If the day gaps past a level,
    the fill is at the open. If both levels are touched the same day, assume the stop hit first.
    """
    if candle.open <= pos["stop"]:
        return candle.open, "stop_gap"
    if candle.low <= pos["stop"]:
        return pos["stop"], "stop"
    if candle.open >= pos["target"]:
        return candle.open, "target_gap"
    if candle.high >= pos["target"]:
        return pos["target"], "target"
    if pos["bars"] + 1 >= cfg.max_hold_days:
        return candle.close, "time"
    return None


def close_position(state, sym, price, reason, date, cfg):
    pos = state["positions"].pop(sym)
    value = pos["qty"] * price
    state["cash"] += value - sell_cost(value, cfg)
    risk = pos["qty"] * pos["risk_per_share"]
    pnl = value - sell_cost(value, cfg) - (pos["qty"] * pos["entry"] + buy_cost(pos["qty"] * pos["entry"], cfg))
    r = pnl / risk if risk else 0.0
    state["trades"].append({"symbol": sym, "entry_date": pos["entry_date"], "exit_date": date,
                            "qty": pos["qty"], "entry": round(pos["entry"], 2), "exit": round(price, 2),
                            "pnl": round(pnl, 2), "r": round(r, 2), "reason": reason})
    if pnl < 0:
        state["loss_streak"] += 1
        if state["loss_streak"] >= cfg.losing_streak_pause:
            state["pause_left"] = cfg.pause_days
            state["loss_streak"] = 0
    else:
        state["loss_streak"] = 0
    return pnl


def open_position(state, sym, qty, price, stop, target, date, cfg, gtt_id=None):
    value = qty * price
    state["cash"] -= value + buy_cost(value, cfg)
    # Keep the planned 3:1 distances relative to the actual fill price.
    state["positions"][sym] = {"qty": qty, "entry": price, "stop": stop, "target": target,
                               "risk_per_share": price - stop, "entry_date": date, "bars": 0,
                               "gtt_id": gtt_id}


def size(price, stop, cash, eq, open_risk, cfg):
    """Shares to buy, or 0 if any guardrail says no."""
    per_share = price - stop
    if per_share <= 0:
        return 0
    qty = math.floor(eq * cfg.risk_per_trade_pct / per_share)
    qty = min(qty, math.floor(eq * cfg.max_position_pct / price))
    qty = min(qty, math.floor((cash - cfg.cash_buffer) / (price * (1 + cfg.other_charges_pct))))
    if qty <= 0 or qty * price < cfg.min_order_value:
        return 0
    if open_risk + qty * per_share > eq * cfg.max_total_risk_pct:
        return 0
    round_trip = buy_cost(qty * price, cfg) + sell_cost(qty * price, cfg)
    if round_trip > cfg.max_cost_to_risk * qty * per_share:
        return 0
    return qty


def floor_action(state, eq, cfg):
    """'hard' (sell all, halt), 'soft' (no new entries) or None."""
    if eq < cfg.hard_floor:
        return "hard"
    if eq < cfg.soft_floor:
        return "soft"
    return None


def pick_entries(state, inds, idx, prices, eq, cfg):
    """New trades for today. inds: {sym: indicators}; idx: {sym: index of today's bar}.

    Returns [{"symbol", "qty", "price", "stop", "target"}], best momentum first.
    """
    if state["halted"] or state["pause_left"] > 0 or floor_action(state, eq, cfg):
        return []
    slots = min(cfg.max_open_positions - len(state["positions"]), cfg.max_new_per_day)
    if slots <= 0:
        return []
    candidates = []
    for sym, ind in inds.items():
        if sym in state["positions"] or sym not in idx:
            continue
        sig = signal(ind, idx[sym], cfg)
        if sig:
            candidates.append((sig[2], sym, sig[0], sig[1]))
    candidates.sort(reverse=True)

    cash = state["cash"]
    open_risk = sum(p["qty"] * max(p["entry"] - p["stop"], 0) for p in state["positions"].values())
    out = []
    for _, sym, stop, target in candidates:
        if len(out) >= slots:
            break
        price = prices[sym]
        qty = size(price, stop, cash, eq, open_risk, cfg)
        if qty:
            out.append({"symbol": sym, "qty": qty, "price": price, "stop": stop, "target": target})
            cash -= qty * price + buy_cost(qty * price, cfg)
            open_risk += qty * (price - stop)
    return out


def end_of_day(state):
    for p in state["positions"].values():
        p["bars"] += 1
    if state["pause_left"] > 0:
        state["pause_left"] -= 1


def stats(trades):
    if not trades:
        return {"trades": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {"trades": len(trades), "win_rate": len(wins) / len(trades),
            "avg_r": sum(t["r"] for t in trades) / len(trades),
            "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
            "targets": sum(t["reason"].startswith("target") for t in trades),
            "stops": sum(t["reason"].startswith("stop") for t in trades),
            "timeouts": sum(t["reason"] == "time" for t in trades)}
