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
import html
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from check_reports import REPORTS  # noqa: E402
from sew import harnesses  # noqa: E402

REPO_URL = 'https://github.com/laceyenterprises/searchlight'
MERMAID_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js'
# SHA-384 of the pinned CDN asset; update together with MERMAID_URL.
MERMAID_INTEGRITY = 'sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I'
ARM_LABEL = {
    'no-search': 'no-search', 'native': 'native', 'brave': 'Brave', 'tavily': 'Tavily', 'exa': 'Exa',
    'parallel-web': 'Parallel', 'firecrawl': 'Firecrawl', 'perplexity': 'Perplexity',
}
REPORT_TITLES = {
    '2026-09-29-web-search-bakeoff': 'Web search bakeoff',
    '2026-10-03-search-gap-bench': 'Search gap bench',
    '2026-10-05-agent-search-behavior': 'How agents used search',
}


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


def heading_harness(heading: str) -> str:
    """The registered harness id a GAP table heading names, as a whole token."""
    named = [h for h in harnesses.ids() if re.search(rf'(?<![\w-]){re.escape(h)}(?![\w-])', heading)]
    if len(named) != 1:
        raise SystemExit(f'GAP heading must name exactly one registered harness: {heading!r}')
    return named[0]


def leaderboards(reports: dict) -> list[dict]:
    boards = []
    native_wsb = next(r for r in behavior(reports)['bakeoff_rows'] if r['arm'] == 'native')
    gap = reports['2026-10-03-search-gap-bench']['summary']
    for intro, table in tables(gap):
        if table[0][:3] != ['Arm', 'Gap closure (95% CI)', 'Pass rate (Wilson 95% CI)']:
            continue
        heading = [line for line in intro.splitlines() if line.startswith('### ')][-1]
        harness = heading_harness(heading)
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
                'note': 'WebFetch refused by the harness (search only); not comparable'
                        if harness == 'claude-code' and arm == 'native' else '',
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
            'note': 'reference arm' if arm == 'no-search' else
                    (f"WebFetch refused in {native_wsb['cells_with_refused_calls']} of {native_wsb['cells']} cells"
                     if arm == 'native' else ''),
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
    'paper': '#F3F5F2', 'surface': '#FFFFFF', 'ink': '#15202A', 'ink2': '#4A5866', 'rule': '#D3DAD5',
    'beam': '#C2700F', 'beam_soft': '#F2E0C4', 'ref': '#7A8893', 'hatch': '#B9C2C9',
}
FONT = "'IBM Plex Sans', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
COND = "'IBM Plex Sans Condensed', 'Arial Narrow', 'Helvetica Neue', Arial, sans-serif"


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def forest(board: dict, x: float, y: float, width: float, row_h: float = 30, label_w: float = 112,
           value_w: float = 104, colors=PALETTE, css: bool = False) -> tuple[str, float]:
    """Dot-and-whisker chart on a fixed 0–100% scale. Returns (svg, height)."""
    c = (lambda key, fallback: f'var(--{key})') if css else (lambda key, fallback: colors[fallback])
    plot_x, plot_w = x + label_w, width - label_w - value_w
    sx = lambda pct: plot_x + plot_w * pct / 100  # noqa: E731
    rows = board['rows']
    height = row_h * len(rows) + 34
    parts = []
    for tick in (0, 25, 50, 75, 100):
        tx = sx(tick)
        parts.append(f'<line x1="{tx:.1f}" y1="{y:.1f}" x2="{tx:.1f}" y2="{y + row_h * len(rows):.1f}" '
                     f'stroke="{c("rule", "rule")}" stroke-width="1"/>')
        parts.append(f'<text x="{tx:.1f}" y="{y + row_h * len(rows) + 18:.1f}" text-anchor="middle" '
                     f'font-family="{MONO}" font-size="11" fill="{c("ink2", "ink2")}">{tick}%</text>')
    for i, row in enumerate(rows):
        cy = y + row_h * i + row_h / 2
        lo, hi = row['ci']
        muted = bool(row['note']) and row['arm'] != 'no-search'
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


