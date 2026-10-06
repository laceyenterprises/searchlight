"""Build the Searchlight results site, leaderboard data and infographic from published reports.

Inputs are only files in this repository: the checked report transcriptions under
``reports/`` (see ``scripts/check_reports.py``) and the pinned provider arm configuration
in ``config/provider-arm-tools.yaml``. Nothing is recalculated from private run data;
the only derived numbers are Wilson intervals for tables that publish counts without
intervals, and they are labelled as computed.

    python3 scripts/build_site.py            # write site/
    python3 scripts/build_site.py --check    # fail if site/ is stale
    python3 scripts/build_site.py --target preview --out /tmp/preview.html
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
import html
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_reports import REPORTS  # noqa: E402

REPO_URL = 'https://github.com/laceyenterprises/searchlight'
MERMAID_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js'
# SHA-384 of the pinned CDN asset; update together with MERMAID_URL.
MERMAID_INTEGRITY = 'sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I'
RERUN_NOTE = 'Rerun 2026-10-06 with page opening fixed'
ARM_LABEL = {
    'no-search': 'No search', 'native': 'Built-in', 'brave': 'Brave', 'tavily': 'Tavily', 'exa': 'Exa',
    'parallel-web': 'Parallel', 'firecrawl': 'Firecrawl', 'perplexity': 'Perplexity',
}
REPORT_TITLES = {
    '2026-09-29-web-search-bakeoff': 'Web search bakeoff',
    '2026-10-03-search-gap-bench': 'Search gap bench',
    '2026-10-05-agent-search-behavior': 'How agents used search',
    '2026-09-26-search-api-head-to-head': 'Search API head-to-head',
}
H2H = '2026-09-26-search-api-head-to-head'
H2H_PROVIDERS = ('Exa', 'Tavily', 'Parallel', 'Firecrawl')


# ---------------------------------------------------------------- data

def load_reports(root: Path = ROOT) -> dict:
    out = {}
    for name in REPORTS:
        directory = root / 'reports' / name
        out[name] = {
            'summary': json.loads((directory / 'summary.json').read_text(encoding='utf-8')),
            'methodology': (directory / 'methodology.md').read_text(encoding='utf-8'),
        }
    return out


def tables(summary: dict) -> list[tuple[str, list[list[str]]]]:
    """Return (preceding markdown, table) pairs in document order."""
    pairs, last = [], ''
    for block in summary['blocks']:
        if 'markdown' in block:
            last = block['markdown']
        else:
            pairs.append((last, block['table']))
    return pairs


def data_rows(table: list[list[str]]) -> list[list[str]]:
    """Table rows without the header and the markdown separator row."""
    return [row for row in table[1:] if not set(''.join(row)) <= set('-: ')]


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def parse_count(cell: str) -> tuple[int, int]:
    match = re.match(r'\s*(\d+)/(\d+)', cell)
    if not match:
        raise ValueError(f'expected k/n in {cell!r}')
    return int(match.group(1)), int(match.group(2))


def parse_pct_ci(cell: str) -> tuple[float, float, float]:
    number = r'\d+(?:\.\d+)?'
    match = re.fullmatch(rf'\s*({number})%\s*\(\s*({number})\s*[-–]\s*({number})%\s*\)\s*', cell)
    if not match:
        raise ValueError(f'expected "p% (lo–hi%)" in {cell!r}')
    return tuple(float(x) for x in match.groups())


def leaderboards(reports: dict) -> list[dict]:
    boards = []
    gap = reports['2026-10-03-search-gap-bench']['summary']
    for intro, table in tables(gap):
        if table[0][:3] != ['Arm', 'Gap closure (95% CI)', 'Pass rate (Wilson 95% CI)']:
            continue
        heading = [line for line in intro.splitlines() if line.startswith('### ')][-1]
        harness = 'claude-code' if 'claude-code' in heading else 'codex'
        cells = int(re.search(r'(\d+) cells per arm', heading).group(1))
        rows = []
        for row in data_rows(table):
            if row[0].startswith(('ceiling', 'floor')):
                continue
            point, lo, hi = parse_pct_ci(row[2])
            arm = row[0].lower().replace('parallel', 'parallel-web')
            rows.append({
                'arm': arm, 'label': ARM_LABEL.get(arm, row[0]), 'pass_pct': point, 'ci': [lo, hi],
                'passes': round(point * cells / 100), 'cells': cells, 'gap_closure': row[1],
                'tokens_per_success': row[3], 'vs_floor': row[4],
                'note': RERUN_NOTE if harness == 'claude-code' and arm == 'native' else '',
                'flagged': False,
            })
        reference = {r[0].split(' ')[0]: r[2] for r in data_rows(table) if r[0].startswith(('ceiling', 'floor'))}
        boards.append({
            'id': f'gap-{harness}', 'bench': 'GAP', 'harness': harness,
            'title': f'Search gap bench: job outcomes on {"Claude Code (claude-opus-5-5)" if harness == "claude-code" else "codex (gpt-6.1-sol)"}',
            'scope': f'{cells // 3} admitted post-cutoff brief tasks × 3 repetitions = {cells} cells per arm. '
                     'Pass = right decision, weighted key-fact recall ≥ 0.7, unsupported claims ≤ 0.25.',
            'interval': 'Wilson 95% (published)',
            'token_accounting': 'GAP tokens per success counts all agent tokens in the arm, failed cells included, '
                                'divided by passing cells.',
            'reference': {'floor (no search)': reference.get('floor', ''), 'ceiling (answer excerpt)': reference.get('ceiling', '')},
            'rows': sorted(rows, key=lambda r: (-r['pass_pct'], r['label'])),
            'source': '2026-10-03-search-gap-bench',
        })
    wsb = reports['2026-09-29-web-search-bakeoff']['summary']
    regrade = next(t for _, t in tables(wsb) if t[0] == ['arm', 'pass', 'tokens/success'])
    rows = []
    for row in data_rows(regrade):
        k, n = parse_count(row[1])
        lo, hi = wilson(k, n)
        arm = row[0].lower()
        rows.append({
            'arm': arm, 'label': ARM_LABEL.get(arm, arm), 'pass_pct': round(100 * k / n) if n else 0, 'ci': [round(lo), round(hi)],
            'passes': k, 'cells': n, 'gap_closure': None, 'tokens_per_success': row[2], 'vs_floor': None,
            'note': 'reference arm' if arm == 'no-search' else (RERUN_NOTE if arm == 'native' else ''),
            'flagged': False,
        })
    boards.append({
        'id': 'wsb-competitive', 'bench': 'WSB', 'harness': 'claude-code',
        'title': 'Web search bakeoff: competitive research tasks on Claude Code (claude-opus-5-5)',
        'scope': '14 competitive tasks × 3 repetitions = 42 cells per arm, regraded 2026-09-30. '
                 'Mostly stable, documented knowledge; search adds less here than on post-cutoff jobs.',
        'interval': 'Wilson 95% (computed from the published counts)',
        'token_accounting': 'WSB tokens per success uses measured tokens from graded cells divided by measured '
                            'successes. Ungraded and unmeasured cells are excluded; per-arm coverage counts '
                            'were not retained, so complete accounting cannot be established.',
        'reference': {},
        'rows': sorted(rows, key=lambda r: (-r['pass_pct'], r['label'])),
        'source': '2026-09-29-web-search-bakeoff',
    })
    for board in boards:
        top = next(r for r in board['rows'] if r['arm'] != 'no-search')
        board['top_lower_bound'] = top['ci'][0]
        for row in board['rows']:
            row['overlaps_top'] = row['ci'][1] >= top['ci'][0]
    return boards


def arm_configs(root: Path = ROOT) -> list[dict]:
    import yaml
    data = yaml.safe_load((root / 'config' / 'provider-arm-tools.yaml').read_text(encoding='utf-8'))
    out = []
    for arm, cfg in data['providers'].items():
        args = cfg.get('args', [])
        package = next((a for a in args if a not in ('-y',) and not a.startswith('-')), '')
        remote = next((a for a in args if a.startswith('https://')), '')
        tools = cfg.get('enabled_tools', [])
        out.append({
            'arm': arm, 'label': ARM_LABEL.get(arm, arm), 'package': package, 'remote': remote,
            'tools': tools,
        })
    return out


def behavior(reports: dict) -> dict:
    return reports['2026-10-05-agent-search-behavior']['summary']['analysis']


def parse_tally(cell: str) -> tuple[int, int | None]:
    """'25' -> (25, None); '105/133 (78.9%)' -> (105, 133). Bold markers are ignored."""
    match = re.fullmatch(r'\s*\**(\d+)\**(?:/(\d+))?(?:\s*\([^)]*\))?\s*', cell)
    if not match:
        raise ValueError(f'expected a count or k/n in {cell!r}')
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


def head_to_head(reports: dict) -> dict:
    """Figures from the search API head-to-head, read from its transcribed tables; nothing is recalculated."""
    pairs = tables(reports[H2H]['summary'])
    stage_a = next(t for _, t in pairs if t[0][0].startswith('Stage A'))
    stage_b = next(t for _, t in pairs if t[0][0] == 'Stage B')
    stage_c = next(t for _, t in pairs if t[0][:2] == ['Provider', 'New tier-1 claims (either arm)'])
    monitors = next(t for _, t in pairs if t[0][0].startswith('Monitor'))

    def measure(table, prefix, key, label):
        row = next(r for r in data_rows(table) if r[0].startswith(prefix))
        values, of = {}, None
        for provider, cell in zip(table[0][1:], row[1:]):
            values[provider], n = parse_tally(cell)
            of = of or n
        bound = re.search(r'\(of (\d+)', row[0])
        return {'id': key, 'label': label, 'of': of or (int(bound.group(1)) if bound else None),
                'values': values, 'source_row': row[0]}

    c_rows = {r[0]: r for r in data_rows(stage_c)}
    measures = [
        measure(stage_a, 'S1 new or correcting claims', 's1', 'New or correcting claims, 40 open questions'),
        measure(stage_a, 'S4 new dated facts', 's4', 'New dated facts, two weeks of company news'),
        measure(stage_a, 'S3 pages recovered', 's3', 'Pages other tools could not read, recovered'),
        measure(stage_b, 'S6 right on KB ground truth', 's6', 'Schema values right on ground truth'),
        measure(stage_b, 'S5 entities verified and new', 's5', 'Entities new to the knowledge base, three lists'),
        {'id': 'sv', 'label': 'New tier-1 claims on four big vendors (no lead significant)', 'of': None,
         'values': {p: parse_tally(c_rows[p][1])[0] for p in H2H_PROVIDERS}, 'source_row': stage_c[0][1]},
    ]
    rows = data_rows(monitors)
    probe = {'monitors': len(rows), 'runs': sum(int(r[1]) for r in rows),
             'runs_with_changes': sum(int(r[2]) for r in rows), 'changes': sum(int(r[3]) for r in rows)}
    if len(monitors[0]) > 4:
        probe['confirmed'] = sum(int(r[4]) for r in rows)
        probe['monitors_passing'] = sum(r[5].startswith('yes') for r in rows)
    return {'title': REPORT_TITLES[H2H], 'source': H2H, 'providers': list(H2H_PROVIDERS), 'measures': measures,
            'monitors': probe,
            'note': 'Counts of verified items from one run per question, transcribed from the report. No intervals '
                    'were published; differences of a few items are within noise.'}


def leader(measure: dict) -> str:
    top = max(measure['values'].values())
    return ' and '.join(p for p, v in measure['values'].items() if v == top)


# ---------------------------------------------------------------- markdown

def inline(text: str, report: str, heading_report: str | None = None) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r'`([^`]+)`', lambda m: f'<code>{m.group(1)}</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'<em>\1</em>', text)

    def link(match):
        label, target = match.group(1), html.unescape(match.group(2))
        context = heading_report if target.startswith('#') and heading_report else report
        return f'<a href="{html.escape(resolve_link(target, context))}">{label}</a>'

    return re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', link, text)


def heading_anchor(report: str, fragment: str) -> str:
    return f'{report}-{fragment}'


def resolve_link(target: str, report: str) -> str:
    if target.startswith(('http://', 'https://')):
        return target
    path, _, fragment = target.partition('#')
    if not path:
        return '#' + heading_anchor(report, fragment)
    parts = (Path('reports') / report / path).as_posix().split('/')
    stack = []
    for part in parts:
        if part == '..':
            if stack:
                stack.pop()
        elif part != '.':
            stack.append(part)
    resolved = '/'.join(stack)
    match = re.fullmatch(r'reports/([^/]+)/(REPORT|methodology)\.md', resolved)
    if match and match.group(1) in REPORTS:
        prefix = 'run' if match.group(2) == 'REPORT' else 'method'
        if fragment:
            namespace = match.group(1) + ('-method' if prefix == 'method' else '')
            return '#' + heading_anchor(namespace, fragment)
        return f'#{prefix}-{match.group(1)}'
    return f'{REPO_URL}/blob/main/{resolved}' + (f'#{fragment}' if fragment else '')


def slug(text: str) -> str:
    return re.sub(r'[^\w -]', '', text.lower()).strip().replace(' ', '-')


def markdown(text: str, report: str, heading_shift: int = 1, base: str | None = None) -> str:
    base = base or report
    out, para, lists, code, quote = [], [], [], None, []

    def flush_para():
        if para:
            out.append(f'<p>{inline(" ".join(para), base, report)}</p>')
            para.clear()

    def flush_quote():
        if quote:
            paragraphs = re.split(r'\n\s*\n', "\n".join(quote))
            body = ''.join(f'<p>{inline(" ".join(part.splitlines()), base, report)}</p>'
                           for part in paragraphs if part.strip())
            out.append(f'<blockquote class="note">{body}</blockquote>')
            quote.clear()

    def close_lists(level=-1):
        while lists and lists[-1] > level:
            lists.pop()
            out.append('</li></ul>')

    for raw in text.split('\n'):
        if code is not None:
            if raw.startswith('```'):
                out.append(f'<pre><code>{html.escape(chr(10).join(code))}</code></pre>')
                code = None
            else:
                code.append(raw)
            continue
        if raw.startswith('```'):
            flush_para(); flush_quote(); close_lists(); code = []
            continue
        if raw.startswith('>'):
            flush_para(); close_lists()
            quote.append(raw.lstrip('> ').strip())
            continue
        flush_quote()
        heading = re.match(r'^(#{1,6}) (.+)$', raw)
        if heading:
            flush_para(); close_lists()
            level = min(6, len(heading.group(1)) + heading_shift)
            title = heading.group(2)
            out.append(f'<h{level} id="{heading_anchor(report, slug(title))}">{inline(title, base, report)}</h{level}>')
            continue
        item = re.match(r'^(\s*)- (.*)$', raw)
        if item:
            flush_para()
            level = len(item.group(1)) // 2
            if lists and lists[-1] >= level:
                close_lists(level)
                if lists and lists[-1] == level:
                    out.append('</li><li>' + inline(item.group(2), base, report))
                    continue
            out.append('<ul><li>' + inline(item.group(2), base, report))
            lists.append(level)
            continue
        if not raw.strip():
            flush_para()
            if lists:
                continue
            continue
        if lists and raw.startswith('  '):
            out[-1] += ' ' + inline(raw.strip(), base, report)
            continue
        close_lists()
        para.append(raw.strip())
    if code is not None:
        out.append(f'<pre><code>{html.escape(chr(10).join(code))}</code></pre>')
    flush_para(); flush_quote(); close_lists()
    return '\n'.join(out)


def table_html(rows: list[list[str]], report: str) -> str:
    head, *body = rows
    if body and set(''.join(body[0])) <= set('-: '):
        body = body[1:]
    body = [row + [''] * max(0, len(head) - len(row)) for row in body]
    numeric = [all(re.match(r'^[\s\d.,%/+−–()kp<=×$—-]*$', r[i]) or not r[i] for r in body) if body else False
               for i in range(len(head))]
    attributes = [' class="num"' if is_numeric else '' for is_numeric in numeric]
    th = ''.join(f'<th{attributes[i]}>{inline(c, report)}</th>' for i, c in enumerate(head))
    trs = ''.join('<tr>' + ''.join(f'<td{attributes[i]}>{inline(c, report)}</td>'
                                   for i, c in enumerate(r)) + '</tr>' for r in body)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def render_report(summary: dict, report: str) -> str:
    parts = []
    for block in summary['blocks']:
        if 'markdown' in block:
            parts.append(markdown(block['markdown'], report, heading_shift=2))
        else:
            parts.append(table_html(block['table'], report))
    return '\n'.join(parts)


# ---------------------------------------------------------------- charts

PALETTE = {  # the infographic is a single-theme graphic with its own ground
    'paper': '#F5F7FA', 'surface': '#FFFFFF', 'ink': '#111111', 'ink2': '#555D67', 'rule': '#E1E5EA',
    'beam': '#007BFF', 'beam_soft': '#D7E8FF', 'beam_mid': '#8DBDFF', 'ref': '#8A949E', 'hatch': '#C5CCD3',
}
FONT = "'Inter', -apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
COND = FONT


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def forest(board: dict, x: float, y: float, width: float, row_h: float = 30, label_w: float = 112,
           value_w: float = 104, colors=PALETTE, css: bool = False,
           ticks: tuple[int, ...] = (0, 25, 50, 75, 100)) -> tuple[str, float]:
    """Dot-and-whisker chart on a fixed 0–100% scale. Returns (svg, height)."""
    c = (lambda key, fallback: f'var(--{key})') if css else (lambda key, fallback: colors[fallback])
    plot_x, plot_w = x + label_w, width - label_w - value_w
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


def _gap_range(board: dict) -> tuple[float, float]:
    pcts = [r['pass_pct'] for r in board['rows']]
    return min(pcts), max(pcts)


def _ceiling(board: dict) -> float:
    match = re.match(r'(\d+)', board['reference'].get('ceiling (answer excerpt)', '') or '')
    return float(match.group(1)) if match else 0.0


def _dumbbell(boards, x, y, width, P) -> tuple[list[str], float]:
    """No-search dot, search-arm range bar and answer-excerpt ceiling per harness.

    Labels live in their own columns: names on the left, values on the right,
    the legend above. Nothing is written on the track.
    """
    out = [_text(x, y, 'Pass rate on post-cutoff briefs (search gap bench)', 14, weight='600')]
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
        name = 'Claude Code' if board['harness'] == 'claude-code' else 'codex'
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
    out = [_text(x, y, 'Pass rate on documented-knowledge tasks (web search bakeoff, Claude Code)', 14, weight='600')]
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
    each = (width - gap) / 2
    height = 0.0
    for i, board in enumerate(boards):
        bx = x + i * (each + gap)
        name, model = (('Claude Code', 'claude-opus-5-5') if board['harness'] == 'claude-code'
                       else ('codex', 'gpt-6.1-sol'))
        out.append(_text(bx, y, name, 14, weight='600'))
        out.append(_text(bx + 9 * len(name) + 8, y, model, 12, P['ink2'], MONO))
        svg, h = forest(board, bx, y + 18, each, row_h=26, label_w=80, value_w=100, ticks=(0, 50, 100))
        out.append(svg)
        height = max(height, 18 + h)
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
    note, nh = _para(x, y + height + 12, 'Counts of verified items, one run per question; no 95% ranges were '
                     'published, so differences of a few items are within noise. Tavily sells no structured '
                     'extraction, so it was not run on the schema.', 104, 12, 16, fill=P['ink2'])
    return out + note, height + 12 + nh


def infographic(boards: list[dict], analysis: dict, h2h: dict | None = None) -> str:
    P = PALETTE
    W, M = 1200, 48
    gap_cc = next(b for b in boards if b['id'] == 'gap-claude-code')
    gap_cx = next(b for b in boards if b['id'] == 'gap-codex')
    wsb = next(b for b in boards if b['id'] == 'wsb-competitive')
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
        body.append(_text(M, 128, f'Results to date, 2026-09-26 to 2026-10-06 · {runs} graded agent runs · 2 coding '
                          f'agents · {providers} providers · 1 API study', 14, '#C9D3DA', MONO))
    else:
        body.append(_text(M, 128, f'Results to date, 2026-09-29 to 2026-10-05 · {runs} graded test runs · 2 AI coding '
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
                          'provider was used. Briefs need the right decision and at least 70% of the key facts.'),
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
    body.append(_text(M, y, 'Pass rates are shown with their 95% range. Where two ranges overlap, the data cannot '
                      'tell them apart.' + (' Finding 5 counts verified items instead.' if h2h else ''), 14, P['ink2']))

    # ---- findings
    y += 62
    body.append(_text(M, y, 'What we found', 26, P['ink'], COND, '700'))
    y += 34
    left_w, right_x = 380, M + 412
    right_w = W - M - right_x
    findings = [
        ('Past the training cutoff, search decides the outcome',
         f'Without search, both agents failed every post-cutoff brief (0%). With any search setup, '
         f'Claude Code passed {cc_lo:.0f}–{cc_hi:.0f}% and codex {cx_lo:.0f}–{cx_hi:.0f}%, close to the '
         f'{_ceiling(gap_cc):.0f}% and {_ceiling(gap_cx):.0f}% reached when handed the answer excerpt.',
         'For work that depends on recent events, search is not a tuning knob. It is the difference between '
         'failing and passing, and every provider tested closes most of the gap.',
         lambda yy: _dumbbell((gap_cc, gap_cx), right_x, yy, right_w, P)),
        ('No provider wins everywhere',
         f'The top provider was {cc_top["label"]} on Claude Code ({cc_top["passes"]}/{cc_top["cells"]}) and '
         f'{cx_top["label"]} on codex ({cx_top["passes"]}/{cx_top["cells"]}), and most intervals overlap. With '
         f'{gap_cx["rows"][0]["cells"]}–{gap_cc["rows"][0]["cells"]} test runs per provider, the bench cannot '
         'separate the leaders.',
         'Choose on cost, latency and integration, then measure on your own agent. Rankings did not carry over '
         'from one agent to the other.',
         lambda yy: _gap_forests((gap_cc, gap_cx), right_x, yy, right_w, P)),
        ('On documented knowledge, memory does most of the work',
         f'In the bakeoff, answering from memory passed {nos["passes"]}/{nos["cells"]} ({nos["pass_pct"]:.0f}%). '
         f'The best provider reached {best_wsb["pass_pct"]:.0f}%, within the margin of error, '
         f'while tokens per success rose from {tps[0]["tokens_per_success"]} ({tps[0]["label"]}) to as much as '
         f'{tps[-1]["tokens_per_success"]} ({tps[-1]["label"]}).',
         'Search pays off where training data runs out. On stable, well-documented questions it mostly adds '
         'tokens.',
         lambda yy: _wsb_forest(wsb, right_x, yy, right_w, P)),
        ('Agents still search like keyword users',
         f'Only {totals["natural_language_pct"]:.0f}% of searches about recent events read as plain questions, and Codex '
         f'leaned on site: filters ({site_lo:.0f}–{site_hi:.0f}% of its provider queries). Agents often skipped '
         'search and opened URLs they remembered. Runs did better when the primary source surfaced.',
         'The next gains are in how agents ask and what results surface: full-question queries, and primary '
         'sources first. These are correlations across runs, not controlled effects.',
         lambda yy: _tiles([
             (f'{totals["natural_language_pct"]:.0f}%', 'of searches about recent events read as plain questions; '
                                                         'the rest were keyword strings.'),
             (f'{exa["fetch_only_cells"]}/{exa["cells"]}', 'Exa bakeoff runs that fetched remembered URLs without '
                                                           f'searching (Firecrawl {fc["fetch_only_cells"]}/{fc["cells"]}, '
                                                           f'Tavily {tv["fetch_only_cells"]}/{tv["cells"]}, '
                                                           f'Parallel {pw["fetch_only_cells"]}/{pw["cells"]}).'),
             (f'{hit_pct:.0f}% vs {miss_pct:.0f}%', 'Claude Code pass rate with the primary source in its results '
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
            f'({max(s3["values"].values())} of {s3["of"]}), and {leader(s6)} filled a fixed schema most accurately '
            f'({max(s6["values"].values())} of {s6["of"]} values).',
            'Choose by job: discovery, rendering and structured extraction rewarded different providers. The study '
            'was run for a knowledge base about Exa, one of the four, so read its context note.',
            lambda yy: _h2h_panels(h2h, right_x, yy + 8, right_w, P)))
    for n, (headline, text, sowhat, chart) in enumerate(findings, start=1):
        left: list[str] = [_text(M, y + 4, f'FINDING {n}', 12, P['beam'], MONO, '700', spacing=1)]
        hl, hh = _para(M, y + 34, headline, 29, 24, 28, family=COND, weight='700')
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
        f'{cells[0]}–{cells[-1]} test runs per provider: ranges are wide, and neighbouring positions are not rankings.',
        ('In the agent benchmarks each provider ran through its own MCP server at default settings; the API study '
         'called tuned APIs directly.' if h2h else
         'Each provider ran through its own MCP server at a pinned version and default settings. Direct APIs, '
         'other search modes and other tools were not tested.'),
        'Grading used automatic checks and blinded model graders. Provider dollar spend was only partly metered.',
        f'Search behavior comes from {queries} logged queries; its links to pass rates are correlations.',
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


# ---------------------------------------------------------------- page

APPARATUS = {
    'pipeline': """flowchart LR
  subgraph catalog[Task catalogs]
    T1[production tasks<br/>web search bakeoff]
    T2[gap briefs<br/>post-cutoff events]
  end
  subgraph cell[One test run = task x search setup x repetition]
    W[fresh scratch<br/>workspace]
    H[agent<br/>Claude Code or Codex]
    M[metering proxy]
    V[(search provider MCP server<br/>pinned version)]
  end
  subgraph grade[Grading]
    D[deterministic<br/>validators]
    J[blinded judges<br/>vs captured sources]
  end
  T1 & T2 --> W --> H
  H <-->|tool calls| M <--> V
  H --> B[(evidence bundle<br/>transcript, final answer,<br/>setup audit, metrics)]
  B --> D & J --> R[reports/*/summary.json] --> S[this site]""",
    'isolation': """flowchart TB
  A[search setup contract] --> CC[Claude Code spawn<br/>--strict-mcp-config<br/>--tools: the setup's built-ins<br/>--allowedTools: the same set]
  A --> CX[codex spawn<br/>exec --sandbox read-only<br/>--disable shell_tool, browser_use,<br/>apps, plugins, ...]
  CC --> P1[search provider:<br/>one provider MCP server<br/>+ ToolSearch + Read]
  CC --> N1[built-in search:<br/>WebSearch + WebFetch + Read]
  CC --> F1[no-search / floor / ceiling:<br/>no tools]
  CX --> P2[search provider:<br/>one provider MCP server]
  CX --> N2[built-in search:<br/>codex --search]
  CX --> F2[no-search / floor / ceiling:<br/>no tools]
  P1 & N1 & F1 & P2 & N2 & F2 --> AU[setup audit:<br/>every observed tool call<br/>checked against the contract]""",
    'calibration': """flowchart LR
  T[candidate brief<br/>event after 2026-01-01] --> FL[floor: no search<br/>5 runs]
  T --> CE[ceiling: answer excerpt<br/>in the prompt, 5 runs]
  FL --> Q{floor passes ≤ 1<br/>and ceiling passes ≥ 4?}
  CE --> Q
  Q -- yes --> AD[admitted for this<br/>agent and model]
  Q -- no --> RJ[rejected]
  AD --> BAT[battery: every search setup x 3]
  BAT --> GC[gap closure =<br/>setup - floor / ceiling - floor]""",
    'grading': """sequenceDiagram
  participant A as Agent answer
  participant V as Schema validator
  participant S as Source capture
  participant J1 as Judge 1 (Claude Code)
  participant J2 as Judge 2 (Codex, gap bench)
  A->>V: JSON deliverable (brief, claims, citations)
  V-->>A: reject malformed answers
  A->>S: cited URLs
  S->>J1: readable source text + rubric (blinded to the search setup)
  S->>J2: same inputs
  J1-->>A: key-fact recall, unsupported claims, decision
  J2-->>A: independent labels, agreement recorded
  Note over A,J2: pass = right decision AND recall >= 0.7 AND unsupported <= 0.25""",
    'direct': """flowchart LR
  Q[pre-registered question sets<br/>from one knowledge base] --> E[Exa] & T[Tavily] & P[Parallel] & F[Firecrawl]
  E & T & P & F --> PO[(results pooled,<br/>deduplicated, shuffled)]
  PO --> J[blind model judges<br/>verify each claim on its page]
  J --> K{already in the<br/>knowledge base?}
  K --> A[attribution rejoined:<br/>credit only where the provider's<br/>own page states the claim]
  A --> R[reports/*/summary.json]""",
}


def ci_svg(row: dict, top_lo: float) -> str:
    lo, hi = row['ci']
    muted = row.get('flagged', False) or row['arm'] == 'no-search'
    color = 'var(--ref)' if muted else 'var(--beam)'
    return (f'<svg viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true">'
            f'<line x1="0" y1="11" x2="100" y2="11" stroke="var(--rule)" stroke-width="1" vector-effect="non-scaling-stroke"/>'
            f'<line x1="{top_lo:.1f}" y1="2" x2="{top_lo:.1f}" y2="20" stroke="var(--ink2)" stroke-width="1" stroke-dasharray="2 2" vector-effect="non-scaling-stroke"/>'
            f'<rect x="{lo:.1f}" y="6" width="{max(0.6, hi - lo):.1f}" height="10" fill="{"var(--hatch)" if muted else "var(--beam-soft)"}"/>'
            f'<line x1="{lo:.1f}" y1="11" x2="{hi:.1f}" y2="11" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke"/>'
            f'<rect x="{row["pass_pct"] - 0.9:.1f}" y="4" width="1.8" height="14" fill="{color}"/></svg>')


def board_html(board: dict) -> str:
    top_lo = board['top_lower_bound']
    has_gap = board['bench'] == 'GAP'
    head = ('<tr><th>Search setup</th><th class="num">Passed</th><th>95% range on a 0–100% scale</th>'
            + ('<th class="num">Gap closure</th>' if has_gap else '')
            + '<th class="num">Tokens per success</th><th>Notes</th></tr>')
    body = []
    for row in board['rows']:
        pill = ('<span class="pill top">overlaps top interval</span>' if row['overlaps_top']
                else '<span class="pill">below top interval</span>')
        if row['arm'] == 'no-search':
            pill = '<span class="pill">reference</span>'
        note = f'<div class="flag">{esc(row["note"])}</div>' if row['note'] and row['arm'] != 'no-search' else ''
        body.append(
            f'<tr><td>{esc(row["label"])}</td><td class="num">{row["passes"]}/{row["cells"]} · {row["pass_pct"]:.0f}%'
            f'<div class="flag">{row["ci"][0]:.0f}–{row["ci"][1]:.0f}%</div></td>'
            f'<td class="ci">{ci_svg(row, top_lo)}</td>'
            + (f'<td class="num">{esc(row["gap_closure"])}</td>' if has_gap else '')
            + f'<td class="num">{esc(row["tokens_per_success"])}</td><td>{pill}{note}</td></tr>')
    ref = ''
    if board['reference']:
        ref = '<p class="flag">' + ' · '.join(f'{esc(k)}: {esc(v)}' for k, v in board['reference'].items()) + '</p>'
    return (f'<section class="board" id="board-{board["id"]}"><h3>{esc(board["title"])}</h3>'
            f'<p class="scope">{esc(board["scope"])} Interval: {esc(board["interval"])}. The dashed line marks the '
            f'lower end of the top setup\'s range; badges describe overlap of individual ranges only. '
            f'Overlap does not establish statistical equivalence or test a difference between setups.</p>'
            f'<div class="tablewrap"><table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>{ref}'
            f'<p class="flag">{esc(board["token_accounting"])}</p>'
            f'<p class="flag">Source: <a href="#run-{board["source"]}">{esc(REPORT_TITLES[board["source"]])}</a>.</p></section>')


def mermaid(name: str) -> str:
    return f'<div class="diagram"><pre class="mermaid">\n{html.escape(APPARATUS[name], quote=False)}\n</pre></div>'


def configs_html(configs: list[dict]) -> str:
    rows = [['Search provider', 'Connection', 'Version / endpoint', 'Tools exposed to the agent']]
    for cfg in configs:
        rows.append([cfg['label'], 'hosted MCP via `mcp-remote`' if cfg['remote'] else 'stdio MCP server (npx)',
                     f'`{cfg["package"]}`' + (f' → `{cfg["remote"]}`' if cfg['remote'] else ''),
                     f'{len(cfg["tools"])}: ' + ', '.join(f'`{t}`' for t in cfg['tools'])])
    rows.append(['Built-in (Claude Code)', 'agent built-in', 'Claude Code 2.1.282', '`WebSearch`, `WebFetch` (refused before 2026-10-06), `Read`'])
    rows.append(['Built-in (Codex)', 'agent built-in', 'codex `--search`', 'built-in web search and page views'])
    return table_html(rows, 'apparatus')


SITE_URL = 'https://searchlightai.dev'
LACEY_URL = 'https://www.laceyenterprises.com'
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="32" height="32" role="img" '
        'aria-label="Searchlight"><circle cx="31" cy="67" r="22" fill="#111111"/>'
        '<path d="M34 69.5 L52.1 10.2 A62 62 0 0 1 91.1 45.3 Z" fill="#007BFF"/>'
        '<circle cx="34" cy="69.5" r="6.4" fill="#FFFFFF"/></svg>')

CSS = """
:root{--ink:#111111;--ink2:#4F5761;--muted:#6B7480;--paper:#FFFFFF;--soft:#F4F6F9;--rule:#E3E7EC;
--accent:#007BFF;--accent-soft:#EAF3FF;--charcoal:#404040;--radius:22px;--wrap:1120px;
--beam:var(--accent);--beam-soft:var(--accent-soft);--hatch:#C5CCD3;--ref:#8A949E;
--sans:'Inter',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;
color-scheme:light}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:var(--wrap);margin:0 auto;padding-inline:24px}
.brand{display:inline-flex;align-items:center;gap:10px;color:var(--ink);font-weight:500;letter-spacing:.2em;font-size:14px}
.brand:hover{text-decoration:none}.brand svg{width:28px;height:28px}
.top{border-bottom:1px solid var(--rule);background:var(--paper);position:sticky;top:0;z-index:5}
.top .wrap{display:flex;align-items:center;gap:16px 28px;min-height:72px}
.top .brand{margin-right:auto}
.nav{display:flex;align-items:center;gap:28px}
.nav a{color:var(--ink);font-size:15px;letter-spacing:.02em}.nav a.on{font-weight:600}
.btn{display:inline-block;border-radius:999px;padding:11px 22px;font-size:15px;font-weight:500;border:1.5px solid var(--ink);
color:var(--ink);background:transparent;line-height:1.2}
.btn:hover{text-decoration:none;background:var(--soft)}
.btn.solid{background:var(--ink);color:var(--paper)}.btn.solid:hover{background:#2A2A2A}
.top .gh{padding:8px 18px;font-size:14px}
section{padding-block:72px}
.soft{background:var(--soft)}
.eyebrow{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);font-weight:600;margin:0 0 14px}
h1,h2,h3{font-weight:500;letter-spacing:-.02em;line-height:1.12;text-wrap:balance;margin:0}
h1{font-size:clamp(38px,5.6vw,64px);max-width:21ch}
h2{font-size:clamp(28px,3.4vw,40px);margin-bottom:12px}
h3{font-size:20px;letter-spacing:-.01em}
.lede{font-size:clamp(18px,1.6vw,21px);color:var(--ink2);max-width:60ch;margin:22px 0 0}
.section-lede{color:var(--ink2);max-width:62ch;margin:0 0 36px}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:32px}
.facts{display:flex;flex-wrap:wrap;gap:12px 36px;margin-top:48px;padding-top:28px;border-top:1px solid var(--rule)}
.facts div{font-size:15px;color:var(--ink2)}.facts strong{display:block;font-size:30px;font-weight:500;color:var(--ink);letter-spacing:-.02em}
.grid{display:grid;gap:20px}
.g2{grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr))}
.g4{grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr))}
.card{background:var(--paper);border:1px solid var(--rule);border-radius:var(--radius);padding:28px}
.finding .big{font-size:clamp(34px,3.6vw,46px);font-weight:500;letter-spacing:-.03em;color:var(--accent);line-height:1.05}
.finding h3{margin:14px 0 8px}.finding p{margin:0;color:var(--ink2)}
.step .n{width:36px;height:36px;border-radius:50%;background:var(--ink);color:var(--paper);display:grid;place-items:center;font-weight:600}
.step h3{margin:18px 0 8px}.step p{margin:0;color:var(--ink2);font-size:15px}
.example .kind{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
.example h3{margin:10px 0 14px}
.example blockquote{margin:0 0 16px;padding:16px 18px;border-radius:14px;background:var(--soft);font-size:15px;color:var(--ink)}
.example dl{margin:0;display:grid;gap:10px}.example dt{font-size:13px;font-weight:600}.example dd{margin:0;color:var(--ink2);font-size:15px}
.report{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:40px;align-items:center}
.report ul{padding-left:20px;color:var(--ink2)}.report li{margin:6px 0}
.shot{display:block;border-radius:var(--radius);overflow:hidden;border:1px solid var(--rule);background:var(--soft);max-height:460px}
.shot img{display:block;width:100%;height:auto}
.foot{background:var(--charcoal);color:#E8EAED;padding-block:56px 32px}
.foot a{color:#FFFFFF;text-decoration:underline;text-underline-offset:3px}
.foot .brand{color:#FFFFFF;text-decoration:none}.foot .brand svg circle:nth-of-type(1){fill:#FFFFFF}.foot .brand svg circle:nth-of-type(2){fill:#404040}
.foot-grid{display:grid;gap:32px;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}
.foot h4{margin:0 0 10px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#B9BEC5;font-weight:600}
.foot p{margin:0;font-size:15px;color:#E8EAED}
.foot-base{margin-top:40px;padding-top:20px;border-top:1px solid #5A5A5A;font-size:14px;color:#B9BEC5}
.page-head{padding-block:64px 24px}
.doc section{padding-block:28px}
.doc h2{font-size:30px;margin-top:8px}
.doc h3{margin:28px 0 10px}
.doc p,.doc li{max-width:78ch}
.caveats{list-style:none;padding:0;display:grid;gap:12px}
.caveats li{background:var(--soft);border-radius:14px;padding:16px 18px}.caveats strong{display:block;margin-bottom:4px}
.infographic{border:1px solid var(--rule);border-radius:var(--radius);overflow-x:auto;background:#F5F7FA}
.infographic svg{display:block;width:100%;height:auto;min-width:720px}
figure{margin:0}figcaption{font-size:14px;color:var(--muted);margin-top:10px}
.tablewrap{overflow-x:auto;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:600;color:var(--ink2);font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.ci{min-width:180px}
.flag{font-size:13px;color:var(--muted)}
.pill{display:inline-block;font-size:12px;border-radius:999px;padding:2px 10px;background:var(--soft);color:var(--ink2)}
.pill.top{background:var(--accent-soft);color:#0057B3}
.board{border:1px solid var(--rule);border-radius:var(--radius);padding:24px;margin:20px 0}
.board h3{margin:0 0 8px}.scope{color:var(--ink2);font-size:15px}
details.run{border:1px solid var(--rule);border-radius:14px;padding:12px 18px;margin:12px 0;background:var(--paper)}
details.run summary{cursor:pointer;font-weight:600}
code{font-family:var(--mono);font-size:.9em;background:var(--soft);padding:1px 5px;border-radius:5px;overflow-wrap:anywhere}
pre{font-family:var(--mono);font-size:13px;background:var(--soft);border-radius:14px;padding:16px;overflow-x:auto;line-height:1.5}
pre code{background:none;padding:0}
.diagram{overflow-x:auto;border:1px solid var(--rule);border-radius:var(--radius);padding:20px;margin:14px 0;background:var(--paper)}
pre.mermaid{background:none;padding:0;margin:0;font-size:.85rem;text-align:center}
.terms{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px}
.terms div{background:var(--soft);border-radius:14px;padding:14px 16px;font-size:15px}.terms b{display:block}
@media (max-width:640px){
section{padding-block:48px}
.top{position:static}
.top .wrap{display:grid;grid-template-columns:1fr auto;grid-template-areas:"brand gh" "nav nav";gap:8px 12px;
min-height:0;padding-block:12px 8px}
.top .brand{grid-area:brand;margin:0}
.top .gh{grid-area:gh;padding:7px 16px}
.nav{grid-area:nav;gap:24px;overflow-x:auto;scrollbar-width:none}
.nav a{white-space:nowrap;padding-block:6px}
.eyebrow{font-size:12px;letter-spacing:.12em}
.actions .btn{flex:1 1 auto;text-align:center}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:18px 24px}
.facts strong{font-size:26px}
.card{padding:20px}
.board{padding:16px}
.infographic{overflow:hidden}
.infographic svg{min-width:0}
.diagram{padding:14px}
.diagram svg{min-width:600px;max-width:none!important}
}
"""

EXAMPLES = [
    ('gap', 'gap-brief-python-security-march-2026', 'Research brief', 'Security releases the AI has never seen',
     'Python shipped these security releases on March 3, 2026, after the AI models\' training data ends. '
     'Without search, an agent can only guess.',
     'The brief must reach the right decision, cover at least 70% of the key facts and keep unsupported claims '
     'to 25% or less, checked against the original source.'),
    ('gap', 'gap-brief-npm-token-scope-2026', 'Research brief', 'A platform policy that just changed',
     'GitHub changed what npm access tokens can do on July 31, 2026. The answer only exists in recent sources.',
     'Same bar as every brief: right decision, key facts covered, few unsupported claims.'),
    ('gap', 'gap-code-jwt-middleware', 'Code fix', 'Patch a security advisory in real code',
     'The fix shipped in a library release after the cutoff. The agent has to find the advisory and the release '
     'that fixes it, then change the code.',
     'Hidden tests must pass in an isolated workspace. Weakening the security check to make them pass fails.'),
    ('production', 'list-build-kubernetes-122-api-removals', 'Documented knowledge', 'A well-documented technical question',
     'This has been public for years and is probably in the AI\'s training data. It tests whether search adds '
     'anything beyond memory.',
     'The list must match the official release notes, scored by a grader that never sees which search provider '
     'was used.'),
]


def _catalog_prompt(root: Path, catalog: str, task_id: str) -> tuple[str, str]:
    """Prompt text and primary source for a catalog task; empty strings when absent."""
    import yaml

    path = root / 'catalogs' / catalog / 'tasks.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError:
        return '', ''
    tasks = data.get('tasks', data) if isinstance(data, dict) else data
    task = next((t for t in tasks or [] if isinstance(t, dict) and t.get('id') == task_id), None)
    if not task:
        return '', ''
    prompt = ' '.join(str(task.get('prompt') or '').split())
    source = str((task.get('oracle') or {}).get('source_url') or '')
    return prompt, source


def _shorten(text: str, limit: int = 260) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0].rstrip(',;:')
    return cut + ' …'


def headline(boards: list[dict], analysis: dict) -> dict:
    """The few numbers the landing page and infographic both quote."""
    gap_cc = next(b for b in boards if b['id'] == 'gap-claude-code')
    gap_cx = next(b for b in boards if b['id'] == 'gap-codex')
    wsb = next(b for b in boards if b['id'] == 'wsb-competitive')
    nos = next(r for r in wsb['rows'] if r['arm'] == 'no-search')
    best = max((r for r in wsb['rows'] if r['arm'] != 'no-search'), key=lambda r: r['pass_pct'])
    tps = sorted(wsb['rows'], key=lambda r: float(r['tokens_per_success'].rstrip('k')))
    cells = sorted({r['cells'] for b in boards for r in b['rows']})
    return {
        'runs': sum(r['cells'] for b in boards for r in b['rows']),
        'providers': len({r['arm'] for b in boards for r in b['rows']} - {'native', 'no-search'}),
        'cc': _gap_range(gap_cc), 'cx': _gap_range(gap_cx),
        'cc_top': gap_cc['rows'][0], 'cx_top': gap_cx['rows'][0],
        'wsb_none': nos, 'wsb_best': best,
        'token_ratio': float(tps[-1]['tokens_per_success'].rstrip('k')) / float(tps[0]['tokens_per_success'].rstrip('k')),
        'natural_pct': analysis['gap_totals']['natural_language_pct'],
        'min_runs': cells[0], 'max_runs': cells[-1],
    }


def _nav(active: str) -> str:
    links = [('index.html', 'Overview'), ('results.html', 'Results'), ('methodology.html', 'Methodology')]
    on = ' class="on"'
    items = ''.join(f'<a href="{href}"{on if href == active else ""}>{label}</a>' for href, label in links)
    return (f'<header class="top"><div class="wrap"><a class="brand" href="index.html">{LOGO}<span>SEARCHLIGHT</span></a>'
            f'<nav class="nav" aria-label="Site">{items}</nav><a class="btn gh" href="{REPO_URL}">GitHub</a></div></header>')


def _footer() -> str:
    return (f'<footer class="foot"><div class="wrap"><div class="foot-grid">'
            f'<div><a class="brand" href="index.html">{LOGO}<span>SEARCHLIGHT</span></a>'
            '<p style="margin-top:14px">An open benchmark of what web search does for AI coding agents.</p></div>'
            f'<div><h4>License</h4><p>Code, tasks and published results are released under the '
            f'<a href="{REPO_URL}/blob/main/LICENSE">Apache License 2.0</a>.</p></div>'
            f'<div><h4>Questions</h4><p><a href="{REPO_URL}/issues/new">Open an issue on GitHub</a>. If you run a search '
            'service we tested and our setup misrepresents it, tell us: corrections are rerun and published beside the '
            'original.</p></div>'
            f'<div><h4>Source</h4><p><a href="{REPO_URL}">github.com/laceyenterprises/searchlight</a></p>'
            f'<p style="margin-top:8px"><a href="{REPO_URL}/tree/main/reports">Raw report data</a></p></div>'
            f'</div><div class="foot-base">A <a href="{LACEY_URL}">Lacey Enterprises</a> project · '
            '© 2026 Lacey Enterprises LLC</div></div></footer>')


CARD_SIZE = (1200, 630)
CARD_SCALE = 2
CARD_KEY = 'searchlight-card-source-sha256'


def _social_meta(title: str, description: str, active: str, alt: str) -> str:
    url = f'{SITE_URL}/' if active == 'index.html' else f'{SITE_URL}/{active}'
    image = f'{SITE_URL}/social-card.png'
    width, height = (n * CARD_SCALE for n in CARD_SIZE)
    tags = [('property', 'og:type', 'website'), ('property', 'og:site_name', 'Searchlight'),
            ('property', 'og:title', title), ('property', 'og:description', description),
            ('property', 'og:url', url), ('property', 'og:image', image),
            ('property', 'og:image:type', 'image/png'), ('property', 'og:image:width', str(width)),
            ('property', 'og:image:height', str(height)), ('property', 'og:image:alt', alt),
            ('name', 'twitter:card', 'summary_large_image'), ('name', 'twitter:title', title),
            ('name', 'twitter:description', description), ('name', 'twitter:image', image),
            ('name', 'twitter:image:alt', alt)]
    return (f'<link rel="canonical" href="{esc(url)}">'
            + ''.join(f'<meta {kind}="{key}" content="{esc(value)}">' for kind, key, value in tags))


def _document(title: str, description: str, active: str, main: str, script: str = '',
              social_title: str = '', card_alt: str = '') -> str:
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" '
             'href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" '
             'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'<meta name="description" content="{esc(description)}">'
            f'{_social_meta(social_title or title, description, active, card_alt or CARD_ALT_DEFAULT)}'
            '<link rel="icon" type="image/svg+xml" href="logo.svg">'
            f'\n<title>{esc(title)}</title>\n{fonts}\n<style>{CSS}</style>\n</head><body>\n'
            f'{_nav(active)}\n<main>\n{main}\n</main>\n{_footer()}\n{script}</body></html>\n')


def landing(root: Path, boards: list[dict], analysis: dict) -> str:
    h = headline(boards, analysis)
    (cc_lo, cc_hi), (cx_lo, cx_hi) = h['cc'], h['cx']
    nos, best = h['wsb_none'], h['wsb_best']
    findings = [
        ('0% → ' + f'{min(cc_lo, cx_lo):.0f}–{max(cc_hi, cx_hi):.0f}%', 'Search decides anything recent',
         'Without search, both agents failed every task about events after their training data ends. With any of '
         f'the {h["providers"]} search providers, they passed most of them.'),
        ('No single winner', 'The best provider depends on the agent',
         f'{h["cc_top"]["label"]} led on Claude Code and {h["cx_top"]["label"]} on Codex, and most providers were '
         'statistically tied. Test on the agent you actually use.'),
        (f'{nos["pass_pct"]:.0f}% vs {best["pass_pct"]:.0f}%', 'On well-known topics, memory did most of the work',
         f'Answering from memory alone passed {nos["pass_pct"]:.0f}% of documented-knowledge tasks; the best search '
         f'provider reached {best["pass_pct"]:.0f}%, a gap within the margin of error, while using up to '
         f'{h["token_ratio"]:.0f}× the tokens.'),
        (f'{h["natural_pct"]:.0f}%', 'Agents search like it is 2010',
         f'Only {h["natural_pct"]:.0f}% of the agents\' searches were written as plain questions; the rest were '
         'keyword strings. Agents did better when the original source showed up in the results.'),
    ]
    finding_html = ''.join(f'<div class="card finding"><div class="big">{esc(big)}</div><h3>{esc(title)}</h3>'
                           f'<p>{esc(text)}</p></div>' for big, title, text in findings)
    steps = [
        ('Give the agent a real job', 'A research brief about something that changed after the AI\'s training '
                                      'data ends, a code fix, or a question with a well-documented answer.'),
        ('Change only the search provider', 'Same agent, same instructions every time. Each test plugs in one '
                                            'search provider at its default settings, alongside two controls: no '
                                            'search, and the agent\'s own built-in search.'),
        ('Repeat it, in isolation', f'Every test runs in a fresh, sealed workspace and is repeated three times: '
                                    f'{h["min_runs"]}–{h["max_runs"]} test runs per provider. Every search and page '
                                    'visit is logged.'),
        ('Grade blind', 'Answers are checked against the original sources by graders that never see which '
                        'search provider was used.'),
    ]
    step_html = ''.join(f'<div class="card step"><div class="n">{i}</div><h3>{esc(t)}</h3><p>{esc(d)}</p></div>'
                        for i, (t, d) in enumerate(steps, start=1))
    examples = []
    for catalog, task_id, kind, title, why, graded in EXAMPLES:
        prompt, source = _catalog_prompt(root, catalog, task_id)
        if not prompt:
            continue
        src = (f' <a href="{esc(source)}">Source</a>.' if source.startswith('https://') else '')
        examples.append(f'<div class="card example"><div class="kind">{esc(kind)}</div><h3>{esc(title)}</h3>'
                        f'<blockquote>“{esc(_shorten(prompt))}”</blockquote><dl>'
                        f'<dt>Why search matters</dt><dd>{esc(why)}{src}</dd>'
                        f'<dt>How it is graded</dt><dd>{esc(graded)}</dd></dl></div>')
    main = f"""<section><div class="wrap">
<p class="eyebrow">Open benchmark · by Lacey Enterprises</p>
<h1>Does web search make AI coding agents better at real work?</h1>
<p class="lede">Searchlight gives AI coding agents real jobs, changes only which search provider they can use, and grades
the results blind. The tasks, the code and the data are all open.</p>
<div class="actions"><a class="btn solid" href="results.html">See the results</a><a class="btn" href="#how">How it works</a>
<a class="btn" href="{REPO_URL}">View on GitHub</a></div>
<div class="facts"><div><strong>{h['runs']}</strong>graded test runs</div><div><strong>2</strong>AI coding agents</div>
<div><strong>{h['providers']}</strong>search providers</div><div><strong>2</strong>benchmarks</div></div>
</div></section>

<section class="soft" id="findings"><div class="wrap">
<h2>What we found</h2>
<p class="section-lede">Results from {h['runs']} graded test runs between September 29 and October 5, 2026, using Claude Code and
Codex.</p>
<div class="grid g2">{finding_html}</div>
</div></section>

<section id="how"><div class="wrap">
<h2>How it works</h2>
<p class="section-lede">A controlled experiment: everything stays the same except the search provider.</p>
<div class="grid g4">{step_html}</div>
<p style="margin-top:24px"><a href="methodology.html">Read the full methodology →</a></p>
</div></section>

<section class="soft" id="examples"><div class="wrap">
<h2>What we asked the agents to do</h2>
<p class="section-lede">Real tasks from the benchmark, quoted from the task catalogs.</p>
<div class="grid g2">{''.join(examples)}</div>
</div></section>

<section id="report"><div class="wrap report">
<div><h2>The report</h2>
<p class="section-lede" style="margin-bottom:16px">Four studies, published with their data, methodology and corrections.</p>
<ul><li><strong>Search gap bench:</strong> research briefs and code fixes about events after the models' training data.</li>
<li><strong>Web search bakeoff:</strong> research questions with well-documented answers.</li>
<li><strong>How agents used search:</strong> what the agents actually typed and opened, across both benchmarks.</li>
<li><strong>Search API head-to-head:</strong> four search APIs called directly on a research workload, with the full
pre-registered record.</li></ul>
<div class="actions"><a class="btn solid" href="results.html">Read the full results</a>
<a class="btn" href="methodology.html">Methodology</a></div></div>
<a class="shot" href="results.html#infographic"><img src="infographic.svg" alt="Searchlight results infographic" loading="lazy"></a>
</div></section>"""
    card = card_facts(boards, analysis)
    return _document('Searchlight', card['description'], 'index.html', main,
                     social_title=card['title'], card_alt=card['alt'])


CARD_ALT_DEFAULT = 'Searchlight: an open benchmark of what web search does for AI coding agents.'


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

CARD_MARK = ('<svg class="mark" viewBox="0 0 100 100" aria-hidden="true">'
             '<circle cx="31" cy="67" r="22" fill="#FFFFFF"/>'
             '<path d="M34 69.5 L52.1 10.2 A62 62 0 0 1 91.1 45.3 Z" fill="#007BFF"/>'
             '<circle cx="34" cy="69.5" r="6.4" fill="#0A0C10"/></svg>')


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


def results_language(markup: str) -> str:
    """Use executive-facing terms in text, preserving report anchors and URLs.

    Input is generated HTML without scripts or styles. Published source reports
    and machine-readable leaderboard fields retain their original terminology.
    """
    terms = {'arm': 'setup', 'arms': 'setups', 'cell': 'run', 'cells': 'runs',
             'harness': 'agent', 'harnesses': 'agents'}

    def replace(match):
        word = match.group()
        replacement = terms[word.lower()]
        return replacement.capitalize() if word[0].isupper() else replacement

    return ''.join(part if i % 2 else re.sub(r'\b(?:arms?|cells?|harness(?:es)?)\b', replace, part, flags=re.I)
                   for i, part in enumerate(re.split(r'(<[^>]*>)', markup)))


def results_page(reports: dict, boards: list[dict], analysis: dict, body_only: bool = False) -> str:
    # The head-to-head report uses "cell" for a table cell, "arm" for its search and research arms and "harness" for
    # its own scripts, not the agent benchmarks' glossary terms, so its full report keeps its words.
    protected = {}
    runs = []
    for name in REPORTS:
        summary = reports[name]['summary']
        section = (
            f'<section id="run-{name}"><h3>{esc(REPORT_TITLES[name])} <span class="flag">({name[:10]})</span></h3>'
            f'<details class="run" {"open" if name == REPORTS[-1] else ""}><summary>Full recorded report</summary>'
            f'{render_report(summary, name)}</details>'
            f'<details class="run" id="method-{name}"><summary>Methodology for this study</summary>'
            f'{markdown(reports[name]["methodology"], name + "-method", heading_shift=2, base=name)}</details>'
            f'<p class="flag">Files: <a href="{REPO_URL}/tree/main/reports/{name}">reports/{name}/</a></p></section>')
        if name == H2H:
            protected[f'\x00{name}\x00'] = section
            section = f'\x00{name}\x00'
        runs.append(section)
    h = headline(boards, analysis)
    h2h = head_to_head(reports)
    head = ['Measure', *h2h['providers']]
    rows = [head] + [[m['label'] + (f' (of {m["of"]})' if m['of'] else '')]
                     + [str(m['values'][p]) if p in m['values'] else '—' for p in h2h['providers']]
                     for m in h2h['measures']]
    probe = h2h['monitors']
    probe_text = (f'An Exa-only Monitors probe made {probe["runs"]} daily runs on {probe["monitors"]} competitors\' pricing and '
                  f'changelog pages and reported {probe["changes"]} changes'
                  + (f', {probe["confirmed"]} of them confirmed on the page; {probe["monitors_passing"]} of '
                     f'{probe["monitors"]} monitors surfaced a real, dated pricing or product change.'
                     if 'confirmed' in probe else '.'))
    direct = f"""<section id="head-to-head"><h2>Search APIs called directly</h2>
<p>A separate study ({esc(REPORT_TITLES[H2H])}, 2026-09-26 to 2026-10-06) sent the same pre-registered research questions to
four search APIs directly, with each vendor's best-practice parameters, and had blind model judges verify every counted item
on its source page. It measures what each API adds to a knowledge base already built with built-in search, not what an
agent does with it.</p>
{table_html(rows, H2H)}
<p class="flag">{esc(h2h['note'])} A dash means not run: Tavily sells no list-building or structured-extraction product.
{esc(probe_text)}</p>
<p class="flag">Context: the study was run while building a go-to-market knowledge base about Exa, one of the four providers,
and its questions come from that knowledge base. Full report: <a href="#run-{H2H}">{esc(REPORT_TITLES[H2H])}</a>.</p>
</section>"""
    main = f"""<div class="wrap doc">
<div class="page-head"><p class="eyebrow">Results</p><h1>What the benchmark measured</h1>
<p class="lede">Two agent benchmarks, two AI coding agents, {h['providers']} search providers and {h['runs']} graded test runs,
plus a study that called four search APIs directly. Every number on this page is generated from the published report
data.</p></div>

<section id="before"><h2>Read this before comparing providers</h2>
<ul class="caveats">
<li><strong>Small samples</strong>{h['min_runs']} to {h['max_runs']} test runs per provider. The 95% ranges overlap widely, so
neighbouring positions are not rankings, and no provider beat answering from memory on the documented-knowledge benchmark by
a statistically clear margin.</li>
<li><strong>One connection per provider</strong>In the two agent benchmarks, each provider ran through its own official MCP
server at a pinned version and default settings; other search modes and tools were not tested there. The search API
head-to-head called the APIs directly with tuned parameters, and its results do not transfer to agents, or back.</li>
<li><strong>The agent matters</strong>Rankings changed between Claude Code and Codex. A result on one agent does not transfer
to another.</li>
<li><strong>Built-in search rerun</strong>In the original runs Claude Code's built-in search could not open pages. After the
fix it was rerun on 2026-10-06, and its rows here come from that rerun. The original rows stay on record in the reports.</li>
<li><strong>Cost coverage</strong>Token counts are reported; provider dollar spend was only partly metered, so dollar
comparisons are omitted.</li>
<li><strong>Who graded</strong>Automatic checks where possible; otherwise model graders that compared answers with the captured
sources without seeing which provider produced them.</li>
</ul></section>

<section id="infographic"><h2>Results at a glance</h2>
<figure><div class="infographic">{infographic(boards, analysis, h2h)}</div>
<figcaption><a href="infographic.svg">Open the infographic full size</a>. Generated from
<code>reports/*/summary.json</code>.</figcaption></figure></section>

<section id="leaderboard"><h2>Leaderboards</h2>
<p>Ordered by observed pass rate. The bar is the 95% range; the tick is the observed rate. Machine-readable data:
<a href="leaderboard.json">leaderboard.json</a>.</p>
{''.join(board_html(b) for b in boards)}
</section>

{direct}

<section id="runs"><h2>Full reports</h2>
<p>The recorded reports, transcribed and checked by <code>scripts/check_reports.py</code>, with plain-language labels
for search setups, test runs and agents.</p>
{''.join(runs)}
</section>
</div>"""
    main = results_language(main)
    for marker, section in protected.items():
        main = main.replace(marker, section)
    if body_only:
        return main
    return _document('Searchlight Results', 'Searchlight results: pass rates, leaderboards and full reports.',
                     'results.html', main)


def methodology_page(configs: list[dict], target: str = 'pages', body_only: bool = False) -> str:
    claude_argv = ('claude --print --output-format stream-json --verbose --no-session-persistence \\\n'
                   '  --model claude-opus-5-5 --mcp-config <run>/claude-mcp.json --strict-mcp-config \\\n'
                   '  --tools ToolSearch,Read --allowedTools \'mcp__<provider>__*\'     # one search provider\n'
                   '  --tools WebSearch,WebFetch,Read --allowedTools WebSearch,WebFetch   # built-in search (fixed)\n'
                   '  --tools \'\' --allowedTools \'\'                                       # no search, floor, ceiling')
    codex_argv = ('codex [--search] exec --json --ephemeral --skip-git-repo-check --sandbox read-only \\\n'
                  '  --model gpt-6.1-sol --disable shell_tool --disable unified_exec \\\n'
                  '  --disable browser_use --disable browser_use_external --disable computer_use \\\n'
                  '  --disable in_app_browser --disable apps --disable plugins --disable remote_plugin ...\n'
                  '# --search only for built-in search; a provider setup adds one MCP server via the run config')
    main = f"""<div class="wrap doc">
<div class="page-head"><p class="eyebrow">Methodology</p><h1>How Searchlight tests search</h1>
<p class="lede">A controlled experiment: the same agent, model and instructions in every test run, with only the search
provider changing. This page describes the apparatus in full.</p></div>

<section id="terms"><h2>Terms used in the reports</h2>
<div class="terms">
<div><b>Search setup (report term: arm)</b>One way of giving the agent search: one provider's MCP server, the agent's built-in
search, or no search at all.</div>
<div><b>Test run (report term: cell)</b>One task, one search setup, one repetition, graded once.</div>
<div><b>Agent (report term: harness)</b>The AI coding agent program: Claude Code or Codex.</div>
<div><b>Floor and ceiling</b>The same task with no search (floor) and with the answer excerpt handed over (ceiling).</div>
<div><b>Gap closure</b>How much of the distance from floor to ceiling a search setup recovers.</div>
<div><b>95% range</b>The Wilson interval around a pass rate. Overlapping ranges mean the data cannot separate two setups.</div>
</div></section>

<section id="pipeline"><h2>From task to report</h2>
<p>The runner gives the agent a fresh scratch workspace, starts it with exactly the tools its search setup allows, records
every tool call and the final answer, and grades the answer without telling the grader which setup produced it.</p>
{mermaid('pipeline')}</section>

<section id="isolation"><h2>What each setup can touch</h2>
<p>Search setups differ only in what the agent can use to search. Claude Code is started with
<code>--strict-mcp-config</code>, so the only MCP server it can reach is the provider's, and <code>--tools</code> limits its
built-ins. Codex runs read-only with its shell, browser, app and plugin features disabled. After each run an audit compares
every observed tool call with the setup's contract; a call outside it marks the run contaminated.</p>
{mermaid('isolation')}
<h3>Claude Code invocation</h3><pre>{esc(claude_argv)}</pre>
<h3>Codex invocation</h3><pre>{esc(codex_argv)}</pre>
<h3>Limits per test run</h3>
{table_html([['Limit', 'Search gap bench', 'Web search bakeoff'],
             ['Wall clock', '1,200 s', '300 to 1,200 s (set per task)'],
             ['Total tokens', '1,000,000', '120,000 to 350,000 (set per task)'],
             ['Search provider calls', '60', '15 to 50 (set per task)'],
             ['Agent start-up timeout', '120 s', '120 s'],
             ['Repetitions', '3 (battery), 5 (calibration)', '3']], 'apparatus')}
</section>

<section id="calibration"><h2>Which tasks count</h2>
<p>A search gap task counts only if it is genuinely beyond the model: with no search the agent must fail, and with the answer
excerpt handed over it must succeed. Calibration is repeated for each agent and model.</p>
{mermaid('calibration')}
<pre>gap closure  =  (setup pass rate − floor pass rate) / (ceiling pass rate − floor pass rate)

   floor          search setup           ceiling
   0% ●─────────────────●──────────────────● 95%
       └──── closed ────┘└──── remaining ────┘</pre></section>

<section id="grading"><h2>How answers are graded</h2>{mermaid('grading')}
<p>Search gap briefs pass when the decision is right, at least 70% of key facts are covered and at most 25% of claims are
unsupported. Code fixes pass when hidden tests pass in an isolated workspace. Bakeoff tasks use automatic checks where a task
has one, and otherwise a single blinded grader scores the task rubric.</p></section>

<section id="providers"><h2>Search provider configurations</h2>
<p>Pinned in <a href="{REPO_URL}/blob/main/config/provider-arm-tools.yaml">config/provider-arm-tools.yaml</a>. Every tool a
provider's server advertised was available to the agent; no provider tool was hidden.</p>
{configs_html(configs)}</section>

<section id="direct-api"><h2>The search API head-to-head</h2>
<p>One study tests the search APIs themselves rather than an agent using them. It sends the same pre-registered research
questions to Exa, Tavily, Parallel and Firecrawl, each with the parameters its own documentation recommends, on the same
day and with the same number of results. The results are pooled and shuffled, and blind model judges verify every counted
claim on its source page. A provider is credited only when its own result page states the claim, and a claim counts as new
only if the knowledge base the questions came from did not already have it. Unlike the benchmarks above, each question ran
once, so the study reports counts without intervals.</p>
{mermaid('direct')}
<p>Its sets, endpoints, amendments and costs are in the study's
<a href="results.html#method-{H2H}">methodology</a>, and its full pre-registered record is in
<a href="{REPO_URL}/tree/main/reports/{H2H}">reports/{H2H}/</a>.</p></section>

<section id="reproduce"><h2>Reproduce, correct, contribute</h2>
<p>Install Searchlight, run <code>sew doctor</code>, then follow each study's <code>reproduce.md</code>. Raw transcripts and
provider responses are not distributed, so exact replay of the published numbers is not possible; a new run measures the
same method. The one exception is the search API head-to-head's Monitors probe, whose raw responses are published. If you run a tested service and a configuration here misrepresents it, open an issue with the configuration you
recommend: corrections are rerun and published beside the original, never silently replaced.</p>
<pre><code>pipx install 'git+{REPO_URL}'
sew doctor
python3 scripts/check_reports.py
python3 scripts/build_site.py --check</code></pre></section>
</div>"""
    if body_only:
        return main
    script = ''
    if target == 'pages':
        script = (f'<script src="{MERMAID_URL}" integrity="{MERMAID_INTEGRITY}" crossorigin="anonymous"></script><script>'
                  "mermaid.initialize({startOnLoad:true,securityLevel:'strict',theme:'neutral'});</script>\n")
    return _document('Searchlight Methodology', 'How Searchlight tests web search for AI coding agents.',
                     'methodology.html', main, script)


def page(reports: dict, boards: list[dict], configs: list[dict], target: str) -> tuple[str, str]:
    """Single-file preview: the results and methodology bodies, without scripts or the document shell."""
    analysis = behavior(reports)
    head = f'<title>Searchlight Results</title>\n<style>{CSS}</style>'
    body = results_page(reports, boards, analysis, body_only=True) + methodology_page(configs, target, body_only=True)
    return head, body


def build(root: Path = ROOT) -> dict[str, str]:
    reports = load_reports(root)
    boards = leaderboards(reports)
    configs = arm_configs(root)
    analysis = behavior(reports)
    h2h = head_to_head(reports)
    data = {
        'generated_from': [f'reports/{name}/summary.json' for name in REPORTS],
        'note': 'Pass rates and intervals as published; WSB intervals are Wilson 95% computed from published counts. '
                'Overlapping intervals are not rankings.',
        'boards': boards,
        'search_api_head_to_head': h2h,
    }
    return {
        'index.html': landing(root, boards, analysis),
        'results.html': results_page(reports, boards, analysis),
        'methodology.html': methodology_page(configs),
        'logo.svg': LOGO + '\n',
        'infographic.svg': infographic(boards, analysis, h2h) + '\n',
        'leaderboard.json': json.dumps(data, indent=2, ensure_ascii=False) + '\n',
        'social-card.html': social_card(boards, analysis),
    }


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
    files = build()
    site = ROOT / 'site'
    if args.check:
        stale = [name for name, content in files.items()
                 if not (site / name).is_file() or (site / name).read_text(encoding='utf-8') != content]
        if stale:
            print('site/ is stale; run python3 scripts/build_site.py: ' + ', '.join(stale), file=sys.stderr)
            return 1
        problem = card_problem(site, files['social-card.html'])
        if problem:
            print(f'site/ is stale: {problem}; run python3 scripts/render_social_card.py', file=sys.stderr)
            return 1
        print('Site is current: ' + ', '.join(sorted(files)))
        return 0
    site.mkdir(exist_ok=True)
    for name, content in files.items():
        (site / name).write_text(content, encoding='utf-8')
    print('Site written: ' + ', '.join(f'site/{name}' for name in sorted(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
