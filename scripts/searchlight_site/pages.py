"""The pages: overview, results, a page per report, studies and each hosted study, learn and methodology."""
from __future__ import annotations

import re
from pathlib import Path

from . import diagrams
from .components import (LEGEND, tie_sentence, behavior_facts, behavior_tiles, bench_label, board_html, board_says, digest, dumbbell,
                         filter_bar, filter_data, finding, latest_strip, localize_links, measures_html, note,
                         numbers_mean, report_date, report_page_name, reports_list, track, _floor_ceiling)
from .data import (BEHAVIOR, EXAMPLES, GAP_PRIMARY, H2H, REPO_URL, WSB_PRIMARY, _catalog_prompt, _gap_range, esc,
                   harness_name, headline, leader, markdown, more_boards, render_report, report_title, results_language,
                   slug, table_html, wilson)
from .design import g, vocabulary
from .layout import _document, read_link, read_panel

BEAM_ART = """<svg class="beamart" viewBox="0 0 420 300" role="img" aria-label="A searchlight beam sweeps a pile of pages and lands on the one that holds the answer, marked verified.">
<defs><linearGradient id="beamfade" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#3D9BFF" stop-opacity=".95"/><stop offset="1" stop-color="#3D9BFF" stop-opacity=".12"/></linearGradient></defs>
<g fill="none" stroke="#5C6573" stroke-width="3"><path d="M230 20h56l14 14v70h-70z"/><path d="M330 150h56l14 14v70h-70z"/><path d="M196 196h50l12 12v62h-62z"/><path d="M354 40h40l10 10v50h-50z"/></g>
<path d="M62 250L236 40 398 196z" fill="url(#beamfade)"/>
<g fill="#0C0F13" stroke="#F2F4F7" stroke-width="3.5"><path d="M262 82h64l16 16v84h-80z"/></g><path d="M326 82v16h16" fill="none" stroke="#F2F4F7" stroke-width="3.5"/>
<path d="M276 120h50M276 136h50M276 152h30" stroke="#7DBBFF" stroke-width="6"/>
<circle cx="330" cy="178" r="22" fill="#2BD99A"/><path d="M319 178l8 8 15-16" fill="none" stroke="#06111F" stroke-width="5"/>
<circle cx="62" cy="250" r="36" fill="#F2F4F7"/><circle cx="62" cy="250" r="13" fill="#0C0F13"/>
</svg>"""


def landing(root: Path, boards: list[dict], analysis: dict, reports: dict, studies: list | None, h2h: dict | None,
            card: dict, pages: list = ()) -> str:
    studies = studies or []
    h = headline(boards, analysis)
    (cc_lo, cc_hi), (cx_lo, cx_hi) = h['cc'], h['cx']
    lo, hi = min(cc_lo, cx_lo), max(cc_hi, cx_hi)
    gap = [b for b in boards if b['bench'] == 'GAP']
    floor = max(float(re.match(r'(\d+)', b['reference'].get('floor (no search)', '') or '0').group(1)) for b in gap)
    nos, best = h['wsb_none'], h['wsb_best']
    hero = (f'<section class="wrap"><div class="hero"><div><p class="eyebrow">{g("beam")}An open benchmark</p>'
            '<h1>Does web search make AI agents better at real work?</h1>'
            '<p class="answer">Searchlight gives AI agents real jobs and changes only the search. When the job is about '
            f'something recent, search decides it: <b>{floor:.0f}%</b> with no search, <b>{lo:.0f}–{hi:.0f}%</b> with any '
            'provider.</p>'
            f'<a class="btn" href="results.html">See the results</a></div>{BEAM_ART}</div>'
            f'{latest_strip(reports, studies)}</section>')
    rows = [
        (f'{floor:.0f}% → {lo:.0f}–{hi:.0f}%', 'Search decides anything recent',
         f'With no search, both agents failed every brief about events after their training data. All '
         f'{h["providers"]} providers closed most of the gap.', 'finding-1'),
        (f'{h["cc_top"]["label"]} · {h["cx_top"]["label"]}', 'A different leader on each agent',
         f'{h["cc_top"]["label"]} led on Claude Code and {h["cx_top"]["label"]} on Codex. {tie_sentence(gap)}', 'finding-2'),
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
    tiles = ''.join(f'<a class="tile" href="learn.html#t-{slug(term)}">{g(glyph)}<b>{esc(term)}</b><span>{esc(one)}</span></a>'
                    for term, glyph, one, _ in concepts)
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
<div class="head"><h2>Learn Searchlight in 60 seconds</h2><p>Eight ideas behind every number on this site.</p></div>
<div class="tiles">{tiles}</div>
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
    return _document('Searchlight', card['description'], 'index.html', main,
                     social_title=card['title'], card_alt=card['alt'], pages=pages)


def results_page(reports: dict, boards: list[dict], analysis: dict, h2h: dict, body_only: bool = False,
                 studies: list | None = None, pages: list = ()) -> str:
    h = headline(boards, analysis)
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
              + '. ' + tie_sentence(gap)}
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
                'Choose on cost and fit, then test on your own agent.', GAP_PRIMARY),
        finding(3, 'On well-documented questions, memory does most of the work',
                {'': f'Answering from memory passed {nos["pass_pct"]:.0f}%; the best provider reached '
                     f'{best["pass_pct"]:.0f}%, within the margin of error, at up to {h["token_ratio"]:.0f}× the tokens '
                     f'({tps[0]["tokens_per_success"]} to {tps[-1]["tokens_per_success"]} per pass).'},
                LEGEND + f'<div class="boards">{board_html(wsb, top=3, notes=False, brief=True)}</div>',
                'On stable questions, search mostly adds cost.',
                WSB_PRIMARY, agent=wsb['harness']),
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


