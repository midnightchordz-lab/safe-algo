"""Settings for the commodity bot. Hard limits are enforced in code and can't be raised past their caps."""
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else cast(raw)


def env(name, default, cast=str):
    """Read the environment when the config is created (not when this file is imported)."""
    return field(default_factory=lambda: _env(name, default, cast))


@dataclass
class CommodityConfig:
    live: bool = field(default_factory=lambda: _env("COMMODITY_LIVE", "0") == "1")
    underlying: str = env("COMMODITY_UNDERLYING", "CRUDEOIL")
    # Where orders go. Upstox has MCX API orders disabled, so NSE commodities (NCO) by default.
    trade_exchange: str = env("COMMODITY_EXCHANGE", "NSE")
    # Where the long price history for the analysis comes from (reading MCX data still works).
    data_exchange: str = env("COMMODITY_DATA_EXCHANGE", "MCX")

    # --- Money (you set these; the bot refuses to trade live without a premium cap) ---
    max_premium: float = env("COMMODITY_MAX_PREMIUM", 0.0, float)      # max cost of the one lot
    max_total_loss: float = env("COMMODITY_MAX_TOTAL_LOSS", 15_000.0, float)  # halt below this
    lots: int = 1                          # hard-coded: one lot per trade

    # --- Decision rules applied on top of the AI ---
    min_confidence: int = env("COMMODITY_MIN_CONFIDENCE", 65, int)  # clamped to >= 60 in __post_init__
    min_reward_risk: float = 1.5           # target at least 1.5x the stop distance (futures points)
    stop_atr_range: tuple = (0.25, 1.5)    # stop distance must be 0.25-1.5x the typical daily move
    max_premium_loss_pct: float = 0.40     # option stop never more than 40% below the fill
    min_days_to_expiry: int = 5
    max_spread_pct: float = 0.03           # skip contracts whose bid/ask gap is over 3% of the price

    # --- Guardrails ---
    max_trades_per_day: int = 1
    losing_streak_pause: int = 3           # after 3 losing trades in a row...
    pause_days: int = 5                    # ...no trades for 5 weekdays

    # --- Timing (IST) ---
    entry_window: tuple = ("18:30", "21:30")   # wait for the entry level only in this window
    exit_by: str = "22:45"                     # always flat by this time (intraday)
    poll_seconds: int = 30

    # --- AI ---
    model: str = "claude-opus-5-5"
    effort: str = env("COMMODITY_AI_EFFORT", "high")

    state_file: str = os.path.join(HERE, "commodity_state.json")
    decisions_file: str = os.path.join(HERE, "commodity_decisions.jsonl")
    log_file: str = os.path.join(HERE, "commodity.log")
    halt_file: str = os.path.join(HERE, "COMMODITY_HALTED")

    def __post_init__(self):
        self.min_confidence = max(60, int(self.min_confidence))
        self.lots = 1
