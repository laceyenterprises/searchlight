"""Offline Hermes configuration and replay of its 0.16.0 session schema."""
from dataclasses import replace

import pytest
import yaml

from sew.arms import prepare_arm_spawn, audit_transcript, PROVIDER_SERVER_NAMES
from sew.harness import HarnessRunConfig, ProviderExposure
from sew.harnesses.hermes_protocol import HermesProtocol
from sew.live_harness import child_environment, spawn_and_capture, LiveLimits
from sew.oss import require_enabled
from sew.host import HostUnavailable
from sew.cost_model import load_price_table


def config(arm):
    exposure = ProviderExposure(provider_id=arm, tool_name='search', mcp_server_name=PROVIDER_SERVER_NAMES[arm],
                                mcp_server_config={'command': 'fake-mcp', 'args': ['--stdio']}) if arm in PROVIDER_SERVER_NAMES else None
    return HarnessRunConfig(harness_id='hermes', provider_id=arm, task_id='fake', model_id='litellm/glm-5.2',
                            native_search_available=False, external_provider=exposure, prompt_text='single prompt')


@pytest.mark.parametrize('arm', [*PROVIDER_SERVER_NAMES, 'no-search'])
def test_isolated_config_and_argv(tmp_path, arm):
    c = config(arm)
    surface = prepare_arm_spawn(c, tmp_path, {'SEW_OSS_ENABLED': '1'}, harness_auth='litellm')
    doc = yaml.safe_load(surface.mcp_config_path.read_text())
    assert doc['custom_providers'][0]['key_env'] == 'SEW_LITELLM_API_KEY'
    assert doc['custom_providers'][0]['base_url'] == 'http://127.0.0.1:4000/v1'
    assert doc['model']['default'] == 'glm-5.2'
    assert {'web', 'terminal', 'browser', 'code_execution', 'delegation'} <= set(doc['agent']['disabled_toolsets'])
    assert 'api_key' not in doc['custom_providers'][0]
    assert doc['platform_toolsets']['cli'] == ([f'mcp-{PROVIDER_SERVER_NAMES[arm]}'] if arm != 'no-search' else [])
    assert set(doc['mcp_servers']) == set(surface.contract.allowed_mcp_servers)
    c = replace(c, harness_args=surface.harness_args, env={**surface.env, 'SEW_LITELLM_API_KEY': 'test-proxy-key'})
    argv = HermesProtocol().argv('hermes', c, tmp_path / 'last')
    assert argv == ['hermes', '-z', 'single prompt', '-m', 'glm-5.2', '--provider', 'custom:searchlight', '--ignore-rules'] + (['-t', PROVIDER_SERVER_NAMES[arm]] if arm != 'no-search' else [])
    env = child_environment(c, {'OPENAI_API_KEY': 'account', 'ANTHROPIC_AUTH_TOKEN': 'account'})
    assert env['SEW_LITELLM_API_KEY'] == 'test-proxy-key'
    assert 'OPENAI_API_KEY' not in env and 'ANTHROPIC_AUTH_TOKEN' not in env


def test_refused_and_override(tmp_path):
    with pytest.raises(HostUnavailable, match='disabled'):
        require_enabled('hermes', 'litellm/glm-5.2', {})
    from sew.schema import SchemaError
    with pytest.raises(SchemaError):
        prepare_arm_spawn(replace(config('exa'), harness_args=('-t', 'all')), tmp_path, {'SEW_OSS_ENABLED': '1'})
    with pytest.raises(SchemaError, match='explicit'):
        prepare_arm_spawn(replace(config('exa'), model_id='glm-5.2'), tmp_path, {'SEW_OSS_ENABLED': '1'})


