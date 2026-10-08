"""Isolated Pi invocation and JSON event protocol (Pi >= 0.79.8)."""
from collections.abc import Mapping
import json
import re
import shutil
import subprocess
from pathlib import Path

from .arms import SpawnSurface
from .live_harness import HarnessProtocol, HarnessSummary
from .oss import load_catalog, configuration
from .schema import SchemaError
from .harnesses.pi import MIN_VERSION


def version_status(binary, env):
    # A failed probe is an operational error, not an unsupported configuration.
    result = subprocess.run([binary, '--version'], env=dict(env), capture_output=True,
                            text=True, timeout=10, check=True)
    match = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', result.stdout)
    if match:
        version = tuple(map(int, match.groups()))
        if version >= tuple(map(int, MIN_VERSION.split('.'))):
            return 'ok (Pi ' + '.'.join(match.groups()) + ')'
    return 'requires Pi >= ' + MIN_VERSION


def arm_spawn(config, contract, server_config, scratch, source_env, *, harness_auth='account'):
    if not config.model_id or not config.model_id.startswith('litellm/') or harness_auth != 'litellm':
        raise SchemaError('Pi requires an explicit litellm/<route> model and LiteLLM auth')
    if config.harness_args:
        raise SchemaError('Pi search cells do not accept extra harness arguments')
    if config.workspace_profile:
        raise SchemaError('Pi code cells are not configured by the search adapter')
    if contract.kind == 'provider' and (
        not isinstance(server_config.get('command'), str) or not server_config['command']
        or server_config.get('url')
        or not isinstance(server_config.get('args', []), list)
        or not isinstance(server_config.get('env', {}), Mapping)
    ):
        raise SchemaError('Pi MCP bridge requires a stdio server command, args and env')
    server_config = dict(server_config)
    server_env = server_config.pop('env', {})
    route = config.model_id[len('litellm/'):]
    catalog = load_catalog()
    if route not in catalog:
        raise SchemaError('Pi model route is absent from the OSS catalog')
    binary = config.binary or source_env.get('SEW_PI_BIN', 'pi')
    if not version_status(binary, source_env).startswith('ok'):
        raise SchemaError('Pi requires version >= ' + MIN_VERSION)
    settings = configuration(source_env)
    home = scratch / 'pi-agent'
    home.mkdir(mode=0o700, exist_ok=True)
    extensions = home / 'extensions'
    shutil.copytree(Path(__file__).parent / 'harnesses/pi_extensions', extensions, dirs_exist_ok=True)
    entry = catalog[route]
    path = home / 'cell.json'
    path.write_text(json.dumps({
        'route': route, 'baseUrl': settings.base_url.rstrip('/') + '/v1',
        'contextWindow': entry['context_window_tokens'], 'maxTokens': entry['max_output_tokens'],
        'serverName': contract.mcp_server_name,
        'server': server_config if contract.kind == 'provider' else None,
    }) + '\n')
    path.chmod(0o600)
    args = ('--no-builtin-tools', '--no-extensions', '--no-skills',
            '--no-prompt-templates', '--no-themes', '--no-context-files', '--no-approve',
            '--offline', '-e', str(extensions / 'litellm.mjs'), '-e', str(extensions / 'mcp-bridge.mjs'))
    return SpawnSurface(contract, args, {'PI_CODING_AGENT_DIR': str(home),
                                       'SEW_PI_CELL_CONFIG': str(path), 'PI_TELEMETRY': '0',
                                       'SEW_PI_MCP_ENV_SECRET': json.dumps(server_env)}, path)


def usage_row(usage):
    if not isinstance(usage, Mapping) or 'output' not in usage:
        return None
    def count(key):
        value = usage.get(key, 0)
        return max(0, value) if type(value) is int else 0
    row = {'input': count('input') + count('cacheWrite'), 'cached_input': count('cacheRead'),
           'output': count('output'), 'reasoning': 0}
    row['total_billable'] = sum(row.values())
    row['cache_write'] = count('cacheWrite')
    return row


class PiProtocol(HarnessProtocol):
    harness_id = 'pi'

    def argv(self, binary, config, last_message_path):
        return [binary, '--provider', 'searchlight-litellm', '--model',
                (config.model_id or '').removeprefix('litellm/'), '-p', '--mode', 'json',
                '--no-session', *config.harness_args]

    def is_ready(self, event):
        return event.get('type') in {'session', 'agent_start', 'turn_start', 'message_start'}

    def is_output(self, event):
        return event.get('type') in {'message_update', 'message_end', 'agent_end'}

    def running_usage(self, event, seen):
        message = event.get('message', {})
        if event.get('type') == 'message_end' and message.get('role') == 'assistant':
            row = usage_row(message.get('usage'))
            if row:
                seen[str(message.get('timestamp', len(seen)))] = row['total_billable']

    def summarize(self, events, last_message):
        messages = [e['message'] for e in events if e.get('type') == 'message_end'
                    and isinstance(e.get('message'), Mapping) and e['message'].get('role') == 'assistant']
        rows = [row for m in messages if (row := usage_row(m.get('usage'))) is not None]
        usage = {key: sum(row[key] for row in rows) for key in rows[0]} if rows else None
        errors = tuple(str(m.get('errorMessage') or 'Pi assistant error') for m in messages
                       if m.get('stopReason') in {'error', 'aborted'})
        text = None
        for message in messages:
            candidate = '\n'.join(b['text'] for b in message.get('content', [])
                                  if b.get('type') == 'text' and isinstance(b.get('text'), str))
            if candidate.strip():
                text = candidate
        return HarnessSummary(text, usage, errors, bool(errors), False)
