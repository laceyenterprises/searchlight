"""Opt-in OSS configuration and the versioned LiteLLM route catalog."""
from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .catalog import module_root
from .host import HostUnavailable, read_config

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
ROUTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*")


def valid_model_id(model: object) -> bool:
    if not isinstance(model, str):
        return False
    if model.startswith("litellm/"):
        route = model[len("litellm/"):]
        return len(model) <= 200 and bool(ROUTE_RE.fullmatch(route)) and all(
            segment not in {".", ".."} for segment in route.split("/")
        )
    return bool(MODEL_RE.fullmatch(model))


def parse_catalog(document):
    from .cost_model import PriceTableError, _require_rate, _require_receipt

    if not isinstance(document, Mapping) or document.get("version") != 1:
        raise PriceTableError("OSS catalog must have version: 1")
    models = document.get("models")
    if not isinstance(models, Mapping) or not models:
        raise PriceTableError("OSS catalog.models must be a non-empty mapping")
    result = {}
    for route, entry in models.items():
        where = f"OSS catalog.models.{route}"
        if not valid_model_id(f"litellm/{route}") or not isinstance(entry, Mapping):
            raise PriceTableError(f"{where} must be a safe route and mapping")
        if entry.get("rate_basis") not in {"list", "self-hosted"}:
            raise PriceTableError(f"{where}.rate_basis must be list or self-hosted")
        _require_receipt(entry, where)
        for field in ("upstream", "provider"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise PriceTableError(f"{where}.{field} is required")
        for field in ("context_window_tokens", "max_output_tokens"):
            if type(entry.get(field)) is not int or entry[field] <= 0:
                raise PriceTableError(f"{where}.{field} must be a positive integer")
        if entry['max_output_tokens'] > entry['context_window_tokens']:
            raise PriceTableError(f"{where} output limit exceeds context")
        for field in ("input_usd_per_mtok", "output_usd_per_mtok", "cached_input_usd_per_mtok"):
            if field == "cached_input_usd_per_mtok" and entry.get(field) is None:
                continue
            _require_rate(entry, field, where)
            if not math.isfinite(entry[field]):
                raise PriceTableError(f"{where}.{field} must be finite")
            if entry['rate_basis'] == 'self-hosted' and entry[field] != 0:
                raise PriceTableError(f"{where} self-hosted rates must be $0")
        if entry['rate_basis'] == 'self-hosted' and entry.get('cached_input_usd_per_mtok') != 0:
            raise PriceTableError(f"{where} self-hosted cache rate must be $0")
        result[route] = dict(entry)
    return result


def load_catalog(path: Path | None = None):
    return parse_catalog(yaml.safe_load((path or module_root() / 'config/oss-models.yaml').read_text()))


@dataclass(frozen=True)
class OssConfig:
    enabled: bool
    base_url: str
    api_key_env: str
    harnesses: tuple[str, ...]
    models: tuple[str, ...]
    enabled_source: str


def configuration(env: Mapping[str, str] | None = None) -> OssConfig:
    env = os.environ if env is None else env
    block = read_config(env).get('oss', {})
    if not isinstance(block, Mapping):
        raise HostUnavailable('sew.yaml oss must be a mapping')
    enabled = block.get('enabled', False)
    source = 'sew.yaml oss.enabled'
    if 'SEW_OSS_ENABLED' in env:
        value = env['SEW_OSS_ENABLED'].lower()
        if value not in {'1', '0', 'true', 'false'}:
            raise HostUnavailable('SEW_OSS_ENABLED must be 1, 0, true or false')
        enabled = value in {'1', 'true'}
        source = 'SEW_OSS_ENABLED'
    if type(enabled) is not bool:
        raise HostUnavailable('sew.yaml oss.enabled must be a boolean')
    proxy = block.get('litellm', {})
    if not isinstance(proxy, Mapping):
        raise HostUnavailable('sew.yaml oss.litellm must be a mapping')
    base = env.get('SEW_LITELLM_BASE_URL', proxy.get('base_url', 'http://127.0.0.1:4000'))
    if not isinstance(base, str):
        raise HostUnavailable('LiteLLM base URL must be an HTTP(S) endpoint without credentials')
    try:
        parsed = urlsplit(base)
        parsed.port  # Validate the port without including the URL in errors.
    except ValueError:
        raise HostUnavailable('invalid LiteLLM base URL') from None
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HostUnavailable('LiteLLM base URL must be an HTTP(S) endpoint without credentials')
    key_env = proxy.get('api_key_env', 'SEW_LITELLM_API_KEY')
    if not isinstance(key_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key_env):
        raise HostUnavailable('oss.litellm.api_key_env must name an environment variable')
    catalog = load_catalog()
    selected_models = block.get('models', list(catalog))
    selected_harnesses = block.get('harnesses', ['hermes', 'pi', 'opencode'])
    if not isinstance(selected_models, list) or any(not isinstance(m, str) or m not in catalog for m in selected_models):
        raise HostUnavailable('oss.models must list catalog routes')
    if not isinstance(selected_harnesses, list) or any(not isinstance(h, str) or not ROUTE_RE.fullmatch(h) or '/' in h for h in selected_harnesses):
        raise HostUnavailable('oss.harnesses must list harness ids')
    return OssConfig(enabled, base.rstrip('/'), key_env, tuple(selected_harnesses), tuple(selected_models), source)


def require_enabled(harness: str, model: str | None, env=None) -> OssConfig:
    from . import harnesses

    settings = configuration(env)
    spec = harnesses.find(harness)
    is_harness = bool(spec and spec.oss) or harness in {'hermes', 'pi', 'opencode'}
    is_model = bool(model and model.startswith('litellm/'))
    subject = f'{model} is an OSS model' if is_model else f'{harness} is an OSS harness'
    if (is_harness or is_model) and not settings.enabled:
        raise HostUnavailable(
            f'{subject}, but OSS models are disabled.\n'
            'Set oss.enabled: true in sew.yaml (or SEW_OSS_ENABLED=1) and configure LiteLLM; see `sew doctor`.'
        )
    if spec and spec.oss_model_only and not is_model:
        raise HostUnavailable(f"{harness} requires an explicit litellm/<route> OSS model")
    if is_harness and harness not in settings.harnesses:
        raise HostUnavailable(f'{harness} is not selected in oss.harnesses')
    if is_model and model[len('litellm/'):] not in settings.models:
        raise HostUnavailable(f'{model} is not selected in oss.models')
    return settings


def litellm_cell_env(harness: str, env=None) -> dict[str, str]:
    """Resolve only proxy credentials; never read account or OAuth credentials."""
    from .host import get_host

    receipt = get_host(env).litellm_credentials(harness)
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get('api_key'), str) or not receipt['api_key']:
        raise HostUnavailable('LiteLLM key not provisioned')
    settings = configuration(env)
    return {'SEW_LITELLM_API_KEY': receipt['api_key'], 'SEW_LITELLM_BASE_URL': settings.base_url}


