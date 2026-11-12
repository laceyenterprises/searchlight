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
    if tied:
        out.append({"q": "Most useful findings", "who": "No clear winner", "big": "", "unit": "",
                    "top": [(r["arm"], f"{r['value']:.1f}") for r in rows[:3]],
                    "ctx": f"Per question. The leader's range overlaps those of {tied} others."})
    else:
        out.append({"q": "Most useful findings", "who": top["arm"], "big": f"{top['value']:.1f}", "unit": "per question",
                    "ctx": "Clear of every other service."})
    unique = sorted(((m.get("unique_insights") or {}, arm) for arm, m in metrics.items()
                     if (m.get("unique_insights") or {}).get("value") is not None),
                    key=lambda x: (-x[0]["value"], x[1]))
    if unique:
        lead, arm = unique[0]
        lo = (lead.get("ci95") or [lead["value"]])[0]
        tied = sum(1 for m, _ in unique[1:] if (m.get("ci95") or [0, m["value"]])[1] >= lo)
        if tied:
            out.append({"q": "Most found by no one else", "who": "No clear winner", "big": "", "unit": "",
                        "top": [(a, str(m["value"])) for m, a in unique[:3]],
                        "ctx": f"Unique findings. The leader's range overlaps those of {tied} others."})
        else:
            out.append({"q": "Most found by no one else", "who": arm, "big": str(lead["value"]), "unit": "unique findings",
                        "ctx": "Clear of every other service."})
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
    top = rows[0]
    tied = sum(1 for r in rows[1:] if r["hi"] >= top["lo"])
    if tied:
        return (f"No clear winner: {top['arm']} found the most useful findings per question ({top['value']:.1f}), "
                f"but its range overlaps {tied} of the other {len(rows) - 1} services'.")
    return f"{top['arm']} found the most useful findings per question ({top['value']:.1f}); no other service comes close."


# The search and research glyphs from design/glyphs (results-list, answer), inlined so the file stays offline.
KIND_GLYPH = {
    "search": '<path d="M8 6h13.5M8 12h13.5M8 18h13.5M2.5 12h3M2.5 18h3"/><path d="M2.5 6h3" class="acc"/>',
    "research": '<path d="M3 4.5h18v11.5h-9.5L7 20.5V16H3z"/><path d="M7 8.5h10"/><path d="M7 12h6" class="acc"/>',
}


def kind_icon(kind: str) -> str:
    label = "search: hands back results" if kind == "search" else "research: writes an answer"
    return (f'<svg class="kd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" role="img" '
            f'aria-label="{label}"><title>{label}</title>{KIND_GLYPH[kind]}</svg>')


def caveats(summary: Mapping[str, Any]) -> list[str]:
    """Three plain lines a reader needs before comparing services; the recorded caveats sit under Full data."""
    rows = ranked(summary)
    most = max([r["questions"] for r in rows] + [summary["set"]["questions"]])
    subset = [r for r in rows if r["questions"] < most]
    out = [f"One run on one day, with {most} questions at most: treat small gaps as noise."]
    if subset:
        fewest = min(r["questions"] for r in subset)
        out.append(f"{len(subset)} services ran only {fewest} of the {most} questions, so compare per question.")
    out.append("Models extracted, checked and valued every claim; a service earns a finding only if its own cited page "
               "supports it.")
    return out[:3]


def _rank_item(i: int, r: dict[str, Any], scale: float) -> str:
    def pos(v):
        return f"{100 * v / scale:.1f}%"
    kind = "research" if r["kind"] == "research" else "search"
    questions = "question" if r["questions"] == 1 else "questions"
    tag = '<span class="kind">imported</span>' if r["imported"] else ""
    return (f'<li data-kind="{kind}"><span class="r">{i}</span>'
            f'<span class="n">{kind_icon(kind)}{escape(r["arm"])}{tag}</span>'
            f'<span class="v">{r["value"]:.1f}</span>'
            f'<span class="t" role="img" aria-label="{escape(r["arm"])}: {r["value"]:.1f} useful findings per question, '
            f'95% range {r["lo"]:.1f} to {r["hi"]:.1f}"><b style="left:{pos(r["lo"])};width:'
            f'{100 * (r["hi"] - r["lo"]) / scale:.1f}%"></b><i style="left:{pos(r["value"])}"></i></span>'
            f'<span class="q">{r["questions"]} {questions}</span></li>')


def digest_html(summary: Mapping[str, Any], top: int = 5) -> str:
    rows = ranked(summary)
    scale = max([r["hi"] for r in rows] + [1.0])
    lines = "".join(finish_item(x) for x in finish(summary, rows))
    head = "".join(_rank_item(i, r, scale) for i, r in enumerate(rows[:top], start=1))
    rest = "".join(_rank_item(i, r, scale) for i, r in enumerate(rows[top:], start=top + 1))
    more = (f'<details class="more-rows"><summary>All {len(rows)} services</summary><ol class="rank" start="{top + 1}">'
            f'{rest}</ol></details>') if rest else ""
    read = "".join(f"<li>{escape(c)}</li>" for c in caveats(summary))
    return ('<section class="digest" aria-label="The takeaway"><p class="eyebrow">At the top</p>'
            f'<ol class="finish">{lines}</ol>'
            f'<div class="readfirst"><h2>Before you compare</h2><ul>{read}</ul></div>'
            '<div class="ranked"><h2>Ranked by useful findings per question</h2>'
            '<p class="lede">The value of every finding a service earned on its own cited page, averaged over the '
            'questions it ran. The bar is its 95% range; where ranges overlap, the test cannot separate them.</p>'
            f'<p class="keys"><span>{kind_icon("search")} search hands back results</span> <span>'
            f'{kind_icon("research")} research writes an answer</span></p><ol class="rank">{head}</ol>{more}</div></section>')


def finish_item(x: Mapping[str, Any]) -> str:
    """One line of the finish: a leader and its number, or no clear winner and the top three."""
    if x.get("top"):
        body = '<ol class="top3">' + "".join(f'<li><span>{escape(a)}</span><b>{escape(v)}</b></li>' for a, v in x["top"])
        body += "</ol>"
    else:
        body = f'<span class="big">{escape(x["big"])}<small>{escape(x["unit"])}</small></span>'
    return (f'<li><span class="q">{escape(x["q"])}</span><span class="who">{escape(x["who"])}</span>{body}'
            f'<span class="ctx">{escape(x["ctx"])}</span></li>')


def render_dashboard(summary: Mapping[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Inside <script type="application/json"> only "</" could end the element early.
    data = data.replace("<", "\\u003c")
    title = f"{summary['set']['title']} ({summary['run']['run_date']})"
    return (template.replace("<title>Insight bake-off</title>", f"<title>{escape(title)}</title>")
            .replace(DIGEST, digest_html(summary), 1)
            .replace(PLACEHOLDER, data))
