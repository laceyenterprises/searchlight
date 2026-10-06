"""Claude qualification never invokes an evaluated model."""

import json
import subprocess
import signal
from pathlib import Path

import pytest
from sew.gap import workspace
from sew.live_harness import ARTIFACT_CAP_BYTES


@pytest.mark.parametrize("egress", [False, True])
@pytest.mark.parametrize("runner_flags", [[], ["--verbose"]])
@pytest.mark.parametrize("command_prefix", [[], ["sandbox-exec", "-p", "cache-read-denial"]])
def test_bench_probes_require_each_denial(tmp_path, monkeypatch, egress, runner_flags, command_prefix):
    prefix = ["srt", *runner_flags, "--settings", "settings.json"]
    monkeypatch.setattr(
        workspace,
        "sandbox_probe_runner",
        lambda *a, **kw: (
            [*prefix, "--", "python", "-c", workspace.DIRECT_PROBE],
            {"attestation": {"sandbox_enabled": True}},
        ),
    )

    def run(argv, **kw):
        assert kw["timeout"] == 75
        assert kw["process_group"] is True
        assert argv[:len(command_prefix)] == command_prefix
        argv = argv[len(command_prefix):]
        assert argv[: len(prefix) + 2] == [*prefix, "--debug", "--"]
        assert argv[len(prefix) + 4] == workspace.CANARY_PROGRAM
        nonce = argv[-1]
        record = {
            "nonce": nonce,
            "probes": {
                name: {"exit_code": 0 if egress else 1, "error": "403"}
                for name in ("curl", "pip", "https")
            },
        }
        stderr = "\n".join(
            f"[SandboxDebug] Connection blocked to {host}:443"
            for host in (nonce + ".example.com", "pypi.org", nonce + ".example.org")
        )
        return subprocess.CompletedProcess(argv, 0, "GAP_CANARY:" + json.dumps(record), stderr)

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    path = tmp_path / "egress-canary.json"
    direct = {"connected": False, "errno": 1, "attribution": "harness-runner-permission:srt"}
    if egress:
        with pytest.raises(workspace.EgressCanaryRefused, match="egress reached"):
            workspace.qualify_claude_code(
                ["claude"], {}, cwd=tmp_path, refusal_path=path, direct_evidence=direct,
                command_prefix=command_prefix,
            )
    else:
        evidence = workspace.qualify_claude_code(
            ["claude"], {}, cwd=tmp_path, refusal_path=path, direct_evidence=direct,
            command_prefix=command_prefix,
        )
        assert evidence["admissible"]
        assert evidence["usage"] == {}
        assert "events" not in evidence  # No model turn or safety verdict is consumed.
    retained = json.loads(path.read_text())
    assert retained["admissible"] is not egress
    assert retained["direct_egress"] == direct
    if egress:
        assert "egress reached" in retained["reason"]


@pytest.mark.parametrize(
    "mismatch",
    [
        "enabled",
        "failIfUnavailable",
        "allowUnsandboxedCommands",
        "excludedCommands",
        "strictAllowlist",
        "allowLocalBinding",
        "allowAllUnixSockets",
        "claude",
        "srt",
    ],
)
def test_attestation_refuses_mismatch(tmp_path, monkeypatch, mismatch):
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "excludedCommands": [],
        "network": {
            "allowedDomains": [],
            "deniedDomains": ["*"],
            "strictAllowlist": True,
            "allowLocalBinding": False,
            "allowAllUnixSockets": False,
        },
    }
    if mismatch in sandbox:
        sandbox[mismatch] = {
            "enabled": False,
            "failIfUnavailable": False,
            "allowUnsandboxedCommands": True,
            "excludedCommands": ["curl"],
        }[mismatch]
    if mismatch in sandbox["network"]:
        sandbox["network"][mismatch] = not sandbox["network"][mismatch]
    settings = tmp_path / "claude.json"
    settings.write_text(json.dumps({"sandbox": sandbox}))
    monkeypatch.setattr(workspace.shutil, "which", lambda *a, **kw: "srt")

    def run(argv, **kw):
        version = "2.1.282 (Claude Code)" if argv[0] == "claude" else "0.0.78"
        return subprocess.CompletedProcess(argv, 0, "wrong" if argv[0] == mismatch else version, "")

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    with pytest.raises(workspace.EgressCanaryRefused):
        workspace.sandbox_probe_runner(
            ["claude", "--settings", str(settings)],
            {},
            cwd=tmp_path,
            harness_id="claude-code",
            scratch=tmp_path,
        )


