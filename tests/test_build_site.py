import copy
import importlib.util
import json
from html.parser import HTMLParser
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

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
def synthetic_site(monkeypatch, tmp_path, leaderboard_reports):
    reports = copy.deepcopy(leaderboard_reports)
    for name in site.REPORTS:
        reports.setdefault(name, {"summary": {"blocks": []}})
        reports[name]["summary"].setdefault("blocks", [])
        reports[name]["methodology"] = "# Synthetic methodology\n\nFixture text."
    site.behavior(reports)["gap_totals"] = {"natural_language_pct": 25}
    monkeypatch.setattr(site, "load_reports", lambda root=tmp_path: reports)
    monkeypatch.setattr(site, "arm_configs", lambda root=tmp_path: [])
    monkeypatch.setattr(site, "_catalog_prompt", lambda *args: ("Synthetic task.", ""))
    monkeypatch.setattr(
        site,
        "head_to_head",
        lambda reports: {
            "providers": ["Exa"],
            "measures": [],
            "note": "Synthetic counts.",
            "monitors": {"monitors": 1, "runs": 2, "changes": 0},
        },
    )
    monkeypatch.setattr(
        site,
        "infographic",
        lambda *args: '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    )
    return lambda: site.build(tmp_path)


def test_every_built_page_has_exact_consent_and_gtm_markup(synthetic_site):
    consent = """<!-- Google consent defaults: analytics storage denied in the EEA, UK, Switzerland and listed territories; no consent UI; ad storage off everywhere -->
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'granted'});
gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',analytics_storage:'denied',region:['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH','AX','GF','GP','MQ','RE','YT','MF','GI','JE','GG','IM']});</script>"""
    loader = """<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-TWQZV6F7');</script>
<!-- End Google Tag Manager -->"""
    notice = (
        "Google Tag Manager loads on every page; analytics cookies are disabled for "
        "visitors in the EEA, the UK, Switzerland and listed territories. No consent opt-in is offered."
    )
    assert site.GTM_CONTAINER_ID == "GTM-TWQZV6F7"
    files = synthetic_site()
    pages = {
        name: markup
        for name, markup in files.items()
        if name.endswith(".html") and name != "social-card.html"
    }
    # Every page carries the markup, including the per-report pages the build discovers.
    assert set(pages) == {"index.html", "results.html", "methodology.html", "studies.html", "learn.html"} | {
        site.report_page_name(name) for name in site.REPORTS}
    for name, markup in pages.items():
        head = markup.split("<head>", 1)[1].split("</head>", 1)[0]
        assert head.count(consent) == head.count(loader) == 1, name
        assert (
            markup.count(consent) == markup.count(loader) == 1
        ), name
        assert head.index("<script") == head.index(consent) + consent.index(
            "<script>"
        ), name
        assert head.index(consent) < head.index(loader), name
        assert head.index('<meta name="viewport"') < head.index(consent), name
        assert "<noscript>" not in markup and "<iframe" not in markup, name
        assert markup.count("GTM-TWQZV6F7") == 1, name
        footer = re.search(r"<footer\b.*?</footer>", markup, re.S).group()
        assert footer.count(notice) == 1, name
        assert "gtag/js" not in markup and "gtag('config'" not in markup, name
    card = files["social-card.html"]
    assert (
        "googletagmanager.com" not in card
        and "GTM-" not in card
        and "gtag(" not in card
    )


def test_built_pages_allow_only_existing_external_hosts_and_tag_manager(synthetic_site):
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []
            self.script = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            field = {"script": "src", "iframe": "src", "link": "href"}.get(tag)
            if field and attrs.get(field):
                self.urls.append(attrs[field])
            if tag == "script":
                self.script = attrs.get("type", "").lower() not in {"application/json", "application/ld+json"}

        def handle_endtag(self, tag):
            if tag == "script":
                self.script = False

        def handle_data(self, data):
            if self.script:
                self.urls.extend(re.findall(r"https?://[^\s\"'<>]+", data))

    allowed = {
        "https://fonts.googleapis.com", "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net", "https://searchlightai.dev",
        "https://www.googletagmanager.com",
    }
    for name, markup in synthetic_site().items():
        if not name.endswith(".html"):
            continue
        parser = Assets()
        parser.feed(markup)
        urls = [url for url in parser.urls if urlsplit(url).scheme in {"http", "https"}]
        assert all(f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" in allowed for url in urls), name
        tag_urls = [url for url in urls if urlsplit(url).netloc == "www.googletagmanager.com"]
        assert tag_urls == ([] if name == "social-card.html" else ["https://www.googletagmanager.com/gtm.js?id="]), name


def test_site_build_is_byte_identical_for_the_same_inputs(synthetic_site):
    first = {
        name: content.encode("utf-8") for name, content in synthetic_site().items()
    }
    second = {
        name: content.encode("utf-8") for name, content in synthetic_site().items()
    }
    assert first == second


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


@pytest.mark.parametrize('harness_count', [4, 5])
def test_infographic_wraps_harness_charts_with_forward_axes_and_visible_intervals(
        three_harness_reports, harness_count, monkeypatch):
    reports = three_harness_reports
    blocks = reports['2026-10-03-search-gap-bench']['summary']['blocks']
    setup = next(b['table'] for b in blocks if 'table' in b and any(r[0] == 'Harnesses' for r in b['table']))
    harness_row = next(r for r in setup if r[0] == 'Harnesses')
    codex = next(i for i, b in enumerate(blocks) if '### codex' in b.get('markdown', ''))
    for harness in ['hermes', 'pi'][:harness_count - 3]:
        harness_row[1] += f', {harness} on litellm/glm-5.2'
        blocks.extend([{'markdown': f'### {harness} (6 tasks, 18 cells per arm)'}, copy.deepcopy(blocks[codex + 1])])
    boards = site.leaderboards(reports)
    gap_boards = [b for b in boards if b['bench'] == 'GAP']
    assert len(gap_boards) == harness_count
    # Unequal board lengths must not let the next row overlap the longest chart.
    gap_boards[0]['rows'] = gap_boards[0]['rows'][:2]
    captured = {}
    original = site._gap_forests

    def capture(boards, x, y, width, palette):
        parts, height = original(boards, x, y, width, palette)
        captured.update(chart=ET.fromstring('<svg>' + ''.join(parts) + '</svg>'), y=y, height=height)
        return parts, height

    monkeypatch.setattr(site, '_gap_forests', capture)
    svg = site.infographic(boards, site.behavior(reports))
    chart = captured['chart']
    ticks = [node for node in chart.findall('text') if node.text in ('0%', '50%', '100%')]
    assert len(ticks) == 3 * harness_count
    for start in range(0, len(ticks), 3):
        axis = ticks[start:start + 3]
        positions = [float(node.attrib['x']) for node in axis]
        assert positions[0] < positions[1] < positions[2]
        assert positions[2] - positions[0] >= 120
    intervals = chart.findall('rect')
    assert len(intervals) == sum(len(board['rows']) for board in gap_boards)
    assert all(float(node.attrib['width']) > 1.5 for node in intervals)
    headings = [node for node in chart.findall('text') if node.attrib.get('font-weight') == '600']
    assert len(headings) == harness_count
    for start in range(0, harness_count, 2):
        row_headings = headings[start:start + 2]
        assert len({node.attrib['y'] for node in row_headings}) == 1
        if start:
            previous_bottom = max(float(node.attrib['y']) for node in ticks[(start - 2) * 3:start * 3])
            assert float(headings[start].attrib['y']) > previous_bottom
    assert max(float(node.attrib['y']) for node in chart.findall('text')) <= captured['y'] + captured['height']
    full = ET.fromstring(svg)
    next_finding = next(node for node in full.iter('{http://www.w3.org/2000/svg}text') if node.text == 'FINDING 3')
    assert float(next_finding.attrib['y']) > captured['y'] + captured['height']


def test_forest_rejects_nonpositive_plotting_width(leaderboard_reports):
    board = site.leaderboards(leaderboard_reports)[0]
    with pytest.raises(ValueError, match='positive plotting space'):
        site.forest(board, 0, 0, 180, label_w=80, value_w=100)


@pytest.mark.parametrize('harness', ['opencode', 'hermes'])
@pytest.mark.parametrize('width', [624, 680])
def test_longest_catalog_model_heading_fits_its_panel(three_harness_reports, harness, width):
    from sew.oss import load_catalog

    longest = max(('litellm/' + route for route in load_catalog()), key=len)
    boards = [copy.deepcopy(board) for board in site.leaderboards(three_harness_reports)
              if board['bench'] == 'GAP']
    boards[1].update(harness=harness, model=longest)
    x, y = 48, 40
    _, short_height = site._gap_forests(
        [{**board, 'model': 'short'} for board in boards], x, y, width, site.PALETTE,
    )
    parts, height = site._gap_forests(boards, x, y, width, site.PALETTE)
    chart = ET.fromstring('<svg>' + ''.join(parts) + '</svg>')
    panel_width = (width - 24) / 2
    panel_x = x + panel_width + 24
    model_lines = [node for node in chart.findall('text')
                   if float(node.attrib['x']) == panel_x
                   and node.attrib.get('font-family') == site.MONO
                   and node.attrib['font-size'] == '12']
    assert len(model_lines) == (2 if width == 624 else 1)
    assert ''.join(node.text for node in model_lines) == longest
    for node in model_lines:
        assert float(node.attrib['x']) + len(node.text) * 7.2 <= panel_x + panel_width
    headings = [node for node in chart.findall('text') if node.attrib.get('font-weight') == '600']
    last_model_y = max(float(node.attrib['y']) for node in model_lines)
    right_bars = [node for node in chart.findall('rect') if float(node.attrib['x']) > panel_x]
    assert min(float(node.attrib['y']) for node in right_bars) > last_model_y
    ticks = [node for node in chart.findall('text') if node.text in ('0%', '50%', '100%')]
    assert float(headings[2].attrib['y']) > max(float(node.attrib['y']) for node in ticks[:6])
    assert height > short_height
    assert max(float(node.attrib['y']) for node in chart.findall('text')) <= y + height


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
    table = next(t for _, t in site.tables(reports[site.WSB_PRIMARY]['summary'])
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
    # The apparatus diagrams are drawn by the generator: five figures, each with a wide and a tall layout, and no
    # third-party diagram script on any page.
    assert methodology.count('<figure class="flow"') == 5
    assert methodology.count('<svg class="wide"') == 5 and methodology.count('<svg class="tall"') == 5
    assert all('mermaid' not in page for page in files.values())
    # Each report has its own page; the results page keeps the old #run- and #method- anchors on its index.
    assert 'id="run-2026-10-05-agent-search-behavior"' in results
    assert 'id="method-2026-10-05-agent-search-behavior"' in results
    assert 'href="report-2026-10-05-agent-search-behavior.html"' in results
    behavior_page = files['report-2026-10-05-agent-search-behavior.html']
    assert 'id="run-2026-10-05-agent-search-behavior"' in behavior_page
    assert 'id="method-2026-10-05-agent-search-behavior"' in behavior_page
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


def _text(markup):
    markup = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', markup, flags=re.S)
    return re.sub(r'<[^>]+>', ' ', markup)


def test_results_use_plain_language_in_leaderboards_and_full_reports():
    files = site.build()
    text = _text(files['results.html'])
    assert not re.search(r'\b(?:arms?|cells?|harness(?:es)?)\b', text, re.I)
    assert '21 runs per setup' in text and '42 runs per setup' in text
    assert 'internal terms' not in text
    # The full reports moved to their own pages; every one but the head-to-head's uses the plain words.
    for name in site.REPORTS:
        if name == site.H2H:
            continue  # the head-to-head's report keeps its own words (a table cell, a research arm, its scripts)
        page = _text(files[site.report_page_name(name)])
        assert not re.search(r'\b(?:arms?|cells?|harness(?:es)?)\b', page, re.I), name
    assert 'tokens/run' in _text(files[site.report_page_name(site.WSB_PRIMARY)])


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
    # "How to read these results" lives on Learn; every other page links to it in one line.
    assert 'When two ranges overlap, the test cannot separate them' in site.build()['learn.html'] and 'href="learn.html#read"' in results


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
    files = site.build()
    results, section = files['results.html'], files[site.report_page_name(site.H2H)]
    assert 'ground-truth cells' in section and 'a research arm' in section
    assert '>code/harness.py</a>' in section and 'code/agent.py' not in section
    assert 'Correct values filling a fixed template' in results and 'Schema runs' not in results


def test_head_to_head_appears_on_every_surface():
    files = site.build()
    results, methodology, landing = files['results.html'], files['methodology.html'], files['index.html']
    assert 'id="finding-5"' in results and f'href="{site.report_page_name(site.H2H)}"' in results
    assert 'id="direct-api"' in methodology and f'{site.report_page_name(site.H2H)}#method-{site.H2H}' in methodology
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


def _page_names(files):
    return [name for name in files if name.endswith('.html') and name != 'social-card.html'
            and not name.startswith('study-')]


def test_header_brand_menu_and_phone_layout():
    # The v2 header replaced the old scrolling link row: a bar from 860 px and, below it, a menu that works
    # without JavaScript (<details>), listing every page and every report and study.
    files = site.build()
    reports = [name for name in files if name.startswith('report-')]
    for name in _page_names(files):
        header = re.search(r'<header class="top">.*?</header>', files[name], re.S).group(0)
        assert '<span class="word">Searchlight</span>' in header and f'href="#{site.LOGO_ID}"' in header
        bar = re.search(r'<nav class="navbar"[^>]*>.*?</nav>', header, re.S).group(0)
        assert f'<a class="gh" href="{site.REPO_URL}">GitHub</a>' in bar
        panel = re.search(r'<details class="menu">.*?</details>', header, re.S).group(0)
        for href, _, _ in site.NAV:
            assert f'href="{href}"' in bar and f'href="{href}"' in panel
        for report in reports:
            assert f'href="{report}"' in panel, (name, report)
        assert header.count('aria-current="page"') == (2 if name in dict((h, 1) for h, _, _ in site.NAV) else
                                                       1 if name.startswith('report-') else 0), name
        if name.startswith('report-'):  # the bar marks the section a report sits in
            assert '<a href="results.html" aria-current="true">' in bar
    css = site.CSS
    assert 'position:sticky' in re.search(r'\.top\{([^{}]*)\}', css).group(1)
    assert '@media (min-width:860px){.navbar{display:flex}.menu{display:none}}' in css
    # Phones: no text under 14 px in the site stylesheet.
    sizes = [int(n) for n in re.findall(r'font(?:-size)?:[^;{}]*?(\d+)px', css)]
    assert sizes and min(sizes) >= 14, sorted(sizes)[:5]
    assert '@media (prefers-reduced-motion:reduce)' in css
    assert '[hidden]{display:none!important}' in css


def test_tokens_are_the_single_source_for_site_and_dashboards():
    tokens = (site.ROOT / 'design' / 'tokens.css').read_text(encoding='utf-8')
    assert site.CSS.startswith(tokens)
    for name in ('--paper', '--ink', '--blue', '--blue-ink', '--soft', '--rule', '--muted', '--font-body', '--space-4'):
        assert re.search(rf'{name}:', tokens), name
    assert ':root[data-theme="dark"]' in tokens and 'prefers-color-scheme: dark' in tokens
    template = (site.ROOT / 'lib/python/sew/insight/dashboard.html').read_text(encoding='utf-8')
    block = re.search(r'/\* tokens: design/tokens.css[^*]*\*/\n(.*?)/\* end tokens \*/', template, re.S).group(1)
    assert block == tokens, 'copy design/tokens.css into the dashboard template'
    study = next((v for k, v in site.build().items() if k.startswith('study-')), None)
    if study:
        assert tokens in study and '--sl-accent' in study


def _luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_tokens_meet_aa_contrast_in_both_themes():
    tokens = (site.ROOT / 'design' / 'tokens.css').read_text(encoding='utf-8')
    light = dict(re.findall(r'(--[\w-]+): (#[0-9A-Fa-f]{6})', tokens.split('@media')[0]))
    dark = dict(light, **dict(re.findall(r'(--[\w-]+): (#[0-9A-Fa-f]{6})', tokens.split(':root[data-theme="dark"]')[1])))
    for theme in (light, dark):
        for ink in ('--ink', '--ink-2', '--muted', '--blue-ink', '--amber-ink'):
            for ground in ('--paper', '--soft'):
                assert _contrast(theme[ink], theme[ground]) >= 4.5, (ink, ground, theme[ink], theme[ground])
        assert _contrast(theme['--on-blue'], theme['--blue-ink']) >= 4.5  # the primary button
        assert _contrast(theme['--amber-ink'], theme['--amber-soft']) >= 4.5
        assert _contrast(theme['--blue-ink'], theme['--blue-soft']) >= 4.5


def test_every_glyph_a_page_uses_is_in_the_sprite():
    ids = set(re.findall(r'<symbol id="([\w-]+)"', site.SPRITE))
    for name, markup in site.build().items():
        if name.endswith('.html') and not name.startswith('study-') and name != 'social-card.html':
            used = set(re.findall(r'<use href="#([\w-]+)"', markup))
            assert used and used <= ids, (name, used - ids)
    index = json.loads((site.ROOT / 'design/glyphs/index.json').read_text(encoding='utf-8'))
    assert {g['id'] for g in index} == ids
    vocab = site.vocabulary()
    assert all(row[1] in ids for row in vocab['Concepts']) and all(row[2] in ids for row in vocab['Report terms'])


def test_logo_options_are_drawn_on_the_glyph_grid():
    for logo_id, _, _ in site.glyphs.LOGOS:
        svg = site.glyphs.logo_svg(logo_id)
        assert svg.startswith('<svg') and 'viewBox="0 0 24 24"' in svg and '#007BFF' in svg
        ET.fromstring(svg)
    # The header carries the lamp; the favicon and app icon carry the tile, the boldest at 16 px.
    assert site.LOGO == site.glyphs.logo_svg(site.FAVICON_ID) and site.LOGO_ID == 'logo-lamp'


def test_overview_teaches_with_the_vocabulary_and_has_one_primary_action():
    html = site.build()['index.html']
    main = html.split('<main id="main">', 1)[1].split('</main>', 1)[0]
    assert main.count('class="btn"') == 1 and 'href="results.html"' in main
    # The overview's tiles are a teaser for the one glossary, on Learn.
    concepts = site.vocabulary()['Concepts'][:8]
    assert main.count('class="tile"') == len(concepts)
    for term, _, one, _ in concepts:
        assert site.esc(one) in main and f'href="learn.html#t-{site.slug(term)}"' in main, term


def test_results_open_with_findings_and_rank_every_board():
    files = site.build()
    results = files['results.html']
    main = results.split('<main id="main">', 1)[1]
    assert main.index('id="finding-1"') < main.index('id="leaderboard"')
    reports = site.load_reports()
    boards = site.leaderboards(reports)
    assert any(b['source'] == '2026-10-06-search-gap-replication' for b in site.more_boards(reports))
    data = json.loads(re.search(r'<script type="application/json" id="filter-data">(.*?)</script>', results, re.S).group(1))
    assert len(data) == sum(len(b['rows']) for b in boards)
    for row in data:
        assert f'id="{row["id"]}"' in results, row['id']
    for board in site.leaderboards(reports):
        assert f'id="board-{board["id"]}"' in results
        assert site.esc(site.board_says(board)) in files[site.report_page_name(board['source'])]
    # Later runs are listed in the record with their takeaway; their boards live on their own pages.
    for board in site.more_boards(reports):
        assert f'href="{site.report_page_name(board["source"])}"' in results
        assert f'id="board-{board["id"]}"' in files[site.report_page_name(board['source'])]
    # Each finding leads with its top three rows; the rest open from "All n setups".
    assert results.count('<details class="allrows">') >= 3
    # Filters are an enhancement: hidden without JavaScript, and every row is rendered on the server.
    assert '<form class="filters js-only"' in results
    # Caveats sit beside the number they qualify.
    native = re.search(r'<li class="row ctl" id="r-gap-claude-code-native".*?</li>', results, re.S).group(0)
    assert '<details class="note caveat">' in native and 'Rerun' in native


def test_every_report_and_study_is_two_taps_from_home():
    files = site.build()
    home = files['index.html']
    for name in files:
        if name.startswith(('report-', 'study-')):
            # one tap opens the menu or scrolls to the list; the second opens the page
            assert f'href="{name}"' in home, name


def test_a_new_report_directory_appears_on_the_site_without_edits(tmp_path):
    import shutil
    for part in ('reports', 'config', 'catalogs'):
        shutil.copytree(site.ROOT / part, tmp_path / part)
    source = tmp_path / 'reports' / '2026-10-06-search-gap-replication'
    new = tmp_path / 'reports' / '2026-12-01-search-gap-rerun'
    shutil.copytree(source, new)
    summary = json.loads((new / 'summary.json').read_text(encoding='utf-8'))
    summary['blocks'][0]['markdown'] = summary['blocks'][0]['markdown'].replace(
        '# GAP replication on Claude Code, 2026-10-06', '# December rerun of the gap bench, 2026-12-01', 1)
    (new / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    files = site.build(tmp_path)
    page = files['report-2026-12-01-search-gap-rerun.html']
    assert 'December rerun of the gap bench' in page and 'class="board"' in page
    assert 'id="board-gap-claude-code-2026-12-01"' in page
    assert 'href="report-2026-12-01-search-gap-rerun.html"' in files['results.html']
    assert 'href="report-2026-12-01-search-gap-rerun.html"' in files['index.html']
    data = json.loads(files['leaderboard.json'])
    assert 'reports/2026-12-01-search-gap-rerun/summary.json' in data['generated_from']
    assert any(b['source'] == '2026-12-01-search-gap-rerun' for b in data['boards'])


def test_an_unknown_benchmark_still_gets_a_page_with_a_digest(tmp_path):
    import shutil
    shutil.copytree(site.ROOT / 'reports', tmp_path / 'reports')
    for part in ('config', 'catalogs'):
        shutil.copytree(site.ROOT / part, tmp_path / part)
    new = tmp_path / 'reports' / '2026-12-02-domain-search-bench'
    new.mkdir()
    (new / 'summary.json').write_text(json.dumps({'schema_version': 1, 'bench': 'DSB', 'blocks': [
        {'markdown': '# Domain search bench, 2026-12-02\n\n## Results'},
        {'table': [['Arm', 'Pass rate (Wilson 95% CI)'], ['---', '---'], ['Exa', '80% (60–92%)'],
                   ['Brave', '70% (50–85%)']]}]}), encoding='utf-8')
    (new / 'methodology.md').write_text('# Method\n\nFixture.', encoding='utf-8')
    files = site.build(tmp_path)
    page = files['report-2026-12-02-domain-search-bench.html']
    assert 'Domain search bench' in page and 'Exa ranked first' in page and '80%' in page


def test_only_the_header_uses_the_top_class():
    # `.top` is the sticky header; any other element with that class becomes sticky too
    # (the leaderboard pills once did and slid over the header on phones).
    for name, html in site.build().items():
        if not name.endswith('.html') or name == 'social-card.html' or name.startswith('study-'):
            continue  # hosted study dashboards carry their own header
        uses = [tag for tag, cls in re.findall(r'<(\w+)[^>]*\bclass="([^"]*)"', html) if 'top' in cls.split()]
        assert uses == ['header'], (name, uses)


def test_hosted_study_page_carries_consent_tags_notice_and_raw_links():
    from pathlib import Path
    template = Path(site.ROOT, "lib/python/sew/insight/dashboard.html").read_text(encoding="utf-8")
    page = site.study_page({"name": "2026-01-01-example", "page": "study-2026-01-01-example.html",
                            "dashboard": template, "summary": {}})
    head = page.split("<head>", 1)[1].split("</head>", 1)[0]
    assert head.count(site.GTM_HEAD) == 1 and page.count(site.GTM_CONTAINER_ID) == 1
    assert head.index('<meta name="viewport"') < head.index("<script")
    assert "<iframe" not in page and "<noscript>" not in page
    footers = re.findall(r"<footer\b.*?</footer>", page, re.S)
    assert sum(f.count(site.ANALYTICS_NOTICE) for f in footers) == 1
    assert 'data-raw-base="https://github.com/laceyenterprises/searchlight/blob/main/reports/2026-01-01-example/"' in page
    assert 'href="studies.html"' in page


def test_studies_page_lists_published_insight_studies():
    studies = site.insight_studies()
    assert all(s["summary"]["schema"] == site.INSIGHT_SCHEMA for s in studies)
    page = site.studies_page(studies)
    for s in studies:
        assert f'href="{s["page"]}"' in page


def test_filter_prose_has_a_variant_for_every_agent_and_hides_off_topic_findings():
    results = site.build()['results.html']
    reports = site.load_reports()
    agents = {b['harness'] for b in site.leaderboards(reports) if b['bench'] == 'GAP'}
    for n in (1, 2):
        section = re.search(rf'<section class="finding" id="finding-{n}".*?</section>', results, re.S).group(0)
        variants = set(re.findall(r'data-prose="([^"]*)"', section))
        assert variants == {''} | agents, (n, variants)
    # The direct-API finding is about no agent: an agent filter hides it.
    assert re.search(r'<section class="finding" id="finding-5"[^>]*data-agent="-"', results)
    # Tiles that describe one agent say so.
    assert results.count('class="stat" data-f data-agent="claude-code"') == 2


def test_latest_strip_names_the_newest_report_or_study():
    files = site.build()
    newest = site.site_map(site.load_reports(), site.insight_studies())[0]
    strip = re.search(r'<a class="latest" href="([^"]+)">', files['index.html'])
    assert strip and strip.group(1) == newest[0]


def test_study_page_shares_the_site_shell():
    files = site.build()
    page = next(v for k, v in files.items() if k.startswith('study-'))
    assert '<header class="top">' in page and 'id="theme"' not in page and '<footer class="foot">' in page
    assert 'class="finish"' in page and 'Full data' in page and '@scope (.dash)' in page
    assert '<main id="main">' in page and page.count('id="main"') == 1


def test_diagrams_keep_every_node_and_edge_and_fit_their_column():
    from searchlight_site import diagrams as d
    for diagram in d.DIAGRAMS.values():
        for layout, width in (('wide', d.WIDE), ('tall', d.TALL)):
            placed = [cell[0] for row in getattr(diagram, layout) for cell in row]
            assert sorted(placed) == sorted(n.id for n in diagram.nodes), (diagram.name, layout)
            t = d.TYPE[layout]
            boxes = d.place(getattr(diagram, layout), {n.id: n for n in diagram.nodes}, t)
            assert all(0 <= x and x + w <= width for x, _, w, _ in boxes.values()), (diagram.name, layout)
        svg = d._flow_svg(diagram, 'wide')
        assert svg.count('class="e"') == len(diagram.edges)
    assert min(t[k] for t in d.TYPE.values() for k in ('t', 'b', 'm', 'e')) >= 14
    figure = d.figure('grading')
    assert figure.count('class="e') >= 2 * len(d.GRADING.messages)


def test_diagram_text_stays_inside_its_card():
    """Every wrapped line fits inside its card's padding, so no word, flag or path touches the border."""
    from searchlight_site import diagrams as d
    for diagram in d.DIAGRAMS.values():
        for layout in ('wide', 'tall'):
            t = d.TYPE[layout]
            for row in getattr(diagram, layout):
                for nid, _, w, *_ in row:
                    stacked, title, body = d._lines(next(n for n in diagram.nodes if n.id == nid), w, t)
                    title_room = w - 2 * d.PAD - (0 if stacked else d.GLYPH + 10)
                    for text, kind in title:
                        assert d._width(text, t[kind], kind) <= title_room, (diagram.name, layout, text)
                    for text, kind in body:
                        assert d._width(text, t[kind], kind) <= w - 2 * d.PAD, (diagram.name, layout, text)


def test_one_glossary_lives_on_learn():
    files = site.build()
    assert 'id="report-terms"' in files['learn.html'] and 'class="glossary"' in files['learn.html']
    assert 'class="glossary"' not in files['methodology.html'] and 'class="terms"' not in files['index.html']
    assert 'Every published run appears here automatically when it is merged' in files['methodology.html']


def _inside(point, box, margin=0.0):
    x, y, w, h = box
    return x + margin < point[0] < x + w - margin and y + margin < point[1] < y + h - margin


def _overlap(a, b, gap=0.0):
    return not (a[0] + a[2] + gap <= b[0] or b[0] + b[2] + gap <= a[0] or a[1] + a[3] + gap <= b[1]
                or b[1] + b[3] + gap <= a[1])


@pytest.mark.parametrize('layout', ['wide', 'tall'])
def test_diagram_layouts_have_no_collisions(layout):
    """No edge crosses a group's label band or runs through a card it does not join, and groups keep apart."""
    from searchlight_site import diagrams as d
    for diagram in d.DIAGRAMS.values():
        geo = d.geometry(diagram, layout)
        for i, g in enumerate(geo['groups']):
            for e in geo['edges']:
                assert not any(_inside(p, g['label']) for p in e['points']), (diagram.name, layout, g['lines'], e['source'])
            for other in geo['groups'][i + 1:]:
                assert not _overlap(g['rect'], other['rect'], d.GROUP_GAP - 1), (diagram.name, layout, g['lines'])
        for e in geo['edges']:
            for nid, box in geo['boxes'].items():
                if nid in (e['source'], e['target']):
                    continue
                assert not any(_inside(p, box, 2) for p in e['points']), (diagram.name, layout, e['source'],
                                                                           e['target'], nid)
        for e in geo['edges']:
            if e['label_box']:
                assert not any(_overlap(e['label_box'], box) for box in geo['boxes'].values()), (diagram.name, layout,
                                                                                               e['label'])
        width = d.WIDE if layout == 'wide' else d.TALL
        for g in geo['groups']:
            assert 0 <= g['rect'][0] and g['rect'][0] + g['rect'][2] <= width, (diagram.name, layout)
