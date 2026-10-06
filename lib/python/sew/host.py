"""Optional host services. Explicit standalone mode never loads host plugins."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class HostUnavailable(RuntimeError):
    """A requested host cannot supply a service."""


class HostServices(Protocol):
    mode: str

    def state_root(self, app_id: str = "sew") -> Path: ...
    def resolve_credential(self, ref: str, *, worker_class: str, purpose: str) -> str: ...
    def harness_auth(self, harness: str) -> dict[str, Any]: ...
    def meter_provider_call(self, **call: Any) -> bool: ...


def config_path(env: Mapping[str, str]) -> Path | None:
    if env.get("SEW_CONFIG"):
        return Path(env["SEW_CONFIG"]).expanduser()
    local = Path.cwd() / "sew.yaml"
    if local.is_file():
        return local
    home = Path(env.get("HOME") or Path.home())
    candidate = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "sew/sew.yaml"
    return candidate if candidate.is_file() else None


def read_config(env: Mapping[str, str]) -> dict[str, Any]:
    path = config_path(env)
    if path is None:
        return {}
    try:
        import yaml
    except ImportError:
        return {}

    try:
        data = yaml.safe_load(path.read_text())
    except OSError:
        return {}
    except yaml.YAMLError:
        raise HostUnavailable("invalid sew.yaml") from None
    return dict(data) if isinstance(data, Mapping) else {}


def credential_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Read only literal dotenv assignments; never execute shell expansions."""
    path = Path(env.get("SEW_ENV_FILE", ".env")).expanduser()
    values: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.removeprefix("export ").partition("=")
            key = key.strip()
            if sep and key.replace("_", "a").isalnum():
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
    except OSError:
        pass
    values.update(env)
    return values


class StandaloneHost:
    mode = "standalone"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = os.environ if env is None else env
        self.reason = "explicit standalone mode"

    def state_root(self, app_id: str = "sew") -> Path:
        home = Path(self.env.get("HOME") or Path.home())
        return Path(
            self.env.get("SEW_STATE_ROOT")
            or Path(self.env.get("XDG_STATE_HOME") or home / ".local/share") / app_id
        ).expanduser()

    def resolve_credential(
        self, ref: str, *, worker_class: str = "codex", purpose: str = ""
    ) -> str:
        raise HostUnavailable(self.unavailable_message("op://"))

    def unavailable_message(self, service: str) -> str:
        return (
            f"{service} is unavailable in standalone mode ({self.reason}); "
            "on Agent OS use the in-tree hq-sew launcher or install agent-os-app-sdk "
            "and set SEW_MODE=agent-os; for standalone use account login and "
            "environment credentials"
        )

    def harness_auth(self, harness: str) -> dict[str, Any]:
        return {"source": "account"}

    def meter_provider_call(self, **call: Any) -> bool:
        # The local CallMeter owns sanitized records and pricing in both modes.
        return True


class AgentOsHost:
    mode = "agent-os"

    def __init__(self, plugin: HostServices, env: Mapping[str, str]) -> None:
        self.plugin = plugin
        self.env = env

    def state_root(self, app_id: str = "sew") -> Path:
        explicit = self.env.get("SEW_STATE_ROOT")
        return Path(explicit).expanduser() if explicit else Path(self.plugin.state_root(app_id))

    def resolve_credential(self, ref: str, *, worker_class: str, purpose: str) -> str:
        return self.plugin.resolve_credential(ref, worker_class=worker_class, purpose=purpose)

    def harness_auth_source(self) -> str:
        detect = getattr(self.plugin, "harness_auth_source", None)
        return detect(self.env) if detect else "broker"

    def harness_auth(self, harness: str) -> dict[str, Any]:
        return self.plugin.harness_auth(harness)

    def meter_provider_call(self, **call: Any) -> bool:
        return self.plugin.meter_provider_call(**call)


@dataclass(frozen=True)
class HostResolution:
    host: HostServices
    reason: str


def _source_host_factory(sdk_path: str):
    source = Path(sdk_path).resolve()
    package_name = "agent_os_app_sdk"
    package_root = source / package_name
    if not (package_root / "host.py").is_file():
        raise ImportError("in-tree SDK source is unavailable")
    # Only the SDK package load uses explicit search locations. The SDK's
    # config bootstrap may add platform/agent-os-config/src to sys.path.
    if package_name in sys.modules:
        package_file = getattr(sys.modules[package_name], "__file__", None)
        if not package_file or Path(package_file).resolve() != package_root / "__init__.py":
            raise ImportError("loaded SDK package does not match the source path")
    else:
        spec = importlib.util.spec_from_file_location(
            package_name,
            package_root / "__init__.py",
            submodule_search_locations=[str(package_root)],
        )
        if spec is None or spec.loader is None:
            raise ImportError("in-tree SDK package is unavailable")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        try:
            spec.loader.exec_module(package)
        except Exception:
            sys.modules.pop(package_name, None)
            raise
    module = importlib.import_module("agent_os_app_sdk.host")
    module_file = getattr(module, "__file__", None)
    if not module_file or Path(module_file).resolve() != package_root / "host.py":
        raise ImportError("loaded SDK host does not match the source path")
    return module.sew_host


def resolve_host(env: Mapping[str, str] | None = None) -> HostResolution:
    env = os.environ if env is None else env
    mode = env.get("SEW_MODE") or read_config(env).get("mode", "auto")
    if not isinstance(mode, str) or mode not in {"auto", "agent-os", "standalone"}:
        raise HostUnavailable("SEW mode must be auto, agent-os or standalone")
    if mode == "standalone":
        return HostResolution(StandaloneHost(env), "explicit standalone mode")
    reason = "no Agent OS host plugin installed"
    try:
        entries = importlib.metadata.entry_points(group="sew.hosts", name="agent-os")
        candidates = [entry.load for entry in entries]
    except Exception:
        candidates = []
        reason = "Agent OS host plugin failed to load or detect"
    # The launcher supplies this opt-in; inherited or explicit environment
    # settings also enable it. Standalone returned before any discovery.
    sdk_path = env.get("SEW_AGENT_OS_SDK_PATH")
    if sdk_path:
        candidates.append(lambda: _source_host_factory(sdk_path))
    for load in candidates:
        try:
            plugin = load()()
            detection = plugin.detect()
            reason = detection.reason
            if detection.available:
                return HostResolution(AgentOsHost(plugin, env), f"{mode}: {reason}")
        except Exception:
            # Plugin exception messages may include sensitive host configuration.
            reason = "Agent OS host plugin failed to load or detect"
    if mode == "agent-os":
        raise HostUnavailable(f"agent-os mode unavailable: {reason}")
    standalone = StandaloneHost(env)
    standalone.reason = f"auto: {reason}"
    return HostResolution(standalone, standalone.reason)


def get_host(env: Mapping[str, str] | None = None) -> HostServices:
    return resolve_host(env).host
