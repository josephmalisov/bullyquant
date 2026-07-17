"""The autonomous strategy loop.

Per campaign: ideate -> compile (auto-fix) -> backtest -> score -> (if strong)
anti-cheat gate -> analyze & propose next generation -> repeat, until a hard cap,
a plateau, or the token budget is hit. State is persisted every step; a campaign
summary and winner/flag alerts are emailed as it runs.
"""

from __future__ import annotations

import time
import traceback

from . import emailer, reporter
from .agents import Analyst, Coder, Ideator, ai_reviewer
from .config import Config
from .qc_client import CompileError, QCClient, patch_dates
from .scorer import score as score_stats
from .store import Store
from .validator import validate

MAX_FIX_ATTEMPTS = 3


class Orchestrator:
    def __init__(self, config: Config, store: Store, llm, qc: QCClient):
        self.cfg = config
        self.store = store
        self.llm = llm
        self.qc = qc
        self.ideator = Ideator(llm)
        self.coder = Coder(llm)
        self.analyst = Analyst(llm)
        self.review = ai_reviewer(llm)
        self.backtests_used = 0

    # ── logging ───────────────────────────────────────────────────────────────

    @staticmethod
    def _log(msg: str) -> None:
        print(f"  {msg}", flush=True)

    # ── QC build + backtest with auto-fix ─────────────────────────────────────

    def _build_and_backtest(self, project_id: int, code: str, name: str,
                            start: str, end: str):
        """Write patched code, compile+run, repairing compile/runtime errors via
        the coder. Returns (result, final_code). Raises RuntimeError if it can't
        be made to run within MAX_FIX_ATTEMPTS or the backtest budget is spent."""
        code = patch_dates(code, start, end)
        last_err = ""
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            self.qc.write_file(project_id, "main.py", code)
            try:
                result = self.qc.run_backtest(project_id, name)
                self.backtests_used += 1
                return result, code
            except CompileError as exc:
                last_err = str(exc)
                self._log(f"compile error (attempt {attempt}) — asking the coder to fix")
            except RuntimeError as exc:
                self.backtests_used += 1  # a runtime crash consumed node time
                last_err = str(exc)
                self._log(f"runtime error (attempt {attempt}) — asking the coder to fix")
            if attempt < MAX_FIX_ATTEMPTS and self.backtests_used < self.cfg.guardrails.max_backtests:
                code = self.coder.fix(code, last_err)
            else:
                break
        raise RuntimeError(f"could not get a clean backtest after {MAX_FIX_ATTEMPTS} attempts:\n{last_err}")

    # ── anti-cheat gate ───────────────────────────────────────────────────────

    def _run_validation(self, code: str, result, in_sample_score: float, project_id: int):
        oos_score = None
        # Out-of-sample re-run (guards against the loop overfitting the window).
        if self.backtests_used < self.cfg.guardrails.max_backtests:
            try:
                self._log("anti-cheat: out-of-sample re-run")
                oos_res, _ = self._build_and_backtest(
                    project_id, code, "OOS holdout",
                    self.cfg.windows.oos_start, self.cfg.windows.oos_end,
                )
                oos_score = score_stats(oos_res.statistics, self.cfg.objective).score
            except Exception as exc:
                self._log(f"anti-cheat: OOS re-run failed ({exc}); skipping OOS check")
        report = validate(
            code, result.statistics, result.closed_trades,
            in_sample_score=in_sample_score if oos_score is not None else None,
            oos_score=oos_score,
            thresholds=self.cfg.thresholds,
            ai_review=self.review,
        )
        return report

    # ── email helpers ─────────────────────────────────────────────────────────

    def _alert(self, kind: str, campaign: dict, gen: dict) -> None:
        if not self.cfg.email.configured:
            return
        subject = {
            "winner": f"[BullyQuant] New validated leader (score {round(gen.get('score') or 0, 3)})",
            "flag": f"[BullyQuant] Anti-cheat flagged a strong performer",
        }.get(kind, "[BullyQuant] Update")
        emailer.send(self.cfg.email, subject, reporter.alert_html(kind, campaign, gen))

    # ── main loop ─────────────────────────────────────────────────────────────

    def run_campaign(self, objective: str) -> int:
        g = self.cfg.guardrails
        params = {
            "windows": vars(self.cfg.windows),
            "models": vars(self.cfg.models),
            "guardrails": vars(g),
        }
        campaign_id = self.store.create_campaign(objective, params)
        self.llm.campaign_id = campaign_id
        self._log(f"campaign #{campaign_id}: {objective}")

        # First candidate: a fresh idea.
        self.llm.generation_id = None
        idea = self.ideator.propose(objective, memory="")
        candidate = {"name": idea["name"], "hypothesis": idea["hypothesis"],
                     "code": idea["code"], "parent_id": None, "lineage": 1}

        best_score = None
        best_gen_id = None
        gens_since_improvement = 0
        lineage_counter = 1
        memory_lines: list[str] = []

        for gen_number in range(1, g.max_generations + 1):
            if self.backtests_used >= g.max_backtests:
                self._log("backtest budget exhausted — stopping")
                break
            gen_id = self.store.add_generation(
                campaign_id, gen_number, parent_id=candidate["parent_id"],
                lineage=candidate["lineage"], name=candidate["name"],
                hypothesis=candidate["hypothesis"], code=candidate["code"],
            )
            self.llm.generation_id = gen_id
            self._log(f"── generation {gen_number}: {candidate['name']} "
                      f"(lineage {candidate['lineage']})")

            project_name = f"BQ c{campaign_id} g{gen_number} {candidate['name']}"[:100]
            try:
                project_id = self.qc.create_project(project_name)
                result, final_code = self._build_and_backtest(
                    project_id, candidate["code"], candidate["name"],
                    self.cfg.windows.start, self.cfg.windows.end,
                )
            except Exception as exc:
                self._log(f"generation failed: {exc}")
                self.store.update_generation(gen_id, status="failed", error=str(exc)[:2000])
                memory_lines.append(f"Gen {gen_number} '{candidate['name']}': FAILED ({str(exc)[:160]}).")
                # Failed lineage — start a new idea next.
                candidate = self._fresh_idea(objective, memory_lines, lineage_counter + 1)
                lineage_counter += 1
                continue

            sb = score_stats(result.statistics, self.cfg.objective)
            self.store.update_generation(
                gen_id, status="scored", code=final_code, project_id=project_id,
                backtest_id=result.backtest_id, url=result.url, stats=result.statistics,
                score=sb.score, score_breakdown=sb.as_dict(),
            )
            self._log(f"score {sb.score:.3f} | ann.ret {sb.car:.1%} | sharpe {sb.sharpe:.2f} "
                      f"| maxDD {sb.drawdown:.1%} | trades {sb.trades} | {result.url}")

            improved = best_score is None or sb.score > best_score + 1e-9
            if improved:
                best_score, best_gen_id = sb.score, gen_id
                gens_since_improvement = 0
                self.store.update_campaign(campaign_id, best_score=best_score, best_gen_id=best_gen_id)
            else:
                gens_since_improvement += 1

            # Anti-cheat gate for strong performers.
            validation = None
            if sb.score >= self.cfg.thresholds.validate_score:
                report = self._run_validation(final_code, result, sb.score, project_id)
                validation = report.as_dict()
                self.store.update_generation(gen_id, validation=validation, status="validated")
                self._log(f"anti-cheat verdict: {report.verdict} (trust {report.trust_score:.2f}, "
                          f"{len(report.flags)} flags)")
                gen_row = self.store.get_generation(gen_id)
                if report.verdict == "clean" and improved:
                    self._alert("winner", self.store.get_campaign(campaign_id), gen_row)
                elif report.verdict in ("suspicious", "cheating"):
                    self._alert("flag", self.store.get_campaign(campaign_id), gen_row)

            memory_lines.append(
                f"Gen {gen_number} '{candidate['name']}': score {sb.score:.3f} "
                f"(ann.ret {sb.car:.1%}, sharpe {sb.sharpe:.2f}, maxDD {sb.drawdown:.1%}, "
                f"trades {sb.trades})"
                + (f", anti-cheat {validation['verdict']}." if validation else ".")
            )

            # Stop conditions.
            if gens_since_improvement >= g.plateau_patience:
                self._log(f"plateau: no improvement for {g.plateau_patience} generations — stopping")
                break
            if g.token_budget and self.llm.spend_usd() >= g.token_budget:
                self._log(f"token/spend budget reached (~${self.llm.spend_usd():.2f}) — stopping")
                break
            if gen_number >= g.max_generations or self.backtests_used >= g.max_backtests:
                break

            # Next candidate: analyst improves this generation (same lineage).
            try:
                self._log("analyst: diagnosing and proposing the next improvement")
                proposal = self.analyst.improve(objective, final_code, result.statistics,
                                                sb.as_dict(), validation)
                self.store.update_generation(gen_id, analysis={
                    "diagnosis": proposal["diagnosis"], "plan": proposal["plan"]})
                candidate = {
                    "name": candidate["name"], "hypothesis": candidate["hypothesis"],
                    "code": proposal["code"], "parent_id": gen_id, "lineage": candidate["lineage"],
                }
            except Exception as exc:
                self._log(f"analyst failed ({exc}); starting a fresh idea")
                candidate = self._fresh_idea(objective, memory_lines, lineage_counter + 1)
                lineage_counter += 1

            time.sleep(1)  # gentle throttle between generations

        self._finalize(campaign_id)
        return campaign_id

    def _fresh_idea(self, objective: str, memory_lines: list[str], lineage: int) -> dict:
        self.llm.generation_id = None
        idea = self.ideator.propose(objective, memory="\n".join(memory_lines[-12:]))
        return {"name": idea["name"], "hypothesis": idea["hypothesis"],
                "code": idea["code"], "parent_id": None, "lineage": lineage}

    def _finalize(self, campaign_id: int) -> None:
        self.store.update_campaign(campaign_id, status="done")
        campaign = self.store.get_campaign(campaign_id)
        gens = self.store.get_generations(campaign_id)
        usage = self.store.usage_totals(campaign_id)
        try:
            path = reporter.write_report(campaign, gens, usage)
            self._log(f"report written: {path}")
        except Exception:
            self._log("report generation failed:\n" + traceback.format_exc())
            path = None
        if self.cfg.email.configured:
            html = reporter.campaign_report_html(campaign, gens, usage)
            ok = emailer.send(
                self.cfg.email,
                f"[BullyQuant] Campaign #{campaign_id} finished — best score "
                f"{round(campaign.get('best_score') or 0, 3)}",
                html,
            )
            self._log("summary email sent" if ok else "summary email not sent")
        else:
            self._log("email not configured — report written to disk only")