STUDY_DECK = 'Which search services found the most useful facts, checked on the pages they cite.'


def finish_html(summary: dict) -> str:
    from sew.insight.dashboard import finish, finish_item

    return '<ol class="finish">' + ''.join(finish_item(x) for x in finish(summary)) + '</ol>'


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
            icon = g('results-list' if kind == 'search' else 'answer', 'kd')
            items.append(f'<li class="row" data-f data-kind="{kind}"><span class="rk">{i}</span>'
                         f'<span class="nm"><span class="name nowrap">{icon}<span class="sr">{kind}: </span>{esc(r["arm"])}'
                         '</span>'
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
            f'<h2><a href="{s["page"]}">{esc(summary["set"]["title"])}</a></h2>'
            f'{finish_html(summary)}'
            f'<div class="board"><header><h3>Ranked by useful findings per question</h3>'
            f'<p class="keys">{g("results-list", "kd")} search hands back results {g("answer", "kd")} research writes an '
            'answer</p></header>'
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
            f'{" · " + esc(meta) if meta else ""}</p><h1>{esc(title)}</h1><p class="lede">{STUDY_DECK}</p>'
            f'<p class="readlink"><a class="more" href="{REPO_URL}/tree/main/reports/{esc(study["name"])}">Data and method'
            '</a></p></div>')
    main = (head + f'<div class="dash" data-raw-base="{esc(raw)}">{body}</div>'
            f'<div class="wrap"><p class="readlink"><a class="more" href="studies.html">All Searchlight studies</a></p></div>')
    return _document(f'{title} · Searchlight study', f'{title}. {STUDY_DECK}',
                     study['page'], main, script=scripts, pages=pages, head=style)


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
    vocab = vocabulary()
    terms = ''.join(f'<div class="term" id="t-{slug(term)}">{g(glyph)}<b>{esc(term)}</b><p>{esc(one)}</p></div>'
                    for term, glyph, one, _ in vocab['Concepts'])
    report_terms = ''.join(f'<div class="gl">{g(glyph)}<div><b>{esc(plain[:1].upper() + plain[1:])}</b> '
                           f'<span class="alias">{esc(term)}</span><p>{esc(meaning)}</p></div></div>'
                           for term, plain, glyph, meaning in vocab['Report terms'])
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
<p>The one glossary on this site. The reports keep some technical words; they are mapped below.</p></div>
<div class="terms">{terms}</div>
<details class="more-info" id="report-terms"><summary>Words in the reports</summary><div class="glossary">{report_terms}</div>
</details>
<p style="margin-top:28px"><a class="more" href="results.html">Now read the results</a></p></div></section>"""
    return _document('Learn · Searchlight', 'How a Searchlight test works, in five steps, and the ideas behind every number.',
                     'learn.html', main, pages=pages)


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


ARRIVES = ('Every published run appears here automatically when it is merged: publishing writes the report and '
           'rebuilds the site in the same commit, and the merge deploys it.')


def methodology_page(configs: list[dict], target: str = 'pages', body_only: bool = False,
                     boards: list[dict] | None = None, analysis: dict | None = None, pages: list = ()) -> str:
    """The method: the five steps, then one drawn diagram per part of the apparatus. Detail is one tap away."""
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
        strip = f'<section style="margin-top:28px">{steps_html(headline(boards, analysis), compact=True)}</section>'

    def more(summary: str, body: str, ident: str = '') -> str:
        return (f'<details class="more-info"{f" id={chr(34)}{ident}{chr(34)}" if ident else ""}><summary>{summary}'
                f'</summary>{body}</details>')
    limits = table_html([['Limit', 'Search gap bench', 'Web search bakeoff'],
                         ['Wall clock', '1,200 s', '300 to 1,200 s'],
                         ['Total tokens', '1,000,000', '120,000 to 350,000'],
                         ['Search provider calls', '60', '15 to 50'],
                         ['Agent start-up timeout', '120 s', '120 s'],
                         ['Repetitions', '3 (5 to calibrate)', '3']], 'apparatus')
    api_more = more('Sets, endpoints and the full record', (
        '<p>Each API was set up as its documentation recommends, the same day and with the same number of results. Its '
        f"sets, endpoints, amendments and costs are in the study's <a href=\"{report_page_name(H2H)}#method-{H2H}\">"
        f'methodology</a>, and its full pre-registered record is in <a href="{REPO_URL}/tree/main/reports/{H2H}">'
        f'reports/{H2H}/</a>.</p>'))
    install = f"pipx install 'git+{REPO_URL}'\nsew doctor\npython3 scripts/check_reports.py\npython3 scripts/build_site.py --check"
    rest = ''.join([
        more('Search provider configurations', (
            f'<p>Pinned in <a href="{REPO_URL}/blob/main/config/provider-arm-tools.yaml">config/provider-arm-tools.yaml</a>. '
            "Every tool a provider's server advertised was available to the agent.</p>" + provider_cards(configs)),
            'providers'),
        more('Insight studies', (
            '<p>An insight study sends one fixed set of questions to every search and research service, each run as its '
            "vendor documents. A model extracts each answer's claims, another checks every claim on the page it cites, and a "
            "blind judge values what each verified finding is worth to the question's reader. A service earns a finding "
            'only if its own cited page supports it.</p>'), 'studies-method'),
        more('Reproduce, correct, contribute', (
            "<p>Install Searchlight, run <code>sew doctor</code>, then follow each study's <code>reproduce.md</code>. Raw "
            "transcripts and provider responses are not distributed (except the head-to-head's Monitors probe), so a new run "
            'measures the same method rather than replaying the numbers. If a configuration here misrepresents a service you '
            'run, open an issue: corrections are rerun and published beside the original.</p>'
            f'<pre><code>{esc(install)}</code></pre>'), 'reproduce'),
        more('Terms used in the reports', '<p>The reports keep some technical words. <a href="learn.html#words">The '
             'glossary</a> gives each one with the plain word this site uses.</p>', 'terms')])
    main = f"""<div class="wrap pagehead"><p class="eyebrow">{g("method")}Methodology</p><h1>How Searchlight tests search</h1>
