from __future__ import annotations
import importlib.metadata
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from sew import host
from sew.doctor import doctor
from sew.providers import CredentialResolver, CredentialUnavailable
from conftest import LIB_PYTHON


class Plugin:
    mode = "agent-os"

    def detect(self):
        return SimpleNamespace(available=True, reason="fixture host available")

    def state_root(self, app_id="sew"):
        return Path("/fixture/var") / app_id

    def harness_auth(self, harness):
        return {"source": "broker"}

    def resolve_credential(self, ref, **kwargs):
        return "fake-plugin-value"

    def meter_provider_call(self, **call):
        return True


def entries(monkeypatch, plugin=None):
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [] if plugin is None else [SimpleNamespace(load=lambda: lambda: plugin)],
    )


@pytest.mark.parametrize("mode", ["auto", "agent-os"])
def test_usable_plugin(monkeypatch, mode):
    entries(monkeypatch, Plugin())
    selected = host.resolve_host({"SEW_MODE": mode})
    assert selected.host.mode == "agent-os"
    assert "fixture host available" in selected.reason
    assert selected.host.state_root() == Path("/fixture/var/sew")
    assert host.resolve_host(
        {"SEW_MODE": mode, "SEW_STATE_ROOT": "/override"}
    ).host.state_root() == Path("/override")


@pytest.mark.parametrize("plugin", [None, Plugin()])
def test_unusable_plugin(monkeypatch, plugin):
    reason = "no Agent OS host plugin installed"
    if plugin:
        reason = "missing services"
        plugin.detect = lambda: SimpleNamespace(available=False, reason=reason)
    entries(monkeypatch, plugin)
    selected = host.resolve_host({"SEW_MODE": "auto"})
    assert selected.host.mode == "standalone"
    assert reason in selected.reason
    with pytest.raises(host.HostUnavailable, match=reason):
        host.resolve_host({"SEW_MODE": "agent-os"})


def test_broken_plugin(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("sensitive exception")

    monkeypatch.setattr(importlib.metadata, "entry_points", broken)
    selected = host.resolve_host({"SEW_MODE": "auto"})
    assert selected.host.mode == "standalone"
    assert "sensitive" not in selected.reason
    with pytest.raises(host.HostUnavailable, match="failed to load or detect"):
        host.resolve_host({"SEW_MODE": "agent-os"})


def test_explicit_standalone_skips_discovery(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kw: pytest.fail("discovered host")
    )
    assert host.get_host({"SEW_MODE": "standalone"}).mode == "standalone"


def test_config_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "sew.yaml"
    config.write_text("mode: standalone\nunknown: ignored\n")
    assert host.get_host({}).mode == "standalone"
    entries(monkeypatch, Plugin())
    assert host.get_host({"SEW_MODE": "agent-os"}).mode == "agent-os"
    config.write_text("mode: invalid\n")
    with pytest.raises(host.HostUnavailable, match="mode must"):
        host.get_host({})
    config.write_text("[")
    with pytest.raises(host.HostUnavailable, match="invalid sew.yaml"):
        host.get_host({})
    assert host.get_host({"SEW_MODE": "standalone"}).mode == "standalone"


def test_xdg_config_and_missing_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config/sew/sew.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("mode: standalone\n")
    assert host.get_host({"XDG_CONFIG_HOME": str(tmp_path / "config")}).mode == "standalone"
    entries(monkeypatch)
    assert host.get_host({"SEW_CONFIG": str(tmp_path / "missing")}).mode == "standalone"


def test_standalone_state_and_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SEW_BRAVE_API_KEY=dotenv-value\nREF_KEY='reference-value'\n")
    standalone = host.StandaloneHost({"HOME": str(tmp_path), "HQ_ROOT": "/forbidden"})
    assert standalone.state_root() == tmp_path / ".local/share/sew"
    assert host.StandaloneHost({"XDG_STATE_HOME": str(tmp_path)}).state_root() == tmp_path / "sew"
    assert host.StandaloneHost({"SEW_STATE_ROOT": str(tmp_path)}).state_root() == tmp_path
    assert (
        CredentialResolver(
            environ={"SEW_MODE": "standalone", "SEW_TAVILY_API_KEY_REF": "env:REF_KEY"}
        ).resolve("tavily")[0]
        == "reference-value"
    )
    assert standalone.harness_auth("codex") == {"source": "account"}
    assert (
        CredentialResolver(environ={"SEW_MODE": "standalone"}).resolve("brave")[0] == "dotenv-value"
    )
    assert (
        CredentialResolver(
            environ={"SEW_MODE": "standalone", "SEW_BRAVE_API_KEY": "override"}
        ).resolve("brave")[0]
        == "override"
    )
    with pytest.raises(CredentialUnavailable, match="op:// is unavailable"):
        CredentialResolver(
            environ={"SEW_MODE": "standalone", "SEW_TAVILY_API_KEY_REF": "op://forbidden"}
        ).resolve("tavily")
    with pytest.raises(host.HostUnavailable, match="op:// is unavailable"):
        standalone.resolve_credential("env:UNSET")


@pytest.mark.parametrize("mode", ["standalone", "agent-os"])
def test_doctor(mode, tmp_path, monkeypatch):
    entries(monkeypatch, Plugin())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sew.gap.sandbox.qualify_backend", lambda *a, **kw: {})
    output = doctor(
        {
            "SEW_MODE": mode,
            "HOME": str(tmp_path),
            "SEW_BRAVE_API_KEY": "never-print-this-value",
            "PATH": "",
        }
    )
    assert f"mode         {mode}" in output
    assert "state root" in output
    assert "codex:" in output and "claude-code:" in output
    assert "brave" in output
    assert "never-print-this-value" not in output
    assert "sandbox" in output
    assert "srt          unavailable; install @anthropic-ai/sandbox-runtime@0.0.78" in output
    if mode == "agent-os":
        assert "OAuth broker" in output


def test_standalone_import_guard(tmp_path):
    script = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'agent_os_config','agent_os_core','cwp_dispatch','session_ledger','agent_os_app_sdk'}:
            raise AssertionError('forbidden import: '+fullname)
