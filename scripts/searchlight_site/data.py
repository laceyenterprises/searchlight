"""The data layer: published reports and studies, read and transcribed. No page markup lives here.

Every number the site shows comes through these functions from the committed files under ``reports/``; the only
derived numbers are Wilson intervals for tables that publish counts without them, and they are labelled as computed.
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / 'scripts', ROOT / 'lib' / 'python'):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from check_reports import REPORTS, discover  # noqa: E402
from sew import harnesses  # noqa: E402

REPO_URL = 'https://github.com/laceyenterprises/searchlight'
# Container publishers control unpinned third-party script execution. Limit
# publish access to trusted maintainers and review container/tag changes.
# Consent defaults govern storage, not every network request or cookieless ping.


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


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def _gap_range(board: dict) -> tuple[float, float]:
    pcts = [r['pass_pct'] for r in board['rows']]
    return min(pcts), max(pcts)


def _ceiling(board: dict) -> float:
    match = re.match(r'(\d+)', board['reference'].get('ceiling (answer excerpt)', '') or '')
    return float(match.group(1)) if match else 0.0


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
