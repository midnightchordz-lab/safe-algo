"""Settings for the swing bot. Separate budget, state and logs from the long-term bot."""
import os
from dataclasses import dataclass, field

from swing.universe import NIFTY50

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else cast(raw)


@dataclass
class SwingConfig:
    # --- Money ---------------------------------------------------------------
    budget: float = _env("SWING_BUDGET", 25_000.0, float)
    cash_buffer: float = 500.0            # always kept aside for charges

    # --- Universe ------------------------------------------------------------
    symbols: tuple = NIFTY50

    # --- Entry rule ----------------------------------------------------------
    # "breakout": new 20-day closing high in an uptrend.
    # "pullback": price dips below its 20-day average in an uptrend, then closes back above it.
    entry: str = _env("SWING_ENTRY", "pullback")  # best of the 4 in the 2019-2026 backtest
    atr_stop_mult: float = _env("SWING_ATR_MULT", 1.5, float)  # stop = entry - 1.5 x ATR(14)
    reward_risk: float = 3.0              # target = entry + 3 x (entry - stop)
    max_hold_days: int = 20               # exit at market after 20 trading days if neither hit

    # --- Guardrails ----------------------------------------------------------
    risk_per_trade_pct: float = 0.01      # a stop-out loses at most 1% of equity (~₹250)
    max_open_positions: int = 3
    max_new_per_day: int = 2
    max_position_pct: float = 0.35        # no single stock above 35% of equity
    max_total_risk_pct: float = 0.03      # all open stop-outs together <= 3% of equity
    min_order_value: float = 3_000.0      # smaller trades lose too much to fixed charges
    max_cost_to_risk: float = 0.35        # skip trades whose round-trip charges exceed 0.35R
    losing_streak_pause: int = 4          # after 4 losses in a row...
    pause_days: int = 5                   # ...no new entries for 5 trading days
    soft_floor: float = _env("SWING_SOFT_FLOOR", 21_250.0, float)  # -15%: no new entries
    hard_floor: float = _env("SWING_HARD_FLOOR", 17_500.0, float)  # -30%: sell all and halt

    # --- Costs (approx. Upstox delivery) -------------------------------------
    brokerage_per_order: float = 20.0
    other_charges_pct: float = 0.0012     # STT, exchange, stamp, GST, per side
    dp_charge_per_sell: float = 20.0      # depository charge on each sell

    # --- Execution -----------------------------------------------------------
    live: bool = _env("SWING_LIVE", "0") == "1"
    access_token: str = _env("UPSTOX_ACCESS_TOKEN", "")
    instrument_keys: dict = field(default_factory=dict)
    state_file: str = _env("SWING_STATE_FILE", os.path.join(HERE, "swing_state.json"))
    halt_file: str = _env("SWING_HALT_FILE", os.path.join(HERE, "SWING_HALTED"))
    log_file: str = os.path.join(HERE, "swing.log")
