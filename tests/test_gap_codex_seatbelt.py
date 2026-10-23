"""Synthetic qualifications for Codex's single macOS sandbox boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sew.gap import sandbox, workspace


@pytest.mark.parametrize("mode", ["workspace-write", "read-only"])
@pytest.mark.parametrize("inline", [False, True])
def test_codex_bench_replaces_sandbox_option(mode, inline):
    option = [f"--sandbox={mode}"] if inline else ["--sandbox", mode]
    argv = ["codex", "exec", "--json", *option, "-"]
    original = list(argv)
    expected = ["--sandbox=danger-full-access"] if inline else ["--sandbox", "danger-full-access"]
    assert workspace._codex_bench_argv(argv) == ["codex", "exec", "--json", *expected, "-"]
    assert argv == original


@pytest.mark.parametrize("prefix", [
    ["codex"],
    ["codex", "exec"],
    ["codex", "--ephemeral", "exec"],
    ["codex", "--profile", "exec", "exec"],
    ["codex", "--profile", "exec", "--cd", "exec", "exec"],
])
def test_codex_bench_inserts_missing_sandbox_option(prefix):
    argv = [*prefix, "-"]
    assert workspace._codex_bench_argv(argv) == [
        prefix[0], "--sandbox", "danger-full-access", *prefix[1:], "-"
    ]
    assert argv == [*prefix, "-"]


@pytest.mark.parametrize("first", [["--sandbox", "workspace-write"], ["--sandbox=read-only"]])
@pytest.mark.parametrize("second", [
    ["--sandbox", "workspace-write"],
    ["--sandbox=read-only"],
    ["--sandbox", "danger-full-access"],
    ["--sandbox=unknown"],
])
def test_codex_bench_refuses_duplicate_sandbox_options(first, second):
    argv = ["codex", *first, "exec", "--json", *second, "-"]
    original = list(argv)
    with pytest.raises(workspace.EgressCanaryRefused, match="duplicate Codex cell sandbox options"):
        workspace._codex_bench_argv(argv)
    assert argv == original


@pytest.mark.parametrize("option", [
    ["--sandbox"],
    ["--sandbox", "--json"],
    ["--sandbox", "danger-full-access"],
    ["--sandbox", "unknown"],
    ["--sandbox="],
    ["--sandbox=danger-full-access"],
    ["--sandbox=unknown"],
])
def test_codex_bench_refuses_invalid_sandbox_mode(option):
    with pytest.raises(workspace.EgressCanaryRefused, match="invalid Codex cell sandbox mode"):
        workspace._codex_bench_argv(["codex", "exec", *option])


@pytest.fixture
def cell(tmp_path, monkeypatch):
    scratch = tmp_path / "cell"
    cwd = scratch / "workspace"
    cwd.mkdir(parents=True)
    (scratch / "wheelhouse").mkdir()
    cache = tmp_path / "runtime/cache"
    cache.mkdir(parents=True)
    (cache / "fixture-2.0-py3-none-any.whl").write_bytes(b"synthetic verifier wheel")
    monkeypatch.setattr(workspace, "VERIFIER_ROOT", tmp_path / "verifiers")
    monkeypatch.setattr(
        workspace, "_harness_endpoints", lambda *a, **kw: ({}, set(), set())
    )
    return scratch, cwd, cache


@pytest.mark.parametrize("harness", ["codex", "pi"])
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "cache-direct",
        "cache-discovery",
        "verifier-wheel",
        "verifier-source",
        "wheelhouse",
        "write",
        "egress",
        "exit",
        "json",
    ],
)
def test_exact_bench_profile_qualifies_before_admission(
    cell, monkeypatch, harness, failure
):
    scratch, cwd, cache = cell
    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("seatbelt", "/fixture/sandbox-exec"),
    )
    monkeypatch.setattr(
        workspace,
        "sandbox_probe_runner",
        lambda *a, **kw: pytest.fail(
            "bench-confined harness must never start an inner sandbox"
        ),
    )
    profiles = []
    probes = []

    def run(argv, **kwargs):
        assert argv[:2] == ["/fixture/sandbox-exec", "-p"]
        profiles.append(argv[2])
        assert "(deny network-outbound)" in argv[2]
        assert "(deny file-write* (require-not" in argv[2]
        assert str(cache.resolve()) in argv[2]
        assert str(workspace.VERIFIER_ROOT) in argv[2]
        command = argv[3:]
        program = command[command.index("-c") + 1]
        args = command[command.index("-c") + 2 :]
        if program == workspace.DIRECT_PROBE:
            probes.append("egress")
            if failure == "exit":
                return subprocess.CompletedProcess(
                    argv, 71, "", "synthetic startup failure"
                )
            if failure == "json":
                return subprocess.CompletedProcess(argv, 0, "invalid JSON", "")
            result = {"connected": failure == "egress", "errno": 1}
        else:
            if program == workspace.CACHE_DIRECT_PROBE:
                target = Path(args[0])
                probe = (
                    ("verifier-source" if target.suffix == ".py" else "verifier-wheel")
                    if target.is_relative_to(workspace.VERIFIER_ROOT)
                    else "cache-direct"
                )
                nonce = args[1]
            elif program == workspace.CACHE_DISCOVERY_PROBE:
                probe, nonce = "cache-discovery", args[-1]
            elif program == workspace.WHEELHOUSE_WRITE_PROBE:
                probe, nonce = "wheelhouse", args[-1]
            elif program == workspace.WRITE_ESCAPE_PROBE:
                probe, nonce = "write", args[-1]
                if failure == "write":
                    Path(args[0]).write_text(nonce)
            else:
                pytest.fail("unexpected probe")
            probes.append(probe)
            denied = probe != failure
            result = {"nonce": nonce, "denied": denied}
            if probe == "wheelhouse":
                result["denied"] = {
                    name: denied
                    for name in (
                        "create",
                        "modify",
                        "chmod",
                        "link",
                        "rename_wheelhouse",
                        "rename_scratch",
                    )
                }
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    original = (
        [harness, "exec", "--sandbox", "workspace-write", "-"]
        if harness == "codex"
        else [harness]
    )
    env = {"HOME": str(scratch.parent / "host-home")}
    receipt = scratch / "refusal.json"
    if failure:
        with pytest.raises(workspace.EgressCanaryRefused):
            with workspace.bench_network_boundary(
                original,
                env,
                cwd=cwd,
                harness_id=harness,
                wheelhouse=cache,
                harness_auth="account" if harness == "codex" else "litellm",
                refusal_path=receipt,
            ):
                pytest.fail("failed qualification admitted")
        assert json.loads(receipt.read_text())["admissible"] is False
        return
    with workspace.bench_network_boundary(
        original,
        env,
        cwd=cwd,
        harness_id=harness,
        wheelhouse=cache,
        harness_auth="account" if harness == "codex" else "litellm",
    ) as (argv, evidence):
        assert all(profile == argv[2] for profile in profiles)
        assert {
            "cache-direct",
            "cache-discovery",
            "verifier-wheel",
            "verifier-source",
            "wheelhouse",
            "write",
            "egress",
        } <= set(probes)
        assert evidence["runner_id"] == "bench-seatbelt"
        assert evidence["policy_scope"] == "harness-tree-profile"
        assert evidence["harness_tree_confined"] is True
        assert evidence["write_escape"]["reached_host"] is False
        assert evidence["wheel_cache_isolation"]["discovery_denied"] is True
        assert Path(env["HOME"]).is_relative_to(scratch)
        assert Path(env["TMPDIR"]).is_relative_to(scratch)
        assert env["HTTPS_PROXY"] == env["https_proxy"]
        if harness == "codex":
            assert argv[3:] == [harness, "exec", "--sandbox", "danger-full-access", "-"]
            assert original[3] == "workspace-write"
            assert evidence["inner_sandbox_enabled"] is False
            assert evidence["policy"]["harness_sandbox_mode"] == "danger-full-access"
        else:
            assert argv[3:] == original
            assert "inner_sandbox_enabled" not in evidence


def test_codex_real_seatbelt_denies_reads_renames_and_write_escape(
    cell, tmp_path, require_containment
):
    require_containment("seatbelt")
    scratch, cwd, cache = cell
    binary = tmp_path / "fake-codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import errno, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[sys.argv.index('--sandbox') + 1] == 'danger-full-access'\n"
        "assert Path(os.environ['CODEX_HOME']).is_relative_to(Path.cwd().parent)\n"
        "assert Path(os.environ['HOME']).is_relative_to(Path.cwd().parent)\n"
        "for name in ('CACHE_WHEEL', 'VERIFIER_SOURCE'):\n"
        "    try: Path(os.environ[name]).read_bytes()\n"
        "    except OSError as exc: assert exc.errno in (errno.EPERM, errno.EACCES)\n"
        "    else: raise AssertionError('private source exposed')\n"
        "for name in ('CACHE_ROOT', 'VERIFIER_ROOT', 'WHEELHOUSE', 'CELL_ROOT'):\n"
        "    path = Path(os.environ[name])\n"
        "    try: path.rename(path.with_name(path.name + '-moved'))\n"
        "    except OSError as exc: assert exc.errno in (errno.EPERM, errno.EACCES)\n"
        "    else: raise AssertionError('protected root renamed')\n"
        "try: Path(os.environ['OUTSIDE_WRITE']).write_text('escaped')\n"
        "except OSError as exc: assert exc.errno in (errno.EPERM, errno.EACCES)\n"
        "else: raise AssertionError('outside write allowed')\n"
        "# Descendants inherit the same boundary, including non-shell reads.\n"
        "p = subprocess.run([sys.executable, '-c', "
        "'import os; from pathlib import Path; Path(os.environ[\"CACHE_WHEEL\"]).read_bytes()'], "
        "capture_output=True)\n"
        "assert p.returncode != 0\n"
        "Path('allowed.txt').write_text('cell output')\n"
    )
    binary.chmod(0o755)
    home = scratch / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('approval_policy = "never"\n')
    workspace.VERIFIER_ROOT.mkdir()
    source = workspace.VERIFIER_ROOT / "source.py"
    source.write_bytes(b"synthetic verifier source")
    outside = tmp_path / "outside.txt"
    env = {
        "PATH": os.defpath,
        "CODEX_HOME": str(home),
        "CACHE_ROOT": str(cache.parent),
        "VERIFIER_ROOT": str(workspace.VERIFIER_ROOT),
        "WHEELHOUSE": str(scratch / "wheelhouse"),
        "CELL_ROOT": str(scratch),
        "CACHE_WHEEL": str(next(cache.glob("*.whl"))),
        "VERIFIER_SOURCE": str(source),
        "OUTSIDE_WRITE": str(outside),
    }
    with workspace.bench_network_boundary(
        [str(binary), "exec", "--sandbox", "workspace-write"],
        env,
        cwd=cwd,
        harness_id="codex",
        wheelhouse=cache,
    ) as (argv, evidence):
        result = subprocess.run(
            argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert evidence["wheel_cache_isolation"]["verifier_copies"]["installed_source"][
            "direct_denied"
        ]
        assert all(
            evidence["wheel_cache_isolation"]["cell_wheelhouse"]["denied"].values()
        )
    assert not outside.exists()
    assert (cwd / "allowed.txt").read_text() == "cell output"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize("pip_denial", [True, False])
def test_codex_canary_exposes_proxy_denial_reason(
    monkeypatch, capsys, platform, pip_denial
):
    nonce, command = workspace.canary_command("codex")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        # Synthetic client diagnostics expose the proxy's attributable reason
        # only when curl and pip run verbose.
        reason = "403 Permission denied: endpoint not allowlisted"
        if argv[0] == "curl":
            error = (
                "< HTTP/1.0 " + reason
                if "--verbose" in argv
                else "curl: (56) CONNECT tunnel failed, response 403"
            )
            return subprocess.CompletedProcess(argv, 56, "", error)
        if argv[1:3] == ["-m", "pip"]:
            error = (
                "ProxyError: Tunnel connection failed: " + reason
                if "-vv" in argv and pip_denial
                else "ERROR: Could not find a version that satisfies the requirement pip "
                "(from versions: none)\nERROR: No matching distribution found for pip"
            )
        else:
            error = "URLError: <urlopen error Tunnel connection failed: " + reason + ">"
        return subprocess.CompletedProcess(argv, 1, "", error)

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "argv", ["canary", "codex", nonce])
    monkeypatch.setattr(subprocess, "run", run)
    exec(workspace.CANARY_PROGRAM, {})
    output = capsys.readouterr().out
    event = {
        "type": "item.completed",
        "item": {
            "id": "synthetic-canary",
            "type": "command_execution",
            "command": command,
            "aggregated_output": output,
            "exit_code": 0,
            "status": "completed",
        },
    }
    assert ("--verbose" in calls[0]) == (platform == "darwin")
    assert ("-vv" in calls[1]) == (platform == "darwin")
    if platform == "darwin" and pip_denial:
        record = workspace.require_canary([event], nonce, command, harness_id="codex")
        assert all(
            probe["attribution"] == "denial-text" for probe in record["probes"].values()
        )
    else:
        with pytest.raises(workspace.EgressCanaryRefused, match="denial unproven"):
            workspace.require_canary([event], nonce, command, harness_id="codex")
