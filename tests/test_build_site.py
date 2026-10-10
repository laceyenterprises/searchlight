import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_site', ROOT / 'scripts' / 'build_site.py')
site = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site)


def test_committed_site_is_current():
    assert site.main(['--check']) == 0


@pytest.fixture
def leaderboard_reports():
    gap_blocks = []
    for harness, cells in [('claude-code', 21), ('codex', 18)]:
        gap_blocks.extend([
            {'markdown': f'### {harness}: {cells} cells per arm'},
            {'table': [
                ['Arm', 'Gap closure (95% CI)', 'Pass rate (Wilson 95% CI)', 'Tokens', 'vs floor'],
                ['Parallel', '80%', '95% (77–99%)', '100', 'yes'],
                ['native', '20%', '50% (30–70%)', '200', 'yes'],
                ['floor', '0%', '10% (1–30%)', '0', ''],
                ['ceiling', '100%', '100% (80–100%)', '0', ''],
            ]},
        ])
    return {
        '2026-10-03-search-gap-bench': {'summary': {'blocks': gap_blocks}},
        '2026-09-29-web-search-bakeoff': {'summary': {'blocks': [{'table': [
            ['arm', 'pass', 'tokens/success'],
            ['Firecrawl', '38/42', '100'], ['Native', '20/42', '200'],
            ['no-search', '10/42', '300'],
        ]}]}},
        '2026-10-05-agent-search-behavior': {'summary': {'analysis': {
            'bakeoff_rows': [{'arm': 'native', 'cells_with_refused_calls': 2, 'cells': 42}],
        }}},
    }


def test_leaderboards_transcribe_published_tables(leaderboard_reports):
    boards = {board['id']: board for board in site.leaderboards(leaderboard_reports)}
    assert set(boards) == {'gap-claude-code', 'gap-codex', 'wsb-competitive'}
    claude = boards['gap-claude-code']['rows']
    assert len(claude) == 2 and all(row['cells'] == 21 for row in claude)
    assert claude[0]['label'] == 'Parallel' and claude[0]['ci'] == [77.0, 99.0]
    native = next(row for row in claude if row['arm'] == 'native')
    assert native['note'] == site.RERUN_NOTE and native['flagged'] is False
    assert all(row['cells'] == 18 for row in boards['gap-codex']['rows'])
    wsb = boards['wsb-competitive']['rows']
    assert wsb[0]['arm'] == 'firecrawl' and wsb[0]['passes'] == 38 and wsb[0]['cells'] == 42
    assert next(row for row in wsb if row['arm'] == 'native')['note'] == site.RERUN_NOTE
    assert not any(row['flagged'] for board in boards.values() for row in board['rows'])


@pytest.fixture
def three_harness_reports():
    """The published reports plus a synthetic third GAP harness on an OSS model."""
    reports = site.load_reports()
    blocks = reports['2026-10-03-search-gap-bench']['summary']['blocks']
    setup = next(b['table'] for b in blocks if 'table' in b and any(r[0] == 'Harnesses' for r in b['table']))
    row = next(r for r in setup if r[0] == 'Harnesses')
    row[1] += ', opencode on litellm/glm-5.2'
    codex = next(i for i, b in enumerate(blocks) if '### codex' in b.get('markdown', ''))
    table = copy.deepcopy(blocks[codex + 1])
    blocks[codex + 2:codex + 2] = [{'markdown': '### opencode (6 tasks, 18 cells per arm)'}, table]
    return reports


def test_leaderboards_carry_any_number_of_harnesses_and_their_models(three_harness_reports):
    boards = site.leaderboards(three_harness_reports)
    gap = [b for b in boards if b['bench'] == 'GAP']
    assert [b['harness'] for b in gap] == ['claude-code', 'codex', 'opencode']
    titles = {b['harness']: b['title'] for b in gap}
    assert titles['claude-code'] == 'Search gap bench: Claude Code (claude-opus-5-5)'
    assert titles['codex'] == 'Search gap bench: Codex (gpt-6.1-sol)'
    assert titles['opencode'] == 'Search gap bench: Opencode (litellm/glm-5.2)'
    assert site.harness_name(gap[2]) == ('Opencode', 'litellm/glm-5.2')

    svg = site.infographic(boards, site.behavior(three_harness_reports))
    assert svg.endswith('</svg>')
    for name in ('Claude Code', 'Codex', 'Opencode', 'litellm/glm-5.2'):
        assert f'>{name}</text>' in svg
    assert '· 3 AI coding agents ·' in svg
    assert site.headline(boards, site.behavior(three_harness_reports))['agents'] == 3


