from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sew import live_harness
from sew.gap import workspace as gap_workspace
from sew.arms import (
    CODEX_DISABLED_FEATURES,
    PROVIDER_SERVER_NAMES,
    audit_transcript,
    prepare_arm_spawn,
)
from sew.gap.workspace import (
    CANARY_PROGRAM,
    EgressCanaryRefused,
    audit_workspace_calls,
    canary_command,
    capture_diff,
    prepare_workspace,
    require_canary,
    snapshot_tree,
    workspace_task,
)
from sew.harness import HarnessRunConfig, ProviderExposure
from sew.live_harness import CodexProtocol, ProcessOutcome, run_live_harness
from sew.schema import SchemaError

GOLDEN = Path(__file__).parent / "fixtures" / "gap"


@pytest.fixture
def gap_config(tmp_path):
    root = tmp_path / "module"
    shutil.copytree(GOLDEN, root / "catalogs" / "gap")
    task = json.loads((GOLDEN / "tasks.json").read_text())[0]
    cache = tmp_path / "cache"
    cache.mkdir()
    for pin in task["packages"]:
        name = pin["url"].rsplit("/", 1)[1]
        content = pin["version"].encode()
        (cache / name).write_bytes(content)
        pin["sha256"] = hashlib.sha256(content).hexdigest()
    (cache / "unrelated.whl").write_bytes(b"not task input")
    (root / "catalogs" / "gap" / "tasks.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "catalog_id": "gap-test",
                "authoring_policy": "synthetic",
                "tasks": [task],
            }
        )
    )
    return HarnessRunConfig(
        harness_id="codex",
        provider_id="no-search",
        native_search_available=False,
        task_id=task["id"],
        mode="live",
        workspace_profile=True,
        gap_module_root=root,
        wheelhouse=cache,
        harness_auth="account",
        model_id="fake",
    )


def config_for(config, harness, arm):
    provider = None
    if arm in PROVIDER_SERVER_NAMES:
        provider = ProviderExposure(
            provider_id=arm,
            tool_name="mcp__" + arm + "__search",
            mcp_server_config={"command": "test-provider", "args": []},
        )
    return replace(
        config,
        harness_id=harness,
        provider_id=arm,
        native_search_available=arm == "native",
        external_provider=provider,
    )


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("arm", ["no-search", "floor", "ceiling", "native", *PROVIDER_SERVER_NAMES])
def test_workspace_surfaces(gap_config, tmp_path, harness, arm):
    config = config_for(gap_config, harness, arm)
    surface = prepare_arm_spawn(config, tmp_path, {})
    assert surface.contract.workspace_profile
    assert surface.contract.native_search == (arm == "native")
    assert len(surface.contract.allowed_mcp_servers) == (arm in PROVIDER_SERVER_NAMES)
    args = surface.harness_args
    if harness == "claude-code":
        tools = set(args[args.index("--tools") + 1].split(","))
        assert tools & {"Read", "Edit", "Write", "Bash"} == {"Read", "Edit", "Write", "Bash"}
        assert tools - {"Read", "Edit", "Write", "Bash"} == (
            {"WebSearch", "WebFetch"}
            if arm == "native"
            else {"ToolSearch"}
            if arm in PROVIDER_SERVER_NAMES
            else set()
        )
        allowed = set(args[args.index("--allowedTools") + 1].split(","))
        assert allowed - {"Read", "Edit", "Write", "Bash"} == (
            {"WebSearch", "WebFetch"}
            if arm == "native"
            else {f"mcp__{surface.contract.mcp_server_name}__*"}
            if arm in PROVIDER_SERVER_NAMES
            else set()
        )
        servers = json.loads(surface.mcp_config_path.read_text())["mcpServers"]
        assert set(servers) == set(surface.contract.allowed_mcp_servers)
        settings = json.loads(Path(args[args.index("--settings") + 1]).read_text())
        sandbox = settings["sandbox"]
        assert sandbox["enabled"] and sandbox["failIfUnavailable"]
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["network"]["allowedDomains"] == []
        assert sandbox["network"]["deniedDomains"] == ["*"]
        assert args[args.index("--setting-sources") + 1] == ""
    else:
        disabled = set(args[1::2])
        assert disabled == set(CODEX_DISABLED_FEATURES) - {"shell_tool", "unified_exec"}
        doc = tomllib.loads(surface.mcp_config_path.read_text())
        assert doc["sandbox_workspace_write"]["network_access"] is False
        assert doc["approval_policy"] == "never"
        assert doc["web_search"] == ("live" if arm == "native" else "disabled")
        assert len(doc.get("mcp_servers", {})) == (arm in PROVIDER_SERVER_NAMES)
        argv = CodexProtocol().argv("codex", config, tmp_path / "last")
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "override",
    [
        "--sandbox=danger-full-access",
        "--settings=leak.json",
        "--dangerously-skip-permissions",
        "--yolo",
        "--add-dir=/tmp",
        "--permission-profile=unsafe",
    ],
)
def test_workspace_refuses_policy_overrides(gap_config, tmp_path, harness, override):
    with pytest.raises(SchemaError, match="overridden"):
        prepare_arm_spawn(
            replace(gap_config, harness_id=harness, harness_args=(override,)), tmp_path, {}
        )


def test_offline_wheels_and_fresh_copies(gap_config, tmp_path):
    root, task = workspace_task(gap_config)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    pristine, workspace, env = prepare_workspace(gap_config, scratch, root, task)
    assert isinstance(pristine, dict)
    assert env["PIP_NO_INDEX"] == "1"
    assert env["PIP_CONFIG_FILE"] == "/dev/null"
    assert {p.name for p in Path(env["PIP_FIND_LINKS"]).iterdir()} == {
        p["url"].rsplit("/", 1)[1] for p in task["packages"] if p["role"] != "new"
    }
    assert not (workspace / "hidden").exists()
    (workspace / "agent-created.py").write_text("changed")
    other = tmp_path / "other"
    other.mkdir()
    _, next_workspace, _ = prepare_workspace(gap_config, other, root, task)
    assert not (next_workspace / "agent-created.py").exists()
    assert not (root / "catalogs/gap/fixture/agent-created.py").exists()


@pytest.mark.parametrize("failure", ["missing", "hash", "symlink", "no-cache"])
def test_bad_wheel_cache_refuses(gap_config, tmp_path, failure):
    root, task = workspace_task(gap_config)
    wheel = gap_config.wheelhouse / task["packages"][0]["url"].rsplit("/", 1)[1]
    if failure == "missing":
        wheel.unlink()
    elif failure == "hash":
        wheel.write_bytes(b"tampered")
    elif failure == "symlink":
        wheel.unlink()
        wheel.symlink_to(gap_config.wheelhouse / "unrelated.whl")
    else:
        gap_config = replace(gap_config, wheelhouse=None)
    (tmp_path / "scratch").mkdir()
    with pytest.raises(SchemaError, match="wheelhouse"):
        prepare_workspace(gap_config, tmp_path / "scratch", root, task)


@pytest.mark.parametrize(
    "command,contaminated",
    [
        ("curl https://example.org", True),
        ("wget example.org", True),
        ("python -m pip download x", True),
        ("pip install x --index-url https://pypi.org/simple", True),
        ("PIP_NO_INDEX=0 pip install x", True),
        ("PIP_NO_INDEX= pip install x", True),
        ('PIP_NO_INDEX="" pip install x', True),
        ("PIP_NO_INDEX='' python -m pip install x", True),
        ("PIP_NO_INDEX='false' pip install x", True),
        ("pip config set global.no-index false", True),
        ("python -m pip config --user set global.no-index 0", True),
        ("pip config unset global.no-index", True),
        ("pip config set global.no-index true", False),
        ("PIP_NO_INDEX=1 pip install x", False),
        ('echo "PIP_NO_INDEX= pip install x"', False),
        ("unset PIP_NO_INDEX; pip install x", True),
        ("pip --isolated install x", True),
        ("git clone /tmp/other", True),
        ("git -C repo fetch origin", True),
        ("git pull", True),
        ("npm view x", True),
        ("npm install x", True),
        ("dig example.org", True),
        ('python -c "import urllib.request; urllib.request.urlopen(url)"', True),
        ('python -c "import socket; socket.getaddrinfo(host, 80)"', True),
        ("python -m pip install x", False),
        ("pip install --no-index --find-links /tmp/wheels x", True),
        ("git diff", False),
        ("npm test", False),
        ("python -m pytest tests", False),
        ("rg curl README.md", False),
        ("echo curl", False),
        ("cat curl.sh", False),
        ("rg https://example.org README.md", False),
    ],
)
@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_shell_network_audit(command, contaminated, harness):
    event = (
        {"type": "tool_use", "name": "Bash", "input": {"command": command}}
        if harness == "claude-code"
        else {"type": "item.completed", "item": {"type": "command_execution", "command": command}}
    )
    assert bool(audit_workspace_calls([event])) == contaminated
    # Prose and tool results mentioning commands are never tool requests.
    assert not audit_workspace_calls([{"type": "result", "result": command}])


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("target", ["hidden", "module", "relative", "symlink", "embedded"])
def test_forbidden_reads(gap_config, tmp_path, harness, target):
    root = gap_config.gap_module_root
    hidden = root / "catalogs/gap/hidden"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alias").symlink_to(hidden, target_is_directory=True)
    path = {
        "hidden": str(hidden / "tests.py"),
        "module": str(root / "lib/internal.py"),
        "relative": "../module/catalogs/gap/hidden/tests.py",
        "symlink": "alias/tests.py",
        "embedded": f"python -c \"open('{hidden}/tests.py').read()\"",
    }[target]
    name = "Read" if harness == "claude-code" and target != "embedded" else "Bash"
    payload = (
        {"file_path": path}
        if name == "Read"
        else {"command": path if target == "embedded" else f"cat {path}"}
    )
    event = {"type": "tool_use", "name": name, "input": payload}
    assert audit_workspace_calls([event], cwd=workspace, forbidden_paths=(root, hidden)) == [
        "workspace:forbidden-read"
    ]
    safe = {
        "type": "tool_use",
        "name": name,
        "input": {"file_path": str(workspace / "tests.py"), "command": "cat tests.py"},
    }
    assert not audit_workspace_calls([safe], cwd=workspace, forbidden_paths=(root, hidden))


@pytest.mark.parametrize("arm", ["no-search", "native", *PROVIDER_SERVER_NAMES])
def test_workspace_search_audit(gap_config, tmp_path, arm):
    contract = prepare_arm_spawn(config_for(gap_config, "codex", arm), tmp_path, {}).contract
    allowed = (
        "web_search"
        if arm == "native"
        else (
            f"mcp__{PROVIDER_SERVER_NAMES[arm]}__search" if arm in PROVIDER_SERVER_NAMES else "Read"
        )
    )
    assert not audit_transcript(contract, [{"type": "tool_use", "name": allowed}]).contaminated
    outside = "web_search" if arm != "native" else "mcp__exa__search"
    assert audit_transcript(contract, [{"type": "tool_use", "name": outside}]).contaminated


