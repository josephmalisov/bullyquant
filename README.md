# BullyQuant — Autonomous AI Strategy Lab

An AI that **invents, backtests, scores, anti-cheats, and iterates** on algorithmic
trading strategies on its own — connected to [QuantConnect](https://www.quantconnect.com)
for real cloud backtests. It runs headless and **emails you the results**; there's no
dashboard to babysit.

Each generation the loop:

1. **Ideates** a strategy (strong model) → writes `main.py` in QuantConnect conventions.
2. **Compiles & auto-fixes** it on QC (a mid model repairs compile/runtime errors).
3. **Backtests** it and **scores** the result on a composite objective.
4. **Anti-cheats** strong performers — static code scan + result sanity +
   out-of-sample re-run + an AI adversarial review — to catch survivorship bias,
   lookahead, overfitting, unrealistic fills, and fluke-driven results.
5. **Analyzes & improves** it (frontier model) → the next generation.

It stops on a hard cap (max generations / backtests), a score plateau, or a spend
budget. A campaign-summary email plus winner/flag alerts go out as it runs; a full
HTML report (with the real QC backtest URLs) is archived under `data/reports/`.

## Model tiers

| Tier | Default model | Role |
|------|---------------|------|
| Frontier | `claude-fable-5` | Analyst (finding improvements) + ideation — the loop's highest-leverage calls |
| Strong | `claude-opus-4-8` | Anti-cheat adversarial review |
| Mid | `claude-sonnet-5` | Code generation + error repair |
| Cheap | `claude-haiku-4-5` | Extraction / naming / summaries |

All model ids are overridable in `.env` (`BQ_MODEL_*`). Fable 5 gets a server-side
refusal fallback to Opus so a safety-classifier false-positive on a strategy prompt
doesn't stall the loop.

### Controlling spend

`daily` mode calls the ideator `population_size` times and the analyst up to
`survivors * (iterations - 1)` times per run — that's the bulk of the Anthropic
cost, well before any QuantConnect usage. Levers, cheapest-and-safest first:

- **Set `BQ_TOKEN_BUDGET`** (USD) in `.env` — both `run` and `daily` check it before
  each new idea/iteration and stop early once crossed. `.env.example` ships this at
  `3`; raise or lower it to taste. This is the one guardrail that actually caps a
  run, so set it before pointing this at an unattended cron.
- **`BQ_IDEATE_WITH_FRONTIER=false`** (the shipped default) — ideation uses Opus
  (`strong`) instead of Fable (`frontier`), at roughly half the per-token price, for
  a small quality trade-off.
- **Shrink the population** — `BQ_POPULATION_SIZE`, `BQ_SURVIVORS`, `BQ_ITERATIONS`
  scale LLM calls close to linearly. `3 / 2 / 3` costs noticeably less than the
  `5 / 3 / 5` default and still gives the analyst room to iterate.
- **Downgrade `BQ_MODEL_FRONTIER`** to `claude-sonnet-5` (or another mid-tier model)
  if you want the analyst/ideator on the same tier as code generation — cuts the
  single most expensive line item at a real quality cost.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in .env  (it is gitignored — nothing personal is committed)
```

Required in `.env` (or the environment):

- `QC_USER_ID`, `QC_API_TOKEN` — from https://www.quantconnect.com/account
- `ANTHROPIC_API_KEY` — from https://console.anthropic.com

Optional:

- **Email** (`EMAIL_SMTP_*`, `EMAIL_TO`) — e.g. a Gmail app password. Without it the
  loop still runs and just writes report files.
- **Fable 5 needs 30-day data retention** on your Anthropic org (it isn't available
  under zero-data-retention). Don't want to enable it? Set `BQ_MODEL_FRONTIER=claude-opus-4-8`
  and the loop runs unchanged on Opus.

## Run

```bash
# Autonomous single-lineage campaign (uses the default objective if you omit --objective)
python -m lab run --objective "Maximize risk-adjusted annualized return on liquid US ETFs" \
    --max-generations 8

# Daily mode: seed 5 random ideas, keep the best 3, iterate each up to 5 times,
# and email the top 3 at the end. This is what the Railway cron runs every morning.
python -m lab daily --objective "Maximize risk-adjusted annualized return on liquid US ETFs" \
    --population-size 5 --survivors 3 --iterations 5

# Custom windows (defaults roll automatically — trailing 4y with a 6mo OOS holdout)
python -m lab run --start 2021-01-01 --end 2023-01-01 --oos-start 2023-01-01 --oos-end 2025-01-01

python -m lab list                 # list campaigns
python -m lab report <campaign_id> # re-render a campaign's HTML report
```

The loop is a plain CLI process — run it locally, in a `tmux`/`screen`, or on any host
or cron. All state lives in `data/bullyquant.db` so a finished campaign is fully
inspectable.

### `run` vs `daily`

- **`run`** — one lineage: ideate once, then the analyst repeatedly proposes the next
  improvement to *that same strategy* until a plateau, cap, or budget is hit. Emails a
  full campaign report plus winner/flag alerts as it goes.
- **`daily`** — a small population: seed `--population-size` (default 5) independent
  ideas, backtest each once, keep the best `--survivors` (default 3), then iterate each
  survivor up to `--iterations` generations total (default 5, seed included). At the end
  it emails the best generation from each surviving lineage — the "top 3" — in one
  message. This is the mode meant for a recurring/cron run.

Both modes share the same guardrails, scorer, and anti-cheat gate; tune them via
`BQ_POPULATION_SIZE` / `BQ_SURVIVORS` / `BQ_ITERATIONS` / `BQ_MAX_BACKTESTS` in `.env`
(see `.env.example`).

## Deploy on Railway (daily cron)

The repo ships a `Dockerfile` and `railway.json` that run `python -m lab daily` on a
schedule as a [Railway cron job](https://docs.railway.app/reference/cron-jobs).

1. Push this repo to GitHub and create a new Railway project from it (Railway detects
   the `Dockerfile` automatically).
2. Add a **volume** mounted at `/app/data` so the SQLite DB (and thus campaign history)
   survives between runs — without it every run starts from a clean database, which is
   fine but loses history.
3. Set the required environment variables in the Railway service (**Variables** tab):
   `QC_USER_ID`, `QC_API_TOKEN`, `ANTHROPIC_API_KEY`, and the `EMAIL_SMTP_*` / `EMAIL_TO`
   vars so you actually get the results. Optionally set `BQ_POPULATION_SIZE`,
   `BQ_SURVIVORS`, `BQ_ITERATIONS`, `BQ_MAX_BACKTESTS`, `BQ_WINDOW_YEARS` to tune it.
4. `railway.json` sets `cronSchedule` to `0 12 * * *` (12:00 UTC = 4:00am Alaska Daylight
   Time, UTC-8) and `restartPolicyType: NEVER` so a run that ends (success or failure)
   doesn't get restarted until the next scheduled fire. Railway cron is fixed UTC and
   does **not** shift for daylight saving, so during Alaska Standard Time (UTC-9,
   roughly Nov–Mar) this fires at 5:00am local instead of 4:00am — bump it to `0 13 * * *`
   for that stretch if you want it pinned to 4:00am year-round. Edit the cron expression
   in `railway.json`, or set it in the Railway dashboard under **Settings → Cron
   Schedule**, and redeploy.

Each firing is a fresh container: it seeds 5 ideas, backtests them on the trailing
window, iterates the best 3, and emails you the results, then exits.

## What "good" means (the objective)

Annualized return is the dominant term, adjusted up by Sharpe and penalized by
drawdown, nudged by per-trade expectancy, and gated by a minimum trade count so a
lucky handful of trades can't top the leaderboard. Weights live in
`lab/config.py::Objective`.

## Anti-cheat

Triggered when a strategy scores well (`Thresholds.validate_score`). It produces a
verdict — `clean` / `suspicious` / `cheating` — a trust score, and itemized flags:

- **Static scan**: survivorship universes (hand-picked famous winners), hardcoded
  dates in the trading logic, missing fee/slippage models, extreme leverage, lookahead.
- **Result sanity**: implausible Sharpe / return / near-zero drawdown / ~100% win
  rate / too-few trades; P&L concentration (one trade carrying the result).
- **Out-of-sample holdout**: re-runs the identical code on a held-out window and
  measures degradation — this is what catches the optimizer overfitting the in-sample
  window.
- **AI review** (Opus): weighs code + stats + the flags and can only make the verdict
  *worse*, never whitewash it.

## Layout

```
lab/
  config.py       env loading, model tiers, objective weights, guardrails
  qc_client.py    QuantConnect cloud API (auth, compile, backtest, closed trades, OOS patch)
  llm.py          four-tier model router (Fable/Opus/Sonnet/Haiku)
  prompts.py      system prompts (ideator, coder, analyst, validator)
  agents.py       Ideator / Coder / Analyst / AI reviewer
  scorer.py       composite objective
  validator.py    anti-cheat gate (static + sanity + OOS + AI)
  orchestrator.py the loop
  store.py        SQLite persistence
  reporter.py     HTML reports + email snippets
  emailer.py      SMTP delivery
  cli.py          `python -m lab ...`
tests/            pytest suite (no network / no API key required)
data/             gitignored: SQLite DB, HTML reports, archived strategy code
Dockerfile        image used for local Docker runs and Railway deploys
railway.json      Railway build/deploy config incl. the daily cron schedule
```

## Tests

```bash
python -m pytest -q
```

The suite mocks QuantConnect and Anthropic, so it needs no credentials or network.

## Safety & scope

This is a research/backtesting tool. Backtests are optimistic by construction — the
anti-cheat gate exists precisely because an autonomous optimizer *will* find strategies
that look too good. Treat a `clean` verdict as "worth a closer manual look," not a
green light to trade real money.