def test_a_report_without_model_names_titles_boards_by_harness(leaderboard_reports):
    boards = {b['id']: b for b in site.leaderboards(leaderboard_reports)}
    assert boards['gap-codex']['title'] == 'Search gap bench: Codex'
    assert site.harness_name(boards['gap-codex']) == ('Codex', '')


def test_published_leaderboard_data_has_no_model_key():
    data = json.loads(site.build()['leaderboard.json'])
    assert all('model' not in board for board in data['boards'])


def test_wilson_interval_matches_reference_values():
    lo, hi = site.wilson(38, 42)
    assert round(lo) == 78 and round(hi) == 96
    assert site.wilson(0, 0) == (0.0, 0.0)


@pytest.mark.parametrize('cell, expected', [
    ('95% (90–99%)', (95, 90, 99)),
    ('95% (90-99%)', (95, 90, 99)),
    ('95.5% (90.25–99.75%)', (95.5, 90.25, 99.75)),
    (' 95.5%\t(90.25 - 99.75%) ', (95.5, 90.25, 99.75)),
])
def test_percentage_intervals_accept_report_text_variations(cell, expected):
    assert site.parse_pct_ci(cell) == expected


@pytest.mark.parametrize('cell', ['95..5% (90-99%)', '95% (90-%)', '95% (90-99%) junk'])
def test_percentage_intervals_reject_malformed_numbers(cell):
    with pytest.raises(ValueError, match='expected'):
        site.parse_pct_ci(cell)


def test_zero_count_leaderboard_arm(leaderboard_reports):
    reports = leaderboard_reports
    table = next(t for _, t in site.tables(reports[site.REPORTS[0]]['summary'])
                 if t[0] == ['arm', 'pass', 'tokens/success'])
    arm = table[2][0].lower()
    table[2][1] = '0/0'
    board = next(b for b in site.leaderboards(reports) if b['id'] == 'wsb-competitive')
    row = next(r for r in board['rows'] if r['arm'] == arm)
    assert row['passes'] == row['cells'] == row['pass_pct'] == 0
    assert row['ci'] == [0, 0]


@pytest.mark.parametrize('empty_keys', [
    ('pass_when_surfaced',), ('pass_when_not_surfaced',),
    ('pass_when_surfaced', 'pass_when_not_surfaced'),
])
def test_infographic_handles_empty_behavior_groups(empty_keys):
    reports = site.load_reports()
    boards = site.leaderboards(reports)
    analysis = site.behavior(reports)
    for row in analysis['gap_rows']:
        if row['harness'] == 'claude-code':
            for key in empty_keys:
                row[key] = [0, 0]
    rendered = site.infographic(boards, analysis)
    assert rendered.endswith('</svg>')
    comparison = re.search(r'>(\d+)% vs (\d+)%</text>', rendered)
    assert comparison
    if 'pass_when_surfaced' in empty_keys:
        assert comparison.group(1) == '0'
    if 'pass_when_not_surfaced' in empty_keys:
        assert comparison.group(2) == '0'


def test_infographic_names_every_arm_and_no_private_paths():
    files = site.build()
    svg = files['infographic.svg']
    for label in ('Brave', 'Tavily', 'Exa', 'Parallel', 'Firecrawl', 'Perplexity', 'Built-in', 'No search'):
        assert label in svg
    for content in files.values():
        assert '/Users/' not in content and 'op://' not in content
    data = json.loads(files['leaderboard.json'])
    assert data['boards'] and 'Overlapping intervals are not rankings' in data['note']