def canary_events(harness, nonce, command, probes=None):
    probes = probes or {
        name: {"exit_code": 1, "error": "Operation not permitted"}
        for name in ("curl", "pip", "https")
    }
    output = "GAP_CANARY:" + json.dumps({"nonce": nonce, "probes": probes})
    if harness == "claude-code":
        hosts = {"curl": nonce + ".example.com", "pip": "pypi.org", "https": nonce + ".example.org"}
        output += (
            "\n<sandbox_violations>\n"
            + "\n".join(
                f"deny network-outbound {hosts[name]}:443 (host is on the deny list)"
                for name, probe in probes.items()
                if name in hosts and probe["error"] == "Operation not permitted"
            )
            + "\n</sandbox_violations>"
        )
    if harness == "codex":
        return [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "aggregated_output": output,
                },
            }
        ]
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": command}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": output}]
            },
        },
    ]


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("shell,flag", [("/bin/sh", "-c"), ("/bin/zsh", "-lc")])
def test_canary_denial_evidence(harness, shell, flag):
    nonce, command = canary_command(harness)
    assert (
        require_canary(canary_events(harness, nonce, command), nonce, command, harness_id=harness)[
            "nonce"
        ]
        == nonce
    )
    wrapped = shlex.join([shell, flag, command])
    assert require_canary(
        canary_events(harness, nonce, wrapped), nonce, command, harness_id=harness
    )
    altered = shlex.join([shell, flag, command + "; echo changed"])
    with pytest.raises(EgressCanaryRefused):
        require_canary(canary_events(harness, nonce, altered), nonce, command, harness_id=harness)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "failure",
    [
        "curl",
        "pip",
        "https",
        "missing",
        "timeout",
        "missing-tool",
        "claimed-only",
        "wrong-command",
        "wrong-nonce",
        "duplicate",
    ],
)
def test_canary_fails_closed(harness, failure):
    nonce, command = canary_command(harness)
    probes = {
        name: {"exit_code": 1, "error": "Operation not permitted"}
        for name in ("curl", "pip", "https")
    }
    if failure in probes:
        probes[failure] = {"exit_code": 0, "error": ""}
    if failure == "missing":
        del probes["https"]
    if failure == "timeout":
        probes["curl"]["error"] = "Connection timed out"
    if failure == "missing-tool":
        probes["curl"]["exit_code"] = None
        probes["curl"]["error"] = "No such file or directory"
    events = canary_events(harness, nonce, command, probes)
    if failure == "claimed-only":
        events = [{"type": "result", "result": events[-1]}]
    if failure == "wrong-command":
        events = canary_events(harness, nonce, "echo forged", probes)
    if failure == "wrong-nonce":
        events = canary_events(harness, "wrong", command, probes)
    if failure == "duplicate":
        events += copy.deepcopy(events)
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id=harness)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_canary_program_executes_each_probe(monkeypatch, capsys, harness):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")

    monkeypatch.setattr(subprocess, "run", run)

    monkeypatch.setattr(sys, "argv", ["probe", harness, "nonce"])
    exec(CANARY_PROGRAM, {})
    output = capsys.readouterr().out
    assert len(calls) == 3
    assert calls[0][0] == "curl"
    assert "download" in calls[1] and "--isolated" in calls[1]
    assert calls[2][0:2] == [sys.executable, "-c"]
    assert calls[0][-1] == (
        "https://nonce.example.com/" if harness == "claude-code" else "https://1.1.1.1/"
    )
    assert calls[2][-1] == (
        "https://nonce.example.org/" if harness == "claude-code" else "https://1.1.1.1/"
    )
    assert set(json.loads(output.removeprefix("GAP_CANARY:"))["probes"]) == {
        "curl",
        "pip",
        "https",
    }


@pytest.fixture
def umask_022():
    # git apply creates files as 0666/0777 minus the umask, while the test sets
    # 0o755 by hand; under a group-writable umask (002) the modes differed.
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def test_binary_patch_roundtrip_and_baseline_tampering(tmp_path, umask_022):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    (pristine / "app.py").write_text("old\n")
    (pristine / "delete.txt").write_text("gone\n")
    (pristine / ".gitignore").write_text("ignored.txt\n")
    (pristine / "binary.dat").write_bytes(b"\0old")
    frozen = snapshot_tree(pristine)
    workspace = tmp_path / "workspace"
    shutil.copytree(pristine, workspace)
    (workspace / "app.py").write_text("new\n")
    (workspace / "app.py").chmod(0o755)
    (workspace / "delete.txt").unlink()
    (workspace / "ignored.txt").write_text("added\n")
    (workspace / "binary.dat").write_bytes(b"\0new")
    (workspace / "space name.txt").write_text("no newline")
    (workspace / "link").symlink_to("app.py")
    (pristine / "app.py").write_text("tampered by shell\n")
    patch = tmp_path / "workspace.diff"
    capture_diff(frozen, workspace, patch)
    assert b"tampered by shell" not in patch.read_bytes()
    restored = tmp_path / "restored"
    restored.mkdir()
    from sew.gap.workspace import materialize_tree

    materialize_tree(frozen, restored)
    subprocess.run(["git", "apply", "--binary", str(patch)], cwd=restored, check=True)
    assert snapshot_tree(restored) == snapshot_tree(workspace)


@pytest.mark.parametrize("unsafe", ["escaping-link", ".git", "oversized"])
def test_diff_refuses_invalid_output(tmp_path, unsafe):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if unsafe == "escaping-link":
        (workspace / "leak").symlink_to(tmp_path)
    elif unsafe == ".git":
        (workspace / ".git").mkdir()
    else:
        (workspace / "huge.txt").write_text("x" * 200000)
    with pytest.raises(SchemaError):
        capture_diff(pristine, workspace, tmp_path / "patch")


@pytest.mark.parametrize("failure", ["file-size", "total-size", "entries", "directories"])
@pytest.mark.parametrize("stage", ["fixture", "final"])
def test_snapshot_resource_limits(tmp_path, monkeypatch, failure, stage):
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_FILE_BYTES", 8, raising=False)
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_TOTAL_BYTES", 12, raising=False)
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_ENTRIES", 3, raising=False)
    tree = tmp_path / "tree"
    tree.mkdir()
    if failure == "file-size":
        (tree / "large").write_bytes(b"x" * 9)
    elif failure == "total-size":
        for name in ("one", "two"):
            (tree / name).write_bytes(b"x" * 7)
    else:
        for index in range(4):
            path = tree / str(index)
            if failure == "directories":
                path.mkdir()
            else:
                path.touch()
    target = tmp_path / "patch"
    with pytest.raises(SchemaError, match="workspace.*(?:size|entries).*cap"):
        if stage == "fixture":
            snapshot_tree(tree)
        else:
            capture_diff({}, tree, target)
    assert not target.exists()


def test_snapshot_allows_exact_resource_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_FILE_BYTES", 8, raising=False)
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_TOTAL_BYTES", 12, raising=False)
    monkeypatch.setattr(gap_workspace, "MAX_WORKSPACE_ENTRIES", 3, raising=False)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "one").write_bytes(b"x" * 8)
    (tree / "two").write_bytes(b"y" * 4)
    (tree / "directory").mkdir()
    snapshot = snapshot_tree(tree)
    assert snapshot["one"][1] == b"x" * 8
    assert snapshot["two"][1] == b"y" * 4
    assert len(snapshot) == 3


