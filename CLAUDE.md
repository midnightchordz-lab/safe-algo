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
| `commodity/` | Commodity option bots, one engine run twice: CRUDEOIL (COMMODITY_* settings, commodity_*.json) and NATURALGAS (NATGAS_* settings, commodity_naturalgas_*.json), each with its own live switch, premium cap and loss limit. Price data + Claude news read, hard rules, 1 lot, intraday; on no-trade it arms Claude's pre-committed call/put plans and executes one when its level holds for two checks | owner's choice (`install_mac.py --commodity-live`) |
| `commodity/score.py` | Replays every logged commodity plan against real 15-minute futures bars: win rate, avg win/loss, profit factor (read-only) | n/a |
| `tools/mcx_ticket.py` | Manual commodity option ticket; every order needs the owner to type YES | manual |
| `research/` | Read-only studies (e.g. scalping cost test); never trade | n/a |
| `autorun.py`, `market_hours.py` | Scheduled job entry point, alerts (email and/or macOS), order cutoff | n/a |
| `install_linux.py` | AWS server (Mumbai, ubuntu@65.0.244.16, ~/safe-algo with .venv): cron jobs + .env, email alerts | where the bots run |
| `install_mac.py` | Mac launchd jobs; with `--server ubuntu@65.0.244.16` the Mac only does the 9:00 login and copies token.txt to the server | login only |
| `upstox_api.py`, `get_token.py`, `envfile.py` | Upstox REST client, daily login, `.env` loader | n/a |

Schedule (IST, Mon-Fri): 09:00 login on the Mac (token copied to the server over SSH), 09:05 server
token check, 14:50 long-term, 14:57 swing, 18:25 crude, 18:26 natural gas, all on the server. No equity orders after
15:10 (closing auction session). The server opens no inbound ports except SSH; don't add a public
login listener without the owner's explicit approval.

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