def test_page_has_apparatus_diagrams_and_resolved_links():
    files = site.build()
    methodology, results = files['methodology.html'], files['results.html']
    assert methodology.count('<pre class="mermaid">') == 5
    assert (f'<script src="{site.MERMAID_URL}" '
            'integrity="sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I" '
            'crossorigin="anonymous"></script>') in methodology
    assert 'mermaid.min.js' not in files['index.html'] + results
    assert 'href="#run-2026-10-05-agent-search-behavior"' in results
    for html in files.values():
        assert not re.search(r'href="[^"#h][^"]*\.md"', html), 'relative markdown links must resolve'
    head, body = site.page(site.load_reports(), site.leaderboards(site.load_reports()), site.arm_configs(), 'preview')
    assert 'mermaid.min.js' not in head + body and '<html' not in head + body


def test_site_pages_share_brand_and_navigation():
    files = site.build()
    assert files['logo.svg'].startswith('<svg') and 'Searchlight' in files['logo.svg']
    for name in ('index.html', 'results.html', 'methodology.html'):
        html = files[name]
        assert '<link rel="icon" type="image/svg+xml" href="logo.svg">' in html
        for href in ('index.html', 'results.html', 'methodology.html', site.REPO_URL):
            assert f'href="{href}"' in html
        assert f'{site.REPO_URL}/blob/main/LICENSE' in html and f'{site.REPO_URL}/issues/new' in html
        assert 'Lacey Enterprises' in html


def test_landing_page_is_plain_language_and_complete():
    html = site.build()['index.html']
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    for jargon in (r'\barms?\b', r'\bcells?\b', r'\bharness(es)?\b', r'\bWilson\b', r'\bMCP\b'):
        assert not re.search(jargon, text, re.I), jargon
    for section in ('id="findings"', 'id="how"', 'id="examples"', 'id="report"'):
        assert section in html
    assert html.count('class="card example"') == 4
    assert 'href="results.html"' in html and 'href="methodology.html"' in html


