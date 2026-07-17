"""Configuration: env loading, model tiers, objective weights, guardrails.

All secrets and the recipient email come from the environment (or a gitignored
`.env`), never from committed code — so the repo is safe to publish.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
STRATEGIES_DIR = DATA_DIR / "strategies"
DB_PATH = DATA_DIR / "bullyquant.db"


# ── .env loading (no external dependency) ─────────────────────────────────────

def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (without overriding
    values already set in the real environment). Silently does nothing if absent.
    """
    path = path or (REPO_ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


# ── Model tiers ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Models:
    frontier: str = "claude-fable-5"   # analyst (improvements) + ideation
    strong: str = "claude-opus-4-8"    # anti-cheat adversarial review
    mid: str = "claude-sonnet-5"       # code generation + error repair
    cheap: str = "claude-haiku-4-5"    # extraction, naming, summaries
    ideate_with_frontier: bool = True  # else ideation uses `strong`

    @property
    def ideator(self) -> str:
        return self.frontier if self.ideate_with_frontier else self.strong


# ── Objective / scoring weights ───────────────────────────────────────────────

@dataclass(frozen=True)
class Objective:
    """Composite score: annualized return dominant, adjusted for Sharpe and
    drawdown, nudged by expectancy, gated by a minimum trade count."""
    sharpe_ref: float = 1.0       # sharpe that yields a neutral (1.0) factor
    sharpe_floor: float = 0.3     # min sharpe multiplier
    sharpe_ceil: float = 2.0      # max sharpe multiplier
    dd_weight: float = 3.0        # drawdown penalty strength
    ev_weight: float = 1.0        # per-trade expectancy nudge
    min_trades: int = 20          # below this, score is scaled down (flukes)


# ── Anti-cheat thresholds ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Thresholds:
    # A strategy scoring at/above this triggers the deep anti-cheat gate.
    validate_score: float = 0.15
    # "Too good to be true" sanity limits.
    sharpe_suspicious: float = 3.5
    car_suspicious: float = 1.00          # >100%/yr annualized
    drawdown_tiny: float = 0.02           # <2% max drawdown ...
    car_big: float = 0.30                 # ... paired with >30%/yr is suspect
    win_rate_suspicious: float = 0.90
    min_trades_sanity: int = 10
    pnl_concentration_max: float = 0.60   # one trade > 60% of gross profit
    oos_degradation_max: float = 0.50     # OOS score < 50% of in-sample = overfit


# ── Backtest windows ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Windows:
    start: str = "2022-01-01"
    end: str = "2024-01-01"
    oos_start: str = "2024-01-01"
    oos_end: str = "2026-01-01"


# ── Email ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Email:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    to: str = ""
    from_: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.to)


# ── Guardrails ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Guardrails:
    max_generations: int = 8
    max_backtests: int = 24
    plateau_patience: int = 3
    token_budget: int = 0  # 0 = unlimited


# ── Top-level config ──────────────────────────────────────────────────────────

@dataclass
class Config:
    qc_user_id: str = ""
    qc_api_token: str = ""
    anthropic_api_key: str = ""
    models: Models = field(default_factory=Models)
    objective: Objective = field(default_factory=Objective)
    thresholds: Thresholds = field(default_factory=Thresholds)
    windows: Windows = field(default_factory=Windows)
    email: Email = field(default_factory=Email)
    guardrails: Guardrails = field(default_factory=Guardrails)

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> "Config":
        if load_env_file:
            load_dotenv()
        return cls(
            qc_user_id=os.environ.get("QC_USER_ID", ""),
            qc_api_token=os.environ.get("QC_API_TOKEN", ""),
            # BQ_ANTHROPIC_API_KEY is an alias honored because some hosts reserve
            # the plain ANTHROPIC_API_KEY name for their own auth and strip it
            # from child processes; the standard name still wins when both are set.
            anthropic_api_key=(
                os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("BQ_ANTHROPIC_API_KEY")
                or ""
            ),
            models=Models(
                frontier=os.environ.get("BQ_MODEL_FRONTIER") or Models.frontier,
                strong=os.environ.get("BQ_MODEL_STRONG") or Models.strong,
                mid=os.environ.get("BQ_MODEL_MID") or Models.mid,
                cheap=os.environ.get("BQ_MODEL_CHEAP") or Models.cheap,
                ideate_with_frontier=_b("BQ_IDEATE_WITH_FRONTIER", True),
            ),
            windows=Windows(
                start=os.environ.get("BQ_START") or Windows.start,
                end=os.environ.get("BQ_END") or Windows.end,
                oos_start=os.environ.get("BQ_OOS_START") or Windows.oos_start,
                oos_end=os.environ.get("BQ_OOS_END") or Windows.oos_end,
            ),
            email=Email(
                host=os.environ.get("EMAIL_SMTP_HOST", ""),
                port=_i("EMAIL_SMTP_PORT", 587),
                user=os.environ.get("EMAIL_SMTP_USER", ""),
                password=os.environ.get("EMAIL_SMTP_PASS", ""),
                to=os.environ.get("EMAIL_TO", ""),
                from_=os.environ.get("EMAIL_FROM", "") or os.environ.get("EMAIL_SMTP_USER", ""),
            ),
            guardrails=Guardrails(
                max_generations=_i("BQ_MAX_GENERATIONS", 8),
                max_backtests=_i("BQ_MAX_BACKTESTS", 24),
                plateau_patience=_i("BQ_PLATEAU_PATIENCE", 3),
                token_budget=_i("BQ_TOKEN_BUDGET", 0),
            ),
        )

    def require_qc(self) -> None:
        if not self.qc_user_id or not self.qc_api_token:
            raise RuntimeError(
                "QuantConnect credentials missing. Set QC_USER_ID and QC_API_TOKEN "
                "in the environment or .env."
            )

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY missing. Set ANTHROPIC_API_KEY (or its "
                "BQ_ANTHROPIC_API_KEY alias) in the environment or .env — the "
                "loop needs Claude API access."
            )
