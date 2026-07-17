"""Render HTML reports and email snippets from campaign data.

Reports are archived under data/reports and linked from emails; each includes
the real QuantConnect backtest URLs so you can open any run on QC's own site.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from .config import REPORTS_DIR

_VERDICT_COLOR = {"clean": "#1a7f37", "suspicious": "#9a6700", "cheating": "#cf222e"}


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _badge(verdict: str) -> str:
    v = (verdict or "").lower()
    color = _VERDICT_COLOR.get(v, "#57606a")
    label = v or "not-checked"
    return (f'<span style="background:{color};color:#fff;padding:2px 8px;'
            f'border-radius:10px;font-size:12px;font-weight:600">{_esc(label)}</span>')


def _fmt_pct(x) -> str:
    try:
        return f"{float(x):.1%}"
    except (TypeError, ValueError):
        return _esc(x)


def _leaderboard_rows(gens: list[dict]) -> str:
    rows = []
    for g in gens:
        sb = g.get("score_breakdown") or {}
        val = g.get("validation") or {}
        url = g.get("url") or ""
        link = f'<a href="{_esc(url)}">QC</a>' if url else "—"
        rows.append(
            "<tr>"
            f"<td>{_esc(g.get('gen_number'))}</td>"
            f"<td>{_esc(g.get('name'))}</td>"
            f"<td style='text-align:right'>{_esc(round(g.get('score') or 0, 3))}</td>"
            f"<td style='text-align:right'>{_fmt_pct(sb.get('annualized_return'))}</td>"
            f"<td style='text-align:right'>{_esc(sb.get('sharpe'))}</td>"
            f"<td style='text-align:right'>{_fmt_pct(sb.get('drawdown'))}</td>"
            f"<td style='text-align:right'>{_esc(sb.get('trades'))}</td>"
            f"<td>{_badge(val.get('verdict'))}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _flags_html(validation: dict) -> str:
    flags = (validation or {}).get("flags") or []
    if not flags:
        return "<p>No anti-cheat flags.</p>"
    items = "".join(
        f"<li><b>{_esc(f.get('severity'))}</b> · {_esc(f.get('category'))}: "
        f"{_esc(f.get('detail'))}</li>"
        for f in flags
    )
    return f"<ul>{items}</ul>"


def campaign_report_html(campaign: dict, gens: list[dict], usage: list[dict]) -> str:
    ranked = sorted([g for g in gens if g.get("score") is not None],
                    key=lambda g: g["score"], reverse=True)
    best = ranked[0] if ranked else None
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    best_block = "<p>No completed generations.</p>"
    if best:
        val = best.get("validation") or {}
        best_block = f"""
        <h2>Best strategy — {_esc(best.get('name'))} {_badge(val.get('verdict'))}</h2>
        <p><b>Score:</b> {_esc(round(best.get('score') or 0, 3))} &nbsp;|&nbsp;
           <a href="{_esc(best.get('url') or '')}">Open backtest on QuantConnect</a></p>
        <p><b>Hypothesis:</b> {_esc(best.get('hypothesis'))}</p>
        <h3>Anti-cheat</h3>
        {_flags_html(val)}
        {f"<p><b>AI reviewer:</b> {_esc(val.get('ai_reasons'))}</p>" if val.get('ai_reasons') else ""}
        <h3>main.py</h3>
        <pre style="background:#f6f8fa;padding:12px;overflow:auto;border-radius:6px">{_esc(best.get('code'))}</pre>
        """

    usage_rows = "".join(
        f"<tr><td>{_esc(u.get('model'))}</td><td>{_esc(u.get('tier'))}</td>"
        f"<td style='text-align:right'>{_esc(u.get('calls'))}</td>"
        f"<td style='text-align:right'>{_esc(u.get('input_tokens'))}</td>"
        f"<td style='text-align:right'>{_esc(u.get('output_tokens'))}</td></tr>"
        for u in (usage or [])
    ) or "<tr><td colspan='5'>No usage recorded.</td></tr>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>BullyQuant campaign {_esc(campaign.get('id'))}</title></head>
<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1f2328">
<h1>BullyQuant — Campaign #{_esc(campaign.get('id'))}</h1>
<p style="color:#57606a">{generated}</p>
<p><b>Objective:</b> {_esc(campaign.get('objective'))}</p>
<p><b>Status:</b> {_esc(campaign.get('status'))} &nbsp;|&nbsp;
   <b>Generations:</b> {_esc(len(gens))} &nbsp;|&nbsp;
   <b>Best score:</b> {_esc(round(campaign.get('best_score') or 0, 3))}</p>

<h2>Leaderboard</h2>
<table style="border-collapse:collapse;width:100%" border="0">
<thead><tr style="text-align:left;border-bottom:2px solid #d0d7de">
<th>Gen</th><th>Name</th><th style="text-align:right">Score</th>
<th style="text-align:right">Ann.Ret</th><th style="text-align:right">Sharpe</th>
<th style="text-align:right">MaxDD</th><th style="text-align:right">Trades</th>
<th>Anti-cheat</th><th>Link</th></tr></thead>
<tbody>{_leaderboard_rows(ranked)}</tbody>
</table>

{best_block}

<h2>Model usage</h2>
<table style="border-collapse:collapse;width:100%" border="0">
<thead><tr style="text-align:left;border-bottom:2px solid #d0d7de">
<th>Model</th><th>Tier</th><th style="text-align:right">Calls</th>
<th style="text-align:right">Input tok</th><th style="text-align:right">Output tok</th></tr></thead>
<tbody>{usage_rows}</tbody>
</table>
</body></html>"""


def write_report(campaign: dict, gens: list[dict], usage: list[dict],
                 out_dir: Path | None = None) -> Path:
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"campaign_{campaign.get('id')}.html"
    path.write_text(campaign_report_html(campaign, gens, usage), encoding="utf-8")
    return path


def alert_html(kind: str, campaign: dict, gen: dict) -> str:
    """Small HTML body for a winner/flag alert email."""
    val = gen.get("validation") or {}
    sb = gen.get("score_breakdown") or {}
    headline = {
        "winner": "New validated leader",
        "flag": "Strong performer flagged by anti-cheat",
    }.get(kind, "Update")
    return f"""<!doctype html><html><body style="font-family:sans-serif;max-width:640px;margin:16px auto">
<h2>{_esc(headline)} — {_esc(gen.get('name'))} {_badge(val.get('verdict'))}</h2>
<p><b>Campaign #{_esc(campaign.get('id'))}:</b> {_esc(campaign.get('objective'))}</p>
<p><b>Score:</b> {_esc(round(gen.get('score') or 0, 3))} &nbsp;|&nbsp;
   Ann.Ret {_fmt_pct(sb.get('annualized_return'))} &nbsp;|&nbsp;
   Sharpe {_esc(sb.get('sharpe'))} &nbsp;|&nbsp; MaxDD {_fmt_pct(sb.get('drawdown'))}</p>
<p><a href="{_esc(gen.get('url') or '')}">Open backtest on QuantConnect</a></p>
{_flags_html(val)}
{f"<p><b>AI reviewer:</b> {_esc(val.get('ai_reasons'))}</p>" if val.get('ai_reasons') else ""}
</body></html>"""
