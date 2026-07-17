from lab.config import Objective
from lab.scorer import parse_percent, parse_float, parse_int, score


def test_parsers():
    assert parse_percent("15.2%") == 0.152
    assert parse_percent("-3.4%") == -0.034
    assert parse_percent("0.25") == 0.25
    assert parse_percent("N/A") == 0.0
    assert parse_float("$1,234.56") == 1234.56
    assert parse_int("42") == 42
    assert parse_int("N/A") == 0


def _stats(car="20%", sharpe="1.5", dd="10%", wr="55%", orders="80",
           avg_win="2%", avg_loss="-1%"):
    return {
        "Compounding Annual Return": car, "Sharpe Ratio": sharpe, "Drawdown": dd,
        "Win Rate": wr, "Total Orders": orders, "Average Win": avg_win,
        "Average Loss": avg_loss,
    }


def test_annualized_return_dominates():
    low = score(_stats(car="10%"))
    high = score(_stats(car="30%"))
    assert high.score > low.score


def test_drawdown_penalizes():
    shallow = score(_stats(dd="5%"))
    deep = score(_stats(dd="40%"))
    assert shallow.score > deep.score


def test_sharpe_rewards():
    poor = score(_stats(sharpe="0.3"))
    great = score(_stats(sharpe="2.5"))
    assert great.score > poor.score


def test_min_trades_gate_scales_down_flukes():
    many = score(_stats(orders="80"))   # 40 round trips
    few = score(_stats(orders="6"))     # 3 round trips -> gated
    assert few.trade_gate < 1.0
    assert few.score < many.score


def test_negative_return_is_negative_score():
    losing = score(_stats(car="-15%"))
    assert losing.score < 0


def test_breakdown_dict_shape():
    d = score(_stats()).as_dict()
    assert set(d) >= {"score", "annualized_return", "sharpe", "drawdown", "trades"}