@pytest.mark.parametrize("limit", ["MAX_WORKSPACE_FILE_BYTES", "MAX_WORKSPACE_TOTAL_BYTES"])
def test_snapshot_rechecks_bytes_after_preflight(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(gap_workspace, limit, 8, raising=False)
    tree = tmp_path / "tree"
    tree.mkdir()
    path = tree / "growing"
    path.write_bytes(b"safe")
    original_check = gap_workspace.check_tree

    def check_then_grow(tree):
        paths = original_check(tree)
        path.write_bytes(b"x" * 9)
        return paths

    monkeypatch.setattr(gap_workspace, "check_tree", check_then_grow)
    with pytest.raises(SchemaError, match="workspace.*size.*cap"):
        snapshot_tree(tree)


def test_snapshot_refuses_large_sparse_file_before_reading(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    with (tree / "large").open("wb") as source:
        source.truncate(1024**3)

    def refuse_read(*args, **kwargs):
        pytest.fail("oversized files must be refused before opening them")

    monkeypatch.setattr(Path, "open", refuse_read)
    with pytest.raises(SchemaError, match="workspace file size exceeds cap"):
        snapshot_tree(tree)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "contamination", [None, "shell", "hidden", "search", "diff-secret", "diff-oversized"]
)
def test_live_workspace_bundle(gap_config, tmp_path, monkeypatch, harness, contamination):
    write_json = live_harness._write_json

    def write_artifact(path, payload):
        if harness == "claude-code" and path.name == "egress-canary.json":
            pytest.fail("Claude qualification owns the canary artifact write")
        return write_json(path, payload)

    monkeypatch.setattr(live_harness, "_write_json", write_artifact)
    requirements = tmp_path / "inherited-requirements.txt"
    requirements.write_text("--find-links https://example.org/wheels/\n")
    inherited_pip = {
        "PIP_REQUIREMENT": str(requirements),
        "PIP_CONSTRAINT": str(requirements),
        "PIP_BUILD_CONSTRAINT": str(requirements),
        "PIP_INDEX_URL": "https://example.org/simple/",
        "PIP_EXTRA_INDEX_URL": "https://example.org/simple/",
        "PIP_FIND_LINKS": "https://example.org/wheels/",
        "PIP_FUTURE_SOURCE_SETTING": str(requirements),
    }
    config = replace(
        gap_config,
        harness_id=harness,
        env={**inherited_pip, "PIP_NO_INDEX": "0", "CELL_SETTING": "retained"},
        max_total_tokens=99999,
        max_provider_calls=999,
    )
    spawned = []

    def spawn(argv, *, prompt, cwd, env, **kwargs):
        pip_keys = {key for key in env if key.startswith("PIP_")}
        assert pip_keys == gap_workspace.CELL_PIP_ENV_ALLOWLIST
        assert env["PIP_FIND_LINKS"] == str(cwd.parent / "wheelhouse")
        assert env["CELL_SETTING"] == "retained"
        spawned.append((argv, prompt, cwd, dict(env), kwargs))
        if harness == "codex" and len(spawned) == 1:
            command = prompt.split("\n", 1)[1]
            assert shlex.split(command)[-2] == harness
            nonce = shlex.split(command)[-1]
            events = canary_events(harness, nonce, command)
        else:
            if config.provider_id in {"floor", "ceiling"}:
                from sew.gap.reference import resolve_gap_task, reference_prompt

                _, task = resolve_gap_task(config)
                assert prompt == reference_prompt(config.provider_id, task)
            assert env["PIP_NO_INDEX"] == "1"
            assert cwd == spawned[0][2]
            assert not (cwd / "hidden").exists()
            assert kwargs["limits"].max_total_tokens == 10000
            assert kwargs["limits"].max_provider_calls == 5
            assert kwargs["limits"].timeout_seconds == 120
            (cwd / "solution.py").write_text('print("fixed")\n')
            events = []
            if contamination == "diff-secret":
                (cwd / "leak.txt").write_text("Authorization: Bearer fixture-secret123")
            elif contamination == "diff-oversized":
                (cwd / "huge.txt").write_text("x" * 200000)
            elif contamination:
                call = {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "curl https://example.org"},
                }
                if contamination == "hidden":
                    call = {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {
                            "file_path": str(config.gap_module_root / "catalogs/gap/hidden/test.py")
                        },
                    }
                elif contamination == "search":
                    call = {"type": "tool_use", "name": "WebSearch", "input": {"query": "leak"}}
                events.append(call)
            if harness == "claude-code":
                events.append(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "Updated code.",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    }
                )
            else:
                events.extend(
                    [
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": "Updated code."},
                        },
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        },
                    ]
                )
        return ProcessOutcome(
            events=[{"event": event, "received_at": "2026-09-30T19:00:00Z"} for event in events],
            ready=True,
            exit_code=0,
            stderr_tail="Harness diagnostic.\n",
        )

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    result = run_live_harness(
        config, tmp_path / "runs", environ={**inherited_pip, "SEW_HARNESS_LIVE": "1"}
    )
    diff_refused = contamination in {"diff-secret", "diff-oversized"}
    assert result.status == (
        "failed" if diff_refused else "contaminated" if contamination else "succeeded"
    )
    assert len(spawned) == (1 if harness == "claude-code" else 2)
    patch = result.bundle_dir / "artifacts/workspace.diff"
    if diff_refused:
        assert result.failure_category == "workspace_diff_refused"
        assert not patch.exists()
        transcript = json.loads((result.bundle_dir / "artifacts/transcript.json").read_text())
        assert "Updated code." in json.dumps(transcript)
        stderr = (result.bundle_dir / "artifacts/harness-stderr.txt").read_text()
        assert "Harness diagnostic." in stderr
        assert "GAP workspace diff refused:" in stderr
        assert "fixture-secret123" not in stderr
        evaluation = json.loads((result.bundle_dir / "evaluations/evaluation.json").read_text())
        assert not evaluation["dimensions"]["completed"]
        assert "workspace_diff_refused" in evaluation["failure_reasons"]
    else:
        assert "solution.py" in patch.read_text()
    assert not spawned[-1][2].exists()  # fresh cwd is removed after capture
    record = json.loads((result.bundle_dir / "run.json").read_text())
    assert record["task_source"] == "gap"
    metadata = json.loads((result.bundle_dir / "artifacts/spawn-metadata.json").read_text())
    assert metadata["arm_contract"]["workspace_profile"]
    if config.provider_id in {"floor", "ceiling"}:
        from sew.gap.reference import resolve_gap_task

        _, task = resolve_gap_task(config)
        assert metadata["arm_contract"]["reference_arm"] == config.provider_id
        assert metadata["arm_contract"]["oracle_excerpt_sha256"] == task["oracle"]["sha256"]
    canary = json.loads((result.bundle_dir / "artifacts/egress-canary.json").read_text())
    assert len(spawned) == (1 if harness == "claude-code" else 2)
    assert spawned[0][0] == spawned[-1][0]
    assert spawned[0][0][0] != "/usr/bin/sandbox-exec"
    assert canary["direct_egress"]["attribution"] == (
        "harness-runner-permission:srt"
        if harness == "claude-code"
        else "harness-runner-permission:codex-sandbox"
    )
    assert canary["harness_id"] == harness
    assert set(canary["probes"]) == {"curl", "pip", "https"}
    bundle = yaml.safe_load((result.bundle_dir / "evidence/bundle.yaml").read_text())
    artifact_paths = {entry["path"] for entry in bundle["artifacts"]}
    assert "artifacts/egress-canary.json" in artifact_paths
    assert ("artifacts/workspace.diff" in artifact_paths) == (not diff_refused)
    if harness == "codex":
        codex_home = Path(spawned[-1][3]["CODEX_HOME"])
        # The generated isolated home is gone with the cell, while spawn env
        # shows the offline install policy was carried into the harness.
        assert not codex_home.exists()


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("failure", ["egress", "missing-evidence", "timeout", "boot-failure"])
def test_live_canary_refuses_before_task(gap_config, tmp_path, monkeypatch, harness, failure):
    calls = []

    def spawn(argv, *, prompt, **kwargs):
        calls.append(prompt)
        assert len(calls) == (0 if harness == "claude-code" else 1), (
            "GAP task must never start after failed canary"
        )
        command = prompt.split("\n", 1)[1]
        nonce = shlex.split(command)[-1]
        probes = {
            name: {"exit_code": 1, "error": "Operation not permitted"}
            for name in ("curl", "pip", "https")
        }
        if failure == "egress":
            probes["https"] = {
                "exit_code": 0,
                "error": "",
            }
        events = (
            [] if failure == "missing-evidence" else canary_events(harness, nonce, command, probes)
        )
        return ProcessOutcome(
            events=[{"event": event, "received_at": "2026-09-30T19:00:00Z"} for event in events],
            ready=failure != "boot-failure",
            exit_code=0,
            timed_out=failure == "timeout",
        )

    if harness == "claude-code":

        def refuse(*a, **kw):
            raise EgressCanaryRefused("fixture out-of-model refusal: " + failure)

        monkeypatch.setattr(gap_workspace, "qualify_claude_code", refuse)
    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    with pytest.raises(EgressCanaryRefused):
        run_live_harness(
            replace(gap_config, harness_id=harness),
            tmp_path / "runs",
            environ={"SEW_HARNESS_LIVE": "1"},
        )
    assert len(calls) == (0 if harness == "claude-code" else 1)
    assert not list((tmp_path / "runs").rglob("run.json"))


@pytest.mark.parametrize("wrong", ["unknown", "production", "brief", "prompt"])
def test_workspace_requires_catalog_code_task(gap_config, wrong):
    if wrong == "unknown":
        gap_config = replace(gap_config, task_id="no-such-task")
    elif wrong == "production":
        gap_config = replace(gap_config, task_id="current-fact-lookup-v1")
    elif wrong == "brief":
        task = json.loads((GOLDEN / "tasks.json").read_text())[5]
        root = gap_config.gap_module_root
        (root / "catalogs/gap/tasks.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "catalog_id": "brief",
                    "authoring_policy": "test",
                    "tasks": [task],
                }
            )
        )
        gap_config = replace(gap_config, task_id=task["id"])
    else:
        gap_config = replace(gap_config, prompt_text="Custom prompt")
    with pytest.raises(SchemaError):
        workspace_task(gap_config)


def test_codex_offline_env_is_explicit_in_shell_policy(gap_config, tmp_path):
    config = replace(gap_config, env={"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "/verified/wheels"})
    surface = prepare_arm_spawn(config, tmp_path, {})
    doc = tomllib.loads(surface.mcp_config_path.read_text())
    assert doc["shell_environment_policy"]["set"] == config.env


def test_workspace_audit_does_not_change_production(gap_config, tmp_path):
    production = prepare_arm_spawn(
        replace(gap_config, workspace_profile=False), tmp_path, {}
    ).contract
    event = {"type": "tool_use", "name": "Bash", "input": {"command": "curl https://example.org"}}
    assert not audit_transcript(production, [event]).contaminated
    args = CodexProtocol().argv(
        "codex", replace(gap_config, workspace_profile=False), tmp_path / "last"
    )
    assert args[args.index("--sandbox") + 1] == "read-only"


def test_per_cell_python_environment_is_offline(tmp_path):
    from sew.gap.workspace import prepare_python_environment

    env = prepare_python_environment(
        tmp_path, {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": "/wheels"}, {"PATH": "/bin"}
    )
    assert env["PATH"].startswith(str(tmp_path / "venv/bin") + ":")
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1"
    result = subprocess.run(
        [
            str(tmp_path / "venv/bin/python"),
            "-c",
            "import sys; print(sys.prefix); print(sys.base_prefix)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == env["VIRTUAL_ENV"]
    assert lines[0] != lines[1]
    assert not (tmp_path / "workspace").exists()


def test_canary_uses_cell_interpreter_from_path(tmp_path, monkeypatch):
    from sew.gap import workspace

    env = workspace.prepare_python_environment(tmp_path, {}, {"PATH": "/bin"})
    # Probe interpreter selection without opening any network connections.
    program = "import sys; print(sys.prefix)"
    monkeypatch.setattr(workspace, "CANARY_PROGRAM", program)
    monkeypatch.setattr(sys, "executable", "/host-only/python3")
    nonce, command = canary_command("codex")
    assert shlex.split(command) == ["python3", "-c", program, "codex", nonce]
    result = subprocess.run(
        command, shell=True, env=env, check=True, capture_output=True, text=True
    )
    assert result.stdout.strip() == env["VIRTUAL_ENV"]


@pytest.mark.parametrize(
    "secret",
    ["Authorization: Bearer fixture-secret123", "Cookie: session=fixture", "known-broker-value"],
)
def test_patch_refuses_secrets_before_writing(tmp_path, secret):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "leak.txt").write_text(secret)
    target = tmp_path / "patch"
    with pytest.raises(SchemaError, match="credential|secret"):
        capture_diff(pristine, workspace, target, forbidden_values=["known-broker-value"])
    assert not target.exists()


@pytest.mark.parametrize("value", ["", "1", "false", "12345678"])
def test_patch_ignores_short_environment_values(tmp_path, value):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "settings.txt").write_text(f"setting = {value}\n")
    target = tmp_path / "patch"
    capture_diff(pristine, workspace, target, forbidden_values=[value])
    assert target.exists()


def test_canary_refuses_extra_calls():
    nonce, command = canary_command("claude-code")
    events = canary_events("claude-code", nonce, command)
    events.insert(0, {"type": "tool_use", "name": "Bash", "input": {"command": "echo forged"}})
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="claude-code")


@pytest.mark.parametrize(
    "command,contaminated",
    [
        ('bash -lc "curl https://example.org"', True),
        ("/usr/bin/curl --help", True),
        ('echo "curl https://example.org"', False),
        ("python -m pip --isolated install x", True),
        ("uv pip install x", True),
        ("uv pip install --offline x", False),
        ("git status", False),
    ],
)
def test_network_command_wrappers(command, contaminated):
    from sew.gap.workspace import shell_network_attempt

    assert shell_network_attempt(command) == contaminated


def test_workspace_profile_type_and_fixture_refusal(gap_config):
    with pytest.raises(SchemaError, match="boolean"):
        replace(gap_config, workspace_profile="yes")
    with pytest.raises(SchemaError, match="live mode"):
        replace(gap_config, mode="fixture")


@pytest.mark.parametrize("tool_type", ["mcp_tool_call", "web_search", "web_search_call"])
def test_canary_refuses_codex_extra_search(tool_type):
    nonce, command = canary_command("codex")
    events = canary_events("codex", nonce, command)
    events.insert(0, {"type": "item.completed", "item": {"type": tool_type, "tool": "search"}})
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="codex")


def test_failed_canary_evidence_is_retained_and_scrubbed(gap_config, tmp_path, monkeypatch):
    secret = "synthetic-credential-for-test"

    def spawn(argv, *, prompt, **kwargs):
        return ProcessOutcome(ready=False, exit_code=1, stderr_tail=secret)

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    with pytest.raises(EgressCanaryRefused):
        run_live_harness(
            replace(gap_config, env={"TEST_API_KEY": secret}),
            tmp_path / "runs",
            environ={"SEW_HARNESS_LIVE": "1"},
        )
    evidence = next((tmp_path / "runs").rglob("egress-canary.json"))
    assert secret not in evidence.read_text()
    assert json.loads(evidence.read_text())["admissible"] is False


def test_contract_resolver_preserves_workspace_profile(gap_config):
    from sew.arms import contract_for

    contract = contract_for(gap_config)
    assert contract.workspace_profile
    assert audit_transcript(
        contract,
        [{"type": "tool_use", "name": "Bash", "input": {"command": "curl https://example.org"}}],
    ).contaminated


@pytest.mark.parametrize(
    "command",
    [
        "pip install x -ihttps://pypi.org/simple",
        "python -I -m pip install x --index-url=https://pypi.org/simple",
        "pip install -qvi /tmp/my_index malicious",
        "python -m pip install -qvi/tmp/my_index malicious",
    ],
)
def test_pip_index_option_forms_are_contamination(command):
    from sew.gap.workspace import shell_network_attempt

    assert shell_network_attempt(command)


def test_patch_refuses_invalid_utf8_without_partial_artifact(tmp_path):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "latin.txt").write_bytes(b"latin \xff")
    target = tmp_path / "patch"
    with pytest.raises(SchemaError, match="UTF-8"):
        capture_diff(pristine, workspace, target)
    assert not target.exists()


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("valid_neighbor", [None, "before", "after"])
@pytest.mark.parametrize(
    "malformed",
    [
        "record-type",
        "missing-nonce",
        "missing-probes",
        "probe-map-type",
        "probe-type",
        "missing-exit-code",
        "missing-error",
        "invalid-json",
    ],
)
def test_canary_rejects_malformed_evidence(harness, valid_neighbor, malformed):
    nonce, command = canary_command(harness)
    events = canary_events(harness, nonce, command)
    result = events[0]["item"] if harness == "codex" else events[-1]["message"]["content"][0]
    output_key = "aggregated_output" if harness == "codex" else "content"
    valid_line, *remaining = result[output_key].splitlines()
    record = json.loads(valid_line.removeprefix("GAP_CANARY:"))
    if malformed == "record-type":
        record = []
    elif malformed == "missing-nonce":
        del record["nonce"]
    elif malformed == "missing-probes":
        del record["probes"]
    elif malformed == "probe-map-type":
        record["probes"] = ["curl", "pip", "https", "bypass"]
    elif malformed == "probe-type":
        record["probes"]["curl"] = None
    elif malformed == "missing-exit-code":
        del record["probes"]["curl"]["exit_code"]
    elif malformed == "missing-error":
        del record["probes"]["curl"]["error"]
    lines = ["GAP_CANARY:" + ("{" if malformed == "invalid-json" else json.dumps(record))]
    if valid_neighbor == "before":
        lines.insert(0, valid_line)
    elif valid_neighbor == "after":
        lines.append(valid_line)
    result[output_key] = "\n".join([*lines, *remaining])
    with pytest.raises(EgressCanaryRefused, match="malformed"):
        require_canary(events, nonce, command, harness_id=harness)


