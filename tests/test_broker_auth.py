from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path
import pytest
from sew import broker_auth
from sew.host import StandaloneHost


class BrokerHost:
    mode = "agent-os"

    def harness_auth(self, harness):
        if harness == "claude-code":
            return {
                "env": {"ANTHROPIC_AUTH_TOKEN": "fake-token"},
                "fingerprint": "receipt",
                "expires_at": "999",
            }
        return {
            "auth": {
                "last_refresh": "2026-10-04T00:00:00Z",
                "tokens": {
                    "access_token": "fake-token",
                    "refresh_token": "agent-os-per-worker-placeholder-no-rotate",
                    "expires_at": 4102444800,
                },
            }
        }


@pytest.fixture
def broker_host(monkeypatch):
    from sew import host

    plugin = BrokerHost()
    monkeypatch.setattr(host, "get_host", lambda env=None: plugin)
    return plugin


@pytest.fixture
def writer_shaped_auth():
    # runtime/codex/bin/codex-worker-auth-sync uses _epoch_seconds floats
    # and _iso_utc(fetched_at), preserving microseconds with a Z suffix.
    return {
        "last_refresh": "2026-10-04T00:00:00.123456Z",
        "tokens": {
            "id_token": "fixture-id-token",
            "access_token": "fake-token",
            "refresh_token": "agent-os-per-worker-placeholder-no-rotate",
            "account_id": "fixture-account",
            "fetched_at": 1791072000.123456,
            "expires_at": 1791075600.123456,
        },
    }


def test_codex_writer_shaped_receipt_preserves_document(
    tmp_path, broker_host, monkeypatch, writer_shaped_auth
):
    monkeypatch.setattr(broker_auth.time, "time", lambda: 1791072000.123456)
    broker_host.harness_auth = lambda harness: {
        "source": "broker",
        "auth": writer_shaped_auth,
    }
    metadata = broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    assert json.loads((tmp_path / "home/auth.json").read_text()) == writer_shaped_auth
    assert metadata["expires_at"] == "1791075600.123456"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("expires_at", "private-invalid-value", "expires_at must be numeric"),
        ("expires_at", None, "expires_at must be numeric"),
        ("last_refresh", "private-invalid-value", "last_refresh is not RFC3339"),
        ("fetched_at", "private-invalid-value", "fetched_at must be numeric"),
        ("fetched_at", None, "fetched_at must be numeric"),
    ],
)
def test_codex_malformed_fields_surface_safe_reason(tmp_path, broker_host, field, value, reason):
    receipt = broker_host.harness_auth("codex")
    target = receipt["auth"] if field == "last_refresh" else receipt["auth"]["tokens"]
    target[field] = value
    calls = []

    def fetch(harness):
        calls.append(harness)
        return receipt

    broker_host.harness_auth = fetch
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"stale":true}')
    with pytest.raises(broker_auth.BrokerAuthError, match=reason) as caught:
        broker_auth.codex_home_auth(home, tmp_path / "log")
    assert calls == ["codex"]
    assert not (home / "auth.json").exists()
    diagnostic = (tmp_path / "log/codex-broker-auth-stderr.log").read_text()
    assert reason in diagnostic
    assert "private-invalid-value" not in str(caught.value) + diagnostic


def test_claude_host_receipt(tmp_path, broker_host):
    child = broker_auth.claude_code_env(tmp_path / "log")
    assert child == {"ANTHROPIC_AUTH_TOKEN": "fake-token"}
    assert child.metadata == {
        "source": "broker",
        "fingerprint": "receipt",
        "expires_at": "999",
    }
    assert broker_auth.auth_source() == "broker"
    assert broker_auth.auth_source("account") == "account"


