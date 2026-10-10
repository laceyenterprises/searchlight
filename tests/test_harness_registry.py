"""The harness registry (OHM-01): derived views, the literal guard, a third harness."""

from __future__ import annotations

import argparse
import ast
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from conftest import MODULE_ROOT
from test_runner import _read_index, _tiny_module

from sew import harnesses
from sew.arms import SpawnSurface, contract_for, prepare_arm_spawn
from sew.cli import build_parser
from sew.doctor import doctor
from sew.harness import HarnessRunConfig
from sew.live_harness import BIN_ENV, DEFAULT_BIN, PROTOCOLS, CodexProtocol, resolve_binary
from sew.runner import HOSTED_HARNESSES, SuiteRunner
from sew.schema import HARNESSES, SchemaError, load_suite_manifest, validate_fixture_run

REPO = MODULE_ROOT
SEW = REPO / "lib" / "python" / "sew"


def test_registry_derives_every_harness_table() -> None:
    assert list(HARNESSES) == ["claude-code", "codex", "pi", "fixture", "hermes", "opencode"]
    assert set(HOSTED_HARNESSES) == {"claude-code", "codex", "pi", "hermes", "opencode"}
    assert dict(BIN_ENV) == {"claude-code": "SEW_CLAUDE_CODE_BIN", "codex": "SEW_CODEX_BIN", "pi": "SEW_PI_BIN", "hermes": "SEW_HERMES_BIN", "opencode": "SEW_OPENCODE_BIN"}
    assert dict(DEFAULT_BIN) == {"claude-code": "claude", "codex": "codex", "pi": "pi", "hermes": "hermes", "opencode": "opencode"}
    assert {h: type(p).__name__ for h, p in PROTOCOLS.items()} == {
        "claude-code": "ClaudeCodeProtocol",
        "codex": "CodexProtocol",
        "pi": "PiProtocol",
        "hermes": "HermesProtocol",
        "opencode": "OpencodeProtocol",
    }
    assert PROTOCOLS["codex"] is PROTOCOLS["codex"]
    assert HARNESSES - {"fixture"} == {"claude-code", "codex", "pi", "hermes", "opencode"}


def test_every_registry_reference_resolves() -> None:
    for spec in harnesses.specs():
        for name in ("protocol", "arm_spawn", "usage_parser"):
            if getattr(spec, name) is not None:
                assert callable(spec.load(name)), (spec.id, name)


def _harness_options(parser: argparse.ArgumentParser, path: str = "sew") -> dict[str, argparse.Action]:
    found = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                found.update(_harness_options(child, f"{path} {name}"))
        elif "--harness" in action.option_strings:
            found[path] = action
    return found


def test_cli_harness_choices_are_the_live_harnesses() -> None:
    options = _harness_options(build_parser())
    assert sorted(options) == [
        "sew gap calibrate",
        "sew gap recalibrate",
        "sew gap run",
        "sew run-live-harness",
    ]
    for command, action in options.items():
        expected = ["claude-code", "codex", "pi", "hermes", "opencode"]
        assert list(action.choices) == expected


def test_registry_rejects_duplicates_and_malformed_specs() -> None:
    with pytest.raises(ValueError, match="already registered"):
        harnesses.register(harnesses.get("codex"))
    with pytest.raises(ValueError, match="must match"):
        harnesses.HarnessSpec(id="Bad_Id", label="bad")
    with pytest.raises(ValueError, match="missing"):
        harnesses.HarnessSpec(id="half", label="half", live=True, bin_env="SEW_HALF_BIN")
    with pytest.raises(ValueError, match="code_cell_sandbox"):
        harnesses.HarnessSpec(id="odd", label="odd", code_cell_sandbox="chroot")
    with pytest.raises(KeyError):
        harnesses.get("no-such-harness")


# --- Guard: no new per-harness literal comparisons outside the registry --------

HOSTED_LITERALS = frozenset({"claude-code", "codex"})
# The registry and the judges, which stay pinned to the two hosted harnesses.
GUARD_EXCLUDED = (
    "lib/python/sew/harnesses/",
    "lib/python/sew/gap/judges.py",
    "lib/python/sew/gap/brief_grade.py",
    "lib/python/sew/judge.py",
    "lib/python/sew/judge_transport.py",
)
# Comparisons that predate the registry. Each entry is (file, source) with a
# count; a new comparison, or another copy of one of these, fails the guard.
# Remove an entry when its site moves onto the registry.
GUARD_ALLOWLIST = Counter(
    {
        # Codex model resolution, the Claude Code broker credential handoff, and
        # the GAP code-cell qualification branch (srt for Claude Code, whose
        # source text test_gap_out_of_model.py pins).
        ("lib/python/sew/live_harness.py", 'config.harness_id == "codex"'): 1,
        ("lib/python/sew/live_harness.py", 'config.harness_id == "claude-code"'): 2,
        # GAP code-cell sandboxing and the egress canary, harness-specific by
        # construction until the code-cell work gives OSS harnesses a runner.
        ("lib/python/sew/gap/workspace.py", 'harness_id not in {"codex", "claude-code"}'): 2,
        ("lib/python/sew/gap/workspace.py", 'harness == "claude-code"'): 3,
        ("lib/python/sew/gap/workspace.py", 'harness_id == "codex"'): 2,
        ("lib/python/sew/gap/workspace.py", 'harness_id == "claude-code"'): 5,
        ("lib/python/sew/gap/calibrate.py", 'harness not in {"codex", "claude-code"}'): 1,
        # Published bake-off and site copy about the two hosted harnesses.
        ("lib/python/sew/bakeoff_report.py", 'row.harness_id == "codex"'): 4,
        ("lib/python/sew/bakeoff_report.py", 'row.harness_id == "claude-code"'): 1,
        ("scripts/build_site.py", "harness == 'claude-code'"): 1,
    }
)