def test_diff_preserves_bytes_despite_workspace_attributes(tmp_path):
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    (pristine / ".gitattributes").write_text("*.txt text eol=lf\n")
    (pristine / "app.txt").write_bytes(b"old\r\n")
    workspace = tmp_path / "workspace"
    shutil.copytree(pristine, workspace)
    (workspace / "app.txt").write_bytes(b"new\r\n")
    patch = tmp_path / "patch"
    capture_diff(pristine, workspace, patch)
    restored = tmp_path / "restored"
    shutil.copytree(pristine, restored)
    subprocess.run(["git", "apply", str(patch)], cwd=restored, check=True)
    assert (restored / "app.txt").read_bytes() == b"new\r\n"


def test_snapshot_refuses_unreadable_directory(tmp_path, monkeypatch):
    def walk(*args, onerror, **kwargs):
        onerror(PermissionError("denied"))
        yield

    monkeypatch.setattr(gap_workspace.os, "walk", walk)
    with pytest.raises(SchemaError, match="unreadable"):
        snapshot_tree(tmp_path)


def test_snapshot_refuses_unreadable_file(tmp_path, monkeypatch):
    (tmp_path / "file").write_text("content")

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(SchemaError, match="unreadable"):
        snapshot_tree(tmp_path)


def test_diff_refuses_git_failure(tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(gap_workspace.subprocess, "run", failed)
    with pytest.raises(SchemaError, match="diff generation failed"):
        capture_diff({}, tmp_path, tmp_path / "diff.patch")


def test_canary_credentials_preserve_json_types_and_escape_safety():
    secret = 'quoted"secret\\value'
    evidence = {"exit_code": 1, "events": [{"message": secret + " 1"}], "ok": False}
    result = live_harness._scrub_canary_credentials(
        evidence, {"API_KEY": secret, "AUTH_TOKEN": "1"}
    )
    assert result["exit_code"] == 1
    assert result["ok"] is False
    assert (
        result["events"][0]["message"]
        == "<redacted:canary-credential> <redacted:canary-credential>"
    )
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("arm", ["floor", "ceiling"])
def test_live_reference_bundle(gap_config, tmp_path, monkeypatch, harness, arm):
    test_live_workspace_bundle(
        replace(gap_config, provider_id=arm), tmp_path, monkeypatch, harness, None
    )


def test_reference_prompt_bytes():
    from sew.gap.reference import reference_prompt

    task = {
        "prompt": "Do the job.\n",
        "oracle": {
            "source_url": "https://example.org/changelog",
            "retrieved_at": "2026-09-30",
            "excerpt": "  Verbatim café.\r\nSecond line.\n\n",
        },
    }
    assert reference_prompt("floor", task) == task["prompt"]
    assert reference_prompt("ceiling", task).encode("utf-8") == (
        "Do the job.\n\n\nReference material:\n"
        "Source URL: https://example.org/changelog\n"
        "Retrieved date: 2026-09-30\n\n"
        "  Verbatim café.\r\nSecond line.\n\n"
    ).encode("utf-8")


@pytest.mark.parametrize("arm", ["floor", "ceiling"])
def test_reference_refusals(gap_config, tmp_path, arm):
    with pytest.raises(SchemaError, match="external provider must"):
        ProviderExposure(provider_id=arm, tool_name="search")
    with pytest.raises(SchemaError, match="GAP catalog task"):
        replace(gap_config, provider_id=arm, task_id="list-build-python313-pep594-removals")
    with pytest.raises(SchemaError, match="prompt must match"):
        replace(gap_config, provider_id=arm, prompt_text="Oracle injection")
    with pytest.raises(SchemaError, match="profile must match"):
        replace(gap_config, provider_id=arm, workspace_profile=False)
    with pytest.raises(SchemaError, match="disable native search"):
        replace(gap_config, provider_id=arm, native_search_available=True)
    config = replace(gap_config, provider_id=arm)
    surface = prepare_arm_spawn(config, tmp_path, {})
    assert audit_transcript(
        surface.contract, [{"type": "tool_use", "name": "WebSearch"}]
    ).contaminated
    assert audit_transcript(
        surface.contract, [{"type": "tool_use", "name": "mcp__exa__search"}]
    ).contaminated
    catalog = config.gap_module_root / "catalogs/gap/tasks.yaml"
    doc = yaml.safe_load(catalog.read_text())
    doc["tasks"][0]["oracle"]["excerpt"] += " changed"
    catalog.write_text(yaml.safe_dump(doc))
    with pytest.raises(SchemaError, match="oracle hash mismatch"):
        prepare_arm_spawn(config, tmp_path, {})


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("arm", ["floor", "ceiling"])
def test_brief_reference_bundle(gap_config, tmp_path, monkeypatch, harness, arm):
    from sew.gap.reference import reference_prompt

    task = next(
        t for t in json.loads((GOLDEN / "tasks.json").read_text()) if t["task_type"] == "brief"
    )
    catalog = gap_config.gap_module_root / "catalogs/gap/tasks.yaml"
    doc = yaml.safe_load(catalog.read_text())
    doc["tasks"] = [task]
    catalog.write_text(yaml.safe_dump(doc))
    config = replace(
        gap_config, provider_id=arm, harness_id=harness, workspace_profile=False, task_id=task["id"]
    )
    surface = prepare_arm_spawn(config, tmp_path, {})
    if harness == "claude-code":
        assert surface.harness_args[surface.harness_args.index("--tools") + 1] == ""
        assert json.loads(surface.mcp_config_path.read_text())["mcpServers"] == {}
    else:
        assert tomllib.loads(surface.mcp_config_path.read_text())["web_search"] == "disabled"
        assert set(surface.harness_args[1::2]) == set(CODEX_DISABLED_FEATURES)
    spawned = []

    def spawn(argv, *, prompt, **kwargs):
        spawned.append(prompt)
        assert prompt == reference_prompt(arm, task)
        return ProcessOutcome(ready=True, exit_code=0, events=[])

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    result = run_live_harness(config, tmp_path / "runs", environ={"SEW_HARNESS_LIVE": "1"})
    assert len(spawned) == 1
    metadata = json.loads((result.bundle_dir / "artifacts/spawn-metadata.json").read_text())
    assert metadata["arm_contract"]["reference_arm"] == arm
    assert metadata["arm_contract"]["oracle_excerpt_sha256"] == task["oracle"]["sha256"]
    assert not metadata["arm_contract"]["native_search"]
    assert metadata["arm_contract"]["allowed_mcp_servers"] == []
    assert not (result.bundle_dir / "artifacts/workspace.diff").exists()


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("arm", ["native", *PROVIDER_SERVER_NAMES])
@pytest.mark.parametrize("read_target", [None, "catalog", "scratch"])
def test_brief_search_arm_uses_the_gap_catalog_prompt_and_budgets(
    gap_config, tmp_path, monkeypatch, harness, arm, read_target
):
    task = next(
        t for t in json.loads((GOLDEN / "tasks.json").read_text()) if t["task_type"] == "brief"
    )
    catalog = gap_config.gap_module_root / "catalogs/gap/tasks.yaml"
    doc = yaml.safe_load(catalog.read_text())
    doc["tasks"] = [task]
    catalog.write_text(yaml.safe_dump(doc))
    config = replace(
        config_for(gap_config, harness, arm),
        workspace_profile=False,
        task_id=task["id"],
        task_source="gap",
    )
    spawned = []

    def spawn(argv, *, prompt, limits, cwd, **kwargs):
        spawned.append((prompt, limits))
        if arm != "native":
            # This spawn double stands in for a harness that discovered its
            # configured provider. Exercise the real observer, without a server.
            from sew.mcp_meter import AVAILABILITY_REF, CallMeter

            run_dir = kwargs["provisional_meters_path"].parents[1]
            record = json.loads((run_dir / AVAILABILITY_REF).read_text())
            meter = CallMeter(
                provider_id=arm, run_id=record["run_id"], call_dir=run_dir / "provider-calls"
            )
            meter.client_message({"id": 1, "method": "initialize"})
            meter.server_message({"id": 1, "result": {}})
            meter.client_message({"id": 2, "method": "tools/list"})
            meter.server_message({"id": 2, "result": {"tools": [{"name": "search"}]}})
        events = []
        if read_target:
            path = catalog if read_target == "catalog" else cwd / "notes.txt"
            event = (
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(path)}}
                if harness == "claude-code"
                else {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": f"cat {shlex.quote(str(path))}",
                    },
                }
            )
            events.append({"event": event, "received_at": "2026-10-03T18:00:00Z"})
        final = (
            {"type": "result", "subtype": "success", "result": "Brief completed."}
            if harness == "claude-code"
            else {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Brief completed."},
            }
        )
        events.append({"event": final, "received_at": "2026-10-03T18:00:01Z"})
        return ProcessOutcome(ready=True, exit_code=0, events=events)

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    result = run_live_harness(config, tmp_path / "runs", environ={"SEW_HARNESS_LIVE": "1"})
    [(prompt, limits)] = spawned
    assert prompt == task["prompt"]
    assert "Reference material" not in prompt
    assert limits.max_total_tokens == task["budgets"]["max_total_tokens"]
    metadata = json.loads((result.bundle_dir / "artifacts/spawn-metadata.json").read_text())
    assert metadata["arm_contract"]["native_search"] == (arm == "native")
    assert metadata["arm_contract"].get("reference_arm") is None
    contaminated = read_target == "catalog"
    assert result.status == ("contaminated" if contaminated else "succeeded")
    assert metadata["arm_audit"]["contaminated"] == contaminated
    assert metadata["arm_audit"]["violations"] == (
        ["workspace:forbidden-read"] if contaminated else []
    )


