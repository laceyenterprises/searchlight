"""Golden spawn surfaces for the hosted harnesses (OHM-01).

The harness registry moved every per-harness spawn decision behind one lookup.
This test pins what Claude Code and Codex are actually launched with on every
arm: the argv, the arm's spawn env, the child environment, every config file
the arm writes, and which harness arguments the arm refuses. The golden file was
generated from searchlight e02eaa1, before the registry existed, so any byte of
drift in a frontier spawn fails here.

Regenerate only when a spawn change is intended and reviewed:

    python3 tests/test_harness_spawn_golden.py --write
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib" / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib" / "python"))

from sew.arms import PROVIDER_SERVER_NAMES, prepare_arm_spawn  # noqa: E402
from sew.harness import HarnessRunConfig, ProviderExposure  # noqa: E402
from sew.live_harness import PROTOCOLS, child_environment, resolve_binary  # noqa: E402
from sew.schema import SchemaError  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "harness-spawn-golden.json"
GAP_FIXTURE = Path(__file__).parent / "fixtures" / "gap"
HOSTED = ("claude-code", "codex")
SEARCH_ARMS = ("native", "no-search", *sorted(PROVIDER_SERVER_NAMES))
CODE_ARMS = ("no-search", "floor", "ceiling", "native", *sorted(PROVIDER_SERVER_NAMES))
# Every option either harness's arm contract refuses today, plus options it must
# keep accepting. Probing the union pins the refusal set exactly.
FLAG_PROBES = (
    "--mcp-config",
    "--mcp-config=x.json",
    "--strict-mcp-config",
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
    "--tools",
    "--model",
    "--fallback-model",
    "-c",
    "-ckey=v",
    "--config",
    "--enable",
    "--search",
    "--ignore-user-config",
    "-m",
    "-p",
    "--profile",
    "--settings",
    "--setting-sources",
    "--permission-mode",
    "--add-dir",
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--sandbox",
    "-s",
    "--full-auto",
    "--yolo",
    "--dangerously-bypass-approvals-and-sandbox",
    "--ask-for-approval",
    "-a",
    "--permission-profile",
    "-P",
    "--cd",
    "-C",
    "--plugin-dir",
    "--verbose",
    "--max-turns",
    "--color",
)


def _gap_root(base: Path) -> tuple[Path, Path, str]:
    root = base / "module"
    shutil.copytree(GAP_FIXTURE, root / "catalogs" / "gap")
    task = json.loads((GAP_FIXTURE / "tasks.json").read_text(encoding="utf-8"))[0]
    wheelhouse = base / "wheelhouse"
    wheelhouse.mkdir()
    for pin in task["packages"]:
        content = pin["version"].encode()
        (wheelhouse / pin["url"].rsplit("/", 1)[1]).write_bytes(content)
        pin["sha256"] = hashlib.sha256(content).hexdigest()
    (root / "catalogs" / "gap" / "tasks.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "catalog_id": "gap-golden",
                "authoring_policy": "synthetic",
                "tasks": [task],
            }
        ),
        encoding="utf-8",
    )
    return root, wheelhouse, task["id"]


def _exposure(arm: str, *, header: bool = False) -> ProviderExposure | None:
    if arm not in PROVIDER_SERVER_NAMES:
        return None
    server: dict[str, Any] = {"command": "/opt/golden/provider-mcp", "args": ["--arm", arm]}
    if header:
        server["header_from_env"] = {"name": "X-Golden", "value": "golden-header-value"}
    return ProviderExposure(
        provider_id=arm,
        tool_name=f"golden_{arm.replace('-', '_')}_search",
        mcp_server_name=PROVIDER_SERVER_NAMES[arm],
        mcp_server_config=server,
    )


def _cases(base: Path) -> list[tuple[str, HarnessRunConfig, str]]:
    gap_root, wheelhouse, gap_task = _gap_root(base)
    cases = []
    for harness in HOSTED:
        search = HarnessRunConfig(
            harness_id=harness,  # type: ignore[arg-type]
            provider_id="native",
            task_id="current-fact-lookup-v1",
            mode="live",
            model_id="golden-model",
            env={"SEW_GOLDEN_CELL": "1"},
        )
        for arm in SEARCH_ARMS:
            cases.append(
                (
                    f"{harness}/search/{arm}",
                    replace(
                        search,
                        provider_id=arm,
                        native_search_available=arm == "native",
                        external_provider=_exposure(arm),
                    ),
                    "account",
                )
            )
        cases.append(
            (
                f"{harness}/search/exa-header",
                replace(
                    search,
                    provider_id="exa",
                    native_search_available=False,
                    external_provider=_exposure("exa", header=True),
                ),
                "account",
            )
        )
        cases.append((f"{harness}/search/native-unpinned", replace(search, model_id=None), "account"))
        cases.append((f"{harness}/search/no-search-broker", replace(
            search, provider_id="no-search", native_search_available=False
        ), "broker"))
        code = replace(
            search,
            task_id=gap_task,
            workspace_profile=True,
            gap_module_root=gap_root,
            wheelhouse=wheelhouse,
            model_id="golden-model",
            env={
                "PATH": "/opt/golden/venv/bin:/usr/bin",
                "VIRTUAL_ENV": "/opt/golden/venv",
                "PIP_NO_INDEX": "1",
                "SEW_GOLDEN_CELL": "1",
            },
        )
        for arm in CODE_ARMS:
            cases.append(
                (
                    f"{harness}/code/{arm}",
                    replace(
                        code,
                        provider_id=arm,
                        native_search_available=arm == "native",
                        external_provider=_exposure(arm),
                    ),
                    "account",
                )
            )
    return cases


def _normalizer(*pairs: tuple[Path, str]):
    ordered = sorted(((str(path), token) for path, token in pairs), key=lambda p: -len(p[0]))

    def normalize(value: Any) -> Any:
        if isinstance(value, str):
            for raw, token in ordered:
                value = value.replace(raw, token)
            return value
        if isinstance(value, list | tuple):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {normalize(k): normalize(v) for k, v in value.items()}
        return value

    return normalize


def _source_env(base: Path) -> dict[str, str]:
    source_home = base / "source-codex-home"
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"golden": "account-login"}\n', encoding="utf-8")
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(base / "home"),
        "CODEX_HOME": str(source_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SEW_CLAUDE_CODE_BIN": "/opt/golden/bin/claude",
        "SEW_CODEX_BIN": "/opt/golden/bin/codex",
        "UNRELATED_SESSION_VAR": "must-not-reach-child",
    }


def _surface(base: Path, key: str, config: HarnessRunConfig, auth: str) -> dict[str, Any]:
    scratch = base / "cells" / key.replace("/", "__")
    scratch.mkdir(parents=True)
    source_env = _source_env(scratch.parent / (scratch.name + "-env"))
    normalize = _normalizer(
        (scratch, "<SCRATCH>"),
        (Path(source_env["CODEX_HOME"]), "<SOURCE_CODEX_HOME>"),
        (Path(source_env["HOME"]), "<HOME>"),
        (base / "module", "<GAP_ROOT>"),
        (base / "wheelhouse", "<WHEELHOUSE>"),
    )
    surface = prepare_arm_spawn(config, scratch, source_env, harness_auth=auth)
    spawn_config = replace(
        config,
        harness_args=tuple(config.harness_args) + surface.harness_args,
        env={**config.env, **surface.env},
    )
    binary = resolve_binary(config, source_env)
    argv = PROTOCOLS[config.harness_id].argv(binary, spawn_config, scratch / "last-message.txt")
    files = {}
    for path in sorted(p for p in scratch.rglob("*") if p.is_file()):
        files[str(path.relative_to(scratch))] = {
            "mode": oct(path.stat().st_mode & 0o777),
            "text": path.read_text(encoding="utf-8"),
        }
    return normalize(
        {
            "contract": surface.contract.as_record(),
            "argv": argv,
            "surface_env": dict(surface.env),
            "child_env": dict(sorted(child_environment(spawn_config, source_env).items())),
            "mcp_config_path": str(surface.mcp_config_path),
            "files": files,
        }
    )


def _refusals(base: Path) -> dict[str, list[str]]:
    gap_root, wheelhouse, gap_task = _gap_root(base / "refusals")
    result = {}
    for harness in HOSTED:
        for workspace in (False, True):
            config = HarnessRunConfig(
                harness_id=harness,  # type: ignore[arg-type]
                provider_id="no-search",
                task_id=gap_task if workspace else "current-fact-lookup-v1",
                mode="live",
                native_search_available=False,
                workspace_profile=workspace,
                gap_module_root=gap_root if workspace else None,
                wheelhouse=wheelhouse if workspace else None,
            )
            refused = []
            for index, flag in enumerate(FLAG_PROBES):
                scratch = base / "probe" / f"{harness}-{workspace}-{index}"
                scratch.mkdir(parents=True)
                try:
                    prepare_arm_spawn(replace(config, harness_args=(flag,)), scratch, {})
                except SchemaError as exc:
                    assert "may not be overridden" in str(exc), exc
                    refused.append(flag)
            result[f"{harness}/{'code' if workspace else 'search'}"] = refused
    return result


def build_golden(base: Path) -> dict[str, Any]:
    base.mkdir(parents=True, exist_ok=True)
    # File modes are part of the surface; pin the umask so they do not depend
    # on the shell that runs the suite.
    previous = os.umask(0o022)
    try:
        return {
            "generated_from": "searchlight e02eaa1 (pre-registry)",
            "surfaces": {
                key: _surface(base, key, config, auth) for key, config, auth in _cases(base)
            },
            "refused_harness_args": _refusals(base),
        }
    finally:
        os.umask(previous)


@pytest.fixture(scope="module")
def actual(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return build_golden(tmp_path_factory.mktemp("spawn-golden"))


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_covers_every_hosted_harness_and_arm(golden: dict[str, Any]) -> None:
    keys = set(golden["surfaces"])
    for harness in HOSTED:
        assert {f"{harness}/search/{arm}" for arm in SEARCH_ARMS} <= keys
        assert {f"{harness}/code/{arm}" for arm in CODE_ARMS} <= keys


def test_hosted_spawn_surfaces_are_byte_identical_to_e02eaa1(
    actual: dict[str, Any], golden: dict[str, Any]
) -> None:
    assert sorted(actual["surfaces"]) == sorted(golden["surfaces"])
    for key, expected in golden["surfaces"].items():
        assert actual["surfaces"][key] == expected, key
    # Byte identity of the serialized form, not just structural equality.
    assert json.dumps(actual["surfaces"], sort_keys=True) == json.dumps(
        golden["surfaces"], sort_keys=True
    )


def test_hosted_refused_harness_args_are_unchanged(
    actual: dict[str, Any], golden: dict[str, Any]
) -> None:
    assert actual["refused_harness_args"] == golden["refused_harness_args"]


if __name__ == "__main__":
    import tempfile

    if sys.argv[1:] != ["--write"]:
        raise SystemExit("usage: python3 tests/test_harness_spawn_golden.py --write")
    with tempfile.TemporaryDirectory(prefix="sew-spawn-golden-") as temporary:
        document = build_golden(Path(temporary))
    GOLDEN_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN_PATH.relative_to(ROOT)} ({len(document['surfaces'])} surfaces)")
