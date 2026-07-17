from lab.agents import Analyst, Coder, Ideator, ai_reviewer
from lab.config import Config, Models
from lab.llm import LLM
from lab.text import extract_code, extract_json
from tests.fakes import FakeAnthropic


def _llm(text):
    return LLM(Config(anthropic_api_key="x", models=Models()), client=FakeAnthropic([text]))


def test_extract_code_and_json():
    assert extract_code("blah\n```python\nx = 1\n```\nafter") == "x = 1"
    assert extract_json('prefix {"verdict": "clean", "reasons": "ok"} suffix') == {
        "verdict": "clean", "reasons": "ok"}


def test_ideator_parses_name_hypothesis_code():
    text = (
        "Hypothesis: mean reversion in ETFs persists due to overreaction.\n"
        "Name: ETF Overreaction Fade\n"
        "```python\nfrom AlgorithmImports import *\nclass S(QCAlgorithm):\n    pass\n```"
    )
    idea = Ideator(_llm(text)).propose("obj")
    assert idea["name"] == "ETF Overreaction Fade"
    assert "class S(QCAlgorithm)" in idea["code"]
    assert "mean reversion" in idea["hypothesis"].lower()


def test_coder_returns_fixed_code():
    text = "The bug was X.\n```python\nfixed = True\n```"
    assert Coder(_llm(text)).fix("broken", "SyntaxError") == "fixed = True"


def test_analyst_splits_diagnosis_plan_and_code():
    text = (
        "Diagnosis: entries were too early.\n"
        "Plan: add a trend filter.\n"
        "```python\nrevised = 1\n```"
    )
    out = Analyst(_llm(text)).improve("obj", "code", {"Sharpe Ratio": "1"}, {"score": 0.2}, None)
    assert out["code"] == "revised = 1"
    assert "trend filter" in out["plan"].lower()


def test_ai_reviewer_returns_verdict_dict():
    text = '{"verdict": "suspicious", "reasons": "tiny sample"}'
    review = ai_reviewer(_llm(text))
    result = review("code", {"Sharpe Ratio": "3"}, [])
    assert result == {"verdict": "suspicious", "reasons": "tiny sample"}
