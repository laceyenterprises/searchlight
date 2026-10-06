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
    assert 'WebFetch refused' in native['note']
    assert all(row['cells'] == 18 for row in boards['gap-codex']['rows'])
    wsb = boards['wsb-competitive']['rows']
    assert wsb[0]['arm'] == 'firecrawl' and wsb[0]['passes'] == 38 and wsb[0]['cells'] == 42
    assert next(row for row in wsb if row['arm'] == 'native')['note'] == 'WebFetch refused in 2 of 42 cells'


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
    assert methodology.count('<pre class="mermaid">') == 4
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
        assert 'badges describe overlap of individual ranges only' in rendered
        assert 'not distinguishable from the top' not in rendered
        assert board['token_accounting'] in rendered
    wsb = next(b for b in boards if b['bench'] == 'WSB')
    assert 'divided by measured successes' in wsb['token_accounting']
    assert 'per-arm coverage counts were not retained' in wsb['token_accounting']
    assert 'Tokens per success is complete' not in site.build()['results.html']


def test_blockquote_preserves_paragraph_breaks():
    rendered = site.markdown('> First line\n> continued\n> \n> Second paragraph', 'r')
    assert rendered == '<blockquote class="note"><p>First line continued</p><p>Second paragraph</p></blockquote>'