def test_claude_branch_contains_no_model_probe_instruction():
    source = (Path(workspace.__file__).parent.parent / "live_harness.py").read_text()
    branch = source.split(
        'if config.harness_id == "claude-code":\n                        from .gap.workspace import qualify_claude_code',
        1,
    )[1].split("                    else:", 1)[0]
    assert "qualify_claude_code(" in branch
    assert "spawn_and_capture" not in branch
    assert "prompt=" not in branch


@pytest.mark.parametrize("stage", ["policy", "version", "missing-srt", "timeout", "failed-probe"])
def test_every_qualification_refusal_retains_scrubbed_evidence(tmp_path, monkeypatch, stage):
    path = tmp_path / "egress-canary.json"
    direct = {"connected": False, "errno": 1, "attribution": "harness-runner-permission:srt"}
    attestation = {"sandbox_enabled": True}
    secret = "private-oauth-token"

    def runner(*args, **kwargs):
        if stage in {"policy", "version", "missing-srt"}:
            raise workspace.EgressCanaryRefused(stage + " mismatch " + secret)
        return ["srt", "--settings", "settings.json", "--", "python"], attestation

    def run(argv, **kwargs):
        if stage == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(
            argv, 1, "", secret + " " + "x" * (ARTIFACT_CAP_BYTES * 2)
        )

    monkeypatch.setattr(workspace, "sandbox_probe_runner", runner)
    monkeypatch.setattr(workspace, "_run_probe_process", run)
    with pytest.raises(workspace.EgressCanaryRefused):
        workspace.qualify_claude_code(
            ["claude"],
            {"ANTHROPIC_AUTH_TOKEN": secret},
            cwd=tmp_path,
            refusal_path=path,
            direct_evidence=direct,
        )
    text = path.read_text()
    evidence = json.loads(text)
    assert secret not in text
    assert len(text.encode()) <= ARTIFACT_CAP_BYTES
    assert not evidence["admissible"]
    assert evidence["direct_egress"] == direct
    assert evidence["reason"] and len(evidence["reason"]) <= 500
    if stage == "timeout":
        assert "timed out after 75s" in evidence["reason"]
    assert evidence["attestation"] == (attestation if stage in {"timeout", "failed-probe"} else {})


def test_qualification_timeout_kills_each_process_group_before_retry(tmp_path, monkeypatch):
    operations = []
    monkeypatch.setattr(workspace.time, "sleep", lambda delay: operations.append(("delay", delay)))
    monkeypatch.setattr(
        workspace.os, "killpg", lambda pid, sig: operations.append(("kill", pid, sig))
    )

    class Process:
        pid = 12345

        def __init__(self, argv, **kwargs):
            assert kwargs["start_new_session"] is True
            operations.append(("spawn",))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, timeout=None):
            if timeout is not None:
                assert timeout == 75
                raise subprocess.TimeoutExpired(["srt"], timeout)
            operations.append(("reap",))
            return "", ""

    monkeypatch.setattr(workspace.subprocess, "Popen", Process)
    with pytest.raises(subprocess.TimeoutExpired):
        workspace._run_probe_process(["srt"], cwd=tmp_path, env={}, timeout=75, process_group=True)
    assert operations == [
        ("spawn",),
        ("kill", 12345, signal.SIGKILL),
        ("reap",),
        ("delay", 0.5),
        ("spawn",),
        ("kill", 12345, signal.SIGKILL),
        ("reap",),
        ("delay", 1),
        ("spawn",),
        ("kill", 12345, signal.SIGKILL),
        ("reap",),
    ]
