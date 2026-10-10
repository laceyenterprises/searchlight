"""GAP code cells for the OSS harnesses: Hermes, Opencode and Pi (OHM-08).

Offline only. Fake harness binaries replay each harness's recorded event shape;
a local stub stands in for LiteLLM. No real harness, model, provider or LiteLLM
call is made, and the live gate is only ever set in a fake-spawn environ map.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from sew import harnesses, live_harness
from sew.arms import PROVIDER_SERVER_NAMES, prepare_arm_spawn
from sew.gap import workspace as gap_workspace
from sew.gap.workspace import (
    CANARY_PROGRAM,
    PORTABLE_CANARY_PROGRAM,
    EgressCanaryRefused,
    audit_workspace_calls,
    bench_network_boundary,
    canary_command,
    require_canary,
)
from sew.harness import HarnessRunConfig, ProviderExposure
from sew.harnesses.hermes_protocol import HermesProtocol
from sew.live_harness import ProcessOutcome, run_live_harness

OSS = ("hermes", "opencode", "pi")
ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "fixtures" / "gap"
STRICT_PIP_ENV = {
    "PIP_NO_INDEX": "1",
    "PIP_CONFIG_FILE": "/dev/null",
    "PIP_FIND_LINKS": "/tmp/cell/wheelhouse",
}
PIP_PIN = "stripe==15.6.1"
PROXY_DENIAL = "< HTTP/1.1 403 Permission denied: endpoint not allowlisted"
OS_DENIAL = "* Immediate connect fail for 1.1.1.1: Operation not permitted"


def shell_events(harness, call_id, command, output, exit_code=0):
    """One shell call and its result, in each harness's recorded event shape."""
    if harness == "codex":
        return [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "id": call_id,
                    "command": command,
                    "aggregated_output": output,
                    "exit_code": exit_code,
                    "status": "failed" if exit_code else "completed",
                },
            }
        ]
    if harness == "hermes":
        # HermesProtocol.poll_events' rendering of its state.db rows.
        return [
            {
                "type": "tool_call",
                "id": call_id,
                "name": "terminal",
                "arguments": json.dumps({"command": command}),
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": json.dumps(
                                {"output": output, "exit_code": exit_code, "error": None}
                            ),
                        }
                    ]
                },
            },
        ]
    if harness == "opencode":
        # `opencode run --format json` emits one tool_use event per finished part.
        return [
            {
                "type": "tool_use",
                "sessionID": "ses_fixture",
                "part": {
                    "id": "prt_" + call_id,
                    "type": "tool",
                    "callID": call_id,
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": command, "description": "run"},
                        "output": output,
                        "metadata": {"output": output, "exit": exit_code, "description": "run"},
                    },
                },
            }
        ]
    assert harness == "pi"
    # `pi --mode json` repeats the call in message_end and turn_end, and the
    # result in message_start and message_end; a failure appends its status.
    text = output
    if exit_code:
        text = (output + "\n\n" if output else "") + f"Command exited with code {exit_code}"
    call = {
        "role": "assistant",
        "content": [
            {"type": "toolCall", "id": call_id, "name": "bash", "arguments": {"command": command}}
        ],
        "stopReason": "toolUse",
    }
    result = {
        "role": "toolResult",
        "toolCallId": call_id,
        "toolName": "bash",
        "content": [{"type": "text", "text": text}],
        "isError": bool(exit_code),
    }
    return [
        {"type": "message_end", "message": call},
        {"type": "message_start", "message": result},
        {"type": "message_end", "message": result},
        {"type": "turn_end", "message": call, "toolResults": [result]},
    ]


def captured(events):
    return [{"event": event, "received_at": "2026-10-10T00:00:00Z"} for event in events]


def canary_output(nonce, denial=PROXY_DENIAL, **overrides):
    probes = {name: {"exit_code": 1, "error": denial} for name in ("curl", "pip", "https")}
    probes.update(overrides)
    return "GAP_CANARY:" + json.dumps({"nonce": nonce, "probes": probes})


def test_oss_harnesses_select_the_portable_sandbox_and_canary():
    for harness in OSS:
        assert harnesses.get(harness).code_cell_sandbox == "portable"
        nonce, command = canary_command(harness)
        assert shlex.split(command) == ["python3", "-c", PORTABLE_CANARY_PROGRAM, harness, nonce]
    # Hosted harnesses keep their canary byte for byte.
    for harness in ("claude-code", "codex"):
        assert shlex.split(canary_command(harness)[1])[2] == CANARY_PROGRAM
    # The model is never asked to bypass the proxy, and every target is an address.
    assert "--noproxy" not in PORTABLE_CANARY_PROGRAM and "bypass" not in PORTABLE_CANARY_PROGRAM
    assert "example." not in PORTABLE_CANARY_PROGRAM and "pypi.org" not in PORTABLE_CANARY_PROGRAM
    with pytest.raises(EgressCanaryRefused, match="unsupported canary harness"):
        canary_command("fixture")


