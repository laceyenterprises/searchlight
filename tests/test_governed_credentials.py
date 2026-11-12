"""KEY-21 governed Brave key via Agent OS host.credential, synthetic only.

The fake host stands in for the Agent OS broker child launcher: it delivers a
unique synthetic key on a private pipe FD to ``sew.credential_child`` and
records argv. The key must never reach the worker-side resolver, request
file, argv, call record, sources, health detail or captured output.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from conftest import LIB_PYTHON
from sew import credential_child
from sew.governed_credentials import PLACEHOLDER, SOURCE, lease_config
from sew.providers import (
    CredentialResolver,
    CredentialUnavailable,
    HttpResponse,
    ProviderRequest,
    make_provider,
)


def synthetic_key() -> str:
    return "SYNTH-KEY21-" + uuid4().hex


class FakeProviderTransport:
    """The provider on the far side of the child: sees the key, echoes it back."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.calls: list[dict] = []

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        assert headers["X-Subscription-Token"] == self.key
        return HttpResponse(
            200,
            {
                "web": {
                    "results": [
                        {
                            "url": "https://example.invalid/a",
                            "title": "Synthetic result",
                            "description": "echoed " + self.key,
                        }
                    ]
                },
                "debug_token": self.key,
            },
            {"x-echo": self.key},
        )


class Lease:
    def __init__(self, host, name, request):
        self.host, self.name, self.request = host, name, request

    def __enter__(self):
        self.host.events.append(("vend", self.name, self.request))
        return self

    def __exit__(self, *exc):
        self.host.events.append(("close", self.name))

    def launch(self, argv):
        """Stand-in for the broker child launcher: key on a private pipe FD."""
        self.host.argv.append(list(argv))
        if self.host.refuse:
            raise RuntimeError("synthetic refusal " + self.host.key)
        read_end, write_end = os.pipe()
        os.write(write_end, self.host.key.encode())
        os.close(write_end)
        prefix = len(argv) - 6
        code = credential_child.main(
            argv[prefix:],
            environ={"AGENT_OS_CREDENTIAL_FD": str(read_end)},
            transport=self.host.provider,
        )
        self.host.exit_codes.append(code)
        return {"state": "attached"}


class Plugin:
    mode = "agent-os"

    def __init__(self, key):
        self.key = key
        self.provider = FakeProviderTransport(key)
        self.events, self.argv, self.exit_codes = [], [], []
        self.refuse = False

    def detect(self):
        return SimpleNamespace(available=True, reason="fixture host available")

    def state_root(self, app_id="sew"):
        return Path("/fixture/var") / app_id

    def resolve_credential(self, ref, **kwargs):
        raise AssertionError("direct reader must not be consulted")

    def credential(self, name, **request):
        return Lease(self, name, request)

    def meter_provider_call(self, **call):
        return True


@pytest.fixture
def governed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    key = synthetic_key()
    plugin = Plugin(key)
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [SimpleNamespace(load=lambda: lambda: plugin)],
    )
    env = {
        "SEW_MODE": "agent-os",
        "SEW_BRAVE_CREDENTIAL_LEASE": "search-example",
        # Old direct copies must be ignored, never used as a fallback.
        "SEW_BRAVE_API_KEY": "direct-copy-" + key,
        "SEW_BRAVE_API_KEY_REF": "op://fixture/brave",
    }
    return SimpleNamespace(key=key, plugin=plugin, env=env)


def _call(env):
    provider = make_provider(
        "brave", credential_resolver=CredentialResolver(environ=env), live_enabled=True
    )
    return provider.call(
        ProviderRequest(operation="search", run_id="run-key21", query="synthetic query")
    )


