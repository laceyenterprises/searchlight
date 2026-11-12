"""Build the Searchlight site, leaderboard data, infographic and share card from published reports.

The one entry point. The page code lives in ``scripts/searchlight_site/`` (data, design, layout, components, pages,
diagrams, readme); this file builds the site from it, draws the one-page infographic and the share card, and writes or
checks ``site/`` and the README images in ``docs/readme/``.

Inputs are only files in this repository: every report and insight study under ``reports/`` (discovered by its summary
schema, see ``scripts/check_reports.py``), the pinned provider arm configuration in ``config/provider-arm-tools.yaml``,
and the design system in ``design/`` (tokens, glyphs, vocabulary). Nothing is recalculated from private run data; the
only derived numbers are Wilson intervals for tables that publish counts without intervals, and they are labelled as
computed. Publishing a new report directory and rebuilding adds its page, its boards and its index entries with no edits.

    python3 scripts/build_site.py            # write site/ and docs/readme/
    python3 scripts/build_site.py --check    # fail if either is stale
    python3 scripts/build_site.py --target preview --out /tmp/preview.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import textwrap
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The names below are the generator's public surface: the tests and scripts/render_social_card.py read them here.
from searchlight_site import diagrams  # noqa: E402,F401
from searchlight_site.components import (  # noqa: E402,F401
    LEGEND, board_html, board_notes, board_says, digest, finding, measures_html, memory_clause, report_page_name, row_html,
    site_map, track)
from searchlight_site.data import (  # noqa: E402,F401
    ANALYTICS_NOTICE, ARM_LABEL, BEHAVIOR, BENCH_LABEL, EXAMPLES, GAP_PRIMARY, H2H, H2H_PROVIDERS, INSIGHT_SCHEMA,
    RECALL_MIN, REPO_URL, REPORT_TITLES, REPORTS, RERUN_NOTE, ROOT, UNSUPPORTED_MAX, WSB_PRIMARY, _catalog_prompt,
    _ceiling, _gap_pct, _gap_range, _point_pct, arm_configs, arm_id, behavior, board_caption, data_rows, discover, esc,
    harness_models, harness_name, head_to_head, heading_anchor, headline, inline, insight_studies, leader,
    leaderboards, load_reports, markdown, more_boards, parse_count, parse_pct_ci, parse_tally, render_report,
    report_title, resolve_link, results_language, slug, table_html, tables, wilson)
from searchlight_site.design import CSS, FAVICON_ID, JS, LOGO, LOGO_ID, SPRITE, TOKENS, glyphs, vocabulary  # noqa: E402,F401
from searchlight_site.layout import (  # noqa: E402,F401
    CARD_ALT_DEFAULT, CARD_SCALE, CARD_SIZE, GTM_BODY, GTM_CONTAINER_ID, GTM_HEAD, LACEY_URL, NAV, SITE_URL, _document)
from searchlight_site.pages import (  # noqa: E402,F401
    landing, learn_page, methodology_page, report_page, results_page, studies_page, study_page)
from searchlight_site import readme  # noqa: E402

PALETTE = {  # the infographic is a single-theme graphic with its own ground
    'paper': '#F5F7FA', 'surface': '#FFFFFF', 'ink': '#111111', 'ink2': '#555D67', 'rule': '#E1E5EA',
    'beam': '#007BFF', 'beam_soft': '#D7E8FF', 'beam_mid': '#8DBDFF', 'ref': '#8A949E', 'hatch': '#C5CCD3',
}


FONT = "'Inter', -apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"


MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"


COND = FONT


def forest(board: dict, x: float, y: float, width: float, row_h: float = 30, label_w: float = 112,
           value_w: float = 104, colors=PALETTE, css: bool = False,
           ticks: tuple[int, ...] = (0, 25, 50, 75, 100)) -> tuple[str, float]:
    """Dot-and-whisker chart on a fixed 0–100% scale. Returns (svg, height)."""
    c = (lambda key, fallback: f'var(--{key})') if css else (lambda key, fallback: colors[fallback])
    plot_x, plot_w = x + label_w, width - label_w - value_w
    if plot_w <= 0:
        raise ValueError('forest chart width must leave positive plotting space')
    sx = lambda pct: plot_x + plot_w * pct / 100  # noqa: E731
    rows = board['rows']
    height = row_h * len(rows) + 34
    parts = []
    for tick in ticks:
        tx = sx(tick)
        parts.append(f'<line x1="{tx:.1f}" y1="{y:.1f}" x2="{tx:.1f}" y2="{y + row_h * len(rows):.1f}" '
                     f'stroke="{c("rule", "rule")}" stroke-width="1"/>')
        parts.append(f'<text x="{tx:.1f}" y="{y + row_h * len(rows) + 18:.1f}" text-anchor="middle" '
                     f'font-family="{MONO}" font-size="11" fill="{c("ink2", "ink2")}">{tick}%</text>')
    for i, row in enumerate(rows):
        cy = y + row_h * i + row_h / 2
        lo, hi = row['ci']
        muted = row.get('flagged', False) and row['arm'] != 'no-search'
        stroke = c('ref', 'ref') if muted or row['arm'] == 'no-search' else c('beam', 'beam')
        parts.append(f'<text x="{x:.1f}" y="{cy + 4:.1f}" font-family="{FONT}" font-size="13" '
                     f'fill="{c("ink", "ink")}">{esc(row["label"])}{" *" if muted else ""}</text>')
        parts.append(f'<rect x="{sx(lo):.1f}" y="{cy - 5:.1f}" width="{max(1.5, sx(hi) - sx(lo)):.1f}" height="10" '
                     f'rx="2" fill="{c("beam-soft", "beam_soft") if not muted else c("hatch", "hatch")}" opacity="0.9"/>')
        parts.append(f'<line x1="{sx(lo):.1f}" y1="{cy:.1f}" x2="{sx(hi):.1f}" y2="{cy:.1f}" stroke="{stroke}" stroke-width="2"/>')
        parts.append(f'<circle cx="{sx(row["pass_pct"]):.1f}" cy="{cy:.1f}" r="5.5" fill="{stroke}" '
                     f'stroke="{c("surface", "surface")}" stroke-width="1.5"/>')
        parts.append(f'<text x="{x + width:.1f}" y="{cy + 4:.1f}" text-anchor="end" font-family="{MONO}" font-size="12" '
                     f'fill="{c("ink", "ink")}">{row["passes"]}/{row["cells"]} · {row["pass_pct"]:.0f}%</text>')
    return '\n'.join(parts), height


def _wrap(text: str, chars: int) -> list[str]:
    lines, line = [], ''
    for word in text.split():
        if line and len(line) + 1 + len(word) > chars:
            lines.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    if line:
        lines.append(line)
    return lines


def _text(x, y, text, size=14, fill=PALETTE['ink'], family=FONT, weight=None, anchor=None, spacing=None):
    extra = (f' font-weight="{weight}"' if weight else '') + (f' text-anchor="{anchor}"' if anchor else '') + \
        (f' letter-spacing="{spacing}"' if spacing else '')
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'fill="{fill}"{extra}>{esc(text)}</text>')


def _para(x, y, text, chars, size=14, lead=20, **kw) -> tuple[list[str], float]:
    lines = _wrap(text, chars)
    return [_text(x, y + i * lead, line, size, **kw) for i, line in enumerate(lines)], lead * len(lines)


def _dumbbell(boards, x, y, width, P) -> tuple[list[str], float]:
    """No-search dot, search-arm range bar and answer-excerpt ceiling per harness.

    Labels live in their own columns: names on the left, values on the right,
    the legend above. Nothing is written on the track.
    """
    out = [_text(x, y, 'Pass rate on briefs about recent events (search gap bench)', 14, weight='600')]
    lx = x
    legend = [('dot', 'No search'), ('bar', 'Range across all search setups'), ('tri', 'Answer excerpt handed over')]
    for kind, label in legend:
        if kind == 'dot':
            out.append(f'<circle cx="{lx + 6:.1f}" cy="{y + 23:.1f}" r="6" fill="{P["ink"]}"/>')
        elif kind == 'bar':
            out.append(f'<rect x="{lx:.1f}" y="{y + 17:.1f}" width="22" height="12" rx="3" fill="{P["beam"]}"/>')
        else:
            out.append(f'<path d="M {lx:.1f} {y + 18:.1f} L {lx + 12:.1f} {y + 18:.1f} L {lx + 6:.1f} {y + 28:.1f} Z" fill="{P["ink"]}"/>')
        width_icon = 30 if kind == 'bar' else 20
        out.append(_text(lx + width_icon, y + 28, label, 12, P['ink2']))
        lx += width_icon + 7 * len(label) + 24
    label_w, value_w = 150, 128
    px, pw = x + label_w, width - label_w - value_w
    sx = lambda pct: px + pw * pct / 100  # noqa: E731
    top = y + 52
    row_h = 64
    bottom = top + row_h * len(boards)
    for tick in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{sx(tick):.1f}" y1="{top:.1f}" x2="{sx(tick):.1f}" y2="{bottom:.1f}" stroke="{P["rule"]}" stroke-width="1"/>')
        out.append(_text(sx(tick), bottom + 16, f'{tick}%', 11, P['ink2'], MONO, anchor='middle'))
    for i, board in enumerate(boards):
        cy = top + row_h * i + row_h / 2
        lo, hi = _gap_range(board)
        ceil = _ceiling(board)
        name, _ = harness_name(board)
        out.append(_text(x, cy - 2, name, 15, weight='600'))
        out.append(_text(x, cy + 16, f'{board["rows"][0]["cells"]} runs per setup', 12, P['ink2'], MONO))
        out.append(f'<line x1="{px:.1f}" y1="{cy:.1f}" x2="{px + pw:.1f}" y2="{cy:.1f}" stroke="{P["hatch"]}" stroke-width="2"/>')
        out.append(f'<rect x="{sx(lo):.1f}" y="{cy - 8:.1f}" width="{max(3, sx(hi) - sx(lo)):.1f}" height="16" rx="4" fill="{P["beam"]}"/>')
        out.append(f'<circle cx="{sx(0):.1f}" cy="{cy:.1f}" r="7" fill="{P["ink"]}"/>')
        out.append(f'<path d="M {sx(ceil) - 6:.1f} {cy - 22:.1f} L {sx(ceil) + 6:.1f} {cy - 22:.1f} '
                   f'L {sx(ceil):.1f} {cy - 12:.1f} Z" fill="{P["ink"]}"/>')
        vx = x + width
        out.append(_text(vx, cy - 8, 'no search 0%', 12, P['ink'], MONO, anchor='end'))
        out.append(_text(vx, cy + 8, f'search {lo:.0f}–{hi:.0f}%', 12, P['beam'], MONO, weight='600', anchor='end'))
        out.append(_text(vx, cy + 24, f'handed over {ceil:.0f}%', 12, P['ink2'], MONO, anchor='end'))
    return out, bottom + 24 - y


def _wsb_forest(board, x, y, width, P) -> tuple[list[str], float]:
    out = [_text(x, y, 'Pass rate on well-documented questions (web search bakeoff, Claude Code)', 14, weight='600')]
    nos = next(r for r in board['rows'] if r['arm'] == 'no-search')
    label_w, value_w = 104, 104
    px, pw = x + label_w, width - label_w - value_w
    ref_x = px + pw * nos['pass_pct'] / 100
    top = y + 40
    out.append(_text(ref_x, top - 6, f'no search {nos["pass_pct"]:.0f}%', 11, P['ink2'], MONO, anchor='middle'))
    svg, h = forest(board, x, top, width, row_h=26, label_w=label_w, value_w=value_w)
    rows_h = 26 * len(board['rows'])
    out.append(svg)
    out.append(f'<line x1="{ref_x:.1f}" y1="{top:.1f}" x2="{ref_x:.1f}" y2="{top + rows_h:.1f}" '
               f'stroke="{P["ink"]}" stroke-width="1.5" stroke-dasharray="4 4"/>')
    return out, (top - y) + h


def _gap_forests(boards, x, y, width, P) -> tuple[list[str], float]:
    out = []
    gap = 24
    label_w, value_w, min_plot_w = 80, 100, 120
    columns = max(1, min(len(boards), int((width + gap) // (label_w + value_w + min_plot_w + gap))))
    each = (width - gap * (columns - 1)) / columns
    height = 0.0
    for start in range(0, len(boards), columns):
        if start:
            height += gap
        row_height = 0.0
        for col, board in enumerate(boards[start:start + columns]):
            bx, by = x + col * (each + gap), y + height
            name, model = harness_name(board)
            out.append(_text(bx, by, name, 14, weight='600'))
            model_offset = 9 * len(name) + 8
            heading_h = 18
            if model_offset + 7.2 * len(model) <= each:
                out.append(_text(bx + model_offset, by, model, 12, P['ink2'], MONO))
            else:
                lines = textwrap.wrap(model, width=max(1, int(each / 7.2)))
                for i, line in enumerate(lines):
                    out.append(_text(bx, by + 18 + i * 16, line, 12, P['ink2'], MONO))
                heading_h += 16 * len(lines)
            svg, h = forest(board, bx, by + heading_h, each, row_h=26, label_w=label_w, value_w=value_w, ticks=(0, 50, 100))
            out.append(svg)
            row_height = max(row_height, heading_h + h)
        height += row_height
    note, nh = _para(x, y + height + 10, 'Claude Code built-in search: rerun on 2026-10-06 after a fix let the agent '
                     'open pages. In the original runs it could only search.',
                     110, 12, 16, fill=P['ink2'])
    return out + note, height + 10 + nh


def _tiles(tiles, x, y, width, P) -> tuple[list[str], float]:
    out = []
    gap = 16
    tw = (width - gap * (len(tiles) - 1)) / len(tiles)
    bodies = [_wrap(body, 27) for _, body in tiles]
    th = 70 + 18 * max(len(b) for b in bodies)
    for i, ((big, _), body) in enumerate(zip(tiles, bodies)):
        tx = x + i * (tw + gap)
        out.append(f'<rect x="{tx:.1f}" y="{y:.1f}" width="{tw:.1f}" height="{th:.1f}" rx="6" fill="{P["surface"]}" stroke="{P["rule"]}"/>')
        out.append(_text(tx + 16, y + 44, big, 32, P['beam'], COND, weight='700'))
        for j, line in enumerate(body):
            out.append(_text(tx + 16, y + 70 + j * 18, line, 13, P['ink']))
    return out, th


def _h2h_panels(h2h: dict, x, y, width, P, ids=('s1', 's4', 's3', 's6')) -> tuple[list[str], float]:
    """Small multiples, one per measure: a bar per provider on that measure's own scale, the leader in full colour."""
    out = []
    gap, row_h, label_w, value_w = 28, 22, 74, 66
    pw = (width - gap) / 2
    measures = [m for m in h2h['measures'] if m['id'] in ids]
    panel_h = 26 + row_h * len(h2h['providers']) + 10
    for i, m in enumerate(measures):
        px, py = x + (i % 2) * (pw + gap), y + (i // 2) * (panel_h + 14)
        scale = m['of'] or max(m['values'].values()) or 1
        top = max(m['values'].values())
        out.append(_text(px, py + 12, m['label'] + (f' (of {m["of"]})' if m['of'] else ''), 13, weight='600'))
        bx, bw = px + label_w, pw - label_w - value_w
        for j, provider in enumerate(h2h['providers']):
            cy = py + 26 + row_h * j + row_h / 2
            out.append(_text(px, cy + 4, provider, 12, P['ink2']))
            out.append(f'<rect x="{bx:.1f}" y="{cy - 6:.1f}" width="{bw:.1f}" height="12" rx="2" fill="{P["rule"]}"/>')
            if provider not in m['values']:
                out.append(_text(bx + 6, cy + 4, 'not run', 11, P['ink2'], MONO))
                continue
            value = m['values'][provider]
            fill = P['beam'] if value == top else P['beam_mid']
            out.append(f'<rect x="{bx:.1f}" y="{cy - 6:.1f}" width="{max(2.0, bw * value / scale):.1f}" height="12" '
                       f'rx="2" fill="{fill}"/>')
            out.append(_text(px + pw, cy + 4, str(value), 12, P['ink'], MONO, '600' if value == top else None,
                             anchor='end'))
    rows = (len(measures) + 1) // 2
    height = rows * panel_h + (rows - 1) * 14
    note, nh = _para(x, y + height + 12, 'Counts of checked items, one run per question; no 95% ranges were '
                     'published, so a difference of a few items may be chance. Tavily sells no data-extraction '
                     'product, so it was not run on the template.', 104, 12, 16, fill=P['ink2'])
    return out + note, height + 12 + nh


def infographic(boards: list[dict], analysis: dict, h2h: dict | None = None) -> str:
    P = PALETTE
    W, M = 1200, 48
    # The findings narrate the published Claude Code and Codex boards; the
    # charts show every GAP board the report has.
    gap_boards = [b for b in boards if b['bench'] == 'GAP']
    gap_cc = next(b for b in boards if b['id'] == 'gap-claude-code')
    gap_cx = next(b for b in boards if b['id'] == 'gap-codex')
    wsb = next(b for b in boards if b['id'] == 'wsb-competitive')
    agents = len({b['harness'] for b in boards})
    rows = {(r['harness'], r['arm']): r for r in analysis['gap_rows']}
    brows = {r['arm']: r for r in analysis['bakeoff_rows']}
    totals = analysis['gap_totals']
    provider_arms = ['brave', 'tavily', 'exa', 'parallel-web', 'firecrawl', 'perplexity']
    site_lo = min(rows[('codex', a)]['site_operator_pct'] for a in provider_arms)
    site_hi = max(rows[('codex', a)]['site_operator_pct'] for a in provider_arms)

    def pooled(harness, key):
        arms = ['native', *provider_arms]
        return (sum(rows[(harness, a)][key][0] for a in arms), sum(rows[(harness, a)][key][1] for a in arms))

    hit, miss = pooled('claude-code', 'pass_when_surfaced'), pooled('claude-code', 'pass_when_not_surfaced')
    hit_pct = 100 * hit[0] / hit[1] if hit[1] else 0
    miss_pct = 100 * miss[0] / miss[1] if miss[1] else 0
    runs = sum(r['cells'] for b in boards for r in b['rows'])
    cells = sorted({r['cells'] for b in boards for r in b['rows']})
    cc_lo, cc_hi = _gap_range(gap_cc)
    cx_lo, cx_hi = _gap_range(gap_cx)
    cc_top, cx_top = gap_cc['rows'][0], gap_cx['rows'][0]
    nos = next(r for r in wsb['rows'] if r['arm'] == 'no-search')
    best_wsb = max((r for r in wsb['rows'] if r['arm'] != 'no-search'), key=lambda r: r['pass_pct'])
    tps = sorted(wsb['rows'], key=lambda r: float(r['tokens_per_success'].rstrip('k')))
    queries = totals['queries'] + analysis['bakeoff_totals']['queries']
    exa, fc, tv, pw = (brows[a] for a in ('exa', 'firecrawl', 'tavily', 'parallel-web'))

    body: list[str] = []
    # ---- header
    body.append(f'<rect x="0" y="0" width="{W}" height="150" fill="{P["ink"]}"/>')
    body.append(f'<path d="M {W} 0 L {W} 150 L {W - 420} 150 Z" fill="{P["beam"]}" opacity="0.22"/>')
    body.append(f'<g transform="translate({M} 28) scale(0.42)">'
                '<circle cx="31" cy="67" r="22" fill="#FFFFFF"/>'
                '<path d="M34 69.5 L52.1 10.2 A62 62 0 0 1 91.1 45.3 Z" fill="#007BFF"/>'
                '<circle cx="34" cy="69.5" r="6.4" fill="#111111"/></g>')
    body.append(_text(M + 52, 62, 'SEARCHLIGHT', 24, P['paper'], FONT, '500', spacing=5))
    body.append(_text(M, 100, 'Does web search help coding agents do real work?', 22, P['paper']))
    providers = len({r['arm'] for b in boards for r in b['rows']} - {'native', 'no-search'})
    if h2h:
        body.append(_text(M, 128, f'Results to date, 2026-09-26 to 2026-10-06 · {runs} graded agent runs · {agents} coding '
                          f'agents · {providers} providers · 1 API study', 14, '#C9D3DA', MONO))
    else:
        body.append(_text(M, 128, f'Results to date, 2026-09-29 to 2026-10-05 · {runs} graded test runs · {agents} AI coding '
                          f'agents · {providers} search providers · 2 benchmarks', 14, '#C9D3DA', MONO))

    # ---- method strip
    y = 206
    body.append(_text(M, y, 'How the test works', 26, P['ink'], COND, '700'))
    steps = [
        ('A real job', "A research brief or a code fix about something that changed after the model's training "
                       'data ends, or a question with a well-documented answer.'),
        ('Swap only the search', 'Same agent, model and instructions in every run. Each test plugs in one '
                                 'search provider at its default settings. Controls: no search, and the '
                                 "agent's built-in search."),
        ('Isolated, repeated runs', f'A fresh, sealed workspace for every run, with every search logged. '
                                    f'{cells[0]}–{cells[-1]} test runs per provider: three repetitions of each task.'),
        ('Blind grading', 'Answers are checked against the original sources by graders that never see which '
                          'provider was used. Briefs need the right decision, 70% of the key facts, and sources that back '
                          'their claims.'),
    ]
    gap = 36
    cw = (W - 2 * M - 3 * gap) / 4
    wrapped = [_wrap(text, 31) for _, text in steps]
    titles = [_wrap(title, 24) for title, _ in steps]
    ch = 64 + 20 * max(len(t) for t in titles) + 18 * max(len(w) for w in wrapped)
    cy0 = y + 24
    for i, ((title, _), tlines, lines) in enumerate(zip(steps, titles, wrapped)):
        cx = M + i * (cw + gap)
        body.append(f'<rect x="{cx:.1f}" y="{cy0:.1f}" width="{cw:.1f}" height="{ch:.1f}" rx="8" fill="{P["surface"]}" stroke="{P["rule"]}"/>')
        body.append(f'<circle cx="{cx + 30:.1f}" cy="{cy0 + 32:.1f}" r="15" fill="{P["beam"]}"/>')
        body.append(_text(cx + 30, cy0 + 38, str(i + 1), 16, P['surface'], COND, '700', anchor='middle'))
        for j, line in enumerate(tlines):
            body.append(_text(cx + 54, cy0 + 38 + j * 20, line, 16, P['ink'], FONT, '600'))
        ty = cy0 + 38 + 20 * len(tlines) + 14
        for j, line in enumerate(lines):
            body.append(_text(cx + 18, ty + j * 18, line, 13, P['ink2']))
        if i < 3:
            ax = cx + cw + gap / 2
            body.append(f'<path d="M {ax - 7:.1f} {cy0 + ch / 2 - 10:.1f} L {ax + 5:.1f} {cy0 + ch / 2:.1f} '
                        f'L {ax - 7:.1f} {cy0 + ch / 2 + 10:.1f}" fill="none" stroke="{P["beam"]}" stroke-width="3" '
                        f'stroke-linecap="round" stroke-linejoin="round"/>')
    y = cy0 + ch + 30
    body.append(_text(M, y, 'Pass rates are shown with their 95% range. Where two ranges overlap, the test cannot '
                      'separate them.' + (' Finding 5 counts verified items instead.' if h2h else ''), 14, P['ink2']))

    # ---- findings
    y += 62
    body.append(_text(M, y, 'What we found', 26, P['ink'], COND, '700'))
    y += 34
    left_w, right_x = 380, M + 412
    right_w = W - M - right_x
    findings = [
        ('Past the training cutoff, search decides the outcome',
         f'Without search, both agents failed every brief about recent events (0%). With any search setup, '
         f'Claude Code passed {cc_lo:.0f}–{cc_hi:.0f}% and Codex {cx_lo:.0f}–{cx_hi:.0f}%, close to the '
         f'{_ceiling(gap_cc):.0f}% and {_ceiling(gap_cx):.0f}% reached when handed an excerpt containing the answer.',
         'For work that depends on recent events, search is not a tuning knob. It is the difference between '
         'failing and passing, and every provider tested closes most of the gap.',
         lambda yy: _dumbbell(gap_boards, right_x, yy, right_w, P)),
        ('No provider wins everywhere',
         f'The top provider was {cc_top["label"]} on Claude Code ({cc_top["passes"]}/{cc_top["cells"]}) and '
         f'{cx_top["label"]} on Codex ({cx_top["passes"]}/{cx_top["cells"]}), and most ranges overlap. With '
         f'{gap_cx["rows"][0]["cells"]}–{gap_cc["rows"][0]["cells"]} test runs per provider, the test cannot '
         'separate the leaders.',
         'Choose on cost, latency and integration, then measure on your own agent. Rankings did not carry over '
         'from one agent to the other.',
         lambda yy: _gap_forests(gap_boards, right_x, yy, right_w, P)),
        ('On documented knowledge, memory does most of the work',
         f'In the bakeoff, answering from memory passed {nos["passes"]}/{nos["cells"]} ({nos["pass_pct"]:.0f}%). '
         f'The best provider reached {best_wsb["pass_pct"]:.0f}%: {memory_clause(nos, best_wsb)}, '
         f'while tokens used per passing run rose from {tps[0]["tokens_per_success"]} ({tps[0]["label"]}) to as much as '
         f'{tps[-1]["tokens_per_success"]} ({tps[-1]["label"]}).',
         'Search pays off where training data runs out. On stable, well-documented questions it mostly adds '
         'cost.',
         lambda yy: _wsb_forest(wsb, right_x, yy, right_w, P)),
        ('Agents still search like keyword users',
         f'Only {totals["natural_language_pct"]:.0f}% of searches about recent events read as plain questions, and Codex '
         f'limited many searches to one website ({site_lo:.0f}–{site_hi:.0f}% of its provider searches). Agents often '
         'skipped search and opened pages they remembered. Runs did better when the original source showed up.',
         'The next gains are in how agents ask and what results show: searches written as questions, and '
         'original sources first. These are patterns across runs, not proven causes.',
         lambda yy: _tiles([
             (f'{totals["natural_language_pct"]:.0f}%', 'of searches about recent events read as plain questions; '
                                                         'the rest were keyword strings.'),
             (f'{exa["fetch_only_cells"]}/{exa["cells"]}', 'Exa bakeoff runs that opened remembered pages without '
                                                           f'searching (Firecrawl {fc["fetch_only_cells"]}/{fc["cells"]}, '
                                                           f'Tavily {tv["fetch_only_cells"]}/{tv["cells"]}, '
                                                           f'Parallel {pw["fetch_only_cells"]}/{pw["cells"]}).'),
             (f'{hit_pct:.0f}% vs {miss_pct:.0f}%', 'Claude Code pass rate with the original source in its results '
                                                    f'({hit[0]}/{hit[1]}) vs without it ({miss[0]}/{miss[1]}).'),
         ], right_x, yy + 8, right_w, P)),
    ]
    if h2h:
        hm = {m['id']: m for m in h2h['measures']}
        s1, s4, s3, s6 = hm['s1'], hm['s4'], hm['s3'], hm['s6']
        news = '' if leader(s4) == leader(s1) else f'{leader(s4)} '
        findings.append((
            'Called directly, each search API led a different job',
            f'A separate study called four search APIs directly on a research workload. {leader(s1)} added the most '
            f'verified new claims ({max(s1["values"].values())} on 40 open questions) and {news}the most new dated '
            f'news facts ({max(s4["values"].values())}). {leader(s3)} read the most pages other tools could not '
            f'({max(s3["values"].values())} of {s3["of"]}), and {leader(s6)} filled in a fixed template most accurately '
            f'({max(s6["values"].values())} of {s6["of"]} values).',
            'Choose by job: finding new facts, reading hard pages and filling in data rewarded different providers. The study '
            'was run for a knowledge base about Exa, one of the four, so read its context note.',
            lambda yy: _h2h_panels(h2h, right_x, yy + 8, right_w, P)))
    for n, (title, text, sowhat, chart) in enumerate(findings, start=1):
        left: list[str] = [_text(M, y + 4, f'FINDING {n}', 12, P['beam'], MONO, '700', spacing=1)]
        hl, hh = _para(M, y + 34, title, 29, 24, 28, family=COND, weight='700')
        left += hl
        ly = y + 34 + hh + 4
        bl, bh = _para(M, ly, text, 49, 14, 20, fill=P['ink2'])
        left += bl
        ly += bh + 10
        sw = _wrap(sowhat, 45)
        box_h = 40 + 20 * len(sw)
        left.append(f'<rect x="{M}" y="{ly:.1f}" width="{left_w}" height="{box_h:.1f}" rx="6" fill="#EAF3FF"/>')
        left.append(f'<rect x="{M}" y="{ly:.1f}" width="5" height="{box_h:.1f}" rx="2" fill="{P["beam"]}"/>')
        left.append(_text(M + 20, ly + 22, 'SO WHAT', 11, P['beam'], MONO, '700', spacing=1))
        for j, line in enumerate(sw):
            left.append(_text(M + 20, ly + 44 + j * 20, line, 14, P['ink']))
        left_bottom = ly + box_h
        right, rh = chart(y + 4)
        body += left + right
        y = max(left_bottom, y + 4 + rh) + 32
        if n < len(findings):
            body.append(f'<line x1="{M}" y1="{y - 14:.1f}" x2="{W - M}" y2="{y - 14:.1f}" stroke="{P["rule"]}" stroke-width="1"/>')
            y += 16

    # ---- caveats
    y += 8
    body.append(f'<rect x="{M}" y="{y:.1f}" width="{W - 2 * M}" height="186" rx="8" fill="{P["surface"]}" stroke="{P["rule"]}"/>')
    body.append(_text(M + 24, y + 34, 'Read before comparing', 18, P['ink'], COND, '700'))
    caveats = [
        f'{cells[0]}–{cells[-1]} test runs per provider: ranges are wide, so one place higher may not mean better.',
        ('In the agent tests each provider was plugged in through its official connector at default settings; the '
         'API study called tuned APIs directly.' if h2h else
         'Each provider ran through its own MCP server at a pinned version and default settings. Direct APIs, '
         'other search modes and other tools were not tested.'),
        'Grading used automatic checks and AI graders that never saw the provider. Dollar costs were only partly tracked.',
        f'Search behavior comes from {queries} logged searches; its links to pass rates are patterns, not causes.',
    ]
    for i, line in enumerate(caveats):
        body.append(_text(M + 24, y + 64 + i * 24, f'·  {line}', 14, P['ink']))
    body.append(_text(M + 24, y + 166, f'searchlightai.dev · {REPO_URL.replace("https://", "")} · '
                      'a Lacey Enterprises project', 13, P['ink2'], MONO))
    H = int(y + 186 + M)
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            'role="img" aria-labelledby="ig-title ig-desc">',
            '<title id="ig-title">Searchlight results to date</title>',
            '<desc id="ig-desc">How the Searchlight test works, '
            + ('five findings with their evidence (pass rates with 95% ranges for search providers on two benchmarks '
               'and two coding agents, how the agents searched, and counts from a study that called four search APIs '
               'directly)' if h2h else
               'four findings with their evidence (pass rates with 95% ranges for search providers on two benchmarks '
               'and two coding agents, and how the agents searched)')
            + ', and the caveats for reading them.</desc>',
            f'<rect width="{W}" height="{H}" fill="{P["paper"]}"/>']
    return '\n'.join(head + body + ['</svg>'])


