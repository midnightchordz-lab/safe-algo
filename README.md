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
   cd safe-algo
   pip install -r requirements.txt
   python -m unittest discover -s tests       # safety tests
   python backtest.py                         # synthetic stress tests (bull/bear/sideways/crash)
   export UPSTOX_ACCESS_TOKEN=$(cat token.txt)
   python backtest.py --upstox --days 2500    # backtest on real NIFTYBEES/GOLDBEES history
   ```

## Running it

Run it **once per trading day around 2:50 PM IST**. No orders go out after 3:10 PM, clear of the
closing auction session (CAS) from about 3:15 PM:

```bash
python bot.py                  # PAPER: real prices, simulated fills, no orders
ALGO_LIVE=1 python bot.py      # LIVE: real orders
```

State is kept in `state.json` and a log in `algo.log`.

### Full automation on a Mac

```bash
python3 install_mac.py            # LIVE; use --paper for practice, --uninstall to remove
```

This saves your API key/secret to `.env` (readable only by you) and schedules, Monday to Friday:

| Time | What happens |
|---|---|
| 09:00 | If today's token is missing, Safari opens the Upstox login page. Log in; the token is captured automatically. |
| 14:50 | The long-term bot runs by itself (skips if the market is closed). If you skipped the morning login it asks again, waiting up to 15 minutes. |

You get a macOS notification whenever it trades, halts, errors or needs a login. Logs:
`algo.log`, `autorun-login.log`, `autorun-trade.log`. Upstox requires a human login every day,
so that step can't be removed. The Mac must be on, awake and online at those times.

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

---

# Swing bot (separate ₹25,000 budget)

Trades Nifty 50 stocks, holding each for days to a few weeks. Every trade risks ₹1 to make ₹3.
It has its own money, state (`swing_state.json`), log (`swing.log`) and halt file (`SWING_HALTED`),
and never touches the long-term bot's holdings.

**Entry (daily, ~2:57 PM):** the stock is in an uptrend (above its 200-day average, 50-day above
200-day) and either makes a new 20-day closing high (`breakout`) or dips below and reclaims its
20-day average (`pullback`). The backtest decides which rule to use.

**Exit:** a stop-loss 1.5 × ATR below entry, a target 3× that distance above, or after 20 trading days.
In LIVE mode the stop and target are a single Upstox GTT order, so **Upstox triggers them during
market hours even if your Mac is off**.

| Guardrail | Setting |
|---|---|
| Risk per trade | 1% of equity (about ₹250) |
| Reward : risk | 3 : 1 on every trade |
| Open positions | at most 3, and at most 2 new per day |
| Single stock | at most 35% of equity |
| Total open risk | at most 3% of equity if every stop hit at once |
| Losing streak | after 4 losses in a row, no new trades for 5 days |
| Soft floor (₹21,250, −15%) | stop new trades, keep stops/targets, notify you |
| Hard floor (₹17,500, −30%) | sell everything and halt |
| Costs | skip trades where charges exceed 0.35R or order < ₹3,000 |
| Same day twice | refused, so nothing is double-traded |

```bash
python3 -m swing.backtest --upstox    # real-data backtest of every rule (downloads ~50 stocks once)
python3 -m swing.bot                  # PAPER run
SWING_LIVE=1 python3 -m swing.bot     # LIVE run
python3 -m swing.bot --resume         # clear a halt
python3 install_mac.py --swing-live   # automate it live (default install keeps swing in PAPER)
```