def test_codex_safe_auth(tmp_path, broker_host):
    metadata = broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    path = tmp_path / "home/auth.json"
    assert (
        json.loads(path.read_text())["tokens"]["refresh_token"]
        == "agent-os-per-worker-placeholder-no-rotate"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert metadata["source"] == "broker"


def test_codex_rejects_rotating_token(tmp_path, broker_host):
    broker_host.harness_auth = lambda harness: {"auth": {"tokens": {"refresh_token": "unsafe"}}}
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    assert not (tmp_path / "home/auth.json").exists()


def test_standalone_broker_refused_without_side_effects(tmp_path, monkeypatch):
    from sew import host

    monkeypatch.setattr(host, "get_host", lambda env=None: StandaloneHost({}))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("spawned broker"))
    assert broker_auth.auth_source() == "account"
    assert broker_auth.auth_source("account") == "account"
    for call in [
        lambda: broker_auth.auth_source("broker"),
        lambda: broker_auth.claude_code_env(tmp_path / "log"),
        lambda: broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log"),
    ]:
        with pytest.raises(broker_auth.BrokerAuthError, match="standalone.*account login"):
            call()
    assert not (tmp_path / "log").exists()


def test_broker_failure_scrubs_diagnostic(tmp_path, broker_host):
    def fail(harness):
        raise subprocess.CalledProcessError(75, [], stderr=b"Authorization: Bearer secret-value")

    broker_host.harness_auth = fail
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.claude_code_env(tmp_path / "log")
    diagnostic = tmp_path / "log/claude-code-broker-auth-stderr.log"
    assert "secret-value" not in diagnostic.read_text()
    assert diagnostic.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("existing", [False, True])
def test_diagnostic_permissions_are_restricted_before_writing(tmp_path, monkeypatch, existing):
    diagnostic = tmp_path / "claude-code-broker-auth-stderr.log"
    if existing:
        diagnostic.write_text("stale diagnostic")
        diagnostic.chmod(0o666)
    original_open = os.open
    original_fdopen = os.fdopen
    opened = []

    def open_diagnostic(path, flags, mode):
        fd = original_open(path, flags, mode)
        opened.append(fd)
        if not existing:
            assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        return fd

    def fdopen_diagnostic(fd, mode):
        stream = original_fdopen(fd, mode)
        original_write = stream.write

        def write(data):
            assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
            return original_write(data)

        monkeypatch.setattr(stream, "write", write)
        return stream

    monkeypatch.setattr(broker_auth.os, "open", open_diagnostic)
    monkeypatch.setattr(broker_auth.os, "fdopen", fdopen_diagnostic)
    broker_auth._auth_failure(
        "claude-code",
        tmp_path,
        subprocess.CalledProcessError(1, [], stderr=b"Authorization: Bearer secret-value"),
    )
    assert len(opened) == 1
    assert "secret-value" not in diagnostic.read_text()
    assert "stale diagnostic" not in diagnostic.read_text()
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("stderr", "transient"),
    [
        (
            b"claude-code adapter: outcome=oauth-broker-unreachable http=000; broker call failed at network layer (curl exit); failing closed",
            True,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-unreachable http=503; broker returned non-200 status (http=503)",
            True,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-gate-saturated http=000; failing closed",
            True,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-bad-response http=200; failing closed",
            True,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-unreachable http=429; failing closed",
            True,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-auth-failed http=503; failing closed",
            False,
        ),
        (
            b"claude-code adapter: outcome=model_not_entitled http=403; failing closed",
            False,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-auth-security-block http=503; failing closed",
            False,
        ),
        (
            b"claude-code adapter: outcome=oauth-broker-standby-model-divergence http=403; failing closed",
            False,
        ),
        (b"SSL certificate verify failed", False),
        (b"claude-code adapter: outcome=oauth-broker-auth-degraded http=401", False),
        (
            b"claude-code adapter: outcome=oauth-broker-secret-unreadable http=000",
            False,
        ),
        (b"claude-code adapter: outcome=future-permanent-refusal http=503", False),
    ],
)
def test_claude_adapter_outcomes_control_retries(stderr: bytes, transient: bool) -> None:
    error = subprocess.CalledProcessError(1, ["bash"], stderr=stderr)
    assert broker_auth._transient(error) is transient