def test_results_use_plain_language_in_leaderboards_and_full_reports():
    rendered = site.build()['results.html']
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', rendered, flags=re.S)
    # The head-to-head's full report keeps its own words (a table cell, a search or research arm, its scripts).
    text = re.sub(rf'<section id="run-{site.H2H}">.*?</section>', ' ', text, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    assert not re.search(r'\b(?:arms?|cells?|harness(?:es)?)\b', text, re.I)
    assert '21 runs per setup' in text and '42 runs per setup' in text
    assert 'internal terms' not in text
    assert 'tokens/run' in text


def test_results_language_preserves_links_anchors_and_identifiers():
    markup = ('<h3 id="21-cells-per-arm">21 cells per arm</h3>'
              '<a href="#21-cells-per-arm">Arms</a>'
              '<a href="config/provider-arm-tools.yaml">Harnesses</a>'
              '<code>tokens/cell</code> cross-harness cells cells_with_refused_calls')
    assert site.results_language(markup) == (
        '<h3 id="21-cells-per-arm">21 runs per setup</h3>'
        '<a href="#21-cells-per-arm">Setups</a>'
        '<a href="config/provider-arm-tools.yaml">Agents</a>'
        '<code>tokens/run</code> cross-agent runs cells_with_refused_calls')


def test_site_chart_css_variables_are_defined():
    for name in ('index.html', 'results.html', 'methodology.html'):
        rendered = site.build()[name]
        defined = set(re.findall(r'(--[\w-]+)\s*:', rendered))
        referenced = set(re.findall(r'var\((--[\w-]+)\)', rendered))
        assert referenced <= defined, (name, referenced - defined)


def test_markdown_renderer_handles_lists_quotes_and_anchors():
    rendered = site.markdown('# Title\n\n> **Note** with [link](#a-b)\n\n- one\n  - two\n- three\n\nText `code`.', 'r')
    assert '<h2 id="r-title">' in rendered
    assert '<blockquote class="note"><p><strong>Note</strong> with <a href="#r-a-b">link</a></p></blockquote>' in rendered
    assert rendered.count('<ul>') == 2 and '<code>code</code>' in rendered


@pytest.mark.parametrize('closing_fence', ['', '\n```'])
def test_markdown_preserves_code_at_eof(closing_fence):
    rendered = site.markdown('Intro\n\n```python\nvalue = "<tag>"\nnext_line' + closing_fence, 'r')
    assert '<p>Intro</p>' in rendered
    assert '<pre><code>value = &quot;&lt;tag&gt;&quot;\nnext_line</code></pre>' in rendered


def test_table_pads_short_rows_without_changing_numeric_alignment():
    rows = [['Arm', 'Passes', 'Cells'], ['---', '---', '---'], ['Exa', '2', '3'], ['native', '1'], ['empty']]
    rendered = site.table_html(rows, 'r')
    assert '<th>Arm</th><th class="num">Passes</th><th class="num">Cells</th>' in rendered
    assert '<tr><td>native</td><td class="num">1</td><td class="num"></td></tr>' in rendered
    assert '<tr><td>empty</td><td class="num"></td><td class="num"></td></tr>' in rendered
    assert rows[-1] == ['empty']


@pytest.mark.parametrize('path', ['../../../README.md', '../../../../README.md', '../../../../../../README.md'])
def test_links_with_excess_parent_segments_do_not_crash(path):
    assert site.resolve_link(path + '#section', site.REPORTS[0]) == site.REPO_URL + '/blob/main/README.md#section'


def test_report_and_methodology_fragments_target_rendered_heading_ids():
    report = site.REPORTS[-1]
    for filename, namespace in [('REPORT.md', report), ('methodology.md', report + '-method')]:
        rendered = site.markdown('## Setup\n\n[local](#setup)', namespace, base=report)
        anchor = site.resolve_link(filename + '#setup', report)
        assert f'id="{anchor[1:]}"' in rendered
        assert f'href="{anchor}"' in rendered
        assert site.resolve_link(f'../{report}/{filename}#setup', site.REPORTS[0]) == anchor
    assert site.resolve_link('REPORT.md', report) == '#run-' + report
    assert site.resolve_link('methodology.md', report) == '#method-' + report


def test_boards_describe_interval_overlap_and_benchmark_token_coverage():
    boards = site.leaderboards(site.load_reports())
    for board in boards:
        rendered = site.board_html(board)
        assert 'not distinguishable from the top' not in rendered
        assert '95% Wilson intervals' in rendered
        # Only the best observed rate is called the leader; the rest say how their range sits against it.
        assert rendered.count('>leader<') == sum(r['pass_pct'] == board['rows'][0]['pass_pct'] for r in board['rows'])
    wsb = next(b for b in boards if b['bench'] == 'WSB')
    # leaderboard.json keeps the report's accounting; the page says the same in plain words.
    assert 'divided by measured successes' in wsb['token_accounting']
    assert 'per-arm coverage counts were not retained' in wsb['token_accounting']
    assert 'how many runs each setup was missing was not recorded' in site.board_html(wsb)
    results = site.build()['results.html']
    assert 'Tokens per success is complete' not in results
    assert 'the difference may be chance' in results


def test_boards_explain_the_pass_rule_in_plain_words():
    boards = site.leaderboards(site.load_reports())
    for board in (b for b in boards if b['bench'] == 'GAP'):
        # The machine-readable scope keeps the report's thresholds; the caption spells them out.
        assert f'recall ≥ {site.RECALL_MIN}' in board['scope']
        assert f'unsupported claims ≤ {site.UNSUPPORTED_MAX}' in board['scope']
        rendered = site.board_html(board)
        assert 'at least 70% of the key facts' in rendered and 'no more than 25% of its claims' in rendered
        assert '≥' not in rendered and '≤' not in rendered and 'recall' not in rendered
        floor, ceiling = (site._point_pct(board['reference'][k])
                          for k in ('floor (no search)', 'ceiling (answer excerpt)'))
        assert f'from {floor} toward {ceiling}' in rendered
    assert site._gap_pct('1.06 (0.88–1.29)') == '106% (88–129%)'
    assert site._gap_pct('-0.12 (-0.30–0.10)') == '-12% (-30–10%)'
    assert site._gap_pct('n/a') == 'n/a'
    assert site._point_pct('89% (67–97%)') == "89%"


@pytest.mark.parametrize('cell, expected', [
    ('25', (25, None)), ('**16**', (16, None)), ('105/133 (78.9%)', (105, 133)), (' 7/149 (4.7%) ', (7, 149)),
])
def test_tallies_accept_report_cells(cell, expected):
    assert site.parse_tally(cell) == expected


@pytest.mark.parametrize('cell', ['$0.50', '28/36 extra', ''])
def test_tallies_reject_other_cells(cell):
    with pytest.raises(ValueError, match='expected'):
        site.parse_tally(cell)


def test_head_to_head_transcribes_report_tables():
    reports = site.load_reports()
    h2h = site.head_to_head(reports)
    measures = {m['id']: m for m in h2h['measures']}
    assert h2h['providers'] == ['Exa', 'Tavily', 'Parallel', 'Firecrawl']
    assert measures['s1']['values'] == {'Exa': 25, 'Tavily': 11, 'Parallel': 19, 'Firecrawl': 10}
    assert measures['s3']['of'] == 15 and measures['s6']['of'] == 133
    assert 'Tavily' not in measures['s6']['values'] and 'Tavily' not in measures['s5']['values']
    assert measures['sv']['values'] == {'Exa': 16, 'Tavily': 13, 'Parallel': 14, 'Firecrawl': 9}
    probe = h2h['monitors']
    assert (probe['monitors'], probe['runs'], probe['changes']) == (5, 49, 20)
    # Every measure is a row of a transcribed table, so REPORT.md shows the same cells.
    report = (site.ROOT / 'reports' / site.H2H / 'REPORT.md').read_text(encoding='utf-8')
    assert all(m['source_row'] in report for m in h2h['measures'])


def test_head_to_head_report_keeps_its_own_terms():
    results = site.build()['results.html']
    section = re.search(rf'<section id="run-{site.H2H}">.*?</section>', results, re.S).group()
    assert 'ground-truth cells' in section and 'a research arm' in section
    assert '>code/harness.py</a>' in section and 'code/agent.py' not in results
    assert 'Correct values filling a fixed template' in results and 'Schema runs' not in results


def test_head_to_head_appears_on_every_surface():
    files = site.build()
    results, methodology, landing = files['results.html'], files['methodology.html'], files['index.html']
    assert 'id="head-to-head"' in results and f'href="#run-{site.H2H}"' in results
    assert 'id="direct-api"' in methodology and f'results.html#method-{site.H2H}' in methodology
    assert 'Search API head-to-head' in landing
    assert 'FINDING 5' in files['infographic.svg'] and 'not run' in files['infographic.svg']
    data = json.loads(files['leaderboard.json'])
    assert data['search_api_head_to_head']['source'] == site.H2H
    assert f'reports/{site.H2H}/summary.json' in data['generated_from']
    # The agent-benchmark caveat no longer claims direct APIs were never tested.
    assert 'Direct APIs, other search modes and other tools were not tested' not in results + files['infographic.svg']


def test_blockquote_preserves_paragraph_breaks():
    rendered = site.markdown('> First line\n> continued\n> \n> Second paragraph', 'r')
    assert rendered == '<blockquote class="note"><p>First line continued</p><p>Second paragraph</p></blockquote>'


def _tiny_png() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00'))
            + chunk(b'IEND', b''))


