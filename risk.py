"""Capital-protection rules. Everything that stops the account going broke is here.

Structural guarantees (cannot be switched off by config):
  * Long-only, delivery (CNC) product: no leverage, no margin, no short selling,
    no futures/options. The worst possible loss on a position is what was paid for it.
  * The bot only sells quantities it actually holds.
  * The bot never spends more than its own budget, even if the account has more cash.

Configurable guards:
  * Capital floor kill-switch: below `capital_floor` everything is sold and the bot halts.
  * Position size limited so that hitting the trailing stop costs <= max_risk_per_trade_pct.
  * Each symbol gets at most an equal share of the budget (no concentration).
"""
import math


def order_cost(value, cfg):
    return cfg.brokerage_per_order + value * cfg.other_charges_pct


def position_size(price, cash, equity, cfg, open_risk=0.0):
    """How many units to buy. Returns 0 if the trade isn't worth or safe to take.

    `open_risk` is the combined worst-case stop-out loss of positions already held.
    """
    if price <= 0:
        return 0
    per_symbol_cap = equity / len(cfg.symbols)
    risk_cap = (equity * cfg.max_risk_per_trade_pct) / cfg.trailing_stop_pct
    spendable = cash - cfg.budget * cfg.cash_buffer_pct
    alloc = min(per_symbol_cap, risk_cap, spendable)
    qty = math.floor(alloc / price)
    if qty <= 0:
        return 0
    # Don't take trades where round-trip charges eat more than 1.5% of the position.
    if 2 * order_cost(qty * price, cfg) > 0.015 * qty * price:
        return 0
    # Never let a new position push projected equity after a full stop-out below the floor.
    worst_case_loss = stop_risk(qty, price, cfg)
    if equity - open_risk - worst_case_loss < cfg.capital_floor:
        return 0
    return qty


def stop_risk(qty, price, cfg):
    """Loss if a position of qty@price is closed at its trailing stop, including charges."""
    value = qty * price
    return value * cfg.trailing_stop_pct + 2 * order_cost(value, cfg)


def floor_breached(equity, cfg):
    return equity < cfg.capital_floor