def test_transient_host_fetch_retries(tmp_path, broker_host, monkeypatch):
    seen = []
    delays = []
    original = broker_host.harness_auth

    def fetch(harness):
        seen.append(harness)
        if len(seen) < 3:
            raise subprocess.TimeoutExpired([], 30)
        return original(harness)

    broker_host.harness_auth = fetch
    monkeypatch.setattr(broker_auth.time, "sleep", delays.append)
    assert broker_auth.claude_code_env(tmp_path / "log")["ANTHROPIC_AUTH_TOKEN"] == "fake-token"
    assert len(seen) == 3
    assert delays == [0.5, 1.0]


def test_codex_fetch_failure_removes_stale_auth(tmp_path, broker_host):
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"stale":true}')

    def fail(harness):
        raise RuntimeError("broker unavailable")

    broker_host.harness_auth = fail
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.codex_home_auth(home, tmp_path / "log")
    assert not (home / "auth.json").exists()


def test_claude_malformed_receipt_fails_closed(tmp_path, broker_host):
    broker_host.harness_auth = lambda harness: {"env": {}}
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.claude_code_env(tmp_path / "log")


def test_sdk_wrapped_transport_error_retries_and_preserves_scrubbed_stderr(
    tmp_path, broker_host, monkeypatch
):
    calls = []
    delays = []

    def fail(harness):
        calls.append(harness)
        try:
            raise subprocess.CalledProcessError(
                75,
                [],
                stderr=b"outcome=oauth-broker-unreachable http=503\nAuthorization: Bearer secret-value",
            )
        except subprocess.CalledProcessError:
            raise RuntimeError("SDK broker authentication failed") from None

    broker_host.harness_auth = fail
    monkeypatch.setattr(broker_auth.time, "sleep", delays.append)
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.claude_code_env(tmp_path / "log")
    assert len(calls) == 3
    assert delays == [0.5, 1.0]
    text = (tmp_path / "log/claude-code-broker-auth-stderr.log").read_text()
    assert "outcome=oauth-broker-unreachable http=503" in text
    assert "secret-value" not in text


@pytest.mark.parametrize("expiry", [0, 999, float("nan"), float("inf")])
def test_codex_expired_receipt_refused_before_write(tmp_path, broker_host, expiry):
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["tokens"]["expires_at"] = expiry
    broker_host.harness_auth = lambda harness: receipt
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"stale":true}')
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.codex_home_auth(home, tmp_path / "log")
    assert not (home / "auth.json").exists()
    assert (
        "expired; refusing harness spawn"
        in (tmp_path / "log/codex-broker-auth-stderr.log").read_text()
    )


def test_codex_missing_refresh_stamp_refused(tmp_path, broker_host):
    receipt = broker_host.harness_auth("codex")
    del receipt["auth"]["last_refresh"]
    broker_host.harness_auth = lambda harness: receipt
    with pytest.raises(broker_auth.BrokerAuthError):
        broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    assert not (tmp_path / "home/auth.json").exists()


@pytest.mark.parametrize("remaining", [1, 119, 120, 121])
def test_codex_requires_boot_lifetime_margin(tmp_path, broker_host, monkeypatch, remaining):
    monkeypatch.setattr(broker_auth.time, "time", lambda: 1000)
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["last_refresh"] = "1970-01-01T00:16:40Z"
    receipt["auth"]["tokens"]["expires_at"] = 1000 + remaining
    calls = []

    def fetch(harness):
        calls.append(harness)
        return receipt

    broker_host.harness_auth = fetch
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"stale":true}')
    if remaining <= 120:
        with pytest.raises(broker_auth.BrokerAuthError, match="expires within 120s"):
            broker_auth.codex_home_auth(home, tmp_path / "log")
        assert not (home / "auth.json").exists()
    else:
        broker_auth.codex_home_auth(home, tmp_path / "log")
        assert json.loads((home / "auth.json").read_text()) == receipt["auth"]
    assert calls == ["codex"]


