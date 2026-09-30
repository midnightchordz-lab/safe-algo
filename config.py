"""All tunable settings live here. Values can be overridden with environment variables."""
import os
from dataclasses import dataclass, field


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else cast(raw)


@dataclass
class Config:
    # --- Money ---------------------------------------------------------------
    # The bot only ever trades this much, even if the Upstox account holds more.
    budget: float = _env("ALGO_BUDGET", 10_000.0, float)
    # Hard floor. If total value (cash + holdings) drops below this, the bot sells
    # everything and permanently halts until a human deletes the halt file.
    capital_floor: float = _env("ALGO_CAPITAL_FLOOR", 8_500.0, float)
    # Always keep this fraction of the budget in cash for charges / slippage.
    cash_buffer_pct: float = 0.05

    # --- Universe ------------------------------------------------------------
    # Low-cost, highly liquid ETFs only. No single stocks, no F&O, no leverage.
    symbols: tuple = ("NIFTYBEES", "GOLDBEES")
    # Optional manual overrides: {"NIFTYBEES": "NSE_EQ|INF204KB14I2"}
    instrument_keys: dict = field(default_factory=dict)

    # --- Strategy (daily candles) -------------------------------------------
    trend_sma: int = 100          # only hold while price is above this moving average
    momentum_days: int = 20       # ...and has risen over the last N days
    trailing_stop_pct: float = 0.07  # exit if price falls 7% below its peak since entry
    max_risk_per_trade_pct: float = 0.03  # a stop-out may cost at most 3% of equity

    # --- Costs used by the backtest & sizing (approx. Upstox delivery) -------
    brokerage_per_order: float = 20.0
    other_charges_pct: float = 0.0012  # STT + exchange + stamp + GST, roughly, per side

    # --- Execution -----------------------------------------------------------
    live: bool = _env("ALGO_LIVE", "0") == "1"
    access_token: str = _env("UPSTOX_ACCESS_TOKEN", "")
    limit_slippage_pct: float = 0.005  # limit orders at LTP +/- 0.5%
    max_orders_per_run: int = 4

    state_file: str = _env("ALGO_STATE_FILE", os.path.join(os.path.dirname(__file__), "state.json"))
    halt_file: str = _env("ALGO_HALT_FILE", os.path.join(os.path.dirname(__file__), "HALTED"))