def test_social_card_is_short_plain_and_sourced():
    reports = site.load_reports()
    facts = site.card_facts(site.leaderboards(reports), site.behavior(reports))
    card = site.build()['social-card.html']
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', card, flags=re.S)
    text = re.sub(r'<title>.*?</title>', ' ', text)
    text = ' '.join(re.sub(r'<[^>]+>', ' ', text).split())
    assert len(facts['stats']) == 3
    for big, label in facts['stats']:
        assert site.esc(big) in card and site.esc(label) in card
        assert len(label.split()) <= 6
    assert site.esc(facts['title']) in card and 'searchlightai.dev' in text
    assert len(text.split()) <= 40, text
    for jargon in (r'\barms?\b', r'\bcells?\b', r'\bharness(es)?\b', r'\bWilson\b', r'\bMCP\b'):
        assert not re.search(jargon, text, re.I), jargon


def test_every_page_carries_social_metadata():
    files = site.build()
    for name, url in (('index.html', 'https://searchlightai.dev/'),
                      ('results.html', 'https://searchlightai.dev/results.html'),
                      ('methodology.html', 'https://searchlightai.dev/methodology.html')):
        html = files[name]
        assert '<meta property="og:image" content="https://searchlightai.dev/social-card.png">' in html
        assert '<meta name="twitter:card" content="summary_large_image">' in html
        assert f'<meta property="og:url" content="{url}">' in html
        assert f'<link rel="canonical" href="{url}">' in html
        assert '<meta property="og:image:width" content="2400">' in html


