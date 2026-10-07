"""Offline OSS contracts: fake credentials, HTTP diagnostics, and no harness launches."""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sew import broker_auth, host, oss
from sew.arms import prepare_arm_spawn
from sew.cli import main
from sew.cost_model import PriceTableError, load_price_table, model_cost
from sew.harness import HarnessRunConfig
from sew.judge_transport import judge_environment
from sew.live_harness import child_environment, run_live_harness, LiveHarnessRefused
from sew.runner import LiveCellExecutor, RunnerError


def environment(tmp_path, **values):
    return {'SEW_MODE': 'standalone', 'HOME': str(tmp_path), 'SEW_CONFIG': str(tmp_path / 'sew.yaml'), 'SEW_ENV_FILE': str(tmp_path / '.env'), **values}


def config(**values):
    return HarnessRunConfig(harness_id='codex', provider_id='native', task_id='current-fact-lookup-v1', mode='live', model_id='litellm/glm-5.2', **values)


def test_disabled_defaults(tmp_path):
    settings = oss.configuration(environment(tmp_path))
    assert settings.enabled is False
    assert settings.base_url == 'http://127.0.0.1:4000'
    assert settings.api_key_env == 'SEW_LITELLM_API_KEY'
    assert settings.harnesses == ('hermes', 'pi', 'opencode')
    assert set(settings.models) == set(oss.load_catalog())


def test_yaml_and_env_overrides(tmp_path):
    (tmp_path / 'sew.yaml').write_text(yaml.safe_dump({'oss': {'enabled': True, 'litellm': {'base_url': 'http://localhost:5000', 'api_key_env': 'MY_PROXY_KEY'}, 'models': ['glm-5.2'], 'harnesses': ['pi']}}))
    env = environment(tmp_path)
    settings = oss.configuration(env)
    assert settings.enabled and settings.models == ('glm-5.2',)
    assert settings.harnesses == ('pi',)
    assert settings.base_url == 'http://localhost:5000'
    assert not oss.configuration({**env, 'SEW_OSS_ENABLED': '0'}).enabled
    env.update(SEW_OSS_ENABLED='1', SEW_LITELLM_BASE_URL='http://localhost:6000', SEW_LITELLM_API_KEY='fake-override', MY_PROXY_KEY='fake-custom')
    assert oss.configuration(env).base_url == 'http://localhost:6000'
    assert host.StandaloneHost(env).litellm_credentials('codex') == {'api_key': 'fake-override'}
    env.pop('SEW_LITELLM_API_KEY')
    assert host.StandaloneHost(env).litellm_credentials('codex') == {'api_key': 'fake-custom'}


@pytest.mark.parametrize('subject', ['litellm/glm-5.2', 'pi', 'hermes', 'opencode'])
def test_disabled_preflight_before_spawn(tmp_path, monkeypatch, subject):
    monkeypatch.setattr('sew.live_harness.spawn_and_capture', lambda *a, **k: pytest.fail('spawned'))
    is_model = subject.startswith('litellm/')
    cell = SimpleNamespace(applicable=True, harness_id='codex' if is_model else subject, model_profile=subject if is_model else 'default')
    with pytest.raises(RunnerError, match='OSS models are disabled'):
        LiveCellExecutor(environ=environment(tmp_path)).preflight([cell], mode='live')
    if is_model:
        with pytest.raises(LiveHarnessRefused, match='SEW_OSS_ENABLED=1'):
            run_live_harness(config(), tmp_path / 'output', environ=environment(tmp_path))
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('harness', ['codex', 'claude-code', 'pi', 'hermes', 'opencode'])
def test_cli_dry_run_never_spawns_or_reads_keys(tmp_path, monkeypatch, capsys, harness):
    for key, value in environment(tmp_path, SEW_OSS_ENABLED='1').items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(host.StandaloneHost, 'litellm_credentials', lambda *a: pytest.fail('read key'))
    monkeypatch.setattr('sew.cli.run_harness', lambda *a, **k: pytest.fail('spawned'))
    assert main(['run-live-harness', '--harness', harness, '--model', 'litellm/glm-5.2', '--arm', 'exa', '--dry-run']) == 0
    output = capsys.readouterr().out
    for text in ['cell plan (no model call)', harness, 'http://127.0.0.1:4000', 'litellm key', 'SEW_LITELLM_API_KEY', 'web_search_exa', 'native', 'config', 'judges']:
        assert text in output


