"""WSB-05 live harness driver tests.

Everything except ``test_live_smoke_*`` is hermetic: a fake harness script
speaks each CLI's real stream format (shapes captured from claude 2.1.282 and
codex-cli 0.157.0) and is driven by ``FAKE_MODE``. The live smoke spawns the
real CLIs and only runs when an operator sets ``SEW_HARNESS_LIVE=1``.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sew.harness import (
    HarnessRunConfig,
    fixture_provider_exposure,
    run_acceptance_fixture_matrix,
    run_fixture_harness,
    run_harness,
)
from sew.live_harness import (
    LIVE_ENV,
    LiveHarnessRefused,
    TranscriptOverflow,
    fit_transcript,
    live_enabled,
    overflow_transcript,
    run_live_harness,
    scrub_text,
)
from sew import live_harness
from sew.harness import assert_no_secret_material
from sew.schema import RUN_STATUSES, SchemaError, validate_fixture_run

TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "harness_boot_failed",
    "provider_unavailable",
    "budget_exhausted",
    "contaminated",
}

FAKE_HARNESS = r"""
import json, os, subprocess, sys, time

mode = os.environ.get("FAKE_MODE", "success")
harness = "codex" if "exec" in sys.argv[1:] else "claude-code"
pid_file = os.environ.get("FAKE_PID_FILE")
if pid_file:
    with open(pid_file, "w") as handle:
        handle.write(str(os.getpid()))
prompt = sys.stdin.read()

def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

def ready():
    if harness == "claude-code":
        emit({"type": "system", "subtype": "init", "tools": ["WebSearch"], "model": "fake"})
    else:
        emit({"type": "thread.started", "thread_id": "t-1"})
        emit({"type": "turn.started"})

def answer(text, usage=None):
    if harness == "claude-code":
        usage = usage or {"input_tokens": 12, "cache_creation_input_tokens": 100,
                          "cache_read_input_tokens": 900, "output_tokens": 40,
                          "output_tokens_details": {"thinking_tokens": 10}}
        emit({"type": "assistant", "message": {"id": "msg_1", "role": "assistant",
              "content": [{"type": "text", "text": text}], "usage": usage}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": text, "usage": usage, "num_turns": 1})
    else:
        emit({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
              "text": text}})
        emit({"type": "turn.completed", "usage": usage or {"input_tokens": 1000,
              "cached_input_tokens": 600, "output_tokens": 50,
              "reasoning_output_tokens": 20}})

def fail(message, subtype="error_during_execution"):
    if harness == "claude-code":
        emit({"type": "result", "subtype": subtype, "is_error": True, "result": message})
    else:
        emit({"type": "error", "message": message})
        emit({"type": "turn.failed", "error": {"message": message}})

if mode == "success":
    ready()
    names = sorted(os.environ)
    answer(json.dumps({"answer": "Release 3.2", "prompt_echo": prompt,
                       "env_names": names,
                       "citation_urls": ["https://fixture.example/releases/current"]}))
    sys.exit(0)
if mode == "contaminated":
    ready()
    if harness == "claude-code":
        emit({"type": "assistant", "message": {"id": "msg_tool", "role": "assistant",
              "content": [{"type": "tool_use", "name": "mcp__firecrawl__search",
                           "input": {"query": "x"}}],
              "usage": {"input_tokens": 1, "output_tokens": 1}}})
    else:
        emit({"type": "item.completed", "item": {"id": "tool_1", "type": "mcp_tool_call",
              "name": "firecrawl.search"}})
    answer("leaked")
    sys.exit(0)
if mode == "prose":
    ready()
    answer("It is Release 3.2, see https://fixture.example/releases/current.")
    sys.exit(0)
if mode == "hang_after_ready":
    ready()
    time.sleep(600)
if mode == "silent":
    time.sleep(600)
if mode == "stream_forever":
    ready()
    while True:
        emit({"type": "stream_event", "event": {"type": "ping"}})
        time.sleep(0.001)
if mode == "stream_flood":
    ready()
    for _ in range(65536):
        emit({"type": "stream_event", "event": {"type": "ping", "payload": "x" * 1000}})
    sys.exit(0)
if mode == "crash_before_ready":
    sys.stderr.write("segfault-ish boot crash\n")
    sys.exit(3)
if mode == "crash_after_ready":
    ready()
    sys.exit(1)
if mode == "empty_answer":
    ready()
    answer("")
    sys.exit(0)
if mode == "auth":
    ready()
    fail("Your access token could not be refreshed. Please log out and sign in again.")
    sys.exit(1)
if mode == "quota":
    ready()
    fail("API Error: 429 rate_limit_error: You have hit your usage limit.")
    sys.exit(1)
if mode == "max_turns":
    ready()
    fail("", subtype="error_max_turns")
    sys.exit(1)
if mode == "token_flood":
    ready()
    for index in range(1000):
        emit({"type": "assistant", "message": {"id": f"msg_{index}", "content": [],
              "usage": {"input_tokens": 5000, "output_tokens": 100}}})
        emit({"type": "turn.completed", "usage": {"input_tokens": 5000, "output_tokens": 100}})
        time.sleep(0.01)
    sys.exit(0)
if mode == "secrets":
    ready()
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "content":
          "GET / Authorization: Bearer sk-ant-api03-SECRETSECRETSECRETSECRET\n"
          "Cookie: sessionid=cookie-secret-value\n"
          "docs say: send Bearer <token> in the header"}]},
          "api_key": "plain-key-value"})
    answer(json.dumps({"answer": "ok"}))
    sys.exit(0)
if mode == "echo_codex_id_token":
    with open(os.path.join(os.environ["CODEX_HOME"], "auth.json")) as handle:
        id_token = json.load(handle)["tokens"]["id_token"]
    ready()
    answer(json.dumps({"answer": id_token}))
    sys.exit(0)
