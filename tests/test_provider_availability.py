"""GAPMCP-01: tool discovery is a prerequisite to grading, not tool use."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import queue
import threading
import time

import pytest

from sew.harness import HarnessRunConfig, ProviderExposure
from sew.live_harness import LIVE_ENV, run_live_harness
from sew.mcp_meter import AVAILABILITY_REF, CallMeter, provider_available, metered_server_config
from sew.provider_startup import prewarm

SERVER = Path(__file__).parent / "fixtures/slow_mcp_server.py"

# A harness that discovers tools, makes no calls, and delivers an answer even
# when discovery times out. This is the incident's critical grading trigger.
DISCOVERY_HARNESS = r"""
import json, os, selectors, sys, tomllib
import subprocess
argv = sys.argv[1:]
sys.stdin.read()
if "--mcp-config" in argv:
    server, = json.load(open(argv[argv.index("--mcp-config") + 1]))["mcpServers"].values()
else:
    server, = tomllib.load(open(os.environ["CODEX_HOME"] + "/config.toml", "rb"))["mcp_servers"].values()
p = subprocess.Popen([server["command"], *server["args"]],
    env={**os.environ, **server.get("env", {})}, stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, text=True)
s = selectors.DefaultSelector(); s.register(p.stdout, selectors.EVENT_READ)
def rpc(id, method):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline()) if s.select(0.8) else None
try:
    if rpc(1, "initialize"):
        rpc(2, "tools/list")
finally:
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # A slow wrapper teardown must not crash the harness before it answers.
        p.kill()
        p.wait()
answer = json.dumps({"answer": "Offline answer", "citation_urls": []})
events = ([{"type":"system", "subtype":"init", "model":"fixture"},
           {"type":"result", "subtype":"success", "is_error":False, "result":answer,
            "usage":{"input_tokens":12,"output_tokens":10}}]
          if "--mcp-config" in argv else
          [{"type":"thread.started","thread_id":"fixture"},
           {"type":"item.completed","item":{"id":"answer","type":"agent_message","text":answer}},
           {"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":10}}])