def test_card_png_stamp_round_trips_and_detects_staleness(tmp_path):
    png = _tiny_png()
    stamped = site.stamp_png(png, site.CARD_KEY, 'a' * 64)
    assert site.png_text_chunks(stamped)[site.CARD_KEY] == 'a' * 64
    restamped = site.stamp_png(stamped, site.CARD_KEY, 'b' * 64)
    assert site.png_text_chunks(restamped) == {site.CARD_KEY: 'b' * 64}
    assert site.png_size(restamped) == (1, 1)
    assert 'missing' in site.card_problem(tmp_path, 'source')
    (tmp_path / 'social-card.png').write_bytes(site.stamp_png(png, site.CARD_KEY, site.card_source_hash('source')))
    assert site.card_problem(tmp_path, 'source') == ''
    assert 'different card source' in site.card_problem(tmp_path, 'changed source')
    (tmp_path / 'social-card.png').write_bytes(b'not a png')
    assert 'not a PNG' in site.card_problem(tmp_path, 'source')


def test_header_and_phone_layout():
    files = site.build()
    for name in ('index.html', 'results.html', 'methodology.html'):
        header = re.search(r'<header class="top">.*?</header>', files[name], re.S).group(0)
        nav = re.search(r'<nav class="nav"[^>]*>.*?</nav>', header, re.S).group(0)
        assert site.REPO_URL not in nav, 'GitHub is its own header element, hidden on phones'
        assert f'<a class="btn gh" href="{site.REPO_URL}">GitHub</a>' in header
    # Include complete rule bodies, but stop at the media query's closing brace.
    # A trailing query must not leak into the phone assertions.
    css = site.CSS + '\n@media (min-width:1200px){.after-phone{color:red}}'
    phone = re.search(r'@media \(max-width:640px\)\{((?:[^{}]|\{[^{}]*\})*)\}', css).group(1)
    assert '.after-phone' not in phone
    # A desktop rule must not satisfy a missing phone declaration.
    header_rule = re.search(r'\.board thead\{[^{}]*\}', phone).group(0)
    moved = css.replace(header_rule, '') + '\n' + header_rule
    moved_phone = re.search(r'@media \(max-width:640px\)\{((?:[^{}]|\{[^{}]*\})*)\}', moved).group(1)
    assert header_rule not in moved_phone
    # Keep column headers available to screen readers while visually hiding them.
    header_style = re.search(r'\.board thead\{([^{}]*)\}', phone).group(1)
    for declaration in ('position:absolute', 'width:1px', 'height:1px', 'overflow:hidden',
                        'clip:rect(0,0,0,0)'):
        assert declaration in header_style
    assert 'display:none' not in header_style and 'visibility:hidden' not in header_style
    # The header stays pinned on phones: one row with the mark and the links.
    assert 'position:static' not in phone and 'position:sticky' in re.search(r'\.top\{([^{}]*)\}', site.CSS).group(1)
    for rule in ('.top .brand span,.top .gh{display:none}',
                 '.board td.gap::before,.board td.tps::before{content:attr(data-label)', '.infographic svg{min-width:0}',
                 '.diagram svg{min-width:600px'):
        assert rule in phone, rule
    assert 'white-space:nowrap' in re.search(r'\.pill\{([^{}]*)\}', site.CSS).group(1)
    results = files['results.html']
    assert 'data-label="Gap closed"' in results and 'data-label="Tokens per pass"' in results
    assert '<td class="setup">' in results and '<td class="notes">' in results
    # The phone SVG minimum width relies on the base frame being scrollable.
    diagram = re.search(r'\.diagram\{([^{}]*)\}', site.CSS).group(1)
    assert 'overflow-x:auto' in diagram
    assert 'Open the infographic full size' in files['results.html']


def test_only_the_header_uses_the_top_class():
    # `.top` is the sticky header; any other element with that class becomes sticky too
    # (the leaderboard pills once did and slid over the header on phones).
    for name, html in site.build().items():
        if not name.endswith('.html') or name == 'social-card.html':
            continue
        uses = [tag for tag, cls in re.findall(r'<(\w+)[^>]*\bclass="([^"]*)"', html) if 'top' in cls.split()]
        assert uses == ['header'], (name, uses)
