"""Pure decision logic shared by the backtester and the live bot.

State is a plain dict so it can be saved as JSON:
  {"cash": float, "positions": {sym: {"qty": int, "entry": float, "peak": float}},
   "halted": bool, "halt_reason": str}
"""
from risk import floor_breached, position_size, stop_risk
from strategy import entry_signal, exit_signal


def new_state(cfg):
    return {"cash": cfg.budget, "positions": {}, "halted": False, "halt_reason": ""}


def equity(state, prices):
    return state["cash"] + sum(p["qty"] * prices[s] for s, p in state["positions"].items())


def decide(state, closes, cfg):
    """closes: {symbol: [daily closes, oldest first]} (last element = current price).

    Returns a list of orders: {"symbol", "side", "qty", "reason"}. Sells come first.
    Does not mutate state (apply fills with `apply_fill`).
    """
    prices = {s: c[-1] for s, c in closes.items()}
    eq = equity(state, prices)
    orders = []

    # 0) Last-resort floor: applies even while halted/frozen.
    if eq < cfg.hard_floor and state["positions"]:
        for s, p in state["positions"].items():
            orders.append({"symbol": s, "side": "SELL", "qty": p["qty"], "reason": "hard_floor"})
        if not state["halted"]:
            orders.append({"symbol": None, "side": "HALT", "qty": 0,
                           "reason": f"equity {eq:.2f} < hard floor {cfg.hard_floor:.2f}"})
        return orders
    if state["halted"]:
        return []

    # 1) Kill switch: stop trading (and, if configured, sell everything first).
    if floor_breached(eq, cfg):
        if cfg.floor_action == "liquidate":
            for s, p in state["positions"].items():
                orders.append({"symbol": s, "side": "SELL", "qty": p["qty"], "reason": "capital_floor"})
        orders.append({"symbol": None, "side": "HALT", "qty": 0,
                       "reason": f"equity {eq:.2f} < floor {cfg.capital_floor:.2f}"})
        return orders

    if cfg.strategy == "rebalance":
        return _rebalance(state, prices, eq, cfg)

    # 2) Exits.
    exiting = set()
    for s, p in state["positions"].items():
        peak = max(p["peak"], prices[s])
        why = exit_signal(closes[s], peak, cfg)
        if why:
            orders.append({"symbol": s, "side": "SELL", "qty": p["qty"], "reason": why})
            exiting.add(s)

    # 3) Entries (using cash as it will be after the exits above).
    cash = state["cash"] + sum(
        state["positions"][s]["qty"] * prices[s] - _cost(state["positions"][s]["qty"] * prices[s], cfg)
        for s in exiting)
    open_risk = sum(stop_risk(p["qty"], prices[s], cfg)
                    for s, p in state["positions"].items() if s not in exiting)
    for s in cfg.symbols:
        if s in state["positions"] or s in exiting or s not in closes:
            continue
        if entry_signal(closes[s], cfg):
            qty = position_size(prices[s], cash, eq, cfg, open_risk)
            if qty > 0:
                orders.append({"symbol": s, "side": "BUY", "qty": qty, "reason": "trend_entry"})
                cash -= qty * prices[s] + _cost(qty * prices[s], cfg)
                open_risk += stop_risk(qty, prices[s], cfg)
    return orders[: cfg.max_orders_per_run + 1]


def _rebalance(state, prices, eq, cfg):
    """Equal-weight buy-and-hold. Trades only on the first run, when weights drift past
    `rebalance_band`, or when idle cash exceeds 10% of equity (e.g. after a partial fill)."""
    syms = [s for s in cfg.symbols if s in prices]
    buffer = cfg.budget * cfg.cash_buffer_pct
    target = (eq - buffer) / len(syms)
    held = {s: state["positions"].get(s, {"qty": 0})["qty"] * prices[s] for s in syms}
    drift = max(abs(held[s] / eq - 1 / len(syms) * (eq - buffer) / eq) for s in syms)
    idle_cash = state["cash"] - buffer > 0.10 * eq
    if not state["positions"] or drift > cfg.rebalance_band or idle_cash:
        reason = "initial_buy" if not state["positions"] else "rebalance"
    else:
        return []

    orders = []
    cash = state["cash"]
    for s in syms:  # sells first, to fund the buys
        excess = held[s] - target
        qty = int(excess // prices[s])
        if qty > 0 and qty * prices[s] >= cfg.min_order_value:
            orders.append({"symbol": s, "side": "SELL", "qty": qty, "reason": reason})
            cash += qty * prices[s] - _cost(qty * prices[s], cfg)
    for s in syms:
        want = target - held[s]
        spendable = cash - buffer
        value = min(want, spendable - _cost(min(want, spendable), cfg))
        qty = int(value // prices[s]) if value > 0 else 0
        if qty > 0 and qty * prices[s] >= cfg.min_order_value:
            orders.append({"symbol": s, "side": "BUY", "qty": qty, "reason": reason})
            cash -= qty * prices[s] + _cost(qty * prices[s], cfg)
    return orders


def _cost(value, cfg):
    return cfg.brokerage_per_order + value * cfg.other_charges_pct


def apply_fill(state, symbol, side, qty, price, cfg):
    """Update the ledger after a (possibly partial) fill."""
    if qty <= 0:
        return
    if side == "BUY":
        value = qty * price
        state["cash"] -= value + _cost(value, cfg)
        pos = state["positions"].setdefault(symbol, {"qty": 0, "entry": price, "peak": price})
        total = pos["qty"] + qty
        pos["entry"] = (pos["entry"] * pos["qty"] + price * qty) / total
        pos["qty"] = total
        pos["peak"] = max(pos["peak"], price)
    elif side == "SELL":
        pos = state["positions"][symbol]
        qty = min(qty, pos["qty"])  # never sell more than the ledger holds
        value = qty * price
        state["cash"] += value - _cost(value, cfg)
        pos["qty"] -= qty
        if pos["qty"] == 0:
            del state["positions"][symbol]


def update_peaks(state, prices):
    for s, p in state["positions"].items():
        p["peak"] = max(p["peak"], prices[s])


def halt(state, reason):
    state["halted"] = True
    state["halt_reason"] = reason
