# Safe-Algo: a capital-protected trend bot for Upstox

A small, cautious trading bot for a **₹10,000** Upstox account. The top priority is
**not losing the account**. Making money comes second.

> **Read this first: no algorithm can guarantee profit, or guarantee zero loss.**
> This bot is built so that it **structurally cannot go to ₹0**, and it stops itself
> after a controlled loss. You can still lose money, and in rare cases (an overnight
> crash) more than the floor. It is not investment advice. Paper-trade it first.

## How it avoids going broke

| Guard | What it does |
|---|---|
| **No leverage, ever** | Delivery (`product: D`) orders only. No intraday margin, no F&O, no short selling. The most you can lose on a position is what you paid for it. |
| **Own budget only** | Trades at most `ALGO_BUDGET` (₹10,000), even if your account holds more. |
| **Capital floor / kill switch** | If total value falls below `ALGO_CAPITAL_FLOOR` (₹8,500), it sells everything, writes a `HALTED` file and refuses to run until you delete it. |
| **Trailing stop** | Each position is sold if it falls 7% below its highest price since purchase. |
| **Risk-based sizing** | A stop-out may cost at most 3% of equity. A new trade is refused if, together with open positions, a full stop-out could breach the floor. |
| **Diversified, liquid ETFs only** | `NIFTYBEES` (Nifty 50) and `GOLDBEES` (gold). They tend to move differently. No penny stocks. |
| **Charge filter** | Skips trades where brokerage and taxes would eat more than 1.5% of the position. |
| **Trust the broker** | In live mode the ledger is checked against real holdings and cash. It never sells more than it holds. |
| **Paper mode by default** | Real orders only when `ALGO_LIVE=1`. |

## The strategy

Once a day, for each ETF:
- **Buy** when the price is above its 100-day average **and** higher than 20 days ago (an uptrend).
- **Sell** when the price drops below the 100-day average **or** hits the 7% trailing stop.

Otherwise the money sits in cash. This is plain trend-following. It won't beat the market in
strong bull runs. It aims to stay out of long crashes, which are what wipe out small accounts.

## Setup

1. Create an app at the Upstox developer console to get an API key and secret.
2. Generate an **access token** (the OAuth login flow). Upstox tokens expire every day,
   so you need a fresh one each morning.
3. Install and test:
   ```bash
   cd trading_algo
   pip install -r requirements.txt
   python -m unittest discover -s tests       # safety tests
   python backtest.py                         # synthetic stress tests (bull/bear/sideways/crash)
   export UPSTOX_ACCESS_TOKEN=...
   python backtest.py --upstox --days 2500    # backtest on real NIFTYBEES/GOLDBEES history
   ```

## Running it

Run it **once per trading day between about 3:00 and 3:15 PM IST**:

```bash
python bot.py                  # PAPER: real prices, simulated fills, no orders
ALGO_LIVE=1 python bot.py      # LIVE: real orders
```

State is kept in `state.json` and a log in `algo.log`. Example cron job (Mon–Fri, 3:05 PM IST):
```
5 15 * * 1-5  cd /path/to/trading_algo && UPSTOX_ACCESS_TOKEN=$(cat token.txt) python bot.py
```

**Recommended path:** paper-trade for at least 4–6 weeks, then go live. Don't place manual
trades in NIFTYBEES/GOLDBEES on the same account, because the bot can't tell them apart from its own.

## Settings (`config.py` or environment variables)

| Env var | Default | Meaning |
|---|---|---|
| `ALGO_BUDGET` | 10000 | Money the bot is allowed to use |
| `ALGO_CAPITAL_FLOOR` | 8500 | Kill-switch level (max ~15% drawdown) |
| `ALGO_LIVE` | 0 | `1` = real orders |
| `UPSTOX_ACCESS_TOKEN` | – | Today's Upstox token |

## Honest limitations

- **Gap risk:** stops are checked once a day at market prices. If the market crashes overnight,
  the sale happens at the lower price. The synthetic "crash" test (a 35% one-day gap in both ETFs
  at once) ends around ₹6,900: halted and below the floor, but far from zero.
- **Small-account costs:** at ₹10k, the ₹20-per-order brokerage is a noticeable drag (about 1% per round trip).
- **Whipsaws:** in choppy, sideways markets trend-following loses small amounts repeatedly.
  On random synthetic data it lost 10–13% in sideways/bear runs before the market recovered.
  That is the price of the protection. Check the real-data backtest before trusting it.
- **Endpoints** follow the official `upstox-python` SDK (v3 orders/candles/LTP). Verify with a
  paper run first. Upstox changes APIs from time to time.
