"""Offline regression coverage for ARGVKEY-01."""

import json
import tempfile
from pathlib import Path

import pytest

from sew.arms import prepare_arm_spawn
from sew.harness import HarnessRunConfig
from sew.runner import RunnerError, load_provider_exposures
from sew import live_harness

FAKE_KEY = "fake-argvkey-credential-123"


def exposure(tmp_path, config, env=None):
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({"parallel-web": config}))
    return load_provider_exposures(path, env or {})["parallel-web"]


@pytest.mark.parametrize(
    "name",
    [
        "PROVIDER_KEY",
        "ACCESS_TOKEN",
        "CLIENT_SECRET",
        "SEW_PARALLEL_WEB_API_KEY",
        "AUTH_HEADER",
        "SERVICE_AUTH",
        "SERVICE_AUTHORIZATION",
        "SERVICE_BEARER",
        "SERVICE_PASSWORD",
        "SERVICE_PAT",
        "SERVICE_APIKEY",
        "SERVICE_CREDENTIALS",
        "CUSTOM_VALUE",
        "AUTH_URL",
        "TOKEN_PATH",
    ],
)
def test_secret_args_refused(tmp_path, name):
    with pytest.raises(
        RunnerError, match=r"parallel-web.*args.*use env or header_from_env"
    ) as error:
        exposure(tmp_path, {"command": "npx", "args": [f"Bearer ${{{name}}}"]}, {name: FAKE_KEY})
    assert FAKE_KEY not in str(error.value)


def test_documented_auth_header_reference_refused(tmp_path):
    with pytest.raises(RunnerError, match="args") as error:
        exposure(
            tmp_path,
            {"command": "npx", "args": ["--header", "Authorization:${AUTH_HEADER}"]},
            {"AUTH_HEADER": "Bearer " + FAKE_KEY},
        )
    assert FAKE_KEY not in str(error.value)


@pytest.mark.parametrize("prefix", ["Bearer ", "bearer ", "Basic ", "Token ", "x-api-key:"])
@pytest.mark.parametrize("literal", [False, True])
def test_credential_shaped_args_refused_after_resolution(tmp_path, prefix, literal):
    value = prefix + FAKE_KEY
    with pytest.raises(RunnerError, match="credential-shaped value in args") as error:
        exposure(
            tmp_path,
            {"command": "npx", "args": ["--header", value if literal else "${SERVICE_URL}"]},
            {"SERVICE_URL": value},
        )
    assert FAKE_KEY not in str(error.value)


@pytest.mark.parametrize("name", ["URL", "ENDPOINT", "HOST", "PATH", "DIR", "SERVICE_URL"])
def test_nonsecret_args_reference_allowlist(tmp_path, name):
    loaded = exposure(tmp_path, {"command": "npx", "args": [f"${{{name}}}"]}, {name: "/safe"})
    assert loaded.mcp_server_config["args"] == ["/safe"]


def test_nonsecret_args_and_env(tmp_path):
    loaded = exposure(
        tmp_path,
        {"command": "npx", "args": ["${ENDPOINT}"], "env": {"API_KEY": "${PROVIDER_KEY}"}},
        {"ENDPOINT": "https://example.invalid/mcp", "PROVIDER_KEY": FAKE_KEY},
    )
    assert loaded.mcp_server_config["args"] == ["https://example.invalid/mcp"]
    assert loaded.mcp_server_config["env"]["API_KEY"] == FAKE_KEY


def config(tmp_path, harness, *, header_value="Bearer ${PROVIDER_KEY}"):
    loaded = exposure(
        tmp_path,
        {
            "command": "/usr/bin/true",
            "args": [],
            "header_from_env": {"name": "Authorization", "value": header_value},
        },
        {"PROVIDER_KEY": FAKE_KEY},
    )
    return HarnessRunConfig(
        harness_id=harness,
        provider_id="parallel-web",
        external_provider=loaded,
        native_search_available=False,
        task_id="current-fact-lookup-v1",
        mode="live",
        harness_auth="account",
        binary="/usr/bin/true",
        run_id_override=f"header-{harness}",
    )


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
@pytest.mark.parametrize("fail", [False, True])
def test_header_private_and_removed_on_teardown(tmp_path, harness, fail):
    header = None
    try:
        with tempfile.TemporaryDirectory() as scratch:
            surface = prepare_arm_spawn(
                config(tmp_path, harness), Path(scratch), {}, harness_auth="broker"
            )
            header = Path(scratch) / ".secrets" / "provider-header.txt"
            assert header.parent.stat().st_mode & 0o777 == 0o700
            assert not (Path(scratch) / "provider-header.txt").exists()
            assert header.stat().st_mode & 0o777 == 0o600
            assert header.read_text() == f"Authorization: Bearer {FAKE_KEY}\n"
            assert FAKE_KEY not in surface.mcp_config_path.read_text()
            assert "--header-file" in surface.mcp_config_path.read_text()
            assert FAKE_KEY not in str(surface.harness_args)
            if fail:
                raise RuntimeError("fake failure")
    except RuntimeError:
        assert fail
    assert header is not None and not header.exists()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("artifact_kind", ["text", "binary", "read-only", "unwritable-secret"])