@pytest.mark.parametrize(
    ("stamp", "fetched_at", "reason"),
    [
        ("2026-10-04T00:00:00Z", 1791071999, "does not match fetched_at"),
        ("2026-10-04T00:00:00Z", 1791072001, "does not match fetched_at"),
        ("2026-10-04T00:00:00Z", float("nan"), "does not match fetched_at"),
        ("2026-10-04T00:00:00Z", float("inf"), "does not match fetched_at"),
        ("2026-10-04T00:01:00.000001Z", 1791072060.000001, "is in the future"),
        ("2026-10-04T00:00:00", 1791072000, "must include timezone"),
    ],
)
def test_codex_refresh_stamp_drift_removes_stale_auth(
    tmp_path, broker_host, monkeypatch, stamp, fetched_at, reason
):
    monkeypatch.setattr(broker_auth.time, "time", lambda: 1791072000)
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["last_refresh"] = stamp
    receipt["auth"]["tokens"]["fetched_at"] = fetched_at
    broker_host.harness_auth = lambda harness: receipt
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"stale":true}')
    with pytest.raises(broker_auth.BrokerAuthError, match=reason):
        broker_auth.codex_home_auth(home, tmp_path / "log")
    assert not (home / "auth.json").exists()


@pytest.mark.parametrize("fetched_at", [1791072000, 1791072000.000001, 1791071999.000001])
def test_codex_refresh_stamp_matches_fetch_with_subsecond_precision(
    tmp_path, broker_host, monkeypatch, fetched_at
):
    monkeypatch.setattr(broker_auth.time, "time", lambda: 1791072000)
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["tokens"]["fetched_at"] = fetched_at
    broker_host.harness_auth = lambda harness: receipt
    broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    assert json.loads((tmp_path / "home/auth.json").read_text()) == receipt["auth"]


def test_refusal_reason_surfaces_by_type(tmp_path):
    error = broker_auth._auth_failure(
        "codex", tmp_path, broker_auth.ReceiptRefused("new safe validation reason")
    )
    assert "new safe validation reason" in str(error)
    error = broker_auth._auth_failure("codex", tmp_path, ValueError("untrusted receipt content"))
    assert "untrusted receipt content" not in str(error)


