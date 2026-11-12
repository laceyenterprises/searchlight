"""Render the self-contained interactive dashboard from a summary.

One HTML file: inline CSS and JS, the summary embedded as JSON, no network
access, so it opens from file:// and works unchanged when published. It opens
with a digest rendered here, so the takeaway and the ranking read without
JavaScript; the site's studies page uses the same digest.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Mapping

TEMPLATE = Path(__file__).with_name("dashboard.html")
PLACEHOLDER = "/*__DATA__*/"
DIGEST = "<!--__DIGEST__-->"


def ranked(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Arms by verified insight per question, with the 95% range of that average.

    The published range is a question bootstrap of the arm's total over the questions it ran, so dividing it by
    that number of questions gives the range of the per-question average."""
    arms = {a["arm_id"]: a for a in summary["arms"]}
    rows = []
    for arm in summary["ranked"]:
        metrics = summary["metrics"][arm]
        per_q = metrics.get("weighted_per_question") or {}
        value, n = per_q.get("value"), per_q.get("n") or 0
        if value is None or not n:
            continue
        ci = (metrics.get("weighted") or {}).get("ci95")
        lo, hi = (ci[0] / n, ci[1] / n) if ci else (value, value)
        meta = arms.get(arm, {})
        rows.append({"arm": arm, "value": value, "lo": lo, "hi": hi, "questions": n,
                     "unique": (metrics.get("unique_insights") or {}).get("value") or 0,
                     "kind": meta.get("operation") or "search", "imported": bool(meta.get("imported"))})
    return sorted(rows, key=lambda r: (-r["value"], r["arm"]))


def _money(value: float) -> str:
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}" if value < 10 else f"${value:,.0f}"


def finish(summary: Mapping[str, Any], rows: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """The photo finish: who found the most, who found the most nobody else did, and the best value.

    Each line names the leader, its number and how many others it cannot be told apart from."""
    rows = ranked(summary) if rows is None else rows
    if not rows:
        return []
    metrics = summary["metrics"]
    out = []
    top = rows[0]
    tied = sum(1 for r in rows[1:] if r["hi"] >= top["lo"])
    most = max(r["questions"] for r in rows)
    subset = f"Ran {top['questions']} of the {most} questions. " if top["questions"] < most else ""
    out.append({"q": "Most useful findings", "who": top["arm"], "big": f"{top['value']:.1f}", "unit": "per question",
                "ctx": subset + (f"Tied with {tied} others: their ranges overlap." if tied
                                 else "Clear of every other service.")})
    unique = sorted(((m.get("unique_insights") or {}, arm) for arm, m in metrics.items()
                     if (m.get("unique_insights") or {}).get("value") is not None),
                    key=lambda x: (-x[0]["value"], x[1]))
    if unique:
        lead, arm = unique[0]
        lo = (lead.get("ci95") or [lead["value"]])[0]
        tied = sum(1 for m, _ in unique[1:] if (m.get("ci95") or [0, m["value"]])[1] >= lo)
        out.append({"q": "Most found by no one else", "who": arm, "big": str(lead["value"]), "unit": "unique findings",
                    "ctx": f"Tied with {tied} others." if tied else "Clear of every other service."})
    value = sorted(((m["cost_per_unique_insight"]["value"], arm) for arm, m in metrics.items()
                    if (m.get("cost_per_unique_insight") or {}).get("value") is not None
                    and (m.get("unique_insights") or {}).get("value")), key=lambda x: (x[0], x[1]))
    if value:
        cost, arm = value[0]
        uniq = metrics[arm]["unique_insights"]["value"]
        out.append({"q": "Best value", "who": arm, "big": _money(cost), "unit": "per unique finding",
                    "ctx": f"{uniq} unique findings for {_money(metrics[arm]['cost_usd']['value'])} in all."})
    return out


def takeaway(summary: Mapping[str, Any], rows: list[dict[str, Any]] | None = None) -> str:
    """One sentence, filled from the numbers, so it stays true as results change."""
    rows = ranked(summary) if rows is None else rows
    if not rows:
        return "No service produced verified insight in this study."
    lines = finish(summary, rows)
    first = lines[0]
    text = f"{first['who']} found the most useful findings per question ({first['big']})"
    if len(lines) > 1 and lines[1]["who"] != first["who"]:
        text += f"; {lines[1]['who']} found the most that no one else did ({lines[1]['big']})"
    tied = sum(1 for r in rows[1:] if r["hi"] >= rows[0]["lo"])
    return text + (f". {tied} of the other {len(rows) - 1} services are tied with the leader." if tied
                   else ". No other service comes close.")


def _rank_item(i: int, r: dict[str, Any], scale: float) -> str:
    def pos(v):
        return f"{100 * v / scale:.1f}%"
    kind = "research" if r["kind"] == "research" else "search"
    tag = "imported" if r["imported"] else kind
    questions = "question" if r["questions"] == 1 else "questions"
    return (f'<li data-kind="{kind}"><span class="r">{i}</span>'
            f'<span class="n">{escape(r["arm"])}<span class="kind">{tag}</span></span>'
            f'<span class="v">{r["value"]:.1f}</span>'
            f'<span class="t" role="img" aria-label="{escape(r["arm"])}: {r["value"]:.1f} useful findings per question, '
            f'95% range {r["lo"]:.1f} to {r["hi"]:.1f}"><b style="left:{pos(r["lo"])};width:'
            f'{100 * (r["hi"] - r["lo"]) / scale:.1f}%"></b><i style="left:{pos(r["value"])}"></i></span>'
            f'<span class="q">{r["questions"]} {questions}</span></li>')


def digest_html(summary: Mapping[str, Any], top: int = 5) -> str:
    rows = ranked(summary)
    scale = max([r["hi"] for r in rows] + [1.0])
    lines = "".join(f'<li><span class="q">{escape(x["q"])}</span><span class="who">{escape(x["who"])}</span>'
                    f'<span class="big">{escape(x["big"])}<small>{escape(x["unit"])}</small></span>'
                    f'<span class="ctx">{escape(x["ctx"])}</span></li>' for x in finish(summary, rows))
    head = "".join(_rank_item(i, r, scale) for i, r in enumerate(rows[:top], start=1))
    rest = "".join(_rank_item(i, r, scale) for i, r in enumerate(rows[top:], start=top + 1))
    more = (f'<details class="more-rows"><summary>All {len(rows)} services</summary><ol class="rank" start="{top + 1}">'
            f'{rest}</ol></details>') if rest else ""
    return ('<section class="digest" aria-label="The takeaway"><p class="eyebrow">The photo finish</p>'
            f'<ol class="finish">{lines}</ol>'
            '<div class="ranked"><h2>Ranked by useful findings per question</h2>'
            '<p class="lede">The value of every finding a service earned on its own cited page, averaged over the '
            'questions it ran. The bar is its 95% range; where ranges overlap, the data cannot separate them.</p>'
            f'<ol class="rank">{head}</ol>{more}</div></section>')


def render_dashboard(summary: Mapping[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Inside <script type="application/json"> only "</" could end the element early.
    data = data.replace("<", "\\u003c")
    title = f"{summary['set']['title']} ({summary['run']['run_date']})"
    return (template.replace("<title>Insight bake-off</title>", f"<title>{escape(title)}</title>")
            .replace(DIGEST, digest_html(summary), 1)
            .replace(PLACEHOLDER, data))