<p class="lede">The same agent, model and instructions in every run; only the search setup changes.</p>
<p class="readlink"><a class="more" href="learn.html#words">The words used here</a></p>{strip}</div>
<div class="wrap doc">

<section id="pipeline"><h2>From task to report</h2>
<p>One test run = task × search setup × repetition. Every tool call is recorded, and the grader never learns the
setup.</p>
{diagrams.figure('pipeline')}</section>

<section id="isolation"><h2>What each setup can touch</h2>
<p>An audit checks every tool call against the setup's contract; a call outside it marks the run contaminated.</p>
{diagrams.figure('isolation')}
{more('The exact commands', f'<h3>Claude Code</h3><pre>{esc(claude_argv)}</pre><h3>Codex</h3><pre>{esc(codex_argv)}</pre>')}
{more('Limits per test run', limits + '<p>Bakeoff limits are set per task.</p>')}
</section>

<section id="calibration"><h2>Which tasks count</h2>
<p>A gap task counts only if the agent fails it with no search and passes it with the answer handed over.</p>
{diagrams.figure('calibration')}</section>

<section id="grading"><h2>How answers are graded</h2>
<p>Two judges see the same inputs; neither is told the search setup.</p>
{diagrams.figure('grading')}
{more('Code fixes and bakeoff tasks', '<p>Code fixes pass when hidden tests pass in an isolated workspace. Bakeoff tasks use '
      'automatic checks where a task has one, and otherwise a single blinded grader scores the task rubric.</p>')}
</section>

<section id="direct-api"><h2>The search API head-to-head</h2>
<p>The APIs themselves, with no agent: the same pre-registered questions, each run once, so counts without ranges.</p>
{diagrams.figure('direct')}
{api_more}
</section>

<section id="more"><h2>The rest of the apparatus</h2>
{rest}
<p class="arrives" id="how-results-get-here">{g("beam")}<span><strong>How results get here.</strong> {ARRIVES}</span></p>
</section>
</div>"""
    if body_only:
        return main
    return _document('Searchlight Methodology', 'How Searchlight tests web search for AI coding agents.',
                     'methodology.html', main, pages=pages)
