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


def opencode_call(call_id, tool, arguments, output, status='completed'):
    state = {'status': status, 'input': arguments}
    if status == 'completed':
        state['output'] = output
    else:
        state['error'] = output
    return {'harness_event': {'type': 'tool_use', 'part': {
        'type': 'tool', 'callID': call_id, 'tool': tool, 'state': state}}}


def hermes_call(call_id, name, arguments, result):
    return [
        {'harness_event': {'type': 'tool_call', 'id': call_id, 'name': name, 'arguments': json.dumps(arguments)}},
        {'harness_event': {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': call_id, 'content': result}]}}},
    ]


def pi_call(call_id, name, arguments, result):
    call = {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': call_id, 'name': name, 'arguments': arguments}]}
    done = {'role': 'toolResult', 'toolCallId': call_id, 'toolName': name, 'content': [{'type': 'text', 'text': result}]}
    # Pi streams each message as start/update/end; only message_end is counted.
    return [{'harness_event': {'type': kind, 'message': message}}
            for message in (call, done) for kind in ('message_start', 'message_update', 'message_end')]


def test_recorded_opencode_transcript_counts_its_provider_search(tmp_path):
    recorded = (ROOT / 'tests' / 'fixtures' / 'opencode-1.17.3.jsonl').read_text().splitlines()
    transcript = [{'role': 'harness', 'harness_event': json.loads(line)} for line in recorded]
    cell = write_cell(tmp_path, 'a', 'opencode', 'exa', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), None)
    assert (result['searches'], result['fetches']) == (1, 0)
    assert [q['words'] for q in result['queries']] == [2]


@pytest.mark.parametrize('harness,transcript', [
    ('opencode', [opencode_call('1', 'exa_web_search_exa', {'query': 'what changed in 2026?', 'numResults': 3},
                                'URL: https://example.org/source'),
                  opencode_call('2', 'exa_web_fetch_exa', {'urls': ['https://example.org/a']}, 'page')]),
    ('hermes', hermes_call('1', 'mcp_exa_web_search_exa', {'query': 'what changed in 2026?', 'numResults': 3},
                           'URL: https://example.org/source')
               + hermes_call('2', 'mcp_exa_web_fetch_exa', {'urls': ['https://example.org/a']}, 'page')),
    ('pi', pi_call('1', 'mcp__exa__web_search_exa', {'query': 'what changed in 2026?', 'numResults': 3},
                   'URL: https://example.org/source')
           + pi_call('2', 'mcp__exa__web_fetch_exa', {'urls': ['https://example.org/a']}, 'page')),
])
def test_oss_harness_provider_calls_are_measured(tmp_path, harness, transcript):
    run = tmp_path / 'run'
    index = [write_cell(run, 'a', harness, 'exa', 't1', transcript, passed=True)]
    (run / 'run-index.json').write_text(json.dumps(index))
    catalog = tmp_path / 'tasks.yaml'
    catalog.write_text('tasks:\n  - id: t1\n    oracle:\n      source_url: https://example.org/source\n')
    row = behavior.analyse([run], catalog)['rows'][0]
    assert (row['harness'], row['arm']) == (harness, 'exa')
    assert (row['search_calls'], row['fetch_calls'], row['queries']) == (1, 1, 1)
    assert row['natural_language_pct'] == 100.0 and row['year_pct'] == 100.0
    assert row['structured_parameters'] == {'numResults': 1}
    assert row['primary_source_surfaced'] == 1 and row['pass_when_surfaced'] == [1, 1]


@pytest.mark.parametrize('name,expected', [
    ('exa_web_search_exa', 'web_search_exa'), ('mcp_exa_web_search_exa', 'web_search_exa'),
    ('mcp__exa__web_search_exa', 'web_search_exa'), ('tavily_tavily_search', 'tavily_search'),
    ('mcp_brave_brave_web_search', 'brave_web_search'), ('brave_llm_context', 'brave_llm_context'),
    ('tavily_search', 'tavily_search'), ('exa_read_file', 'exa_read_file'), (None, ''),
])
def test_tool_names_drop_harness_server_prefixes(name, expected):
    assert behavior.tool_name(name) == expected


def test_opencode_native_tools_and_failed_calls(tmp_path):
    transcript = [
        opencode_call('1', 'websearch', {'query': 'site:example.org release notes'}, 'https://example.org/notes'),
        opencode_call('2', 'webfetch', {'url': 'https://example.org/notes'}, 'page'),
        opencode_call('3', 'websearch', {'query': 'second try'}, 'rate limited', status='error'),
    ]
    cell = write_cell(tmp_path, 'a', 'opencode', 'native', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), 'example.org/other')
    assert (result['searches'], result['fetches']) == (2, 1)
    # The failed search returned nothing observable, and no result held the source.
    assert result['surfaced'] is None


def test_hermes_unparseable_arguments_still_count_the_call(tmp_path):
    transcript = [{'harness_event': {'type': 'tool_call', 'id': '1', 'name': 'mcp_tavily_tavily_search',
                                     'arguments': '{not json'}},
                  {'harness_event': {'type': 'user', 'message': {'content': [
                      {'type': 'tool_result', 'tool_use_id': '1', 'content': 'x'}]}}}]
    cell = write_cell(tmp_path, 'a', 'hermes', 'tavily', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), None)
    assert result['searches'] == 1 and result['queries'] == []


