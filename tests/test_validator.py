from lab import validator
from lab.validator import (
    oos_degradation, pnl_concentration, sanity_check, static_scan, validate,
)

SURVIVORSHIP_CODE = '''
from AlgorithmImports import *
class S(QCAlgorithm):
    TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "META"]
    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2024, 1, 1)
        for t in self.TICKERS:
            self.add_equity(t, Resolution.DAILY)
    def on_data(self, data):
        for t in self.TICKERS:
            self.set_holdings(t, 0.2)
'''

CLEAN_CODE = '''
from AlgorithmImports import *
class S(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2024, 1, 1)
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
        self.rsi = self.rsi(self.spy, 14)
    def on_data(self, data):
        if self.rsi.current.value < 30:
            self.set_holdings(self.spy, 0.5)
'''

DATE_FIT_CODE = '''
from AlgorithmImports import *
class S(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2024, 1, 1)
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
    def on_data(self, data):
        if self.time > datetime(2023, 3, 10):
            self.set_holdings(self.spy, 1.5)
'''


def _cats(flags):
    return {f.category for f in flags}


def test_static_scan_detects_survivorship():
    assert "survivorship" in _cats(static_scan(SURVIVORSHIP_CODE))


def test_static_scan_detects_hardcoded_dates_and_leverage():
    cats = _cats(static_scan(DATE_FIT_CODE))
    assert "hardcoded-dates" in cats
    assert "leverage" in cats


def test_static_scan_clean_code_has_no_high_flags():
    flags = static_scan(CLEAN_CODE)
    assert all(f.severity != "high" for f in flags)


def test_pnl_concentration():
    trades = [{"profitLoss": "100"}, {"profitLoss": "5"}, {"profitLoss": "-10"}]
    conc = pnl_concentration(trades)
    assert round(conc, 3) == round(100 / 105, 3)
    assert pnl_concentration([]) == 0.0


def test_sanity_flags_suspicious_stats():
    stats = {
        "Sharpe Ratio": "5.0", "Compounding Annual Return": "150%",
        "Drawdown": "1%", "Win Rate": "95%", "Total Orders": "4",
    }
    cats = _cats(sanity_check(stats, []))
    assert "sanity-sharpe" in cats
    assert "sanity-return" in cats
    assert "sanity-winrate" in cats


def test_oos_degradation_math():
    assert oos_degradation(1.0, 0.4) == 0.6
    assert oos_degradation(1.0, 1.2) == 0.0   # improved -> no degradation
    assert oos_degradation(0.0, -1.0) == 0.0  # nothing to degrade from


def test_validate_flags_survivorship_as_not_clean():
    report = validate(SURVIVORSHIP_CODE, {"Sharpe Ratio": "2.0"}, [])
    assert report.verdict in ("suspicious", "cheating")
    assert report.trust_score < 1.0


def test_validate_ai_review_can_escalate():
    def ai(code, stats, flags):
        return {"verdict": "cheating", "reasons": "fabricated"}
    report = validate(CLEAN_CODE, {"Sharpe Ratio": "1.2"}, [], ai_review=ai)
    assert report.verdict == "cheating"
    assert report.ai_reasons == "fabricated"
