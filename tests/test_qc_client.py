import pytest

from lab.qc_client import CompileError, QCClient, patch_dates
from tests.fakes import FakeHTTP


def test_patch_dates():
    code = "self.set_start_date(2020, 1, 1)\nself.set_end_date(2021, 6, 30)"
    out = patch_dates(code, "2022-03-04", "2024-12-31")
    assert "self.set_start_date(2022, 3, 4)" in out
    assert "self.set_end_date(2024, 12, 31)" in out


def test_create_project_and_write_file():
    http = FakeHTTP({
        ("POST", "projects/create"): {"success": True, "projects": [{"projectId": 777}]},
        ("POST", "files/update"): {"success": True},
    })
    qc = QCClient("uid", "tok", http=http)
    assert qc.create_project("My Strat") == 777
    qc.write_file(777, "main.py", "print(1)")  # should not raise


def test_run_backtest_success():
    http = FakeHTTP({
        ("POST", "compile/create"): {"success": True, "compileId": "c1"},
        ("GET", "compile/read"): {"success": True, "state": "BuildSuccess"},
        ("POST", "backtests/create"): {"success": True, "backtest": {"backtestId": "bt1"}},
        ("GET", "backtests/read"): {
            "success": True,
            "backtest": {
                "name": "run", "completed": True,
                "statistics": {"Sharpe Ratio": "1.5", "Compounding Annual Return": "20%"},
                "runtimeStatistics": {},
                "totalPerformance": {"closedTrades": [{"profitLoss": "100"}]},
            },
        },
    })
    qc = QCClient("uid", "tok", http=http)
    result = qc.run_backtest(777, "run")
    assert result.backtest_id == "bt1"
    assert result.statistics["Sharpe Ratio"] == "1.5"
    assert result.closed_trades == [{"profitLoss": "100"}]
    assert "project/777/bt1" in result.url


def test_compile_error_raised():
    http = FakeHTTP({
        ("POST", "compile/create"): {"success": True, "compileId": "c1"},
        ("GET", "compile/read"): {"success": True, "state": "BuildError",
                                  "logs": ["SyntaxError: bad"]},
    })
    qc = QCClient("uid", "tok", http=http)
    with pytest.raises(CompileError):
        qc.run_backtest(777, "run")


def test_runtime_error_raised():
    http = FakeHTTP({
        ("POST", "compile/create"): {"success": True, "compileId": "c1"},
        ("GET", "compile/read"): {"success": True, "state": "BuildSuccess"},
        ("POST", "backtests/create"): {"success": True, "backtest": {"backtestId": "bt1"}},
        ("GET", "backtests/read"): {"success": True, "backtest": {
            "error": "KeyError: symbol", "completed": False}},
    })
    qc = QCClient("uid", "tok", http=http)
    with pytest.raises(RuntimeError) as exc:
        qc.run_backtest(777, "run")
    assert "KeyError" in str(exc.value)
