"""The AI agents: ideator, coder, analyst, and the anti-cheat AI reviewer.

Each is a thin wrapper over the LLM router that owns a system prompt and knows
how to parse the model's response. They are deliberately small so the loop logic
lives in the orchestrator.
"""

from __future__ import annotations

import json

from . import prompts
from .text import extract_code, extract_json


class Ideator:
    """Invents a brand-new strategy (FRONTIER/ideator tier)."""

    def __init__(self, llm):
        self.llm = llm

    def propose(self, objective: str, memory: str = "") -> dict:
        user = f"Objective:\n{objective}\n"
        if memory:
            user += (
                "\nMemory from past attempts in this campaign (learn from these; do NOT "
                f"repeat failed ideas, build on what improved the score):\n{memory}\n"
            )
        user += "\nInvent a new strategy for this objective."
        resp = self.llm.complete("ideator", prompts.IDEATOR_SYSTEM, user,
                                 effort="high", max_tokens=8000)
        code = extract_code(resp.text)
        name, hypothesis = _split_name_hypothesis(resp.text)
        return {"name": name, "hypothesis": hypothesis, "code": code, "raw": resp.text}


class Coder:
    """Repairs compile/runtime errors (MID tier)."""

    def __init__(self, llm):
        self.llm = llm

    def fix(self, code: str, error: str) -> str:
        user = (
            f"Current main.py:\n```python\n{code}\n```\n\n"
            f"QuantConnect error:\n{error}\n\nReturn the corrected complete main.py."
        )
        resp = self.llm.complete("mid", prompts.CODER_SYSTEM, user, effort="high", max_tokens=8000)
        return extract_code(resp.text)


class Analyst:
    """Diagnoses a result and proposes the next generation (FRONTIER tier)."""

    def __init__(self, llm):
        self.llm = llm

    def improve(self, objective: str, code: str, stats: dict, score_breakdown: dict,
                validation: dict | None) -> dict:
        parts = [
            f"Objective:\n{objective}\n",
            f"Current main.py:\n```python\n{code}\n```\n",
            f"Backtest statistics:\n{json.dumps(stats, indent=2)}\n",
            f"Composite score breakdown:\n{json.dumps(score_breakdown, indent=2)}\n",
        ]
        if validation:
            parts.append(f"Anti-cheat validation report:\n{json.dumps(validation, indent=2)}\n")
        parts.append("Diagnose and propose the single best next improvement, then give the full revised main.py.")
        resp = self.llm.complete("frontier", prompts.ANALYST_SYSTEM, "\n".join(parts),
                                 effort="xhigh", max_tokens=12000)
        code_out = extract_code(resp.text)
        diagnosis, plan = _split_diagnosis_plan(resp.text)
        return {"diagnosis": diagnosis, "plan": plan, "code": code_out, "raw": resp.text}


def ai_reviewer(llm):
    """Return a callable (code, stats, flags) -> {'verdict', 'reasons'} for the
    validator's AI review step (STRONG tier)."""
    def review(code: str, stats: dict, flags: list) -> dict:
        user = (
            f"Strategy code:\n```python\n{code}\n```\n\n"
            f"Backtest statistics:\n{json.dumps(stats, indent=2)}\n\n"
            f"Automated flags already raised:\n{json.dumps(flags, indent=2)}\n\n"
            "Give your final verdict as JSON."
        )
        resp = llm.complete("strong", prompts.VALIDATOR_SYSTEM, user, effort="high", max_tokens=2000)
        data = extract_json(resp.text)
        return {"verdict": data.get("verdict", ""), "reasons": data.get("reasons", "")}
    return review


# ── response parsing ──────────────────────────────────────────────────────────

def _before_code(text: str) -> str:
    idx = text.find("```")
    return (text[:idx] if idx != -1 else text).strip()


def _split_name_hypothesis(text: str) -> tuple[str, str]:
    """Pull a name + hypothesis out of the ideator's preamble."""
    pre = _before_code(text)
    name = ""
    hypothesis = pre
    for line in pre.splitlines():
        line = line.strip()
        m = _match_labeled(line, ("name",))
        if m:
            name = m
            break
    if not name:
        # Fall back: a short standalone line is likely the name.
        for line in pre.splitlines():
            s = line.strip().strip("*# ").strip()
            if 0 < len(s) <= 48 and 2 <= len(s.split()) <= 6:
                name = s
                break
    return (name or "AI Strategy"), hypothesis


def _split_diagnosis_plan(text: str) -> tuple[str, str]:
    pre = _before_code(text)
    diagnosis, plan = "", ""
    lower = pre.lower()
    if "plan" in lower:
        di = lower.find("diagnosis")
        pi = lower.find("plan")
        diagnosis = pre[di:pi].strip() if di != -1 else pre[:pi].strip()
        plan = pre[pi:].strip()
    else:
        diagnosis = pre
    return diagnosis, plan


def _match_labeled(line: str, labels: tuple[str, ...]) -> str:
    s = line.strip().strip("*#- ").strip()
    low = s.lower()
    for lab in labels:
        for sep in (":", "-", "—"):
            prefix = f"{lab}{sep}"
            if low.startswith(prefix):
                return s[len(prefix):].strip().strip('"')
    return ""