def test_unknown_gap_task_still_refused_for_search_arms(gap_config, tmp_path):
    config = replace(
        gap_config,
        provider_id="native",
        native_search_available=True,
        workspace_profile=False,
        task_id="gap-no-such-task",
        task_source="gap",
    )
    with pytest.raises(SchemaError, match="GAP catalog task"):
        run_live_harness(config, tmp_path / "runs", environ={"SEW_HARNESS_LIVE": "1"})


@pytest.mark.parametrize("arm", ["floor", "ceiling"])
def test_reference_catalog_reuse_preserves_freshness(gap_config, monkeypatch, arm):
    from sew.gap import reference
    from sew.arms import contract_for

    calls = []
    original = reference.load_gap_tasks

    def load(root):
        calls.append(root)
        return original(root)

    monkeypatch.setattr(reference, "load_gap_tasks", load)
    config = replace(gap_config, provider_id=arm)
    contract_for(config)
    _, task = reference.resolve_gap_task(config)
    assert len(calls) == 1
    task["oracle"]["sha256"] = "mutated"
    assert reference.resolve_gap_task(config)[1]["oracle"]["sha256"] != "mutated"
    catalog = config.gap_module_root / "catalogs/gap/tasks.yaml"
    doc = yaml.safe_load(catalog.read_text())
    doc["tasks"][0]["oracle"]["excerpt"] += " changed"
    catalog.write_text(yaml.safe_dump(doc))
    with pytest.raises(SchemaError, match="oracle hash mismatch"):
        contract_for(config)
    assert len(calls) == 2


def test_claude_canary_attributes_separate_violation_block():
    nonce, command = canary_command("claude-code")
    probes = {
        name: {"exit_code": 1, "error": "Couldn't connect to server"}
        for name in ("curl", "pip", "https")
    }
    events = canary_events("claude-code", nonce, command, probes)
    result = events[-1]["message"]["content"][0]
    result["content"] = result["content"].split("\n<sandbox_violations>")[0] + (
        "\n<sandbox_violations>\n"
        f"deny network-outbound {nonce}.example.com:443 (host is on the deny list)\n"
        "deny network-outbound pypi.org:443 (host is on the deny list)\n"
        f"deny network-outbound {nonce}.example.org:443 (host is on the deny list)\n"
        "</sandbox_violations>"
    )
    assert require_canary(events, nonce, command, harness_id="claude-code")["nonce"] == nonce
    result["content"] = result["content"].replace(
        nonce + ".example.org:443", "other.example.org:443"
    )
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="claude-code")


@pytest.mark.parametrize("probe", ["curl", "pip", "https"])
@pytest.mark.parametrize("exit_code", [0, None, True])
def test_claude_denial_does_not_replace_exit_code_guard(probe, exit_code):
    nonce, command = canary_command("claude-code")
    probes = {
        name: {"exit_code": 1, "error": "Operation not permitted"}
        for name in ("curl", "pip", "https")
    }
    probes[probe]["exit_code"] = exit_code
    # Keep every matching sandbox violation even when the exit code is invalid.
    events = canary_events("claude-code", nonce, command, probes)
    assert events[-1]["message"]["content"][0]["content"].count("deny network-outbound") == 3
    with pytest.raises(EgressCanaryRefused, match="egress reached or denial unproven"):
        require_canary(events, nonce, command, harness_id="claude-code")


def test_claude_unrecognized_violation_format_is_diagnosed():
    nonce, command = canary_command("claude-code")
    events = canary_events("claude-code", nonce, command)
    result = events[-1]["message"]["content"][0]
    result["content"] = result["content"].replace("host is on the deny list", "new denial wording")
    with pytest.raises(EgressCanaryRefused, match="unrecognized sandbox_violations format"):
        require_canary(events, nonce, command, harness_id="claude-code")


def test_captured_claude_canary_refuses_unattributed_probes():
    events = json.loads(
        (Path(__file__).parent / "fixtures/claude-egress-canary-captured.json").read_text()
    )
    command = events[0]["message"]["content"][0]["input"]["command"]
    nonce = shlex.split(command)[-1]
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="claude-code")
    # Isolate the attribution defect from the retired DNS/three-probe schema.
    from sew.gap.workspace import CANARY_PROGRAM

    command = shlex.join(["python3", "-c", CANARY_PROGRAM, "claude-code", nonce])
    events[0]["message"]["content"][0]["input"]["command"] = command
    result = events[-1]["message"]["content"][0]
    line, suffix = result["content"].split("\n", 1)
    record = json.loads(line.removeprefix("GAP_CANARY:"))
    record["probes"]["https"] = record["probes"].pop("dns")
    result["content"] = "GAP_CANARY:" + json.dumps(record) + "\n" + suffix
    with pytest.raises(EgressCanaryRefused, match="denial unproven"):
        require_canary(events, nonce, command, harness_id="claude-code")


@pytest.mark.parametrize("replacement", ["", "other.example.com:443", "pypi.org:80"])
def test_claude_requires_matching_sandbox_violation(replacement):
    nonce, command = canary_command("claude-code")
    events = canary_events("claude-code", nonce, command)
    result = events[-1]["message"]["content"][0]
    result["content"] = result["content"].replace("pypi.org:443", replacement)
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="claude-code")


def test_claude_refuses_second_exact_call_without_result():
    nonce, command = canary_command("claude-code")
    events = canary_events("claude-code", nonce, command)
    events.append(copy.deepcopy(events[0]))
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="claude-code")


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_canary_attribution_and_harness_binding(harness):
    nonce, command = canary_command(harness)
    events = canary_events(harness, nonce, command)
    record = require_canary(events, nonce, command, harness_id=harness)
    assert record["probes"]["curl"]["attribution"] == (
        "denial-text" if harness == "codex" else "sandbox-violation"
    )
    other = "claude-code" if harness == "codex" else "codex"
    with pytest.raises(EgressCanaryRefused, match="harness mismatch"):
        require_canary(events, nonce, command, harness_id=other)
    with pytest.raises(EgressCanaryRefused, match="event harness mismatch"):
        require_canary(canary_events(other, nonce, command), nonce, command, harness_id=harness)


def test_codex_duplicate_item_delivery_and_second_empty_call():
    nonce, command = canary_command("codex")
    events = canary_events("codex", nonce, command)
    events[0]["item"]["id"] = "first"
    events.append(copy.deepcopy(events[0]))
    assert require_canary(events, nonce, command, harness_id="codex")
    second = copy.deepcopy(events[0])
    second["item"].update(id="second", aggregated_output="")
    events.append(second)
    with pytest.raises(EgressCanaryRefused, match="extra Codex"):
        require_canary(events, nonce, command, harness_id="codex")


def test_claude_partial_wording_drift_and_crlf():
    nonce, command = canary_command("claude-code")
    events = canary_events("claude-code", nonce, command)
    result = events[-1]["message"]["content"][0]
    result["content"] = result["content"].replace("\n", "\r\n")
    assert require_canary(events, nonce, command, harness_id="claude-code")
    result["content"] = result["content"].replace("host is on the deny list", "unknown reason", 1)
    with pytest.raises(EgressCanaryRefused, match="unrecognized sandbox_violations"):
        require_canary(events, nonce, command, harness_id="claude-code")


REAL_BENCH_BOUNDARY = gap_workspace.bench_network_boundary
REAL_CLAUDE_QUALIFICATION = gap_workspace.qualify_claude_code


def test_live_claude_boundary_receives_selected_broker_auth(
    gap_config, tmp_path, monkeypatch, agent_os_host
):
    monkeypatch.setattr(
        live_harness.broker_auth,
        "claude_code_env",
        lambda *a, **kw: {"ANTHROPIC_AUTH_TOKEN": "fixture-access-token"},
    )

    def boundary(argv, env, **kwargs):
        assert kwargs["harness_auth"] == "broker"
        assert kwargs["wheelhouse"] == gap_config.wheelhouse
        assert env["ANTHROPIC_AUTH_TOKEN"] == "fixture-access-token"
        raise EgressCanaryRefused("broker reached boundary")

    monkeypatch.setattr(gap_workspace, "bench_network_boundary", boundary)
    monkeypatch.setattr(
        live_harness,
        "spawn_and_capture",
        lambda *a, **kw: pytest.fail("boundary refusal must precede a model invocation"),
    )
    with pytest.raises(EgressCanaryRefused, match="broker reached boundary"):
        run_live_harness(
            replace(gap_config, harness_id="claude-code", harness_auth="broker"),
            tmp_path / "run",
            environ={"SEW_HARNESS_LIVE": "1"},
        )


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("failure", ["cache", "wheelhouse"])
def test_live_cache_isolation_refusal_precedes_model_turn(
    gap_config, tmp_path, monkeypatch, harness, failure
):
    from sew.gap import sandbox

    class UnconfinedBackend:
        name = "seatbelt"

        def command(self, argv, *args, **kwargs):
            return argv

    monkeypatch.setattr(sandbox, "select_backend", lambda env=None: UnconfinedBackend())
    monkeypatch.setattr(gap_workspace, "_harness_endpoints", lambda *args: ({}, set(), set()))
    monkeypatch.setattr(gap_workspace, "bench_network_boundary", REAL_BENCH_BOUNDARY)
    monkeypatch.setattr(gap_workspace, "prepare_python_environment", lambda scratch, env, source: env)
    if failure == "wheelhouse":
        monkeypatch.setattr(gap_workspace, "qualify_cache_reads", lambda *a, **kw: {})
        monkeypatch.setattr(gap_workspace, "qualify_verifier_reads", lambda *a, **kw: {})
    monkeypatch.setattr(
        live_harness, "spawn_and_capture", lambda *a, **kw: pytest.fail("no model may start")
    )
    reason = "cache isolation unproven" if failure == "cache" else "wheelhouse immutability unproven"
    with pytest.raises(EgressCanaryRefused, match=reason):
        run_live_harness(
            replace(gap_config, harness_id=harness), tmp_path / "runs",
            environ={"SEW_HARNESS_LIVE": "1"},
        )
    receipt = next((tmp_path / "runs").rglob("egress-canary.json"))
    record = json.loads(receipt.read_text())
    assert not record["admissible"] and reason in record["reason"]


@pytest.fixture(autouse=True)
def fixture_bench_boundary(monkeypatch):
    def qualify(*args, **kwargs):
        evidence = {
            "harness_id": "claude-code",
            "admissible": True,
            "qualification": "out-of-model-srt",
            "direct_egress": kwargs["direct_evidence"],
            "probes": {name: {} for name in ("curl", "pip", "https")},
            "usage": {},
        }
        Path(kwargs["refusal_path"]).write_text(json.dumps(evidence), encoding="utf-8")
        return evidence

    monkeypatch.setattr(
        gap_workspace,
        "qualify_claude_code",
        qualify,
    )
    # Existing harness integration fixtures never spawn a live sandbox.
    monkeypatch.setattr(
        gap_workspace,
        "bench_network_boundary",
        lambda argv, env, **kw: nullcontext(
            (
                list(argv),
                {
                    "connected": False,
                    "errno": 1,
                    "attribution": (
                        "harness-runner-permission:srt"
                        if kw["harness_id"] == "claude-code"
                        else "harness-runner-permission:codex-sandbox"
                    ),
                },
            )
        ),
    )


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_agent_canary_has_no_bypass(harness):
    _, command = canary_command(harness)
    assert "--noproxy" not in command
    assert "bypass" not in command


