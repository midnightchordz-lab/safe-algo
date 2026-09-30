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
| **Soft floor (₹8,500)** | Below `ALGO_CAPITAL_FLOOR` the bot stops all trading but keeps your holdings, so it doesn't sell at the bottom of a crash. It writes a `HALTED` file and waits for you to decide (`python3 bot.py --resume`). |
| **Hard floor (₹7,000)** | Even while frozen, if value keeps falling below `ALGO_HARD_FLOOR` it sells everything. This stops a long, slow bear market from grinding the account toward zero. |
| **Trend strategy only** | Optional mode: 7% trailing stop per position; a stop-out costs at most 3% of equity. |
| **Diversified, liquid ETFs only** | `NIFTYBEES` (Nifty 50) and `GOLDBEES` (gold). They tend to move differently. No penny stocks. |
| **Charge filter** | Skips trades where brokerage and taxes would eat more than 1.5% of the position. |
| **Trust the broker** | In live mode the ledger is checked against real holdings and cash. It never sells more than it holds. |
| **Paper mode by default** | Real orders only when `ALGO_LIVE=1`. |

## The strategy (default: `rebalance`)

Buy NIFTYBEES and GOLDBEES 50/50 and hold them. When one grows past about 60% of the
portfolio, sell a little of it and buy the other to get back to 50/50. That means very few
trades, and the ₹20 brokerage stays small. Shares and gold tend to move differently, which
cushions crashes.

On about 6.8 years of real prices (2019–2026, including the March 2020 crash), ₹10,000 became
**₹27,094**. The lowest point was ₹9,642 and the worst drop was 18.3%. The older trend strategy
(`ALGO_STRATEGY=trend`) made only ₹15,865 over the same period, with a similar lowest point.

## Setup

1. Create an app at the Upstox developer console to get an API key and secret.
2. Each morning, run `python get_token.py` (needs `UPSTOX_API_KEY` / `UPSTOX_API_SECRET`)
   to log in and save the day's access token to `token.txt`. Upstox tokens expire every day.
3. Install and test:
   ```bash
   cd trading_algo
   pip install -r requirements.txt
   python -m unittest discover -s tests       # safety tests
   python backtest.py                         # synthetic stress tests (bull/bear/sideways/crash)
   export UPSTOX_ACCESS_TOKEN=$(cat token.txt)
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
| `ALGO_STRATEGY` | rebalance | `rebalance` or `trend` |
| `ALGO_CAPITAL_FLOOR` | 8500 | Soft floor: stop trading, keep holdings, alert you |
| `ALGO_FLOOR_ACTION` | freeze | `freeze` or `liquidate` at the soft floor |
| `ALGO_HARD_FLOOR` | 7000 | Last resort: sell everything below this |
| `ALGO_LIVE` | 0 | `1` = real orders |
| `UPSTOX_ACCESS_TOKEN` | – | Today's Upstox token |

## Honest limitations

- **Realistic worst case is about a 30% loss.** Holdings ride out drops down to the ₹7,000 hard
  floor. Floors are checked once a day, so an overnight crash can sell below ₹7,000; the
  synthetic 35% one-day gap test ended at ₹5,824. That's a painful loss, but far from zero.
- **Past results don't repeat.** 2019–2026 was very good for gold. In a long 2008-style crash
  the `trend` mode would have protected more.
- **Small-account costs:** at ₹10k, the ₹20-per-order brokerage is a noticeable drag (about 1% per round trip).
- **Endpoints** follow the official `upstox-python` SDK (v3 orders/candles/LTP). Verify with a
  paper run first. Upstox changes APIs from time to time.
