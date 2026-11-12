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


def takeaway(summary: Mapping[str, Any], rows: list[dict[str, Any]] | None = None) -> str:
    """One sentence, filled from the numbers, so it stays true as results change."""
    rows = ranked(summary) if rows is None else rows
    if not rows:
        return "No service produced verified insight in this study."
    top = rows[0]
    unique = max(rows, key=lambda r: (r["unique"], -rows.index(r)))
    most = max(r["questions"] for r in rows)
    subset = f", over the {top['questions']} of {most} questions it ran" if top["questions"] < most else ""
    first = f"{top['arm']} found the most verified insight per question ({top['value']:.1f}{subset})"
    if unique["arm"] == top["arm"]:
        first += f", and the most that no other service found ({unique['unique']})."
    else:
        first += f"; {unique['arm']} found the most that no other service found ({unique['unique']})."
    overlap = sum(1 for r in rows[1:] if r["hi"] >= top["lo"])
    if overlap:
        tail = (f" The ranges of {overlap} of the other {len(rows) - 1} services overlap the leader's, "
                "so the top places are a tie.")
    else:
        tail = " Its range clears every other arm's."
    return first + tail


def digest_html(summary: Mapping[str, Any]) -> str:
    rows = ranked(summary)
    scale = max([r["hi"] for r in rows] + [1.0])
    items = []
    for i, r in enumerate(rows, start=1):
        pos = lambda v: f"{100 * v / scale:.1f}%"  # noqa: E731
        kind = "research" if r["kind"] == "research" else "search"
        tag = "imported" if r["imported"] else kind
        items.append(
            f'<li class="{kind}" data-kind="{kind}"><span class="r">{i}</span>'
            f'<span class="n">{escape(r["arm"])}<span class="kind">{tag}</span></span>'
            f'<span class="v">{r["value"]:.1f}</span>'
            f'<span class="t" role="img" aria-label="{escape(r["arm"])}: {r["value"]:.1f} per question, 95% range '
            f'{r["lo"]:.1f} to {r["hi"]:.1f}"><b style="left:{pos(r["lo"])};width:{100 * (r["hi"] - r["lo"]) / scale:.1f}%">'
            f'</b><i style="left:{pos(r["value"])}"></i></span>'
            f'<span class="q">{r["questions"]} Qs</span></li>')
    return ('<section class="digest" aria-labelledby="digest-say"><p class="eyebrow">The takeaway</p>'
            f'<p class="say" id="digest-say">{escape(takeaway(summary, rows))}</p>'
            f'<ol class="rank">{"".join(items)}</ol>'
            '<p class="how">Verified insight per question: the value of every finding a service earned on its own '
            'cited page, averaged over the questions it ran. The bar is its 95% range; where ranges overlap, the data '
            'cannot separate the services. The views below hold every number and claim.</p></section>')


def render_dashboard(summary: Mapping[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Inside <script type="application/json"> only "</" could end the element early.
    data = data.replace("<", "\\u003c")
    title = f"{summary['set']['title']} ({summary['run']['run_date']})"
    return (template.replace("<title>Insight bake-off</title>", f"<title>{escape(title)}</title>")
            .replace(DIGEST, digest_html(summary), 1)
            .replace(PLACEHOLDER, data))