@pytest.mark.parametrize(
    "result",
    [
        {"connected": True, "errno": None},
        {"connected": False, "errno": 111},
        {"connected": False, "errno": None},
        {},
    ],
)
def test_bench_direct_check_refuses_unproven_denial(result):
    with pytest.raises(EgressCanaryRefused):
        gap_workspace.require_direct_denial(result, runner="codex-sandbox")


def test_bench_direct_check_accepts_only_permission():
    assert gap_workspace.require_direct_denial({"connected": False, "errno": 1}, runner="srt")


def test_captured_safety_refusal_cannot_qualify_even_with_valid_tool_result():
    events = json.loads(
        (Path(__file__).parent / "fixtures/claude-canary-safety-refusal.json").read_text()
    )
    nonce, command = canary_command("claude-code")
    events += canary_events("claude-code", nonce, command)
    with pytest.raises(EgressCanaryRefused, match="model refusal"):
        require_canary(events, nonce, command, harness_id="claude-code")


@pytest.mark.parametrize("connected, error", [(False, 1), (True, None), (False, 60)])
def test_bench_runner_qualifies_inside_filesystem_wrapper(monkeypatch, tmp_path, connected, error):
    from sew.gap import sandbox

    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("seatbelt", "/usr/bin/sandbox-exec"),
    )
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(
        gap_workspace.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("192.0.2.10", 443))]
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "connected": connected,
                    "errno": error,
                }
            ),
            "",
        )

    monkeypatch.setattr(
        gap_workspace,
        "sandbox_probe_runner",
        lambda *a, **kw: (
            [
                "srt",
                "--settings",
                "fixture.json",
                "--",
                "python3",
                "-c",
                gap_workspace.DIRECT_PROBE,
            ],
            {
                "runner": "srt",
                "runner_id": "srt",
                "runner_version": "fixture",
                "policy": {"network": {"allowedDomains": []}},
            },
        ),
    )
    monkeypatch.setattr(subprocess, "run", run)
    if connected or error != 1:
        with pytest.raises(EgressCanaryRefused):
            with REAL_BENCH_BOUNDARY(["claude"], {}, cwd=tmp_path, harness_id="claude-code"):
                pytest.fail("unproven denial admitted")
        return
    with REAL_BENCH_BOUNDARY(["claude"], {}, cwd=tmp_path, harness_id="claude-code") as boundary:
        argv, evidence = boundary

    assert argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert str(gap_workspace.VERIFIER_ROOT) in argv[2]
    assert "network" not in argv[2]
    assert argv[3:] == ["claude"]
    assert calls[0][0][:3] == argv[:3]
    assert calls[0][0][3] == "srt"
    assert calls[0][0][-1] == gap_workspace.DIRECT_PROBE
    assert evidence["runner_version"] == "fixture"


def test_forbidden_root_follows_relocated_workbench(tmp_path, monkeypatch):
    relocated = tmp_path / "searchlight"
    relocated.mkdir()
    monkeypatch.setattr(gap_workspace, "module_root", lambda: relocated)
    event = {
        "type": "tool_use",
        "name": "Read",
        "input": {"file_path": str(relocated / "config/price-table.yaml")},
    }
    assert audit_workspace_calls([event]) == ["workspace:forbidden-read"]
    # A similarly named unrelated directory is no longer blocked by a literal.
    event["input"]["file_path"] = str(
        tmp_path / "modules" / "search-evaluation-workbench" / "README.md"
    )
    assert audit_workspace_calls([event]) == []


@pytest.mark.parametrize(
    "command,target",
    [
        ("pip download stripe==15.6.1 --no-deps -d /tmp/st", "pypi.org:443"),
        ("curl https://example.com", "example.com:443"),
        ("env -u A -u B curl https://example.com", "example.com:443"),
        ("env -i curl https://example.com", "example.com:443"),
    ],
)
@pytest.mark.parametrize("evidence", ["matched", "missing", "unrelated", "wrong-target", "prose"])
def test_network_attempt_denial_attribution(command, target, evidence):
    request = {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": command}}
    output = f"<sandbox_violations>\ndeny network-outbound {target} (host is on the deny list)\n</sandbox_violations>"
    if evidence == "missing":
        output = "No matching distribution found; connection failed"
    if evidence == "wrong-target":
        output = output.replace(target, "unrelated.example:443")
    result = {
        "type": "tool_result",
        "tool_use_id": "other" if evidence == "unrelated" else "call-1",
        "content": output,
    }
    if evidence == "prose":
        result = {"type": "text", "text": output}
    denied = set()
    violations = audit_workspace_calls([request, result], denied_attempts=denied)
    assert violations == ([] if evidence == "matched" else ["workspace:shell-network"])
    assert len(denied) == (1 if evidence == "matched" else 0)


def test_captured_codex_failed_command_without_denial_contaminates():
    event = json.loads((Path(__file__).parent / "fixtures/codex-failed-command.json").read_text())
    assert event["type"] == "item.completed"
    assert event["item"]["status"] == "failed"
    assert event["item"]["exit_code"] == 1
    assert audit_workspace_calls([event]) == ["workspace:shell-network"]


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "bash -lc 'mkdir -p d && pip download x'",
        "/bin/zsh -lc 'mkdir -p d && python3 -m pip download x'",
        "sh -c \"bash -lc 'curl https://example.com'\"",
    ],
)
@pytest.mark.parametrize("status,exit_code", [("failed", 7), ("failed", None), ("completed", 7)])
def test_codex_permission_denial_and_local_read_still_contaminates(command, status, exit_code):
    # Preserve the captured exec event envelope; commands and denial text are
    # synthetic regression inputs, not claims of live containment qualification.
    event = json.loads((Path(__file__).parent / "fixtures/codex-failed-command.json").read_text())
    event["item"].update(
        status=status,
        command=command,
        exit_code=exit_code,
        aggregated_output="curl: connect failed: Operation not permitted",
    )
    denied = set()
    assert audit_workspace_calls([event], denied_attempts=denied) == []
    assert len(denied) == 1
    read = {
        "type": "tool_use",
        "name": "Read",
        "input": {"file_path": "/bench/catalogs/gap/hidden/oracle.txt"},
    }
    assert audit_workspace_calls([event, read]) == ["workspace:forbidden-read"]
    event["item"].update(status="completed", exit_code=0)
    assert audit_workspace_calls([event]) == ["workspace:shell-network"]


@pytest.mark.parametrize(
    "command",
    [
        "bash -lc 'curl https://example.com; curl https://other.example'",
        "bash -lc 'mkdir -p d && pip download x && curl https://example.com'",
        "bash -lc 'curl https://example.com' | sh -c 'curl https://other.example'",
        "bash -lc 'curl https://example.com",
    ],
)
def test_codex_denial_cannot_certify_multiple_or_malformed_invocations(command):
    event = json.loads((Path(__file__).parent / "fixtures/codex-failed-command.json").read_text())
    event["item"].update(
        command=command, aggregated_output="connect failed: Operation not permitted"
    )
    assert audit_workspace_calls([event]) == ["workspace:shell-network"]


def test_one_denial_cannot_certify_two_network_commands():
    command = "curl https://example.com; curl https://other.example"
    request = {"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": command}}
    result = {
        "type": "tool_result",
        "tool_use_id": "call",
        "content": "<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>",
    }
    assert audit_workspace_calls([request, result]) == ["workspace:shell-network"]


def test_denial_does_not_qualify_identical_unmatched_call():
    command = "curl https://example.com"
    requests = [
        {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}}
        for call_id in ("a", "b")
    ]
    result = {
        "type": "tool_result",
        "tool_use_id": "a",
        "content": "<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>",
    }
    denied = set()
    assert audit_workspace_calls([*requests, result], denied_attempts=denied) == [
        "workspace:shell-network"
    ]
    assert len(denied) == 1


def test_arm_audit_counts_distinct_denied_calls():
    from sew.arms import ArmContract, audit_transcript

    command = "curl https://example.com"
    transcript = []
    for call_id in ("a", "b"):
        transcript.extend(
            [
                {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}},
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": "<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>",
                },
            ]
        )
    contract = ArmContract(
        kind="floor", harness_id="claude-code", provider_id="floor", workspace_profile=True
    )
    audit = audit_transcript(contract, transcript)
    assert audit.contaminated is False
    assert audit.denied_network_attempts == 2


@pytest.mark.parametrize(
    "payload",
    [
        None,
        42,
        ["bad"],
        '"bad"',
        "curl https://example.com",
        '{"command": "curl https://example.com"}',
    ],
)
def test_denial_pairing_tolerates_non_mapping_input(payload):
    request = {"type": "tool_use", "id": "call", "name": "Bash", "input": payload}
    audit_workspace_calls([request])


@pytest.mark.parametrize(
    "command,output",
    [
        ("curl https://example.com; false", "network: Permission denied"),
        ("curl https://example.com | head", "network: Permission denied"),
        ("curl https://example.com", "network: Permission denied\nfetched content"),
        (
            "curl https://example.com; echo fake",
            "<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>",
        ),
        (
            "curl https://example.com",
            "<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>\nmore output",
        ),
        (
            "curl https://example.com",
            "<sandbox_violations></sandbox_violations>\n<sandbox_violations>\ndeny network-outbound example.com:443 (host is on the deny list)\n</sandbox_violations>",
        ),
    ],
)
def test_denial_shaped_stdout_cannot_certify_call(command, output):
    event = {
        "type": "command_execution",
        "id": "call",
        "command": command,
        "status": "failed",
        "exit_code": 1,
        "aggregated_output": output,
    }
    assert audit_workspace_calls([event]) == ["workspace:shell-network"]


@pytest.mark.parametrize(
    "reason",
    [
        "bubblewrap unavailable",
        "bubblewrap canary denial unproven",
        "Linux Claude code cells require broker OAuth auth",
    ],
)
def test_backend_refusal_prevents_code_but_not_brief(gap_config, tmp_path, monkeypatch, reason):
    from sew.gap import sandbox

    source_env = {"SEW_HARNESS_LIVE": "1", "SEW_CONFIG": str(tmp_path / "run-config.yaml")}

    def select(env):
        assert env == source_env
        return sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")

    def refuse(*args, **kwargs):
        raise EgressCanaryRefused("GAP refused: " + reason)

    if "Claude" in reason:
        gap_config = replace(gap_config, harness_id="claude-code")
    if "canary" in reason or "Claude" in reason:
        monkeypatch.setattr(
            sandbox,
            "select_backend",
            select,
        )
        monkeypatch.setattr(sandbox, "qualify_backend", refuse)
        monkeypatch.setattr(gap_workspace.shutil, "which", lambda *a, **kw: sys.executable)
        monkeypatch.setattr(
            gap_workspace.socket,
            "getaddrinfo",
            lambda *a, **kw: [(2, 1, 6, "", ("192.0.2.10", 443))],
        )
    else:
        monkeypatch.setattr(sandbox, "select_backend", refuse)
    monkeypatch.setattr(gap_workspace, "bench_network_boundary", REAL_BENCH_BOUNDARY)
    calls = []

    def spawn(*args, **kwargs):
        calls.append(kwargs["prompt"])
        return ProcessOutcome(events=[], ready=True, exit_code=0)

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    with pytest.raises(EgressCanaryRefused, match=reason):
        run_live_harness(gap_config, tmp_path / "code", environ=source_env)
    assert calls == []
    artifacts = list((tmp_path / "code").rglob("egress-canary.json"))
    assert len(artifacts) == 1
    evidence = json.loads(artifacts[0].read_text())
    assert not evidence["admissible"] and reason in evidence["reason"]
    run_live_harness(
        replace(gap_config, workspace_profile=False, task_id="brief", prompt_text="Write a brief."),
        tmp_path / "brief",
        environ={"SEW_HARNESS_LIVE": "1"},
    )
    assert len(calls) == 1