for event in events: print(json.dumps(event), flush=True)
"""


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("never", [False, True])
@pytest.mark.parametrize("snapshot_failure", [False, True])
def test_live_discovery_without_calls(tmp_path, harness, never, snapshot_failure, monkeypatch):
    if snapshot_failure:
        from sew import live_harness

        original = live_harness.metered_server_config

        def failing_child(*args, **kwargs):
            wrapped = original(*args, **kwargs)
            child_args = wrapped["args"][2:]
            script = (
                "from sew import mcp_meter; import sys; "
                "mcp_meter.os.replace = lambda *args: (_ for _ in ()).throw(PermissionError('fixture')); "
                f"sys.argv = ['sew.mcp_meter', *{child_args!r}]; "
                "raise SystemExit(mcp_meter.main())"
            )
            wrapped["args"] = ["-c", script]
            return wrapped

        monkeypatch.setattr(live_harness, "metered_server_config", failing_child)
    binary = tmp_path / "harness"
    binary.write_text(f"#!{sys.executable}\n{DISCOVERY_HARNESS}")
    binary.chmod(0o755)
    result = run_live_harness(
        HarnessRunConfig(
            harness_id=harness,
            provider_id="brave",
            task_id="current-fact-lookup-v1",
            mode="live",
            harness_auth="account",
            binary=str(binary),
            model_id="fixture",
            native_search_available=False,
            # This exercises discovery status, not cold subprocess boot latency.
            # The fake harness answers only after discovery and the server
            # teardown, each a cold Python start; on a loaded CI runner
            # without bytecode caching that once took over 15 s (3.13, e02eaa1).
            timeout_seconds=90,
            boot_timeout_seconds=60,
            external_provider=ProviderExposure(
                provider_id="brave",
                tool_name="mcp__brave__*",
                mcp_server_name="brave",
                mcp_server_config={
                    "command": sys.executable,
                    "args": [
                        str(SERVER),
                        *(["--never-initialize"] if never else ["--delay", "0.1"]),
                    ],
                },
            ),
        ),
        tmp_path / "runs",
        environ={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), LIVE_ENV: "1"},
    )
    assert result.status == (
        "provider_unavailable" if never and not snapshot_failure else "succeeded"
    )
    run = json.loads((result.bundle_dir / "run.json").read_text())
    assert run["provider_call_refs"] == []
    availability = json.loads((result.bundle_dir / AVAILABILITY_REF).read_text())
    assert availability["availability"] == ("unknown" if snapshot_failure or never else "available")
    assert run["provider_availability"] == (
        "unknown" if snapshot_failure else "unavailable" if never else "available"
    )
    assert availability["wrapper_engaged"] is True
    assert provider_available(result.bundle_dir, "brave", result.run_id) is (
        None if snapshot_failure else not never
    )
    evidence = (result.bundle_dir / "evidence/bundle.yaml").read_text()
    assert AVAILABILITY_REF in evidence
    if snapshot_failure:
        from sew.mcp_meter import OBSERVATION_FAILURE_REF

        assert OBSERVATION_FAILURE_REF in evidence


@pytest.mark.parametrize(
    "reply",
    [
        {"error": {"message": "secret must not be recorded"}},
        {"result": {}},
        {"result": {"tools": []}},
    ],
)
def test_failed_empty_or_malformed_discovery_is_not_availability(tmp_path, reply):
    meter = CallMeter(provider_id="brave", run_id="cell", call_dir=tmp_path / "provider-calls")
    meter.client_message({"id": 1, "method": "initialize"})
    meter.server_message({"id": 1, "result": {"protocolVersion": "2025-06-18"}})
    meter.client_message({"id": 2, "method": "tools/list"})
    meter.server_message({"id": 2, **reply})
    assert not provider_available(tmp_path, "brave", "cell")
    assert "secret must not be recorded" not in (tmp_path / AVAILABILITY_REF).read_text()


def test_prewarm_resolves_pins_without_launching_server_or_passing_credentials(monkeypatch):
    calls = []
    monkeypatch.setenv("SEW_BRAVE_API_KEY", "fixture-never-passed")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    exposure = ProviderExposure(
        provider_id="brave",
        tool_name="search",
        mcp_server_config={
            "command": "npx",
            "args": ["-y", "@brave/brave-search-mcp-server@2.1.4"],
            "env": {"BRAVE_API_KEY": "fixture-never-passed"},
        },
    )
    assert prewarm({"brave": exposure}) == ["@brave/brave-search-mcp-server@2.1.4"]
    assert calls[0][0][-4:] == ["--", "node", "-e", ""]
    assert "fixture-never-passed" not in str(calls)
    assert "--ignore-scripts" in calls[0][0]


def test_prewarm_refuses_unpinned_packages(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unpinned resolution"))
    with pytest.raises(ValueError, match="exact pinned"):
        prewarm(
            {
                "brave": ProviderExposure(
                    provider_id="brave",
                    tool_name="search",
                    mcp_server_config={"command": "npx", "args": ["-y", "server@latest"]},
                )
            }
        )


@pytest.mark.skipif(
    not os.environ.get("SEW_CODEX_STARTUP_REPRO_BIN"),
    reason="opt-in installed Codex reproduction; no model turn or auth",
)
@pytest.mark.parametrize("timeout, expected", [(None, "failed"), (120, "ready")])
def test_real_codex_slow_server_timeout(tmp_path, timeout, expected):
    """A slow fixture exceeds the installed Codex default, but fits SEW's 120s."""
    wrapped = metered_server_config(
        {"command": sys.executable, "args": [str(SERVER.resolve()), "--delay", "35"]},
        provider_id="brave",
        run_id="repro",
        call_dir=tmp_path / "provider-calls",
    )
    assert wrapped is not None
    home = tmp_path / "codex"
    home.mkdir()
    lines = [
        'cli_auth_credentials_store="ephemeral"',
        'mcp_oauth_credentials_store="file"',
        "[mcp_servers.fixture]",
        f"command={json.dumps(wrapped['command'])}",
        f"args={json.dumps(wrapped['args'])}",
    ]
    if timeout is not None:
        lines.append(f"startup_timeout_sec={timeout}")
    lines += [
        "[mcp_servers.fixture.env]",
        *[f"{k}={json.dumps(v)}" for k, v in wrapped["env"].items()],
    ]
    (home / "config.toml").write_text("\n".join(lines) + "\n")
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG") if k in os.environ}
    env.update(CODEX_HOME=str(home), HOME=str(home))
    proc = subprocess.Popen(
        [os.environ["SEW_CODEX_STARTUP_REPRO_BIN"], "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=tmp_path,
        text=True,
        start_new_session=True,
    )
    replies = queue.Queue()
    reader = threading.Thread(
        target=lambda: [replies.put(json.loads(line)) for line in proc.stdout], daemon=True
    )
    reader.start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "gapmcp-fixture", "version": "1"}},
            }
        )
        deadline = time.monotonic() + 55
        status = None
        while time.monotonic() < deadline:
            message = replies.get(timeout=max(0.1, deadline - time.monotonic()))
            if message.get("id") == 1:
                send({"method": "initialized"})
                send(
                    {
                        "id": 2,
                        "method": "thread/start",
                        "params": {
                            "cwd": str(tmp_path),
                            "model": "fixture",
                            "approvalPolicy": "never",
                            "ephemeral": True,
                        },
                    }
                )
            if message.get("method") == "mcpServer/startupStatus/updated":
                status = message["params"]
                if status["status"] in {"ready", "failed"}:
                    break
        print(
            f"Codex startup: timeout={timeout}, status={status['status']}, error={status.get('error')}"
        )
        assert status["status"] == expected, status
        if expected == "failed":
            assert "startup_timeout_sec" in status["error"]
        else:
            # The relay persists after forwarding the response; allow its
            # callback to finish before checking the durable snapshot.
            while (
                not provider_available(tmp_path, "brave", "repro") and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert provider_available(tmp_path, "brave", "repro")
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)


