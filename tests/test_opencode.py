"""Offline Opencode contract; never enables the live gate or calls a model.

The sanitized replay uses v1.17.3 run.ts/message-v2.ts event shapes, and
session.ts getUsage's disjoint token buckets. No live cell was used to
produce the fixture. Source: https://github.com/anomalyco/opencode/tree/v1.17.3
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from sew import harnesses, oss
from sew.arms import ArmContract, PROVIDER_SERVER_NAMES, audit_transcript, prepare_arm_spawn, provider_tool_calls
from sew.cost_model import load_price_table, model_cost
from sew.bakeoff_report import _priceable_usage
from sew.doctor import doctor
from sew.harness import HarnessRunConfig, ProviderExposure
from sew.harnesses.opencode import MIN_VERSION, PROVIDER
from sew.harnesses.opencode_protocol import OpencodeProtocol
from sew.live_harness import LiveHarnessRefused, LiveLimits, child_environment, run_live_harness, spawn_and_capture
from sew.host import HostUnavailable
from sew.schema import SchemaError

FIXTURE = Path(__file__).parent / 'fixtures/opencode-1.17.3.jsonl'


@pytest.fixture
def fake_opencode(tmp_path):
    binary = tmp_path / 'fake-opencode'
    binary.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print(os.environ.get('FAKE_VERSION', '{MIN_VERSION}'))
    raise SystemExit(0)
record = {{'argv': sys.argv[1:], 'prompt': sys.stdin.read(),
          'env': dict(os.environ),
          'config': json.loads(Path(os.environ['OPENCODE_CONFIG']).read_text())}}
Path(os.environ['FAKE_RECORD']).write_text(json.dumps(record))
print(Path({str(FIXTURE)!r}).read_text(), end='')
if os.environ.get('FAKE_EXTRA_TRANSCRIPT'):
    print(Path(os.environ['FAKE_EXTRA_TRANSCRIPT']).read_text(), end='')
''')
    binary.chmod(0o755)
    return binary


def environment(tmp_path):
    return {'SEW_MODE': 'standalone', 'HOME': str(tmp_path),
            'SEW_CONFIG': str(tmp_path / 'sew.yaml'), 'SEW_ENV_FILE': str(tmp_path / '.env'),
            'SEW_OSS_ENABLED': '1', 'SEW_LITELLM_BASE_URL': 'http://localhost:4567',
            'SEW_LITELLM_API_KEY': 'fake-proxy-key',
            'ANTHROPIC_AUTH_TOKEN': 'fake-account', 'OPENAI_API_KEY': 'fake-account'}


def config(arm='exa', **kwargs):
    exposure = None
    if arm in PROVIDER_SERVER_NAMES:
        exposure = ProviderExposure(arm, 'search', mcp_server_name=PROVIDER_SERVER_NAMES[arm],
            mcp_server_config={'command': 'stub-mcp', 'args': ['--stdio'], 'env': {'MCP_TEST': 'yes'}})
    return HarnessRunConfig(harness_id='opencode', provider_id=arm, task_id='current-fact-lookup-v1',
        mode='live', model_id='litellm/glm-5.2', native_search_available=arm == 'native',
        external_provider=exposure, **kwargs)


@pytest.mark.parametrize('arm', [*PROVIDER_SERVER_NAMES, 'no-search', 'native'])
def test_config_argv_env_per_arm(tmp_path, fake_opencode, arm):
    source = environment(tmp_path)
    cfg = config(arm)
    surface = prepare_arm_spawn(cfg, tmp_path, source, harness_auth='litellm')
    document = json.loads(surface.mcp_config_path.read_text())
    provider = document['provider'][PROVIDER]
    assert provider['npm'] == '@ai-sdk/openai-compatible'
    assert provider['options'] == {'baseURL': 'http://localhost:4567/v1', 'apiKey': '{env:SEW_LITELLM_API_KEY}'}
    assert list(provider['models']) == ['glm-5.2']
    assert document['tools']['shell'] is False and document['tools']['bash'] is False
    assert document['permission']['*'] == 'deny'
    assert document['share'] == 'disabled' and document['autoupdate'] is False
    if arm in PROVIDER_SERVER_NAMES:
        server = PROVIDER_SERVER_NAMES[arm]
        assert document['mcp'] == {server: {'type': 'local', 'command': ['stub-mcp', '--stdio'],
                                          'environment': {'MCP_TEST': '{env:SEW_OPENCODE_MCP_0}'}, 'enabled': True}}
        assert document['tools'][server + '_*'] is True
        assert surface.env['SEW_OPENCODE_MCP_0'] == 'yes'
    else:
        assert document['mcp'] == {}
    assert document['tools']['webfetch'] is (arm == 'native')
    assert document['tools']['websearch'] is (arm == 'native')
    assert document['permission'].get('websearch') == ('allow' if arm == 'native' else None)
    assert surface.env['OPENCODE_CONFIG'] == str(surface.mcp_config_path)
    for key in ['HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME']:
        assert Path(surface.env[key]).is_relative_to(tmp_path / 'opencode')
    assert surface.env['OPENCODE_DISABLE_PROJECT_CONFIG'] == 'true'
    assert surface.env['OPENCODE_ENABLE_EXA'] == ('true' if arm == 'native' else 'false')
    assert surface.mcp_config_path.stat().st_mode & 0o777 == 0o600
    cell_env = {**surface.env, **oss.litellm_cell_env('opencode', source), 'FAKE_RECORD': str(tmp_path / 'record')}
    cfg = replace(cfg, env=cell_env, harness_args=surface.harness_args)
    protocol = OpencodeProtocol()
    argv = protocol.argv(str(fake_opencode), cfg, tmp_path / 'unused')
    assert argv == [str(fake_opencode), 'run', '--pure', '--format', 'json', '--model', PROVIDER + '/glm-5.2']
    child = child_environment(cfg, source)
    assert 'ANTHROPIC_AUTH_TOKEN' not in child and 'OPENAI_API_KEY' not in child
    assert child['SEW_LITELLM_API_KEY'] == 'fake-proxy-key'
    outcome = spawn_and_capture(argv, prompt='Find the release', cwd=tmp_path,
        env=child, limits=LiveLimits(5, 2, None), protocol=protocol)
    assert outcome.exit_code == 0 and outcome.ready and outcome.first_output_at
    record = json.loads((tmp_path / 'record').read_text())
    assert record['prompt'] == 'Find the release'
    assert record['argv'] == argv[1:] and record['config'] == document
    assert 'fake-proxy-key' not in surface.mcp_config_path.read_text()
    captured = [entry['event'] for entry in outcome.events]
    summary = protocol.summarize(captured, None)
    assert summary.final_text == events()[2]['part']['text']
    assert summary.usage['total_billable'] == 185
    if arm == 'exa':
        assert not audit_transcript(surface.contract, captured).contaminated


@pytest.mark.parametrize('failure', ['home', 'state', 'opencode.json'])
def test_arm_setup_recovers_from_partial_failure(tmp_path, monkeypatch, failure):
    cfg = config()
    source = environment(tmp_path)
    failed_path = tmp_path / 'opencode' / failure
    method = 'write_text' if failure == 'opencode.json' else 'mkdir'
    original = getattr(Path, method)

    def fail_at_path(path, *args, **kwargs):
        if path == failed_path:
            raise OSError('transient setup failure')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, method, fail_at_path)
        with pytest.raises(OSError, match='transient setup failure'):
            prepare_arm_spawn(cfg, tmp_path, source, harness_auth='litellm')
    surface = prepare_arm_spawn(cfg, tmp_path, source, harness_auth='litellm')
    document = surface.mcp_config_path.read_text()
    repeated = prepare_arm_spawn(cfg, tmp_path, source, harness_auth='litellm')
    assert repeated == surface
    assert repeated.mcp_config_path.read_text() == document
    assert surface.mcp_config_path.stat().st_mode & 0o777 == 0o600
    for directory in ['', 'home', 'config', 'data', 'state', 'cache']:
        assert (tmp_path / 'opencode' / directory).stat().st_mode & 0o777 == 0o700


def events():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def test_recorded_transcript_usage_cost_and_audit():
    protocol = OpencodeProtocol()
    replay = events()
    summary = protocol.summarize(replay + [replay[-1]], None)
    assert summary.final_text == 'Release 3.2. https://fixture.example/releases/current'
    assert summary.usage == {'input': 105, 'cached_input': 30, 'output': 40, 'reasoning': 10,
                             'cache_write': 5, 'total_billable': 185}
    seen = {}
    for event in replay + [replay[-1]]:
        protocol.running_usage(event, seen)
    assert sum(seen.values()) == 185
    priced, reason = _priceable_usage(summary.usage)
    assert reason is None
    cost = model_cost('litellm/glm-5.2', priced, load_price_table())
    assert cost['amount_usd'] == pytest.approx((105 * 1.4 + 30 * .26 + 50 * 4.4) / 1e6)
    assert model_cost('litellm/local/qwen3-coder-next-80b-a3b-6bit', priced, load_price_table())['amount_usd'] == 0
    contract = ArmContract('provider', 'opencode', 'exa', 'exa')
    assert audit_transcript(contract, replay).contaminated is False
    assert provider_tool_calls(contract, replay) == [('call_search', 'exa_web_search_exa')]
    forbidden = {'type': 'tool_use', 'part': {'type': 'tool', 'callID': 'forbidden', 'tool': 'webfetch'}}
    audit = audit_transcript(contract, replay + [forbidden])
    assert audit.contaminated and 'webfetch' in audit.violations
    assert audit_transcript(ArmContract('no-search', 'opencode'), replay).contaminated
    assert not audit_transcript(ArmContract('native', 'opencode'), [forbidden]).contaminated
    assert harnesses.get('opencode').native_search


def test_errors_and_missing_usage():
    protocol = OpencodeProtocol()
    assert protocol.summarize(events()[:-1], None).usage is None
    summary = protocol.summarize(events() + [{'type': 'error', 'error': {'data': {'message': '401 unauthorized'}}}], None)
    assert summary.reported_error and summary.final_text is None
    assert '401' in summary.error_messages[0]


def test_disabled_and_non_oss_refused_before_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr('sew.live_harness.spawn_and_capture', lambda *a, **k: pytest.fail('spawned'))
    env = {**environment(tmp_path), 'SEW_OSS_ENABLED': '0'}
    with pytest.raises(LiveHarnessRefused, match='OSS models are disabled'):
        run_live_harness(config(), tmp_path / 'output', environ=env)
    with pytest.raises(HostUnavailable, match='explicit litellm'):
        oss.require_enabled('opencode', 'frontier-model', environment(tmp_path))
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('flag', ['--model=x', '-m', '--agent', '--attach', '--session', '--format', '--dir', '--share', '--dangerously-skip-permissions', '--no-pure'])
def test_arm_overrides_refused(tmp_path, flag):
    with pytest.raises(SchemaError, match='arm-controlled'):
        prepare_arm_spawn(config(harness_args=(flag,)), tmp_path, environment(tmp_path))


def test_config_environment_override_refused(tmp_path):
    with pytest.raises(SchemaError, match='arm-controlled'):
        prepare_arm_spawn(config(env={'OPENCODE_CONFIG_CONTENT': '{}'}), tmp_path, environment(tmp_path))


@pytest.mark.parametrize('version,status', [
    ('1.17.3', 'ok 1.17.3'), ('1.17.2', 'unsupported version'), ('bad', 'version unverified'),
    ('update warning\n1.17.3\n \n', 'ok 1.17.3'),
    ('update warning\n1.17.2', 'unsupported version'), ('\n \n', 'version unverified'),
])
def test_doctor_minimum_version(tmp_path, fake_opencode, version, status):
    env = {**environment(tmp_path), 'SEW_OSS_ENABLED': '0', 'PATH': str(tmp_path), 'SEW_OPENCODE_BIN': str(fake_opencode), 'FAKE_VERSION': version}
    output = doctor(env)
    assert 'opencode: ' + status in output
    assert 'oss models   disabled' in output


def test_doctor_uses_resolved_binary(tmp_path, fake_opencode, monkeypatch):
    calls = []
    original = subprocess.run

    def record_run(argv, *args, **kwargs):
        calls.append(argv)
        return original(argv, *args, **kwargs)

    monkeypatch.setattr('sew.doctor.subprocess.run', record_run)
    env = {**environment(tmp_path), 'SEW_OSS_ENABLED': '0', 'PATH': str(tmp_path),
           'SEW_OPENCODE_BIN': fake_opencode.name}
    assert 'opencode: ok 1.17.3' in doctor(env)
    assert [str(fake_opencode), '--version'] in calls


def test_final_answer_uses_last_message_and_usage_sums_steps():
    replay = events()
    earlier = {'type': 'text', 'part': {'id': 'early', 'messageID': 'msg_early', 'text': 'Searching'}}
    finish = {**replay[-1], 'part': {**replay[-1]['part'], 'id': 'second_step'}}
    summary = OpencodeProtocol().summarize([earlier, *replay, finish], None)
    assert summary.final_text == replay[2]['part']['text']
    assert summary.usage['total_billable'] == 370


def test_native_dry_run_and_nested_route(tmp_path):
    plan = oss.cell_plan('opencode', 'litellm/glm-5.2', 'native', env=environment(tmp_path))
    assert 'harness web tools enabled' in plan
    assert 'opencode/opencode.json' in plan
    cfg = replace(config(), model_id='litellm/fireworks/kimi-k3')
    argv = OpencodeProtocol().argv('fake-opencode', cfg, tmp_path / 'unused')
    assert argv[-1] == PROVIDER + '/fireworks/kimi-k3'


@pytest.mark.parametrize('provider,server', PROVIDER_SERVER_NAMES.items())
def test_audit_provider_names_and_forbidden_native(provider, server):
    call = {'type': 'tool_use', 'part': {'type': 'tool', 'callID': 'own', 'tool': server + '_search'}}
    contract = ArmContract('provider', 'opencode', provider, server)
    assert not audit_transcript(contract, [call]).contaminated
    assert provider_tool_calls(contract, [call]) == [('own', server + '_search')]
    for tool in ('websearch', 'webfetch'):
        forbidden = {'type': 'tool_use', 'part': {'type': 'tool', 'callID': 'native', 'tool': tool}}
        assert audit_transcript(contract, [call, forbidden]).contaminated


def test_incomplete_usage_remains_unknown():
    from sew.harnesses.opencode_protocol import usage_row
    assert usage_row({'output': 10}) is None
    assert usage_row({'input': 1, 'output': '10', 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}) is None


def test_partial_usage_is_not_reported_as_measured():
    replay = events()
    replay.append({'type': 'step_finish', 'part': {'id': 'incomplete', 'tokens': {'output': 10}}})
    assert OpencodeProtocol().summarize(replay, None).usage is None


def test_fake_replay_detects_forbidden_native_call(tmp_path, fake_opencode):
    cfg = config()
    source = environment(tmp_path)
    surface = prepare_arm_spawn(cfg, tmp_path, source, harness_auth='litellm')
    cfg = replace(cfg, env={**surface.env, **oss.litellm_cell_env('opencode', source),
        'FAKE_RECORD': str(tmp_path / 'record'),
        'FAKE_EXTRA_TRANSCRIPT': str(FIXTURE.with_name('opencode-1.17.3-forbidden.jsonl'))})
    protocol = OpencodeProtocol()
    outcome = spawn_and_capture(protocol.argv(str(fake_opencode), cfg, tmp_path / 'unused'),
        prompt='Find the release', cwd=tmp_path, env=child_environment(cfg, source),
        limits=LiveLimits(5, 2, None), protocol=protocol, contract=surface.contract)
    replay = [entry['event'] for entry in outcome.events]
    audit = audit_transcript(surface.contract, replay)
    assert audit.contaminated and audit.violations == ('webfetch',)
    assert provider_tool_calls(surface.contract, replay) == [
        ('call_search', 'exa_web_search_exa'), ('call_forbidden', 'webfetch')]