@pytest.mark.parametrize("harness", OSS)
@pytest.mark.parametrize("denial", [PROXY_DENIAL, OS_DENIAL])
@pytest.mark.parametrize("wrapped", [False, True])
def test_canary_accepts_each_oss_event_shape(harness, denial, wrapped):
    nonce, command = canary_command(harness)
    observed = shlex.join(["/bin/bash", "-lc", command]) if wrapped else command
    events = captured(shell_events(harness, "canary", observed, canary_output(nonce, denial)))
    record = require_canary(events, nonce, command, harness_id=harness)
    assert record["nonce"] == nonce
    assert {probe["attribution"] for probe in record["probes"].values()} == {"denial-text"}


def pi_streaming_canary(nonce, command):
    fixture = (GOLDEN / "pi-streaming-canary.json").read_text()
    return json.loads(
        fixture.replace('"CANARY_COMMAND"', json.dumps(command))
        .replace('"CANARY_OUTPUT"', json.dumps(canary_output(nonce)))
    )


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("late_snapshot", [False, True])
def test_pi_canary_accepts_cumulative_streaming_snapshots(wrapped, late_snapshot):
    nonce, command = canary_command("pi")
    events = pi_streaming_canary(nonce, command)
    if late_snapshot:
        events += events[1:3]
    if wrapped:
        events = [{"harness_event": event} for event in events]
    record = require_canary(captured(events), nonce, command, harness_id="pi")
    assert record["nonce"] == nonce


@pytest.mark.parametrize("defect", ["extra-call", "other-tool", "wrong-command", "unfinished"])
def test_pi_streaming_canary_still_requires_one_exact_completed_call(defect):
    nonce, command = canary_command("pi")
    events = pi_streaming_canary(nonce, command)
    if defect == "extra-call":
        events += shell_events("pi", "extra", command, canary_output(nonce))
    elif defect == "other-tool":
        events.append({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "extra", "name": "read", "arguments": {"path": "."}}
        ]}})
    elif defect == "wrong-command":
        for event in events:
            if event["type"] in {"message_end", "turn_end"} and event["message"]["role"] == "assistant":
                event["message"]["content"][0]["arguments"]["command"] = "echo forged"
    else:
        # A final streaming snapshot plus a result is not a completed request.
        events = [event for event in events if not (
            event["type"] in {"message_end", "turn_end"} and event["message"]["role"] == "assistant"
        )]
    with pytest.raises(EgressCanaryRefused):
        require_canary(captured(events), nonce, command, harness_id="pi")


@pytest.mark.parametrize("harness", OSS)
@pytest.mark.parametrize(
    "failure",
    [
        "reached",
        "timeout",
        "missing-probe",
        "wrong-nonce",
        "wrong-command",
        "extra-call",
        "other-tool",
        "call-without-result",
        "claimed-in-prose",
        "hosted-shape",
    ],
)
def test_canary_fails_closed_on_each_oss_event_shape(harness, failure):
    nonce, command = canary_command(harness)
    output = canary_output(nonce)
    if failure == "reached":
        output = canary_output(nonce, https={"exit_code": 0, "error": ""})
    elif failure == "timeout":
        output = canary_output(nonce, curl={"exit_code": 28, "error": "Connection timed out"})
    elif failure == "missing-probe":
        record = json.loads(output.removeprefix("GAP_CANARY:"))
        del record["probes"]["pip"]
        output = "GAP_CANARY:" + json.dumps(record)
    elif failure == "wrong-nonce":
        output = canary_output("0" * 32)
    events = shell_events(
        "codex" if failure == "hosted-shape" else harness,
        "canary",
        "echo forged" if failure == "wrong-command" else command,
        output,
    )
    if failure == "extra-call":
        events += shell_events(harness, "again", command, output)
    elif failure == "other-tool":
        events.insert(0, {"type": "tool_call", "id": "r", "name": "read_file", "arguments": "{}"})
    elif failure == "call-without-result" and harness == "opencode":
        # One part carries both; an unfinished part has no result yet.
        state = events[0]["part"]["state"]
        state["status"] = "running"
        del state["output"], state["metadata"]
    elif failure == "call-without-result":
        events = events[:1]
    elif failure == "claimed-in-prose":
        events = [{"type": "hermes.assistant", "text": output}, {"type": "text", "part": {"text": output}}]
    with pytest.raises(EgressCanaryRefused):
        require_canary(captured(events), nonce, command, harness_id=harness)


