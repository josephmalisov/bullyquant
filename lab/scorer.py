"""Composite objective function.

Ranks strategies and is the target the analyst optimizes. Annualized return is
the dominant term, adjusted upward by Sharpe and penalized by drawdown, nudged
by per-trade expectancy, and gated by a minimum trade count so a lucky handful
of trades can't top the leaderboard.

All functions are pure and operate on QuantConnect's string-valued statistics
dict, so they're easy to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Objective


def parse_percent(value) -> float:
    """'15.2%' -> 0.152 ; '-3.4%' -> -0.034 ; already-fractional passthrough."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if s in ("", "N/A", "-"):
        return 0.0
    pct = s.endswith("%")
    s = s.rstrip("%").strip()
    try:
        num = float(s)
    except ValueError:
        return 0.0
    return num / 100.0 if pct else num


def parse_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("$", "")
    if s in ("", "N/A", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_int(value) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return 0


@dataclass
class ScoreBreakdown:
    score: float
    car: float            # annualized return (fraction)
    sharpe: float
    drawdown: float       # positive fraction
    win_rate: float
    ev: float             # per-trade expectancy (fraction)
    trades: int
    sharpe_factor: float
    dd_factor: float
    trade_gate: float

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "annualized_return": round(self.car, 4),
            "sharpe": round(self.sharpe, 3),
            "drawdown": round(self.drawdown, 4),
            "win_rate": round(self.win_rate, 4),
            "expectancy": round(self.ev, 4),
            "trades": self.trades,
        }


def expectancy(stats: dict, win_rate: float) -> float:
    """Per-trade expectancy from win rate and average win/loss."""
    avg_win = parse_percent(stats.get("Average Win"))
    avg_loss = parse_percent(stats.get("Average Loss"))  # already negative
    return win_rate * avg_win + (1.0 - win_rate) * avg_loss


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score(stats: dict, obj: Objective | None = None) -> ScoreBreakdown:
    obj = obj or Objective()
    car = parse_percent(stats.get("Compounding Annual Return"))
    sharpe = parse_float(stats.get("Sharpe Ratio"))
    drawdown = abs(parse_percent(stats.get("Drawdown")))
    win_rate = parse_percent(stats.get("Win Rate"))
    trades = parse_int(stats.get("Total Orders")) // 2  # round trips
    ev = expectancy(stats, win_rate)

    # Sharpe multiplier: >1 rewards, <1 discounts, clamped.
    sharpe_factor = _clamp(sharpe / obj.sharpe_ref if obj.sharpe_ref else 1.0,
                           obj.sharpe_floor, obj.sharpe_ceil)
    # Drawdown penalty: more drawdown -> smaller factor in (0, 1].
    dd_factor = 1.0 / (1.0 + obj.dd_weight * drawdown)
    # Min-trades gate: scale down below the sample floor.
    trade_gate = 1.0 if trades >= obj.min_trades else (trades / obj.min_trades if obj.min_trades else 1.0)

    raw = car * sharpe_factor * dd_factor * (1.0 + obj.ev_weight * ev)
    final = raw * trade_gate

    return ScoreBreakdown(
        score=final, car=car, sharpe=sharpe, drawdown=drawdown, win_rate=win_rate,
        ev=ev, trades=trades, sharpe_factor=sharpe_factor, dd_factor=dd_factor,
        trade_gate=trade_gate,
    )