if mode in ("straggler_exit", "straggler_hang"):
    # A tool subprocess that ignores SIGTERM and does not hold the harness's
    # stdout, like an MCP server that outlives a clean harness exit.
    straggler_pid_file = os.environ["FAKE_STRAGGLER_PID_FILE"]
    subprocess.Popen(
        [sys.executable, "-c",
         "import os, signal, sys, time\n"
         "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
         "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
         "time.sleep(600)\n",
         straggler_pid_file],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    while not (os.path.exists(straggler_pid_file) and open(straggler_pid_file).read()):
        time.sleep(0.01)
    ready()
    if mode == "straggler_exit":
        answer(json.dumps({"answer": "ok"}))
        sys.exit(0)
    time.sleep(600)
if mode == "event_flood":
    ready()
    for index in range(4000):
        emit({"type": "stream_event", "event": {"type": "ping", "index": index}})
    answer(json.dumps({"answer": "ok"}))
    sys.exit(0)
if mode == "bearer_prose":
    ready()
    answer(json.dumps({"answer": "The ring bearer walked in first; bearer bonds pay the holder.",
                       "citation_urls": []}))
    sys.exit(0)
if mode == "huge_answer":
    ready()
    answer(json.dumps({"answer": "a" * 300000}))
    sys.exit(0)
if mode == "huge":
    ready()
    for index in range(40):
        emit({"type": "assistant", "message": {"id": f"m{index}", "content": [
              {"type": "tool_use", "name": f"mcp__exa__search_{index}", "input": {"q": "x"}}]}})
        emit({"type": "user", "message": {"content": [
              {"type": "tool_result", "content": "y" * 20000}]}})
    answer(json.dumps({"answer": "ok"}))
    sys.exit(0)
sys.exit(99)
"""


@pytest.fixture()
def fake_harness(tmp_path: Path) -> Path:
    path = tmp_path / "fake-harness"
    path.write_text(f"#!{sys.executable}\n{FAKE_HARNESS}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _live_env(**extra: str) -> dict[str, str]:
    # CODEX_HOME comes from the autouse fixture (an empty temp home), never
    # the host's ~/.codex.
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(Path.home()),
        "CODEX_HOME": os.environ["CODEX_HOME"],
        LIVE_ENV: "1",
        **extra,
    }


def _config(
    fake: Path | str,
    mode: str,
    *,
    harness_id: str = "claude-code",
    **overrides: object,
) -> HarnessRunConfig:
    env = {"FAKE_MODE": mode, **overrides.pop("env", {})}  # type: ignore[arg-type]
    return HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id=overrides.pop("provider_id", "native"),  # type: ignore[arg-type]
        task_id="current-fact-lookup-v1",
        mode="live",
        binary=str(fake),
        env=env,
        harness_auth=overrides.pop("harness_auth", "account"),
        run_id_override=overrides.pop("run_id_override", f"live-{harness_id}-{mode}"),  # type: ignore[arg-type]
        timeout_seconds=overrides.pop("timeout_seconds", 20.0),  # type: ignore[arg-type]
        boot_timeout_seconds=overrides.pop("boot_timeout_seconds", 10.0),  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


def _read(run_dir: Path, rel: str) -> object:
    return json.loads((run_dir / rel).read_text(encoding="utf-8"))


def _bundle_text(run_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in run_dir.rglob("*") if p.is_file())


def test_every_terminal_status_is_a_valid_run_status() -> None:
    assert TERMINAL_STATUSES <= RUN_STATUSES


def test_live_mode_config_no_longer_raises_but_unknown_mode_does() -> None:
    config = HarnessRunConfig(
        harness_id="codex", provider_id="native", task_id="current-fact-lookup-v1", mode="live"
    )
    assert config.mode == "live"
    with pytest.raises(SchemaError, match="mode must be one of"):
        HarnessRunConfig(
            harness_id="codex",
            provider_id="native",
            task_id="current-fact-lookup-v1",
            mode="replay",  # type: ignore[arg-type]
        )
    with pytest.raises(SchemaError, match="timeout_seconds must be a finite positive"):
        HarnessRunConfig(
            harness_id="codex",
            provider_id="native",
            task_id="current-fact-lookup-v1",
            timeout_seconds=0,
        )


def test_live_spawn_is_refused_without_operator_gate(tmp_path: Path, fake_harness: Path) -> None:
    pid_file = tmp_path / "pid"
    config = _config(fake_harness, "success", env={"FAKE_PID_FILE": str(pid_file)})

    with pytest.raises(LiveHarnessRefused, match=LIVE_ENV):
        run_live_harness(config, tmp_path / "out", environ={"PATH": os.environ["PATH"]})

    assert not pid_file.exists(), "nothing may spawn without the operator gate"
    assert not live_enabled({LIVE_ENV: "true"})


def test_run_fixture_harness_rejects_live_config(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        harness_id="codex", provider_id="native", task_id="current-fact-lookup-v1", mode="live"
    )
    with pytest.raises(SchemaError, match="requires mode='fixture'"):
        run_fixture_harness(config, tmp_path)


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_success_injects_prompt_and_captures_transcript(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    prompt = "Find the current release label.\nAnswer as JSON."
    result = run_live_harness(
        _config(fake_harness, "success", harness_id=harness_id, prompt_text=prompt),
        tmp_path,
        environ=_live_env(),
    )

    assert (result.status, result.failure_category) == ("succeeded", None)
    validate_fixture_run(result.bundle_dir)
    answer = _read(result.bundle_dir, "artifacts/final-answer.json")
    assert answer["answer"] == "Release 3.2"
    assert answer["prompt_echo"] == prompt, "prompt must arrive on the harness's stdin"
    assert "harness" not in answer and "provider" not in answer, "deliverable stays blinded"

    transcript = _read(result.bundle_dir, "artifacts/transcript.json")
    harness_events = [e["harness_event"] for e in transcript if "harness_event" in e]
    assert harness_events, "the harness stream must be captured"
    markers = {e.get("event") for e in transcript}
    assert {"prompt_sent", "harness_ready", "first_output_token", "final_answer"} <= markers

    run = _read(result.bundle_dir, "run.json")
    assert run["mode"] == "live"
    metrics = _read(result.bundle_dir, "metrics/metrics.json")
    assert metrics["token_usage"]["accounting_source"] == "measured"
    assert metrics["latency_ms"]["harness_boot"] is not None


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_out_of_arm_call_fails_live_cell_as_contaminated(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    config = _config(
        fake_harness,
        "contaminated",
        harness_id=harness_id,
        provider_id="exa",
        native_search_available=False,
        external_provider=fixture_provider_exposure("exa"),
    )
    result = run_live_harness(config, tmp_path, environ=_live_env())

    assert (result.status, result.failure_category) == ("contaminated", "out_of_arm_tool_call")
    run = _read(result.bundle_dir, "run.json")
    assert run["status"] == "contaminated"
    metadata = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert metadata["arm_audit"]["violations"]


def test_claude_usage_is_split_into_disjoint_buckets(tmp_path: Path, fake_harness: Path) -> None:
    result = run_live_harness(_config(fake_harness, "success"), tmp_path, environ=_live_env())

    usage = _read(result.bundle_dir, "metrics/metrics.json")["token_usage"]
    # input 12 + cache writes 100; cache reads 900; output 40 of which 10 thinking.
    assert (usage["input"], usage["cached_input"], usage["output"], usage["reasoning"]) == (
        112,
        900,
        30,
        10,
    )
    assert usage["total_billable"] == 1052
    assert usage["cache_write"] == 100


def test_claude_usage_retains_cache_write_ttl_breakdown() -> None:
    row = live_harness._claude_usage_row(
        {
            "input_tokens": 12,
            "cache_creation_input_tokens": 100,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 40,
                "ephemeral_1h_input_tokens": 60,
            },
            "cache_read_input_tokens": 900,
            "output_tokens": 40,
        }
    )
    assert row is not None
    assert row["total_billable"] == 1052
    assert (row["cache_write"], row["cache_write_5m"], row["cache_write_1h"]) == (100, 40, 60)


def test_codex_usage_does_not_double_count_cached_input(tmp_path: Path, fake_harness: Path) -> None:
    result = run_live_harness(
        _config(fake_harness, "success", harness_id="codex"), tmp_path, environ=_live_env()
    )

    usage = _read(result.bundle_dir, "metrics/metrics.json")["token_usage"]
    assert (usage["input"], usage["cached_input"], usage["output"], usage["reasoning"]) == (
        400,
        600,
        30,
        20,
    )
    assert usage["total_billable"] == 1050


def test_codex_profile_model_is_pinned_and_recorded(tmp_path: Path, fake_harness: Path) -> None:
    home = tmp_path / "source-codex"
    home.mkdir()
    (home / "config.toml").write_text('model = "gpt-6-sol"\n', encoding="utf-8")
    result = run_live_harness(
        _config(fake_harness, "success", harness_id="codex"),
        tmp_path / "run",
        environ=_live_env(CODEX_HOME=str(home)),
    )
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["model_id"] == "gpt-6-sol"
    assert spawn["argv"][spawn["argv"].index("--model") + 1] == "gpt-6-sol"


def test_codex_without_profile_model_remains_unknown(tmp_path: Path, fake_harness: Path) -> None:
    home = tmp_path / "source-codex"
    home.mkdir()
    result = run_live_harness(
        _config(fake_harness, "success", harness_id="codex"),
        tmp_path / "run",
        environ=_live_env(CODEX_HOME=str(home)),
    )
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["model_id"] is None
    assert "--model" not in spawn["argv"]


def test_codex_custom_provider_model_is_not_pinned(tmp_path: Path, fake_harness: Path) -> None:
    home = tmp_path / "source-codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "gpt-oss:120b"\nmodel_provider = "ollama"\n', encoding="utf-8"
    )
    result = run_live_harness(
        _config(fake_harness, "success", harness_id="codex"),
        tmp_path / "run",
        environ=_live_env(CODEX_HOME=str(home)),
    )
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["model_id"] is None
    assert "--model" not in spawn["argv"]


def test_prose_answer_is_wrapped_with_citations(tmp_path: Path, fake_harness: Path) -> None:
    result = run_live_harness(_config(fake_harness, "prose"), tmp_path, environ=_live_env())

    answer = _read(result.bundle_dir, "artifacts/final-answer.json")
    assert answer["citation_urls"] == ["https://fixture.example/releases/current"]
    assert answer["answer"].startswith("It is Release 3.2")


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_hung_task_is_killed_at_timeout_and_classified_timeout(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    result = run_live_harness(
        _config(
            fake_harness,
            "hang_after_ready",
            harness_id=harness_id,
            timeout_seconds=4.0,
            boot_timeout_seconds=3.0,
            env={"FAKE_PID_FILE": str(pid_file)},
        ),
        tmp_path,
        environ=_live_env(),
    )
    elapsed = time.monotonic() - started

    assert (result.status, result.failure_category) == ("timeout", "timeout")
    assert elapsed < 15, "the cell must stop at its timeout, not run the task out"
    _assert_dead(int(pid_file.read_text()))
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["process"]["timed_out"] is True
    assert spawn["process"]["ready"] is True


def test_harness_that_streams_forever_still_times_out(tmp_path: Path, fake_harness: Path) -> None:
    result = run_live_harness(
        _config(fake_harness, "stream_forever", timeout_seconds=4.0, boot_timeout_seconds=3.0),
        tmp_path,
        environ=_live_env(),
    )

    assert result.status == "timeout"


def test_cancel_stops_the_child_and_is_classified_cancelled(
    tmp_path: Path, fake_harness: Path
) -> None:
    pid_file = tmp_path / "pid"
    cancel = threading.Event()
    threading.Timer(3.0, cancel.set).start()

    result = run_live_harness(
        _config(fake_harness, "hang_after_ready", env={"FAKE_PID_FILE": str(pid_file)}),
        tmp_path,
        environ=_live_env(),
        cancel_event=cancel,
    )

    assert (result.status, result.failure_category) == ("cancelled", "cancelled")
    _assert_dead(int(pid_file.read_text()))


@pytest.mark.parametrize(
    ("mode", "harness_id", "status", "category"),
    [
        ("silent", "claude-code", "harness_boot_failed", "harness_no_first_output"),
        ("silent", "codex", "harness_boot_failed", "harness_no_first_output"),
        ("crash_before_ready", "claude-code", "harness_boot_failed", "harness_exited_before_ready"),
        ("auth", "codex", "harness_boot_failed", "harness_auth_failed"),
        ("auth", "claude-code", "harness_boot_failed", "harness_auth_failed"),
        ("quota", "claude-code", "provider_unavailable", "provider_unavailable"),
        ("quota", "codex", "provider_unavailable", "provider_unavailable"),
        ("max_turns", "claude-code", "budget_exhausted", "harness_budget_limit"),
        # The wrong-answer side of the line: the harness booted and ran.
        ("crash_after_ready", "claude-code", "failed", "harness_exit_nonzero"),
        ("empty_answer", "codex", "failed", "empty_final_answer"),
    ],
)
def test_terminal_status_classification(
    tmp_path: Path, fake_harness: Path, mode: str, harness_id: str, status: str, category: str
) -> None:
    # Only the silent harness should ever reach the boot deadline; the host can
    # take ~1s just to start the fake, so the others get a generous window.
    boot = 2.0 if mode == "silent" else 10.0
    result = run_live_harness(
        _config(fake_harness, mode, harness_id=harness_id, boot_timeout_seconds=boot),
        tmp_path,
        environ=_live_env(),
    )

    assert (result.status, result.failure_category) == (status, category)
    validate_fixture_run(result.bundle_dir)
    evaluation = _read(result.bundle_dir, "evaluations/evaluation.json")
    assert category in evaluation["failure_reasons"]


def test_boot_failure_and_wrong_answer_are_different_findings(
    tmp_path: Path, fake_harness: Path
) -> None:
    boot = run_live_harness(
        _config(fake_harness, "crash_before_ready"), tmp_path, environ=_live_env()
    )
    wrong = run_live_harness(
        _config(fake_harness, "crash_after_ready"), tmp_path, environ=_live_env()
    )

    assert boot.status == "harness_boot_failed"
    assert wrong.status == "failed"


def test_missing_binary_is_a_boot_failure(tmp_path: Path) -> None:
    result = run_live_harness(
        _config(tmp_path / "no-such-harness", "success"), tmp_path, environ=_live_env()
    )

    assert (result.status, result.failure_category) == (
        "harness_boot_failed",
        "harness_spawn_error",
    )
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert "FileNotFoundError" in spawn["process"]["spawn_error"]


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_token_budget_kills_the_cell_as_budget_exhausted(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    started = time.monotonic()
    result = run_live_harness(
        _config(fake_harness, "token_flood", harness_id=harness_id, max_total_tokens=20_000),
        tmp_path,
        environ=_live_env(),
    )

    assert (result.status, result.failure_category) == (
        "budget_exhausted",
        "token_budget_exceeded",
    )
    assert time.monotonic() - started < 10
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["process"]["running_tokens"] > 20_000


def test_child_env_is_an_allowlist_and_values_are_never_recorded(
    tmp_path: Path, fake_harness: Path
) -> None:
    environ = _live_env(
        ANTHROPIC_AUTH_TOKEN="sk-ant-oat01-parent-auth-value",
        CLAUDE_CODE_MESSAGING_TOKEN="parent-session-socket-token",
        CLAUDE_WORKER_GH_TOKEN="ghs_parentgithubtokenvalue",
        CLAUDECODE="1",
    )
    result = run_live_harness(
        _config(fake_harness, "success", env={"EXA_API_KEY": "exa-arm-key-value"}),
        tmp_path,
        environ=environ,
    )

    names = set(_read(result.bundle_dir, "artifacts/final-answer.json")["env_names"])
    assert {"ANTHROPIC_AUTH_TOKEN", "EXA_API_KEY", "PATH"} <= names
    assert not names & {"CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_WORKER_GH_TOKEN", "CLAUDECODE"}
    assert LIVE_ENV not in names

    text = _bundle_text(result.bundle_dir)
    for secret in (
        "sk-ant-oat01-parent-auth-value",
        "parent-session-socket-token",
        "ghs_parentgithubtokenvalue",
        "exa-arm-key-value",
    ):
        assert secret not in text
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert "ANTHROPIC_AUTH_TOKEN" in spawn["env_passthrough"]
    assert spawn["env"]["EXA_API_KEY"] == "<redacted>"


def test_credentials_in_the_transcript_are_scrubbed(tmp_path: Path, fake_harness: Path) -> None:
    result = run_live_harness(_config(fake_harness, "secrets"), tmp_path, environ=_live_env())

    assert result.status == "succeeded"
    text = _bundle_text(result.bundle_dir)
    assert "SECRETSECRET" not in text
    assert "cookie-secret-value" not in text
    assert "plain-key-value" not in text
    validate_fixture_run(result.bundle_dir)


def test_oversized_transcript_is_elided_without_dropping_tool_calls(
    tmp_path: Path, fake_harness: Path
) -> None:
    result = run_live_harness(_config(fake_harness, "huge"), tmp_path, environ=_live_env())

    validate_fixture_run(result.bundle_dir)
    transcript_path = result.bundle_dir / "artifacts" / "transcript.json"
    assert transcript_path.stat().st_size <= 200_000
    tools = {
        block["name"]
        for event in json.loads(transcript_path.read_text(encoding="utf-8"))
        for block in ((event.get("harness_event") or {}).get("message") or {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    assert len(tools) == 40


def test_transcript_over_the_cap_fails_the_cell_instead_of_crashing(
    tmp_path: Path, fake_harness: Path
) -> None:
    result = run_live_harness(_config(fake_harness, "event_flood"), tmp_path, environ=_live_env())

    assert (result.status, result.failure_category) == ("failed", "transcript_over_artifact_cap")
    validate_fixture_run(result.bundle_dir)
    transcript_path = result.bundle_dir / "artifacts" / "transcript.json"
    assert transcript_path.stat().st_size <= 200_000
    marker = json.loads(transcript_path.read_text(encoding="utf-8"))[-1]
    assert marker["event"] == "sew.transcript_overflow"
    assert 0 < marker["events_kept"] < marker["events_total"]
    assert _read(result.bundle_dir, "run.json")["failure_category"] == (
        "transcript_over_artifact_cap"
    )


def test_fit_transcript_raises_a_typed_overflow_and_overflow_transcript_fits() -> None:
    flood = [
        {"role": "harness", "harness_event": {"type": "ping", "index": i}} for i in range(4000)
    ]

    with pytest.raises(TranscriptOverflow):
        fit_transcript(flood)
    fitted = overflow_transcript(flood)
    assert len((json.dumps(fitted, indent=2, sort_keys=True) + "\n").encode()) <= 190_000
    assert fitted[-1]["events_total"] == 4000
    assert len(fitted) - 1 == fitted[-1]["events_kept"] > 0


@pytest.mark.parametrize(
    ("mode", "status"), [("straggler_exit", "succeeded"), ("straggler_hang", "timeout")]
)
def test_a_straggler_that_ignores_sigterm_does_not_outlive_the_cell(
    tmp_path: Path, fake_harness: Path, mode: str, status: str
) -> None:
    straggler_pid_file = tmp_path / "straggler.pid"
    result = run_live_harness(
        _config(
            fake_harness,
            mode,
            timeout_seconds=4.0,
            boot_timeout_seconds=3.0,
            env={"FAKE_STRAGGLER_PID_FILE": str(straggler_pid_file)},
        ),
        tmp_path,
        environ=_live_env(),
    )

    assert result.status == status
    _assert_dead(int(straggler_pid_file.read_text()))


def test_benign_bearer_prose_survives_a_complete_live_run(
    tmp_path: Path, fake_harness: Path
) -> None:
    prompt = "Explain what a ring bearer does and how bearer bonds work."
    result = run_live_harness(
        _config(fake_harness, "bearer_prose", prompt_text=prompt), tmp_path, environ=_live_env()
    )

    assert result.status == "succeeded"
    answer = _read(result.bundle_dir, "artifacts/final-answer.json")["answer"]
    assert answer == "The ring bearer walked in first; bearer bonds pay the holder."
    assert (result.bundle_dir / "artifacts" / "prompt.md").read_text(encoding="utf-8").strip() == (
        prompt
    )


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("Authorization: Bearer abc.def", True),
        ("send bearer sk-ant-api03-abcdefghij1234567890 upstream", True),
        ("Cookie: sessionid=cookie-secret-value", True),
        ("The ring bearer carried bearer bonds for the bearer certificate example.", False),
        ("Authorization: <redacted:bearer-header>", False),
        ("use <redacted:bearer-placeholder> in the header", False),
        ("a recipe card said cookie: chocolate chip", False),
    ],
)
def test_evidence_check_flags_credential_shapes_not_the_word_bearer(
    tmp_path: Path, text: str, flagged: bool
) -> None:
    (tmp_path / "evidence.txt").write_text(text, encoding="utf-8")
    if flagged:
        with pytest.raises(SchemaError, match="secret-like material"):
            assert_no_secret_material(tmp_path)
    else:
        assert_no_secret_material(tmp_path)


def test_opaque_credential_under_an_unfamiliar_env_key_is_never_recorded(
    tmp_path: Path, fake_harness: Path
) -> None:
    result = run_live_harness(
        _config(
            fake_harness,
            "success",
            env={"MCP_AUTH": "opaque-cred-0f9e8d7c6b5a", "TZ": "UTC"},
        ),
        tmp_path,
        environ=_live_env(),
    )

    assert "opaque-cred-0f9e8d7c6b5a" not in _bundle_text(result.bundle_dir)
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["env"]["MCP_AUTH"] == "<redacted>"
    assert spawn["env"]["TZ"] == "UTC"


@pytest.mark.parametrize("run_id", ["../escape", "/tmp/absolute", "nested/run", "..", ""])
def test_run_id_override_must_be_a_single_safe_component(fake_harness: Path, run_id: str) -> None:
    with pytest.raises(SchemaError, match="single safe path component"):
        _config(fake_harness, "success", run_id_override=run_id or "..")


def test_an_existing_run_directory_is_never_deleted(tmp_path: Path, fake_harness: Path) -> None:
    config = _config(fake_harness, "prose", run_id_override="same-id")
    first = run_live_harness(config, tmp_path, environ=_live_env())
    second = run_live_harness(config, tmp_path, environ=_live_env())

    assert (first.run_id, second.run_id) == ("same-id", "same-id-r2")
    validate_fixture_run(first.bundle_dir)
    validate_fixture_run(second.bundle_dir)


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_nonfinite_limits_are_rejected_through_the_cli(
    tmp_path: Path, fake_harness: Path, value: str
) -> None:
    from conftest import LIB_PYTHON, MODULE_ROOT

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sew.cli",
            "run-live-harness",
            "--harness",
            "codex",
            "--output-root",
            str(tmp_path / "out"),
            "--timeout-seconds",
            value,
        ],
        cwd=MODULE_ROOT,
        env={
            "PYTHONPATH": str(LIB_PYTHON),
            "PATH": os.environ["PATH"],
            "HOME": str(Path.home()),
            LIVE_ENV: "1",
            "SEW_CODEX_BIN": str(fake_harness),
        },
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "timeout_seconds must be a finite positive number" in completed.stderr
    out = tmp_path / "out"
    assert not out.exists() or not any(out.iterdir()), "nothing may be spawned or written"


def test_dense_tool_trace_keeps_tool_identity_when_fully_elided() -> None:
    transcript = [
        {
            "role": "harness",
            "received_at": "2026-09-26T12:00:00.000Z",
            "harness_event": {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{index}",
                            "name": f"mcp__exa__search_{index}",
                            "input": {"q": "x" * 300},
                        }
                    ]
                },
            },
        }
        for index in range(450)
    ]

    fitted = fit_transcript(transcript)

    blocks = [event["harness_event"]["message"]["content"][0] for event in fitted]
    assert {block["name"] for block in blocks} == {f"mcp__exa__search_{i}" for i in range(450)}
    assert all(block["type"] == "tool_use" for block in blocks)
    assert all(event["role"] == "harness" for event in fitted)
    assert blocks[0]["input"]["q"] == "<elided:300 chars>", "fitting must reach full elision"


def test_runaway_stdout_stops_at_the_evidence_capture_budget(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_harness, "CAPTURE_BUDGET_BYTES", 20_000)
    started = time.monotonic()
    result = run_live_harness(
        _config(fake_harness, "stream_flood", timeout_seconds=20.0), tmp_path, environ=_live_env()
    )

    assert (result.status, result.failure_category) == (
        "budget_exhausted",
        "evidence_capture_budget_exceeded",
    )
    assert time.monotonic() - started < 15, "the budget, not the wall clock, must stop it"
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["process"]["capture_overflow"] is True
    validate_fixture_run(result.bundle_dir)


def test_large_prompt_is_elided_and_the_cell_still_succeeds(
    tmp_path: Path, fake_harness: Path
) -> None:
    result = run_live_harness(
        _config(fake_harness, "prose", prompt_text="p" * 300_000), tmp_path, environ=_live_env()
    )

    assert result.status == "succeeded"
    prompt_path = result.bundle_dir / "artifacts" / "prompt.md"
    assert prompt_path.stat().st_size <= 200_000
    assert "<elided:" in prompt_path.read_text(encoding="utf-8")


def test_oversized_final_answer_fails_the_cell_instead_of_crashing(
    tmp_path: Path, fake_harness: Path
) -> None:
    result = run_live_harness(_config(fake_harness, "huge_answer"), tmp_path, environ=_live_env())

    assert (result.status, result.failure_category) == ("failed", "final_answer_over_artifact_cap")
    validate_fixture_run(result.bundle_dir)
    assert (result.bundle_dir / "artifacts" / "final-answer.json").stat().st_size <= 200_000


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
@pytest.mark.parametrize("harness_auth", ["account", "broker"])
@pytest.mark.usefixtures("agent_os_host")
def test_native_arm_without_native_search_is_unsupported_not_a_crash(
    tmp_path: Path, harness_id: str, harness_auth: str
) -> None:
    # The binary does not exist: reaching a spawn would be a boot failure, so
    # "unsupported" also proves nothing was spawned.
    result = run_live_harness(
        _config(
            tmp_path / "no-such-harness",
            "success",
            harness_id=harness_id,
            harness_auth=harness_auth,
            native_search_available=False,
        ),
        tmp_path,
        environ=_live_env(),
    )

    assert (result.status, result.failure_category) == ("unsupported", "native_search_unsupported")
    validate_fixture_run(result.bundle_dir)
    # The recorded contract has exactly the canonical native-arm shape, so
    # supported and unsupported cells aggregate the same way.
    from dataclasses import replace as _replace

    from sew.arms import contract_for

    canonical = contract_for(
        _replace(
            _config(tmp_path / "no-such-harness", "success", harness_id=harness_id),
            native_search_available=True,
        )
    ).as_record()
    spawn = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert spawn["arm_contract"] == canonical
    assert spawn["harness_auth"] == {"source": "none"}


def test_fit_transcript_leaves_small_transcripts_alone() -> None:
    small = [{"role": "user", "content": "x" * 100}]
    assert fit_transcript(small) is small


def test_scrub_text_neutralises_bearer_and_cookie_headers() -> None:
    scrubbed = scrub_text("Authorization: Bearer abc.def\nSet-Cookie: a=b\nuse Bearer <token>")
    assert "abc.def" not in scrubbed
    assert "bearer " not in scrubbed.lower()
    assert "cookie:" not in scrubbed.lower()


def test_scrub_text_preserves_bearer_prose() -> None:
    text = "The ring bearer carried bearer bonds for the bearer certificate example."
    assert scrub_text(text) == text


def test_run_harness_dispatches_on_mode(tmp_path: Path, fake_harness: Path, monkeypatch) -> None:
    fixture = run_harness(
        HarnessRunConfig(
            harness_id="codex", provider_id="native", task_id="current-fact-lookup-v1"
        ),
        tmp_path,
    )
    assert fixture.status == "succeeded"

    monkeypatch.delenv(LIVE_ENV, raising=False)
    with pytest.raises(LiveHarnessRefused):
        run_harness(_config(fake_harness, "success"), tmp_path)


def test_fixture_mode_is_offline_and_spawns_nothing(tmp_path: Path, monkeypatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixture mode touched the network or spawned a process")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    results = run_acceptance_fixture_matrix(tmp_path)
    results.append(
        run_harness(
            HarnessRunConfig(
                harness_id="claude-code",
                provider_id="firecrawl",
                task_id="current-fact-lookup-v1",
                external_provider=fixture_provider_exposure("firecrawl"),
            ),
            tmp_path,
        )
    )

    assert [r.status for r in results] == ["succeeded"] * 5


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"harness pid {pid} outlived its cell")


LIVE_SMOKE_PROMPT = 'Reply with only this JSON object and nothing else: {"answer": "ok"}'


@pytest.mark.skipif(not live_enabled(), reason=f"{LIVE_ENV}=1 is required for the live smoke")
@pytest.mark.parametrize(("harness_id", "binary"), [("claude-code", "claude"), ("codex", "codex")])
def test_live_smoke_spawns_real_harness_and_captures_transcript(
    tmp_path: Path, harness_id: str, binary: str
) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} is not on PATH")
    result = run_live_harness(
        HarnessRunConfig(
            harness_id=harness_id,  # type: ignore[arg-type]
            provider_id="native",
            task_id="current-fact-lookup-v1",
            mode="live",
            prompt_text=LIVE_SMOKE_PROMPT,
            timeout_seconds=180,
        ),
        tmp_path,
    )

    validate_fixture_run(result.bundle_dir)
    transcript = _read(result.bundle_dir, "artifacts/transcript.json")
    assert any("harness_event" in event for event in transcript), "no transcript captured"
    assert result.status in TERMINAL_STATUSES
    print(f"live-smoke {harness_id}: status={result.status} category={result.failure_category}")
    if result.status == "succeeded":
        answer = _read(result.bundle_dir, "artifacts/final-answer.json")
        assert answer.get("answer") == "ok"


def test_cli_run_live_harness_is_gated_and_runs(tmp_path: Path, fake_harness: Path) -> None:
    from conftest import LIB_PYTHON, MODULE_ROOT

    base = {"PYTHONPATH": str(LIB_PYTHON), "PATH": os.environ["PATH"], "HOME": str(Path.home())}
    argv = [
        sys.executable,
        "-m",
        "sew.cli",
        "run-live-harness",
        "--harness",
        "codex",
        "--output-root",
        str(tmp_path),
        "--timeout-seconds",
        "30",
    ]
    refused = subprocess.run(argv, cwd=MODULE_ROOT, env=base, text=True, capture_output=True)
    assert refused.returncode == 2
    assert LIVE_ENV in refused.stderr

    ran = subprocess.run(
        argv,
        cwd=MODULE_ROOT,
        env={**base, LIVE_ENV: "1", "SEW_CODEX_BIN": str(fake_harness)},
        text=True,
        capture_output=True,
    )
    # FAKE_MODE is unset, so the fake answers successfully.
    assert ran.returncode == 0, ran.stderr
    assert "status=succeeded" in ran.stdout


@pytest.mark.usefixtures("agent_os_host")
def test_broker_claude_fetches_each_cell_and_never_passes_api_key(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sew import broker_auth

    calls = []
    token = "fake-sew-broker-access-token"

    def fetch(log_dir: Path, *, environ=None):
        calls.append(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "broker-fetch.json").write_text(
            json.dumps({"status": "success", "secret": "must-scrub", "detail": "x" * 200000})
        )
        return broker_auth.BrokerClaudeEnv(token, "abc123", "soon")

    captured = []
    original = live_harness.spawn_and_capture

    def capture(*args, **kwargs):
        captured.append(kwargs["env"])
        return original(*args, **kwargs)

    monkeypatch.setattr(broker_auth, "claude_code_env", fetch)
    monkeypatch.setattr(live_harness, "spawn_and_capture", capture)
    for index in range(2):
        config = _config(
            fake_harness, "success", run_id_override=f"broker-{index}", harness_auth="broker"
        )
        result = run_live_harness(
            config, tmp_path, environ=_live_env(ANTHROPIC_API_KEY="old-account-key")
        )
        metadata = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
        assert metadata["harness_auth"] == {
            "source": "broker",
            "fingerprint": "abc123",
            "expires_at": "soon",
        }
        assert "ANTHROPIC_API_KEY" not in metadata["env_passthrough"]
        receipt = _read(result.bundle_dir, "logs/broker-fetch.json")
        assert receipt["status"] == "success"
        assert receipt["secret"] == "<redacted>"
        assert (result.bundle_dir / "logs/broker-fetch.json").stat().st_size <= 200000
        assert token not in "".join(
            p.read_text() for p in result.bundle_dir.rglob("*") if p.is_file()
        )
    assert len(calls) == 2
    assert all(
        env["ANTHROPIC_AUTH_TOKEN"] == token and "ANTHROPIC_API_KEY" not in env for env in captured
    )


@pytest.mark.usefixtures("agent_os_host")
def test_malformed_broker_receipt_does_not_abort_cell_finalization(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sew import broker_auth

    token = "fake-sew-broker-access-token"

    def fetch(log_dir: Path, *, environ=None):
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "broker-fetch.json").write_text(f'{{broken "{token}"')
        return broker_auth.BrokerClaudeEnv(token, "abc123", "soon")

    monkeypatch.setattr(broker_auth, "claude_code_env", fetch)
    monkeypatch.setattr(
        live_harness,
        "spawn_and_capture",
        lambda *args, **kwargs: live_harness.ProcessOutcome(
            events=[
                {
                    "received_at": "2026-09-27T00:00:00Z",
                    "event": {"type": "system", "subtype": "init"},
                },
                {
                    "received_at": "2026-09-27T00:00:01Z",
                    "event": {
                        "type": "result",
                        "subtype": "success",
                        "result": "answer",
                        "usage": {},
                    },
                },
            ],
            exit_code=0,
            ready=True,
        ),
    )
    result = run_live_harness(
        _config(fake_harness, "success", harness_auth="broker"), tmp_path, environ=_live_env()
    )
    assert result.status == "succeeded"
    assert _read(result.bundle_dir, "logs/broker-fetch.json") == {"unparseable_receipt": True}
    assert token not in _bundle_text(result.bundle_dir)


@pytest.mark.usefixtures("agent_os_host")
def test_broker_codex_id_token_is_removed_from_harness_output(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sew import broker_auth

    id_token = "broker-vended-id-token-unique-value"

    def sync(codex_home: Path, log_dir: Path, *, environ=None):
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "access-value", "id_token": id_token}})
        )
        return {"source": "broker", "fingerprint": "abc123", "expires_at": "soon"}

    monkeypatch.setattr(broker_auth, "codex_home_auth", sync)
    result = run_live_harness(
        _config(fake_harness, "echo_codex_id_token", harness_id="codex", harness_auth="broker"),
        tmp_path,
        environ=_live_env(),
    )
    assert id_token not in _bundle_text(result.bundle_dir)
    assert "<redacted:broker-token>" in _bundle_text(result.bundle_dir)


@pytest.mark.usefixtures("agent_os_host")
def test_broker_failure_is_auth_boot_failure_without_spawn(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sew import broker_auth

    def fail(*args, **kwargs):
        raise broker_auth.BrokerAuthError("unavailable")

    monkeypatch.setattr(broker_auth, "claude_code_env", fail)
    monkeypatch.setattr(live_harness, "spawn_and_capture", lambda *a, **kw: pytest.fail("spawned"))
    result = run_live_harness(
        _config(fake_harness, "success", harness_auth="broker"), tmp_path, environ=_live_env()
    )
    assert (result.status, result.failure_category) == (
        "harness_boot_failed",
        "harness_auth_failed",
    )


def test_broker_failure_diagnostic_survives_cell_scratch_cleanup(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch, agent_os_host
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("fake")
    stderr = b"claude-code adapter: outcome=oauth-broker-auth-failed http=401; failing closed"

    def fail(harness):
        raise subprocess.CalledProcessError(1, [harness], stderr=stderr)

    monkeypatch.setattr(agent_os_host, "harness_auth", fail)
    monkeypatch.setattr(live_harness, "spawn_and_capture", lambda *a, **kw: pytest.fail("spawned"))
    result = run_live_harness(
        _config(fake_harness, "success", harness_auth="broker"),
        tmp_path,
        environ=_live_env(OAUTH_BROKER_SHARED_SECRET_FILE=str(secret)),
    )
    assert (result.status, result.failure_category) == (
        "harness_boot_failed",
        "harness_auth_failed",
    )
    ref = "logs/claude-code-broker-auth-stderr.log"
    diagnostic = result.bundle_dir / ref
    assert diagnostic.read_bytes() == stderr
    assert diagnostic.stat().st_mode & 0o777 == 0o600
    metadata = _read(result.bundle_dir, "artifacts/spawn-metadata.json")
    assert metadata["harness_auth"] == {"source": "broker", "diagnostic_ref": ref}
    assert ref in (result.bundle_dir / "evidence/bundle.yaml").read_text()


def test_account_auth_keeps_child_credentials(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []
    original = live_harness.spawn_and_capture

    def capture(*args, **kwargs):
        captured.append(kwargs["env"])
        return original(*args, **kwargs)

    monkeypatch.setattr(live_harness, "spawn_and_capture", capture)
    result = run_live_harness(
        _config(fake_harness, "success", harness_auth="account"),
        tmp_path,
        environ=_live_env(ANTHROPIC_API_KEY="account-key"),
    )
    assert result.status == "succeeded"
    assert captured[0]["ANTHROPIC_API_KEY"] == "account-key"
    assert _read(result.bundle_dir, "artifacts/spawn-metadata.json")["harness_auth"] == {
        "source": "account"
    }


def _codex_config(home: Path, text: str) -> dict[str, str]:
    (home / "config.toml").write_text(text, encoding="utf-8")
    return {"CODEX_HOME": str(home)}


def test_codex_profile_model_follows_codex_resolution(tmp_path: Path) -> None:
    from sew.live_harness import codex_profile_model

    top = _codex_config(tmp_path, 'model = "gpt-6-sol"\n')
    assert codex_profile_model(top, "default") == ("gpt-6-sol", "codex_config:top_level")

    active = _codex_config(
        tmp_path,
        'model = "gpt-6-sol"\nprofile = "work"\n[profiles.work]\nmodel = "gpt-6-mini"\n',
    )
    assert codex_profile_model(active, "default") == ("gpt-6-mini", "codex_config:profiles.work")
    # A named SEW profile selects its own table the same way.
    assert codex_profile_model(active, "work") == ("gpt-6-mini", "codex_config:profiles.work")

    # A profile table without a model inherits the top-level model.
    inherit = _codex_config(
        tmp_path,
        'model = "gpt-6-sol"\nprofile = "p"\n[profiles.p]\nmodel_reasoning_effort = "high"\n',
    )
    assert codex_profile_model(inherit, "default") == ("gpt-6-sol", "codex_config:top_level")


def test_codex_profile_model_is_unknown_rather_than_wrong(tmp_path: Path) -> None:
    from sew.live_harness import codex_profile_model

    missing = _codex_config(tmp_path, 'model = "gpt-6-sol"\nprofile = "gone"\n')
    assert codex_profile_model(missing, "default") == (None, None)
    other = _codex_config(tmp_path, 'model = "llama"\nmodel_provider = "ollama"\n')
    assert codex_profile_model(other, "default") == (None, None)
    assert codex_profile_model({"CODEX_HOME": str(tmp_path / "absent")}, "default") == (None, None)


def test_tests_never_read_the_hosts_codex_home() -> None:
    home = Path(os.environ["CODEX_HOME"])
    assert home != Path.home() / ".codex"
    assert not (home / "config.toml").exists()


@pytest.mark.parametrize('state, expected', [('running', False), ('exited', True), ('zombie', True)])
def test_leader_exit_without_waitid_keeps_pid_reserved(monkeypatch, state, expected):
    from types import SimpleNamespace
    from sew import live_harness

    closed = []

    class Events:
        def control(self, changes, max_events, timeout):
            assert max_events == 1 and timeout == 0
            if state == 'zombie':
                raise ProcessLookupError()
            return [] if state == 'running' else [object()]

        def close(self):
            closed.append(True)

    monkeypatch.delattr(live_harness.os, 'waitid', raising=False)
    monkeypatch.setattr(live_harness.select, 'kqueue', Events, raising=False)
    monkeypatch.setattr(live_harness.select, 'kevent', lambda *a, **kw: object(), raising=False)
    for name in ('KQ_FILTER_PROC', 'KQ_EV_ADD', 'KQ_EV_ONESHOT', 'KQ_NOTE_EXIT'):
        monkeypatch.setattr(live_harness.select, name, 1, raising=False)
    # No poll/wait method: exit observation must never reap this leader.
    proc = SimpleNamespace(pid=42, returncode=None)
    assert live_harness._leader_exited(proc) is expected
    assert proc.returncode is None
    assert closed == [True]


@pytest.mark.skipif(sys.platform != 'darwin', reason='requires native kqueue')
def test_kqueue_observes_real_exit_without_reaping(monkeypatch):
    from sew import live_harness

    monkeypatch.delattr(live_harness.os, 'waitid', raising=False)
    proc = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not live_harness._leader_exited(proc) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert live_harness._leader_exited(proc)
        assert proc.returncode is None
        live_harness._terminate(proc)
        assert proc.returncode == 0
    finally:
        live_harness._terminate(proc)


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
@pytest.mark.parametrize("arm", ["exa", "parallel-web", "firecrawl", "brave", "tavily", "perplexity"])
@pytest.mark.parametrize("route", ["glm-5.2", "local/qwen3-coder-next-80b-a3b-6bit"])
def test_oss_provider_spawn(fake_harness, tmp_path, monkeypatch, harness, arm, route):
    import tomllib
    from sew.oss import load_catalog

    monkeypatch.setattr(live_harness, "live_enabled", lambda env: True)
    monkeypatch.setattr(live_harness, "provider_available", lambda *args, **kwargs: True)
    original = live_harness.spawn_and_capture
    captured = {}

    def capture(argv, **kwargs):
        captured.update(argv=argv, env=kwargs["env"])
        if harness == "codex":
            home = Path(kwargs["env"]["CODEX_HOME"])
            captured["settings"] = tomllib.loads((home / "config.toml").read_text())
            assert not (home / "auth.json").exists()
        return original(argv, **kwargs)

    monkeypatch.setattr(live_harness, "spawn_and_capture", capture)
    monkeypatch.setattr(live_harness, "codex_profile_model", lambda *args: pytest.fail("frontier fallback"))
    monkeypatch.setattr(live_harness.broker_auth, "claude_code_env",
                        lambda *a, **k: pytest.fail("credential lookup"))
    monkeypatch.setattr(live_harness.broker_auth, "codex_home_auth",
                        lambda *a, **k: pytest.fail("credential lookup"))
    config = _config(fake_harness, "success", harness_id=harness,
                     provider_id=arm, native_search_available=False, model_id="litellm/" + route,
                     external_provider=fixture_provider_exposure(arm))
    source = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
              "SEW_OSS_ENABLED": "1", "SEW_LITELLM_API_KEY": "fake-proxy-key",
              "SEW_LITELLM_BASE_URL": "http://localhost:4444",
              "ANTHROPIC_API_KEY": "fake-account", "ANTHROPIC_AUTH_TOKEN": "fake-oauth",
              "OPENAI_API_KEY": "fake-account", "BROKER_TOKEN": "fake-oauth"}
    result = run_live_harness(config, tmp_path / "runs", environ=source)
    assert result.status == "succeeded"
    argv, env = captured["argv"], captured["env"]
    assert argv[argv.index("--model") + 1] == route
    assert "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert "BROKER_TOKEN" not in env
    assert env["SEW_LITELLM_API_KEY"] == "fake-proxy-key"
    if harness == "claude-code":
        assert env["ANTHROPIC_AUTH_TOKEN"] == "fake-proxy-key"
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:4444"
        entry = load_catalog()[route]
        if entry["rate_basis"] == "self-hosted":
            assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(entry["context_window_tokens"])
            assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(entry["max_output_tokens"])
        else:
            assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env
            assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
        assert "WebSearch" not in argv[argv.index("--tools") + 1]
    else:
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        settings = captured["settings"]
        assert settings["model_provider"] == "searchlight_litellm"
        assert settings["web_search"] == "disabled"
        assert len(settings["mcp_servers"]) == 1
        assert settings["model_providers"]["searchlight_litellm"] == {
            "name": "Searchlight LiteLLM", "base_url": "http://localhost:4444/v1",
            "env_key": "SEW_LITELLM_API_KEY", "wire_api": "responses"}
    metrics = _read(result.bundle_dir, "metrics/metrics.json")
    assert metrics["token_usage"]["usage_basis"] == "harness"
    cost = _read(result.bundle_dir, "artifacts/model-cost.json")
    if route.startswith("local/"):
        assert cost["amount_usd"] == 0
        assert cost["assumed_rate"]["rate_basis"] == "self-hosted"
    else:
        assert cost["amount_usd"] > 0
        assert cost["assumed_rate"]["rate_basis"] == "list"
    assert "fake-proxy-key" not in _bundle_text(result.bundle_dir)
    assert source["ANTHROPIC_AUTH_TOKEN"] == "fake-oauth"
    from sew.judge_transport import judge_environment

    judge = judge_environment(_config(fake_harness, "success", harness_id=harness), source)
    assert judge["ANTHROPIC_AUTH_TOKEN"] == "fake-oauth"
    assert "SEW_LITELLM_API_KEY" not in judge
    assert "SEW_LITELLM_BASE_URL" not in judge
    assert "ANTHROPIC_BASE_URL" not in judge


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_oss_native_not_applicable(fake_harness, tmp_path, monkeypatch, harness):
    monkeypatch.setattr(live_harness, "live_enabled", lambda env: True)
    monkeypatch.setattr(live_harness, "spawn_and_capture", lambda *a, **k: pytest.fail("spawned"))
    config = _config(fake_harness, "success", harness_id=harness, model_id="litellm/glm-5.2")
    result = run_live_harness(config, tmp_path / "runs", environ={"SEW_OSS_ENABLED": "1"})
    assert result.status == "not_applicable"
    assert _read(result.bundle_dir, "run.json")["failure_category"] == "native-search-unavailable-on-oss-model"