@pytest.mark.parametrize("harness", OSS)
def test_hosted_canary_refuses_oss_event_shapes(harness):
    # An OSS transcript never qualifies a hosted harness, nor the reverse.
    nonce, command = canary_command("codex")
    events = captured(shell_events(harness, "canary", command, canary_output(nonce)))
    with pytest.raises(EgressCanaryRefused):
        require_canary(events, nonce, command, harness_id="codex")


# (command, output, exit code): each GAPATTEMPT/GAPPIP outcome class at least once.
AUDIT_CORPUS = [
    ("pytest -q", "1 passed", 0),
    ("curl https://example.org", "curl: (6) Could not resolve host: example.org", 6),
    (
        "python3 -c \"import urllib.request; urllib.request.urlopen('https://example.org')\"",
        "Traceback (most recent call last):\n"
        "urllib.error.URLError: <urlopen error [Errno 1] Operation not permitted>",
        1,
    ),
    (
        "python3 -c \"import urllib.request; urllib.request.urlopen('https://example.org')\"",
        "urllib.error.HTTPError: HTTP Error 403: Permission denied: endpoint not allowlisted",
        1,
    ),
    (f"pip download {PIP_PIN}", "Successfully downloaded stripe", 0),
    (f"pip install --no-index --find-links /tmp/cell/wheelhouse {PIP_PIN}", "Successfully installed stripe-15.6.1", 0),
    (f"pip download {PIP_PIN} --index-url https://example.org/simple", "Successfully downloaded stripe", 0),
    (f"pip download {PIP_PIN} && curl https://example.org", "Successfully downloaded stripe", 0),
    (f"echo 'pip download {PIP_PIN}'", f"pip download {PIP_PIN}", 0),
    ("cat ../catalogs/gap/hidden/test_task.py", "hidden", 0),
]


def audit(events):
    denied, neutralized = set(), set()
    violations = audit_workspace_calls(
        events, cell_env=STRICT_PIP_ENV, denied_attempts=denied, neutralized_attempts=neutralized
    )
    return violations, len(denied), len(neutralized)


@pytest.mark.parametrize("harness", OSS)
@pytest.mark.parametrize("command, output, exit_code", AUDIT_CORPUS)
def test_shell_audit_matches_codex_for_each_oss_event_shape(harness, command, output, exit_code):
    # The shell-tool list, denial pairing and strict pip allowlist read every
    # OSS shape exactly as they read the same Codex command.
    expected = audit(shell_events("codex", "c1", command, output, exit_code))
    assert audit(shell_events(harness, "c1", command, output, exit_code)) == expected


def test_audit_corpus_covers_every_outcome():
    outcomes = [audit(shell_events("codex", "c1", *case)) for case in AUDIT_CORPUS]
    assert ([], 0, 0) in outcomes  # clean
    assert (["workspace:shell-network"], 0, 0) in outcomes  # unattributed attempt
    assert ([], 1, 0) in outcomes  # attributable denial
    assert ([], 0, 1) in outcomes  # strict pip allowlist
    assert (["workspace:forbidden-read"], 0, 0) in outcomes