CARD_KEY = 'searchlight-card-source-sha256'


def card_facts(boards: list[dict], analysis: dict) -> dict:
    """Title, description and three statistics for the social card, from the published boards."""
    h = headline(boards, analysis)
    gap = [b for b in boards if b['bench'] == 'GAP']
    floor = max(float(re.match(r'(\d+)', b['reference'].get('floor (no search)', '') or '0').group(1)) for b in gap)
    best = max(hi for _, hi in (h['cc'], h['cx']))
    cc = next(b for b in boards if b['id'] == 'gap-claude-code')
    cx = next(b for b in boards if b['id'] == 'gap-codex')
    top = next(r for r in cc['rows'] if r['arm'] != 'native')
    other = next(r for r in cx['rows'] if r['arm'] == top['arm'])
    stats = [
        (f'{floor:.0f}% → {best:.0f}%', 'no search → best search provider'),
        (f'{top["pass_pct"]:.0f}% vs {other["pass_pct"]:.0f}%', 'same provider, two different agents'),
        (f'{h["runs"]}', 'runs, graded blind'),
    ]
    title = 'Does web search make AI coding agents better?'
    description = (f'Claude Code and Codex did real jobs with only the search provider changed. {h["runs"]} runs, '
                   'graded blind. Open tasks, code and data.')
    alt = (f'Searchlight. {title} ' + ' '.join(f'{big}: {text}.' for big, text in stats))
    return {'title': title, 'description': description, 'stats': stats, 'alt': alt, 'runs': h['runs']}