def _names_hosted_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in HOSTED_LITERALS
    if isinstance(node, (ast.Set, ast.Tuple, ast.List, ast.Dict)):
        elements = node.keys if isinstance(node, ast.Dict) else node.elts
        return any(_names_hosted_literal(e) for e in elements if e is not None)
    return False


def hosted_literal_comparisons(path: Path, relpath: str) -> Counter:
    """(relpath, normalised source) for each comparison against a hosted harness id."""

    source = path.read_text(encoding="utf-8")
    found: Counter = Counter()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for index, op in enumerate(node.ops):
            left, right = operands[index], operands[index + 1]
            if isinstance(op, (ast.Eq, ast.NotEq)):
                hit = any(
                    isinstance(side, ast.Constant) and _names_hosted_literal(side)
                    for side in (left, right)
                )
            elif isinstance(op, (ast.In, ast.NotIn)):
                hit = not isinstance(right, ast.Constant) and _names_hosted_literal(right)
            else:
                hit = False
            if hit:
                segment = ast.get_source_segment(source, node) or ast.unparse(node)
                found[(relpath, " ".join(segment.split()))] += 1
                break
    return found


def _guarded_files() -> list[tuple[Path, str]]:
    paths = sorted(SEW.rglob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    files = []
    for path in paths:
        relpath = path.relative_to(REPO).as_posix()
        if not relpath.startswith(GUARD_EXCLUDED):
            files.append((path, relpath))
    return files


def test_no_new_hosted_harness_literal_comparisons() -> None:
    found: Counter = Counter()
    for path, relpath in _guarded_files():
        found += hosted_literal_comparisons(path, relpath)
    unexpected = found - GUARD_ALLOWLIST
    assert not unexpected, (
        "compare against the harness registry (sew.harnesses), not a hosted harness id: "
        f"{sorted(unexpected.elements())}"
    )
    stale = GUARD_ALLOWLIST - found
    assert not stale, f"drop these guard allowlist entries, the sites are gone: {sorted(stale)}"


@pytest.mark.parametrize(
    "snippet",
    [
        'if harness_id == "codex":\n    pass\n',
        'ok = "claude-code" != config.harness_id\n',
        'ok = cell.harness_id in ("claude-code", "codex")\n',
        'ok = h not in {"codex"}\n',
    ],
)
def test_guard_catches_new_comparisons(tmp_path: Path, snippet: str) -> None:
    path = tmp_path / "new.py"
    path.write_text(snippet, encoding="utf-8")
    assert sum(hosted_literal_comparisons(path, "new.py").values()) == 1


def test_guard_ignores_registry_reads(tmp_path: Path) -> None:
    path = tmp_path / "ok.py"
    path.write_text(
        'x = harnesses.get(h).code_cell_sandbox == "srt"\n'
        'y = h in HOSTED_HARNESSES\n'
        'z = "codex" in text\n',
        encoding="utf-8",
    )
    assert not hosted_literal_comparisons(path, "ok.py")


# --- A third harness, registered only for the test ---------------------------


class AcmeProtocol(CodexProtocol):
    harness_id = "acme"


WRITER_CALLS: list[str] = []


def acme_arm_spawn(config, contract, server_config, scratch, source_env, *, harness_auth="account"):
    WRITER_CALLS.append(config.provider_id)
    mcp_config = Path(scratch) / "acme-mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    return SpawnSurface(
        contract=contract,
        harness_args=("--acme-arm", contract.kind),
        env={"ACME_HOME": str(scratch)},
        mcp_config_path=mcp_config,
    )


@pytest.fixture
def acme():
    spec = harnesses.register(
        harnesses.HarnessSpec(
            id="acme",
            label="Acme",
            live=True,
            bin_env="SEW_ACME_BIN",
            default_bin="acme",
            protocol=AcmeProtocol,
            arm_spawn=acme_arm_spawn,
            forbidden_flags=frozenset({"--acme-tools"}),
            native_search=False,
            oss=True,
        )
    )
    WRITER_CALLS.clear()
    try:
        yield spec
    finally:
        harnesses.unregister("acme")