def test_bubblewrap_claude_refuses_without_host_srt_probe(
    gap_config, tmp_path, monkeypatch, agent_os_host
):
    from sew.gap import sandbox

    monkeypatch.setattr(gap_workspace, "bench_network_boundary", REAL_BENCH_BOUNDARY)
    monkeypatch.setattr(gap_workspace, "qualify_claude_code", REAL_CLAUDE_QUALIFICATION)
    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"),
    )
    monkeypatch.setattr(
        sandbox, "qualify_backend", lambda *a, **kw: {"backend": "bubblewrap", "admissible": True}
    )
    monkeypatch.setattr(
        gap_workspace, "qualify_cache_reads",
        lambda *a, **kw: {"direct_denied": True, "discovery_denied": True},
    )
    monkeypatch.setattr(gap_workspace.shutil, "which", lambda *a, **kw: sys.executable)
    monkeypatch.setattr(
        gap_workspace.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("192.0.2.10", 443))]
    )
    monkeypatch.setattr(
        live_harness.broker_auth,
        "claude_code_env",
        lambda *a, **kw: {"ANTHROPIC_AUTH_TOKEN": "fixture-access-token"},
    )
    monkeypatch.setattr(
        gap_workspace,
        "sandbox_probe_runner",
        lambda *a, **kw: pytest.fail("host srt cannot qualify a namespace"),
    )
    monkeypatch.setattr(
        live_harness,
        "spawn_and_capture",
        lambda *a, **kw: pytest.fail("refusal must precede model turns"),
    )
    with pytest.raises(EgressCanaryRefused, match="Linux Claude code cells are unsupported"):
        run_live_harness(
            replace(gap_config, harness_id="claude-code", harness_auth="broker"),
            tmp_path / "runs",
            environ={"SEW_HARNESS_LIVE": "1"},
        )
    (artifact,) = (tmp_path / "runs").rglob("egress-canary.json")
    evidence = json.loads(artifact.read_text())
    assert not evidence["admissible"]
    assert evidence["direct_egress"]["backend"] == "bubblewrap"
    assert "Linux Claude code cells are unsupported" in evidence["reason"]


@pytest.mark.parametrize("attempt", [0, 1])
def test_captured_compound_pip_results_are_not_exempt(attempt):
    # Both captured requests chain pip with other shell commands.
    transcript = json.loads(
        (Path(__file__).parent / "fixtures/gap-pip-local-results.json").read_text()
    )[attempt]
    neutralized, denied = set(), set()
    assert audit_workspace_calls(
        transcript,
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
        neutralized_attempts=neutralized,
        denied_attempts=denied,
    ) == ["workspace:shell-network"]
    assert not neutralized
    assert not denied
    assert audit_workspace_calls(transcript) == ["workspace:shell-network"]


def test_pip_background_result_for_allowlisted_command_is_neutralized():
    transcript = json.loads(
        (Path(__file__).parent / "fixtures/gap-pip-local-results.json").read_text()
    )[0]
    transcript[0]["input"]["command"] = "pip download stripe==15.6.1 --no-deps -d downloads"
    neutralized = set()
    assert audit_workspace_calls(
        transcript,
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
        neutralized_attempts=neutralized,
    ) == []
    assert len(neutralized) == 1


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize(
    "source_option",
    [
        "-qvi links.html",
        "-qvi./links.html",
        "-Uqvvi links.html",
        "-r requirements.txt",
        "-rrequirements.txt",
        "-qrrequirements.txt",
        "--requirement=requirements.txt",
        "--req requirements.txt",
        "--require''ment requirements.txt",
        "-c requirements.txt",
        "-crequirements.txt",
        "--constraint=requirements.txt",
        "--const requirements.txt",
        "--build-constraint requirements.txt",
        "-f links.html",
        "-flinks.html",
        "--find-links=links.html",
        "--find links.html",
    ],
)
@pytest.mark.parametrize("operation", ["download", "install"])
@pytest.mark.parametrize("wrapper", ["{}", "bash -lc {}"])
def test_pip_indirect_sources_cannot_certify_truncated_success(
    tmp_path, harness, source_option, operation, wrapper
):
    (tmp_path / "requirements.txt").write_text(
        "--find-links https://example.org/wheels/\nstripe==15.6.1\n"
    )
    (tmp_path / "links.html").write_text(
        '<a href="https://example.org/stripe-15.6.1-py3-none-any.whl">stripe</a>'
    )
    command = (
        f"pip {operation} {source_option} --no-deps --retries 0 --timeout 1"
        + (" -d downloads" if operation == "download" else "")
        + " 2>&1 | tail -1"
    )
    command = wrapper.format(shlex.quote(command)) if wrapper != "{}" else command
    output = f"Successfully {'downloaded' if operation == 'download' else 'installed'} stripe"
    transcript = (
        [
            {
                "type": "command_execution",
                "id": "pip",
                "command": command,
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": output,
            }
        ]
        if harness == "codex"
        else [
            {"type": "tool_use", "id": "pip", "name": "Bash", "input": {"command": command}},
            {"type": "tool_result", "tool_use_id": "pip", "content": output, "is_error": False},
        ]
    )
    neutralized, denied = set(), set()
    assert audit_workspace_calls(
        transcript,
        cwd=tmp_path,
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
        neutralized_attempts=neutralized,
        denied_attempts=denied,
    ) == ["workspace:shell-network"]
    assert not neutralized
    assert not denied


@pytest.mark.parametrize(
    "command",
    [
        "pip download stripe==15.6.1 --no-deps -d downloads",
        "pip3 download -q --no-index --find-links {wheelhouse} --dest downloads stripe==15.6.1",
        "python3 -m pip install --quiet --no-deps --find-links {wheelhouse} stripe==15.6.1",
    ],
)
def test_pip_download_from_cell_wheelhouse_still_qualifies(gap_config, tmp_path, command):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, _, env = prepare_workspace(gap_config, scratch, *workspace_task(gap_config))
    command = command.format(wheelhouse=env["PIP_FIND_LINKS"])
    neutralized = set()
    assert audit_workspace_calls(
        [
            {
                "type": "command_execution",
                "id": "pip",
                "command": command,
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "Successfully downloaded stripe",
            }
        ],
        cell_env=env,
        neutralized_attempts=neutralized,
    ) == []
    assert neutralized == {("pip", command)}


@pytest.mark.parametrize(
    "command",
    [
        "pip download stripe==15.6.1 --no-deps -d /tmp/x",
        "python -m pip download stripe==15.6.1 --no-deps -d downloads",
        "/tmp/venv/bin/pip install pytest",
        "pip install stripe",
    ],
)
@pytest.mark.parametrize(
    "output",
    [
        "ERROR: Could not find a version (from versions: none)\nERROR: No matching distribution found",
        "Processing /tmp/wheelhouse/stripe.whl\nSuccessfully installed stripe",
        "Processing /tmp/wheelhouse/stripe.whl\nSuccessfully downloaded stripe",
    ],
)
def test_pip_local_failure_and_success_metric(command, output):
    event = {
        "type": "command_execution",
        "id": "pip",
        "command": command,
        "status": "completed",
        "exit_code": 0,
        "aggregated_output": output,
    }
    neutralized = set()
    assert (
        audit_workspace_calls(
            [event],
            cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
            neutralized_attempts=neutralized,
        )
        == []
    )
    assert neutralized == ({("pip", command)} if "download" in command else set())


@pytest.mark.parametrize(
    "command",
    [
        "pip download stripe --isolated",
        "pip download stripe --iso''lated",
        "pip download stripe --isol",
        "pip install stripe --isol",
        "pip install stripe --index=https://pypi.org/simple",
        'env PI""P_NO_INDEX=0 pip download stripe',
        'env PI""P_NO_INDEX=0 pip install stripe',
        "env -u A -u B -u PIP_NO_INDEX pip install stripe",
        "env --unset=PIP_NO_INDEX pip install stripe",
        "env -i pip install stripe",
        "env --ignore-environment pip install stripe",
        "env - pip install stripe",
        "env -i sh -c 'pip install stripe'",
        'export PI""P_NO_INDEX=0; pip download stripe',
        "export PIP_NO_INDEX=0; pip install stripe",
        "pip download stripe -i https://pypi.org/simple",
        "pip download stripe --extra-index-url=https://pypi.org/simple",
        "env -u PIP_NO_INDEX pip download stripe",
        "env -u PIP_NO_INDEX pip install stripe",
        "PIP_INDEX_URL= pip download stripe",
        "pip download stripe --no-index=false",
        "pip download https://example.com/stripe.whl",
        "curl https://example.com",
        "pip download stripe; curl https://example.com",
    ],
)
def test_pip_overrides_and_other_network_calls_remain_contaminating(command):
    event = {
        "type": "command_execution",
        "id": "pip",
        "command": command,
        "status": "failed",
        "exit_code": 1,
        "aggregated_output": "ERROR: (from versions: none)\nERROR: No matching distribution found",
    }
    neutralized = set()
    assert audit_workspace_calls(
        [event],
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
        neutralized_attempts=neutralized,
    ) == ["workspace:shell-network"]
    assert not neutralized


STRICT_PIP_ENV = {
    "PIP_NO_INDEX": "1",
    "PIP_CONFIG_FILE": "/dev/null",
    "PIP_FIND_LINKS": "/tmp/cell/wheelhouse",
}
PIP_PIN = "stripe==15.6.1"


