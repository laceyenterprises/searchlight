"""Inspect host capabilities, with an explicit opt-in broker credential check."""

from __future__ import annotations
import os
import tempfile
from pathlib import Path
import shutil
from collections.abc import Mapping
from . import harnesses as harness_registry
from .host import config_path, credential_environment, resolve_host


def doctor(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    resolution = resolve_host(env)
    host = resolution.host
    from .providers import PROVIDER_CREDENTIALS

    values = credential_environment(env)
    present = []
    for provider, (key, ref_key) in PROVIDER_CREDENTIALS.items():
        ref = values.get(ref_key, "")
        if (
            values.get(key)
            or (ref.startswith("env:") and values.get(ref[4:]))
            or (host.mode == "agent-os" and ref.startswith("op://"))
        ):
            present.append(provider)
    harnesses = []
    for harness, command in (
        (spec.id, spec.default_bin) for spec in harness_registry.specs(live=True)
    ):
        available = shutil.which(command, path=env.get("PATH", "")) is not None
        status = "CLI available; account login unverified" if available else "CLI unavailable"
        if (
            host.mode == "agent-os"
            and getattr(host, "harness_auth_source", lambda: "broker")() == "broker"
        ):
            status = "OAuth broker configured; authentication unverified"
        harnesses.append(f"{harness}: {status}")
    from .gap.sandbox import qualify_backend, select_backend
    from .gap.workspace import EgressCanaryRefused

    try:
        backend = select_backend(env)
        with tempfile.TemporaryDirectory(prefix="sew-doctor-") as scratch:
            profile = "(version 1)(allow default)(deny network-outbound)"
            qualify_backend(backend, Path(scratch), env, profile=profile)
        sandbox = f"{backend.name} — egress canary denied (backend admissible; GAP shell canary still required)"
    except (EgressCanaryRefused, OSError) as exc:
        sandbox = f"unavailable / inadmissible ({exc})"
    srt = shutil.which("srt", path=env.get("PATH", ""))
    srt_status = (
        f"available ({srt}); canary unverified"
        if srt
        else "unavailable; install @anthropic-ai/sandbox-runtime@0.0.78 "
        "with npm install --prefix <dir> and add <dir>/node_modules/.bin to PATH"
    )
    path = config_path(env)
    return "\n".join(
        [
            f"mode         {host.mode} ({resolution.reason})",
            f"state root   {host.state_root()}",
            f"harnesses    {'; '.join(harnesses)}",
            f"providers    keys present: {' '.join(present) or 'none'} ({len(present)}/{len(PROVIDER_CREDENTIALS)} set)",
            "credentials  "
            + (
                "secrets bus (service-account env)"
                if host.mode == "agent-os"
                else "environment / local .env"
            ),
            f"sandbox      {sandbox}",
            f"srt          {srt_status}",
            f"config       {path or 'none (defaults)'}",
            *oss_lines(env, host),
        ]
    )


def command(args) -> int:
    if not getattr(args, "codex_broker_auth", False):
        print(doctor())
        return 0
    from .broker_auth import codex_home_auth

    with tempfile.TemporaryDirectory(prefix="sew-codex-auth-check-") as temporary:
        root = Path(temporary)
        try:
            metadata = codex_home_auth(root / "home", root / "log")
            print(f"codex broker auth validated; expires_at={metadata['expires_at']}")
        except Exception as exc:
            try:
                from .live_harness import scrub_text

                message = scrub_text(str(exc))
            except Exception:
                # Scrubbing can itself be unavailable during host setup.
                message = type(exc).__name__
            print(f"codex broker auth validation failed: {message}")
            diagnostic = root / "log/codex-broker-auth-stderr.log"
            if diagnostic.is_file():
                # Only the scrubbed diagnostic reaches the activation log.
                print("temporary diagnostic contents (file removed after this check):")
                print(diagnostic.read_text())
            return 1
    return 0


def oss_lines(env, host) -> list[str]:
    """Only enabled OSS performs HTTP diagnostics; errors never echo credentials."""
    import json
    from urllib.request import Request, build_opener, HTTPRedirectHandler
    from .oss import configuration, load_catalog

    settings = configuration(env)
    if not settings.enabled:
        return ['oss models   disabled (sew.yaml oss.enabled: false); OSS harnesses and litellm/* models are refused']
    catalog = load_catalog()
    selected = set(settings.models)
    lines = [f'oss models   enabled ({settings.enabled_source})']
    try:
        receipt = host.litellm_credentials('claude-code')
        key = receipt['api_key']
        if not isinstance(key, str) or not key:
            raise ValueError
    except Exception:
        key = None
    key_status = (f'{settings.api_key_env} set (value not shown)' if host.mode == 'standalone' else 'resolved through the host credential service') if key else 'not provisioned'
    lines.append(f'litellm key  {"ok" if key else "unavailable"} {key_status}')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = build_opener(NoRedirect())
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    for path in ('/health/readiness', '/v1/models'):
        try:
            request = Request(settings.base_url + path, headers=headers)
            with opener.open(request, timeout=3) as response:
                status = response.status
                data = response.read(1_000_001)
                if len(data) > 1_000_000:
                    raise ValueError
            if path == '/health/readiness':
                lines.append(f'litellm      ok {settings.base_url} {path} {status}')
            else:
                ids = {row['id'] for row in json.loads(data)['data']}
                lines.append(f'oss routes reachable {len(selected & ids)}/{len(selected)} listed by /v1/models')
        except Exception:
            lines.append(f'litellm      unavailable {settings.base_url} {path}' if path == '/health/readiness' else 'oss routes reachable unavailable (/v1/models)')
    self_hosted = sum(catalog[route]['rate_basis'] == 'self-hosted' for route in selected)
    lines.append(f'oss catalog  {len(selected)} routes; {len(selected)} priced ({self_hosted} self-hosted at $0)')
    return lines