def infographic(boards: list[dict], analysis: dict) -> str:
    P = PALETTE
    W = 1200
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
    search_lo = {b['id']: min(r['pass_pct'] for r in b['rows']) for b in (gap_cc, gap_cx)}
    search_hi = {b['id']: max(r['pass_pct'] for r in b['rows']) for b in (gap_cc, gap_cx)}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} 1760" width="{W}" height="1760" '
           f'role="img" aria-labelledby="ig-title ig-desc">',
           '<title id="ig-title">Searchlight results to date</title>',
           '<desc id="ig-desc">Pass rates with 95% intervals for search providers on two benchmarks and two '
           'coding-agent harnesses, plus how the agents used their search tools.</desc>',
           f'<rect width="{W}" height="1760" fill="{P["paper"]}"/>']
    # header
    out.append(f'<rect x="0" y="0" width="{W}" height="132" fill="{P["ink"]}"/>')
    out.append(f'<path d="M {W} 0 L {W} 132 L {W - 420} 132 Z" fill="{P["beam"]}" opacity="0.22"/>')
    out.append(f'<text x="40" y="62" font-family="{COND}" font-size="40" font-weight="700" letter-spacing="2" fill="{P["paper"]}">SEARCHLIGHT</text>')
    out.append(f'<text x="40" y="96" font-family="{FONT}" font-size="18" fill="#C9D3DA">What changes when coding agents can search: results to date (2026-09-29 to 2026-10-05)</text>')
    out.append(f'<text x="{W - 40}" y="96" text-anchor="end" font-family="{MONO}" font-size="13" fill="#C9D3DA">8 arms · 2 harnesses · 2 benchmarks</text>')

    # panel 1: search decides post-cutoff jobs
    y = 180
    out.append(f'<text x="40" y="{y}" font-family="{COND}" font-size="24" font-weight="600" fill="{P["ink"]}">1 · On post-cutoff jobs, search decides the outcome</text>')
    out.append(f'<text x="40" y="{y + 24}" font-family="{FONT}" font-size="14" fill="{P["ink2"]}">Search gap bench: briefs about events after both models\' training data. Pass rate per arm group.</text>')
    for i, (board, ceiling) in enumerate(((gap_cc, gap_cc['reference']['ceiling (answer excerpt)']),
                                          (gap_cx, gap_cx['reference']['ceiling (answer excerpt)']))):
        by = y + 70 + i * 74
        x0, w = 420, 700
        sx = lambda pct: x0 + w * pct / 100  # noqa: E731
        name = 'Claude Code' if board['harness'] == 'claude-code' else 'codex'
        out.append(f'<text x="40" y="{by + 20}" font-family="{FONT}" font-size="16" font-weight="600" fill="{P["ink"]}">{name}</text>')
        out.append(f'<text x="40" y="{by + 40}" font-family="{MONO}" font-size="12" fill="{P["ink2"]}">{board["rows"][0]["cells"]} cells per arm</text>')
        out.append(f'<line x1="{x0}" y1="{by + 22}" x2="{x0 + w}" y2="{by + 22}" stroke="{P["rule"]}" stroke-width="2"/>')
        lo, hi = search_lo[board['id']], search_hi[board['id']]
        out.append(f'<rect x="{sx(lo):.1f}" y="{by + 12}" width="{sx(hi) - sx(lo):.1f}" height="20" rx="4" fill="{P["beam"]}"/>')
        out.append(f'<text x="{sx(lo) - 10:.1f}" y="{by + 27}" text-anchor="end" font-family="{MONO}" font-size="13" font-weight="600" fill="{P["beam"]}">search arms {lo:.0f}–{hi:.0f}%</text>')
        out.append(f'<circle cx="{sx(0):.1f}" cy="{by + 22}" r="9" fill="{P["ink"]}"/>')
        out.append(f'<text x="{sx(0) + 16:.1f}" y="{by + 27}" font-family="{MONO}" font-size="13" fill="{P["ink"]}">no search 0%</text>')
        ceil = float(re.match(r'(\d+)', ceiling).group(1))
        out.append(f'<path d="M {sx(ceil) - 6:.1f} {by + 2} L {sx(ceil) + 6:.1f} {by + 2} L {sx(ceil):.1f} {by + 10} Z" fill="{P["ink"]}"/>')
        out.append(f'<text x="{sx(ceil):.1f}" y="{by - 4}" text-anchor="end" font-family="{MONO}" font-size="12" fill="{P["ink"]}">answer handed over {ceil:.0f}%</text>')

    # panel 2: GAP forest plots
    y = 420
    out.append(f'<text x="40" y="{y}" font-family="{COND}" font-size="24" font-weight="600" fill="{P["ink"]}">2 · Which arm closes the gap depends on the agent</text>')
    out.append(f'<text x="40" y="{y + 24}" font-family="{FONT}" font-size="14" fill="{P["ink2"]}">Pass rate with Wilson 95% interval (bar). Overlapping intervals are not rankings.</text>')
    for i, board in enumerate((gap_cc, gap_cx)):
        bx = 40 + i * 580
        name = 'Claude Code · claude-opus-5-5' if board['harness'] == 'claude-code' else 'codex · gpt-6.1-sol'
        out.append(f'<text x="{bx}" y="{y + 62}" font-family="{FONT}" font-size="15" font-weight="600" fill="{P["ink"]}">{name}</text>')
        svg, _ = forest(board, bx, y + 78, 540)
        out.append(svg)
    out.append(f'<text x="40" y="{y + 352}" font-family="{FONT}" font-size="12" fill="{P["ink2"]}">* Claude Code native arm: the harness refused its WebFetch calls, so it searched without fetching pages. Not comparable to the provider rows; fixed for future runs.</text>')

    # panel 3: WSB
    y = 820
    out.append(f'<text x="40" y="{y}" font-family="{COND}" font-size="24" font-weight="600" fill="{P["ink"]}">3 · On documented knowledge, search adds less</text>')
    out.append(f'<text x="40" y="{y + 24}" font-family="{FONT}" font-size="14" fill="{P["ink2"]}">Web search bakeoff, 14 competitive tasks × 3 on Claude Code, regraded. No arm differs from no-search at 95% confidence.</text>')
    svg, h = forest(wsb, 40, y + 50, 760)
    out.append(svg)
    nos = next(r for r in wsb['rows'] if r['arm'] == 'no-search')
    for j, line in enumerate(['Answering from memory alone', f'passed {nos["passes"]} of {nos["cells"]} cells here.',
                              'On the post-cutoff gap bench the', 'same condition passed 0%.']):
        out.append(f'<text x="830" y="{y + 70 + 22 * j}" font-family="{FONT}" font-size="14" fill="{P["ink"]}">{esc(line)}</text>')
    out.append(f'<text x="830" y="{y + 172}" font-family="{FONT}" font-size="13" fill="{P["ink2"]}">Tokens per success ranged from</text>')
    tps = sorted(wsb['rows'], key=lambda r: float(r['tokens_per_success'].rstrip('k')))
    out.append(f'<text x="40" y="{y + 50 + h + 14}" font-family="{FONT}" font-size="12" fill="{P["ink2"]}">* native: the harness refused most WebFetch calls; no-search is the reference arm. Intervals computed from published counts.</text>')
    out.append(f'<text x="830" y="{y + 192}" font-family="{MONO}" font-size="13" fill="{P["ink2"]}">{tps[0]["tokens_per_success"]} ({tps[0]["label"]}) to {tps[-1]["tokens_per_success"]} ({tps[-1]["label"]}).</text>')

    # panel 4: behavior
    y = 1210
    out.append(f'<text x="40" y="{y}" font-family="{COND}" font-size="24" font-weight="600" fill="{P["ink"]}">4 · How the agents actually searched</text>')
    out.append(f'<text x="40" y="{y + 24}" font-family="{FONT}" font-size="14" fill="{P["ink2"]}">From {totals["queries"] + analysis["bakeoff_totals"]["queries"]} logged search queries in both benchmarks.</text>')
    stats = [
        (f'{totals["natural_language_pct"]:.0f}%', 'of gap-bench queries read as natural language',
         'Agents wrote keyword strings even where a tool asked for a description of the page.'),
        (f'{site_lo:.0f}–{site_hi:.0f}%', 'of codex provider queries used site:',
         'Claude Code used structured domain filters instead, where a tool offered one.'),
        (f'{brows["exa"]["fetch_only_cells"]}/{brows["exa"]["cells"]}', 'Exa bakeoff cells fetched without searching',
         f'Similar for Firecrawl {brows["firecrawl"]["fetch_only_cells"]}/{brows["firecrawl"]["cells"]}, Tavily {brows["tavily"]["fetch_only_cells"]}/{brows["tavily"]["cells"]}, Parallel {brows["parallel-web"]["fetch_only_cells"]}/{brows["parallel-web"]["cells"]}: agents opened remembered URLs.'),
        (f'{hit_pct:.0f}% vs {miss_pct:.0f}%', 'Claude Code pass rate with vs without the primary source in results',
         f'{hit[0]}/{hit[1]} against {miss[0]}/{miss[1]} cells on the gap bench, pooled over arms.'),
    ]
    for i, (big, label, detail) in enumerate(stats):
        sx0 = 40 + (i % 2) * 570
        sy = y + 56 + (i // 2) * 150
        out.append(f'<rect x="{sx0}" y="{sy}" width="550" height="130" rx="6" fill="{P["surface"]}" stroke="{P["rule"]}"/>')
        out.append(f'<rect x="{sx0}" y="{sy}" width="6" height="130" fill="{P["beam"]}"/>')
        out.append(f'<text x="{sx0 + 26}" y="{sy + 52}" font-family="{COND}" font-size="40" font-weight="700" fill="{P["beam"]}">{esc(big)}</text>')
        out.append(f'<text x="{sx0 + 26}" y="{sy + 80}" font-family="{FONT}" font-size="15" font-weight="600" fill="{P["ink"]}">{esc(label)}</text>')
        words, line, lines = detail.split(), '', []
        for word in words:
            if len(line) + len(word) > 70:
                lines.append(line); line = word
            else:
                line = f'{line} {word}'.strip()
        lines.append(line)
        for j, text in enumerate(lines[:2]):
            out.append(f'<text x="{sx0 + 26}" y="{sy + 102 + j * 18}" font-family="{FONT}" font-size="13" fill="{P["ink2"]}">{esc(text)}</text>')

    # footer

    y = 1584
    out.append(f'<line x1="40" y1="{y}" x2="{W - 40}" y2="{y}" stroke="{P["rule"]}" stroke-width="2"/>')
    notes = [
        'Read before comparing: 18–42 cells per arm; intervals overlap widely, so adjacent positions are not rankings.',
        'Each vendor ran through its own MCP server at a pinned version and default settings. Direct APIs, other modes and other tools were not tested.',
        'Grading: validators and blinded model judges (Claude; codex as second judge on the gap bench). Vendor spend only partly metered.',
        'Rankings changed between harnesses, so a result on one agent does not transfer to another.',
    ]
    for i, note in enumerate(notes):
        out.append(f'<text x="40" y="{y + 32 + i * 24}" font-family="{FONT}" font-size="14" fill="{P["ink"]}">{esc(note)}</text>')
    out.append(f'<text x="40" y="{y + 150}" font-family="{MONO}" font-size="13" fill="{P["ink2"]}">{REPO_URL.replace("https://", "")} · data: reports/*/summary.json · method: reports/*/methodology.md</text>')
    out.append('</svg>')
    return '\n'.join(out)


# ---------------------------------------------------------------- page

CSS = """
:root{--paper:#F3F5F2;--surface:#FFFFFF;--ink:#15202A;--ink2:#4A5866;--rule:#D3DAD5;--beam:#B4680D;
--beam-soft:#F2E0C4;--ref:#7A8893;--hatch:#C6CDD2;--code:#E9EDEA;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#0F151B;--surface:#161E26;--ink:#E4EAEE;
--ink2:#9DABB6;--rule:#2B3641;--beam:#F0A43A;--beam-soft:#3D2E17;--ref:#8C9AA6;--hatch:#3A4651;--code:#1D2730;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#0F151B;--surface:#161E26;--ink:#E4EAEE;--ink2:#9DABB6;--rule:#2B3641;--beam:#F0A43A;
--beam-soft:#3D2E17;--ref:#8C9AA6;--hatch:#3A4651;--code:#1D2730;color-scheme:dark}
body{background:var(--paper);color:var(--ink);font:16px/1.6 'IBM Plex Sans',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;margin:0}
.wrap{max-width:1120px;margin:0 auto;padding-inline:20px;padding-block:0 64px}
header.top{background:var(--ink);color:var(--paper);padding-block:28px 22px}
header.top .wrap{padding-block:0}
.wordmark{font:700 2.1rem/1 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.08em;margin:0}
.tag{margin:.5rem 0 0;color:var(--hatch);max-width:62ch}
nav.toc{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--paper);border-bottom:1px solid var(--rule)}
nav.toc .wrap{display:flex;gap:18px;flex-wrap:wrap;padding-block:10px;font:500 .9rem 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.04em;text-transform:uppercase}
nav.toc a{color:var(--ink2);text-decoration:none}nav.toc a:hover,nav.toc a:focus-visible{color:var(--beam)}
h2{font:600 1.7rem/1.2 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;margin:3.2rem 0 .6rem;text-wrap:balance}
h3{font:600 1.25rem/1.3 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;margin:2rem 0 .4rem;text-wrap:balance}
h4,h5,h6{font:600 1.05rem/1.35 'IBM Plex Sans',sans-serif;margin:1.6rem 0 .3rem}
p,li{max-width:72ch}a{color:var(--beam)}a:focus-visible{outline:2px solid var(--beam);outline-offset:2px}
code{font:.86em 'IBM Plex Mono',ui-monospace,Menlo,monospace;background:var(--code);padding:.08em .3em;border-radius:3px}
pre{background:var(--code);padding:14px 16px;border-radius:6px;overflow-x:auto;font-size:.85rem;line-height:1.5}pre code{background:none;padding:0}
.lede{font-size:1.1rem;color:var(--ink2);max-width:66ch}
.tablewrap{overflow-x:auto;margin:1rem 0 1.4rem;border:1px solid var(--rule);border-radius:6px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:7px 10px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{font:600 .78rem 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--ink2);background:var(--paper)}
td.num,th.num{text-align:right;font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
.note{border-left:4px solid var(--beam);background:var(--beam-soft);margin:1.2rem 0;padding:10px 16px;border-radius:0 6px 6px 0}
.note p{margin:.2rem 0}
.caveats{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin:1rem 0 0;padding:0;list-style:none}
.caveats li{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:12px 14px;font-size:.92rem;max-width:none}
.caveats strong{display:block;font:600 .8rem 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--beam);margin-bottom:2px}
.board{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:18px 18px 8px;margin:1.4rem 0}
.board h3{margin-top:0}.board .scope{color:var(--ink2);font-size:.92rem;margin:.2rem 0 .8rem}
.board table{font-size:.92rem}.board td.ci{min-width:220px;width:36%}
.ci svg{display:block;width:100%;height:22px}
.flag{font-size:.8rem;color:var(--ink2)}
.pill{display:inline-block;font:600 .72rem 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.05em;text-transform:uppercase;padding:1px 7px;border-radius:999px;border:1px solid var(--rule);color:var(--ink2);white-space:nowrap}
.pill.top{border-color:var(--beam);color:var(--beam)}
figure{margin:1.4rem 0}figcaption{color:var(--ink2);font-size:.88rem;margin-top:.4rem;max-width:72ch}
.infographic{overflow-x:auto;border:1px solid var(--rule);border-radius:8px;background:#F3F5F2}
.infographic svg{display:block;width:100%;height:auto;min-width:720px}
.diagram{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:14px;overflow-x:auto}
pre.mermaid{background:none;padding:0;margin:0;font-size:.85rem;text-align:center}
details.run{border-top:1px solid var(--rule);margin-top:1rem;padding-top:.6rem}
details.run>summary{cursor:pointer;font:600 1rem 'IBM Plex Sans Condensed','Arial Narrow',sans-serif;letter-spacing:.03em;color:var(--beam)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.argv{font:.8rem/1.55 'IBM Plex Mono',ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
footer.end{border-top:1px solid var(--rule);margin-top:3rem;padding-top:1rem;color:var(--ink2);font-size:.9rem}
@media (max-width:560px){h2{font-size:1.4rem}.wordmark{font-size:1.7rem}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""

APPARATUS = {
    'pipeline': """flowchart LR
  subgraph catalog[Task catalogs]
    T1[production tasks<br/>web search bakeoff]
    T2[gap briefs<br/>post-cutoff events]
  end
  subgraph cell[One cell = task x arm x repetition]
    W[fresh scratch<br/>workspace]
    H[harness<br/>Claude Code or codex]
    M[metering proxy]
    V[(vendor MCP server<br/>pinned version)]
  end
  subgraph grade[Grading]
    D[deterministic<br/>validators]
    J[blinded judges<br/>vs captured sources]
  end
  T1 & T2 --> W --> H
  H <-->|tool calls| M <--> V
  H --> B[(evidence bundle<br/>transcript, final answer,<br/>arm audit, metrics)]
  B --> D & J --> R[reports/*/summary.json] --> S[this site]""",
    'isolation': """flowchart TB
  A[arm contract] --> CC[Claude Code spawn<br/>--strict-mcp-config<br/>--tools: the arm's built-ins<br/>--allowedTools: the same set]
  A --> CX[codex spawn<br/>exec --sandbox read-only<br/>--disable shell_tool, browser_use,<br/>apps, plugins, ...]
  CC --> P1[provider arm:<br/>one vendor MCP server<br/>+ ToolSearch + Read]
  CC --> N1[native arm:<br/>WebSearch + WebFetch + Read]
  CC --> F1[no-search / floor / ceiling:<br/>no tools]
  CX --> P2[provider arm:<br/>one vendor MCP server]
  CX --> N2[native arm:<br/>codex --search]
  CX --> F2[no-search / floor / ceiling:<br/>no tools]
  P1 & N1 & F1 & P2 & N2 & F2 --> AU[arm audit:<br/>every observed tool call<br/>checked against the contract]""",
    'calibration': """flowchart LR
  T[candidate brief<br/>event after 2026-01-01] --> FL[floor: no search<br/>5 runs]
  T --> CE[ceiling: answer excerpt<br/>in the prompt, 5 runs]
  FL --> Q{floor passes ≤ 1<br/>and ceiling passes ≥ 4?}
  CE --> Q
  Q -- yes --> AD[admitted for this<br/>harness and model]
  Q -- no --> RJ[rejected]
  AD --> BAT[battery: every arm x 3]
  BAT --> GC[gap closure =<br/>arm - floor / ceiling - floor]""",
    'grading': """sequenceDiagram
  participant A as Agent answer
  participant V as Schema validator
  participant S as Source capture
  participant J1 as Judge 1 (Claude Code)
  participant J2 as Judge 2 (codex, gap bench)
  A->>V: JSON deliverable (brief, claims, citations)
  V-->>A: reject malformed answers
  A->>S: cited URLs
  S->>J1: readable source text + rubric (blinded to arm)
  S->>J2: same inputs
  J1-->>A: key-fact recall, unsupported claims, decision
  J2-->>A: independent labels; agreement recorded
  Note over A,J2: pass = right decision AND recall >= 0.7 AND unsupported <= 0.25""",
}


def ci_svg(row: dict, top_lo: float) -> str:
    lo, hi = row['ci']
    muted = bool(row['note'])
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
    head = ('<tr><th>Arm</th><th class="num">Passed</th><th>95% interval on a 0–100% scale</th>'
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
            f'lower end of the top arm\'s interval; badges describe overlap of individual intervals only. '
            f'Overlap does not establish statistical equivalence or test a difference between arms.</p>'
            f'<div class="tablewrap"><table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>{ref}'
            f'<p class="flag">{esc(board["token_accounting"])}</p>'
            f'<p class="flag">Source: <a href="#run-{board["source"]}">{esc(REPORT_TITLES[board["source"]])}</a>.</p></section>')


def mermaid(name: str) -> str:
    return f'<div class="diagram"><pre class="mermaid">\n{html.escape(APPARATUS[name], quote=False)}\n</pre></div>'


def configs_html(configs: list[dict]) -> str:
    rows = [['Arm', 'Interface', 'Version / endpoint', 'Tools exposed to the agent']]
    for cfg in configs:
        rows.append([cfg['label'], 'hosted MCP via `mcp-remote`' if cfg['remote'] else 'stdio MCP server (npx)',
                     f'`{cfg["package"]}`' + (f' → `{cfg["remote"]}`' if cfg['remote'] else ''),
                     f'{len(cfg["tools"])}: ' + ', '.join(f'`{t}`' for t in cfg['tools'])])
    rows.append(['native (Claude Code)', 'harness built-in', 'Claude Code 2.1.282', '`WebSearch`, `WebFetch` (refused before 2026-10-06), `Read`'])
    rows.append(['native (codex)', 'harness built-in', 'codex `--search`', 'built-in web search and page views'])
    return table_html(rows, 'apparatus')


def page(reports: dict, boards: list[dict], configs: list[dict], target: str) -> str:
    analysis = behavior(reports)
    ig = infographic(boards, analysis)
    runs = []
    for name in REPORTS:
        summary = reports[name]['summary']
        runs.append(
            f'<section id="run-{name}"><h2>{esc(REPORT_TITLES[name])} <span class="flag">({name[:10]})</span></h2>'
            f'<details class="run" {"open" if name == REPORTS[-1] else ""}><summary>Full recorded report</summary>'
            f'{render_report(summary, name)}</details>'
            f'<details class="run" id="method-{name}"><summary>Methodology</summary>'
            f'{markdown(reports[name]["methodology"], name + "-method", heading_shift=2, base=name)}</details>'
            f'<p class="flag">Files: <a href="{REPO_URL}/tree/main/reports/{name}">reports/{name}/</a></p></section>')
    claude_argv = ('claude --print --output-format stream-json --verbose --no-session-persistence \\\n'
                   '  --model claude-opus-5-5 --mcp-config <cell>/claude-mcp.json --strict-mcp-config \\\n'
                   '  --tools ToolSearch,Read --allowedTools \'mcp__<vendor>__*\'        # provider arm\n'
                   '  --tools WebSearch,WebFetch,Read --allowedTools WebSearch,WebFetch   # native arm (fixed)\n'
                   '  --tools \'\' --allowedTools \'\'                                       # no-search, floor, ceiling')
    codex_argv = ('codex [--search] exec --json --ephemeral --skip-git-repo-check --sandbox read-only \\\n'
                  '  --model gpt-6.1-sol --disable shell_tool --disable unified_exec \\\n'
                  '  --disable browser_use --disable browser_use_external --disable computer_use \\\n'
                  '  --disable in_app_browser --disable apps --disable plugins --disable remote_plugin ...\n'
                  '# --search only on the native arm; provider arms add one MCP server via the cell config')
    script = ''
    if target == 'pages':
        script = (f'<script src="{MERMAID_URL}" integrity="{MERMAID_INTEGRITY}" crossorigin="anonymous"></script><script>'
                  "mermaid.initialize({startOnLoad:true,securityLevel:'strict',theme:"
                  "(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'neutral')});</script>")
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
             '&family=IBM+Plex+Sans+Condensed:wght@500;600;700&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&display=swap">')
    head = f'<title>Searchlight Results</title>\n{fonts}\n<style>{CSS}</style>'
    body = f"""<header class="top"><div class="wrap"><p class="wordmark">SEARCHLIGHT</p>
<p class="tag">An open benchmark of what web search does for coding agents: which search arm helps an agent finish a real job,
at what token cost, and how agents actually use the tools they are given.</p></div></header>
<nav class="toc" aria-label="Sections"><div class="wrap"><a href="#results">Results</a><a href="#before">Read first</a>
<a href="#leaderboard">Leaderboard</a><a href="#apparatus">Test apparatus</a><a href="#runs">Runs</a><a href="#reproduce">Reproduce</a></div></nav>
<main class="wrap">
<section id="results"><h2>Results to date</h2>
<p class="lede">Two benchmarks, two coding-agent harnesses, eight retrieval arms,
{sum(r['cells'] for b in boards for r in b['rows'] if r['arm'] != 'no-search')} graded provider and native cells in the
leaderboard below, and a transcript analysis of how the agents searched.
Every number on this page is generated from the published report data in <code>reports/</code>.</p>
<figure><div class="infographic">{ig}</div>
<figcaption>Infographic generated by <code>scripts/build_site.py</code> from <code>reports/*/summary.json</code>. The same image is
<a href="{REPO_URL}/blob/main/site/infographic.svg">site/infographic.svg</a>.</figcaption></figure></section>

<section id="before"><h2>Read this before comparing vendors</h2>
<ul class="caveats">
<li><strong>Small samples</strong>18 to 42 cells per arm. Intervals overlap widely; adjacent positions are not rankings, and no
bakeoff arm differs from answering without search at 95% confidence.</li>
<li><strong>One interface per vendor</strong>Each vendor ran through its own MCP server at a pinned version with default settings.
Direct APIs, other search modes and other tools were not tested.</li>
<li><strong>The agent matters</strong>Rankings changed between Claude Code and codex. Parallel led on one harness and tied last on the
other. A result on one agent does not transfer to another.</li>
<li><strong>Native arm defect</strong>Claude Code's native arm could not fetch pages: the harness refused WebFetch because only
WebSearch was pre-approved. Its rows are flagged and not comparable. Fixed for future runs.</li>
<li><strong>Cost coverage</strong>GAP tokens per success counts all agent tokens, including failures. WSB excludes ungraded and
unmeasured cells and lacks per-arm coverage counts. Vendor dollar spend was metered only partly (off on the gap bench), so
dollar comparisons are omitted here.</li>
<li><strong>Who graded</strong>Deterministic validators where possible; otherwise blinded model judges against captured sources
(Claude primary, codex second judge on the gap bench). Judges never saw which arm produced an answer.</li>
</ul></section>

<section id="leaderboard"><h2>Leaderboard</h2>
<p>Ordered by observed pass rate. The bar is the 95% interval; the tick is the observed rate. Token accounting and coverage are
defined beside each benchmark's table. The leaderboard updates automatically when a new report lands in
<code>reports/</code>; machine-readable data is in <a href="leaderboard.json">leaderboard.json</a>.</p>
{''.join(board_html(b) for b in boards)}
</section>

<section id="apparatus"><h2>Test apparatus</h2>
<p>Each <strong>cell</strong> is one task, one arm and one repetition. The runner gives the agent a fresh scratch workspace, starts
the harness with exactly the tools its arm allows, records every tool call and the final answer into an evidence bundle, and
grades the answer without telling the grader which arm produced it.</p>
<h3>From catalog to report</h3>{mermaid('pipeline')}
<h3>What each arm can touch</h3>
<p>An arm differs from another only in its retrieval surface. Claude Code is started with <code>--strict-mcp-config</code>, so the
only MCP server it can reach is the arm's vendor server, and <code>--tools</code> limits its built-ins. Codex runs read-only with its
shell, browser, app and plugin features disabled. After each cell an arm audit compares every observed tool call with the arm
contract; a call outside the contract marks the cell contaminated.</p>
{mermaid('isolation')}
<div class="grid2"><div><h4>Claude Code invocation</h4><pre class="argv">{esc(claude_argv)}</pre></div>
<div><h4>codex invocation</h4><pre class="argv">{esc(codex_argv)}</pre></div></div>
<h3>Per-cell limits</h3>
{table_html([['Limit', 'Gap bench', 'Web search bakeoff'],
             ['Wall clock per cell', '1,200 s', '300 to 1,200 s (set per task)'],
             ['Total tokens per cell', '1,000,000', '120,000 to 350,000 (set per task)'],
             ['Provider calls per cell', '60', '15 to 50 (set per task)'],
             ['Harness boot timeout', '120 s', '120 s'],
             ['Repetitions', '3 (battery), 5 (calibration)', '3']], 'apparatus')}
<h3>How a gap-bench task is admitted</h3>
<p>A task counts only if it is genuinely beyond the model: with no search the agent must fail, and with the answer excerpt
handed over it must succeed. Calibration is repeated for each harness and model.</p>
{mermaid('calibration')}
<pre class="argv">gap closure  =  (arm pass rate − floor pass rate) / (ceiling pass rate − floor pass rate)

   floor            arm                 ceiling
   0% ●─────────────────●──────────────────● 95%
       └──── closed ────┘└──── remaining ────┘</pre>
<h3>How answers are graded</h3>{mermaid('grading')}
<h3>Arm configurations</h3>
<p>Pinned in <a href="{REPO_URL}/blob/main/config/provider-arm-tools.yaml">config/provider-arm-tools.yaml</a>. Every tool a server
advertised was available to the agent; no vendor tool was hidden.</p>
{configs_html(configs)}
</section>

<section id="runs"><h2 style="margin-bottom:0">Runs</h2>
<p>Each run is the recorded report, transcribed into <code>summary.json</code> and checked by <code>scripts/check_reports.py</code>.</p>
{''.join(runs)}
</section>

<section id="reproduce"><h2>Reproduce, correct, contribute</h2>
<p>Install Searchlight, run <code>sew doctor</code>, then follow each run's <code>reproduce.md</code>. Raw transcripts and provider
responses are not distributed, so exact replay of the published aggregates is not possible; a new run measures the same
method. If you maintain a tested service and a configuration here misrepresents it, open an issue with the configuration you
recommend: corrections are rerun and published beside the original, never silently replaced.</p>
<pre><code>pipx install 'git+{REPO_URL}'
sew doctor
python3 scripts/check_reports.py
python3 scripts/build_site.py --check</code></pre>
</section>
<footer class="end">Searchlight is Apache-2.0. Built from <a href="{REPO_URL}">{REPO_URL.replace('https://', '')}</a>.
Generated by <code>scripts/build_site.py</code>; do not edit by hand.</footer>
</main>
{script}"""
    return head, body


def document(parts: tuple[str, str]) -> str:
    head, body = parts
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<meta name="description" content="Searchlight: an open benchmark of web search for coding agents.">'
            f'\n{head}\n</head><body>\n{body}\n</body></html>\n')


def build(root: Path = ROOT) -> dict[str, str]:
    reports = load_reports(root)
    boards = leaderboards(reports)
    configs = arm_configs(root)
    data = {
        'generated_from': [f'reports/{name}/summary.json' for name in REPORTS],
        'note': 'Pass rates and intervals as published; WSB intervals are Wilson 95% computed from published counts. '
                'Overlapping intervals are not rankings.',
        'boards': boards,
    }
    return {
        'index.html': document(page(reports, boards, configs, 'pages')),
        'infographic.svg': infographic(boards, behavior(reports)) + '\n',
        'leaderboard.json': json.dumps(data, indent=2, ensure_ascii=False) + '\n',
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
        print('Site is current: ' + ', '.join(sorted(files)))
        return 0
    site.mkdir(exist_ok=True)
    for name, content in files.items():
        (site / name).write_text(content, encoding='utf-8')
    print('Site written: ' + ', '.join(f'site/{name}' for name in sorted(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
