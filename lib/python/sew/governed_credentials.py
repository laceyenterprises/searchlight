"""Agent OS governed provider credentials (KEY-21), disabled by default.

When enabled for a provider, Searchlight never reads that provider's key. The
configuration is a reference only: the name of an Agent OS credential, never a
value. A provider call then runs through ``host.credential(...)``: the Agent OS
broker launches the approved ``sew.credential_child`` with the key on a private
pipe FD, the child performs exactly one allowlisted HTTPS request and writes a
key-free response for this process to read.

The key therefore never enters the Searchlight worker process, its environment,
argv, logs, call records or results. The worker only builds the request with a
fixed placeholder in the provider's auth header; the child substitutes the key
for that single header and refuses any other destination.

Enable per provider (Agent OS mode only) with either:

* ``SEW_BRAVE_CREDENTIAL_LEASE=<credential-name>`` in the environment, or
* ``credential_leases: {brave: {name: <credential-name>}}`` in ``sew.yaml``
  (optional ``purpose``, ``ttl_seconds``, ``delivery``, ``child_argv``).

Only ``brave`` is migrated so far. Its direct readers (``SEW_BRAVE_API_KEY``,
``SEW_BRAVE_API_KEY_REF``) are not consulted while the lease is configured, and
there is no fallback to them when the lease is refused.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLACEHOLDER = "agent-os-credential-lease-placeholder"
SOURCE = "agent-os-credential-lease"
CHILD_MODULE = "sew.credential_child"
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
DEFAULT_TTL_SECONDS = 60
MAX_TTL_SECONDS = 300
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class GovernedProvider:
    """Where a governed key may go: one origin, fixed paths, one header."""

    origin: str
    paths: frozenset[str]
    header: str


GOVERNED_PROVIDERS: Mapping[str, GovernedProvider] = {
    "brave": GovernedProvider(
        origin="https://api.search.brave.com",
        paths=frozenset({"/res/v1/web/search"}),
        header="X-Subscription-Token",
    ),
}
ENV_KEYS = {provider: f"SEW_{provider.upper()}_CREDENTIAL_LEASE" for provider in GOVERNED_PROVIDERS}


class GovernedCredentialError(RuntimeError):
    """Value-free refusal for governed credential configuration or delivery."""


@dataclass(frozen=True)
class LeaseConfig:
    provider_id: str
    name: str
    purpose: str = "search"
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    delivery: str = "fd"
    child_argv: tuple[str, ...] = ()

    def argv_prefix(self) -> list[str]:
        return list(self.child_argv) or [sys.executable, "-m", CHILD_MODULE]


def _yaml_entry(environ: Mapping[str, str], provider_id: str) -> Any:
    from .host import read_config

    leases = read_config(environ).get("credential_leases")
    if leases is None:
        return None
    if not isinstance(leases, Mapping):
        raise GovernedCredentialError("credential_leases must be a mapping")
    return leases.get(provider_id)


def lease_config(provider_id: str, environ: Mapping[str, str]) -> LeaseConfig | None:
    """Return the reference-only lease config, or None when disabled (default)."""
    if provider_id not in GOVERNED_PROVIDERS:
        return None
    raw_env = environ.get(ENV_KEYS[provider_id])
    entry = _yaml_entry(environ, provider_id) if not raw_env else {"name": raw_env}
    if entry is None:
        from .host import read_config

        migrations = read_config(environ).get("credential_migrations", {})
        if migrations is None:
            migrations = {}
        if not isinstance(migrations, Mapping):
            raise GovernedCredentialError("invalid credential migration config")
        if provider_id in migrations:
            raise GovernedCredentialError("migrated provider paused; direct reader forbidden")
        return None
    if isinstance(entry, str):
        entry = {"name": entry}
    if not isinstance(entry, Mapping) or not set(entry) <= {
        "name",
        "purpose",
        "ttl_seconds",
        "delivery",
        "child_argv",
    }:
        raise GovernedCredentialError(f"invalid {provider_id} credential lease config")
    name, purpose = entry.get("name"), entry.get("purpose", "search")
    ttl = entry.get("ttl_seconds", DEFAULT_TTL_SECONDS)
    delivery = entry.get("delivery", "fd")
    child_argv = entry.get("child_argv", ())
    # A reference is a credential name. A raw key pasted here is refused and
    # never echoed: the message names only the setting.
    if (
        not isinstance(name, str)
        or not IDENTIFIER.fullmatch(name)
        or not isinstance(purpose, str)
        or not IDENTIFIER.fullmatch(purpose)
        or type(ttl) is not int
        or not 0 < ttl <= MAX_TTL_SECONDS
        or delivery != "fd"
        or not isinstance(child_argv, (list, tuple))
        or any(not isinstance(arg, str) or not arg for arg in child_argv)
    ):
        raise GovernedCredentialError(f"invalid {provider_id} credential lease config")
    return LeaseConfig(provider_id, name, purpose, ttl, delivery, tuple(child_argv))


def write_private_json(path: Path, document: Mapping[str, Any]) -> None:
    write_private_text(path, json.dumps(document))


def write_private_text(path: Path, encoded: str) -> None:
    """Write encoded content to a new private file without following symlinks."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(encoded)


