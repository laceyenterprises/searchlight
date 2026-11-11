"""Render the self-contained interactive dashboard from a summary.

One HTML file: inline CSS and JS, the summary embedded as JSON, no network
access, so it opens from file:// and works unchanged when published.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any, Mapping

TEMPLATE = Path(__file__).with_name("dashboard.html")
PLACEHOLDER = "/*__DATA__*/"


def render_dashboard(summary: Mapping[str, Any]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Inside <script type="application/json"> only "</" could end the element early.
    data = data.replace("<", "\\u003c")
    title = f"{summary['set']['title']} ({summary['run']['run_date']})"
    return (template.replace("<title>Insight bake-off</title>", f"<title>{escape(title)}</title>")
            .replace(PLACEHOLDER, data))
