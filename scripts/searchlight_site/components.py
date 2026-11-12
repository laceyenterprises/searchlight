"""The building blocks pages are made of: ranked rows and boards, notes, measures, digests, filters and indexes."""
from __future__ import annotations

import json
import re

from .data import (ARM_LABEL, BEHAVIOR, BENCH_LABEL, GAP_PRIMARY, H2H, INSIGHT_SCHEMA, REPORT_TITLES, RERUN_NOTE,
                   WSB_PRIMARY, _gap_range, _point_pct, arm_id, board_caption, data_rows, esc, harness_name, leader,
                   parse_count, parse_pct_ci, report_title, tables, wilson)
from .design import g
from .layout import NARROW  # noqa: F401  (re-exported for pages)

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


def overlap(board: dict) -> tuple[int, int]:
    """(other setups whose 95% range reaches the leader's, other setups)."""
    rows = [r for r in board['rows'] if r['arm'] != 'no-search']
    if not rows:
        return 0, 0
    return sum(1 for r in rows[1:] if r['ci'][1] >= rows[0]['ci'][0]), len(rows) - 1


def tie_clause(tied: int, others: int) -> str:
    """The one way the site says how a leader's range sits against the rest."""
    if others and tied == others:
        return "every other setup's range overlaps the leader's, so the test cannot separate them"
    if tied:
        return f"{tied} of the {others} other setups' ranges overlap the leader's, so the test cannot separate those"
    return "no other setup's range reaches the leader's"


def tie_sentence(boards: list[dict]) -> str:
    """The tie clause across several boards, said once when it is the same for all of them."""
    clauses = [(harness_name(b)[0], tie_clause(*overlap(b))) for b in boards]
    if len({c for _, c in clauses}) == 1:
        text = clauses[0][1]
        return f'On each agent, {text}.' if len(clauses) > 1 else f'{text[:1].upper()}{text[1:]}.'
    return '; '.join(f'on {agent}, {clause}' for agent, clause in clauses).capitalize() + '.'


def board_says(board: dict) -> str:
    """The board's takeaway in one sentence, filled from its rows."""
    rows = [r for r in board['rows'] if r['arm'] != 'no-search']
    if not rows:
        return ''
    top = rows[0]
    nos = next((r for r in board['rows'] if r['arm'] == 'no-search'), None)
    if nos:
        overlap_nos = nos['ci'][1] >= top['ci'][0]
        return (f'Answering from memory passed {_pct(nos["pass_pct"])}; the best search setup, {top["label"]}, reached '
                f'{_pct(top["pass_pct"])}' + (', and their ranges overlap: search added little here.' if overlap_nos else '.'))
    clause = tie_clause(*overlap(board))
    return f'{top["label"]} passed most often: {top["passes"]} of {top["cells"]} runs. {clause[:1].upper()}{clause[1:]}.'


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
    order = sorted(h2h['providers'], key=lambda p: (p not in m['values'], -m['values'].get(p, 0), p))
    for provider in order:
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
                     f'<span class="when">{esc(page_meta(name, reports, studies))} · <span class="nw">{report_date(name)}'
                     '</span></span></a></li>')
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


def site_map(reports: dict, studies: list[dict]) -> list[tuple[str, str, str]]:
    """Every report and study page, newest first: (href, title, glyph). The menu and the indexes read it."""
    items = [(report_page_name(name), report_title(name, reports[name]['summary']), 'report') for name in reports]
    items += [(s['page'], s['summary']['set']['title'], 'study') for s in studies]
    return sorted(items, key=lambda item: (re.search(r'\d{4}-\d{2}-\d{2}', item[0]).group(), item[1]), reverse=True)
