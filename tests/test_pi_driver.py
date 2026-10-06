from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import MODULE_ROOT

from sew.pi_driver import LIVE_ENV, PiHarnessDriver
from sew.schema import SchemaError, load_record, validate_fixture_run


def test_default_pi_profiles_load_from_configuration() -> None:
    driver = PiHarnessDriver.from_config()

    assert driver.profile_ids == ["oss-large", "oss-no-tools", "oss-small"]
    assert driver.profile("oss-small").resolved_model == "local/qwen2.5-coder-7b-instruct"
    assert driver.profile("oss-small").tool_calling_mode == "prompt_wrapped"
    assert driver.profile("oss-large").token_accounting_source == "measured"
    assert driver.profile("oss-no-tools").token_accounting_source == "unknown"


def test_lighthouse_fixture_runs_two_configured_pi_profiles(tmp_path: Path) -> None:
    driver = PiHarnessDriver.from_config()

    results = driver.run_lighthouse_fixture(
        tmp_path,
        profile_ids=["oss-small", "oss-large"],
        provider_id="fixture",
    )

    assert len(results) == 12
    assert {result.status for result in results} == {"succeeded"}
    assert {result.run_id.split("-fixture-")[0] for result in results} == {
        "pi-oss-small",
        "pi-oss-large",
    }
    for result in results:
        validation = validate_fixture_run(result.run_dir)
        assert validation.status == "succeeded"


def test_pi_token_accounting_sources_are_written_to_metrics(tmp_path: Path) -> None:
    driver = PiHarnessDriver.from_config()

    results = driver.run_lighthouse_fixture(
        tmp_path,
        profile_ids=["oss-small", "oss-large", "oss-no-tools"],
        provider_id="fixture",
    )

    seen = {}
    for result in results:
        run = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
        if run["task_id"] != "current-fact-lookup-v1":
            continue
        metrics = load_record(result.run_dir, run["metrics_ref"])
        harness = load_record(result.run_dir, run["harness_ref"])
        seen[run["model_profile"]] = (
            metrics["token_usage"]["accounting_source"],
            harness["token_accounting_source"],
        )

    assert seen == {
        "oss-small": ("estimated", "estimated"),
        "oss-large": ("measured", "measured"),
        "oss-no-tools": ("unknown", "unknown"),
    }


def test_unsupported_tool_calling_records_unsupported_not_provider_failure(
    tmp_path: Path,
) -> None:
    driver = PiHarnessDriver.from_config()

    result = driver.run_fixture_task(
        tmp_path,
        profile=driver.profile("oss-no-tools"),
        provider_id="exa",
        task_id="current-fact-lookup-v1",
    )

    assert result.status == "unsupported"
    validation = validate_fixture_run(result.run_dir)
    assert validation.status == "unsupported"
    run = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    provider_call = load_record(result.run_dir, run["provider_call_refs"][0])
    assert provider_call["status"] == "not_applicable"
    assert provider_call["error_class"] == "tool_calling_unsupported"


def test_native_fixture_respects_profile_native_search_flag(tmp_path: Path) -> None:
    driver = PiHarnessDriver.from_config()

    result = driver.run_fixture_task(
        tmp_path,
        profile=driver.profile("oss-large"),
        provider_id="native",
        task_id="current-fact-lookup-v1",
    )

    assert result.status == "unsupported"
    validation = validate_fixture_run(result.run_dir)
    assert validation.status == "unsupported"
    run = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    metrics = load_record(result.run_dir, run["metrics_ref"])
    assert metrics["token_usage"] == {
        "accounting_source": "measured",
        "cached_input": 0,
        "input": 0,
        "output": 0,
        "reasoning": 0,
    }


def test_live_smoke_skips_without_explicit_flag(tmp_path: Path) -> None:
    driver = PiHarnessDriver.from_config()

    result = driver.run_live_smoke(
        tmp_path,
        profile_id="oss-small",
        provider_id="exa",
        env={},
    )

    assert result.status == "skipped"
    assert LIVE_ENV in result.reason


def test_live_smoke_skips_without_provider_credentials(tmp_path: Path) -> None:
    driver = PiHarnessDriver.from_config()

    result = driver.run_live_smoke(
        tmp_path,
        profile_id="oss-small",
        provider_id="exa",
        env={LIVE_ENV: "1"},
    )

    assert result.status == "skipped"
    assert "EXA_API_KEY" in result.reason


def test_live_smoke_writes_live_mode_when_runtime_and_credentials_exist(tmp_path: Path) -> None:
    fake_pi = tmp_path / "pi"
    fake_pi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(fake_pi, 0o755)
    driver = PiHarnessDriver.from_config()

    result = driver.run_live_smoke(
        tmp_path / "runs",
        profile_id="oss-small",
        provider_id="exa",
        env={
            LIVE_ENV: "1",
            "EXA_API_KEY": "test-key",
            "SEW_PI_BIN": str(fake_pi),
        },
    )

    assert result.status == "passed"
    assert result.run_dir is not None
    run = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    harness = load_record(result.run_dir, run["harness_ref"])
    assert run["mode"] == "live"
    assert harness["fixture_mode"] is False


def test_live_smoke_skips_when_runtime_probe_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pi = tmp_path / "pi"
    fake_pi.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    os.chmod(fake_pi, 0o755)
    driver = PiHarnessDriver.from_config()

    def timeout_probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=[str(fake_pi), "--help"], timeout=15)

    monkeypatch.setattr(subprocess, "run", timeout_probe)

    result = driver.run_live_smoke(
        tmp_path / "runs",
        profile_id="oss-small",
        provider_id="exa",
        env={
            LIVE_ENV: "1",
            "EXA_API_KEY": "test-key",
            "SEW_PI_BIN": str(fake_pi),
        },
    )

    assert result.status == "skipped"
    assert result.reason == "Pi runtime probe timed out"
    assert result.run_dir is None


def test_live_smoke_skips_when_runtime_probe_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pi = tmp_path / "pi"
    fake_pi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(fake_pi, 0o755)
    driver = PiHarnessDriver.from_config()

    def failed_probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("resource temporarily unavailable")

    monkeypatch.setattr(subprocess, "run", failed_probe)

    result = driver.run_live_smoke(
        tmp_path / "runs",
        profile_id="oss-small",
        provider_id="exa",
        env={
            LIVE_ENV: "1",
            "EXA_API_KEY": "test-key",
            "SEW_PI_BIN": str(fake_pi),
        },
    )

    assert result.status == "skipped"
    assert result.reason == "Pi runtime failed to start: resource temporarily unavailable"
    assert result.run_dir is None


def test_unknown_pi_profile_is_rejected() -> None:
    driver = PiHarnessDriver.from_config(MODULE_ROOT / "config" / "pi-model-profiles.yaml")

    with pytest.raises(SchemaError, match="unknown Pi model profile"):
        driver.profile("missing")