@pytest.mark.parametrize(
    "diagnostic, attempts",
    [
        (b"npm ERR! ECONNRESET\nfull details", 3),
        (b"npm ERR! E404 package missing\nfull details", 1),
    ],
)
def test_prewarm_bounded_retries_preserve_full_stderr(monkeypatch, diagnostic, attempts):
    from sew import provider_startup

    calls, sleeps = [], []

    def fail(cmd, **kwargs):
        calls.append(kwargs)
        raise subprocess.CalledProcessError(1, cmd, stderr=diagnostic)

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(provider_startup.time, "sleep", sleeps.append)
    exposure = ProviderExposure(
        provider_id="brave",
        tool_name="search",
        mcp_server_config={"command": "npx", "args": ["server@1.2.3"]},
    )
    with pytest.raises(ValueError) as error:
        prewarm({"brave": exposure})
    assert diagnostic.decode() in str(error.value)
    assert len(calls) == attempts
    assert sleeps == ([1.0, 2.0] if attempts == 3 else [])
    assert all(call["capture_output"] for call in calls)


def test_prewarm_recovers_from_timeout(monkeypatch):
    from sew import provider_startup

    calls = []

    def resolve(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 180, stderr=b"registry stalled")

    monkeypatch.setattr(subprocess, "run", resolve)
    monkeypatch.setattr(provider_startup.time, "sleep", lambda _: None)
    exposure = ProviderExposure(
        provider_id="brave",
        tool_name="search",
        mcp_server_config={"command": "npx", "args": ["server@1.2.3"]},
    )
    assert prewarm({"brave": exposure}) == ["server@1.2.3"]
    assert len(calls) == 2


def test_exported_meter_survives_stripped_child_env(tmp_path):
    import shutil
    from sew import mcp_meter

    wrapped = metered_server_config(
        {"command": sys.executable, "args": [str(SERVER.resolve()), "--delay", "0"]},
        provider_id="brave",
        run_id="export",
        call_dir=tmp_path / "provider-calls",
    )
    exported = tmp_path / "export/lib/python"
    shutil.copytree(Path(mcp_meter.__file__).parent, exported / "sew")
    paths = wrapped["env"]["PYTHONPATH"].split(os.pathsep)
    paths[0] = str(exported)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PYTHONPATH": os.pathsep.join(paths)}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search"}},
    ]
    result = subprocess.run(
        [wrapped["command"], *wrapped["args"]],
        cwd=tmp_path,
        env=env,
        input="".join(json.dumps(r) + "\n" for r in requests),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert provider_available(tmp_path, "brave", "export") is True
    assert len(list((tmp_path / "provider-calls").glob("brave-*.json"))) == 1


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("engaged", [False, True])
def test_unobserved_meter_keeps_availability_unknown_or_available(tmp_path, completed, engaged):
    from sew.mcp_meter import availability_record, write_availability

    record = availability_record("exa", "cell")
    record.update(
        wrapper_engaged=engaged,
        observation_reason="server_not_launched" if engaged else "wrapper_not_engaged",
    )
    write_availability(tmp_path / "provider-calls", record)
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/transcript.json").write_text(
        json.dumps(
            [
                {
                    "harness_event": {
                        "type": "item.completed" if completed else "item.started",
                        "item": {
                            "type": "mcp_tool_call",
                            "id": "c",
                            "server": "exa",
                            "tool": "search",
                            "status": "completed" if completed else "in_progress",
                        },
                    }
                }
            ]
        )
    )
    assert provider_available(tmp_path, "exa", "cell") is (True if completed else None)