@pytest.mark.parametrize(
    "event",
    [
        {"type": "tool_call", "id": "r", "name": "read_file", "arguments": json.dumps({"path": "HIDDEN"})},
        {"type": "tool_call", "id": "w", "name": "write_file", "arguments": json.dumps({"path": "HIDDEN"})},
        {"type": "tool_call", "id": "p", "name": "patch", "arguments": json.dumps({"path": "HIDDEN"})},
        {"type": "tool_use", "part": {"type": "tool", "callID": "r", "tool": "read",
                                      "state": {"status": "completed", "input": {"filePath": "HIDDEN"}}}},
        {"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "r", "name": "read", "arguments": {"path": "HIDDEN"}}]}},
    ],
    ids=["hermes-read", "hermes-write", "hermes-patch", "opencode-read", "pi-read"],
)
def test_oss_file_tools_cannot_read_hidden_assets(tmp_path, event):
    hidden = tmp_path / "module" / "catalogs" / "gap" / "hidden" / "test_task.py"
    event = json.loads(json.dumps(event).replace("HIDDEN", str(hidden)))
    assert audit_workspace_calls([event], forbidden_paths=[hidden.parent]) == [
        "workspace:forbidden-read"
    ]


@pytest.mark.parametrize("tool", ["search_files", "grep", "glob", "list"])
@pytest.mark.parametrize("destination", ["catalog", "hidden", "cache", "verifier", "symlink", "allowed"])
@pytest.mark.parametrize("relative", [False, True])
def test_oss_file_search_tools_audit_forbidden_paths(tmp_path, tool, destination, relative):
    root = tmp_path / "module"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = {
        "catalog": root / "catalogs" / "gap",
        "hidden": root / "catalogs" / "gap" / "hidden",
        "cache": tmp_path / "verifier-cache",
        "verifier": gap_workspace.VERIFIER_ROOT,
        "symlink": workspace / "alias",
        "allowed": workspace / "src",
    }
    paths["symlink"].symlink_to(paths["hidden"], target_is_directory=True)
    path = paths[destination]
    path = os.path.relpath(path, workspace) if relative else str(path)
    arguments = {"path": path, "pattern": ".", "target": "content"}
    if tool == "search_files":
        event = {"type": "tool_call", "id": "s", "name": tool, "arguments": json.dumps(arguments)}
    else:
        event = {"type": "tool_use", "part": {"type": "tool", "callID": "s", "tool": tool,
                 "state": {"status": "completed", "input": arguments}}}
    assert audit_workspace_calls(
        [event], cwd=workspace, forbidden_paths=[root, paths["cache"]]
    ) == ([] if destination == "allowed" else ["workspace:forbidden-read"])


def test_opencode_glob_cannot_search_forbidden_paths_via_pattern(tmp_path):
    forbidden = tmp_path / "catalog"
    event = {"type": "tool_use", "part": {"type": "tool", "callID": "g", "tool": "glob",
             "state": {"status": "completed", "input": {"pattern": str(forbidden / "**/*.py")}}}}
    assert audit_workspace_calls([event], forbidden_paths=[forbidden]) == ["workspace:forbidden-read"]


@pytest.fixture
def fake_pi(tmp_path):
    path = tmp_path / "bin" / "pi"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\necho 0.79.8\n")
    path.chmod(0o755)
    return path


def code_config(harness, arm, **kwargs):
    provider = None
    if arm in PROVIDER_SERVER_NAMES:
        provider = ProviderExposure(
            provider_id=arm,
            tool_name="web_search_exa",
            mcp_server_name=PROVIDER_SERVER_NAMES[arm],
            mcp_server_config={"command": "stub-mcp", "args": [], "env": {}},
        )
    return HarnessRunConfig(
        harness_id=harness,
        provider_id=arm,
        task_id="fake",
        mode="live",
        model_id="litellm/glm-5.2",
        native_search_available=False,
        external_provider=provider,
        **kwargs,
    )


@pytest.mark.parametrize("harness", OSS)
@pytest.mark.parametrize("arm", ["no-search", "exa"])
def test_code_cell_surface_enables_shell_and_reads_endpoint_from_env(tmp_path, fake_pi, harness, arm):
    source = {"SEW_OSS_ENABLED": "1", "SEW_PI_BIN": str(fake_pi), "PATH": os.defpath}
    surfaces = {}
    for workspace in (False, True):
        scratch = tmp_path / f"cell-{workspace}"
        scratch.mkdir()
        config = code_config(harness, arm, workspace_profile=workspace)
        surfaces[workspace] = prepare_arm_spawn(config, scratch, source, harness_auth="litellm")
    search, code = surfaces[False], surfaces[True]
    if harness == "hermes":
        documents = {key: yaml.safe_load(s.mcp_config_path.read_text()) for key, s in surfaces.items()}
        document = documents[True]
        assert {"terminal", "file"} <= set(document["platform_toolsets"]["cli"])
        assert not {"terminal", "file"} & set(document["agent"]["disabled_toolsets"])
        assert {"web", "browser", "code_execution", "delegation"} <= set(
            document["agent"]["disabled_toolsets"]
        )
        assert document["custom_providers"][0]["base_url"] == "${SEW_LITELLM_BASE_URL}/v1"
        assert code.harness_args == (
            "--ignore-rules", "-t", "terminal,file" + (",exa" if arm == "exa" else "")
        )
        assert set(document["mcp_servers"]) == set(code.contract.allowed_mcp_servers)
        # Search cells keep the shell off and the literal endpoint.
        assert "terminal" in documents[False]["agent"]["disabled_toolsets"]
        assert documents[False]["custom_providers"][0]["base_url"] == "http://127.0.0.1:4000/v1"
    elif harness == "opencode":
        documents = {key: json.loads(s.mcp_config_path.read_text()) for key, s in surfaces.items()}
        document = documents[True]
        for tool in ("bash", "read", "write", "edit"):
            assert document["tools"][tool] is True and document["permission"][tool] == "allow"
        assert document["tools"]["webfetch"] is False and document["tools"]["websearch"] is False
        assert document["permission"]["*"] == "deny"
        options = document["provider"]["searchlight-litellm"]["options"]
        assert options["baseURL"] == "{env:SEW_LITELLM_BASE_URL}/v1"
        assert documents[False]["tools"]["bash"] is False
        assert documents[False]["provider"]["searchlight-litellm"]["options"]["baseURL"] == (
            "http://127.0.0.1:4000/v1"
        )
    else:
        cells = {key: json.loads(s.mcp_config_path.read_text()) for key, s in surfaces.items()}
        assert cells[True]["baseUrlEnv"] == "SEW_LITELLM_BASE_URL" and "baseUrl" not in cells[True]
        assert cells[False]["baseUrl"] == "http://127.0.0.1:4000/v1"
        assert "--no-builtin-tools" not in code.harness_args
        assert "--no-builtin-tools" in search.harness_args
        assert "--no-extensions" in code.harness_args


def test_pi_extension_reads_code_cell_endpoint_from_env(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Pi provider extension test requires Node.js")
    cell = tmp_path / "cell.json"
    cell.write_text(json.dumps({"baseUrlEnv": "SEW_LITELLM_BASE_URL", "route": "glm-5.2",
                                "contextWindow": 1000, "maxTokens": 100}))
    script = tmp_path / "fake-pi.mjs"
    script.write_text(
        "const providers = [];\n"
        "const provider = await import(process.argv[2]);\n"
        "provider.default({registerProvider: (id, p) => providers.push(p)});\n"
        "console.log(providers[0].baseUrl);\n"
    )
    env = {"PATH": os.environ["PATH"], "SEW_PI_CELL_CONFIG": str(cell),
           "SEW_LITELLM_API_KEY": "offline-placeholder"}
    extension = (ROOT / "lib/python/sew/harnesses/pi_extensions/litellm.mjs").as_uri()
    result = subprocess.run(
        [node, str(script), extension],
        env={**env, "SEW_LITELLM_BASE_URL": "http://127.0.0.1:40123/"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://127.0.0.1:40123/v1"
    # A code cell without the endpoint fails rather than guessing one.
    missing = subprocess.run([node, str(script), extension], env=env, capture_output=True,
                             text=True, timeout=10)
    assert missing.returncode != 0 and "LiteLLM endpoint is required" in missing.stderr


@pytest.mark.parametrize("harness", OSS)
def test_oss_code_cells_reach_only_litellm(harness):
    env = {"SEW_LITELLM_BASE_URL": "http://127.0.0.1:4000"}
    with pytest.raises(EgressCanaryRefused, match="require LiteLLM auth"):
        gap_workspace._harness_endpoints(env, harness, harness_auth="account")
    with pytest.raises(EgressCanaryRefused, match="missing LiteLLM endpoint"):
        gap_workspace._harness_endpoints({}, harness, harness_auth="litellm")
    _, _, authorities = gap_workspace._harness_endpoints(env, harness, harness_auth="litellm")
    assert authorities == {("127.0.0.1", 4000)}


@pytest.mark.parametrize(
    "authority, ports",
    [
        (("127.0.0.1", 4000), [4000, 50000]),
        (("localhost", 4000), [4000, 50000]),
        (("::1", 4000), [4000, 50000]),
        (("litellm.example.com", 443), [50000]),
    ],
)
def test_portable_profile_adds_only_write_and_egress_denials(tmp_path, authority, ports):
    base = gap_workspace.cache_read_profile(None, tmp_path)
    profile = gap_workspace.portable_profile(base, tmp_path, "http://127.0.0.1:50000", {authority})
    # The hosted files-only rules are kept verbatim; only denials and the
    # LiteLLM/proxy peers follow.
    assert profile.startswith(base)
    added = profile[len(base):]
    assert added.count("(allow ") == len(ports)
    assert [f'(allow network-outbound (remote ip "localhost:{port}"))' for port in ports] == [
        rule for rule in (f"(allow{part}" for part in added.split("(allow")[1:])
    ]
    assert "(deny network-outbound)" in added
    assert f'(deny file-write* (require-not (require-any (subpath "{tmp_path.resolve()}")' in added


def test_script_runtime_exposes_interpreter_environment_only(tmp_path):
    environment = tmp_path / "opt" / "hermes-agent"
    (environment / "bin").mkdir(parents=True)
    base = tmp_path / "opt" / "python" / "bin"
    base.mkdir(parents=True)
    (base / "python3").write_text("")
    (environment / "bin" / "python").symlink_to(base / "python3")
    (environment / "pyvenv.cfg").write_text(f"home = {base}\n")
    launcher = environment / "bin" / "hermes"
    launcher.write_text(f"#!{environment / 'bin' / 'python'}\nimport hermes\n")
    roots = gap_workspace._script_runtime(launcher)
    assert set(roots) == {environment.resolve(), base.resolve().parent}
    # A native binary or an env shebang adds nothing.
    native = tmp_path / "native"
    native.write_bytes(b"\xcf\xfa\xed\xfe")
    env_script = tmp_path / "env-script"
    env_script.write_text("#!/usr/bin/env python3\n")
    assert gap_workspace._script_runtime(native) == []
    assert gap_workspace._script_runtime(env_script) == []


def test_portable_cell_env_keeps_home_and_tmp_in_scratch(tmp_path):
    scratch = tmp_path / "cell"
    own_home = scratch / "opencode" / "home"
    own_home.mkdir(parents=True)
    env = {"HOME": "/Users/someone", "TMPDIR": "/var/folders/x"}
    gap_workspace._portable_cell_env(env, scratch.resolve())
    assert env == {"HOME": str(scratch / "harness-home"), "TMPDIR": str(scratch / "harness-tmp")}
    env = {"HOME": str(own_home)}
    gap_workspace._portable_cell_env(env, scratch.resolve())
    assert env["HOME"] == str(own_home)  # An arm-isolated home is kept.


@pytest.mark.parametrize("escape", ["denied", "reached-host", "malformed"])
def test_write_escape_qualification(tmp_path, monkeypatch, escape):
    from sew.gap import sandbox

    cwd = tmp_path / "cell" / "workspace"
    cwd.mkdir(parents=True)
    backend = sandbox.SandboxBackend("seatbelt", "/usr/bin/sandbox-exec")

    def run(argv, **kwargs):
        target, nonce = Path(argv[-2]), argv[-1]
        assert target.parent == tmp_path  # Outside scratch, chosen by the parent.
        if escape == "reached-host":
            target.write_text(nonce)
        stdout = "{}" if escape == "malformed" else json.dumps({"nonce": nonce, "denied": True})
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(gap_workspace, "_run_probe_process", run)
    if escape != "denied":
        with pytest.raises(EgressCanaryRefused, match="write confinement unproven"):
            gap_workspace.qualify_write_escape(lambda c: c, cwd=cwd, env={}, backend=backend)
        assert not list(tmp_path.glob("sew-write-escape-*"))  # Cleaned up either way.
        return
    evidence = gap_workspace.qualify_write_escape(lambda c: c, cwd=cwd, env={}, backend=backend)
    assert evidence == {"backend": "seatbelt", "denied_in_sandbox": True, "reached_host": False,
                        "harness_tree_confined": True}


# --- Real containment: a fake harness binary under the real portable sandbox ---

FAKE_HARNESS = r"""
import json, os, sqlite3, subprocess, sys
from pathlib import Path
shape = os.environ["FAKE_SHAPE"]
results = []
for call_id, command in json.loads(Path(os.environ["FAKE_COMMANDS"]).read_text()):
    run = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=90)
    results.append((call_id, command, run.stdout + run.stderr, run.returncode))
if shape == "hermes":
    # Hermes 0.16.0 records the session in $HERMES_HOME/state.db.
    db = sqlite3.connect(Path(os.environ["HERMES_HOME"]) / "state.db")
    db.execute("CREATE TABLE sessions (id TEXT, input_tokens INT, output_tokens INT,"
               " cache_read_tokens INT, reasoning_tokens INT)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT,"
               " tool_calls TEXT, tool_call_id TEXT)")
    db.execute("INSERT INTO sessions VALUES ('fake', 1, 1, 0, 0)")
    for call_id, command, output, code in results:
        calls = [{"id": call_id, "function": {"name": "terminal",
                                              "arguments": json.dumps({"command": command})}}]
        db.execute("INSERT INTO messages (role, content, tool_calls) VALUES ('assistant', '', ?)",
                   (json.dumps(calls),))
        db.execute("INSERT INTO messages (role, content, tool_call_id) VALUES ('tool', ?, ?)",
                   (json.dumps({"output": output, "exit_code": code, "error": None}), call_id))
    db.commit()
    db.close()
else:
    sys.path.insert(0, os.environ["FAKE_SHAPES"])
    from shapes import shell_events
    for call_id, command, output, code in results:
        for event in shell_events(shape, call_id, command, output, code):
            print(json.dumps(event))
"""


@pytest.fixture
def fake_litellm():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"litellm stub ok")

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


def run_fake_harness(harness, argv, env, cwd, scratch, commands):
    path = scratch / f"commands-{len(list(scratch.glob('commands-*')))}.json"
    path.write_text(json.dumps(commands))
    result = subprocess.run(
        argv, cwd=cwd, env={**env, "FAKE_COMMANDS": str(path)}, capture_output=True, text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    if harness == "hermes":
        events = HermesProtocol().poll_events(env, set())
        HermesProtocol().reset_session(env)
    else:
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return [event for event in events if not event["type"].startswith("hermes.")]


@pytest.mark.parametrize("harness", OSS)
def test_fake_harness_shell_is_contained_like_hosted_code_cells(
    tmp_path, harness, fake_litellm, require_containment
):
    require_containment()
    base_url, requests = fake_litellm
    scratch = tmp_path / "cell"
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    shapes = scratch / "shapes"
    shapes.mkdir()
    source = Path(__file__).read_text()
    (shapes / "shapes.py").write_text(
        "import json\n" + source[source.index("def shell_events"):source.index("def captured")]
    )
    binary = tmp_path / "bin" / harness
    binary.parent.mkdir()
    binary.write_text(f"#!{sys.executable}\n" + FAKE_HARNESS)
    binary.chmod(0o755)
    env = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "HOME": str(tmp_path / "host-home"),
        "SEW_LITELLM_BASE_URL": base_url,
        "SEW_LITELLM_API_KEY": "offline-placeholder",
        "FAKE_SHAPE": harness,
        "FAKE_SHAPES": str(shapes),
    }
    if harness == "hermes":
        env["HERMES_HOME"] = str(scratch / "hermes-home")
        Path(env["HERMES_HOME"]).mkdir()
    # Outside the cell: the bench interpreter (read-only on Linux) and the
    # scratch's parent (a private /tmp inside the Linux namespace).
    outside = [Path(sys.prefix) / f"sew-escape-{harness}", tmp_path / f"sew-escape-{harness}"]
    try:
        with bench_network_boundary(
            [str(binary)], env, cwd=workspace, harness_id=harness, harness_auth="litellm"
        ) as (argv, evidence):
            assert evidence["harness_tree_confined"] is True
            assert evidence["write_escape"]["reached_host"] is False
            assert Path(env["HOME"]).is_relative_to(scratch.resolve())
            # The mandatory fresh canary, through this harness's own event shape.
            nonce, command = canary_command(harness)
            events = run_fake_harness(harness, argv, env, workspace, scratch, [["canary", command]])
            record = require_canary(captured(events), nonce, command, harness_id=harness)
            assert set(record["probes"]) == {"curl", "pip", "https"}
            # A task whose shell writes outside the cell, reaches for the web and
            # calls the LiteLLM endpoint the harness itself uses.
            task = [
                [f"write-{index}", f"echo escaped > {shlex.quote(str(path))}"]
                for index, path in enumerate(outside)
            ] + [
                ["egress", "python3 -c \"import urllib.request; "
                           "urllib.request.urlopen('http://1.1.1.1/', timeout=8)\""],
                ["socket", "python3 -c \"import socket; "
                           "socket.create_connection(('1.1.1.1', 443), timeout=8)\""],
                ["litellm", "python3 -c \"import os, urllib.request; print(urllib.request.urlopen("
                            "os.environ['SEW_LITELLM_BASE_URL'] + '/v1/models', timeout=8).read().decode())\""],
            ]
            events = run_fake_harness(harness, argv, env, workspace, scratch, task)
    finally:
        leaked = [path for path in outside if path.exists()]
        for path in leaked:
            path.unlink()
    assert not leaked, f"writes escaped the cell: {leaked}"
    results = {}
    calls, names = gap_workspace._shell_calls(events)
    for entry in gap_workspace._blocks(events):
        result = gap_workspace._portable_shell_result(entry, calls, names)
        if result is not None:
            results[result[0]] = result[1:]
    assert set(results) == {"write-0", "write-1", "egress", "socket", "litellm"}
    assert results["write-0"][2] is True  # The read-only/denied target fails outright.
    assert results["egress"][2] is True and results["socket"][2] is True
    command, output, failed = results["litellm"]
    assert not failed and "litellm stub ok" in output, output
    assert requests == ["/v1/models"]
    # The audit reads this transcript exactly as it reads the same Codex commands.
    codex = [
        event
        for call_id, (command, output, failed) in results.items()
        for event in shell_events("codex", call_id, command, output, int(failed))
    ]
    assert audit(events) == audit(codex)


@pytest.fixture
def gap_config(tmp_path):
    root = tmp_path / "module"
    shutil.copytree(GOLDEN, root / "catalogs" / "gap")
    task = json.loads((GOLDEN / "tasks.json").read_text())[0]
    cache = tmp_path / "cache"
    cache.mkdir()
    import hashlib

    for pin in task["packages"]:
        name = pin["url"].rsplit("/", 1)[1]
        (cache / name).write_bytes(pin["version"].encode())
        pin["sha256"] = hashlib.sha256(pin["version"].encode()).hexdigest()
    (root / "catalogs" / "gap" / "tasks.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "catalog_id": "gap-test", "authoring_policy": "synthetic",
             "tasks": [task]}
        )
    )
    return HarnessRunConfig(
        harness_id="hermes", provider_id="no-search", native_search_available=False,
        task_id=task["id"], mode="live", workspace_profile=True, gap_module_root=root,
        wheelhouse=cache, model_id="litellm/glm-5.2",
    )


@pytest.mark.parametrize("harness", OSS)
def test_live_code_cell_runs_canary_then_task(gap_config, tmp_path, monkeypatch, fake_pi, harness):
    """The canary precedes the task, carries its own prompt, and leaves no trace."""
    from contextlib import nullcontext

    boundary = []

    def fake_boundary(argv, env, **kwargs):
        boundary.append((kwargs["harness_id"], kwargs["harness_auth"]))
        return nullcontext(
            (["portable-prefix", *argv], {"attribution": "harness-runner-permission:bench-seatbelt"})
        )

    monkeypatch.setattr(gap_workspace, "bench_network_boundary", fake_boundary)
    spawned = []

    def spawn(argv, *, prompt, cwd, env, **kwargs):
        spawned.append((argv, prompt))
        assert argv[0] == "portable-prefix"
        assert env["PIP_NO_INDEX"] == "1" and env["SEW_LITELLM_API_KEY"] == "offline-placeholder"
        if len(spawned) == 1:
            command = prompt.split("\n", 1)[1]
            nonce = shlex.split(command)[-1]
            if harness == "hermes":
                assert argv[argv.index("-z") + 1] == prompt  # Hermes takes its prompt in argv.
                (Path(env["HERMES_HOME"]) / "state.db").write_text("canary ledger")
            events = shell_events(harness, "canary", command, canary_output(nonce))
        else:
            if harness == "hermes":
                assert argv[argv.index("-z") + 1] == prompt
                assert not (Path(env["HERMES_HOME"]) / "state.db").exists()
            else:
                assert argv == spawned[0][0]
            (cwd / "solution.py").write_text('print("fixed")\n')
            events = shell_events(harness, "task", "pytest -q", "1 passed")
        return ProcessOutcome(events=captured(events), ready=True, exit_code=0)

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    environ = {
        "SEW_HARNESS_LIVE": "1",  # Read from this map by the fake spawn only.
        "SEW_OSS_ENABLED": "1",
        "SEW_LITELLM_API_KEY": "offline-placeholder",
        "SEW_PI_BIN": str(fake_pi),
        "PATH": os.defpath,
    }
    result = run_live_harness(replace(gap_config, harness_id=harness), tmp_path / "runs",
                              environ=environ)
    assert boundary == [(harness, "litellm")]
    assert len(spawned) == 2 and spawned[1][1] != spawned[0][1]
    canary = json.loads((result.bundle_dir / "artifacts/egress-canary.json").read_text())
    assert canary["admissible"] and canary["harness_id"] == harness
    assert set(canary["probes"]) == {"curl", "pip", "https"}
    assert "solution.py" in (result.bundle_dir / "artifacts/workspace.diff").read_text()
    assert result.status != "contaminated"


@pytest.mark.parametrize("harness", OSS)
def test_live_code_cell_refuses_task_after_failed_canary(gap_config, tmp_path, monkeypatch, fake_pi, harness):
    from contextlib import nullcontext

    monkeypatch.setattr(
        gap_workspace, "bench_network_boundary",
        lambda argv, env, **kw: nullcontext((list(argv), {"attribution": "fixture"})),
    )
    prompts = []

    def spawn(argv, *, prompt, **kwargs):
        prompts.append(prompt)
        command = prompt.split("\n", 1)[1]
        nonce = shlex.split(command)[-1]
        output = canary_output(nonce, https={"exit_code": 0, "error": ""})
        return ProcessOutcome(events=captured(shell_events(harness, "canary", command, output)),
                              ready=True, exit_code=0)

    monkeypatch.setattr(live_harness, "spawn_and_capture", spawn)
    environ = {"SEW_HARNESS_LIVE": "1", "SEW_OSS_ENABLED": "1",
               "SEW_LITELLM_API_KEY": "offline-placeholder", "SEW_PI_BIN": str(fake_pi),
               "PATH": os.defpath}
    with pytest.raises(EgressCanaryRefused):
        run_live_harness(replace(gap_config, harness_id=harness), tmp_path / "runs", environ=environ)
    assert len(prompts) == 1, "the GAP task must never start after a failed canary"
    assert not list((tmp_path / "runs").rglob("run.json"))
