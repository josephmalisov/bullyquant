from lab.config import Config, Models
from lab.llm import LLM
from lab.orchestrator import Orchestrator
from lab.qc_client import QCClient
from lab.store import Store
from tests.fakes import FakeAnthropic, FakeHTTP


def _orch(coder_response: str) -> Orchestrator:
    cfg = Config(anthropic_api_key="x", models=Models())
    store = Store(":memory:")
    llm = LLM(cfg, client=FakeAnthropic([coder_response]), store=store)
    http = FakeHTTP({
        ("POST", "files/update"): {"success": True},
        ("POST", "compile/create"): {"success": True, "compileId": "c1"},
        # First compile fails (triggers the coder), second succeeds.
        ("GET", "compile/read"): [
            {"success": True, "state": "BuildError", "logs": ["SyntaxError: bad"]},
            {"success": True, "state": "BuildSuccess"},
        ],
        ("POST", "backtests/create"): {"success": True, "backtest": {"backtestId": "bt1"}},
        ("GET", "backtests/read"): {
            "success": True,
            "backtest": {
                "name": "run", "completed": True,
                "statistics": {"Sharpe Ratio": "1.0", "Compounding Annual Return": "10%"},
                "runtimeStatistics": {}, "totalPerformance": {"closedTrades": []},
            },
        },
    })
    qc = QCClient("uid", "tok", http=http)
    return Orchestrator(cfg, store, llm, qc)


def test_build_and_backtest_repatches_dates_after_coder_fix():
    """A coder fix that drops/alters the literal set_start_date/set_end_date
    calls must not silently change the backtest window (regression: the fix
    used to be written to QC verbatim, with the dates only patched once
    before the retry loop)."""
    orch = _orch(
        "Fixed the syntax error.\n```python\n"
        "self.set_start_date(1999, 1, 1)\nself.set_end_date(1999, 12, 31)\n"
        "```"
    )
    broken_code = "self.set_start_date(2020, 1, 1)\nself.set_end_date(2020, 6, 1)\nbroken ="

    result, final_code = orch._build_and_backtest(
        777, broken_code, "run", "2022-01-01", "2023-01-01"
    )

    assert "self.set_start_date(2022, 1, 1)" in final_code
    assert "self.set_end_date(2023, 1, 1)" in final_code
    assert result.backtest_id == "bt1"