def test_core_unavailable_leaves_bundle_evidence(tmp_path, monkeypatch):
    from sew import mcp_meter

    monkeypatch.setattr(mcp_meter, "_core_cache", [None])
    assert (
        metered_server_config(
            {"command": "unused"},
            provider_id="exa",
            run_id="cell",
            call_dir=tmp_path / "provider-calls",
        )
        is None
    )
    record = json.loads((tmp_path / AVAILABILITY_REF).read_text())
    assert record["observed"] is False
    assert record["observation_reason"] == mcp_meter.CORE_UNAVAILABLE
    assert provider_available(tmp_path, "exa", "cell") is None


@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("engaged", [False, True])
def test_url_transport_and_claude_completed_call(tmp_path, is_error, engaged):
    wrapped = metered_server_config(
        {"url": "https://fixture.invalid/mcp"},
        provider_id="parallel-web",
        run_id="cell",
        call_dir=tmp_path / "provider-calls",
    )
    assert wrapped is None
    assert (
        json.loads((tmp_path / AVAILABILITY_REF).read_text())["observation_reason"]
        == "url_transport"
    )
    if engaged:
        from sew.mcp_meter import availability_record, write_availability

        record = availability_record("parallel-web", "cell")
        record.update(wrapper_engaged=True, observation_reason="server_not_launched")
        write_availability(tmp_path / "provider-calls", record)
    (tmp_path / "artifacts").mkdir()
    transcript = [
        {
            "harness_event": {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "c", "name": "mcp__parallel__search"}]
                },
            }
        },
        {
            "harness_event": {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c",
                            "content": "fixture",
                            "is_error": is_error,
                        }
                    ]
                },
            }
        },
    ]
    (tmp_path / "artifacts/transcript.json").write_text(json.dumps(transcript))
    assert provider_available(tmp_path, "parallel-web", "cell") is (None if is_error else True)
    assert provider_available(tmp_path, "exa", "cell") is None


def test_legacy_all_false_is_unobserved_but_initialize_is_observed(tmp_path):
    from sew.mcp_meter import availability_record, write_availability

    record = availability_record("exa", "cell")
    del record["observed"]
    del record["observation_reason"]
    write_availability(tmp_path / "provider-calls", record)
    assert provider_available(tmp_path, "exa", "cell") is None
    record["initialize_requested"] = True
    write_availability(tmp_path / "provider-calls", record)
    assert provider_available(tmp_path, "exa", "cell") is False


@pytest.mark.parametrize("status", ["succeeded", "failed", "timeout"])
@pytest.mark.parametrize(
    "reason",
    [
        "url_transport",
        "mcp_metering_core_unavailable",
        "wrapper_not_engaged",
        "server_not_launched",
    ],
)
def test_adoption_preserves_unobserved_availability(tmp_path, status, reason):
    import shutil
    from sew.mcp_meter import availability_record, write_availability
    from sew.runner import MatrixCell, adopt_completed_bundle
    from sew.schema import validate_fixture_run

    fixture = Path(__file__).parents[1] / "fixtures/runs/failure"
    run = json.loads((fixture / "run.json").read_text(encoding="utf-8"))
    run.update(mode="live", provider_id="exa", status=status)
    run_dir = tmp_path / run["run_id"]
    shutil.copytree(fixture, run_dir)
    run_path = run_dir / "run.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    original = run_path.read_bytes()
    record = availability_record("exa", run["run_id"])
    record.update(observation_reason=reason, wrapper_engaged=reason == "server_not_launched")
    write_availability(run_dir / "provider-calls", record)
    cell = MatrixCell(
        suite_id=run["suite_id"],
        suite_version="1",
        task_id=run["task_id"],
        provider_id="exa",
        harness_id=run["harness_id"],
        model_profile=run["model_profile"],
        repetition=1,
        run_id=run["run_id"],
        required_operation="search",
        applicable=True,
    )

    validate_fixture_run(run_dir)
    if reason == "server_not_launched" and status == "succeeded":
        assert provider_available(run_dir, "exa", run["run_id"]) is False
        assert adopt_completed_bundle(cell, tmp_path) is None
        assert json.loads(run_path.read_text())["status"] == "provider_unavailable"
        return
    assert provider_available(run_dir, "exa", run["run_id"]) is None
    adopted = adopt_completed_bundle(cell, tmp_path)

    assert adopted is not None
    assert adopted.status == status
    assert adopted.failure_category == run["failure_category"]
    assert run_path.read_bytes() == original

    # Observed failed discovery still rewrites the bundle and prevents adoption.
    record.update(observed=True, observation_reason=None)
    write_availability(run_dir / "provider-calls", record)
    assert adopt_completed_bundle(cell, tmp_path) is None
    rewritten = json.loads(run_path.read_text(encoding="utf-8"))
    assert rewritten["status"] == "provider_unavailable"
    assert rewritten["failure_category"] == "provider_tools_unavailable"