def test_proxy_auth_does_not_touch_host_account_or_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_auth, '_broker_credentials', lambda *a: pytest.fail('read OAuth'))
    monkeypatch.setattr(host.StandaloneHost, 'harness_auth', lambda *a: pytest.fail('read account'))
    env = environment(tmp_path, SEW_OSS_ENABLED='1', SEW_LITELLM_API_KEY='fake-proxy', ANTHROPIC_AUTH_TOKEN='fake-oauth', ANTHROPIC_API_KEY='fake-account', OPENAI_API_KEY='fake-account')
    for requested in (None, 'account', 'broker', 'litellm'):
        assert broker_auth.auth_source(requested, env, model_id='litellm/glm-5.2') == 'litellm'
    home = tmp_path / 'account'
    home.mkdir()
    (home / 'auth.json').write_text('{"fake":"account"}')
    env['CODEX_HOME'] = str(home)
    scratch = tmp_path / 'cell'
    scratch.mkdir()
    surface = prepare_arm_spawn(config(), scratch, env, harness_auth='litellm')
    assert not (Path(surface.env['CODEX_HOME']) / 'auth.json').exists()
    child = child_environment(replace(config(), env={**surface.env, **oss.litellm_cell_env('codex', env)}), env)
    assert child['SEW_LITELLM_API_KEY'] == 'fake-proxy'
    assert not any(k in child for k in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'))
    assert env['ANTHROPIC_AUTH_TOKEN'] == 'fake-oauth'


def test_host_seam_delegates_only_proxy_credentials(tmp_path):
    calls = []
    plugin = SimpleNamespace(litellm_credentials=lambda harness: calls.append(harness) or {'api_key': 'fake-scoped'})
    assert host.AgentOsHost(plugin, {}).litellm_credentials('pi') == {'api_key': 'fake-scoped'}
    assert calls == ['pi']
    with pytest.raises(host.HostUnavailable, match='unavailable'):
        host.AgentOsHost(SimpleNamespace(), {}).litellm_credentials('pi')
    with pytest.raises(host.HostUnavailable, match='not provisioned'):
        host.StandaloneHost(environment(tmp_path)).litellm_credentials('pi')


def test_judge_env_has_no_proxy_settings(tmp_path):
    env = environment(tmp_path, SEW_OSS_ENABLED='1', SEW_LITELLM_API_KEY='fake-proxy', SEW_LITELLM_BASE_URL='http://localhost:6000', OPENAI_BASE_URL='http://localhost:6000/v1', ANTHROPIC_BASE_URL='http://localhost:6000', OPENAI_API_KEY='fake-judge')
    cell_env = oss.litellm_cell_env('codex', env)
    assert cell_env['SEW_LITELLM_API_KEY'] == 'fake-proxy'
    judge = HarnessRunConfig(harness_id='codex', provider_id='native', task_id='judge', mode='live')
    actual = judge_environment(judge, env)
    assert actual['OPENAI_API_KEY'] == 'fake-judge'
    assert not any(k in actual for k in ('SEW_LITELLM_API_KEY', 'SEW_LITELLM_BASE_URL', 'OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL'))


@pytest.mark.parametrize('field,value', [('as_of', None), ('as_of', 'not-a-date'), ('source', None), ('context_window_tokens', 0), ('max_output_tokens', False), ('input_usd_per_mtok', float('nan')), ('rate_basis', 'unknown')])
def test_catalog_requires_valid_receipts_and_limits(field, value):
    document = {'version': 1, 'models': copy.deepcopy(oss.load_catalog())}
    document['models']['glm-5.2'][field] = value
    with pytest.raises(PriceTableError):
        oss.parse_catalog(document)


def test_self_hosted_must_be_zero():
    document = {'version': 1, 'models': copy.deepcopy(oss.load_catalog())}
    document['models']['glm-5.2']['rate_basis'] = 'self-hosted'
    with pytest.raises(PriceTableError, match='self-hosted'):
        oss.parse_catalog(document)
    local = oss.load_catalog()['local/qwen3-coder-next-80b-a3b-6bit']
    assert local['rate_basis'] == 'self-hosted' and local['input_usd_per_mtok'] == 0


def test_catalog_costs_and_unknown():
    usage = {'input': 1_000_000, 'output': 1_000_000, 'cached_input': 1_000_000, 'cache_write': 0, 'accounting_source': 'measured'}
    table = load_price_table()
    result = model_cost('litellm/glm-5.2', usage, table)
    assert result['amount_usd'] == pytest.approx(6.06)
    assert result['price_as_of'] == '2026-10-06'
    assert result['assumed_rate']['rate_basis'] == 'list'
    assert model_cost('litellm/local/qwen3-coder-next-80b-a3b-6bit', usage, table)['amount_usd'] == 0
    assert model_cost('litellm/unknown', usage, table)['reason'].endswith('no_published_rate')


def test_doctor_only_probes_when_enabled(tmp_path, monkeypatch):
    from sew.doctor import oss_lines
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': route} for route in oss.load_catalog()]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = environment(tmp_path, SEW_LITELLM_BASE_URL=f'http://127.0.0.1:{server.server_port}', SEW_LITELLM_API_KEY='fake-proxy')
        assert 'disabled' in '\n'.join(oss_lines(env, host.StandaloneHost(env)))
        assert calls == []
        env['SEW_OSS_ENABLED'] = '1'
        output = '\n'.join(oss_lines(env, host.StandaloneHost(env)))
        assert '/health/readiness 200' in output and '7/7 listed' in output
        assert '7 routes; 7 priced (1 self-hosted at $0)' in output
        assert 'fake-proxy' not in output
        assert calls == [('/health/readiness', 'Bearer fake-proxy'), ('/v1/models', 'Bearer fake-proxy')]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize('route', list(oss.load_catalog()))
def test_model_validators_accept_every_route(tmp_path, route, monkeypatch, capsys):
    from sew.gap.calibrate import calibration_path
    model = 'litellm/' + route
    assert oss.valid_model_id(model)
    outside = Path('/tmp') / ('sew-oss-' + tmp_path.name)
    path = calibration_path(outside, 'codex', model)
    assert path.parent == outside.resolve() / 'gap/calibration'
    assert '%2F' in path.name
    for key, value in environment(tmp_path, SEW_OSS_ENABLED='1').items():
        monkeypatch.setenv(key, value)
    assert main(['run-live-harness', '--harness', 'codex', '--model-id', model, '--dry-run']) == 0


@pytest.mark.parametrize('model', ['litellm/../escape', 'litellm//glm', 'litellm/a/../../b', 'litellm/', 'litellm/a\\b'])
def test_model_validation_blocks_paths(model):
    assert not oss.valid_model_id(model)


def test_proxy_auth_strips_explicit_account_keys_and_isolates_claude(tmp_path):
    env = environment(tmp_path, SEW_OSS_ENABLED='1', SEW_LITELLM_API_KEY='fake-proxy')
    proxy = replace(config(), harness_id='claude-code', env={'ANTHROPIC_API_KEY': 'fake-account', 'OPENAI_API_KEY': 'fake-account'})
    scratch = tmp_path / 'cell'
    scratch.mkdir()
    surface = prepare_arm_spawn(proxy, scratch, env, harness_auth='litellm')
    assert Path(surface.env['CLAUDE_CONFIG_DIR']).parent == scratch
    child = child_environment(replace(proxy, env={**proxy.env, **surface.env, **oss.litellm_cell_env('claude-code', env)}), env)
    assert 'ANTHROPIC_API_KEY' not in child and 'OPENAI_API_KEY' not in child


def test_suite_oss_profile_materializes_model(tmp_path):
    cell = SimpleNamespace(harness_id='codex', provider_id='native', task_id='current-fact-lookup-v1', model_profile='litellm/glm-5.2', suite_id='lighthouse', run_id='oss-test')
    result = LiveCellExecutor(environ=environment(tmp_path, SEW_OSS_ENABLED='1'))._harness_config(cell)
    assert result.model_id == 'litellm/glm-5.2'
    assert result.env == {}


def test_judges_use_own_auth_when_cell_selects_proxy():
    from sew.judge_transport import HarnessJudgeTransport
    from sew.gap.judges import default_brief_judges
    assert HarnessJudgeTransport('codex', harness_auth='litellm').harness_auth is None
    assert all(j.transport.harness_auth is None for j in default_brief_judges('litellm'))


def test_blinding_oss_vocabulary():
    from sew.judge import build_judge_payload, find_identity_leaks
    text = 'hermes opencode pi litellm glm kimi qwen deepseek'
    rubric = {'rubric_id': 'quality', 'dimensions': ['quality'], 'score_scale': {'minimum': 0, 'maximum': 4}, 'minimum_score': 2}
    payload, report = build_judge_payload({'prompt': text}, rubric, {'answer': text})
    assert find_identity_leaks(payload) == []
    assert report['redacted_spans'] > 0


def test_example_routes_match_catalog():
    from sew.catalog import module_root
    example = yaml.safe_load((module_root() / 'config/litellm-example.yaml').read_text())
    assert {row['model_name'] for row in example['model_list']} == set(oss.load_catalog())
    assert all(row['litellm_params']['api_key'].startswith('os.environ/') for row in example['model_list'])


def test_proxy_host_reads_only_its_named_key(tmp_path):
    class GuardedEnvironment(dict):
        def __getitem__(self, key):
            if key in {'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'}:
                pytest.fail('read account credential')
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in {'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'}:
                pytest.fail('read account credential')
            return super().get(key, default)

    env = GuardedEnvironment(environment(tmp_path, SEW_LITELLM_API_KEY='fake-proxy', OPENAI_API_KEY='fake-account'))
    assert host.StandaloneHost(env).litellm_credentials('codex')['api_key'] == 'fake-proxy'


def test_oss_profile_preflight_and_missing_key(tmp_path, monkeypatch):
    cell = SimpleNamespace(applicable=True, harness_id='codex', provider_id='native', task_id='current-fact-lookup-v1', model_profile='litellm/glm-5.2', suite_id='lighthouse', run_id='oss-test')
    monkeypatch.setattr('sew.runner.live_enabled', lambda env: True)
    monkeypatch.setattr('sew.runner.shutil.which', lambda *a, **kw: '/fake/codex')
    plans = []
    monkeypatch.setattr('sew.runner.prepare_arm_spawn', lambda config, *a, **kw: plans.append((config.model_id, kw['harness_auth'])))
    env = environment(tmp_path, SEW_OSS_ENABLED='1')
    with pytest.raises(RunnerError, match='LiteLLM key not provisioned'):
        LiveCellExecutor(environ=env).preflight([cell], mode='live')
    assert plans == []
    env['SEW_LITELLM_API_KEY'] = 'fake-proxy'
    LiveCellExecutor(environ=env).preflight([cell], mode='live')
    assert plans == [('litellm/glm-5.2', 'litellm')]


def test_full_doctor_oss_output(tmp_path, monkeypatch):
    from sew.doctor import doctor
    monkeypatch.setattr('sew.gap.sandbox.qualify_backend', lambda *a, **kw: {})
    monkeypatch.setattr('sew.gap.sandbox.select_backend', lambda *a, **kw: SimpleNamespace(name='fake'))
    env = environment(tmp_path, PATH='')
    assert 'oss models   disabled' in doctor(env)
    # The request is stubbed, so even the configured default endpoint is never called.
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, *args):
            return json.dumps({'data': [{'id': route} for route in oss.load_catalog()]}).encode()
    monkeypatch.setattr('urllib.request.OpenerDirector.open', lambda *a, **kw: Response())
    env.update(SEW_OSS_ENABLED='1', SEW_LITELLM_API_KEY='fake-proxy')
    output = doctor(env)
    assert 'oss models   enabled (SEW_OSS_ENABLED)' in output and '7/7 listed' in output
    assert 'fake-proxy' not in output