def test_third_harness_runs_end_to_end_through_the_fixture_path(acme, tmp_path: Path) -> None:
    assert "acme" in HARNESSES and "acme" in HOSTED_HARNESSES
    assert BIN_ENV["acme"] == "SEW_ACME_BIN" and DEFAULT_BIN["acme"] == "acme"
    assert isinstance(PROTOCOLS["acme"], AcmeProtocol)

    args = build_parser().parse_args(["run-live-harness", "--harness", "acme"])
    assert args.harness == "acme"

    module_base = _tiny_module(
        tmp_path, providers=["native", "exa", "brave"], harnesses={"acme": ["default"]}
    )
    suite = load_suite_manifest(module_base / "catalogs" / "tiny")
    assert list(suite["harnesses"]) == ["acme"]

    runner = SuiteRunner(module_base=module_base, state_root=tmp_path / "state")
    dry = runner.dry_run("tiny")
    reasons = {c["provider_id"]: c["not_applicable_reason"] for c in dry["cells"]}
    # Acme has no web search of its own, so the native arm is not applicable.
    assert reasons == {"native": "native_search_unavailable", "exa": None, "brave": None}

    summary = runner.run("tiny", run_id="acme-fixture", resume=False)
    assert summary["status_counts"] == {"succeeded": 2, "not_applicable": 1}
    index = _read_index(Path(summary["run_root"]))
    ran = [entry for entry in index if entry.get("run_dir")]
    assert len(ran) == 2
    for entry in ran:
        run_dir = Path(entry["run_dir"])
        assert validate_fixture_run(run_dir).status == "succeeded"
        assert json.loads((run_dir / "run.json").read_text())["harness_id"] == "acme"


def test_third_harness_dispatches_to_its_own_spawn_writer(acme, tmp_path: Path) -> None:
    config = HarnessRunConfig(
        harness_id="acme",  # type: ignore[arg-type]
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )
    surface = prepare_arm_spawn(config, tmp_path, {})
    assert WRITER_CALLS == ["no-search"]
    assert surface.harness_args == ("--acme-arm", contract_for(config).kind)
    assert resolve_binary(config, {"SEW_ACME_BIN": "/opt/acme"}) == "/opt/acme"

    refused = HarnessRunConfig(
        harness_id="acme",  # type: ignore[arg-type]
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
        harness_args=("--acme-tools", "web"),
    )
    with pytest.raises(SchemaError):
        prepare_arm_spawn(refused, tmp_path, {})


def test_doctor_checks_every_live_harness_binary(acme, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sew.gap.sandbox.qualify_backend", lambda *a, **kw: {})
    output = doctor({"SEW_MODE": "standalone", "HOME": str(tmp_path), "PATH": ""})
    line = next(line for line in output.splitlines() if line.startswith("harnesses"))
    assert "claude-code: CLI unavailable" in line
    assert "codex: CLI unavailable" in line
    assert "acme: CLI unavailable" in line


@pytest.mark.parametrize("env_source", ["process", "mapping"])
@pytest.mark.parametrize(
    "bin_env, override, executable",
    [
        (None, None, "acme"),
        (None, None, None),
        ("SEW_ACME_BIN", None, "acme"),
        ("SEW_ACME_BIN", "", "acme"),
        ("SEW_ACME_BIN", "custom-acme", "custom-acme"),
    ],
)
def test_doctor_optional_bin_env(tmp_path, monkeypatch, env_source, bin_env, override, executable):
    # Exercise doctor independently of the registry's current live-spec validation.
    spec = harnesses.HarnessSpec(id="acme", label="Acme", bin_env=bin_env, default_bin="acme")
    monkeypatch.setattr(harnesses, "specs", lambda **where: (spec,))
    monkeypatch.setattr("sew.gap.sandbox.qualify_backend", lambda *a, **kw: {})
    monkeypatch.chdir(tmp_path)
    if executable:
        binary = tmp_path / executable
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
    env = {"SEW_MODE": "standalone", "SEW_OSS_ENABLED": "0", "HOME": str(tmp_path), "PATH": str(tmp_path)}
    if override is not None:
        env[bin_env] = override
    if env_source == "process":
        for key in list(os.environ):
            monkeypatch.delenv(key)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        output = doctor()
    else:
        output = doctor(env)
    status = "CLI available; account login unverified" if executable and override != "" else "CLI unavailable"
    assert f"harnesses    acme: {status}" in output


def test_unregistered_harness_is_refused_everywhere(tmp_path: Path) -> None:
    assert "acme" not in HARNESSES and "acme" not in HOSTED_HARNESSES
    assert "acme" not in BIN_ENV and "acme" not in PROTOCOLS
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run-live-harness", "--harness", "acme"])
    module_base = _tiny_module(tmp_path, harnesses={"acme": ["default"]})
    with pytest.raises(SchemaError):
        load_suite_manifest(module_base / "catalogs" / "tiny")
    config = HarnessRunConfig(
        harness_id="acme",  # type: ignore[arg-type]
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )
    with pytest.raises(SchemaError, match="unsupported live harness"):
        prepare_arm_spawn(config, tmp_path, {})