@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "Token", "Basic", "raw"])
def test_cell_bundle_never_contains_header_value(
    tmp_path, monkeypatch, fail, artifact_kind, scheme
):
    headers = []
    binary = b"\xff\x00\x80binary artifact\r\n" + FAKE_KEY.encode() + b"\r\n"
    write_bytes = Path.write_bytes

    def guarded_write(path, *args, **kwargs):
        if path.name in {"immutable.txt", "unwritable.txt"} and path.exists():
            raise PermissionError("read-only artifact must not be rewritten")
        return write_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", guarded_write)

    def capture(argv, **kwargs):
        header = kwargs["cwd"] / ".secrets" / "provider-header.txt"
        headers.append(header)
        assert header.exists()
        assert FAKE_KEY not in str(argv)
        (kwargs["provisional_meters_path"].parent / "echo.txt").write_text(FAKE_KEY)
        if artifact_kind == "binary":
            (kwargs["provisional_meters_path"].parent / "capture.bin").write_bytes(binary)
        elif artifact_kind == "read-only":
            artifact = kwargs["provisional_meters_path"].parent / "immutable.txt"
            artifact.write_text("safe text")
            artifact.chmod(0o444)
        elif artifact_kind == "unwritable-secret":
            artifact = kwargs["provisional_meters_path"].parent / "unwritable.txt"
            artifact.write_text(FAKE_KEY)
            artifact.chmod(0o444)
            # A later artifact must still be scrubbed after the failed write.
            (artifact.parent / "later.txt").write_text(FAKE_KEY)
        return live_harness.ProcessOutcome(
            events=[
                {
                    "received_at": "2026-10-04T00:00:00Z",
                    "event": {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": f"Answer echo {FAKE_KEY}",
                        "usage": {},
                        "num_turns": 1,
                    },
                }
            ],
            ready=True,
            exit_code=0,
            stderr_tail=f"echo {FAKE_KEY}",
            spawn_error=FAKE_KEY if fail else None,
        )

    monkeypatch.setattr(live_harness, "spawn_and_capture", capture)
    monkeypatch.setattr(live_harness, "provider_available", lambda *args, **kwargs: True)
    result = live_harness.run_live_harness(
        config(
            tmp_path,
            "claude-code",
            header_value=("${PROVIDER_KEY}" if scheme == "raw" else scheme + " ${PROVIDER_KEY}"),
        ),
        tmp_path / "out",
        environ={"SEW_HARNESS_LIVE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert result.status == ("harness_boot_failed" if fail else "succeeded")
    assert headers and all(not p.exists() for p in headers)
    if artifact_kind == "binary":
        assert (result.bundle_dir / "artifacts/capture.bin").read_bytes() == binary.replace(
            FAKE_KEY.encode(), b"<redacted:broker-token>"
        )
    elif artifact_kind == "read-only":
        artifact = result.bundle_dir / "artifacts/immutable.txt"
        assert artifact.read_text() == "safe text"
        assert artifact.stat().st_mode & 0o777 == 0o444
    elif artifact_kind == "unwritable-secret":
        assert not (result.bundle_dir / "artifacts/unwritable.txt").exists()
        assert FAKE_KEY not in (result.bundle_dir / "artifacts/later.txt").read_text()
    for artifact in result.bundle_dir.rglob("*"):
        if artifact.is_file():
            assert FAKE_KEY.encode() not in artifact.read_bytes()


@pytest.mark.parametrize("header_value", ["", "Bearer "])
def test_empty_header_token_preserves_cell_output(tmp_path, monkeypatch, header_value):
    def capture(argv, **kwargs):
        (kwargs["provisional_meters_path"].parent / "echo.json").write_text(
            json.dumps({"answer": "abc"})
        )
        return live_harness.ProcessOutcome(
            events=[
                {
                    "received_at": "2026-10-04T00:00:00Z",
                    "event": {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "abc",
                        "usage": {},
                        "num_turns": 1,
                    },
                }
            ],
            ready=True,
            exit_code=0,
            stderr_tail="ordinary stderr",
        )

    monkeypatch.setattr(live_harness, "spawn_and_capture", capture)
    monkeypatch.setattr(live_harness, "provider_available", lambda *args, **kwargs: True)
    result = live_harness.run_live_harness(
        config(tmp_path, "claude-code", header_value=header_value),
        tmp_path / "out",
        environ={"SEW_HARNESS_LIVE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert result.status == "succeeded"
    assert json.loads((result.bundle_dir / "artifacts/echo.json").read_text()) == {"answer": "abc"}
    assert json.loads((result.bundle_dir / "artifacts/final-answer.json").read_text()) == {
        "answer": "abc",
        "citation_urls": [],
    }
    transcript = json.loads((result.bundle_dir / "artifacts/transcript.json").read_text())
    assert (
        next(item["harness_event"] for item in transcript if item["role"] == "harness")["result"]
        == "abc"
    )
    assert (result.bundle_dir / "artifacts/harness-stderr.txt").read_text().strip() == (
        "ordinary stderr"
    )


def test_binary_artifact_still_checked_for_secret_material(tmp_path):
    from sew.harness import assert_no_secret_material
    from sew.schema import SchemaError

    artifact = tmp_path / "capture.bin"
    artifact.write_bytes(b"\xff\x00Authorization: Bearer fake-binary-credential\x80")
    with pytest.raises(SchemaError, match="secret-like material"):
        assert_no_secret_material(tmp_path)


@pytest.mark.parametrize(
    "header",
    [
        {"name": "Authorization\nInjected", "value": FAKE_KEY},
        {"name": "Authorization", "value": FAKE_KEY + "\r\nInjected: value"},
    ],
)
def test_header_rejects_line_injection(tmp_path, header):
    from dataclasses import replace
    from sew.schema import SchemaError

    cfg = config(tmp_path, "claude-code")
    server = {**cfg.external_provider.mcp_server_config, "header_from_env": header}
    cfg = replace(cfg, external_provider=replace(cfg.external_provider, mcp_server_config=server))
    with pytest.raises(SchemaError, match="header_from_env"):
        prepare_arm_spawn(cfg, tmp_path, {}, harness_auth="broker")
    assert not (tmp_path / ".secrets").exists()