def _pip_result(harness, command, output="Successfully downloaded stripe"):
    if harness == "codex":
        return [
            {
                "type": "command_execution",
                "id": "pip",
                "command": command,
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": output,
            }
        ]
    return [
        {"type": "tool_use", "id": "pip", "name": "Bash", "input": {"command": command}},
        {"type": "tool_result", "tool_use_id": "pip", "content": output, "is_error": False},
    ]


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize(
    "command",
    [
        f"pip download {PIP_PIN}",
        f"pip3 install {PIP_PIN}",
        f"python -m pip download {PIP_PIN} other-name==2.0.0rc1",
        f"python3 -m pip install {PIP_PIN} --no-deps",
        f"pip download --no-deps -d downloads --no-index -q {PIP_PIN}",
        f"pip download --dest /tmp/out --quiet -q {PIP_PIN}",
        f"pip download --find-links /tmp/cell/wheelhouse {PIP_PIN}",
        f"pip install --no-index --find-links /tmp/cell/wheelhouse {PIP_PIN}",
    ],
)
def test_strict_pip_allowlist_is_exempt(harness, command):
    neutralized = set()
    assert audit_workspace_calls(
        _pip_result(harness, command), cell_env=STRICT_PIP_ENV, neutralized_attempts=neutralized
    ) == []
    assert gap_workspace.pip_command_is_exempt(command, STRICT_PIP_ENV)
    # Neutralization counts detected attempts; a plain offline install is not one.
    assert len(neutralized) == int(gap_workspace.shell_network_attempt(command))


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize(
    "command",
    [
        # Reported bypasses of the command-text exemption.
        "pip download -r requirements.txt",
        f"pip download --no-deps -r requirements.txt {PIP_PIN}",
        f"pip download -qd downloads {PIP_PIN}",
        f"pip download -qr requirements.txt {PIP_PIN}",
        f"pip install -qvi /tmp/my_index {PIP_PIN}",
        f"bash -i -c 'pip download {PIP_PIN}'",
        f"bash -- -c 'pip download {PIP_PIN}'",
        f"bash -c 'pip download {PIP_PIN}'",
        f"bash -lc 'pip download {PIP_PIN}'",
        f"/bin/zsh -lc 'pip download {PIP_PIN}'",
        f"sh -c 'pip download {PIP_PIN}'",
        "bash -i -c 'pip install https://example.org/stripe-15.6.1-py3-none-any.whl'",
        f"env pip download {PIP_PIN}",
        f"env -u PIP_NO_INDEX pip download {PIP_PIN}",
        f"PIP_NO_INDEX=0 pip download {PIP_PIN}",
        f"xargs pip download {PIP_PIN} < /dev/null",
        f"eval pip download {PIP_PIN}",
        f"nohup pip download {PIP_PIN}",
        f"time pip download {PIP_PIN}",
        f"(pip download {PIP_PIN})",
        f"echo $(pip download {PIP_PIN})",
        f"echo `pip download {PIP_PIN}`",
        f"pip download {PIP_PIN} | tail -1",
        f"pip download {PIP_PIN} 2>&1 | tail -1",
        f"pip download {PIP_PIN} > log",
        f"cd /tmp && pip download {PIP_PIN}",
        f"pip download {PIP_PIN}; true",
        f"pip download {PIP_PIN} || true",
        f"pip download {PIP_PIN} & wait",
        f"pip download {PIP_PIN}\ntrue",
        f"pip download {PIP_PIN} # comment",
        # Forms from earlier review rounds of the exemption.
        f"pip download {PIP_PIN} -ihttps://pypi.org/simple",
        f"pip download {PIP_PIN} -i /tmp/index",
        f"pip download {PIP_PIN} --index-url https://pypi.org/simple",
        f"pip download {PIP_PIN} --extra-index-url https://pypi.org/simple",
        f"pip download {PIP_PIN} --index /tmp/index",
        f"pip download {PIP_PIN} --trusted-host pypi.org",
        f"pip download {PIP_PIN} --config-settings key=value",
        f"pip download {PIP_PIN} --isolated",
        f"pip download {PIP_PIN} --no-index=false",
        f"pip download {PIP_PIN} -c constraints.txt",
        f"pip download {PIP_PIN} --constraint constraints.txt",
        f"pip download {PIP_PIN} --requirement requirements.txt",
        f"pip download {PIP_PIN} -e .",
        f"pip download {PIP_PIN} -f /tmp/cell/wheelhouse",
        f"pip download {PIP_PIN} --find-links=/tmp/cell/wheelhouse",
        f"pip download {PIP_PIN} --find-links /tmp/cell/wheelhouse/",
        f"pip download {PIP_PIN} --find-links /tmp/other",
        f"pip download {PIP_PIN} --find-links https://example.org/wheels/",
        f"pip download {PIP_PIN} --find-links file:///tmp/cell/wheelhouse",
        f"pip download {PIP_PIN} -d https://example.org/out",
        f"pip download {PIP_PIN} --dest=downloads",
        f"pip download {PIP_PIN} -d",
        f"pip download {PIP_PIN} -qq",
        f"pip download {PIP_PIN} -d $TMPDIR",
        f"pip download $(echo -r requirements.txt) {PIP_PIN}",
        f"pip download '{PIP_PIN}'",
        f"pip download p''ip {PIP_PIN}",
        f"pip download {PIP_PIN}' '",
        "pip download stripe",
        "pip download stripe>=15",
        "pip download stripe==15.*",
        "pip download stripe[extra]==15.6.1",
        "pip download stripe===15.6.1",
        "pip download https://example.org/stripe-15.6.1-py3-none-any.whl",
        "pip download stripe==1.zip",
        "pip download ./stripe==15.6.1",
        f"pip -q download {PIP_PIN}",
        f"pip --isolated download {PIP_PIN}",
        f"python -I -m pip download {PIP_PIN}",
        f"python3.12 -m pip download {PIP_PIN}",
        f"pip3.12 download {PIP_PIN}",
        f"/tmp/venv/bin/pip download {PIP_PIN}",
        f"uv pip download {PIP_PIN}",
        "printenv PIP_NO_INDEX; pip download stripe",
    ],
)
def test_non_allowlisted_pip_requests_are_contamination(harness, command):
    neutralized, denied = set(), set()
    assert audit_workspace_calls(
        _pip_result(harness, command),
        cell_env=STRICT_PIP_ENV,
        neutralized_attempts=neutralized,
        denied_attempts=denied,
    ) == ["workspace:shell-network"]
    assert not neutralized
    assert not denied
    assert not gap_workspace.pip_command_is_exempt(command, STRICT_PIP_ENV)


@pytest.mark.parametrize(
    "command",
    [
        f"pip download {PIP_PIN} --index-url https://pypi.org/simple",
        f"pip download -r requirements.txt {PIP_PIN}",
        f"pip download {PIP_PIN}",
    ],
)
def test_sandbox_denied_pip_request_counts_as_denied_attempt(command):
    output = (
        "<sandbox_violations>\n"
        "deny network-outbound pypi.org:443 (host is on the deny list)\n"
        "</sandbox_violations>"
    )
    transcript = [
        {"type": "tool_use", "id": "pip", "name": "Bash", "input": {"command": command}},
        {"type": "tool_result", "tool_use_id": "pip", "content": output, "is_error": True},
    ]
    neutralized, denied = set(), set()
    assert audit_workspace_calls(
        transcript,
        cell_env=STRICT_PIP_ENV,
        neutralized_attempts=neutralized,
        denied_attempts=denied,
    ) == []
    assert denied == {("pip", command)}
    assert not neutralized


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"PIP_NO_INDEX": "0", "PIP_CONFIG_FILE": "/dev/null"},
        {"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/tmp/pip.conf"},
        {
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": "/dev/null",
            "PIP_FIND_LINKS": "https://example.org/wheels/",
        },
    ],
)
def test_pip_neutralization_requires_enforced_cell_config(env):
    event = {
        "type": "command_execution",
        "id": "pip",
        "command": "pip download stripe==15.6.1",
        "status": "failed",
        "aggregated_output": "ERROR: (from versions: none)\nNo matching distribution found",
    }
    assert audit_workspace_calls([event], cell_env=env) == ["workspace:shell-network"]


@pytest.mark.parametrize("command", [
    "pip download $(echo -r requirements.txt) stripe",
    "pip install $(echo -r requirements.txt) stripe",
    "pip download `echo -r requirements.txt` stripe",
    "pip install `echo -r requirements.txt` stripe",
    "pip download ${PIP_OPTIONS} stripe",
    "pip install $PIP_OPTIONS stripe",
])
def test_dynamic_pip_sources_cannot_certify_success(command):
    event = {"type": "command_execution", "id": "pip", "command": command,
             "status": "completed", "exit_code": 0,
             "aggregated_output": "Successfully downloaded stripe"}
    neutralized = set()
    assert audit_workspace_calls(
        [event], cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
        neutralized_attempts=neutralized,
    ) == ["workspace:shell-network"]
    assert not neutralized


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("setting", [
    "PIP_REQUIREMENT", "PIP_CONSTRAINT", "PIP_BUILD_CONSTRAINT",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FUTURE_SOURCE_SETTING",
])
def test_inherited_pip_sources_cannot_neutralize_truncated_success(tmp_path, harness, setting):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("--find-links https://example.org/wheels/\n")
    command = "pip download stripe==15.6.1 --no-deps -d downloads"
    output = "Successfully downloaded stripe"
    transcript = (
        [{"type": "command_execution", "id": "pip", "command": command,
          "status": "completed", "exit_code": 0, "aggregated_output": output}]
        if harness == "codex" else [
            {"type": "tool_use", "id": "pip", "name": "Bash", "input": {"command": command}},
            {"type": "tool_result", "tool_use_id": "pip", "content": output},
        ]
    )
    neutralized = set()
    assert audit_workspace_calls(
        transcript,
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null", setting: str(requirements)},
        neutralized_attempts=neutralized,
    ) == ["workspace:shell-network"]
    assert not neutralized


def test_arm_audit_records_neutralized_pip_separately():
    from sew.arms import ArmContract, audit_transcript

    event = {
        "type": "command_execution",
        "id": "pip",
        "command": "pip download stripe==15.6.1",
        "status": "completed",
        "aggregated_output": "Successfully downloaded stripe",
    }
    contract = ArmContract(
        kind="floor", harness_id="codex", provider_id="floor", workspace_profile=True
    )
    audit = audit_transcript(
        contract, [event], cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"}
    )
    assert not audit.contaminated
    assert audit.config_neutralized_network_attempts == 1
    assert audit.denied_network_attempts == 0


@pytest.mark.parametrize(
    "output",
    ["", "connection failed", "Successfully downloaded stripe\nhttps://example.com/stripe.whl"],
)
def test_pip_neutralization_requires_local_result_evidence(output):
    event = {
        "type": "command_execution",
        "id": "pip",
        "command": "pip download stripe==15.6.1",
        "status": "failed",
        "aggregated_output": output,
    }
    assert audit_workspace_calls(
        [event], cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"}
    ) == ["workspace:shell-network"]


@pytest.mark.parametrize(
    "command",
    [
        "printenv PIP_NO_INDEX; pip install stripe",
        "env -u A -u B pip install stripe",
        "env --unset=A pip install stripe",
    ],
)
def test_pip_offline_diagnostics_do_not_count_as_network(command):
    neutralized = set()
    assert not gap_workspace.shell_network_attempt(command)
    assert (
        audit_workspace_calls(
            [
                {
                    "type": "command_execution",
                    "id": "offline",
                    "command": command,
                    "status": "completed",
                    "exit_code": 0,
                    "aggregated_output": "Successfully installed foo-1.0",
                }
            ],
            cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
            neutralized_attempts=neutralized,
        )
        == []
    )
    assert not neutralized


@pytest.mark.parametrize(
    "wrapper",
    ["env -u A -u B", "env --unset=A", "env -i", "env -", "env --"],
)
def test_network_commands_after_env_options_are_detected(wrapper):
    assert gap_workspace.shell_network_attempt(f"{wrapper} curl https://example.com")


@pytest.mark.parametrize("option", ["--iso''lated", "--isol", "--index", "--extra-index"])
def test_pip_quote_removed_overrides_cannot_certify_success(option):
    command = f"pip download stripe {option} | tail -2"
    assert audit_workspace_calls(
        [
            {
                "type": "command_execution",
                "id": "pip",
                "command": command,
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "Successfully downloaded stripe",
            }
        ],
        cell_env={"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": "/dev/null"},
    ) == ["workspace:shell-network"]


@pytest.mark.parametrize(
    "command,expected",
    [
        ("pip install --no-index --find-links ../wheelhouse stripe==16.0.0", 1),
        ("pip download stripe", 1),
        ("curl https://example.org/stripe-16.0.0.whl", 1),
        ("pip install other==16.0.0", 1),
        ("python -c 'import stripe'", 0),
    ],
)
def test_new_package_attempt_is_behavior_evidence(gap_config, tmp_path, command, expected):
    contract = prepare_arm_spawn(gap_config, tmp_path, {}).contract
    event = {"type": "tool_use", "id": "attempt", "name": "Bash", "input": {"command": command}}
    audit = audit_transcript(
        contract, [event, event], new_packages=[{"name": "stripe", "version": "16.0.0"}]
    )
    assert audit.new_package_attempts == expected
    if command.startswith("pip install --no-index"):
        assert audit.contaminated  # Explicit find-links inputs are unverified.
