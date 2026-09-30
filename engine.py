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
    if state["halted"]:
        return []
    prices = {s: c[-1] for s, c in closes.items()}
    eq = equity(state, prices)
    orders = []

    # 1) Kill switch: sell everything and stop.
    if floor_breached(eq, cfg):
        for s, p in state["positions"].items():
            orders.append({"symbol": s, "side": "SELL", "qty": p["qty"], "reason": "capital_floor"})
        orders.append({"symbol": None, "side": "HALT", "qty": 0,
                       "reason": f"equity {eq:.2f} < floor {cfg.capital_floor:.2f}"})
        return orders

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