def test_sdk_host_receipt_preserves_document(tmp_path, monkeypatch, writer_shaped_auth):
    sdk_host = pytest.importorskip("agent_os_app_sdk.host")
    from sew import host
    from types import SimpleNamespace

    secret = tmp_path / "secret"
    secret.write_text("fixture")
    monkeypatch.setenv("OAUTH_BROKER_SHARED_SECRET_FILE", str(secret))
    monkeypatch.setenv("OP_BIOMETRIC_UNLOCK_ENABLED", "false")
    monkeypatch.setattr(sdk_host, "_host_paths", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(broker_auth.time, "time", lambda: 1791072000.123456)
    auth = writer_shaped_auth

    def run(argv, **kwargs):
        assert argv[1].endswith("runtime/codex/bin/codex-worker-auth-sync")
        Path(kwargs["env"]["CODEX_WORKER_AUTH_PATH"]).write_text(json.dumps(auth))
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(sdk_host, "_run_broker", run)
    monkeypatch.setattr(host, "get_host", lambda env=None: sdk_host.sew_host())
    broker_auth.codex_home_auth(tmp_path / "cell", tmp_path / "log")
    assert json.loads((tmp_path / "cell/auth.json").read_text()) == auth


@pytest.mark.parametrize("valid", [True, False])
def test_doctor_codex_auth_check_without_model_turn(tmp_path, monkeypatch, capsys, valid):
    sdk_host = pytest.importorskip("agent_os_app_sdk.host")
    from sew import doctor, host
    from types import SimpleNamespace
    import sys

    secret = tmp_path / "secret"
    secret.write_text("fixture")
    monkeypatch.setenv("OAUTH_BROKER_SHARED_SECRET_FILE", str(secret))
    monkeypatch.setattr(sdk_host, "_host_paths", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(host, "get_host", lambda env=None: sdk_host.sew_host())
    monkeypatch.setattr(doctor, "doctor", lambda: "mode agent-os")
    auth = BrokerHost().harness_auth("codex")["auth"]
    if not valid:
        auth["tokens"]["expires_at"] = 0
    writer_argv = [
        sys.executable,
        str(tmp_path / "runtime/codex/bin/codex-worker-auth-sync"),
    ]
    calls = []
    auth_paths = []

    def run(argv, **kwargs):
        assert argv == writer_argv, "doctor spawned a process other than the auth writer"
        calls.append(argv)
        assert kwargs["env"]["OP_BIOMETRIC_UNLOCK_ENABLED"] == "false"
        auth_path = Path(kwargs["env"]["CODEX_WORKER_AUTH_PATH"])
        auth_paths.append(auth_path)
        auth_path.write_text(json.dumps(auth))
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(sdk_host, "_run_broker", run)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("spawned model"))
    assert doctor.command(SimpleNamespace(codex_broker_auth=True)) == (0 if valid else 1)
    assert calls == [writer_argv]
    assert all(not path.exists() for path in auth_paths)
    output = capsys.readouterr().out
    assert "fake-token" not in output
    assert (
        "codex broker auth validated" if valid else "receipt expired; refusing harness spawn"
    ) in output


def test_doctor_surfaces_scrubbed_diagnostic_before_cleanup(broker_host, monkeypatch, capsys):
    from sew import doctor
    from types import SimpleNamespace

    monkeypatch.setattr(doctor, "doctor", lambda: "mode agent-os")

    def fail(harness):
        raise subprocess.CalledProcessError(
            1,
            [],
            stderr=b"outcome=oauth-broker-auth-failed http=503\nAuthorization: Bearer secret-value",
        )

    broker_host.harness_auth = fail
    assert doctor.command(SimpleNamespace(codex_broker_auth=True)) == 1
    output = capsys.readouterr().out
    assert "outcome=oauth-broker-auth-failed http=503" in output
    assert "secret-value" not in output
    assert "file removed after this check" in output
    path = output.split("diagnostic: ", 1)[1].splitlines()[0]
    assert not Path(path).exists()


@pytest.mark.parametrize("offset", [0.000001, 0.001, 60])
def test_codex_accepts_broker_clock_skew(tmp_path, broker_host, monkeypatch, offset):
    from datetime import datetime, timezone

    now = 1791072000
    monkeypatch.setattr(broker_auth.time, "time", lambda: now)
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["last_refresh"] = datetime.fromtimestamp(now + offset, timezone.utc).isoformat()
    receipt["auth"]["tokens"]["fetched_at"] = now + offset
    broker_host.harness_auth = lambda harness: receipt
    home = tmp_path / "home"
    broker_auth.codex_home_auth(home, tmp_path / "log")
    assert json.loads((home / "auth.json").read_text()) == receipt["auth"]


def test_doctor_reports_host_setup_error(monkeypatch, capsys):
    from types import SimpleNamespace
    from sew import doctor, host

    monkeypatch.setattr(doctor, "doctor", lambda: "mode agent-os")

    def fail(environ):
        raise RuntimeError("host setup unavailable; Authorization: Bearer secret-value")

    monkeypatch.setattr(host, "get_host", fail)
    assert doctor.command(SimpleNamespace(codex_broker_auth=True)) == 1
    output = capsys.readouterr().out
    assert "codex broker auth validation failed: host setup unavailable" in output
    assert "secret-value" not in output


def test_doctor_scrubber_failure_withholds_exception_text(monkeypatch, capsys):
    from sew import doctor, host, live_harness
    from types import SimpleNamespace

    monkeypatch.setattr(doctor, "doctor", lambda: "mode agent-os")

    def fail(environ):
        raise RuntimeError("Authorization: Bearer secret-value")

    def broken_scrubber(text):
        raise ImportError("scrubber unavailable")

    monkeypatch.setattr(host, "get_host", fail)
    monkeypatch.setattr(live_harness, "scrub_text", broken_scrubber)
    assert doctor.command(SimpleNamespace(codex_broker_auth=True)) == 1
    output = capsys.readouterr().out
    assert "codex broker auth validation failed: RuntimeError" in output
    assert "secret-value" not in output


@pytest.mark.parametrize("valid", [True, False])
def test_doctor_broker_probe_skips_full_report(broker_host, monkeypatch, capsys, valid):
    from sew import doctor
    from sew.cli import main

    def broken_report():
        raise RuntimeError("sandbox qualification unavailable")

    monkeypatch.setattr(doctor, "doctor", broken_report)
    receipt = broker_host.harness_auth("codex")
    if not valid:
        receipt["auth"]["tokens"]["expires_at"] = 0
    broker_host.harness_auth = lambda harness: receipt
    assert main(["doctor", "--codex-broker-auth"]) == (0 if valid else 1)
    output = capsys.readouterr().out
    assert "sandbox qualification unavailable" not in output
    assert (
        "codex broker auth validated" if valid else "receipt expired; refusing harness spawn"
    ) in output


@pytest.mark.parametrize("valid", [True, False])
def test_doctor_cli_broker_receipt_and_cleanup(broker_host, monkeypatch, capsys, valid):
    from sew import doctor
    from sew.cli import main

    monkeypatch.setattr(doctor, "doctor", lambda: "mode agent-os")
    receipt = broker_host.harness_auth("codex")
    if not valid:
        receipt["auth"]["tokens"]["expires_at"] = 0
    calls = []
    paths = []
    original = broker_auth.codex_home_auth

    def fetch(harness):
        calls.append(harness)
        return receipt

    def check(home, log):
        paths.extend([home.parent, home / "auth.json", log])
        return original(home, log)

    broker_host.harness_auth = fetch
    monkeypatch.setattr(broker_auth, "codex_home_auth", check)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("spawned model"))
    assert main(["doctor", "--codex-broker-auth"]) == (0 if valid else 1)
    assert calls == ["codex"]
    assert all(not path.exists() for path in paths)
    output = capsys.readouterr().out
    assert "fake-token" not in output
    assert (
        "codex broker auth validated; expires_at=4102444800"
        if valid
        else "receipt expired; refusing harness spawn"
    ) in output