sys.meta_path.insert(0, Guard())
from sew.cli import main
from sew.host import get_host
from sew.providers import CredentialResolver, CredentialUnavailable
from sew.live_harness import scrub_text
from sew import broker_auth, mcp_meter
assert main(['doctor']) == 0
assert get_host().harness_auth('codex')['source'] == 'account'
assert broker_auth.auth_source() == 'account'
try:
    broker_auth.auth_source('broker')
except broker_auth.BrokerAuthError as exc:
    assert 'standalone' in str(exc)
else:
    raise AssertionError('accepted broker')
try:
    CredentialResolver(environ={'SEW_MODE':'standalone','SEW_BRAVE_API_KEY_REF':'op://forbidden'}).resolve('brave')
except CredentialUnavailable:
    pass
else:
    raise AssertionError('accepted op')
assert 'secret-value' not in scrub_text('Authorization: Bearer secret-value')
assert mcp_meter._core() is not None
assert mcp_meter.load_tariffs()
assert not any(n.split('.')[0] in {'agent_os_config','agent_os_core','cwp_dispatch','session_ledger','agent_os_app_sdk'} for n in sys.modules)
"""
    env = {
        "PYTHONPATH": str(LIB_PYTHON),
        "SEW_MODE": "standalone",
        "SEW_AGENT_OS_SDK_PATH": str(LIB_PYTHON.parents[3] / "platform/app-sdk/python/src"),
        "HOME": str(tmp_path),
        "PATH": "",
    }
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=tmp_path, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr


def test_agent_os_provider_uses_plugin_and_hides_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    plugin = Plugin()
    entries(monkeypatch, plugin)
    env = {"SEW_MODE": "agent-os", "SEW_BRAVE_API_KEY_REF": "op://fixture/key"}
    assert CredentialResolver(environ=env).resolve("brave") == (
        "fake-plugin-value",
        "SEW_BRAVE_API_KEY_REF",
    )

    def fail(ref, **kwargs):
        raise RuntimeError("secret must not be printed")

    plugin.resolve_credential = fail
    with pytest.raises(CredentialUnavailable, match="host refused") as exc:
        CredentialResolver(environ=env).resolve("brave")
    assert "secret must not" not in str(exc.value)


def test_standalone_does_not_consult_hq_or_broker(monkeypatch, tmp_path):
    class Guard(dict):
        def get(self, key, *args):
            assert key not in {
                "HQ_ROOT",
                "OAUTH_BROKER_SHARED_SECRET_FILE",
                "OAUTH_BROKER_SHARED_SECRET",
            }
            return super().get(key, *args)

    env = Guard(SEW_MODE="standalone", HOME=str(tmp_path))
    assert host.get_host(env).state_root() == tmp_path / ".local/share/sew"
    assert host.get_host(env).harness_auth("codex") == {"source": "account"}


def test_installed_sdk_plugin_services(monkeypatch, tmp_path):
    import os

    if os.environ.get("SEW_MODE") != "agent-os":
        pytest.skip("installed SDK integration runs in agent-os suite")
    from agent_os_app_sdk import host as sdk

    service = tmp_path / "deploy/modules/worker-pool/bin/hq-app-host"
    service.parent.mkdir(parents=True)
    service.touch(mode=0o700)
    hq = tmp_path / "hq"
    hq.mkdir()
    monkeypatch.setattr(sdk, "_host_paths", lambda: (hq, tmp_path / "deploy"))
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "fixture-service-account-token")
    monkeypatch.setenv("OP_BIOMETRIC_UNLOCK_ENABLED", "false")
    seen = []

    def service_call(operation, payload):
        seen.append((operation, payload))
        return "fixture-credential" if operation == "credential" else True

    monkeypatch.setattr(sdk, "_service", service_call)
    selected = host.resolve_host({"SEW_MODE": "agent-os"})
    assert selected.host.mode == "agent-os"
    assert selected.host.state_root() == hq / "var/sew"
    assert (
        selected.host.resolve_credential(
            "op://fixture/key", worker_class="codex", purpose="fixture"
        )
        == "fixture-credential"
    )
    assert selected.host.meter_provider_call(
        provider_id="brave", run_id="fixture", tool="brave_web_search", arguments={}, reply={}
    )
    assert [operation for operation, payload in seen] == ["credential", "meter"]
    assert seen[0][1]["worker_class"] == "codex"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"paired"', "paired"),
        ("'paired'", "paired"),
        ('"leading', '"leading'),
        ("trailing'", "trailing'"),
        ("\"mismatch'", "\"mismatch'"),
        ('"', '"'),
        ('""quoted""', '"quoted"'),
    ],
)
def test_dotenv_preserves_literal_quotes(tmp_path, raw, expected):
    path = tmp_path / "credentials.env"
    path.write_text(f"KEY={raw}\n")
    assert host.credential_environment({"SEW_ENV_FILE": str(path)})["KEY"] == expected


def test_config_without_pyyaml(tmp_path, monkeypatch):
    import builtins

    config = tmp_path / "sew.yaml"
    config.write_text("mode: agent-os\n")
    original = builtins.__import__

    def without_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("missing PyYAML")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_yaml)
    entries(monkeypatch)
    assert host.read_config({"SEW_CONFIG": str(config)}) == {}
    assert host.get_host({"SEW_CONFIG": str(config)}).mode == "standalone"


@pytest.mark.parametrize("mode, present", [("agent-os", True), ("standalone", False)])
def test_doctor_op_reference_is_configured_unverified(tmp_path, monkeypatch, mode, present):
    entries(monkeypatch, Plugin())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sew.gap.sandbox.qualify_backend", lambda *a, **kw: {})
    output = doctor({"SEW_MODE": mode, "SEW_BRAVE_API_KEY_REF": "op://private/key", "PATH": ""})
    assert ("keys present: brave" in output) == present
    assert "op://private/key" not in output


@pytest.mark.parametrize("service", ["broker", "op"])
@pytest.mark.parametrize("plugin", [None, Plugin()])
def test_auto_fallback_errors_explain_detection_and_fix(monkeypatch, tmp_path, service, plugin):
    from sew import broker_auth

    monkeypatch.chdir(tmp_path)
    if plugin:
        plugin.detect = lambda: SimpleNamespace(available=False, reason="missing services")
    entries(monkeypatch, plugin)
    env = {"SEW_MODE": "auto", "SEW_BRAVE_API_KEY_REF": "op://private/key"}
    with pytest.raises((broker_auth.BrokerAuthError, CredentialUnavailable)) as exc:
        if service == "broker":
            broker_auth.auth_source("broker", environ=env)
        else:
            CredentialResolver(environ=env).resolve("brave")
    message = str(exc.value)
    assert "standalone mode" in message
    assert ("missing services" if plugin else "no Agent OS host plugin installed") in message
    assert "hq-sew launcher" in message
    assert "agent-os-app-sdk" in message
    assert "SEW_MODE=agent-os" in message
    assert "op://private/key" not in message


def test_invalid_explicit_sdk_path_is_diagnosed(monkeypatch, tmp_path):
    entries(monkeypatch)
    selected = host.resolve_host({"SEW_MODE": "auto", "SEW_AGENT_OS_SDK_PATH": str(tmp_path)})
    assert selected.host.mode == "standalone"
    assert "failed to load or detect" in selected.reason


@pytest.fixture
def source_sdk(tmp_path, monkeypatch):
    package = tmp_path / "agent_os_app_sdk"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "host.py").write_text(
        "from types import SimpleNamespace\n"
        "def sew_host():\n"
        "    return SimpleNamespace(detect=lambda: SimpleNamespace(\n"
        "        available=True, reason='source fixture available'))\n"
    )
    for name in ("agent_os_app_sdk", "agent_os_app_sdk.host"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield tmp_path
    for name in ("agent_os_app_sdk", "agent_os_app_sdk.host"):
        sys.modules.pop(name, None)


@pytest.mark.parametrize("mode", ["auto", "agent-os"])
@pytest.mark.parametrize("failure", ["metadata", "load", "factory", "detect", "unavailable"])
def test_source_sdk_after_failed_installed_candidate(monkeypatch, source_sdk, mode, failure):
    def broken():
        raise ModuleNotFoundError("sensitive installed SDK detail")

    plugin = Plugin()
    if failure == "detect":
        plugin.detect = broken
    elif failure == "unavailable":
        plugin.detect = lambda: SimpleNamespace(available=False, reason="missing services")
    factory = broken if failure == "factory" else lambda: plugin
    load = broken if failure == "load" else lambda: factory
    if failure == "metadata":
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: broken())
    else:
        monkeypatch.setattr(
            importlib.metadata, "entry_points", lambda **kw: [SimpleNamespace(load=load)]
        )
    selected = host.resolve_host({"SEW_MODE": mode, "SEW_AGENT_OS_SDK_PATH": str(source_sdk)})
    assert selected.host.mode == "agent-os"
    assert selected.reason == f"{mode}: source fixture available"


def test_installed_candidates_are_independent(monkeypatch, tmp_path):
    def broken():
        raise ImportError("sensitive installed detail")

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [SimpleNamespace(load=broken), SimpleNamespace(load=lambda: Plugin)],
    )
    selected = host.resolve_host({"SEW_AGENT_OS_SDK_PATH": str(tmp_path)})
    assert selected.host.mode == "agent-os"
    assert "fixture host available" in selected.reason


@pytest.mark.parametrize("seed", ["matching", "package-mismatch", "host-mismatch", "no-file"])
def test_source_sdk_validates_cached_modules(monkeypatch, source_sdk, tmp_path, seed):
    entries(monkeypatch)
    if seed == "matching":
        host._source_host_factory(str(source_sdk))
    elif seed == "host-mismatch":
        host._source_host_factory(str(source_sdk))
        monkeypatch.setitem(
            sys.modules,
            "agent_os_app_sdk.host",
            SimpleNamespace(__file__=str(tmp_path / "other/host.py"), sew_host=Plugin),
        )
    else:
        package = SimpleNamespace()
        if seed == "package-mismatch":
            package.__file__ = str(tmp_path / "other/__init__.py")
        monkeypatch.setitem(sys.modules, "agent_os_app_sdk", package)
    env = {"SEW_MODE": "auto", "SEW_AGENT_OS_SDK_PATH": str(source_sdk)}
    selected = host.resolve_host(env)
    assert selected.host.mode == ("agent-os" if seed == "matching" else "standalone")
    if seed != "matching":
        assert selected.reason == "auto: Agent OS host plugin failed to load or detect"
        with pytest.raises(host.HostUnavailable, match="failed to load or detect"):
            host.resolve_host(dict(env, SEW_MODE="agent-os"))


def test_source_sdk_reports_last_detection_reason(monkeypatch, source_sdk):
    def broken():
        raise ImportError("sensitive installed detail")

    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kw: [SimpleNamespace(load=broken)]
    )
    factory = host._source_host_factory(str(source_sdk))
    plugin = factory()
    plugin.detect = lambda: SimpleNamespace(available=False, reason="source unavailable")
    monkeypatch.setattr(sys.modules["agent_os_app_sdk.host"], "sew_host", lambda: plugin)
    selected = host.resolve_host({"SEW_AGENT_OS_SDK_PATH": str(source_sdk)})
    assert selected.reason == "auto: source unavailable"


def test_failed_source_package_exec_rolls_back(monkeypatch, source_sdk):
    entries(monkeypatch)
    (source_sdk / "agent_os_app_sdk/__init__.py").write_text("raise ImportError('private')\n")
    selected = host.resolve_host({"SEW_AGENT_OS_SDK_PATH": str(source_sdk)})
    assert selected.host.mode == "standalone"
    assert "private" not in selected.reason
    assert "agent_os_app_sdk" not in sys.modules


def test_doctor_reports_srt_available(tmp_path, monkeypatch):
    entries(monkeypatch, Plugin())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sew.doctor.shutil.which", lambda command, **kw: "/tools/srt" if command == "srt" else None
    )
    output = doctor({"SEW_MODE": "standalone", "HOME": str(tmp_path), "PATH": "/tools"})
    assert "srt          available (/tools/srt); canary unverified" in output
