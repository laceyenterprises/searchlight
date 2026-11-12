"""Build the Searchlight results site, leaderboard data and infographic from published reports.

Inputs are only files in this repository: every report and insight study under ``reports/``
(discovered by its summary schema, see ``scripts/check_reports.py``), the pinned provider arm
configuration in ``config/provider-arm-tools.yaml``, and the design system in ``design/``
(tokens, glyphs, vocabulary). Nothing is recalculated from private run data; the only derived
numbers are Wilson intervals for tables that publish counts without intervals, and they are
labelled as computed. Publishing a new report directory and rebuilding adds its page, its
boards and its index entries with no edits here.

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
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from check_reports import REPORTS, discover  # noqa: E402
from sew import harnesses  # noqa: E402

REPO_URL = 'https://github.com/laceyenterprises/searchlight'
# Container publishers control unpinned third-party script execution. Limit
# publish access to trusted maintainers and review container/tag changes.
# Consent defaults govern storage, not every network request or cookieless ping.
GTM_CONTAINER_ID = "GTM-TWQZV6F7"
GTM_HEAD = f"""<!-- Google consent defaults: analytics storage denied in the EEA, UK, Switzerland and listed territories; no consent UI; ad storage off everywhere -->
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}
gtag('consent','default',{{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'}});
gtag('consent','default',{{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH','AX','GF','GP','MQ','RE','YT','MF','GI','JE','GG','IM']}});</script>
<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{GTM_CONTAINER_ID}');</script>
<!-- End Google Tag Manager -->"""
# No non-JavaScript iframe: it cannot apply the storage defaults above.
GTM_BODY = ""
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
    '2026-10-06-search-gap-replication': 'Search gap bench replication',
    '2026-09-26-search-api-head-to-head': 'Search API head-to-head',
}
H2H = '2026-09-26-search-api-head-to-head'
GAP_PRIMARY = '2026-10-03-search-gap-bench'
WSB_PRIMARY = '2026-09-29-web-search-bakeoff'
BEHAVIOR = '2026-10-05-agent-search-behavior'
BENCH_LABEL = {'GAP': 'Search gap bench', 'WSB': 'Web search bakeoff', 'DSB': 'Domain search bench',
               'agent-search-behavior': 'Search behavior', 'search-api-head-to-head': 'Search API study',
               'insight': 'Insight study'}
H2H_PROVIDERS = ('Exa', 'Tavily', 'Parallel', 'Firecrawl')
RECALL_MIN, UNSUPPORTED_MAX = 0.7, 0.25  # the search gap bench's brief pass rule


# ---------------------------------------------------------------- data

def load_reports(root: Path = ROOT) -> dict:
    out = {}
    for name in discover(root):
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


def harness_models(summary: dict) -> dict[str, str]:
    """{harness: model} from a GAP setup table's "Harnesses" row ("codex on gpt-6.1-sol, ...")."""
    for _, table in tables(summary):
        for row in data_rows(table):
            if len(row) > 1 and row[0] in ('Harnesses', 'Harness'):
                return dict(re.findall(r'([\w.-]+) on ([\w./:-]+)', row[1]))
    return {}


def harness_name(board: dict) -> tuple[str, str]:
    """(display label, model) for a board; the model is empty when the report does not name it."""
    return harnesses.get(board['harness']).label, board.get('model', '')


def arm_id(cell: str) -> str:
    """A report's arm label as the site's arm id: 'Parallel' -> 'parallel-web', 'Built-in (native)' -> 'native'."""
    arm = cell.strip().lower()
    if arm.startswith('built-in') or arm == 'native':
        return 'native'
    if arm in ('no search', 'no-search'):
        return 'no-search'
    return arm.replace('parallel', 'parallel-web') if arm == 'parallel' else arm


def report_title(name: str, summary: dict | None = None) -> str:
    """A report's short title: the curated one, else its first heading without the trailing date."""
    if name in REPORT_TITLES:
        return REPORT_TITLES[name]
    for block in (summary or {}).get('blocks', []):
        heading = re.search(r'^# (.+)$', block.get('markdown', ''), re.M)
        if heading:
            title = re.split(r'(?:,| \(|:)\s*\d{4}-\d{2}-\d{2}', heading.group(1))[0]
            return title.strip(' :,') or name
    return name


def gap_boards(name: str, summary: dict, primary: bool = True) -> list[dict]:
    """One board per agent table in a GAP report. The primary report's boards keep their historical ids."""
    boards = []
    models = harness_models(summary)
    title = 'Search gap bench' if primary else report_title(name, summary)
    for intro, table in tables(summary):
        if table[0][:3] != ['Arm', 'Gap closure (95% CI)', 'Pass rate (Wilson 95% CI)']:
            continue
        heading = [line for line in intro.splitlines() if line.startswith('### ')][-1]
        harness = heading_harness(heading)
        cells = int(re.search(r'(\d+) (?:cells|runs) per arm', heading).group(1))
        rows = []
        for row in data_rows(table):
            if row[0].startswith(('ceiling', 'floor')):
                continue
            point, lo, hi = parse_pct_ci(row[2])
            arm = arm_id(row[0])
            rows.append({
                'arm': arm, 'label': ARM_LABEL.get(arm, row[0]), 'pass_pct': point, 'ci': [lo, hi],
                'passes': round(point * cells / 100), 'cells': cells, 'gap_closure': row[1],
                'tokens_per_success': row[3], 'vs_floor': row[4],
                'note': RERUN_NOTE if primary and harness == 'claude-code' and arm == 'native' else '',
                'flagged': False,
            })
        reference = {r[0].split(' ')[0]: r[2] for r in data_rows(table) if r[0].startswith(('ceiling', 'floor'))}
        label = harnesses.get(harness).label
        boards.append({
            'id': f'gap-{harness}' if primary else f'gap-{harness}-{name[:10]}', 'bench': 'GAP', 'harness': harness,
            'model': models.get(harness, ''),
            'title': f'{title}: {label}' + (f' ({models[harness]})' if harness in models else ''),
            'scope': f'{cells // 3} admitted post-cutoff brief tasks × 3 repetitions = {cells} cells per arm. '
                     f'Pass = right decision, weighted key-fact recall ≥ {RECALL_MIN}, unsupported claims ≤ {UNSUPPORTED_MAX}.',
            'interval': 'Wilson 95% (published)',
            'token_accounting': 'GAP tokens per success counts all agent tokens in the arm, failed cells included, '
                                'divided by passing cells.',
            'reference': {'floor (no search)': reference.get('floor', ''), 'ceiling (answer excerpt)': reference.get('ceiling', '')},
            'rows': sorted(rows, key=lambda r: (-r['pass_pct'], r['label'])),
            'source': name,
        })
    return boards


def _mark_leaders(boards: list[dict]) -> list[dict]:
    for board in boards:
        top = next(r for r in board['rows'] if r['arm'] != 'no-search')
        board['top_lower_bound'] = top['ci'][0]
        for row in board['rows']:
            row['overlaps_top'] = row['ci'][1] >= top['ci'][0]
    return boards


def more_boards(reports: dict) -> list[dict]:
    """Boards from every GAP report other than the primary one: replications and new runs, found automatically."""
    boards = []
    for name in sorted(reports):
        summary = reports[name]['summary']
        if name != GAP_PRIMARY and summary.get('bench') == 'GAP':
            try:
                boards.extend(gap_boards(name, summary, primary=False))
            except (SystemExit, ValueError, AttributeError, IndexError):
                continue  # a table this parser does not know still gets its report page
    return _mark_leaders(boards)


def leaderboards(reports: dict) -> list[dict]:
    boards = gap_boards(GAP_PRIMARY, reports[GAP_PRIMARY]['summary'])
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
        'title': 'Web search bakeoff: Claude Code (claude-opus-5-5)',
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
    return _mark_leaders(boards)


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
        measure(stage_a, 'S1 new or correcting claims', 's1', 'New or corrected facts, 40 open questions'),
        measure(stage_a, 'S4 new dated facts', 's4', 'New dated facts, two weeks of company news'),
        measure(stage_a, 'S3 pages recovered', 's3', 'Pages it read that built-in tools could not'),
        measure(stage_b, 'S6 right on KB ground truth', 's6', 'Correct values filling a fixed template'),
        measure(stage_b, 'S5 entities verified and new', 's5', 'New entries found for three lists'),
        {'id': 'sv', 'label': 'New key figures (prices, volumes, revenue) on four big vendors; no clear leader',
         'of': None, 'values': {p: parse_tally(c_rows[p][1])[0] for p in H2H_PROVIDERS}, 'source_row': stage_c[0][1]},
    ]
    rows = data_rows(monitors)
    probe = {'monitors': len(rows), 'runs': sum(int(r[1]) for r in rows),
             'runs_with_changes': sum(int(r[2]) for r in rows), 'changes': sum(int(r[3]) for r in rows)}
    if len(monitors[0]) > 4:
        probe['confirmed'] = sum(int(r[4]) for r in rows)
        probe['monitors_passing'] = sum(r[5].startswith('yes') for r in rows)
    return {'title': REPORT_TITLES[H2H], 'source': H2H, 'providers': list(H2H_PROVIDERS), 'measures': measures,
            'monitors': probe,
            'note': 'Counts of checked items from one run per question, copied from the report. No ranges were '
                    'published, so a difference of a few items may be chance.'}


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
            flush_para()
            flush_quote()
            close_lists()
            code = []
            continue
        if raw.startswith('>'):
            flush_para()
            close_lists()
            quote.append(raw.lstrip('> ').strip())
            continue
        flush_quote()
        heading = re.match(r'^(#{1,6}) (.+)$', raw)
        if heading:
            flush_para()
            close_lists()
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
    flush_para()
    flush_quote()
    close_lists()
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
         f'The best provider reached {best_wsb["pass_pct"]:.0f}%, within the margin of error, '
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


def _point_pct(cell: str) -> str:
    """'89% (67–97%)' -> '89%'."""
    match = re.match(r'\s*(\d+(?:\.\d+)?)%', cell)
    return f'{float(match.group(1)):.0f}%' if match else cell


def _gap_pct(cell: str) -> str:
    """Gap closure as a share of the gap: '1.06 (0.88–1.29)' -> '106% (88–129%)'. Can be negative."""
    number = r'-?\d+(?:\.\d+)?'
    match = re.fullmatch(rf'\s*({number})\s*\(\s*({number})\s*[-–]\s*({number})\s*\)\s*', cell)
    if not match:
        return cell
    point, lo, hi = (round(float(x) * 100) for x in match.groups())
    return f'{point}% ({lo}–{hi}%)'


def board_caption(board: dict) -> str:
    """The tasks behind a leaderboard and what counts as a pass, in plain words."""
    runs = board['rows'][0]['cells']
    if board['bench'] == 'GAP':
        return (f'{runs // 3} research briefs about events after the model\'s training data, each run 3 times: {runs} '
                f'runs per setup. A brief passes if it reaches the right decision, gets at least '
                f'{RECALL_MIN:.0%} of the key facts (important ones count more), and no more than '
                f'{UNSUPPORTED_MAX:.0%} of its claims lack support in the sources it cites.')
    return (f'{runs // 3} research tasks with mostly well-documented answers, each run 3 times: {runs} runs per setup. '
            'A run passes the task\'s automatic checks or, for open-ended tasks, a blind AI grader\'s checklist '
            '(regraded 2026-09-30 after grading fixes).')


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
        'agents': len({b['harness'] for b in boards}),
        'providers': len({r['arm'] for b in boards for r in b['rows']} - {'native', 'no-search'}),
        'cc': _gap_range(gap_cc), 'cx': _gap_range(gap_cx),
        'cc_top': gap_cc['rows'][0], 'cx_top': gap_cx['rows'][0],
        'wsb_none': nos, 'wsb_best': best,
        'token_ratio': float(tps[-1]['tokens_per_success'].rstrip('k')) / float(tps[0]['tokens_per_success'].rstrip('k')),
        'natural_pct': analysis['gap_totals']['natural_language_pct'],
        'min_runs': cells[0], 'max_runs': cells[-1],
    }


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


# ---------------------------------------------------------------- design system

DESIGN = ROOT / 'design'
SITE_ASSETS = ROOT / 'scripts' / 'site_assets'
sys.path.insert(0, str(DESIGN / 'glyphs'))
import searchlight_glyphs as glyphs  # noqa: E402

# The header mark (A, lamp) and the favicon (C, tile). design/glyphs/glyphs.html shows every option.
LOGO_ID = 'logo-lamp'
FAVICON_ID = 'logo-tile'
LOGO = glyphs.logo_svg(FAVICON_ID)
SITE_URL = 'https://searchlightai.dev'
LACEY_URL = 'https://www.laceyenterprises.com'


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


# The card is drawn on the night ground: the mark's ink is white and its lamp hole is the card's ground.
CARD_MARK = ('<svg class="mark" viewBox="0 0 24 24" aria-hidden="true" style="color:#FFFFFF;--paper:#0A0C10;'
             '--sl-accent:#3D9BFF">' + glyphs.LOGOS[[x[0] for x in glyphs.LOGOS].index(LOGO_ID)][2] + '</svg>')
TOKENS = _read(DESIGN / 'tokens.css')
CSS = TOKENS + _read(SITE_ASSETS / 'site.css')
JS = _read(SITE_ASSETS / 'site.js')
SPRITE = glyphs.sprite().replace('style="display:none"', 'aria-hidden="true" focusable="false" '
                                 'style="position:absolute;width:0;height:0;overflow:hidden"')


def g(glyph: str, cls: str = '') -> str:
    """A glyph from the sprite, decorative: the words beside it carry the meaning."""
    return f'<svg class="g{" " + cls if cls else ""}" aria-hidden="true" focusable="false"><use href="#{glyph}"/></svg>'


def mark(cls: str = 'mark') -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="#{LOGO_ID}"/></svg>'


def vocabulary() -> dict[str, list[list[str]]]:
    """The tables in design/vocabulary.md, by section, without their header rows."""
    out: dict[str, list[list[str]]] = {}
    section = None
    for line in _read(DESIGN / 'vocabulary.md').splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            out[section] = []
        elif section and line.startswith('|') and not set(line) <= set('|-: '):
            out[section].append([cell.strip() for cell in line.strip().strip('|').split('|')])
    return {name: rows[1:] for name, rows in out.items() if rows}


# ---------------------------------------------------------------- shared numbers

def report_date(name: str) -> str:
    return name[:10] if re.match(r'\d{4}-\d{2}-\d{2}', name) else ''


def bench_label(summary: dict) -> str:
    bench = summary.get('bench') or ('insight' if summary.get('schema') == INSIGHT_SCHEMA else '')
    return BENCH_LABEL.get(bench, bench or 'Report')


def report_page_name(name: str) -> str:
    return f'report-{name}.html'


def _pct(value: float) -> str:
    return f'{value:.0f}%'


def _floor_ceiling(board: dict) -> tuple[str, str]:
    return tuple(_point_pct(board['reference'].get(k, '') or '') for k in ('floor (no search)', 'ceiling (answer excerpt)'))


def board_says(board: dict) -> str:
    """The board's takeaway in one sentence, filled from its rows."""
    rows = [r for r in board['rows'] if r['arm'] != 'no-search']
    if not rows:
        return ''
    top = rows[0]
    others = [r for r in rows[1:] if r['ci'][1] >= top['ci'][0]]
    lead = f'{top["label"]} passed most often: {top["passes"]} of {top["cells"]} runs.'
    nos = next((r for r in board['rows'] if r['arm'] == 'no-search'), None)
    if nos:
        overlap = nos['ci'][1] >= top['ci'][0]
        return (f'Answering from memory passed {_pct(nos["pass_pct"])}; the best search setup, {top["label"]}, reached '
                f'{_pct(top["pass_pct"])}' + (', and their ranges overlap: search added little here.' if overlap else '.'))
    if len(others) == len(rows) - 1:
        return lead + ' Every other setup\'s range overlaps it: a tie.'
    if others:
        return lead + f' The ranges of {len(others)} of the other {len(rows) - 1} setups overlap it: a tie.'
    return lead + ' No other setup\'s range reaches it.'


# ---------------------------------------------------------------- rows and boards

ROW_NOTES = {
    RERUN_NOTE: ('Rerun', "In the first runs Claude Code's built-in search could not open pages. It was fixed and rerun "
                          'on 2026-10-06; this row is the rerun. The original row stays in the report.'),
    'reference arm': ('Control', 'No search at all: the agent answers from memory. Every other row is compared with it.'),
}


def note(label: str, text: str, kind: str = 'caveat') -> str:
    """A note beside the number it qualifies: a short label that opens to one or two sentences.

    Amber marks a real caveat (the number needs it to be read right); grey marks a neutral note."""
    glyph = 'caveat' if kind == 'caveat' else 'source'
    return (f'<details class="note {kind}"><summary>{g(glyph)}{esc(label)}</summary>'
            f'<div>{esc(text)}</div></details>')


def track(lo: float, hi: float, point: float, label: str, lower_bound: float | None = None,
          scale: float = 100.0) -> str:
    def at(v):
        return f'{100 * max(0.0, min(v, scale)) / scale:.1f}%'
    lb = f'<span class="lb" style="left:{at(lower_bound)}"></span>' if lower_bound is not None else ''
    return (f'<span class="track" role="img" aria-label="{esc(label)}">'
            f'<span class="ci" style="left:{at(lo)};width:{100 * max(0.6, hi - lo) / scale:.1f}%"></span>{lb}'
            f'<span class="dot" style="left:{at(point)}"></span></span>')


def row_html(board: dict, row: dict, rank: int, best: float) -> str:
    control = row['arm'] in ('no-search', 'native')
    lo, hi = row['ci']
    chip = ''
    if row['arm'] == 'no-search':
        chip = '<span class="chip ctl">control</span>'
    elif row['pass_pct'] == best:
        chip = '<span class="chip">leader</span>'
    flag = ''
    if row.get('note') in ROW_NOTES and row['arm'] != 'no-search':
        flag = note(*ROW_NOTES[row['note']])
    small = f'<small>{esc(row["tokens_per_success"])} tokens per pass</small>' if row.get('tokens_per_success') else ''
    label = (f'{row["label"]}: {row["pass_pct"]:.0f}% passed, {row["passes"]} of {row["cells"]} runs; '
             f'95% range {lo:.0f} to {hi:.0f}%')
    lead = ' data-lead' if row['pass_pct'] == best and row['arm'] != 'no-search' else ''
    return (f'<li class="row{" ctl" if control else ""}" id="r-{board["id"]}-{row["arm"]}" data-rank="{rank}"{lead}>'
            f'<span class="rk">{rank}</span><span class="nm"><span class="name">{esc(row["label"])}{chip}{flag}</span>'
            f'{small}</span><span class="pc">{row["pass_pct"]:.0f}%</span>'
            f'<span class="tr">{track(lo, hi, row["pass_pct"], label, board.get("top_lower_bound"))}</span>'
            f'<span class="nn"><b>{lo:.0f}–{hi:.0f}%</b><span class="k">{row["passes"]} of {row["cells"]}</span></span></li>')


SCALE = ('<div class="scale" aria-hidden="true"><span></span><span class="ticks"><span>0%</span><span>50%</span>'
         '<span>100%</span></span><span></span></div>')


LEGEND = ('<div class="legend" aria-hidden="true"><span><i class="k-pt"></i>Pass rate</span>'
          '<span><i class="k-ci"></i>95% range</span><span><i class="k-lb"></i>Where the leader\'s range starts</span></div>')


def board_notes(board: dict) -> str:
    """What a board's numbers mean: the pass rule, the scale and the source."""
    if board['bench'] == 'GAP':
        floor, ceiling = _floor_ceiling(board)
        foot = (f'For scale: with no search the agent passed {floor}; handed an excerpt containing the answer, it passed '
                f'{ceiling}. The full report gives each setup\'s gap closed: how far it got from {floor} toward {ceiling}. '
                'Tokens per pass: all tokens used, failed runs included, divided by passing runs.')
        ranges = 'as published'
    else:
        foot = ('Tokens per pass counts only runs whose tokens were measured; how many runs each setup was missing was '
                'not recorded, so treat it as approximate.')
        ranges = 'computed from its counts'
    return (f'<p>{esc(board_caption(board))}</p><p>{esc(foot)}</p>'
            f'<p>Source: <a href="{report_page_name(board["source"])}">{esc(REPORT_TITLES.get(board["source"], board["source"]))}'
            f'</a>; ranges are 95% Wilson intervals, {ranges}.</p>')


def numbers_mean(boards: list[dict]) -> str:
    """One "What the numbers mean" per finding or report, however many boards it shows."""
    seen, parts = set(), []
    for board in boards:
        key = (board['bench'], board['source'])
        if key not in seen:
            seen.add(key)
            parts.append(board_notes(board))
    return f'<details class="more-info"><summary>What the numbers mean</summary>{"".join(parts)}</details>'


def board_html(board: dict, top: int | None = None, notes: bool = True, brief: bool = False) -> str:
    """One benchmark as ranked rows: provider, a big number, its 95% range and n.

    With ``top``, the first rows show and the rest sit behind "All n setups". ``brief`` drops the board's own sentence
    where a finding's prose already says it. Details are one tap away."""
    agent, model = harness_name(board)
    runs = board['rows'][0]['cells']
    best = max(r['pass_pct'] for r in board['rows'] if r['arm'] != 'no-search')
    items = [row_html(board, row, i, best) for i, row in enumerate(board['rows'], start=1)]
    shown, rest = (items[:top], items[top:]) if top else (items, [])
    more = (f'<details class="allrows"><summary>All {len(items)} setups</summary>'
            f'<ol class="rows" start="{len(shown) + 1}">{"".join(rest)}</ol></details>') if rest else ''
    bench = BENCH_LABEL.get(board['bench'], board['bench'])
    if board['source'] not in (GAP_PRIMARY, WSB_PRIMARY):
        bench = REPORT_TITLES.get(board['source'], bench)
    providers = ' '.join(sorted({r['arm'] for r in board['rows']}))
    foot = f'<div class="bfoot">{numbers_mean([board])}</div>' if notes else ''
    return (f'<section class="board" id="board-{board["id"]}" data-f-group data-agent="{board["harness"]}" '
            f'data-bench="{board["source"]}" data-providers="{esc(providers)}">'
            f'<header><h3>{esc(agent)}{f" <span class=model>{esc(model)}</span>" if model else ""}</h3>'
            + (f'<p>{runs} runs per setup</p>' if brief else
               f'<p>{runs} runs per setup · {esc(bench)} · {report_date(board["source"])}</p>')
            + ('' if brief else f'<p class="says">{esc(board_says(board))}</p>') + f'</header>{SCALE}'
            f'<ol class="rows">{"".join(shown)}</ol>{more}{foot}</section>')


def dumbbell(gap: list[dict]) -> str:
    """Per agent: no search, the range across search setups, and the answer handed over, on one 0-100% track."""
    rows = []
    for board in gap:
        agent, model = harness_name(board)
        lo, hi = _gap_range(board)
        floor, ceiling = _floor_ceiling(board)
        f, c = (float(re.match(r'\d+(?:\.\d+)?', v).group()) if re.match(r'\d', v) else 0.0 for v in (floor, ceiling))
        label = f'{agent}: no search {floor}; with search {lo:.0f} to {hi:.0f}%; answer handed over {ceiling}'
        rows.append(
            f'<li class="row" data-f data-agent="{board["harness"]}" data-bench="{board["source"]}">'
            f'<span class="nm"><span class="name">{esc(agent)}</span><small>{esc(model)} · {board["rows"][0]["cells"]} runs '
            f'per setup</small></span><span class="pc">{lo:.0f}–{hi:.0f}%</span>'
            f'<span class="tr"><span class="track" role="img" aria-label="{esc(label)}">'
            f'<span class="span" style="left:{lo:.1f}%;width:{max(1.0, hi - lo):.1f}%"></span>'
            f'<span class="tick" style="left:{c:.1f}%"></span><span class="dot" style="left:{f:.1f}%"></span></span></span>'
            f'<span class="nn">no search {esc(floor)}<br>answer {esc(ceiling)}</span></li>')
    legend = ('<div class="legend" aria-hidden="true"><span><i class="k-dot"></i>No search</span>'
              '<span><i class="k-bar"></i>Every search setup, lowest to highest</span>'
              '<span><i class="k-tick"></i>Answer handed over</span></div>')
    scale = ('<div class="scale" aria-hidden="true"><span class="ticks"><span>0%</span><span>50%</span><span>100%</span>'
             '</span><span></span></div>')
    return f'<div class="dumb board" data-f-group>{scale}<ol class="rows">{"".join(rows)}</ol>{legend}</div>'


def _measure(h2h: dict, m: dict) -> str:
    scale = m['of'] or max(m['values'].values()) or 1
    top = max(m['values'].values())
    items = []
    for provider in h2h['providers']:
        if provider not in m['values']:
            items.append(f'<li data-f data-provider="{arm_id(provider)}"><span>{esc(provider)}</span>'
                         '<span class="bt"></span><span class="na">not run</span></li>')
            continue
        value = m['values'][provider]
        items.append(f'<li class="{"lead" if value == top else ""}" data-f data-provider="{arm_id(provider)}">'
                     f'<span>{esc(provider)}</span><span class="bt" role="img" aria-label="{esc(provider)}: {value}">'
                     f'<i style="width:{100 * value / scale:.1f}%"></i></span><span class="v">{value}</span></li>')
    of = f' <span class="of">of {m["of"]}</span>' if m['of'] else ''
    return f'<div class="measure" data-f-group><h3>{esc(m["label"])}{of}</h3><ul class="bars">{"".join(items)}</ul></div>'


def measures_html(h2h: dict, ids=('s1', 's4', 's3', 's6'), show: int = 2) -> str:
    """Counts without intervals: one small bar list per measure, the leader in blue, every other bar grey.

    The first ``show`` measures are visible; the rest open from "n more measures"."""
    measures = [m for m in h2h['measures'] if m['id'] in ids]
    head = ''.join(_measure(h2h, m) for m in measures[:show])
    rest = ''.join(_measure(h2h, m) for m in measures[show:])
    more = (f'<details class="allrows"><summary>{len(measures) - show} more measures</summary>'
            f'<div class="measures">{rest}</div></details>') if rest else ''
    return f'<div class="measures">{head}</div>{more}'


def behavior_facts(analysis: dict) -> dict:
    rows = {(r['harness'], r['arm']): r for r in analysis['gap_rows']}
    brows = {r['arm']: r for r in analysis['bakeoff_rows']}
    arms = ['native', 'brave', 'tavily', 'exa', 'parallel-web', 'firecrawl', 'perplexity']

    def pooled(key):
        return (sum(rows[('claude-code', a)][key][0] for a in arms if ('claude-code', a) in rows),
                sum(rows[('claude-code', a)][key][1] for a in arms if ('claude-code', a) in rows))
    hit, miss = pooled('pass_when_surfaced'), pooled('pass_when_not_surfaced')
    return {'natural': analysis['gap_totals']['natural_language_pct'], 'hit': hit, 'miss': miss,
            'hit_pct': 100 * hit[0] / hit[1] if hit[1] else 0, 'miss_pct': 100 * miss[0] / miss[1] if miss[1] else 0,
            'fetch': {a: brows[a] for a in ('exa', 'firecrawl', 'tavily', 'parallel-web') if a in brows}}


def behavior_tiles(analysis: dict) -> str:
    """Three numbers about how the agents searched. A tile that describes one agent says so, for the filters."""
    b = behavior_facts(analysis)
    exa = b['fetch'].get('exa')
    tiles = [(f'{b["natural"]:.0f}%', 'of searches about recent events read as plain questions; the rest were '
              'keyword strings.', '')]
    if exa:
        tiles.append((f'{exa["fetch_only_cells"]} of {exa["cells"]}',
                      'Claude Code runs with Exa that skipped search for pages it remembered.', 'claude-code'))
    tiles.append((f'{b["hit_pct"]:.0f}% vs {b["miss_pct"]:.0f}%',
                  'Claude Code passes with the original source in its results, and without.', 'claude-code'))
    return '<div class="stats">' + ''.join(
        f'<div class="stat" data-f{f" data-agent={chr(34)}{agent}{chr(34)}" if agent else ""}><div class="big">{esc(big)}'
        f'</div><p>{esc(text)}</p></div>' for big, text, agent in tiles) + '</div>'


# ---------------------------------------------------------------- report digests

def generic_rankings(summary: dict) -> list[tuple[str, list[dict]]]:
    """Ranked rows from any report table whose first column names setups and whose cells hold pass rates."""
    out = []
    for intro, table in tables(summary):
        if not table or table[0][0].strip().lower() not in ('arm', 'provider', 'setup', 'search setup'):
            continue
        body = data_rows(table)
        for col in range(1, len(table[0])):
            parsed = []
            for row in body:
                cell = row[col] if col < len(row) else ''
                try:
                    point, lo, hi = parse_pct_ci(cell)
                    k = n = None
                except ValueError:
                    try:
                        k, n = parse_count(cell)
                    except ValueError:
                        break
                    point = 100 * k / n if n else 0.0
                    lo, hi = wilson(k, n)
                parsed.append({'label': row[0], 'pct': point, 'ci': [lo, hi], 'k': k, 'n': n})
            else:
                if parsed:
                    heading = ([line[4:] if line.startswith('### ') else line[3:]
                                for line in intro.splitlines() if line.startswith(('## ', '### '))] or [''])[-1]
                    out.append((f'{heading}: {table[0][col]}'.strip(': '), sorted(parsed, key=lambda r: -r['pct'])))
                    break
    return out


def generic_board(title: str, rows: list[dict]) -> str:
    items = []
    for i, r in enumerate(rows, start=1):
        n = f'{r["k"]} of {r["n"]}' if r['n'] else ''
        items.append(f'<li class="row"><span class="rk">{i}</span><span class="nm">{esc(r["label"])}</span>'
                     f'<span class="pc">{r["pct"]:.0f}%</span><span class="tr">'
                     f'{track(r["ci"][0], r["ci"][1], r["pct"], r["label"] + ": " + _pct(r["pct"]))}</span>'
                     f'<span class="nn"><b>{r["ci"][0]:.0f}–{r["ci"][1]:.0f}%</b>{n}</span></li>')
    return f'<section class="board"><header><h3>{esc(title)}</h3></header>{SCALE}<ol class="rows">{"".join(items)}</ol></section>'


def _first_paragraph(summary: dict, heading: str = 'Headline') -> str:
    text = '\n'.join(b['markdown'] for b in summary.get('blocks', []) if 'markdown' in b)
    section = re.search(rf'^## {heading}\s*\n(.*?)(?=^#|\Z)', text, re.M | re.S)
    body = section.group(1) if section else re.sub(r'^#.*$', '', text, flags=re.M)
    for para in re.split(r'\n\s*\n', body):
        para = ' '.join(line.strip().lstrip('-> ').strip() for line in para.splitlines()).strip()
        if para and not para.startswith('|'):
            return re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', para).replace('**', '').replace('`', '')
    return ''


def digest(name: str, reports: dict, boards: list[dict], analysis: dict | None, h2h: dict | None) -> tuple[str, str]:
    """(takeaway sentence, glanceable HTML) for one report, from its own numbers."""
    summary = reports[name]['summary']
    mine = [b for b in boards if b['source'] == name]
    if mine and summary.get('bench') == 'GAP':
        lo = min(r['pass_pct'] for b in mine for r in b['rows'])
        hi = max(r['pass_pct'] for b in mine for r in b['rows'])
        floors = sorted({_floor_ceiling(b)[0] for b in mine})
        agents = ' and '.join(harness_name(b)[0] for b in mine)
        say = (f'With any search setup, {agents} passed {lo:.0f}–{hi:.0f}% of briefs about recent events; '
               f'with no search, {" to ".join(floors)}.')
        return say, (f'<div class="boards{" two" if len(mine) > 1 else ""}">'
                     f'{"".join(board_html(b, notes=False) for b in mine)}</div>')
    if mine:
        return board_says(mine[0]), f'<div class="boards">{"".join(board_html(b, notes=False) for b in mine)}</div>'
    if name == H2H and h2h and {'s1', 's3', 's6'} <= {m['id'] for m in h2h.get('measures', [])}:
        hm = {m['id']: m for m in h2h['measures']}
        s1, s3, s6 = hm['s1'], hm['s3'], hm['s6']
        say = (f'Called directly, {leader(s1)} added the most verified new facts ({max(s1["values"].values())} on 40 open '
               f'questions), {leader(s3)} read the most pages built-in tools could not ({max(s3["values"].values())} of '
               f'{s3["of"]}), and {leader(s6)} filled a fixed template most accurately.')
        return say, measures_html(h2h) + f'<p class="muted small" style="margin-top:12px">{esc(h2h["note"])}</p>'
    if name == BEHAVIOR and analysis and analysis.get('gap_rows'):
        b = behavior_facts(analysis)
        say = (f'Only {b["natural"]:.0f}% of searches about recent events read as plain questions, and Claude Code passed '
               f'{b["hit_pct"]:.0f}% of briefs when the original source showed up in its results, against '
               f'{b["miss_pct"]:.0f}% when it did not.')
        return say, behavior_tiles(analysis)
    rankings = generic_rankings(summary)
    if rankings:
        title, rows = rankings[0]
        say = f'{rows[0]["label"]} ranked first on {title.lower()}: {rows[0]["pct"]:.0f}%.'
        return say, ''.join(generic_board(t, r) for t, r in rankings[:2])
    return _first_paragraph(summary), ''


# ---------------------------------------------------------------- layout

NAV = [('index.html', 'Overview', 'beam'), ('results.html', 'Results', 'ranking'), ('studies.html', 'Studies', 'study'),
       ('learn.html', 'Learn', 'learn'), ('methodology.html', 'Methodology', 'method')]


NARROW = ('A narrow measurement: a fixed set of jobs on the agents and settings named, not a ranking of any provider '
          'or product as a whole.')


def site_map(reports: dict, studies: list[dict]) -> list[tuple[str, str, str]]:
    """Every report and study page, newest first: (href, title, glyph). The menu and the indexes read it."""
    items = [(report_page_name(name), report_title(name, reports[name]['summary']), 'report') for name in reports]
    items += [(s['page'], s['summary']['set']['title'], 'study') for s in studies]
    return sorted(items, key=lambda item: (re.search(r'\d{4}-\d{2}-\d{2}', item[0]).group(), item[1]), reverse=True)


def _nav(active: str, pages: list[tuple[str, str, str]] = ()) -> str:
    def current(href):
        return ' aria-current="page"' if href == active else ''
    # A report or study page sits under Results or Studies; the bar marks that section.
    section = 'results.html' if active.startswith('report-') else 'studies.html' if active.startswith('study-') else active

    def in_section(href):
        return current(href) or (' aria-current="true"' if href == section else '')
    bar = ''.join(f'<a href="{href}"{in_section(href)}>{label}</a>' for href, label, _ in NAV)
    panel = ''.join(f'<a href="{href}"{current(href)}>{g(glyph)}{label}</a>' for href, label, glyph in NAV)
    if pages:
        panel += '<p class="sub">Reports and studies</p>' + ''.join(
            f'<a class="small" href="{href}"{current(href)}>{g(glyph)}{esc(title)}</a>' for href, title, glyph in pages)
    panel += f'<a class="small" href="{REPO_URL}">{g("open-source")}Source on GitHub</a>'
    return (f'<header class="top"><div class="wrap"><a class="brand" href="index.html" aria-label="Searchlight home">'
            f'{mark()}<span class="word">Searchlight</span></a>'
            f'<nav class="navbar" aria-label="Site">{bar}<a class="gh" href="{REPO_URL}">GitHub</a></nav>'
            f'<details class="menu"><summary aria-label="Menu">{g("menu-open", "open")}{g("menu-close", "close")}</summary>'
            f'<nav class="navpanel" aria-label="Site menu">{panel}</nav></details></div></header>')


def _footer() -> str:
    links = [(href, label) for href, label, _ in NAV] + [
        (REPO_URL, 'GitHub'), (f'{REPO_URL}/tree/main/reports', 'Raw data'),
        (f'{REPO_URL}/blob/main/LICENSE', 'Apache 2.0'), (f'{REPO_URL}/issues/new', 'Report a problem')]
    return (f'<footer class="foot"><div class="wrap"><div class="foot-top"><a class="brand" href="index.html">{mark()}'
            '<span class="word">Searchlight</span></a><nav class="foot-links" aria-label="Footer">'
            + ''.join(f'<a href="{href}">{label}</a>' for href, label in links) + '</nav></div>'
            f'<div class="foot-base"><p>A <a href="{LACEY_URL}">Lacey Enterprises</a> project · © 2026 Lacey Enterprises LLC</p>'
            f'<p class="analytics-disclosure">{ANALYTICS_NOTICE}</p></div></div></footer>')


def _document(title: str, description: str, active: str, main: str, script: str = '',
              social_title: str = '', card_alt: str = '', pages: list = (), head: str = '') -> str:
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" '
             'href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" '
             'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&'
             'family=JetBrains+Mono:wght@500;600&display=swap">')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'\n{GTM_HEAD}\n'
            f'<meta name="description" content="{esc(description)}">'
            f'{_social_meta(social_title or title, description, active, card_alt or CARD_ALT_DEFAULT)}'
            '<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">'
            '<meta name="theme-color" content="#0C0F13" media="(prefers-color-scheme: dark)">'
            '<link rel="icon" type="image/svg+xml" href="logo.svg">'
            f'\n<title>{esc(title)}</title>\n{fonts}\n<style>{CSS}{head}</style>\n</head><body>\n'
            '<script>document.documentElement.classList.add("js")</script>\n'
            f'{GTM_BODY}\n{SPRITE}<a class="skip" href="#main">Skip to content</a>\n'
            f'{_nav(active, pages)}\n<main id="main">\n{main}\n</main>\n{_footer()}\n'
            f'<script>{JS}</script>\n{script}</body></html>\n')


def read_panel() -> str:
    """How to read these results, in full. It lives on the Learn page; every other page links to it."""
    return (f'<aside class="readme" id="read" aria-label="How to read these results">{g("method", "s32")}<div>'
            '<h3>How to read these results</h3>'
            f'<p>{NARROW} Different jobs, agents or settings can give a different order.</p>'
            '<ul><li>Every pass rate comes with its 95% range. When two ranges overlap, the data cannot separate them: '
            'the difference may be chance.</li>'
            '<li>Each benchmark is a few dozen runs per setup on one or two agents, at default settings.</li>'
            '<li>Studies ran each question once, so they report counts; a difference of a few items may be chance.</li>'
            '<li>A note beside a number says what qualifies it. Tap it to read.</li></ul>'
            '<p class="links"><a class="more" href="methodology.html">Read the method</a></p></div></aside>')


def read_link() -> str:
    return '<p class="readlink"><a class="more" href="learn.html#read">How to read these numbers</a></p>'


def page_meta(name: str, reports: dict, studies: list[dict]) -> str:
    """A short fact for an index line: how big a study or report is."""
    study = next((s for s in studies if s['name'] == name), None)
    if study:
        return f'{len(study["summary"]["arms"])} services · {study["summary"]["set"]["questions"]} questions'
    return bench_label(reports[name]['summary'])


def reports_list(reports: dict, studies: list[dict], takeaways: dict[str, str], anchors: bool = False,
                 compact: bool = False) -> str:
    """Every report and study, newest first, each one tap from here."""
    items = []
    for href, title, glyph in site_map(reports, studies):
        name = href.removeprefix('report-').removeprefix('study-').removesuffix('.html')
        ids = f' id="run-{name}"' if anchors and name in reports else ''
        alias = f'<span id="method-{name}"></span>' if anchors and name in reports else ''
        items.append(f'<li{ids}>{alias}<a class="row-link" href="{href}">{g(glyph)}<span class="t">{esc(title)}</span>'
                     + (f'<span class="d">{esc(takeaways[name])}</span>' if takeaways.get(name) else '') +
                     f'<span class="when">{esc(page_meta(name, reports, studies))} · {report_date(name)}</span></a></li>')
    return f'<ul class="reports{" compact" if compact else ""}">{"".join(items)}</ul>'


def latest_strip(reports: dict, studies: list[dict]) -> str:
    """The newest report or study, as one line near the top of the overview. Static: rebuilt when one is published."""
    pages = site_map(reports, studies)
    if not pages:
        return ''
    href, title, glyph = pages[0]
    name = href.removeprefix('report-').removeprefix('study-').removesuffix('.html')
    return (f'<a class="latest" href="{href}"><span class="tag">Latest</span><span class="when">{report_date(name)}</span>'
            f'{g(glyph)}<span class="t">{esc(title)}</span><span class="m">{esc(page_meta(name, reports, studies))}</span>'
            '</a>')


def takeaways_for(reports: dict, studies: list[dict], boards: list[dict], analysis, h2h) -> dict[str, str]:
    from sew.insight.dashboard import takeaway
    out = {name: digest(name, reports, boards, analysis, h2h)[0] for name in reports}
    out.update({s['name']: takeaway(s['summary']) for s in studies})
    return out


# ---------------------------------------------------------------- overview

BEAM_ART = """<svg class="beamart" viewBox="0 0 420 300" role="img" aria-label="A searchlight beam sweeps a pile of pages and lands on the one that holds the answer, marked verified.">
<defs><linearGradient id="beamfade" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#3D9BFF" stop-opacity=".95"/><stop offset="1" stop-color="#3D9BFF" stop-opacity=".12"/></linearGradient></defs>
<g fill="none" stroke="#5C6573" stroke-width="3"><path d="M230 20h56l14 14v70h-70z"/><path d="M330 150h56l14 14v70h-70z"/><path d="M196 196h50l12 12v62h-62z"/><path d="M354 40h40l10 10v50h-50z"/></g>
<path d="M62 250L236 40 398 196z" fill="url(#beamfade)"/>
<g fill="#0C0F13" stroke="#F2F4F7" stroke-width="3.5"><path d="M262 82h64l16 16v84h-80z"/></g><path d="M326 82v16h16" fill="none" stroke="#F2F4F7" stroke-width="3.5"/>
<path d="M276 120h50M276 136h50M276 152h30" stroke="#7DBBFF" stroke-width="6"/>
<circle cx="330" cy="178" r="22" fill="#2BD99A"/><path d="M319 178l8 8 15-16" fill="none" stroke="#06111F" stroke-width="5"/>
<circle cx="62" cy="250" r="36" fill="#F2F4F7"/><circle cx="62" cy="250" r="13" fill="#0C0F13"/>
</svg>"""


def landing(root: Path, boards: list[dict], analysis: dict, reports: dict | None = None, studies: list | None = None,
            h2h: dict | None = None, pages: list = ()) -> str:
    reports = reports if reports is not None else load_reports(root)
    studies = studies or []
    h = headline(boards, analysis)
    (cc_lo, cc_hi), (cx_lo, cx_hi) = h['cc'], h['cx']
    lo, hi = min(cc_lo, cx_lo), max(cc_hi, cx_hi)
    gap = [b for b in boards if b['bench'] == 'GAP']
    floor = max(float(re.match(r'(\d+)', b['reference'].get('floor (no search)', '') or '0').group(1)) for b in gap)
    nos, best = h['wsb_none'], h['wsb_best']
    others = [r for b in gap for r in b['rows'][1:] if r['arm'] != 'no-search']
    tied = sum(1 for b in gap for r in b['rows'][1:] if r['arm'] != 'no-search' and r['ci'][1] >= b['top_lower_bound'])
    hero = (f'<section class="wrap"><div class="hero"><div><p class="eyebrow">{g("beam")}An open benchmark by Lacey '
            'Enterprises</p><h1>Does web search make AI agents better at real work?</h1>'
            '<p class="what">Searchlight gives AI agents real jobs, changes only the search provider, and grades the '
            'results blind.</p>'
            f'<p class="answer">When the job is about something recent, search decides it: with no search, both agents '
            f'passed <b>{floor:.0f}%</b> of those briefs; with any provider, <b>{lo:.0f}–{hi:.0f}%</b>.</p>'
            f'<a class="btn" href="results.html">See the results</a></div>{BEAM_ART}</div>'
            f'{latest_strip(reports, studies)}</section>')
    rows = [
        (f'{floor:.0f}% → {lo:.0f}–{hi:.0f}%', 'Search decides anything recent',
         f'With no search, both agents failed every brief about events after their training data. All '
         f'{h["providers"]} providers closed most of the gap.', 'finding-1'),
        (f'{tied} of {len(others)}', 'No provider wins everywhere',
         f'{h["cc_top"]["label"]} led on Claude Code and {h["cx_top"]["label"]} on Codex, but {tied} of the '
         f'{len(others)} other setups\' ranges overlap their leader\'s: ties.', 'finding-2'),
        (f'{nos["pass_pct"]:.0f}% vs {best["pass_pct"]:.0f}%', 'On well-known topics, memory does most of the work',
         f'Answering from memory passed {nos["pass_pct"]:.0f}%; the best provider reached {best["pass_pct"]:.0f}%, '
         f'within the margin of error, using up to {h["token_ratio"]:.0f}× the tokens.', 'finding-3'),
        (f'{h["natural_pct"]:.0f}%', 'Agents still search with keywords',
         f'Only {h["natural_pct"]:.0f}% of searches read as plain questions. Runs did better when the original source '
         'showed up.', 'finding-4'),
    ]
    if h2h and h2h.get('measures'):
        hm = {m['id']: m for m in h2h['measures']}
        if 's1' in hm and 's3' in hm:
            s1, s3 = hm['s1'], hm['s3']
            rows.append((f'{max(s1["values"].values())}', f'Called directly, {leader(s1)} added the most new facts',
                         f'{max(s1["values"].values())} verified new facts on 40 open questions; {leader(s3)} read the '
                         f'most pages other tools could not ({max(s3["values"].values())} of {s3["of"]}).', 'finding-5'))
    found = ''.join(f'<li><a href="results.html#{anchor}"><span class="k">{esc(k)}</span><span class="t">{esc(t)}</span>'
                    f'<span class="d">{esc(d)}</span></a></li>' for k, t, d, anchor in rows)
    concepts = vocabulary()['Concepts'][:8]
    tiles = ''.join(f'<a class="tile" href="#c-{slug(term)}" data-concept="c-{slug(term)}" role="tab" '
                    f'aria-controls="c-{slug(term)}" aria-selected="{"true" if i == 0 else "false"}">'
                    f'{g(glyph)}<b>{esc(term)}</b><span>{esc(one)}</span></a>'
                    for i, (term, glyph, one, _) in enumerate(concepts))
    panels = ''.join(f'<div class="concept" id="c-{slug(term)}" role="tabpanel"><h3>{g(glyph, "s32")}{esc(term)}</h3>'
                     f'<p class="one">{esc(one)}</p><p>{esc(more)}</p></div>'
                     for term, glyph, one, more in concepts)
    examples = []
    for catalog, task_id, kind, title, why, graded in EXAMPLES:
        prompt, source = _catalog_prompt(root, catalog, task_id)
        if not prompt:
            continue
        glyph = {'Research brief': 'brief', 'Code fix': 'code-fix'}.get(kind, 'source')
        src = f' <a href="{esc(source)}">The source</a>.' if source.startswith('https://') else ''
        examples.append(f'<article class="card example"><p class="eyebrow">{g(glyph)}{esc(kind)}</p><h3>{esc(title)}</h3>'
                        f'<p>{esc(why)}{src}</p><details class="more-info"><summary>Read the task and how it is graded'
                        f'</summary><blockquote>{esc(prompt)}</blockquote><p>{esc(graded)}</p></details></article>')
    main = f"""{hero}

<section class="band" id="findings"><div class="wrap">
<div class="head"><h2>What we found</h2><p>From {h['runs']} graded runs of Claude Code and Codex.</p></div>
<ul class="takeaways">{found}</ul>
{read_link()}
</div></section>

<section class="band soft" id="how"><div class="wrap">
<div class="head"><h2>Learn Searchlight in 60 seconds</h2><p>Eight ideas behind every number on this site. Pick one to
read more.</p></div>
<div class="tiles" role="tablist" aria-label="Ideas">{tiles}</div>
<div class="concepts" data-tabs>{panels}</div>
<p style="margin-top:20px"><a class="more" href="learn.html">See how a test works, step by step</a></p>
</div></section>

<section class="band" id="examples"><div class="wrap">
<div class="head"><h2>What we asked the agents to do</h2><p>Real tasks from the benchmark, quoted from its task catalogs.</p></div>
<div class="cards">{''.join(examples)}</div>
</div></section>

<section class="band soft" id="report"><div class="wrap">
<div class="head"><h2>Every report and study</h2><p>Each one published with its data, method and corrections.</p></div>
{reports_list(reports, studies, {})}
</div></section>"""
    card = card_facts(boards, analysis)
    return _document('Searchlight', card['description'], 'index.html', main,
                     social_title=card['title'], card_alt=card['alt'], pages=pages)


# ---------------------------------------------------------------- results

def filter_bar(boards: list[dict], extra_providers: list[str] = ()) -> str:
    """Client-side filters over the rendered rows: one row of chips per question. Hidden without JavaScript: the page
    already shows everything."""
    agents = sorted({(b['harness'], harness_name(b)[0]) for b in boards}, key=lambda a: a[1])
    benches = sorted({b['source'] for b in boards} | {H2H}, reverse=True)
    providers = sorted({(r['arm'], r['label']) for b in boards for r in b['rows']} |
                       {(arm_id(p), ARM_LABEL.get(arm_id(p), p)) for p in extra_providers}, key=lambda p: p[1])

    def group(name, label, options):
        chips = ''.join(f'<label><input type="radio" name="{name}" value="{esc(v)}"{" checked" if not v else ""}>'
                        f'<span>{esc(t)}</span></label>' for v, t in options)
        return f'<fieldset class="seg"><legend>{label}</legend><div class="opts">{chips}</div></fieldset>'
    return ('<form class="filters js-only" id="filters" aria-label="Filter the results" data-filters><div class="wrap">'
            + group('agent', 'Agent', [('', 'All')] + agents)
            + group('bench', 'Benchmark', [('', 'All')] + [(b, f'{REPORT_TITLES.get(b, b)} · {report_date(b)[5:]}')
                                                          for b in benches])
            + group('provider', 'Setup', [('', 'All')] + providers)
            + '</div></form>')


def filter_data(boards: list[dict]) -> str:
    """The rows the filters act on, as embedded JSON: id, agent, benchmark, provider, rank and label."""
    rows = [{'id': f'r-{b["id"]}-{r["arm"]}', 'agent': b['harness'], 'bench': b['source'], 'provider': r['arm'],
             'rank': i, 'label': r['label']} for b in boards for i, r in enumerate(b['rows'], start=1)]
    data = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    return f'<script type="application/json" id="filter-data">{data}</script>'


def prose(variants: dict[str, str]) -> str:
    """A finding's lede, one variant per agent filter ('' is the unfiltered text). The filters show the one that
    matches, so the words never contradict the rows."""
    return ''.join(f'<p data-prose="{esc(agent)}"{" hidden" if agent else ""}>{esc(text)}</p>'
                   for agent, text in variants.items())


def finding(n: int, title: str, lede: str | dict, body: str, so: str, bench: str = '', note_html: str = '',
            agent: str = '', notes: str = '') -> str:
    text = prose(lede) if isinstance(lede, dict) else f'<p>{esc(lede)}</p>'
    attrs = (f' data-bench="{bench}"' if bench else '') + (f' data-agent="{agent}"' if agent else '')
    return (f'<section class="finding" id="finding-{n}" data-f-group{attrs}>'
            f'<div class="head"><p class="eyebrow">Finding {n}</p><h2>{esc(title)}</h2>{text}'
            f'{f"<div class=notes>{note_html}</div>" if note_html else ""}</div>'
            f'{body}{notes}<p class="so">{g("insight")}<span><strong>So what:</strong> {esc(so)}</span></p></section>')


def results_page(reports: dict, boards: list[dict], analysis: dict, body_only: bool = False,
                 studies: list | None = None, pages: list = ()) -> str:
    h = headline(boards, analysis)
    h2h = head_to_head(reports)
    extra = more_boards(reports)
    gap = [b for b in boards if b['bench'] == 'GAP']
    wsb = next(b for b in boards if b['id'] == 'wsb-competitive')
    nos, best = h['wsb_none'], h['wsb_best']
    tps = sorted(wsb['rows'], key=lambda r: float(r['tokens_per_success'].rstrip('k')))
    b = behavior_facts(analysis) if analysis.get('gap_rows') else None

    def agent_name(board):
        return harness_name(board)[0]
    one = {}
    for board in gap:
        lo, hi = _gap_range(board)
        floor, ceiling = _floor_ceiling(board)
        one[board['harness']] = (lo, hi, floor, ceiling, board)
    all_lo, all_hi = min(v[0] for v in one.values()), max(v[1] for v in one.values())
    f1 = {'': f'With no search, both agents failed every brief about recent events; with any search setup they passed '
              f'{all_lo:.0f}–{all_hi:.0f}%.'}
    f2 = {'': ' and '.join(f'{v[4]["rows"][0]["label"]} led on {agent_name(v[4])}' for v in one.values())
              + ', and most ranges overlap: the test cannot separate the leaders.'}
    for harness, (lo, hi, floor, ceiling, board) in one.items():
        name = agent_name(board)
        f1[harness] = (f'With no search, {name} failed every brief about recent events ({floor}); with any search setup '
                       f'it passed {lo:.0f}–{hi:.0f}%, close to the {ceiling} it reached when handed the answer.')
        f2[harness] = f'On {name}: {board_says(board)}'
    findings = [
        finding(1, 'Past the training cutoff, search decides the outcome', f1, dumbbell(gap),
                'For recent events, search is the difference between failing and passing.', GAP_PRIMARY),
        finding(2, 'No provider wins everywhere', f2,
                LEGEND + f'<div class="boards two">{"".join(board_html(x, top=3, notes=False, brief=True) for x in gap)}</div>',
                'Choose on cost and fit, then test on your own agent.', GAP_PRIMARY,
                notes=numbers_mean(gap)),
        finding(3, 'On well-documented questions, memory does most of the work',
                {'': f'Answering from memory passed {nos["pass_pct"]:.0f}%; the best provider reached '
                     f'{best["pass_pct"]:.0f}%, within the margin of error, at up to {h["token_ratio"]:.0f}× the tokens '
                     f'({tps[0]["tokens_per_success"]} to {tps[-1]["tokens_per_success"]} per pass).'},
                LEGEND + f'<div class="boards">{board_html(wsb, top=3, notes=False, brief=True)}</div>',
                'On stable questions, search mostly adds cost.',
                WSB_PRIMARY, agent=wsb['harness'], notes=numbers_mean([wsb])),
    ]
    if b:
        queries = analysis.get('gap_totals', {}).get('queries', 0) + analysis.get('bakeoff_totals', {}).get('queries', 0)
        findings.append(finding(
            4, 'Agents still search like keyword users',
            {'': 'Agents rarely searched in plain questions, often skipped search for pages they remembered, and did '
                 'better when the original source showed up.',
             'codex': 'Across both agents, few searches read as plain questions. The other two measures were taken on '
                      'Claude Code only.'},
            behavior_tiles(analysis),
            'Better questions and original sources first are the next gains.',
            BEHAVIOR,
            note(f'From {queries} logged searches', 'Computed from the stored transcripts of the two benchmarks. Links '
                 'between search behaviour and pass rates are patterns, not causes.', 'neutral') if queries else ''))
    if h2h.get('measures'):
        hm = {m['id']: m for m in h2h['measures']}
        if all(k in hm for k in ('s1', 's4', 's3', 's6')):
            s1, s3, s6 = hm['s1'], hm['s3'], hm['s6']
            findings.append(finding(
                5, 'Called directly, each search API led a different job',
                {'': f'With no agent in the loop, {leader(s1)} added the most verified new facts '
                     f'({max(s1["values"].values())}), {leader(s3)} read the most hard pages and {leader(s6)} filled a '
                     'template best.'},
                measures_html(h2h, ids=('s1', 's3', 's4', 's6'), show=1),
                'Choose an API by the job you need done.', H2H,
                note('Counts, no ranges', h2h['note'] + ' Tavily sells no data-extraction product, so it was not run on '
                     'the template.') + note('Context', 'The study was run while building a sales and marketing '
                                             'knowledge base about Exa, one of the four, and its questions came from '
                                             'that work.', 'neutral'),
                agent='-'))
    # Every other benchmark run (replications, newer runs) is found automatically and listed in the record with its
    # takeaway; its full board is one tap away on its report page.
    later = {x['source'] for x in extra}
    takeaways = {name: digest(name, reports, extra, analysis, h2h)[0] for name in later}
    providers = h2h.get('providers', [])
    main = f"""<div class="wrap pagehead"><p class="eyebrow">{g("ranking")}Results</p>
<h1>Search decides recent work. Which provider matters less.</h1>
<p class="lede">{h['runs']} graded runs of {h['agents']} AI coding agents with {h['providers']} search providers, plus one
study that called four search APIs directly.</p></div>
{filter_bar(boards, providers)}
<div class="wrap">{read_link()}
<p class="empty js-only" id="filter-empty" hidden>Nothing matches these filters. Widen one of them.</p>
{''.join(findings)}
<section class="finding" id="leaderboard"><div class="head"><p class="eyebrow">{g("report")}The record</p>
<h2>Every report and study</h2><p>Each has its own page. Data: <a href="leaderboard.json">leaderboard.json</a> ·
<a href="infographic.svg">one-page infographic</a></p></div>
{reports_list(reports, studies or [], takeaways, anchors=True, compact=True)}
</section>
</div>
{filter_data(boards)}"""
    main = results_language(main)
    if body_only:
        return main
    return _document('Searchlight Results', 'Searchlight results: what web search does for AI coding agents, with every '
                     'pass rate, its range and the full reports.', 'results.html', main, pages=pages)


def localize_links(markup: str, name: str, names: list[str]) -> str:
    """Cross-report links point at each report's own page; links within this report stay on the page."""
    def fix(match):
        target = match.group(1)
        for other in names:
            if target in (f'#run-{other}', f'#method-{other}'):
                return f'href="{report_page_name(other)}{"#method-" + other if "method" in target else ""}"'
            if other != name and (target.startswith(f'#{other}-') or target.startswith(f'#{other}-method-')):
                return f'href="{report_page_name(other)}{target}"'
        if target == f'#run-{name}':
            return 'href="#top"'
        return match.group(0)
    return re.sub(r'href="(#[^"]+)"', fix, markup)


def report_page(name: str, reports: dict, boards: list[dict], analysis: dict | None, h2h: dict | None,
                pages: list = ()) -> str:
    summary = reports[name]['summary']
    title = report_title(name, summary)
    say, glance = digest(name, reports, boards, analysis, h2h)
    mine = [b for b in boards if b['source'] == name]
    full = render_report(summary, name)
    method = markdown(reports[name]['methodology'], name + '-method', heading_shift=2, base=name)
    top = (f'<div class="wrap pagehead" id="top"><nav class="crumbs" aria-label="Breadcrumb"><a href="results.html">Results'
           f'</a><span aria-hidden="true">›</span><span>{esc(title)}</span></nav>'
           f'<p class="eyebrow" style="margin-top:20px">{g("report")}{esc(bench_label(summary))} · {report_date(name)}</p>'
           f'<h1>{esc(title)}</h1><p class="lede">{esc(say)}</p></div>'
           f'<div class="wrap">{read_link()}{LEGEND if mine else ""}{glance}{numbers_mean(mine) if mine else ""}</div>')
    detail = (f'<div class="wrap doc" style="margin-top:32px">'
              f'<details class="full" id="run-{name}"><summary>{g("report")}The full report</summary>'
              f'<div class="body">{full}</div></details>'
              f'<details class="full" id="method-{name}"><summary>{g("method")}How this was run</summary>'
              f'<div class="body">{method}</div></details>'
              f'<p class="files">Files: <a href="{REPO_URL}/tree/main/reports/{name}">reports/{name}/</a></p></div>')
    if name != H2H:  # the head-to-head report keeps its own words: a table cell, a research arm, its scripts
        top, detail = results_language(top), results_language(detail)
    else:
        top = results_language(top)
    main = localize_links(top + detail, name, list(reports))
    return _document(f'{title} · Searchlight', say[:300] or f'{title}: a Searchlight report.', report_page_name(name),
                     main, pages=pages)


# ---------------------------------------------------------------- studies

def study_question(summary: dict) -> str:
    """A study's reader-facing question, from its set: the title says what the questions were about."""
    title = summary.get('set', {}).get('title', '')
    return f'Which search services found the most useful facts about {title[:1].lower() + title[1:]}?' if title else \
        'Which search services found the most useful facts?'


def finish_html(summary: dict) -> str:
    from sew.insight.dashboard import finish

    return '<ol class="finish">' + ''.join(
        f'<li><span class="q">{esc(x["q"])}</span><span class="who">{esc(x["who"])}</span>'
        f'<span class="big">{esc(x["big"])}<small>{esc(x["unit"])}</small></span><span class="ctx">{esc(x["ctx"])}</span></li>'
        for x in finish(summary)) + '</ol>'


def studies_page(studies: list[dict], pages: list = ()) -> str:
    from sew.insight.dashboard import ranked

    cards = []
    for s in studies:
        summary = s['summary']
        rows = ranked(summary)
        scale = max([r['hi'] for r in rows] + [1.0])
        items = []
        for i, r in enumerate(rows, start=1):
            kind = 'research' if r['kind'] == 'research' else 'search'
            label = f'{r["arm"]}: {r["value"]:.1f} useful findings per question, 95% range {r["lo"]:.1f} to {r["hi"]:.1f}'
            items.append(f'<li class="row" data-f data-kind="{kind}"><span class="rk">{i}</span>'
                         f'<span class="nm"><span class="name">{esc(r["arm"])}<span class="chip ctl">{kind}</span></span>'
                         f'<small>{r["unique"]} found by no one else</small></span>'
                         f'<span class="pc">{r["value"]:.1f}</span><span class="tr">'
                         f'{track(r["lo"], r["hi"], r["value"], label, scale=scale)}</span>'
                         f'<span class="nn">{r["questions"]} questions</span></li>')
        top, rest = items[:3], items[3:]
        more = (f'<details class="allrows"><summary>All {len(items)} services</summary>'
                f'<ol class="rows" start="4">{"".join(rest)}</ol></details>') if rest else ''
        sponsor = summary['set'].get('sponsor')
        meta = (f'{len(summary["arms"])} services · {summary["set"]["questions"]} questions · '
                f'${summary["spend"]["total_usd"]:.2f} spent' + (f' · sponsored by {sponsor.get("name", "")}' if sponsor else ''))
        cards.append(
            f'<article class="study" id="study-{s["name"]}" data-f-group>'
            f'<p class="eyebrow">{g("study")}Study · {report_date(s["name"])} · {esc(meta)}</p>'
            f'<h2><a href="{s["page"]}">{esc(study_question(summary))}</a></h2>'
            f'{finish_html(summary)}'
            f'<div class="board"><header><h3>Ranked by useful findings per question</h3>'
            '<p>Search services hand back results; research services write an answer.</p></header>'
            '<div class="scale" aria-hidden="true"><span></span><span class="ticks"><span>0</span>'
            f'<span>{scale / 2:.0f}</span><span>{scale:.0f}</span></span><span></span></div>'
            f'<ol class="rows">{"".join(top)}</ol>{more}</div>'
            f'<p class="actions"><a class="btn" href="{s["page"]}">Open the study</a>'
            f'<a class="more" href="{REPO_URL}/tree/main/reports/{esc(s["name"])}">Report, data and method</a></p></article>')
    filters = ('<form class="filters js-only" aria-label="Filter the studies" data-filters><div class="wrap">'
               '<fieldset class="seg"><legend>Services</legend><div class="opts">'
               '<label><input type="radio" name="kind" value="" checked><span>All</span></label>'
               '<label><input type="radio" name="kind" value="search"><span>Search</span></label>'
               '<label><input type="radio" name="kind" value="research"><span>Research</span></label>'
               '</div></fieldset></div></form>')
    main = (f'<div class="wrap pagehead"><p class="eyebrow">{g("study")}Studies</p>'
            '<h1>Which search service finds the most that holds up?</h1>'
            '<p class="lede">Each study sends one fixed set of questions to every search and research service and checks '
            'every claim on the page it cites.</p></div>'
            + (filters if studies else '')
            + f'<div class="wrap">{read_link()}' + (''.join(cards) or '<p class="empty">No studies yet.</p>') + '</div>')
    return _document('Insight studies · Searchlight',
                     'Which search APIs find the most unique, verified insight, study by study.',
                     'studies.html', main, pages=pages)


STUDY_SHELL = re.compile(r'/\* shell: .*?/\* end shell \*/\n', re.S)


def study_page(study: dict, pages: list = ()) -> str:
    """The study's own dashboard, hosted in the site's shell: the same header, type and theme handling, and the page
    heading, consent and tag markup, raw links to the bundle and footer of every other page."""
    template = study['dashboard']
    summary = study.get('summary') or {}
    title = summary.get('set', {}).get('title', study['name'])
    raw = f"{REPO_URL}/blob/main/reports/{study['name']}/"
    style = re.search(r'<style>(.*?)</style>', template, re.S).group(1)
    style = re.sub(r'/\* tokens: design/tokens\.css.*?/\* end tokens \*/\n', '', style, flags=re.S)
    style = STUDY_SHELL.sub('', style)
    body = re.search(r'<main id="main" class="dash"[^>]*>(.*?)</main>', template, re.S).group(1)
    body = re.sub(r'<!--shell-->.*?<!--/shell-->', '', body, flags=re.S)
    scripts = template[template.index('<script type="application/json" id="data">'):template.rindex('</body>')]
    meta = (f'{len(summary["arms"])} services · {summary["set"]["questions"]} questions · '
            f'${summary["spend"]["total_usd"]:.2f} spent') if summary.get('arms') else ''
    head = (f'<div class="wrap pagehead"><nav class="crumbs" aria-label="Breadcrumb"><a href="studies.html">Studies</a>'
            f'<span aria-hidden="true">›</span><span>{report_date(study["name"])}</span></nav>'
            f'<p class="eyebrow" style="margin-top:20px">{g("study")}Study · {report_date(study["name"])}'
            f'{" · " + esc(meta) if meta else ""}</p><h1>{esc(study_question(summary))}</h1>'
            f'<p class="readlink"><a class="more" href="{REPO_URL}/tree/main/reports/{esc(study["name"])}">Data and method'
            '</a></p></div>')
    main = (head + f'<div class="dash" data-raw-base="{esc(raw)}">{body}</div>'
            f'<div class="wrap"><p class="readlink"><a class="more" href="studies.html">All Searchlight studies</a></p></div>')
    return _document(f'{title} · Searchlight study', f'{study_question(summary)} One study, every claim checked.',
                     study['page'], main, script=scripts, pages=pages, head=style)


# ---------------------------------------------------------------- learn and method

def walkthrough(h: dict) -> list[tuple[str, list[str], str, str]]:
    """How a test works: (title, glyphs, body, notice), from the same numbers as the results."""
    return [
        ('Give the agent a real job', ['cutoff', 'brief', 'code-fix'],
         "A research brief or a code fix about something that changed after the model's training cutoff, or a "
         'question with a well-documented answer.',
         'Past the cutoff, memory alone cannot pass. That is what makes search measurable.'),
        ('Change only the search', ['agent', 'setup', 'no-search', 'built-in'],
         'The same agent, model and instructions every time. Each run plugs in one search provider at its default '
         'settings.',
         "Two controls frame every provider: no search at all, and the agent's own built-in search."),
        ('Run it sealed, three times', ['sealed', 'repeat'],
         f'Each run starts in a fresh, isolated workspace, and every task runs three times: {h["min_runs"]}–'
         f'{h["max_runs"]} runs per setup. Every search and page visit is logged.',
         'A few dozen runs is a small sample. That is why every score carries a range.'),
        ('Grade it blind', ['blind', 'judge', 'source'],
         'Automatic checks run where a task has them. Otherwise an AI judge compares the answer with the saved source '
         'pages, never told which setup produced it.',
         'A brief passes with the right decision, 70% of the key facts and few unsupported claims.'),
        ('Rank it honestly', ['ranking', 'range', 'tie'],
         'Setups are ranked by how often their runs passed. Every pass rate shows its 95% range.',
         'When ranges overlap, the results call it a tie instead of naming a winner.'),
    ]


def steps_html(h: dict, paged: bool = False, compact: bool = False) -> str:
    items = []
    for i, (title, icons, body, notice) in enumerate(walkthrough(h), start=1):
        pic = '' if compact else ('<div class="pic">' + ''.join(g(x) for x in icons) + '</div>')
        tail = '' if compact else f'<p class="notice"><strong>Notice:</strong> {esc(notice)}</p>'
        items.append(f'<li class="step" id="step-{i}"><p class="n">Step {i} of {len(walkthrough(h))}</p>{pic}'
                     f'<h3>{esc(title)}</h3><p>{esc(body)}</p>{tail}</li>')
    cls = 'steps' + (' paged' if paged else '') + (' strip' if compact else '')
    stepper = ('<div class="stepper" data-stepper-controls><span class="pos sr" aria-live="polite"></span>'
               '<button class="btn quiet" type="button" data-step="-1">Back</button>'
               '<button class="btn" type="button" data-step="1">Next</button></div>') if paged else ''
    return f'<ol class="{cls}"{" data-stepper" if paged else ""}>{"".join(items)}</ol>{stepper}'


def learn_page(boards: list[dict], analysis: dict, pages: list = ()) -> str:
    h = headline(boards, analysis)
    best = h['wsb_best']
    k, n = best['passes'], best['cells']
    lo, hi = wilson(k, n)
    terms = ''.join(f'<div class="term" id="t-{slug(term)}">{g(glyph)}<b>{esc(term)}</b><p>{esc(one)}</p></div>'
                    for term, glyph, one, _ in vocabulary()['Concepts'])
    explorer = (f'<div class="explorer" data-explorer data-k="{k}" data-n="{n}">'
                f'<div class="ctl"><span>Passes</span><button type="button" data-d="k,-1" aria-label="One fewer pass">−'
                f'</button><span class="val" data-v="k">{k}</span><button type="button" data-d="k,1" '
                'aria-label="One more pass">+</button><span>of runs</span><button type="button" data-d="n,-1" '
                f'aria-label="One fewer run">−</button><span class="val" data-v="n">{n}</span><button type="button" '
                'data-d="n,1" aria-label="One more run">+</button></div>'
                f'<div class="big blue" data-v="pct">{100 * k / n:.0f}%</div>'
                f'{track(lo, hi, 100 * k / n, f"95% range {lo:.0f} to {hi:.0f}%")}'
                '<div class="axis" aria-hidden="true"><span>0%</span><span>25%</span><span>50%</span><span>75%</span>'
                '<span>100%</span></div>'
                f'<p aria-live="polite"><strong>95% range: <span data-v="range">{lo:.0f}% to {hi:.0f}%</span>.</strong> '
                '<span data-v="say">Run the same test again and the score could land anywhere in that range.</span></p>'
                '</div>')
    main = f"""<div class="wrap pagehead"><p class="eyebrow">{g("learn")}Learn</p>
<h1>How a Searchlight test works.</h1>
<p class="lede">Five steps, the same for every search setup.</p></div>

<section class="band" style="padding-top:24px"><div class="wrap">{steps_html(h, paged=True)}</div></section>

<section class="band soft" id="ranges"><div class="wrap">
<div class="head"><p class="eyebrow">{g("range")}Try it: honest ranges</p><h2>How sure is {100 * k / n:.0f}%?</h2>
<p>A pass rate from a few dozen runs is a measurement, so it comes with a range. Change the passes and the runs, and watch
the range move. It starts at {best['label']}'s score in the web search bakeoff.</p></div>
{explorer}
<details class="more-info"><summary>How the range is worked out</summary>
<p>Start with the score p = passes ÷ runs, and z = 1.96 for 95%. Wilson's interval sets the centre at
(p + z²/2n) ÷ (1 + z²/n) and the half-width at z × √(p(1 − p)/n + z²/4n²) ÷ (1 + z²/n). It never runs past 0% or 100%, and it
stays honest at the edges. To halve a range, run about four times as many tests.</p></details>
</div></section>

<section class="band" id="reading"><div class="wrap">{read_panel()}</div></section>

<section class="band soft" id="words"><div class="wrap">
<div class="head"><p class="eyebrow">{g("brief")}The words</p><h2>Every idea, in one line</h2>
<p>The reports keep some technical words; the <a href="methodology.html#terms">methodology</a> maps them to these.</p></div>
<div class="terms">{terms}</div>
<p style="margin-top:28px"><a class="more" href="results.html">Now read the results</a></p></div></section>"""
    return _document('Learn · Searchlight', 'How a Searchlight test works, in five steps, and the ideas behind every number.',
                     'learn.html', main, pages=pages)


DIAGRAM_STEPS = {
    'pipeline': ['Tasks come from two catalogs: production tasks and post-cutoff briefs.',
                 'Each run gets a fresh scratch workspace and one agent: Claude Code or Codex.',
                 'The agent calls one search provider through a metering proxy.',
                 'The transcript, answer, setup audit and metrics form an evidence bundle.',
                 'Automatic checks and blind judges grade it; the report and this site are built from the grades.'],
    'isolation': ['Each search setup has a contract: the tools the agent may use.',
                  'Claude Code starts with only the setup\'s connector and built-ins; Codex starts read-only with its '
                  'shell, browser, apps and plugins off.',
                  'A provider setup gets one search connector; built-in search gets the agent\'s own; no search gets '
                  'no tools.',
                  'After the run, an audit checks every tool call against the contract.'],
    'calibration': ['A candidate brief is about an event after 2026-01-01.',
                    'It runs five times with no search (floor) and five times with the answer handed over (ceiling).',
                    'It counts only if the floor passes at most once and the ceiling at least four times.',
                    'Admitted tasks then run three times on every search setup.'],
    'grading': ['The answer must be valid JSON: a brief, its claims and citations.',
                'The cited pages are captured and their text read.',
                'A Claude Code judge, blind to the setup, scores key facts, unsupported claims and the decision.',
                'A Codex judge scores the same inputs; their agreement is recorded.',
                'Pass: the right decision, at least 70% of key facts, at most 25% unsupported claims.'],
    'direct': ['Pre-registered questions from one knowledge base go to Exa, Tavily, Parallel and Firecrawl.',
               'Their results are pooled, deduplicated and shuffled.',
               'Blind judges verify each claim on its page and check whether the knowledge base already had it.',
               'A provider is credited only where its own page states the claim.'],
}


def diagram(name: str) -> str:
    """A flow as a site-styled step list on phones and as a diagram on wider screens."""
    steps = ''.join(f'<li>{esc(step)}</li>' for step in DIAGRAM_STEPS[name])
    return f'<ol class="steplist">{steps}</ol>{mermaid(name)}'


def provider_cards(configs: list[dict]) -> str:
    """One card per search provider: how it connects, its pinned version, and its tools behind one tap."""
    cards = []
    for cfg in configs:
        tools = ''.join(f'<code>{esc(t)}</code>' for t in cfg['tools'])
        version = esc(cfg['package']) + (f' → {esc(cfg["remote"])}' if cfg['remote'] else '')
        cards.append(f'<div class="pcard"><h3>{esc(cfg["label"])}</h3>'
                     f'<p>{"Hosted connector via mcp-remote" if cfg["remote"] else "Local connector (npx)"}</p>'
                     f'<p class="ver"><code>{version}</code></p>'
                     f'<details class="more-info"><summary>{len(cfg["tools"])} tools</summary><p class="tools">{tools}</p>'
                     '</details></div>')
    cards.append('<div class="pcard"><h3>Built-in (Claude Code)</h3><p>The agent\'s own search</p>'
                 '<p class="ver"><code>Claude Code 2.1.282</code></p><details class="more-info"><summary>3 tools</summary>'
                 '<p class="tools"><code>WebSearch</code><code>WebFetch</code><code>Read</code></p>'
                 '<p>WebFetch was refused before 2026-10-06.</p></details></div>')
    cards.append('<div class="pcard"><h3>Built-in (Codex)</h3><p>The agent\'s own search</p>'
                 '<p class="ver"><code>codex --search</code></p><details class="more-info"><summary>2 tools</summary>'
                 '<p class="tools"><code>web search</code><code>page views</code></p></details></div>')
    return f'<div class="pcards">{"".join(cards)}</div>'


def methodology_page(configs: list[dict], target: str = 'pages', body_only: bool = False,
                     boards: list[dict] | None = None, analysis: dict | None = None, pages: list = ()) -> str:
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
    strip = ''
    if boards is not None and analysis is not None:
        strip = (f'<section style="margin-top:28px">{steps_html(headline(boards, analysis), compact=True)}'
                 '<p style="margin-top:16px"><a class="more" href="learn.html">Walk through it, with pictures</a></p>'
                 '</section>')
    terms = ''.join(f'<div class="gl">{g(glyph)}<div><b>{esc(plain[:1].upper() + plain[1:])}</b> '
                    f'<span class="alias">{esc(term)}</span><p>{esc(meaning)}</p></div></div>'
                    for term, plain, glyph, meaning in vocabulary()['Report terms'])
    main = f"""<div class="wrap pagehead"><p class="eyebrow">{g("method")}Methodology</p><h1>How Searchlight tests search</h1>
<p class="lede">The same agent, model and instructions in every run, with only the search setup changing. The steps
first; the full apparatus after.</p>{strip}</div>
<div class="wrap doc">

<section id="terms"><h2>Terms used in the reports</h2>
<p>The reports keep their technical words. Each one, with the plain word this site uses:</p>
<div class="glossary">{terms}</div></section>

<section id="pipeline"><h2>From task to report</h2>
<p>A fresh workspace, exactly the tools the setup allows, every tool call recorded, and a grader that never learns the
setup.</p>
{diagram('pipeline')}</section>

<section id="isolation"><h2>What each setup can touch</h2>
<p>Setups differ only in what the agent can use to search. Claude Code starts with <code>--strict-mcp-config</code>, so the
only MCP server it can reach is the provider's, and <code>--tools</code> limits its built-ins. Codex runs read-only. An audit
compares every tool call with the setup's contract; a call outside it marks the run contaminated.</p>
{diagram('isolation')}
<details class="more-info"><summary>The exact commands</summary>
<h3>Claude Code</h3><pre>{esc(claude_argv)}</pre>
<h3>Codex</h3><pre>{esc(codex_argv)}</pre></details>
<h3>Limits per test run</h3>
{table_html([['Limit', 'Search gap bench', 'Web search bakeoff'],
             ['Wall clock', '1,200 s', '300 to 1,200 s'],
             ['Total tokens', '1,000,000', '120,000 to 350,000'],
             ['Search provider calls', '60', '15 to 50'],
             ['Agent start-up timeout', '120 s', '120 s'],
             ['Repetitions', '3 (5 to calibrate)', '3']], 'apparatus')}
<p class="small muted">Bakeoff limits are set per task.</p>
</section>

<section id="calibration"><h2>Which tasks count</h2>
<p>A search gap task counts only if it is beyond the model: with no search the agent must fail, and with the answer
excerpt handed over it must succeed. Calibration is repeated for each agent and model.</p>
{diagram('calibration')}
<p><b>Gap closed</b> = (setup pass rate − no-search pass rate) ÷ (answer-handed-over pass rate − no-search pass rate).
100% means the setup did as well as being handed the answer.</p></section>

<section id="grading"><h2>How answers are graded</h2>{diagram('grading')}
<p>Code fixes pass when hidden tests pass in an isolated workspace. Bakeoff tasks use automatic checks where a task has one,
and otherwise a single blinded grader scores the task rubric.</p></section>

<section id="providers"><h2>Search provider configurations</h2>
<p>Pinned in <a href="{REPO_URL}/blob/main/config/provider-arm-tools.yaml">config/provider-arm-tools.yaml</a>. Every tool a
provider's server advertised was available to the agent.</p>
{provider_cards(configs)}</section>

<section id="direct-api"><h2>The search API head-to-head</h2>
<p>One study tests the search APIs themselves rather than an agent using them: the same pre-registered questions, each API
set up as its documentation recommends, the same day and number of results. Each question ran once, so it reports counts
without ranges.</p>
{diagram('direct')}
<p>Its sets, endpoints, amendments and costs are in the study's
<a href="{report_page_name(H2H)}#method-{H2H}">methodology</a>, and its full pre-registered record is in
<a href="{REPO_URL}/tree/main/reports/{H2H}">reports/{H2H}/</a>.</p></section>

<section id="studies-method"><h2>Insight studies</h2>
<p>An insight study sends one fixed set of questions to every search and research service, each run as its vendor documents.
A model extracts each answer's claims, another checks every claim on the page it cites, and a blind judge values what each
verified finding is worth to the question's reader. A service earns a finding only if its own cited page supports it.</p>
</section>

<section id="reproduce"><h2>Reproduce, correct, contribute</h2>
<p>Install Searchlight, run <code>sew doctor</code>, then follow each study's <code>reproduce.md</code>. Raw transcripts and
provider responses are not distributed (except the head-to-head's Monitors probe), so a new run measures the same method
rather than replaying the numbers. If a configuration here misrepresents a service you run, open an issue: corrections are
rerun and published beside the original.</p>
<p>New reports appear here by themselves: publish a report directory under <code>reports/</code> (or run
<code>sew insight publish</code>, which also rebuilds the site), and its page, rows and index entries are generated.</p>
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
                  '(function(){var s=getComputedStyle(document.documentElement),v=function(n){return s.getPropertyValue(n)'
                  '.trim()};mermaid.initialize({startOnLoad:true,securityLevel:"strict",theme:"base",'
                  'flowchart:{useMaxWidth:false},sequence:{useMaxWidth:false},themeVariables:{'
                  'fontFamily:"Inter, -apple-system, sans-serif",fontSize:"15px",background:v("--paper"),'
                  'primaryColor:v("--soft"),primaryTextColor:v("--ink"),primaryBorderColor:v("--rule"),'
                  'secondaryColor:v("--blue-soft"),tertiaryColor:v("--paper"),lineColor:v("--muted"),'
                  'clusterBkg:v("--paper"),clusterBorder:v("--rule"),edgeLabelBackground:v("--paper"),'
                  'actorBkg:v("--soft"),actorBorder:v("--rule"),actorTextColor:v("--ink"),signalColor:v("--ink-2"),'
                  'signalTextColor:v("--ink"),noteBkgColor:v("--blue-soft"),noteTextColor:v("--ink"),'
                  'noteBorderColor:v("--rule")}})})();</script>\n')
    return _document('Searchlight Methodology', 'How Searchlight tests web search for AI coding agents.',
                     'methodology.html', main, script, pages=pages)


def page(reports: dict, boards: list[dict], configs: list[dict], target: str) -> tuple[str, str]:
    """Single-file preview: the results and methodology bodies, without scripts or the document shell."""
    analysis = behavior(reports)
    head = f'<title>Searchlight Results</title>\n<style>{CSS}</style>'
    body = results_page(reports, boards, analysis, body_only=True) + methodology_page(configs, target, body_only=True)
    return head, body


INSIGHT_SCHEMA = 'sew-insight-summary'
ANALYTICS_NOTICE = ('Google Tag Manager loads on every page; analytics cookies are disabled for visitors in the EEA, '
                    'the UK, Switzerland and listed territories. No consent opt-in is offered.')


def insight_studies(root: Path = ROOT) -> list[dict]:
    """Every published insight-bench study: a reports/<name>/ whose summary.json is an insight summary."""
    studies = []
    for name in discover(root, 'insight'):
        path = Path(root) / 'reports' / name / 'summary.json'
        summary = json.loads(path.read_text(encoding='utf-8'))
        dashboard = path.parent / 'dashboard.html'
        if dashboard.is_file():
            studies.append({'name': name, 'summary': summary, 'page': f'study-{name}.html',
                            'dashboard': dashboard.read_text(encoding='utf-8')})
    return sorted(studies, key=lambda s: s['name'], reverse=True)


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
        'index.html': landing(root, boards, analysis, reports, studies, h2h, pages),
        'results.html': results_page(reports, boards, analysis, studies=studies, pages=pages),
        'learn.html': learn_page(boards, analysis, pages),
        'methodology.html': methodology_page(configs, boards=boards, analysis=analysis, pages=pages),
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
    # Report and study pages come and go with their report directories.
    orphans = sorted(p.name for pattern in ('report-*.html', 'study-*.html') for p in site.glob(pattern)
                     if p.name not in files)
    if args.check:
        stale = [name for name, content in files.items()
                 if not (site / name).is_file() or (site / name).read_text(encoding='utf-8') != content] + orphans
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
    for name in orphans:
        (site / name).unlink()
    for name, content in files.items():
        (site / name).write_text(content, encoding='utf-8')
    print('Site written: ' + ', '.join(f'site/{name}' for name in sorted(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