@pytest.mark.parametrize(
    "status,marker,expected",
    [
        ("succeeded", None, False),
        ("failed", None, None),
        ("timeout", None, None),
        ("harness_boot_failed", None, None),
        ("failed", "harness_ready", None),
        ("timeout", "first_output_token", False),
    ],
)
def test_engaged_wrapper_without_server_launch_requires_startup(tmp_path, status, marker, expected):
    from sew.mcp_meter import availability_record, write_availability

    wrapped = metered_server_config(
        {"command": "/usr/bin/true"},
        provider_id="exa",
        run_id="cell",
        call_dir=tmp_path / "provider-calls",
    )
    assert wrapped is not None
    record = json.loads((tmp_path / AVAILABILITY_REF).read_text())
    assert record["wrapper_engaged"] is True
    assert record["observed"] is False
    assert record["observation_reason"] == "server_not_launched"
    assert record["availability"] == "unknown"
    (tmp_path / "run.json").write_text(json.dumps({"status": status}))
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/transcript.json").write_text(
        json.dumps([{"event": marker}] if marker else [])
    )
    assert provider_available(tmp_path, "exa", "cell") is expected

    # An uninstalled wrapper remains unknown and eligible for grading.
    write_availability(tmp_path / "provider-calls", availability_record("exa", "cell"))
    assert provider_available(tmp_path, "exa", "cell") is None
    assert json.loads((tmp_path / AVAILABILITY_REF).read_text())["availability"] == "unknown"


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "body,timeout,boot_timeout,status,category",
    [
        ("import sys; sys.exit(2)", 5, 2, "harness_boot_failed", "harness_exited_before_ready"),
        ("import time; time.sleep(10)", 5, 0.3, "harness_boot_failed", "harness_no_first_output"),
    ],
)
def test_unlaunched_wrapper_preserves_pre_ready_harness_failure(
    tmp_path, harness, body, timeout, boot_timeout, status, category
):
    result = _run_unlaunched_harness(tmp_path, harness, body, timeout, boot_timeout)
    record = json.loads((result.bundle_dir / AVAILABILITY_REF).read_text())
    assert record["wrapper_engaged"] is True and record["observed"] is False
    assert result.status == status
    assert result.failure_category == category
    assert provider_available(result.bundle_dir, "brave", result.run_id) is None


