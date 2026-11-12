#!/usr/bin/env python3
"""Searchlight glyphs: line glyphs drawn in code by Lacey Enterprises for Searchlight's concepts.

    python3 design/glyphs/searchlight_glyphs.py   # writes searchlight-glyphs.svg and index.json next to this file

The construction follows Agent Paddock's glyph grammar (a 24 px grid, 2 px strokes in currentColor, butt caps,
mitred joins, at most one accent), with Searchlight's own subjects. The accent reads --sl-accent (Lacey blue in
design/tokens.css; ink when unset). Signal fills read --sl-go, --sl-red and --sl-amber, with the tokens' values as
fallbacks. Standard library only.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

A = 'style="stroke:var(--sl-accent, currentColor)"'               # accent stroke
AF = 'stroke="none" style="fill:var(--sl-accent, currentColor)"'    # accent fill
INK = 'fill="currentColor" stroke="none"'                           # ink fill
GO = 'style="stroke:var(--sl-go, #00A86B)"'
RED = 'style="stroke:var(--sl-red, #E5202E)"'
AMBER = 'style="stroke:var(--sl-amber, #F5B700)"'


def lens(cx=10, cy=10, r=6, handle=True, accent_handle=False):
    """The search lens: a ring and a 45-degree handle."""
    out = f'<circle cx="{cx}" cy="{cy}" r="{r}"/>'
    if handle:
        d = f'M{cx + r * .72:.2f} {cy + r * .72:.2f}L{cx + r * .72 + 5:.2f} {cy + r * .72 + 5:.2f}'
        out += f'<path d="{d}" {A if accent_handle else ""}/>'
    return out


def page(x=5, y=2.5, w=14, h=19, fold=4):
    """A page with a folded corner."""
    return (f'<path d="M{x} {y}h{w - fold}l{fold} {fold}v{h - fold}H{x}z"/>'
            f'<path d="M{x + w - fold} {y}v{fold}h{fold}"/>')


# (id, group, meaning, markup)
GLYPHS = [
    # Who and what is tested
    ("agent", "cast", "Agent: the AI coding agent doing the job (Claude Code, Codex)",
     '<rect x="2.5" y="4" width="19" height="16" rx="2"/><path d="M6 10l3 2.5L6 15"/>'
     f'<path d="M11 15h6" {A}/>'),
    ("provider", "cast", "Search provider: the search service plugged into the agent",
     '<path d="M6.5 9.5h11v4a5.5 5.5 0 0 1-11 0zM12 19v3"/>'
     f'<path d="M9 3v6.5M15 3v6.5" {A}/>'),
    ("setup", "cast", "Search setup: one way of giving the agent search; the only thing that changes between runs",
     '<rect x="2.5" y="4" width="19" height="16" rx="2"/><path d="M6 9h4M14 9h4M6 15h12"/>'
     f'<circle cx="12" cy="9" r="2" {AF}/>'),
    ("built-in", "cast", "Built-in search: the search the agent ships with",
     '<rect x="2.5" y="4" width="19" height="16" rx="2"/>'
     f'<circle cx="11" cy="11.5" r="3.5" {A}/><path d="M13.6 14.1l3 3" {A}/>'),
    ("no-search", "cast", "No search: the control run that answers from memory alone",
     lens(10.5, 10.5, 6.5) + '<path d="M3.5 3.5l17 17" style="stroke:var(--sl-accent, currentColor)"/>'),
    ("model", "cast", "Model: the AI model inside the agent, whose knowledge stops at a training cutoff",
     '<rect x="6" y="6" width="12" height="12" rx="1.5"/>'
     '<path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>'
     f'<rect x="9.5" y="9.5" width="5" height="5" {AF}/>'),
    # The job
    ("brief", "job", "Brief: a short research report that ends in a decision and cites its sources",
     '<rect x="4.5" y="3" width="15" height="18" rx="2"/><path d="M8 7.5h8M8 11h8M8 14.5h4"/>'
     f'<path d="M13 17.5l1.6 1.6 3-3" {A}/>'),
    ("code-fix", "job", "Code fix: change real code so its hidden tests pass",
     '<path d="M8 6.5L2.5 12 8 17.5M16 6.5l5.5 5.5-5.5 5.5"/>'
     f'<path d="M13.5 4l-3 16" {A}/>'),
    ("cutoff", "job", "Training cutoff: the date the model's knowledge ends; Searchlight's jobs are about what came after",
     '<path d="M2.5 18h19M5 18v-3M9 18v-3"/>'
     f'<path d="M13 3.5v17" {A}/><circle cx="18" cy="10" r="2.2" {INK}/>'),
    ("query", "job", "Query: what the agent typed into search",
     '<rect x="2" y="7" width="20" height="10" rx="5"/><path d="M6 12h6"/>'
     f'<circle cx="16.5" cy="12" r="2" {A}/>'),
    ("source", "job", "Source: the page a claim comes from",
     page() + f'<path d="M8.5 12h7" {A}/><path d="M8.5 16h7"/>'),
    ("results-list", "job", "Search setup: hands back a list of results for the agent to read",
     '<path d="M8 6h13.5M8 12h13.5M8 18h13.5"/>' + f'<path d="M2.5 6h3" {A}/>'
     + '<path d="M2.5 12h3M2.5 18h3"/>'),
    ("answer", "job", "Research setup: hands back a written answer with its sources",
     '<path d="M3 4.5h18v11.5h-9.5L7 20.5V16H3z"/><path d="M7 8.5h10"/>'
     f'<path d="M7 12h6" {A}/>'),
    # Running it fairly
    ("run", "fair", "Run: one task, one search setup, one repetition, graded once",
     '<circle cx="12" cy="12" r="9"/>' f'<path d="M10 8l5.5 4-5.5 4z" {AF}/>'),
    ("repeat", "fair", "Repeat: every task runs three times per setup",
     '<rect x="2.5" y="2.5" width="13" height="13" rx="1.5"/><path d="M19 6v13H6"/>'
     f'<path d="M15.5 9.5v6h-6" {A}/>'),
    ("sealed", "fair", "Sealed workspace: a fresh, isolated workspace for every run",
     '<path d="M12 2.5l8.5 4.5v10L12 21.5 3.5 17V7z"/><path d="M3.5 7L12 11.5 20.5 7M12 11.5v10"/>'
     f'<path d="M12 11.5l8.5-4.5" {A}/>'),
    ("same-but-one", "fair", "Change one thing: the agent, model and instructions stay fixed; only the search setup changes",
     '<rect x="2.5" y="3" width="5" height="18" rx="1"/><rect x="9.5" y="3" width="5" height="18" rx="1"/>'
     f'<rect x="16.5" y="3" width="5" height="18" rx="1" {A}/>'),
    ("blind", "fair", "Blind grading: the grader never sees which setup produced the answer",
     '<path d="M2 12c2.6-4.3 6-6.5 10-6.5s7.4 2.2 10 6.5c-2.6 4.3-6 6.5-10 6.5S4.6 16.3 2 12z"/>'
     '<circle cx="12" cy="12" r="3"/>' f'<path d="M4 20L20 4" {A}/>'),
    ("judge", "fair", "Judge: an AI model that checks each answer against its sources",
     '<path d="M12 3v18M7 21h10M4 6.5h16"/>'
     '<path d="M4 6.5L1.5 13a3 3 0 0 0 5 0zM20 6.5L17.5 13a3 3 0 0 0 5 0z"/>'
     f'<circle cx="12" cy="4" r="1.8" {AF}/>'),
    ("pre-registered", "fair", "Pre-registered: the questions and scoring rules were fixed before any result was seen",
     '<rect x="4.5" y="3" width="11" height="15" rx="1.5"/><path d="M7.5 7.5h5M7.5 11h5"/>'
     f'<rect x="13" y="14" width="8" height="7" rx="1" {A}/><path d="M14.5 14v-2a2.5 2.5 0 0 1 5 0v2" {A}/>'),
    ("holdout", "fair", "Holdout: questions kept back from tuning, so a result cannot be fitted to them",
     '<rect x="2.5" y="5" width="13" height="15" rx="1.5"/><path d="M6 2.5h12.5a3 3 0 0 1 3 3v9"/>'
     f'<path d="M6 12.5h6" {A}/><path d="M6 16h4"/>'),
    # Grading
    ("verified", "grading", "Verified: the claim was checked on the page it cites and holds up",
     '<circle cx="12" cy="12" r="9"/>' f'<path d="M7.5 12.3l3 3 6-6.3" {GO}/>'),
    ("unsupported", "grading", "Unsupported: the cited page does not back the claim",
     '<circle cx="12" cy="12" r="9"/>' f'<path d="M8.5 8.5l7 7M15.5 8.5l-7 7" {RED}/>'),
    ("unverifiable", "grading", "Unverifiable: the cited page could not be read, so the claim was not credited",
     '<circle cx="12" cy="12" r="9"/>'
     f'<path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7v.5M12 16.5v1.5" {AMBER}/>'),
    ("strict-credit", "grading", "Strict credit: a provider earns a finding only if its own cited page supports it",
     page() + f'<path d="M8.5 14l2.5 2.5 4.5-5" {A}/>'),
    ("insight", "grading", "Insight: a verified finding that would change a decision, valued 1 to 3",
     '<path d="M12 2.5l2.4 6.1 6.6.5-5 4.3 1.6 6.4L12 16.3l-5.6 3.5L8 13.4 3 9.1l6.6-.5z"/>'
     f'<circle cx="12" cy="12" r="1.8" {AF}/>'),
    ("unique", "grading", "Unique: a finding only one provider earned",
     f'<circle cx="5" cy="6" r="1.8" {INK}/><circle cx="12" cy="6" r="1.8" {INK}/><circle cx="19" cy="6" r="1.8" {INK}/>'
     f'<circle cx="5" cy="13" r="1.8" {INK}/><circle cx="12" cy="13" r="1.8" {INK}/>'
     f'<path d="M19 9.5l3.5 3.5-3.5 3.5-3.5-3.5z" {AF}/>'
     f'<circle cx="5" cy="20" r="1.8" {INK}/><circle cx="12" cy="20" r="1.8" {INK}/><circle cx="19" cy="20" r="1.8" {INK}/>'),
    ("overlap", "grading", "Overlap: findings two providers both earned",
     '<circle cx="9" cy="12" r="6.5"/><circle cx="15" cy="12" r="6.5"/>'
     f'<path d="M12 6.2a6.5 6.5 0 0 1 0 11.6 6.5 6.5 0 0 1 0-11.6z" {AF}/>'),
    ("gap-closed", "grading", "Gap closed: how far a setup got from the no-search score toward the answer-handed-over score",
     '<path d="M3 20.5h18M3 3.5h18"/>' '<rect x="8" y="3.5" width="8" height="17"/>'
     f'<rect x="8" y="9" width="8" height="11.5" {AF}/>'),
    # Reading the results
    ("ranking", "reading", "Ranking: setups in order of their score",
     f'<path d="M3 5.5h18" {A}/><path d="M3 12h13M3 18.5h8"/>'),
    ("range", "reading", "95% range: where the true score most likely sits",
     f'<path d="M3.5 7v10M20.5 7v10M3.5 12h17"/><circle cx="14" cy="12" r="2.8" {AF}/>'),
    ("tie", "reading", "Tie: two ranges overlap, so the data cannot separate them",
     '<path d="M2.5 8h13M2.5 5.5v5M15.5 5.5v5M8.5 16h13M8.5 13.5v5M21.5 13.5v5"/>'
     f'<path d="M8.5 8h7M8.5 16h7" stroke-width="3" {A}/>'),
    ("tokens", "reading", "Tokens: the units AI models bill by",
     '<rect x="2.5" y="8" width="5" height="8" rx="1"/><rect x="16.5" y="8" width="5" height="8" rx="1"/>'
     f'<rect x="9.5" y="8" width="5" height="8" rx="1" {AF}/>'),
    ("cost", "reading", "Cost: what a run or a study spent, in dollars",
     '<circle cx="12" cy="12" r="9"/><path d="M15 8.5h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9"/>'
     f'<path d="M12 5.5v3M12 16.5v3" {A}/>'),
    ("latency", "reading", "Latency: how long a provider took to answer",
     '<circle cx="12" cy="13.5" r="7.5"/><path d="M9.5 2.5h5M12 2.5V6M18.2 6.8l1.6-1.6"/>'
     f'<path d="M12 13.5l3.2-3.6" {A}/>'),
    ("caveat", "reading", "Caveat: a note on what a number does and does not show",
     '<path d="M12 3L22 20.5H2z"/>' f'<path d="M12 9.5v5.5M12 17v1.5" {AMBER}/>'),
    ("filter", "reading", "Filter: narrow the view to one agent, provider or benchmark",
     '<path d="M3 4.5h18l-7 8v6l-4 2v-8z"/>' f'<path d="M6.5 8.5h11" {A}/>'),
    # The publication
    ("benchmark", "publication", "Benchmark: a fixed set of jobs, run on every search setup",
     '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 17v-4M12 17V8"/>'
     f'<path d="M17 17V5.5" {A}/>'),
    ("study", "publication", "Study: one fixed set of questions sent to every search and research service",
     '<path d="M9 2.5h6M10 2.5v6.5L4 19a1.6 1.6 0 0 0 1.4 2.5h13.2A1.6 1.6 0 0 0 20 19l-6-10V2.5"/>'
     f'<path d="M6.5 15h11" {A}/>'),
    ("report", "publication", "Report: the published record of a benchmark or study, with its data",
     page() + '<path d="M8.5 17v-3M12 17v-6"/>' f'<path d="M15.5 17V9.5" {A}/>'),
    ("method", "publication", "Methodology: how the tests are built, run and graded",
     '<path d="M12 6.5C10 5 7 4.5 3 4.5v14c4 0 7 .5 9 2 2-1.5 5-2 9-2v-14c-4 0-7 .5-9 2zM12 6.5v14"/>'
     f'<path d="M15.5 5.6v5.9l1.5-1.5 1.5 1.5V5.2" {A}/>'),
    ("walkthrough", "publication", "How it works: a test, one step at a time",
     '<circle cx="4" cy="19" r="1.8"/>' f'<circle cx="9" cy="12" r="1.6" {INK}/><circle cx="15" cy="12" r="1.6" {INK}/>'
     '<path d="M5.2 17.6L8 13.4M10.6 12h2.8"/>' f'<path d="M16.2 10.6L19.5 5.5" {A}/>'
     f'<circle cx="20" cy="4.5" r="1.8" {AF}/>'),
    ("learn", "publication", "Learn: the ideas behind the numbers, one at a time",
     '<path d="M9 17.5h6M9.5 21h5M9 17.5c0-2.5-3.5-4-3.5-8a6.5 6.5 0 0 1 13 0c0 4-3.5 5.5-3.5 8"/>'
     f'<path d="M10.5 10.5L12 13l1.5-2.5" {A}/>'),
    ("open-source", "publication", "Open: the tasks, the code and the data are published",
     '<path d="M8 7L3 12l5 5M16 7l5 5-5 5"/>' f'<path d="M10.5 12h3" {A}/>'),
    ("contact", "publication", "Contact: questions, corrections and result challenges",
     '<path d="M3 4.5h18v11.5h-9.5L7 20.5V16H3z"/>' f'<path d="M7 10.5h.5M11.75 10.5h.5M16.5 10.5h.5" {A}/>'),
    # Interface
    ("menu-open", "ui", "Menu: open the site map", '<path d="M3 6.5h18M3 12h18M3 17.5h18"/>'),
    ("menu-close", "ui", "Close the menu", '<path d="M5.5 5.5l13 13M18.5 5.5l-13 13"/>'),
    # The beam: Searchlight's own motif, used only where it explains finding something
    ("beam", "beam", "Find: a search lights up the page that holds the answer",
     '<path d="M3 16.5a3.5 3.5 0 1 0 7 0 3.5 3.5 0 1 0-7 0z"/>'
     f'<path d="M8.5 13.5L15 3.5l6.5 6.5-11 4.3z" {AF}/>'),
]

# The logo options. Each is a 24 px mark: ink plus Lacey blue, legible at 16 px.
LOGOS = [
    ("logo-lamp", "Lamp and beam: the searchlight itself. A round lamp casts one wide Lacey blue beam up and to the "
     "right, toward what it finds.",
     '<path d="M7 17L12.81 1.03A17 17 0 0 1 22.97 11.19z" stroke="none" style="fill:var(--sl-accent, currentColor)"/>'
     '<circle cx="7" cy="17" r="6" fill="currentColor" stroke="none"/>'
     '<circle cx="7" cy="17" r="2.2" stroke="none" style="fill:var(--paper, #FFFFFF)"/>'),
    ("logo-lens", "Lens and beam: the search lens, lit. The beam leaves the lens like light from a lamp.",
     '<path d="M8.5 15.5L13.97 .46A16 16 0 0 1 23.54 10.03z" stroke="none" style="fill:var(--sl-accent, currentColor)"/>'
     '<circle cx="8.5" cy="15.5" r="5" stroke-width="2.6" style="fill:var(--paper, #FFFFFF)"/>'
     '<path d="M4.9 19.1L1.5 22.5" stroke-width="2.6"/>'),
    ("logo-tile", "Tile: the lamp and beam on a solid ink tile, the boldest at favicon size.",
     '<rect x="1" y="1" width="22" height="22" rx="5" fill="currentColor" stroke="none"/>'
     '<path d="M6 18L10.4 1.58A17 17 0 0 1 22.42 13.6z" stroke="none" style="fill:var(--sl-accent, currentColor)"/>'
     '<circle cx="6" cy="18" r="3.2" stroke="none" style="fill:var(--paper, #FFFFFF)"/>'),
]

SYMBOL = ('<symbol id="{id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="butt" stroke-linejoin="miter">{body}</symbol>')


def sprite(include_logos=True):
    rows = [SYMBOL.format(id=g[0], body=g[3]) for g in GLYPHS]
    if include_logos:
        rows += [SYMBOL.format(id=g[0], body=g[2]) for g in LOGOS]
    return '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n' + '\n'.join(rows) + '\n</svg>\n'


def logo_svg(logo_id, size=32, label='Searchlight'):
    """A standalone mark, for logo.svg and the favicon. It follows the viewer's theme: ink and the lamp hole swap on
    a dark browser chrome, and the beam stays Lacey blue."""
    body = next(g[2] for g in LOGOS if g[0] == logo_id)
    body = (body.replace('fill="currentColor"', 'class="i"').replace('var(--sl-accent, currentColor)', '#007BFF')
            .replace('style="fill:var(--paper, #FFFFFF)"', 'class="h"'))
    style = ('<style>.i{fill:#111111}.h{fill:#FFFFFF}[stroke-width]{stroke:#111111}'
             '@media (prefers-color-scheme:dark){.i{fill:#F2F4F7}.h{fill:#0C0F13}[stroke-width]{stroke:#F2F4F7}}</style>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" height="{size}" role="img" '
            f'aria-label="{label}" fill="none" stroke-width="2" stroke-linecap="butt" '
            f'stroke-linejoin="miter">{style}{body}</svg>')


def main():
    (HERE / "searchlight-glyphs.svg").write_text(sprite(), encoding="utf-8")
    index = [{"id": g[0], "group": g[1], "meaning": g[2]} for g in GLYPHS]
    index += [{"id": g[0], "group": "logo", "meaning": g[1]} for g in LOGOS]
    (HERE / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"{len(GLYPHS)} glyphs and {len(LOGOS)} logo options -> searchlight-glyphs.svg, index.json")


if __name__ == "__main__":
    main()