def test_doctor_cli_standalone_refuses_broker(monkeypatch, capsys):
    from sew import doctor, host
    from sew.cli import main

    monkeypatch.setattr(doctor, "doctor", lambda: "mode standalone")
    monkeypatch.setattr(host, "get_host", lambda env=None: StandaloneHost({}))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("spawned broker"))
    assert main(["doctor", "--codex-broker-auth"]) == 1
    assert "account login" in capsys.readouterr().out


def test_doctor_default_does_not_fetch_broker(monkeypatch, capsys):
    from sew import doctor
    from sew.cli import main

    monkeypatch.setattr(doctor, "doctor", lambda: "mode standalone")
    monkeypatch.setattr(broker_auth, "codex_home_auth", lambda *a: pytest.fail("fetched auth"))
    assert main(["doctor"]) == 0
    assert capsys.readouterr().out.strip() == "mode standalone"


def test_codex_old_refresh_stamp_preserves_operator_policy(tmp_path, broker_host, monkeypatch):
    monkeypatch.setattr(broker_auth.time, "time", lambda: 2_000_000_000)
    receipt = broker_host.harness_auth("codex")
    receipt["auth"]["last_refresh"] = "1970-01-01T00:16:40Z"
    receipt["auth"]["tokens"]["fetched_at"] = 1000.0
    receipt["auth"]["tokens"]["expires_at"] = 2_000_000_500.0
    broker_host.harness_auth = lambda harness: receipt
    broker_auth.codex_home_auth(tmp_path / "home", tmp_path / "log")
    assert json.loads((tmp_path / "home/auth.json").read_text()) == receipt["auth"]