def _run_unlaunched_harness(tmp_path, harness, body, timeout=5, boot_timeout=2):
    binary = tmp_path / "harness"
    binary.write_text(f"#!{sys.executable}\n{body}\n")
    binary.chmod(0o755)
    return run_live_harness(
        HarnessRunConfig(
            harness_id=harness,
            provider_id="brave",
            task_id="current-fact-lookup-v1",
            mode="live",
            harness_auth="account",
            binary=str(binary),
            model_id="fixture",
            native_search_available=False,
            timeout_seconds=timeout,
            boot_timeout_seconds=boot_timeout,
            external_provider=ProviderExposure(
                provider_id="brave",
                tool_name="mcp__brave__*",
                mcp_server_name="brave",
                mcp_server_config={"command": sys.executable, "args": [str(SERVER)]},
            ),
        ),
        tmp_path / "runs",
        environ={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), LIVE_ENV: "1"},
    )


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("completed", [False, True])
def test_live_unlaunched_wrapper_uses_captured_completed_calls(tmp_path, harness, completed):
    # Answer without starting any MCP child. A completed call simulates a child
    # unable to overwrite the parent's availability snapshot.
    body = "import json,sys\nargv=sys.argv[1:]\nsys.stdin.read()\n"
    body += DISCOVERY_HARNESS[DISCOVERY_HARNESS.index("answer =") :]
    if completed:
        calls = (
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "c", "name": "mcp__brave__search"}]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "c", "content": "fixture"}
                        ]
                    },
                },
            ]
            if harness == "claude-code"
            else [
                {
                    "type": "item.completed",
                    "item": {
                        "id": "c",
                        "type": "mcp_tool_call",
                        "server": "brave",
                        "tool": "search",
                        "status": "completed",
                    },
                }
            ]
        )
        body = body.replace(
            "for event in events:", f"events = {calls!r} + events\nfor event in events:"
        )
    result = _run_unlaunched_harness(tmp_path, harness, body)
    assert result.status == ("succeeded" if completed else "provider_unavailable")
    record = json.loads((result.bundle_dir / AVAILABILITY_REF).read_text())
    assert record["wrapper_engaged"] is True and record["observed"] is False
    assert record["availability"] == "unknown"  # Discovery-only, not the final verdict.
    assert provider_available(result.bundle_dir, "brave", result.run_id) is completed


@pytest.mark.parametrize(
    "value,expected", [("available", True), ("unavailable", False), ("unknown", None)]
)
def test_persisted_verdict_precedes_truncated_transcript(tmp_path, value, expected):
    from sew.mcp_meter import availability_record, write_availability

    record = availability_record("brave", "cell")
    record.update(wrapper_engaged=True, observation_reason="server_not_launched")
    write_availability(tmp_path / "provider-calls", record)
    (tmp_path / "run.json").write_text(
        json.dumps(
            {
                "run_id": "cell",
                "provider_id": "brave",
                "status": "failed",
                "failure_category": "transcript_over_artifact_cap",
                "provider_availability": value,
            }
        )
    )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts/transcript.json").write_text(json.dumps([{"event": "harness_ready"}]))
    assert provider_available(tmp_path, "brave", "cell") is expected
    assert provider_available(tmp_path, "exa", "cell") is None
    assert provider_available(tmp_path, "brave", "other") is None


def test_child_snapshot_failure_keeps_stale_parent_unknown(tmp_path, monkeypatch, capsys):
    from sew import mcp_meter

    record = mcp_meter.availability_record("brave", "cell")
    record.update(wrapper_engaged=True, observation_reason="server_not_launched")
    mcp_meter.write_availability(tmp_path / "provider-calls", record)
    (tmp_path / "run.json").write_text(json.dumps({"status": "succeeded"}))

    def fail_replace(*args):
        raise PermissionError("fixture snapshot failure")

    monkeypatch.setattr(mcp_meter.os, "replace", fail_replace)
    meter = CallMeter(provider_id="brave", run_id="cell", call_dir=tmp_path / "provider-calls")
    meter.client_message({"id": 1, "method": "initialize"})
    meter.server_message({"id": 1, "result": {}})
    meter.client_message({"id": 2, "method": "tools/list"})
    meter.server_message({"id": 2, "result": {"tools": [{"name": "search"}]}})
    assert mcp_meter.OBSERVATION_FAILED in capsys.readouterr().err
    assert (tmp_path / mcp_meter.OBSERVATION_FAILURE_REF).exists()
    assert json.loads((tmp_path / AVAILABILITY_REF).read_text())["observed"] is False
    assert provider_available(tmp_path, "brave", "cell") is None
    # Even when the marker cannot survive, captured stderr prevents exclusion.
    (tmp_path / mcp_meter.OBSERVATION_FAILURE_REF).unlink()
    (tmp_path / "artifacts/harness-stderr.txt").write_text(mcp_meter.OBSERVATION_FAILED)
    assert provider_available(tmp_path, "brave", "cell") is None


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_live_verdict_survives_transcript_overflow(tmp_path, monkeypatch, harness):
    from sew import live_harness

    monkeypatch.setattr(
        live_harness,
        "fit_transcript",
        lambda transcript: (_ for _ in ()).throw(live_harness.TranscriptOverflow()),
    )
    monkeypatch.setattr(
        live_harness, "overflow_transcript", lambda transcript: [{"event": "harness_ready"}]
    )
    body = "import json,sys\nargv=sys.argv[1:]\nsys.stdin.read()\n"
    body += DISCOVERY_HARNESS[DISCOVERY_HARNESS.index("answer =") :]
    calls = (
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "c", "name": "mcp__brave__search"}]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "c", "content": "fixture"}]
                },
            },
        ]
        if harness == "claude-code"
        else [
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "c",
                    "server": "brave",
                    "tool": "search",
                    "status": "completed",
                },
            },
        ]
    )
    body = body.replace(
        "for event in events:", f"events = {calls!r} + events\nfor event in events:"
    )
    result = _run_unlaunched_harness(tmp_path, harness, body)
    run = json.loads((result.bundle_dir / "run.json").read_text())
    assert result.status == "failed"
    assert run["failure_category"] == "transcript_over_artifact_cap"
    assert run["provider_availability"] == "available"
    assert provider_available(result.bundle_dir, "brave", result.run_id) is True