CARD_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1200px;height:630px;overflow:hidden}
body{background:#0A0C10;color:#FFFFFF;font-family:Inter,"Helvetica Neue",Arial,sans-serif;position:relative;
-webkit-font-smoothing:antialiased}
.glow{position:absolute;inset:0;background:radial-gradient(760px 520px at 88% 30%,rgba(0,123,255,.22),rgba(0,123,255,0) 65%)}
.mark{position:absolute;right:72px;top:96px;width:300px;height:300px}
.frame{position:absolute;inset:0;padding:60px 64px 56px;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:center;font-size:18px}
.word{font-weight:600;letter-spacing:.34em}
.url{font-weight:500;color:#9AA5B1;letter-spacing:.02em}
h1{margin-top:74px;max-width:740px;font-size:64px;line-height:1.04;font-weight:700;letter-spacing:-.03em}
.stats{margin-top:auto;display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid rgba(255,255,255,.16)}
.stat{padding:26px 24px 0 0}
.stat+.stat{padding-left:32px;border-left:1px solid rgba(255,255,255,.12)}
.big{font-size:50px;line-height:1;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}
.stat:first-child .big{color:#3D9BFF}
.label{margin-top:12px;font-size:19px;color:#9AA5B1;white-space:nowrap}
"""


def social_card(boards: list[dict], analysis: dict) -> str:
    """The 1200x630 share image's source; scripts/render_social_card.py renders it to site/social-card.png."""
    card = card_facts(boards, analysis)
    stats = ''.join(f'<div class="stat"><div class="big">{esc(big)}</div><div class="label">{esc(text)}</div></div>'
                    for big, text in card['stats'])
    fonts = ('<link rel="stylesheet" '
             'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=block">')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
            f'<title>Searchlight social card</title>{fonts}<style>{CARD_CSS}</style></head><body>'
            f'<div class="glow"></div>{CARD_MARK}<div class="frame">'
            '<div class="top"><span class="word">SEARCHLIGHT</span><span class="url">searchlightai.dev</span></div>'
            f'<h1>{esc(card["title"])}</h1><div class="stats">{stats}</div></div></body></html>\n')


def card_source_hash(source: str) -> str:
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def png_text_chunks(data: bytes) -> dict[str, str]:
    """tEXt chunks of a PNG, keyword -> text."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    out, i = {}, 8
    while i + 8 <= len(data):
        length, kind = struct.unpack('>I4s', data[i:i + 8])
        body = data[i + 8:i + 8 + length]
        if kind == b'tEXt' and b'\0' in body:
            key, value = body.split(b'\0', 1)
            out[key.decode('latin-1')] = value.decode('latin-1')
        if kind == b'IEND':
            break
        i += 12 + length
    return out


def stamp_png(data: bytes, key: str, value: str) -> bytes:
    """Insert a tEXt chunk after IHDR (replacing an existing one with the same keyword)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    chunks, i = [], 8
    while i + 8 <= len(data):
        length, kind = struct.unpack('>I4s', data[i:i + 8])
        raw = data[i:i + 12 + length]
        body = data[i + 8:i + 8 + length]
        if not (kind == b'tEXt' and body.split(b'\0', 1)[0] == key.encode('latin-1')):
            chunks.append(raw)
        i += 12 + length
        if kind == b'IEND':
            break
    payload = key.encode('latin-1') + b'\0' + value.encode('latin-1')
    text = struct.pack('>I', len(payload)) + b'tEXt' + payload + struct.pack('>I', zlib.crc32(b'tEXt' + payload) & 0xFFFFFFFF)
    return data[:8] + chunks[0] + text + b''.join(chunks[1:])


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR."""
    return struct.unpack('>II', data[16:24])


def card_problem(site: Path, source: str) -> str:
    """Why site/social-card.png does not match the card source, or '' when it does."""
    png = site / 'social-card.png'
    if not png.is_file():
        return 'social-card.png is missing'
    try:
        stamp = png_text_chunks(png.read_bytes()).get(CARD_KEY)
    except ValueError:
        return 'social-card.png is not a PNG'
    if stamp != card_source_hash(source):
        return 'social-card.png was rendered from a different card source'
    return ''


CARD_MARK = ('<svg class="mark" viewBox="0 0 24 24" aria-hidden="true" style="color:#FFFFFF;--paper:#0A0C10;'
             '--sl-accent:#3D9BFF">' + glyphs.LOGOS[[x[0] for x in glyphs.LOGOS].index(LOGO_ID)][2] + '</svg>')


def page(reports: dict, boards: list[dict], configs: list[dict], target: str) -> tuple[str, str]:
    """Single-file preview: the results and methodology bodies, without scripts or the document shell."""
    analysis = behavior(reports)
    head = f'<title>Searchlight Results</title>\n<style>{CSS}</style>'
    body = (results_page(reports, boards, analysis, head_to_head(reports), body_only=True)
            + methodology_page(configs, target, body_only=True))
    return head, body


def build(root: Path = ROOT) -> dict[str, str]:
    reports = load_reports(root)
    boards = leaderboards(reports)
    extra = more_boards(reports)
    configs = arm_configs(root)
    analysis = behavior(reports)
    h2h = head_to_head(reports)
    data = {
        'generated_from': [f'reports/{name}/summary.json' for name in sorted(reports)],
        'note': 'Pass rates and intervals as published; WSB intervals are Wilson 95% computed from published counts. '
                'Overlapping intervals are not rankings.',
        # Board titles already name each model; leaving the key out keeps the
        # published data file unchanged.
        'boards': [{key: value for key, value in board.items() if key != 'model'} for board in boards + extra],
        'search_api_head_to_head': h2h,
    }
    studies = insight_studies(root)
    pages = site_map(reports, studies)
    everything = boards + extra
    return {
        **{s['page']: study_page(s, pages) for s in studies},
        **{report_page_name(name): report_page(name, reports, everything, analysis, h2h, pages) for name in reports},
        'studies.html': studies_page(studies, pages),
        'index.html': landing(root, boards, analysis, reports, studies, h2h, card_facts(boards, analysis), pages),
        'results.html': results_page(reports, boards, analysis, h2h, studies=studies, pages=pages),
        'learn.html': learn_page(boards, analysis, pages),
        'methodology.html': methodology_page(configs, boards=boards, analysis=analysis, pages=pages),
        'logo.svg': LOGO + '\n',
        'infographic.svg': infographic(boards, analysis, h2h) + '\n',
        'leaderboard.json': json.dumps(data, indent=2, ensure_ascii=False) + '\n',
        'social-card.html': social_card(boards, analysis),
    }


def readme_art(root: Path = ROOT) -> dict[str, str]:
    """The README images, {path under docs/readme/: SVG}, from the same reports as the site."""
    reports = load_reports(root)
    boards = leaderboards(reports)
    return readme.art(boards, behavior(reports), reports, insight_studies(root))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='fail if site/ differs from a fresh build')
    parser.add_argument('--target', choices=['pages', 'preview'], default='pages')
    parser.add_argument('--out', type=Path, help='preview target: file to write')
    args = parser.parse_args(argv)
    if args.target == 'preview':
        reports = load_reports()
        boards = leaderboards(reports)
        out = args.out or Path('searchlight-preview.html')
        head, body = page(reports, boards, arm_configs(), 'preview')
        out.write_text(head + '\n' + body + '\n', encoding='utf-8')
        print(f'Preview written: {out}')
        return 0
    site = ROOT / 'site'
    files = {f'site/{name}': content for name, content in build().items()}
    art = readme_art()
    files.update({f'{readme.OUT}/{name}': content for name, content in art.items()})
    files.update(readme.alt_texts(art))  # the charts' alt text in the READMEs, with their generated numbers
    # Report and study pages come and go with their report directories; README images with what they draw.
    orphans = sorted(p.relative_to(ROOT).as_posix() for folder, patterns in
                     ((site, ('report-*.html', 'study-*.html')), (ROOT / readme.OUT, ('*.svg',)))
                     for pattern in patterns for p in folder.glob(pattern)
                     if p.relative_to(ROOT).as_posix() not in files)
    if args.check:
        stale = [name for name, content in files.items()
                 if not (ROOT / name).is_file() or (ROOT / name).read_text(encoding='utf-8') != content] + orphans
        if stale:
            print('site/, docs/readme/ or a README chart alt is stale; run python3 scripts/build_site.py: '
                  + ', '.join(stale),
                  file=sys.stderr)
            return 1
        problem = card_problem(site, files['site/social-card.html'])
        if problem:
            print(f'site/ is stale: {problem}; run python3 scripts/render_social_card.py', file=sys.stderr)
            return 1
        print(f'Site and README art are current: {len(files)} files')
        return 0
    for folder in (site, ROOT / readme.OUT):
        folder.mkdir(parents=True, exist_ok=True)
    for name in orphans:
        (ROOT / name).unlink()
    for name, content in files.items():
        (ROOT / name).write_text(content, encoding='utf-8')
    print('Site written: ' + ', '.join(name for name in sorted(files) if name.startswith('site/')))
    print(f'README art written: {sum(1 for name in files if name.startswith(readme.OUT))} files in {readme.OUT}/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