class LeaseUnavailable(OSError):
    """The governed delivery refused or failed; surfaces as a transport error."""


class LeasedTransport:
    """Runs placeholder-bearing requests through the Agent OS child launcher.

    Requests without the placeholder go to ``inner`` unchanged, so wrapping a
    provider whose lease is disabled preserves existing behavior.
    """

    def __init__(self, inner: Any, provider_id: str, environ: Mapping[str, str]) -> None:
        self.inner = inner
        self.provider_id = provider_id
        self.environ = environ

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ):
        if not any(PLACEHOLDER in str(value) for value in headers.values()):
            return self.inner.request(
                method, url, headers=headers, json_body=json_body, timeout_seconds=timeout_seconds
            )
        config = lease_config(self.provider_id, self.environ)
        if config is None:
            raise LeaseUnavailable("credential lease not configured")
        return self._leased(config, method, url, headers, json_body, timeout_seconds)

    def _leased(self, config, method, url, headers, json_body, timeout_seconds):
        from .host import get_host
        from .providers import HttpResponse

        scratch = Path(tempfile.mkdtemp(prefix="sew-lease-"))
        try:
            os.chmod(scratch, 0o700)
            request_path, response_path = scratch / "request.json", scratch / "response.json"
            write_private_json(
                request_path,
                {
                    "provider": self.provider_id,
                    "method": method,
                    "url": url,
                    "headers": dict(headers),
                    "json_body": json_body,
                    "timeout_seconds": timeout_seconds,
                },
            )
            host = get_host(self.environ)
            credential = getattr(host, "credential", None)
            if credential is None:
                raise LeaseUnavailable("host has no governed credential service")
            argv = config.argv_prefix() + [
                "--provider",
                self.provider_id,
                "--request",
                str(request_path),
                "--response",
                str(response_path),
            ]
            try:
                with credential(
                    config.name,
                    kind="api-key",
                    purpose=config.purpose,
                    ttl_seconds=config.ttl_seconds,
                    delivery=config.delivery,
                ) as lease:
                    lease.launch(argv)
            except LeaseUnavailable:
                raise
            except Exception:
                # Host messages are value-free, but keep the class only.
                raise LeaseUnavailable("credential lease refused or unavailable") from None
            try:
                if response_path.stat().st_size > MAX_RESPONSE_BYTES:
                    raise ValueError
                reply = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise LeaseUnavailable("credential child produced no response") from None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        error = reply.get("error") if isinstance(reply, Mapping) else "invalid"
        if error == "timeout":
            raise TimeoutError("provider request timed out")
        if error is not None:
            raise LeaseUnavailable(f"credential child {error}")
        return HttpResponse(
            int(reply["status_code"]), reply.get("body"), dict(reply.get("headers") or {})
        )