@pytest.mark.parametrize(
    "verdict,expected", [("available", "failed"), ("unknown", "failed"), ("unavailable", None)]
)
def test_adoption_prefers_saved_verdict(tmp_path, verdict, expected):
    import shutil
    from sew.mcp_meter import availability_record, write_availability
    from sew.runner import MatrixCell, adopt_completed_bundle

    fixture = Path(__file__).parents[1] / "fixtures/runs/failure"
    run = json.loads((fixture / "run.json").read_text())
    run.update(mode="live", provider_id="exa", status="failed", provider_availability=verdict)
    directory = tmp_path / run["run_id"]
    shutil.copytree(fixture, directory)
    (directory / "run.json").write_text(json.dumps(run))
    record = availability_record("exa", run["run_id"])
    record.update(wrapper_engaged=True, observation_reason="server_not_launched")
    write_availability(directory / "provider-calls", record)
    (directory / "artifacts/transcript.json").write_text(json.dumps([{"event": "harness_ready"}]))
    cell = MatrixCell(
        suite_id=run["suite_id"],
        suite_version="1",
        task_id=run["task_id"],
        provider_id="exa",
        harness_id=run["harness_id"],
        model_profile=run["model_profile"],
        repetition=1,
        run_id=run["run_id"],
        required_operation="search",
        applicable=True,
    )
    adopted = adopt_completed_bundle(cell, tmp_path)
    assert (adopted.status if adopted is not None else None) == expected


def test_core_unavailable_child_write_failure_marks_unknown(tmp_path, monkeypatch):
    from sew import mcp_meter

    record = mcp_meter.availability_record("exa", "cell")
    record.update(wrapper_engaged=True, observation_reason="server_not_launched")
    mcp_meter.write_availability(tmp_path / "provider-calls", record)
    (tmp_path / "run.json").write_text(json.dumps({"status": "succeeded"}))
    monkeypatch.setattr(mcp_meter, "_core_cache", [None])

    def fail_replace(*args):
        raise PermissionError("fixture snapshot failure")

    monkeypatch.setattr(mcp_meter.os, "replace", fail_replace)
    monkeypatch.setattr(mcp_meter.os, "execvpe", lambda *args: (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        mcp_meter.main(
            [
                "--provider",
                "exa",
                "--run-id",
                "cell",
                "--call-dir",
                str(tmp_path / "provider-calls"),
                "--",
                "unused",
            ]
        )
    assert (tmp_path / mcp_meter.OBSERVATION_FAILURE_REF).exists()
    assert provider_available(tmp_path, "exa", "cell") is None


def test_discovery_snapshot_pending_until_reply(tmp_path):
    meter = CallMeter(provider_id="exa", run_id="cell", call_dir=tmp_path / "provider-calls")
    assert json.loads((tmp_path / AVAILABILITY_REF).read_text())["availability"] == "unknown"
    meter.client_message({"id": 1, "method": "tools/list"})
    assert json.loads((tmp_path / AVAILABILITY_REF).read_text())["availability"] == "unknown"
    meter.server_message({"id": 1, "error": {"code": -1}})
    assert json.loads((tmp_path / AVAILABILITY_REF).read_text())["availability"] == "unavailable"