def cell_plan(harness: str, model: str | None, arm: str, *, requested_auth=None, env=None) -> str:
    """Describe the cell without resolving credentials, writing files or spawning."""
    from . import broker_auth, harnesses
    from .arms import PROVIDER_SERVER_NAMES

    settings = require_enabled(harness, model, env)
    spec = harnesses.find(harness)
    if model and not valid_model_id(model):
        raise HostUnavailable('model must be an explicit, safe model identifier')
    source = broker_auth.auth_source(requested_auth, env, model_id=model)
    oss_model = bool(model and model.startswith('litellm/'))
    endpoint = f' via {settings.base_url}' if oss_model else ''
    label = ' (OSS harness)' if (spec and spec.oss) or harness in {'hermes', 'pi', 'opencode'} else ''
    tools = {
        'exa': 'web_search_exa, web_fetch_exa',
        'parallel-web': 'web_search_preview, web_fetch',
        'firecrawl': 'firecrawl_search, firecrawl_scrape',
        'brave': 'brave_web_search',
        'tavily': 'tavily_search, tavily_extract',
        'perplexity': 'perplexity_search',
    }
    arm_text = f'{arm} -> MCP stdio server, tools: {tools[arm]}' if arm in PROVIDER_SERVER_NAMES else arm
    config_line = {
        'claude-code': '$SEW_CELL/claude-mcp.json',
        'codex': '$SEW_CELL/codex-home/config.toml (isolated CODEX_HOME)',
        'hermes': '$SEW_CELL/hermes-home/config.yaml (isolated HERMES_HOME)',
        'opencode': '$SEW_CELL/opencode/opencode.json (isolated XDG_* and OPENCODE_CONFIG)',
    }.get(harness, 'provided by harness adapter (pending)')
    auth_text = f'litellm key from {settings.api_key_env} (OAuth/account credentials not forwarded)' if source == 'litellm' else source
    native = 'unavailable on OSS models' if oss_model and arm == 'native' and not (spec and spec.oss and spec.native_search) else 'harness web tools enabled' if arm == 'native' else 'harness web tools disabled'
    return '\n'.join([
        'cell plan (no model call):',
        f'  harness   {harness}{label}    model   {model or "configured default"}{endpoint}',
        f'  auth      {auth_text}',
        f'  arm       {arm_text}',
        f'  native    {native}',
        f'  config    {config_line}',
        '  judges    unchanged: claude-code, codex on their own credentials',
    ])
