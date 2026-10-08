"""Offline Pi protocol, fake runtime and real stdio bridge round trips."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from dataclasses import replace

import pytest

from sew.arms import prepare_arm_spawn, audit_transcript, contract_for
from sew.harness import HarnessRunConfig, ProviderExposure
from sew.pi_live import PiProtocol, version_status
from sew import live_harness
from sew.cost_model import model_cost, load_price_table
from sew.runner import LiveCellExecutor
from sew.schema import SchemaError

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ROOT / 'lib/python/sew/harnesses/pi_extensions'


@pytest.fixture
def fake_pi(tmp_path):
    path = tmp_path / 'pi'
    path.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo 0.79.8; exit 0; fi\ncat >/dev/null\ncat <<\'JSON\'\n' +
                    (ROOT / 'tests/fixtures/pi-session.jsonl').read_text() + '\nJSON\n')
    path.chmod(0o755)
    return path


def config(arm='exa'):
    return HarnessRunConfig(harness_id='pi', provider_id=arm, task_id='pi-offline-test',
                            mode='live', model_id='litellm/glm-5.2',
                            native_search_available=False, prompt_text='offline prompt',
                            external_provider=ProviderExposure(
                                provider_id=arm, tool_name='web_search_exa',
                                mcp_server_config={'command': 'stub-mcp', 'args': [], 'env': {}})
                            if arm != 'no-search' else None)


def environment(fake_pi):
    return {'PATH': os.environ['PATH'], 'SEW_OSS_ENABLED': '1', 'SEW_MODE': 'standalone',
            'SEW_PI_BIN': str(fake_pi), 'SEW_LITELLM_API_KEY': 'offline-placeholder'}


@pytest.mark.parametrize('arm', ['exa', 'parallel-web', 'firecrawl', 'brave', 'tavily', 'perplexity', 'no-search'])
def test_isolated_surface(fake_pi, tmp_path, arm):
    c = config(arm)
    surface = prepare_arm_spawn(c, tmp_path, environment(fake_pi), harness_auth='litellm')
    cell = json.loads(surface.mcp_config_path.read_text())
    assert cell['server'] == ({'command': 'stub-mcp', 'args': []} if c.external_provider else None)
    assert cell['baseUrl'].endswith('/v1')
    assert cell['contextWindow'] > cell['maxTokens'] > 0
    assert 'offline-placeholder' not in surface.mcp_config_path.read_text()
    assert surface.env['PI_CODING_AGENT_DIR'] == str(tmp_path / 'pi-agent')
    argv = PiProtocol().argv(str(fake_pi), replace(c, harness_args=surface.harness_args), tmp_path / 'unused')
    assert argv[1:8] == ['--provider', 'searchlight-litellm', '--model', 'glm-5.2', '-p', '--mode', 'json']
    assert '--no-builtin-tools' in argv and '--no-extensions' in argv
    child = live_harness.child_environment(replace(c, env=surface.env), {
        'OPENAI_API_KEY': 'account-placeholder', 'ANTHROPIC_AUTH_TOKEN': 'account-placeholder'})
    assert 'OPENAI_API_KEY' not in child and 'ANTHROPIC_AUTH_TOKEN' not in child


def test_version_and_model_refused(fake_pi, tmp_path):
    assert version_status(str(fake_pi), environment(fake_pi)).startswith('ok')
    fake_pi.write_text('#!/bin/sh\necho 0.79.7\n')
    with pytest.raises(SchemaError, match='version'):
        prepare_arm_spawn(config(), tmp_path, environment(fake_pi), harness_auth='litellm')
    with pytest.raises(SchemaError, match='explicit'):
        prepare_arm_spawn(replace(config(), model_id=None), tmp_path, {}, harness_auth='account')


def test_recorded_session_usage_cost_and_audit():
    events = [json.loads(line) for line in (ROOT / 'tests/fixtures/pi-session.jsonl').read_text().splitlines()]
    protocol = PiProtocol()
    summary = protocol.summarize(events, None)
    assert summary.final_text == 'Recorded answer.'
    assert summary.usage['input'] == 120
    assert summary.usage['cached_input'] == 10
    assert summary.usage['output'] == 30
    seen = {}
    for event in events:
        protocol.running_usage(event, seen)
    assert sum(seen.values()) == 160
    contract = contract_for(config())
    assert not audit_transcript(contract, events).contaminated
    leaked = {'type': 'message_end', 'message': {'content': [{'type': 'toolCall', 'id': 'leak', 'name': 'web_search'}]}}
    assert audit_transcript(contract, events + [leaked]).contaminated
    cost = model_cost('litellm/glm-5.2', {**summary.usage, 'accounting_source': 'measured'}, load_price_table())
    assert cost['amount_usd'] > 0


def test_fake_cell_writes_real_bundle(fake_pi, tmp_path, monkeypatch):
    # Patch the gate only inside this offline test; never set the live env flag.
    monkeypatch.setattr(live_harness, 'live_enabled', lambda env: True)
    result = live_harness.run_live_harness(config(), tmp_path / 'runs', environ=environment(fake_pi))
    assert result.status == 'succeeded'
    run = json.loads((result.bundle_dir / 'run.json').read_text())
    assert run['mode'] == 'live'
    assert 'offline-placeholder' not in ''.join(p.read_text() for p in result.bundle_dir.rglob('*') if p.is_file())


def test_live_executor_selects_pi(monkeypatch, tmp_path):
    from sew import runner
    from types import SimpleNamespace
    executor = LiveCellExecutor(environ={'SEW_OSS_ENABLED': '1'})
    monkeypatch.setattr(executor, '_task', lambda _: {'budgets': {'wall_clock_seconds': 10, 'max_total_tokens': 1000, 'max_provider_calls': 2}})
    monkeypatch.setattr(executor, '_harness_config', lambda *a, **k: config())
    monkeypatch.setattr(runner, 'adopt_completed_bundle', lambda *a: None)
    monkeypatch.setattr(runner, 'run_live_harness', lambda *a, **k: SimpleNamespace(status='succeeded', run_id='test', bundle_dir=tmp_path, failure_category=None))
    cell = SimpleNamespace(harness_id='pi', model_profile='litellm/glm-5.2', provider_id='exa', run_id='test', task_id='test')
    assert executor.execute(cell, tmp_path, mode='live').status == 'succeeded'


def test_extensions_register_provider_and_bridge_round_trip(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Pi extension round trip requires Node.js')
    stub = tmp_path / 'stub.mjs'
    stub.write_text('''import {createInterface} from 'node:readline';
createInterface({input:process.stdin}).on('line', line => {
 if (process.env.SEW_LITELLM_API_KEY || process.env.PROVIDER_KEY !== 'provider-placeholder') process.exit(3);
 const req = JSON.parse(line); if (!req.id) return;
 const result = req.method === 'initialize' ? {protocolVersion:'2024-11-05',capabilities:{}} :
 req.method === 'tools/list' ? {tools:[{name:req.params.cursor ? 'fetch' : 'search',description:'stub search',inputSchema:{type:'object',properties:{q:{type:'string'}}}}],...(req.params.cursor ? {} : {nextCursor:'page2'})} :
 {content:[{type:'text',text:req.params.arguments.q}]};
 process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,result})+'\\n');
});''')
    cell = tmp_path / 'cell.json'
    cell.write_text(json.dumps({'baseUrl': 'http://unused.invalid/v1', 'route': 'glm-5.2',
                               'contextWindow': 10000, 'maxTokens': 1000, 'serverName': 'exa',
                               'server': {'command': node, 'args': [str(stub)]}}))
    script = tmp_path / 'fake-pi.mjs'
    script.write_text('''import assert from 'node:assert/strict';
const handlers = {}, tools = [], providers = [];
const pi = {on:(name,fn)=>handlers[name]=fn,registerTool:t=>tools.push(t),
 registerProvider:(id,p)=>providers.push({id,...p}),setActiveTools:n=>pi.active=n};
const provider = await import(process.argv[2]);
const bridge = await import(process.argv[3]);
provider.default(pi); bridge.default(pi);
assert.equal(providers[0].api,'openai-completions');
assert.equal(providers[0].models[0].contextWindow,10000);
assert.equal(providers[0].apiKey,'offline-placeholder');
await handlers.session_start();
assert.deepEqual(pi.active,['mcp__exa__search','mcp__exa__fetch']);
assert.equal(tools.length,2);
const result = await tools[0].execute('call',{q:'round trip'});
assert.equal(result.content[0].text,'round trip');
handlers.session_shutdown();
''')
    env = {'PATH': os.environ['PATH'], 'SEW_PI_CELL_CONFIG': str(cell), 'SEW_LITELLM_API_KEY': 'offline-placeholder',
           'SEW_PI_MCP_ENV_SECRET': json.dumps({'PROVIDER_KEY': 'provider-placeholder'})}
    result = subprocess.run([node, str(script), (EXTENSIONS / 'litellm.mjs').as_uri(),
                             (EXTENSIONS / 'mcp-bridge.mjs').as_uri()], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_disabled_and_native_cells(fake_pi, tmp_path, monkeypatch):
    with pytest.raises(live_harness.LiveHarnessRefused, match="disabled"):
        live_harness.run_live_harness(config(), tmp_path, environ={})
    monkeypatch.setattr(live_harness, 'live_enabled', lambda env: True)
    native = replace(config(), provider_id='native', external_provider=None,
                     native_search_available=True)
    result = live_harness.run_live_harness(native, tmp_path, environ=environment(fake_pi))
    assert result.status == 'unsupported'
    assert result.failure_category == 'native_search_unsupported'


def test_error_and_unknown_usage():
    summary = PiProtocol().summarize([{'type': 'message_end', 'message': {
        'role': 'assistant', 'content': [], 'stopReason': 'error', 'errorMessage': 'connection refused'}}], None)
    assert summary.reported_error
    assert summary.usage is None
    assert summary.error_messages == ('connection refused',)


def test_extra_args_cannot_widen_tools(fake_pi, tmp_path):
    with pytest.raises(SchemaError, match='arguments'):
        prepare_arm_spawn(replace(config(), harness_args=('unreviewed prompt',)), tmp_path,
                          environment(fake_pi), harness_auth='litellm')


def test_stdio_only_and_provider_key_stays_off_disk(fake_pi, tmp_path):
    c = config()
    exposure = replace(c.external_provider, mcp_server_config={'url': 'http://unused.invalid'})
    with pytest.raises(SchemaError, match='stdio'):
        prepare_arm_spawn(replace(c, external_provider=exposure), tmp_path,
                          environment(fake_pi), harness_auth='litellm')
    exposure = replace(c.external_provider, mcp_server_config={
        'command': 'stub-mcp', 'env': {'PROVIDER_KEY': 'provider-placeholder'}})
    surface = prepare_arm_spawn(replace(c, external_provider=exposure), tmp_path,
                                environment(fake_pi), harness_auth='litellm')
    assert 'provider-placeholder' not in surface.mcp_config_path.read_text()
    assert json.loads(surface.env['SEW_PI_MCP_ENV_SECRET']) == {'PROVIDER_KEY': 'provider-placeholder'}


def test_no_search_extension_exposes_no_tools(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Pi extension test requires Node.js')
    cell = tmp_path / 'cell.json'
    cell.write_text('{"server": null}')
    script = """import assert from 'node:assert/strict';
const bridge = await import(process.argv[1]);
const handlers = {};
const pi = {on:(k,v)=>handlers[k]=v,registerTool:()=>assert.fail('unexpected tool'),
 setActiveTools:n=>assert.deepEqual(n,[])};
bridge.default(pi);
await handlers.session_start();
handlers.session_shutdown();
"""
    result = subprocess.run([node, '--input-type=module', '-e', script,
                             (EXTENSIONS / 'mcp-bridge.mjs').as_uri()],
                            env={'SEW_PI_CELL_CONFIG': str(cell)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
