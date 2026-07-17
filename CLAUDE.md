# BullyQuant — repo conventions for Claude Code

Autonomous AI trading-strategy lab. The AI invents a strategy, backtests it on
QuantConnect, scores it, anti-cheats strong performers, and iterates — emailing
results. See `README.md` for the full picture.

## Architecture (where things live)

- `lab/config.py` — all env loading + tunables (model tiers, `Objective` weights,
  anti-cheat `Thresholds`, `Guardrails`, `Windows`, `Email`). **Change behavior here
  first** rather than sprinkling constants across modules.
- `lab/llm.py` — the four-tier router (`frontier`/`strong`/`mid`/`cheap` → model ids).
  Request shaping is per model family: Fable omits `thinking` + uses beta fallbacks;
  Opus/Sonnet use adaptive thinking + `output_config.effort`; Haiku is a plain request.
- `lab/agents.py` — thin wrappers (Ideator, Coder, Analyst, `ai_reviewer`). Loop logic
  stays in `orchestrator.py`, not here.
- `lab/qc_client.py` — QuantConnect API. Auth = HTTP Basic with
  `sha256(api_token:timestamp)`. The HTTP layer is injectable (`http=`) for tests.
- `lab/scorer.py`, `lab/validator.py` — **pure, unit-tested**. Keep them
  dependency-free (no LLM, no network) so the deterministic logic stays testable; the
  validator takes the AI review as an injected callable.
- `lab/store.py` — SQLite. JSON blobs (`stats`, `score_breakdown`, `validation`,
  `analysis`) are serialized transparently by `update_generation`.

## Conventions

- Secrets and the recipient email come **only** from the environment / gitignored
  `.env`. Never hardcode a credential, email address, or the `claude-*` model
  identifier into committed code — the repo is meant to be publishable.
- Keep `scorer.py` and `validator.py` pure and covered by tests in `tests/`.
- Anything that talks to QC or Anthropic should be injectable so `tests/` can run with
  no network and no API key (see `tests/fakes.py`).
- Generated strategy `main.py` must follow the LEAN conventions in `lab/prompts.py`
  (`QC_CONVENTIONS`) — snake_case API, literal `self.set_start_date(Y, M, D)` calls
  (the harness rewrites them for OOS re-runs).

## Common tasks

- Run tests: `python -m pytest -q` (no credentials needed).
- Run a campaign: `python -m lab run --objective "..." --max-generations N`.
- Tune what "good" means: `Objective` in `lab/config.py`.
- Tune the anti-cheat sensitivity: `Thresholds` in `lab/config.py`.
- Add a new anti-cheat rule: a pure function in `lab/validator.py` returning `Flag`s,
  wired into `validate()`, plus a test.
