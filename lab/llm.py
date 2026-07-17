"""Model router — four tiers over the Anthropic API.

  frontier -> analyst (finding improvements) + ideation      (Fable 5)
  strong   -> anti-cheat adversarial review                  (Opus 4.8)
  mid      -> code generation + compile/runtime-error repair  (Sonnet 5)
  cheap    -> extraction, naming, log/memory summarization    (Haiku 4.5)

Request shaping is per model family:
  * Fable/Mythos: thinking is always on (omit the param); drive depth with
    output_config.effort; wire server-side refusal fallbacks to `strong` so a
    classifier false-positive on a strategy prompt doesn't stall the loop.
  * Opus 4 / Sonnet 5: adaptive thinking + effort.
  * Haiku (and other older tiers): plain request (no thinking/effort).

The Anthropic client is injectable so callers can unit-test without a key.
"""

from __future__ import annotations

from dataclasses import dataclass

# Approximate USD per 1M tokens (input, output) for the spend guard/report.
PRICES = {
    "frontier": (10.0, 50.0),
    "strong": (5.0, 25.0),
    "mid": (3.0, 15.0),
    "cheap": (1.0, 5.0),
}

FALLBACK_BETA = "server-side-fallback-2026-06-01"


@dataclass
class LLMResponse:
    text: str
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = ""


def _fam(model: str) -> dict:
    m = (model or "").lower()
    return {
        "fable": ("fable" in m) or ("mythos" in m),
        "opus4": "opus-4" in m,
        "sonnet5": "sonnet-5" in m,
        "sonnet46": "sonnet-4-6" in m,
        "haiku": "haiku" in m,
    }


class LLM:
    def __init__(self, config, *, client=None, store=None):
        self.cfg = config
        self.models = config.models
        self._client = client
        self.store = store
        self.campaign_id = None
        self.generation_id = None
        self.total_input = 0
        self.total_output = 0

    # ── client ────────────────────────────────────────────────────────────────

    def client(self):
        if self._client is None:
            import anthropic  # lazy: tests inject a fake client and never import
            self._client = anthropic.Anthropic(api_key=self.cfg.anthropic_api_key or None)
        return self._client

    def model_for(self, tier: str) -> str:
        return {
            "frontier": self.models.frontier,
            "ideator": self.models.ideator,
            "strong": self.models.strong,
            "mid": self.models.mid,
            "cheap": self.models.cheap,
        }[tier]

    # ── core call ─────────────────────────────────────────────────────────────

    def complete(self, tier: str, system: str, user: str, *, effort: str = "high",
                 max_tokens: int = 4000) -> LLMResponse:
        model = self.model_for(tier)
        fam = _fam(model)
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": "user", "content": user}]
        client = self.client()

        if fam["fable"]:
            resp = client.beta.messages.create(
                model=model, max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks=[{"model": self.models.strong}],
                output_config={"effort": effort},
                system=system_blocks, messages=messages,
            )
        elif fam["opus4"] or fam["sonnet5"] or fam["sonnet46"]:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                system=system_blocks, messages=messages,
            )
        else:  # haiku / older tiers — plain request
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system_blocks, messages=messages,
            )

        return self._finish(tier, model, resp)

    def _finish(self, tier: str, model: str, resp) -> LLMResponse:
        text = "".join(
            getattr(b, "text", "") for b in getattr(resp, "content", [])
            if getattr(b, "type", None) == "text"
        )
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        self.total_input += in_tok
        self.total_output += out_tok
        if self.store is not None:
            self.store.record_usage(self.campaign_id, self.generation_id, tier, model, in_tok, out_tok)
        return LLMResponse(text=text, model=model, tier=tier,
                           input_tokens=in_tok, output_tokens=out_tok,
                           stop_reason=getattr(resp, "stop_reason", "") or "")

    # ── spend guard ───────────────────────────────────────────────────────────

    def spend_usd(self) -> float:
        # Approximate — blends tiers by their share of tokens isn't tracked here,
        # so use the store if available; otherwise a rough frontier-weighted guess.
        if self.store is not None and self.campaign_id is not None:
            total = 0.0
            for row in self.store.usage_totals(self.campaign_id):
                tier = row.get("tier", "strong")
                pin, pout = PRICES.get(tier, PRICES["strong"])
                total += (row["input_tokens"] or 0) / 1e6 * pin
                total += (row["output_tokens"] or 0) / 1e6 * pout
            return total
        pin, pout = PRICES["strong"]
        return self.total_input / 1e6 * pin + self.total_output / 1e6 * pout