@pytest.mark.parametrize('error', ['rate limited', 'failed to search https://example.org/source'])
def test_failed_pi_search_has_no_returned_evidence(tmp_path, error):
    transcript = pi_call('1', 'mcp__exa__web_search_exa', {'query': 'release notes'}, error)
    for entry in transcript[3:]:
        entry['harness_event']['message']['isError'] = True
    cell = write_cell(tmp_path, 'a', 'pi', 'exa', 't1', transcript, passed=True)
    assert list(behavior.tool_events(transcript)) == [('web_search_exa', {'query': 'release notes'}, None)]
    result = behavior.analyse_cell(Path(cell['run_dir']), 'example.org/source')
    assert result['searches'] == 1 and len(result['queries']) == 1
    assert result['surfaced'] is None
    row = behavior.aggregate([result], True)
    assert row['primary_source_observed_cells'] == 0
    assert row['primary_source_unknown_cells'] == 1
    assert row['pass_when_surfaced'] == row['pass_when_not_surfaced'] == [0, 0]


@pytest.mark.parametrize('harness,call,prefix', [
    ('hermes', hermes_call, 'mcp_exa_'),
    ('pi', pi_call, 'mcp__exa__'),
    ('claude-code', claude_call, 'mcp__exa__'),
])
@pytest.mark.parametrize('returned_url,surfaced', [
    ('https://other.example/', None), ('https://example.org/source', True),
])
def test_interrupted_calls_are_counted_once_without_evidence(tmp_path, harness, call, prefix,
                                                          returned_url, surfaced):
    search = call('pending-search', prefix + 'web_search_exa', {'query': 'release notes'}, 'ignored')
    fetch = call('pending-fetch', prefix + 'web_fetch_exa', {'url': 'https://example.org/source'}, 'ignored')
    # Include Pi's start/update/end streaming events, but no result message.
    call_events = 3 if harness == 'pi' else 1
    transcript = search[:call_events] + fetch[:call_events]
    transcript += call('completed', prefix + 'web_search_exa', {'query': 'second query'}, returned_url)
    events = list(behavior.tool_events(transcript))
    assert len(events) == 3
    assert sum(text is None for _, _, text in events) == 2
    cell = write_cell(tmp_path, 'a', harness, 'exa', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), 'example.org/source')
    assert (result['searches'], result['fetches'], len(result['queries'])) == (2, 1, 2)
    assert result['surfaced'] is surfaced
    row = behavior.aggregate([result], True)
    assert row['no_provider_call_cells'] == 0
    assert row['primary_source_unknown_cells'] == int(surfaced is None)


@pytest.mark.parametrize('harness,call,prefix', [
    ('hermes', hermes_call, 'mcp_exa_'),
    ('pi', pi_call, 'mcp__exa__'),
    ('claude-code', claude_call, 'mcp__exa__'),
])
def test_only_interrupted_search_still_counts(tmp_path, harness, call, prefix):
    transcript = call('1', prefix + 'web_search_exa', {'query': 'release notes'}, 'ignored')
    transcript = transcript[:3 if harness == 'pi' else 1]
    cell = write_cell(tmp_path, 'a', harness, 'exa', 't1', transcript)
    result = behavior.analyse_cell(Path(cell['run_dir']), 'example.org/source')
    assert result['searches'] == 1 and len(result['queries']) == 1
    assert result['surfaced'] is None
