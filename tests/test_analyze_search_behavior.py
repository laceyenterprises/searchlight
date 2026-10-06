import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('analyze_search_behavior', ROOT / 'scripts' / 'analyze_search_behavior.py')
behavior = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(behavior)


def claude_call(call_id, name, arguments, result):
    return [
        {'harness_event': {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': call_id, 'name': name, 'input': arguments}]}}},
        {'harness_event': {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': call_id, 'content': result}]}}},
    ]


def codex_mcp(tool, arguments, text):
    return {'harness_event': {'type': 'item.completed', 'item': {
        'type': 'mcp_tool_call', 'server': 'vendor', 'tool': tool, 'arguments': arguments,
        'result': {'content': [{'type': 'text', 'text': text}]}}}}


def write_cell(root, run_id, harness, arm, task, transcript, passed=None):
    cell = root / 'bundles' / run_id
    (cell / 'artifacts').mkdir(parents=True)
    (cell / 'artifacts' / 'transcript.json').write_text(json.dumps(transcript))
    if passed is not None:
        (cell / 'evaluations').mkdir()
        (cell / 'evaluations' / 'gap-outcome.json').write_text(json.dumps({'status': 'scored', 'passed': passed}))
    return {'run_dir': str(cell), 'harness_id': harness, 'provider_id': arm, 'task_id': task, 'run_id': run_id}


def test_query_style_rules():
    style = behavior.query_style
    assert style('How does the Python 3.10 tarfile filter work?')['natural_language']
    assert style('blog post comparing React and Vue performance')['natural_language'] is False
    assert style('the release page for the security fix of Python')['natural_language']
    keyword = style('site:github.blog/changelog 2026 January "Move work" converting')
    assert keyword == {'words': 8, 'natural_language': False, 'quoted': True,
                       'site_operator': True, 'year': True, 'boolean_operator': False}
    assert style('python 3.10.21 OR 3.10.20 release')['boolean_operator']


def test_codex_builtin_search_and_page_views_are_split():
    events = list(behavior.tool_events([
        {'harness_event': {'type': 'item.completed', 'item': {
            'type': 'web_search', 'query': 'a ...', 'action': {'type': 'search', 'queries': ['alpha beta', 'gamma']}}}},
        {'harness_event': {'type': 'item.completed', 'item': {
            'type': 'web_search', 'query': 'https://example.org/x', 'action': {'type': 'open_page', 'url': 'https://example.org/x'}}}},
    ]))
    assert [name for name, _, _ in events] == ['codex_native_search', 'codex_native_open']
    assert behavior.queries_of(events[0][1]) == ['alpha beta', 'gamma']


def test_parallel_objective_is_a_parameter_not_a_query():
    arguments = {'objective': 'Find the deprecation notice for account conversion', 'search_queries': ['github account conversion 2026']}
    assert behavior.queries_of(arguments) == ['github account conversion 2026']


@pytest.mark.parametrize('harness', ['claude-code', 'codex'])
@pytest.mark.parametrize('tool', ['perplexity_ask', 'perplexity_research', 'perplexity_reason'])
def test_conversational_search_counts_only_user_requests(tmp_path, harness, tool):
    arguments = {'messages': [
        {'role': 'system', 'content': 'Search carefully'},
        {'role': 'user', 'content': 'What changed in 2026?'},
        {'role': 'assistant', 'content': 'Here are some changes'},
        {'role': 'user', 'content': 'site:example.org release'},
        {'role': 'user', 'content': None}, None,
    ]}
    transcript = (claude_call('1', 'mcp__perplexity__' + tool, arguments, 'answer')
                  if harness == 'claude-code' else [codex_mcp(tool, arguments, 'answer')])
    cell = write_cell(tmp_path, 'a', harness, 'perplexity', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), None)
    assert result['searches'] == 1 and len(result['queries']) == 2
    assert behavior.aggregate([result], False)['natural_language_pct'] == 50.0
    assert behavior.queries_of({'messages': 'invalid'}) == []


@pytest.mark.parametrize('results,expected', [
    (None, None), ([], False), ([{'url': 'https://other.example/'}], False),
    ([{'url': 'https://example.org/source'}], True),
])
def test_native_search_input_is_never_returned_evidence(tmp_path, results, expected):
    item = {'type': 'web_search', 'query': 'https://example.org/source',
            'action': {'type': 'search', 'query': 'https://example.org/source'}}
    if results is not None:
        item['results'] = results
    transcript = [{'harness_event': {'type': 'item.completed', 'item': item}}]
    cell = write_cell(tmp_path, 'a', 'codex', 'native', 't1', transcript, passed=True)
    result = behavior.analyse_cell(Path(cell['run_dir']), 'example.org/source')
    assert result['surfaced'] is expected
    row = behavior.aggregate([result], True)
    assert row['primary_source_unknown_cells'] == int(expected is None)
    assert row['primary_source_observed_cells'] == int(expected is not None)
    assert row['pass_when_not_surfaced'] == ([1, 1] if expected is False else [0, 0])
    assert row['pass_when_surfaced'] == ([1, 1] if expected is True else [0, 0])


@pytest.mark.parametrize('url,expected', [('https://other.example/', None), ('https://example.org/source', True)])
def test_mixed_native_result_visibility_requires_positive_evidence(tmp_path, url, expected):
    transcript = [{'harness_event': {'type': 'item.completed', 'item': {
        'type': 'web_search', 'action': {'type': 'search', 'query': 'source'}, **extra}}}
        for extra in ({}, {'results': [{'url': url}]})]
    cell = write_cell(tmp_path, 'a', 'codex', 'native', 't1', transcript)
    assert behavior.analyse_cell(Path(cell['run_dir']), 'example.org/source')['surfaced'] is expected


def test_analysis_counts_fetch_only_surfacing_and_pass_rates(tmp_path):
    catalog = tmp_path / 'tasks.yaml'
    catalog.write_text(
        'tasks:\n'
        '  - id: t1\n    oracle:\n      source_url: https://example.org/changelog/item/\n'
    )
    run = tmp_path / 'run'
    index = [
        write_cell(run, 'a', 'claude-code', 'exa', 't1',
                   claude_call('1', 'mcp__exa__web_search_exa', {'query': 'example changelog item 2026', 'numResults': 5},
                               'Title: x\nURL: https://example.org/changelog/item?ref=1\n'), passed=True),
        write_cell(run, 'b', 'claude-code', 'exa', 't1',
                   claude_call('2', 'mcp__exa__web_search_exa', {'query': 'what changed in the example service?'},
                               'URL: https://other.example/page\n'), passed=False),
        write_cell(run, 'c', 'claude-code', 'exa', 't1',
                   claude_call('3', 'mcp__exa__web_fetch_exa', {'urls': ['https://example.org/docs']}, 'page text'),
                   passed=False),
        write_cell(run, 'd', 'codex', 'brave', 't1',
                   [codex_mcp('brave_web_search', {'query': 'site:example.org changelog item', 'count': 5},
                              'https://example.org/changelog/item')], passed=True),
    ]
    (run / 'run-index.json').write_text(json.dumps(index))
    result = behavior.analyse([run], catalog)
    rows = {(row['harness'], row['arm']): row for row in result['rows']}
    exa = rows[('claude-code', 'exa')]
    assert exa['cells'] == 3 and exa['search_calls'] == 2 and exa['fetch_calls'] == 1
    assert exa['fetch_only_cells'] == 1 and exa['no_provider_call_cells'] == 0
    assert exa['queries'] == 2 and exa['natural_language_pct'] == 50.0 and exa['year_pct'] == 50.0
    assert exa['structured_parameters'] == {'numResults': 1}
    assert exa['searched_cells'] == 2 and exa['primary_source_surfaced'] == 1
    assert exa['pass_when_surfaced'] == [1, 1] and exa['pass_when_not_surfaced'] == [0, 1]
    assert exa['pass_when_fetch_only'] == [0, 1] and exa['pass_when_searched'] == [1, 2]
    brave = rows[('codex', 'brave')]
    assert brave['site_operator_pct'] == 100.0 and brave['primary_source_surfaced'] == 1
    assert result['totals']['queries'] == 3


def test_cli_writes_json(tmp_path):
    run = tmp_path / 'run'
    index = [write_cell(run, 'a', 'claude-code', 'tavily', 't1',
                        claude_call('1', 'mcp__tavily__tavily_search', {'query': 'tavily docs'}, 'https://x.example'))]
    (run / 'run-index.json').write_text(json.dumps(index))
    out = tmp_path / 'out.json'
    assert behavior.main([str(run), '--json', str(out)]) == 0
    data = json.loads(out.read_text())
    assert data['rows'][0]['arm'] == 'tavily' and 'rules' in data


def test_harness_refusals_are_counted(tmp_path):
    run = tmp_path / 'run'
    refusal = "Claude requested permissions to use WebFetch, but you haven't granted it yet."
    index = [write_cell(run, 'a', 'claude-code', 'native', 't1',
                        claude_call('1', 'WebSearch', {'query': 'x'}, 'https://a.example')
                        + claude_call('2', 'WebFetch', {'url': 'https://a.example'}, refusal))]
    (run / 'run-index.json').write_text(json.dumps(index))
    row = behavior.analyse([run])['rows'][0]
    assert row['fetch_calls'] == 1 and row['refused_calls'] == 1 and row['cells_with_refused_calls'] == 1
