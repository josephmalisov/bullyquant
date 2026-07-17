from lab.config import Config, Models
from lab.llm import LLM
from lab.store import Store
from tests.fakes import FakeAnthropic


def _cfg(**model_overrides):
    return Config(anthropic_api_key="x", models=Models(**model_overrides))


def test_fable_uses_beta_with_fallbacks_and_no_thinking():
    fake = FakeAnthropic(["hello"])
    llm = LLM(_cfg(), client=fake)
    resp = llm.complete("frontier", "sys", "user")
    assert resp.text == "hello"
    # Fable calls go through the beta endpoint; capture the kwargs.
    call = fake.calls[-1]
    assert call["model"] == "claude-fable-5"
    assert call["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert "server-side-fallback-2026-06-01" in call["betas"]
    assert "thinking" not in call  # thinking is always on for Fable — omit it
    assert call["output_config"] == {"effort": "high"}


def test_opus_uses_adaptive_thinking_and_effort():
    fake = FakeAnthropic(["ok"])
    llm = LLM(_cfg(), client=fake)
    llm.complete("strong", "sys", "user", effort="xhigh")
    call = fake.calls[-1]
    assert call["model"] == "claude-opus-4-8"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "xhigh"}


def test_haiku_is_plain_request():
    fake = FakeAnthropic(["ok"])
    llm = LLM(_cfg(), client=fake)
    llm.complete("cheap", "sys", "user")
    call = fake.calls[-1]
    assert call["model"] == "claude-haiku-4-5"
    assert "thinking" not in call
    assert "output_config" not in call


def test_ideator_tier_can_fall_back_to_opus():
    fake = FakeAnthropic(["ok"])
    llm = LLM(_cfg(ideate_with_frontier=False), client=fake)
    assert llm.model_for("ideator") == "claude-opus-4-8"


def test_usage_recorded_to_store():
    fake = FakeAnthropic(["a", "b"])
    store = Store(":memory:")
    cid = store.create_campaign("obj")
    llm = LLM(_cfg(), client=fake, store=store)
    llm.campaign_id = cid
    llm.complete("cheap", "s", "u")
    llm.complete("cheap", "s", "u")
    totals = store.usage_totals(cid)
    assert totals[0]["calls"] == 2
    assert llm.total_input == 200