def test_fake_binary_session_capture(tmp_path, monkeypatch):
    surface = prepare_arm_spawn(config('exa'), tmp_path, {'SEW_OSS_ENABLED': '1'})
    binary = tmp_path / 'hermes'
    binary.write_text('''#!/usr/bin/env python3
import os, sqlite3, json
from pathlib import Path
assert '-z' in __import__('sys').argv
p = Path(os.environ['HERMES_HOME']) / 'state.db'
with sqlite3.connect(p) as db:
 db.execute('CREATE TABLE sessions(id TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, reasoning_tokens INTEGER)')
 db.execute("INSERT INTO sessions VALUES ('session',100,20,10,0)")
 db.execute('CREATE TABLE messages(id INTEGER, role TEXT, content TEXT, tool_calls TEXT, tool_call_id TEXT)')
 db.execute('INSERT INTO messages VALUES (1,?,?,?,NULL)', ('assistant',None,json.dumps([{'id':'call','function':{'name':'mcp_exa_web_search_exa','arguments':'{}'}}])))
 db.execute('INSERT INTO messages VALUES (3,?,?,?,NULL)', ('assistant','answer',None))
 db.execute('INSERT INTO messages VALUES (2,?,?,?,?)', ('tool','search result',None,'call'))
print('answer')
''')
    binary.chmod(0o755)
    protocol = HermesProtocol()
    c = replace(config('exa'), env=dict(surface.env), harness_args=surface.harness_args)
    import os
    outcome = spawn_and_capture(protocol.argv(str(binary), c, tmp_path/'last'), prompt='single prompt', cwd=tmp_path,
                                env={**os.environ, **surface.env}, limits=LiveLimits(5, 5, None, None), protocol=protocol,
                                contract=surface.contract)
    assert outcome.exit_code == 0 and outcome.ready
    events = [e['event'] for e in outcome.events]
    summary = protocol.summarize(events, None)
    assert summary.final_text == 'answer'
    assert summary.usage == {'input':100, 'output':20, 'cached_input':10, 'reasoning':0}
    audit = audit_transcript(surface.contract, events)
    assert not audit.contaminated and 'mcp_exa_web_search_exa' in audit.observed_tool_calls
    assert audit_transcript(surface.contract, events + [{'type':'tool_call','name':'web_search'}]).contaminated
    assert any(e.get('message', {}).get('content', [{}])[0].get('tool_use_id') == 'call' for e in events)
    assert protocol.poll_events(surface.env, set())
    from sew import live_harness
    monkeypatch.setattr(live_harness, 'live_enabled', lambda env: True)
    # The replay binary does not connect to an MCP server.
    monkeypatch.setattr(live_harness, '_metered', lambda config, **kwargs: config)
    result = live_harness.run_live_harness(
        replace(config('exa'), mode='live', binary=str(binary)), tmp_path / 'runs',
        environ={'PATH': os.environ['PATH'], 'SEW_HOST': 'standalone',
                 'SEW_OSS_ENABLED': '1', 'SEW_LITELLM_API_KEY': 'test-proxy-key'})
    assert result.status == 'succeeded'


def test_usage_catalog_cost():
    from sew.cost_model import model_cost
    usage = {'input': 100, 'output': 20, 'cached_input': 10, 'accounting_source': 'harness'}
    cost = model_cost('litellm/glm-5.2', usage, load_price_table())
    assert cost['amount_usd'] == pytest.approx(0.0002306)


def test_doctor_minimum_version(tmp_path):
    from sew.doctor import doctor
    binary = tmp_path / 'hermes'
    binary.write_text('#!/bin/sh\necho "Hermes Agent v0.15.0"\n')
    binary.chmod(0o755)
    text = doctor({'PATH': str(tmp_path), 'SEW_HOST': 'standalone', 'SEW_STATE_ROOT': str(tmp_path / 'state')})
    assert 'hermes: unsupported version; requires >= 0.16.0' in text


def test_native_arm_is_unavailable(tmp_path, monkeypatch):
    from sew import harnesses
    assert harnesses.get('hermes').native_search is False
    from sew import live_harness
    monkeypatch.setattr(live_harness, 'live_enabled', lambda env: True)
    monkeypatch.setattr(live_harness, 'spawn_and_capture', lambda *a, **kw: pytest.fail('native must not spawn'))
    result = live_harness.run_live_harness(
        replace(config('no-search'), provider_id='native', native_search_available=True, mode='live'),
        tmp_path / 'native', environ={'SEW_OSS_ENABLED': '1'})
    assert result.status == 'unsupported'


def test_session_usage_snapshots_replace_prior_counts():
    protocol = HermesProtocol()
    seen = {}
    for count in (10, 20, 20):
        protocol.running_usage({'type': 'hermes.usage', 'usage': {
            'id': 'one', 'input_tokens': count, 'output_tokens': 5, 'cache_read_tokens': 2}}, seen)
    assert seen == {'one': 27}
    assert protocol.summarize([{'type': 'hermes.error', 'message': 'Invalid Hermes session ledger'},
                               {'type': 'hermes.assistant', 'text': 'answer'}], None).reported_error