def test_disabled_by_default_preserves_direct_reader(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert lease_config("brave", {}) is None
    env = {"SEW_MODE": "standalone", "SEW_BRAVE_API_KEY": "fixture-direct"}
    assert CredentialResolver(environ=env).resolve("brave") == (
        "fixture-direct",
        "SEW_BRAVE_API_KEY",
    )
    # Other providers are not migrated and keep their readers.
    assert lease_config("tavily", {"SEW_TAVILY_CREDENTIAL_LEASE": "x"}) is None


def test_lease_replaces_direct_reader_without_reading_it(governed):
    assert CredentialResolver(environ=governed.env).resolve("brave") == (PLACEHOLDER, SOURCE)


def test_synthetic_provider_call_through_host_credential(governed, capfd):
    result = _call(governed.env)
    assert result.status == "ok" and len(result.sources) == 1
    assert governed.plugin.exit_codes == [0]
    [vend, close] = governed.plugin.events
    assert vend == (
        "vend",
        "search-example",
        {"kind": "api-key", "purpose": "search", "ttl_seconds": 60, "delivery": "fd"},
    )
    assert close == ("close", "search-example")
    [call] = governed.plugin.provider.calls
    assert call["url"] == "https://api.search.brave.com/res/v1/web/search"
    argv = governed.plugin.argv[0]
    assert argv[:3] == [sys.executable, "-m", "sew.credential_child"]
    # Scratch request/response files are removed after the call.
    assert not Path(argv[argv.index("--request") + 1]).parent.exists()
    surfaces = json.dumps(
        [result.call_record, list(result.sources), argv, result.error_class], default=str
    )
    captured = capfd.readouterr()
    assert governed.key not in surfaces + captured.out + captured.err
    assert "[redacted]" in json.dumps(list(result.sources))


def test_refused_lease_has_no_fallback_and_no_key_in_result(governed):
    governed.plugin.refuse = True
    result = _call(governed.env)
    assert result.status == "failed" and result.error_class == "transport_error"
    assert governed.plugin.provider.calls == []
    assert governed.key not in json.dumps(result.call_record, default=str)


def test_standalone_mode_refuses_configured_lease(governed):
    env = dict(governed.env, SEW_MODE="standalone")
    with pytest.raises(CredentialUnavailable, match="requires Agent OS mode"):
        CredentialResolver(environ=env).resolve("brave")


@pytest.mark.parametrize("value", ["SYNTH-KEY21-RAW-VALUE", "op://fixture/brave", "a b"])
def test_reference_only_config_refuses_values_without_echo(governed, value):
    env = dict(governed.env, SEW_BRAVE_CREDENTIAL_LEASE=value)
    with pytest.raises(CredentialUnavailable) as caught:
        CredentialResolver(environ=env).resolve("brave")
    assert value not in str(caught.value)


def test_yaml_reference_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sew.yaml").write_text(
        "credential_leases:\n  brave:\n    name: search-example\n    ttl_seconds: 30\n"
    )
    config = lease_config("brave", {"SEW_MODE": "agent-os"})
    assert config is not None and config.name == "search-example" and config.ttl_seconds == 30


def _request(tmp_path, **overrides):
    document = {
        "provider": "brave",
        "method": "POST",
        "url": "https://api.search.brave.com/res/v1/web/search",
        "headers": {"X-Subscription-Token": PLACEHOLDER, "Accept": "application/json"},
        "json_body": {"q": "synthetic"},
        "timeout_seconds": 5,
    }
    document.update(overrides)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "https://attacker.example.invalid/res/v1/web/search"},
        {"url": "https://api.search.brave.com/other"},
        {"headers": {"Authorization": PLACEHOLDER}},
        {"headers": {"X-Subscription-Token": PLACEHOLDER, "X-Copy": PLACEHOLDER}},
        {"headers": {"X-Subscription-Token": "prefix-" + PLACEHOLDER}},
        {"json_body": {"q": PLACEHOLDER}},
        {"provider": "tavily"},
    ],
)
def test_child_refuses_any_other_destination_or_placement(tmp_path, overrides):
    key = synthetic_key()
    transport = FakeProviderTransport(key)
    read_end, write_end = os.pipe()
    os.write(write_end, key.encode())
    os.close(write_end)
    code = credential_child.main(
        [
            "--provider",
            "brave",
            "--request",
            str(_request(tmp_path, **overrides)),
            "--response",
            str(tmp_path / "response.json"),
        ],
        environ={"AGENT_OS_CREDENTIAL_FD": str(read_end)},
        transport=transport,
    )
    assert code == 2 and transport.calls == []
    assert json.loads((tmp_path / "response.json").read_text()) == {"error": "refused"}


def test_child_subprocess_reads_only_the_passed_fd(tmp_path):
    """Real child process: FD delivery, clean env, refusal before any network."""
    key = synthetic_key()
    read_end, write_end = os.pipe()
    request = _request(tmp_path, url="https://attacker.example.invalid/res/v1/web/search")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sew.credential_child",
            "--provider",
            "brave",
            "--request",
            str(request),
            "--response",
            str(tmp_path / "response.json"),
        ],
        env={"PYTHONPATH": str(LIB_PYTHON), "AGENT_OS_CREDENTIAL_FD": str(read_end)},
        pass_fds=(read_end,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(read_end)
    os.write(write_end, key.encode())
    os.close(write_end)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 2
    assert key.encode() not in out + err
    assert json.loads((tmp_path / "response.json").read_text()) == {"error": "refused"}
