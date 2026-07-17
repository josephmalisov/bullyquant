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
# Autonomous campaign (uses the default objective if you omit --objective)
python -m lab run --objective "Maximize risk-adjusted annualized return on liquid US ETFs" \
    --max-generations 8

# Custom windows (in-sample and the anti-cheat out-of-sample holdout come from .env)
python -m lab run --start 2021-01-01 --end 2023-01-01 --oos-start 2023-01-01 --oos-end 2025-01-01

python -m lab list                 # list campaigns
python -m lab report <campaign_id> # re-render a campaign's HTML report
```

The loop is a plain CLI process — run it locally, in a `tmux`/`screen`, or on any host
or cron. All state lives in `data/bullyquant.db` so a finished campaign is fully
inspectable.

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
