"""Anti-cheat / validation gate.

Runs when a strategy scores well. Combines:
  1. Static code scan   — survivorship universes, hardcoded dates, missing
                           costs, lookahead patterns, extreme leverage.
  2. Result sanity      — implausible Sharpe/return/drawdown/win-rate/trade
                           count, and P&L concentration from closed trades.
  3. Out-of-sample math — degradation of the held-out re-run vs in-sample.
  4. AI adversarial review (Opus/STRONG) — optional; a final verdict + reasons.

The deterministic parts are pure and unit-tested. The AI review is injected as a
callable so the module has no hard dependency on the LLM layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Thresholds
from .scorer import parse_float, parse_int, parse_percent

# Famous recent winners — a hardcoded universe of these over their winning
# window is the classic survivorship trap.
KNOWN_WINNERS = {
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "AVGO",
    "AMD", "NFLX", "SMCI", "SOXL", "TQQQ", "UPRO", "COIN", "PLTR", "MSTR",
}

SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class Flag:
    category: str
    severity: str  # info | low | medium | high
    detail: str

    def as_dict(self) -> dict:
        return {"category": self.category, "severity": self.severity, "detail": self.detail}


@dataclass
class ValidationReport:
    verdict: str = "clean"          # clean | suspicious | cheating
    trust_score: float = 1.0        # 0 (cheating) .. 1 (clean)
    flags: list = field(default_factory=list)
    ai_verdict: str = ""
    ai_reasons: str = ""
    oos_score: float | None = None
    oos_degradation: float | None = None

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "trust_score": round(self.trust_score, 3),
            "flags": [f.as_dict() for f in self.flags],
            "ai_verdict": self.ai_verdict,
            "ai_reasons": self.ai_reasons,
            "oos_score": self.oos_score,
            "oos_degradation": self.oos_degradation,
        }

    @property
    def max_severity(self) -> str:
        if not self.flags:
            return "info"
        return max((f.severity for f in self.flags), key=lambda s: SEV_ORDER.get(s, 0))


# ── 1. Static code scan ───────────────────────────────────────────────────────

def _tickers_in(code: str) -> set[str]:
    """Uppercase 1-5 letter tokens passed to add_equity/add_* or in string lists."""
    found = set()
    for m in re.finditer(r"add_(?:equity|option|future|forex|crypto)\(\s*[\"']([A-Z]{1,5})[\"']", code):
        found.add(m.group(1))
    # Tickers in quoted list literals, e.g. TICKERS = ["AAPL", "MSFT", ...]
    for m in re.finditer(r"[\"']([A-Z]{2,5})[\"']", code):
        found.add(m.group(1))
    return found


def static_scan(code: str) -> list[Flag]:
    flags: list[Flag] = []

    tickers = _tickers_in(code)
    winners = tickers & KNOWN_WINNERS
    # A small universe made mostly of famous winners is a survivorship red flag.
    if winners and len(tickers) <= 12 and len(winners) >= max(2, len(tickers) // 2):
        flags.append(Flag(
            "survivorship", "high",
            f"Universe is dominated by famous recent winners "
            f"({', '.join(sorted(winners))}) — likely survivorship bias.",
        ))

    # Hardcoded dates in the body (outside the set_start/end_date calls) — a
    # strategy that references specific calendar dates may be curve-fit to events.
    body = re.sub(r"self\.set_(?:start|end)_date\([^)]*\)", "", code)
    date_hits = re.findall(r"\b(?:19|20)\d{2}[-/,]\s*\d{1,2}[-/,]\s*\d{1,2}\b", body)
    date_hits += re.findall(r"datetime\(\s*(?:19|20)\d{2}\s*,", body)
    if date_hits:
        flags.append(Flag(
            "hardcoded-dates", "high",
            f"{len(date_hits)} hardcoded calendar date(s) in the trading logic — "
            f"possible fitting to specific historical events.",
        ))

    # Missing transaction-cost / slippage modeling.
    trades = bool(re.search(r"set_holdings|market_order|limit_order|self\.buy|self\.sell", code))
    models_costs = bool(re.search(r"set_brokerage_model|set_fee_model|set_slippage_model|"
                                  r"set_security_initializer|FeeModel|SlippageModel", code))
    if trades and not models_costs:
        flags.append(Flag(
            "no-costs", "low",
            "No explicit fee/slippage model set — QC applies defaults, but verify "
            "the strategy isn't relying on frictionless fills.",
        ))

    # Extreme leverage.
    lev = [float(m) for m in re.findall(r"set_holdings\(\s*[^,]+,\s*(-?\d+(?:\.\d+)?)", code)]
    if any(abs(x) > 1.0 for x in lev):
        flags.append(Flag(
            "leverage", "medium",
            f"set_holdings uses >100% allocation ({max((abs(x) for x in lev), default=0):.2f}) "
            f"— leveraged exposure inflates returns.",
        ))
    if re.search(r"set_leverage\(\s*([5-9]|[1-9]\d)", code):
        flags.append(Flag("leverage", "medium", "High explicit leverage (set_leverage >= 5)."))

    # Lookahead-ish patterns.
    if re.search(r"\.history\(", code) and re.search(r"future|end=self\.time\s*\+|Resolution\.TICK", code):
        flags.append(Flag(
            "lookahead", "medium",
            "history() combined with future-referencing code — check for lookahead.",
        ))

    return flags


# ── 2. Result sanity ──────────────────────────────────────────────────────────

def pnl_concentration(closed_trades: list) -> float:
    """Share of gross profit contributed by the single most profitable trade.
    1.0 means one trade produced all the profit; 0.0 means none/spread out."""
    profits = [parse_float(t.get("profitLoss")) for t in (closed_trades or [])]
    gross_win = sum(p for p in profits if p > 0)
    if gross_win <= 0:
        return 0.0
    return max((p for p in profits if p > 0), default=0.0) / gross_win


def sanity_check(stats: dict, closed_trades: list, th: Thresholds | None = None) -> list[Flag]:
    th = th or Thresholds()
    flags: list[Flag] = []

    sharpe = parse_float(stats.get("Sharpe Ratio"))
    car = parse_percent(stats.get("Compounding Annual Return"))
    drawdown = abs(parse_percent(stats.get("Drawdown")))
    win_rate = parse_percent(stats.get("Win Rate"))
    trades = parse_int(stats.get("Total Orders")) // 2

    if sharpe >= th.sharpe_suspicious:
        flags.append(Flag("sanity-sharpe", "high",
                          f"Sharpe {sharpe:.2f} exceeds {th.sharpe_suspicious} — implausibly high."))
    if car >= th.car_suspicious:
        flags.append(Flag("sanity-return", "high",
                          f"Annualized return {car:.0%} exceeds {th.car_suspicious:.0%}."))
    if drawdown < th.drawdown_tiny and car > th.car_big:
        flags.append(Flag("sanity-drawdown", "high",
                          f"{car:.0%}/yr with only {drawdown:.1%} max drawdown — too clean."))
    if win_rate >= th.win_rate_suspicious:
        flags.append(Flag("sanity-winrate", "medium",
                          f"Win rate {win_rate:.0%} at/above {th.win_rate_suspicious:.0%}."))
    if trades < th.min_trades_sanity:
        flags.append(Flag("sanity-samples", "medium",
                          f"Only {trades} round-trip trades — too small to trust."))

    conc = pnl_concentration(closed_trades)
    if conc > th.pnl_concentration_max:
        flags.append(Flag("pnl-concentration", "high",
                          f"One trade produced {conc:.0%} of gross profit — result rides on a fluke."))

    return flags


# ── 3. Out-of-sample degradation ──────────────────────────────────────────────

def oos_degradation(in_sample_score: float, oos_score: float) -> float:
    """Fraction of in-sample score lost out of sample, in [0, 1].
    0 = held up perfectly (or improved); 1 = fully collapsed to <=0."""
    if in_sample_score <= 0:
        return 0.0
    return max(0.0, min(1.0, (in_sample_score - oos_score) / in_sample_score))


def oos_flags(in_sample_score: float, oos_score: float, th: Thresholds | None = None) -> list[Flag]:
    th = th or Thresholds()
    deg = oos_degradation(in_sample_score, oos_score)
    if deg > th.oos_degradation_max:
        return [Flag("overfit", "high",
                     f"Out-of-sample score dropped {deg:.0%} vs in-sample "
                     f"({in_sample_score:.3f} -> {oos_score:.3f}) — overfit to the window.")]
    return []


# ── Verdict assembly ──────────────────────────────────────────────────────────

def _verdict_from_flags(flags: list[Flag]) -> tuple[str, float]:
    penalty = {"info": 0.0, "low": 0.05, "medium": 0.15, "high": 0.35}
    trust = 1.0 - sum(penalty.get(f.severity, 0.0) for f in flags)
    trust = max(0.0, min(1.0, trust))
    highs = sum(1 for f in flags if f.severity == "high")
    if highs >= 2 or trust < 0.4:
        return "cheating", trust
    if highs >= 1 or trust < 0.75:
        return "suspicious", trust
    return "clean", trust


def validate(
    code: str,
    stats: dict,
    closed_trades: list,
    *,
    in_sample_score: float | None = None,
    oos_score: float | None = None,
    thresholds: Thresholds | None = None,
    ai_review=None,
) -> ValidationReport:
    """Run the full gate. ``ai_review`` (optional) is a callable
    (code, stats, flags) -> {"verdict": str, "reasons": str}."""
    th = thresholds or Thresholds()
    flags: list[Flag] = []
    flags += static_scan(code)
    flags += sanity_check(stats, closed_trades, th)

    report = ValidationReport()
    if in_sample_score is not None and oos_score is not None:
        report.oos_score = round(oos_score, 4)
        report.oos_degradation = round(oos_degradation(in_sample_score, oos_score), 4)
        flags += oos_flags(in_sample_score, oos_score, th)

    report.flags = flags
    verdict, trust = _verdict_from_flags(flags)
    report.verdict, report.trust_score = verdict, trust

    if ai_review is not None:
        try:
            result = ai_review(code, stats, [f.as_dict() for f in flags]) or {}
            report.ai_verdict = str(result.get("verdict", "")).lower()
            report.ai_reasons = str(result.get("reasons", ""))
            # The AI can only make the verdict *worse*, never whitewash flags.
            rank = {"clean": 0, "suspicious": 1, "cheating": 2}
            if rank.get(report.ai_verdict, -1) > rank.get(report.verdict, 0):
                report.verdict = report.ai_verdict
                report.trust_score = min(report.trust_score, 0.5 if report.ai_verdict == "suspicious" else 0.2)
        except Exception:
            pass  # AI review is best-effort; deterministic verdict stands.

    return report
