"""Command-line entry point.

    python -m lab run --objective "high annualized return on liquid US ETFs, \\
        risk-adjusted, drawdown-aware" --max-generations 8

    python -m lab report <campaign_id>       # re-render an existing campaign's report
    python -m lab list                       # list campaigns
"""

from __future__ import annotations

import argparse
import sys

from .config import DB_PATH, Config
from .llm import LLM
from .qc_client import QCClient
from .store import Store

DEFAULT_OBJECTIVE = (
    "Maximize annualized return on liquid, broadly-representative US equities/ETFs, "
    "adjusted for risk (Sharpe/Sortino) and penalized for drawdown, with a healthy "
    "win rate and expectancy. Avoid survivorship bias, lookahead, and overfitting."
)


def _cmd_run(args) -> int:
    cfg = Config.from_env()
    cfg.require_qc()
    cfg.require_anthropic()

    # Apply CLI overrides by rebuilding the frozen window/guardrail configs.
    from dataclasses import replace
    if args.start or args.end or args.oos_start or args.oos_end:
        cfg.windows = replace(
            cfg.windows,
            start=args.start or cfg.windows.start,
            end=args.end or cfg.windows.end,
            oos_start=args.oos_start or cfg.windows.oos_start,
            oos_end=args.oos_end or cfg.windows.oos_end,
        )
    if args.max_generations or args.max_backtests:
        cfg.guardrails = replace(
            cfg.guardrails,
            max_generations=args.max_generations or cfg.guardrails.max_generations,
            max_backtests=args.max_backtests or cfg.guardrails.max_backtests,
        )

    store = Store(DB_PATH)
    llm = LLM(cfg, store=store)
    qc = QCClient(cfg.qc_user_id, cfg.qc_api_token)

    from .orchestrator import Orchestrator
    orch = Orchestrator(cfg, store, llm, qc)
    objective = args.objective or DEFAULT_OBJECTIVE
    campaign_id = orch.run_campaign(objective)
    print(f"\nCampaign #{campaign_id} complete. Report: data/reports/campaign_{campaign_id}.html")
    store.close()
    return 0


def _cmd_report(args) -> int:
    from . import reporter
    store = Store(DB_PATH)
    campaign = store.get_campaign(args.campaign_id)
    if not campaign:
        print(f"No campaign #{args.campaign_id}.")
        return 1
    gens = store.get_generations(args.campaign_id)
    usage = store.usage_totals(args.campaign_id)
    path = reporter.write_report(campaign, gens, usage)
    print(f"Report written: {path}")
    store.close()
    return 0


def _cmd_list(args) -> int:
    store = Store(DB_PATH)
    rows = store.conn.execute(
        "SELECT id, objective, status, best_score FROM campaigns ORDER BY id DESC"
    ).fetchall()
    if not rows:
        print("No campaigns yet.")
    for r in rows:
        print(f"#{r['id']:>3}  {r['status']:<8}  best={round(r['best_score'] or 0, 3):<7}  "
              f"{(r['objective'] or '')[:70]}")
    store.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lab", description="BullyQuant autonomous strategy lab")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run an autonomous campaign")
    p_run.add_argument("--objective", "-o", default="", help="what a 'good' strategy means")
    p_run.add_argument("--max-generations", type=int, default=0)
    p_run.add_argument("--max-backtests", type=int, default=0)
    p_run.add_argument("--start", default="")
    p_run.add_argument("--end", default="")
    p_run.add_argument("--oos-start", default="")
    p_run.add_argument("--oos-end", default="")
    p_run.set_defaults(func=_cmd_run)

    p_report = sub.add_parser("report", help="re-render a campaign report")
    p_report.add_argument("campaign_id", type=int)
    p_report.set_defaults(func=_cmd_report)

    p_list = sub.add_parser("list", help="list campaigns")
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
