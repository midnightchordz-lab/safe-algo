# safe-algo

Automated Upstox trading bots for one owner. This repository holds only this project.

## Non-negotiable rule: one project per repository

- Only work on these trading bots belongs in this repository.
- When the owner starts a **new project**, do not save anything for it here or in any other existing
  repository. Ask the owner to create a **new GitHub repository** first, and put all of that project's
  work there.
- Never put this project's code into another repository (it was moved out of the Food app repo for that reason).

## What's here

| Path | What it is | Mode |
|---|---|---|
| `bot.py`, `engine.py`, `risk.py`, `strategy.py`, `config.py` | Long-term bot: NIFTYBEES + GOLDBEES 50/50, rebalanced; soft floor ₹8,500 (freeze), hard floor ₹7,000 (sell) | LIVE |
| `swing/` | Swing bot: Nifty 50 pullbacks, 3:1 reward/risk, broker-held GTT stops | PAPER |
| `commodity/` | Commodity option bot: CRUDEOIL price data + Claude news read, hard rules, 1 lot, intraday; on no-trade it watches Claude's breakout levels and re-checks once | owner's choice (`install_mac.py --commodity-live`) |
| `tools/mcx_ticket.py` | Manual commodity option ticket; every order needs the owner to type YES | manual |
| `research/` | Read-only studies (e.g. scalping cost test); never trade | n/a |
| `autorun.py`, `install_mac.py`, `market_hours.py` | macOS launchd scheduling, notifications, order cutoff | n/a |
| `upstox_api.py`, `get_token.py`, `envfile.py` | Upstox REST client, daily login, `.env` loader | n/a |

Schedule (IST, Mon-Fri): 09:00 login, 14:50 long-term, 14:57 swing, 18:25 commodity. No equity
orders after 15:10 (closing auction session).

## Working rules

- Run `python3 -m unittest discover -s tests` before every push; all tests must pass.
- Guardrails are the point of this project: never weaken floors, caps, the 1-lot limit, the order
  cutoff or the "buy-only, never sell an option not held" rules without the owner explicitly asking.
- Anything that places real orders is tested with fakes first, and new strategies are backtested and
  paper-traded before going live.
- Never commit private files: `.env`, `token.txt`, `*state.json`, logs, `swing_data.json`
  (all git-ignored). Never print or log API keys.
- The owner runs everything on a Mac with Python 3.9 (the Anthropic SDK resolves to 0.x there):
  keep code 3.9-compatible and pass newer Claude API fields via `extra_body` / `extra_headers`.
- Give the owner exact, copy-paste Terminal commands, one per line, with no placeholders they might
  run literally.
