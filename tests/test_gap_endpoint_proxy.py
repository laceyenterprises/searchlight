from __future__ import annotations

import errno
import http.client
import json
import socket
import subprocess
import sys
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sew.gap.endpoint_proxy import endpoint_proxy
from sew.gap.workspace import EgressCanaryRefused, bench_network_boundary
from sew.gap import workspace


@pytest.fixture
def upstream():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(self.path.encode())

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize("tunnel", [False, True])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_proxy_relays_only_selected_authority(upstream, tunnel, method):
    records = socket.getaddrinfo("127.0.0.1", upstream, type=socket.SOCK_STREAM)
    # Use a name that does not resolve: only the saved DNS answer may be used.
    with endpoint_proxy({("endpoint.invalid", upstream): records}) as proxy:
        port = urlsplit(proxy).port
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as client:
            if tunnel:
                client.set_tunnel("endpoint.invalid", upstream)
                path = "/v1/test?query=1"
            else:
                path = f"http://endpoint.invalid:{upstream}/v1/test?query=1"
            client.request(method, path, body=b"request body" if method == "POST" else None)
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == (b"request body" if method == "POST" else b"/v1/test?query=1")
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as client:
            path = f"endpoint.invalid:{upstream + 1}" if tunnel else "http://other.invalid/"
            client.request("CONNECT" if tunnel else "GET", path)
            assert client.getresponse().status == 403
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


@pytest.mark.parametrize(
    "url",
    [
        "https://endpoint.invalid:8443/v1",
        "http://endpoint.invalid:8080/v1",
        "http://endpoint.invalid/v1",
        "https://endpoint.invalid/v1",
    ],
)
def test_boundary_preserves_endpoint_port(monkeypatch, tmp_path, url):
    from sew.gap import sandbox

    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("seatbelt", "/usr/bin/sandbox-exec"),
    )
    seen = []

    def resolve(host, port, **kwargs):
        seen.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port))]

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, '{"connected":false,"errno":1}', ""
        ),
    )
    env = {"OPENAI_BASE_URL": url, "HTTPS_PROXY": "http://stale.invalid", "NO_PROXY": "*"}
    expected = urlsplit(url).port or (443 if url.startswith("https:") else 80)
    with bench_network_boundary(["codex"], env, cwd=tmp_path, harness_id="codex") as (_, evidence):
        assert seen == [("endpoint.invalid", expected)]
        assert evidence["endpoint_authorities"] == [{"host": "endpoint.invalid", "port": expected}]
        assert env["HTTPS_PROXY"] == env["https_proxy"] == env["HTTP_PROXY"]
        assert env["NO_PROXY"] == env["no_proxy"] == "localhost,127.0.0.1,::1"


@pytest.mark.parametrize(
    "url", ["http://host:bad", "ftp://host/", "http:///v1", "http://host:99999", "http://host:0"]
)
def test_boundary_rejects_invalid_endpoint(tmp_path, url, require_containment):
    require_containment("seatbelt")
    with pytest.raises(EgressCanaryRefused, match="invalid or unresolved"):
        with bench_network_boundary(
            ["codex"], {"OPENAI_BASE_URL": url}, cwd=tmp_path, harness_id="codex"
        ):
            pytest.fail("invalid endpoint admitted")


def test_proxy_closes_after_boundary_probe_refusal(monkeypatch, tmp_path):
    records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))]
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, '{"connected":true,"errno":null}', ""
        ),
    )
    env = {}
    with pytest.raises(EgressCanaryRefused):
        with bench_network_boundary(["codex"], env, cwd=tmp_path, harness_id="codex"):
            pytest.fail("connected probe admitted")
    with pytest.raises(OSError):
        socket.socket().connect(("127.0.0.1", urlsplit(env["HTTPS_PROXY"]).port))


@pytest.mark.parametrize("tunnel", [False, True])
@pytest.mark.parametrize("stage", ["create", "timeout", "connect"])
@pytest.mark.parametrize("ipv4_available", [False, True])
def test_proxy_falls_back_after_socket_errors(monkeypatch, upstream, tunnel, stage, ipv4_available):
    records = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", upstream, 0, 0))]
    if ipv4_available:
        records += socket.getaddrinfo("127.0.0.1", upstream, type=socket.SOCK_STREAM)
    real_socket = socket.socket
    closed = []

    class UnsupportedSocket:
        def settimeout(self, timeout):
            if stage == "timeout":
                raise OSError(errno.EIO, "socket setup failed")

        def connect(self, address):
            raise OSError(errno.ENETUNREACH, "IPv6 unavailable")

        def close(self):
            closed.append(True)

    def create(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_INET6:
            if stage == "create":
                raise OSError(errno.EAFNOSUPPORT, "IPv6 unsupported")
            return UnsupportedSocket()
        return real_socket(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", create)
    with endpoint_proxy({("endpoint.invalid", upstream): records}) as proxy:
        with closing(
            http.client.HTTPConnection("127.0.0.1", urlsplit(proxy).port, timeout=3)
        ) as client:
            if tunnel and ipv4_available:
                client.set_tunnel("endpoint.invalid", upstream)
                client.request("GET", "/fallback")
            else:
                client.request(
                    "CONNECT" if tunnel else "GET",
                    f"endpoint.invalid:{upstream}"
                    if tunnel
                    else f"http://endpoint.invalid:{upstream}/fallback",
                )
            response = client.getresponse()
            assert response.status == (200 if ipv4_available else 502)
            if ipv4_available:
                assert response.read() == b"/fallback"
    assert closed == ([] if stage == "create" else [True])


@pytest.fixture
def probe_environment(monkeypatch):
    records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))]
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: records)
    delays = []
    monkeypatch.setattr("time.sleep", delays.append)
    return delays


@pytest.mark.parametrize(
    "error", ["timeout", errno.EAGAIN, errno.EIO, errno.EINTR, errno.ETIMEDOUT]
)
@pytest.mark.parametrize("recovers", [False, True])
def test_bench_probe_retries_transient_errors(
    monkeypatch, tmp_path, probe_environment, error, recovers
):
    calls = []
    failures = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if recovers and len(calls) == 3:
            return subprocess.CompletedProcess(argv, 0, '{"connected":false,"errno":1}', "")
        failure = (
            subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if error == "timeout"
            else OSError(error, "transient spawn failure")
        )
        failures.append(failure)
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    env = {}
    if recovers:
        with bench_network_boundary(["codex"], env, cwd=tmp_path, harness_id="codex") as (
            _,
            evidence,
        ):
            assert evidence["attribution"] == "harness-runner-permission:codex-sandbox"
    else:
        with pytest.raises(EgressCanaryRefused, match="bench probe failed") as refusal:
            with bench_network_boundary(["codex"], env, cwd=tmp_path, harness_id="codex"):
                pytest.fail("exhausted probe admitted")
        assert refusal.value.__cause__ is failures[-1]
    assert len(calls) == 3
    assert all(call == calls[0] for call in calls)
    assert probe_environment == [0.5, 1.0]
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", urlsplit(env["HTTPS_PROXY"]).port), timeout=1)


@pytest.mark.parametrize(
    "error", [errno.ENOENT, errno.EACCES, "exit", "json", "connected", "unproven"]
)
def test_bench_probe_does_not_retry_terminal_failures(
    monkeypatch, tmp_path, probe_environment, error
):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if isinstance(error, int):
            raise OSError(error, "permanent spawn failure")
        outputs = {
            "exit": (1, "", "sandbox profile compilation failed"),
            "json": (0, "invalid json", ""),
            "connected": (0, '{"connected":true,"errno":null}', ""),
            "unproven": (0, '{"connected":false,"errno":60}', ""),
        }
        return subprocess.CompletedProcess(argv, *outputs[error])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(EgressCanaryRefused) as refusal:
        with bench_network_boundary(["codex"], {}, cwd=tmp_path, harness_id="codex"):
            pytest.fail("failed probe admitted")
    assert len(calls) == 1
    assert probe_environment == []
    if error == "exit":
        assert "sandbox profile compilation failed" in str(refusal.value.__cause__)


REAL_RUNNER = workspace.sandbox_probe_runner


@pytest.fixture(autouse=True)
def fixture_runner(monkeypatch):
    # These tests qualify the model-runner probe, not the Linux namespace path.
    # Real bubblewrap transport and verifier coverage lives in test_gap_sandbox.
    from sew.gap import sandbox

    monkeypatch.setattr(
        sandbox, "select_backend",
        lambda env=None: sandbox.SandboxBackend("seatbelt", "/fixture/sandbox-exec"),
    )
    monkeypatch.setattr(
        workspace,
        "sandbox_probe_runner",
        lambda *a, **kw: (
            ["codex", "sandbox", "--", sys.executable, "-c", workspace.DIRECT_PROBE],
            {
                "runner": "sandbox",
                "runner_id": "codex-sandbox",
                "runner_version": "fixture",
                "policy": {"network_access": False},
            },
        ),
        raising=False,
    )


def test_harness_without_cache_still_denies_verifier_root(monkeypatch, tmp_path, probe_environment):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"connected":false,"errno":1}', "")

    monkeypatch.setattr(subprocess, "run", run)
    original = ["codex", "exec", "--sandbox", "workspace-write"]
    with bench_network_boundary(original, {}, cwd=tmp_path, harness_id="codex") as (argv, evidence):
        assert argv[:2] == ["/fixture/sandbox-exec", "-p"]
        assert str(workspace.VERIFIER_ROOT) in argv[2]
        assert argv[3:] == original
        assert calls[0][:3] == argv[:3]
        assert calls[0][3:5] == ["codex", "sandbox"]
        assert evidence["runner_version"] == "fixture"


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_cache_boundary_wraps_harness_and_nested_runner(
    monkeypatch, tmp_path, probe_environment, harness
):
    cache = tmp_path / "cache"
    cache.mkdir()
    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)
    prefix = []

    def qualify(wrap, wheelhouse, **kw):
        assert wheelhouse == cache
        prefix.extend(wrap([]))
        assert prefix[:2] == ["/fixture/sandbox-exec", "-p"]
        assert "file-read* file-write*" in prefix[2]
        assert str(cache) in prefix[2]
        return {"direct_denied": True, "discovery_denied": True, "harness_tree_confined": True}

    def run(argv, **kw):
        assert argv[:len(prefix)] == prefix
        assert argv[len(prefix):len(prefix) + 2] == ["codex", "sandbox"]
        return subprocess.CompletedProcess(argv, 0, '{"connected":false,"errno":1}', "")

    monkeypatch.setattr(workspace, "qualify_cache_reads", qualify)
    copy_probes = []

    def qualify_copies(wrap, wheelhouse, **kw):
        assert wheelhouse == cache
        assert wrap([]) == prefix
        assert str(workspace.VERIFIER_ROOT) in prefix[2]
        copy_probes.append(wheelhouse)
        return {"wheel": {"direct_denied": True}, "installed_source": {"direct_denied": True}}

    monkeypatch.setattr(workspace, "qualify_verifier_reads", qualify_copies)
    write_probes = []

    def qualify_writes(wrap, **kw):
        assert wrap([]) == prefix
        assert kw["cwd"] == cwd
        assert '(deny file-write* (subpath ' + json.dumps(str(cwd.parent / "wheelhouse")) + '))' in prefix[2]
        write_probes.append(cwd)
        return {"denied": {"create": True}, "harness_tree_confined": True}

    monkeypatch.setattr(workspace, "qualify_wheelhouse_writes", qualify_writes)
    monkeypatch.setattr(subprocess, "run", run)
    original = [harness, "exec"]
    with bench_network_boundary(
        original, {}, cwd=cwd, harness_id=harness, wheelhouse=cache
    ) as (argv, evidence):
        assert argv == [*prefix, *original]
        assert evidence["wheel_cache_isolation"]["harness_tree_confined"]
        assert evidence["wheel_cache_isolation"]["verifier_copies"]["wheel"]["direct_denied"]
        assert evidence["wheel_cache_isolation"]["cell_wheelhouse"]["denied"]["create"]
    assert copy_probes == [cache]
    assert write_probes == [cwd]


@pytest.mark.parametrize(
    "message",
    [
        "sandbox-exec: sandbox_apply: Operation not permitted",
        "srt: cannot start sandbox inside another sandbox",
        "Sandbox initialization failed: EPERM",
    ],
)
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_nested_sandbox_is_distinct_refusal(
    monkeypatch, tmp_path, probe_environment, message, stream
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 71, message if stream == "stdout" else "", message if stream == "stderr" else ""
        ),
    )
    with pytest.raises(EgressCanaryRefused, match="nested sandbox"):
        with bench_network_boundary(["codex"], {}, cwd=tmp_path, harness_id="codex"):
            pytest.fail("runner never started")


@pytest.fixture
def codex_env(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text("[sandbox_workspace_write]\nnetwork_access = false\n")
    return {"CODEX_HOME": str(home)}


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_probe_uses_cell_runner_and_policy(monkeypatch, tmp_path, harness, codex_env):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv,
            0,
            "Usage: codex sandbox [OPTIONS] [COMMAND]...\n"
            if argv[-1] == "--help"
            else "0.0.78\n"
            if argv[0] == "/installed/srt"
            else "2.1.282 (Claude Code)\n"
            if argv[0] == "claude"
            else "codex-cli 0.159.1\n",
            "",
        ),
    )
    monkeypatch.setattr(workspace.shutil, "which", lambda *a, **kw: "/installed/srt")
    settings = tmp_path / "claude.json"
    network = {
        "allowedDomains": [],
        "deniedDomains": ["*"],
        "strictAllowlist": True,
        "allowLocalBinding": False,
        "allowAllUnixSockets": False,
    }
    settings.write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "failIfUnavailable": True,
                    "allowUnsandboxedCommands": False,
                    "excludedCommands": [],
                    "network": network,
                }
            }
        )
    )
    argv = (
        ["claude", "--settings", str(settings)]
        if harness == "claude-code"
        else ["/installed/codex", "exec", "--sandbox", "read-only"]
    )
    command, evidence = REAL_RUNNER(
        argv, codex_env, cwd=tmp_path, harness_id=harness, scratch=tmp_path
    )
    assert command[-3:] == [sys.executable, "-c", workspace.DIRECT_PROBE]
    if harness == "codex":
        assert evidence["runner_version"] == "codex-cli 0.159.1"
        assert command[:2] == ["/installed/codex", "sandbox"]
        assert "macos" not in command
        assert 'sandbox_mode="read-only"' in command
        assert "sandbox_workspace_write.network_access=false" not in command
        assert evidence["policy"]["sandbox_workspace_write"]["network_access"] is False
    else:
        assert command[:2] == ["/installed/srt", "--settings"]
        assert json.loads(Path(command[2]).read_text())["network"] == network
        assert evidence["policy"]["network"] == network
        assert evidence["runner_version"] == "0.0.78"
        assert evidence["harness_version"] == "2.1.282 (Claude Code)"
        assert evidence["bundled_sandbox_runtime_version"] is None
        assert evidence["runtime_relationship"] == "standalone-srt-not-bundled-claude-runtime"
    assert evidence["harness_tree_confined"] is False
    assert evidence["policy_scope"] == "requested-runner-policy"


def test_missing_claude_runner_refuses(monkeypatch, tmp_path):
    settings = tmp_path / "claude.json"
    settings.write_text(
        '{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false,"excludedCommands":[],"network":{"allowedDomains":[],"deniedDomains":["*"],"strictAllowlist":true,"allowLocalBinding":false,"allowAllUnixSockets":false}}}'
    )
    monkeypatch.setattr(workspace.shutil, "which", lambda *a, **kw: None)
    with pytest.raises(EgressCanaryRefused, match="srt.*unavailable"):
        REAL_RUNNER(
            ["claude", "--settings", str(settings)],
            {},
            cwd=tmp_path,
            harness_id="claude-code",
            scratch=tmp_path,
        )


@pytest.mark.parametrize(
    "usage, expected",
    [
        ("Usage: codex sandbox [OPTIONS] [COMMAND]...", ["codex", "sandbox"]),
        (
            "Usage: codex sandbox [OPTIONS] <COMMAND>\nCommands:\n  macos  Run under Seatbelt",
            ["codex", "sandbox", "macos"],
        ),
        ("Usage: unknown", None),
    ],
)
def test_codex_runner_discovers_usage(monkeypatch, tmp_path, usage, expected, codex_env):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, usage if argv[-1] == "--help" else "codex-cli 0.159.1", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    args = (["codex", "exec", "--sandbox", "workspace-write"], codex_env)
    kwargs = dict(cwd=tmp_path, harness_id="codex", scratch=tmp_path)
    if expected is None:
        with pytest.raises(EgressCanaryRefused, match="usage unavailable or unsupported"):
            REAL_RUNNER(*args, **kwargs)
    else:
        command, evidence = REAL_RUNNER(*args, **kwargs)
        assert command[: len(expected)] == expected
        assert 'sandbox_mode="workspace-write"' in command
        assert calls == [["codex", "sandbox", "--help"], ["codex", "--version"]]
        assert evidence["runner_version"] == "codex-cli 0.159.1"


@pytest.mark.parametrize(
    "config",
    [
        "[sandbox_workspace_write]\nnetwork_access = true\n",
        "[sandbox_workspace_write]\nnetwork_access = 'false'\n",
        "[sandbox_workspace_write]\n",
        "invalid toml [",
    ],
)
def test_codex_probe_rejects_invalid_cell_network_policy(tmp_path, codex_env, config):
    (Path(codex_env["CODEX_HOME"]) / "config.toml").write_text(config)
    with pytest.raises(EgressCanaryRefused, match="invalid Codex cell sandbox policy"):
        REAL_RUNNER(
            ["codex", "exec", "--sandbox", "workspace-write"],
            codex_env,
            cwd=tmp_path,
            harness_id="codex",
            scratch=tmp_path,
        )


@pytest.mark.parametrize(
    "override",
    [
        ["-c", "sandbox_workspace_write.network_access=true"],
        ["--config=sandbox_workspace_write.network_access=true"],
        ["-csandbox_workspace_write.network_access=true"],
        ["--profile", "unsafe"],
    ],
)
def test_codex_probe_rejects_policy_overrides(tmp_path, codex_env, override):
    with pytest.raises(EgressCanaryRefused, match="invalid Codex cell sandbox policy"):
        REAL_RUNNER(
            ["codex", "exec", "--sandbox", "workspace-write", *override],
            codex_env,
            cwd=tmp_path,
            harness_id="codex",
            scratch=tmp_path,
        )


@pytest.mark.parametrize("stage", ["codex-help", "codex-version", "srt-version", "claude-version"])
@pytest.mark.parametrize(
    "error", ["timeout", errno.EAGAIN, errno.EINTR, errno.EIO, errno.ETIMEDOUT]
)
@pytest.mark.parametrize("recovers", [False, True])
def test_runner_discovery_retries_transient_errors(
    monkeypatch, tmp_path, codex_env, stage, error, recovers
):
    delays = []
    calls = []
    failures = []
    monkeypatch.setattr(workspace.time, "sleep", delays.append)
    monkeypatch.setattr(workspace.shutil, "which", lambda *a, **kw: "/installed/srt")
    target = {
        "codex-help": ["codex", "sandbox", "--help"],
        "codex-version": ["codex", "--version"],
        "srt-version": ["/installed/srt", "--version"],
        "claude-version": ["claude", "--version"],
    }[stage]

    def run(argv, **kwargs):
        if argv == target:
            calls.append((argv, kwargs))
            if not recovers or len(calls) < 3:
                failure = (
                    subprocess.TimeoutExpired(argv, kwargs["timeout"])
                    if error == "timeout"
                    else OSError(error, "transient discovery failure")
                )
                failures.append(failure)
                raise failure
        return subprocess.CompletedProcess(
            argv,
            0,
            "Usage: codex sandbox [OPTIONS] [COMMAND]..."
            if argv[-1] == "--help"
            else "0.0.78"
            if argv[0] == "/installed/srt"
            else "2.1.282 (Claude Code)"
            if argv[0] == "claude"
            else "fixture",
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    if stage.startswith("codex"):
        argv = ["codex", "exec", "--sandbox", "workspace-write"]
        harness = "codex"
    else:
        settings = tmp_path / "claude.json"
        settings.write_text(
            json.dumps(
                {
                    "sandbox": {
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
                }
            )
        )
        argv = ["claude", "--settings", str(settings)]
        harness = "claude-code"
    kwargs = dict(cwd=tmp_path, harness_id=harness, scratch=tmp_path)
    if recovers:
        _, evidence = REAL_RUNNER(argv, codex_env, **kwargs)
        assert evidence["runner_version"] == ("fixture" if harness == "codex" else "0.0.78")
    else:
        with pytest.raises(EgressCanaryRefused, match="unavailable") as refusal:
            REAL_RUNNER(argv, codex_env, **kwargs)
        assert refusal.value.__cause__ is failures[-1]
    assert len(calls) == 3
    assert all(call == calls[0] for call in calls)
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize("failure", [errno.ENOENT, errno.EACCES, "exit", "unsupported"])
def test_runner_discovery_does_not_retry_terminal_errors(monkeypatch, tmp_path, codex_env, failure):
    calls = []
    delays = []
    monkeypatch.setattr(workspace.time, "sleep", delays.append)

    def run(argv, **kwargs):
        calls.append(argv)
        if isinstance(failure, int):
            raise OSError(failure, "permanent discovery failure")
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0, "unknown usage", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(EgressCanaryRefused, match="usage unavailable or unsupported"):
        REAL_RUNNER(
            ["codex", "exec", "--sandbox", "workspace-write"],
            codex_env,
            cwd=tmp_path,
            harness_id="codex",
            scratch=tmp_path,
        )
    assert calls == [["codex", "sandbox", "--help"]]
    assert delays == []


@pytest.mark.parametrize("harness,expected", [
    ("claude-code", {("api.anthropic.com", 443)}),
    ("codex", {("api.openai.com", 443), ("chatgpt.com", 443)}),
])
def test_frontier_and_litellm_authorities(monkeypatch, harness, expected):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port))
    ])
    env = {"SEW_LITELLM_BASE_URL": "http://proxy.invalid:4000", "OPENAI_BASE_URL": "https://provider.invalid", "ANTHROPIC_BASE_URL": "https://provider.invalid"}
    assert workspace._harness_endpoints({}, harness)[2] == expected
    assert workspace._harness_endpoints(env, harness, harness_auth="litellm")[2] == {("proxy.invalid", 4000)}
    with pytest.raises(EgressCanaryRefused, match="missing LiteLLM"):
        workspace._harness_endpoints({}, harness, harness_auth="litellm")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_litellm_bridge_rewrites_only_selected_loopback(tmp_path, upstream, host):
    """Exercise the actual bridge with a fake endpoint; no model or harness call."""
    import os
    import signal
    from sew.gap.sandbox import PROXY_BRIDGE

    base = f"http://{host}:{upstream}/proxy"
    home = tmp_path / "codex-home"
    home.mkdir()
    config = home / "config.toml"
    config.write_text('[model_providers.searchlight_litellm]\nbase_url = \"http://localhost:1/stale-bridge/v1\"\n[other]\nbase_url = \"http://other.invalid\"\n')
    records = socket.getaddrinfo("127.0.0.1", upstream, type=socket.SOCK_STREAM)
    code = r'''
import http.client, json, os, urllib.request, urllib.error, socket
from urllib.parse import urlsplit
from pathlib import Path
base = os.environ['SEW_LITELLM_BASE_URL']
assert base == os.environ['ANTHROPIC_BASE_URL']
assert base != os.environ['ORIGINAL_BASE']
assert os.environ['NO_PROXY'] == os.environ['no_proxy'] == 'localhost,127.0.0.1,::1'
text = (Path(os.environ['CODEX_HOME']) / 'config.toml').read_text()
assert base + '/v1' in text
assert 'http://other.invalid' in text
assert 'stale-bridge' not in text
assert urllib.request.urlopen(base + '/v1/messages', timeout=3).read() == b'/proxy/v1/messages'
request = urllib.request.Request(base + '/v1/messages', data=b'fake payload')
assert urllib.request.urlopen(request, timeout=3).read() == b'fake payload'
proxy = urlsplit(os.environ['HTTP_PROXY'])
endpoint = urlsplit(base)
connection = http.client.HTTPConnection(proxy.hostname, proxy.port, timeout=3)
connection.set_tunnel(endpoint.hostname, endpoint.port)
connection.request('GET', '/proxy/v1/responses')
assert connection.getresponse().read() == b'/proxy/v1/responses'
connection.close()
for url in ('http://provider.invalid/', os.environ['OTHER_PORT']):
    # Explicit proxy requests exercise the parent allowlist even when the
    # target is loopback and normal clients would bypass it via NO_PROXY.
    connection = http.client.HTTPConnection(proxy.hostname, proxy.port, timeout=3)
    connection.request('GET', url)
    assert connection.getresponse().status == 403
    connection.close()
with socket.create_connection((proxy.hostname, proxy.port), timeout=3) as connection:
    # A discarded oversized header must not let its tail become a fresh
    # request at the parent proxy. The extra byte exposed the old relay bug.
    oversized = b'GET / HTTP/1.1\r\nX-Padding: '.ljust(65536, b'a') + b'a'
    tail = ('GET ' + os.environ['ORIGINAL_BASE'] + '/header-tail HTTP/1.1\r\nHost: ignored\r\n\r\n').encode()
    connection.sendall(oversized + tail)
    try:
        response = connection.recv(65536)
    except ConnectionResetError:
        response = b''
    assert response == b'', response
print('contained', flush=True)
'''
    with endpoint_proxy({(urlsplit(base).hostname, upstream): records}) as proxy:
        # A TCP stand-in for the Unix transport makes the bridge logic portable
        # under callers that deny Unix sockets. The real namespace test uses Unix.
        proxy_port = urlsplit(proxy).port
        bootstrap = f"""
import socket
original_socket = socket.socket
class FakeUnixSocket(original_socket):
    def __init__(self, family=socket.AF_INET, *args, **kwargs):
        self.is_unix = family == socket.AF_UNIX
        super().__init__(socket.AF_INET if self.is_unix else family, *args, **kwargs)
    def connect(self, address):
        return super().connect(('127.0.0.1', {proxy_port}) if self.is_unix else address)
socket.socket = FakeUnixSocket
exec({PROXY_BRIDGE!r})
"""
        env = {**os.environ, "SEW_GAP_LITELLM_BASE_URL": base, "SEW_LITELLM_BASE_URL": base,
               "ANTHROPIC_BASE_URL": base, "CODEX_HOME": str(home), "ORIGINAL_BASE": base,
               "OTHER_PORT": f"http://{host}:{upstream + 1}/", "NO_PROXY": "*"}
        process = subprocess.Popen([sys.executable, "-I", "-c", bootstrap, "fake-unix-transport", sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert stdout.strip() == "contained"
        finally:
            os.killpg(process.pid, signal.SIGTERM)


@pytest.mark.parametrize("status,accepted", [
    ("200 Connection Established", True),
    ("200 OK", True),
    ("200 ", True),
    ("403 Connection Established", False),
    ("2000 OK", False),
])
def test_litellm_tls_bridge_connect_status(tmp_path, status, accepted):
    from sew.gap.sandbox import PROXY_BRIDGE
    from sew.gap.verify import _execute

    payload = b"\x16fake TLS handshake"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            requests.append(self.path)
            self.connection.settimeout(3)
            self.wfile.write(f"HTTP/1.1 {status}\r\n\r\n".encode())
            self.wfile.flush()
            if accepted:
                data = self.rfile.read(len(payload))
                if data:
                    self.wfile.write(data)

        def log_message(self, *args):
            pass

    # As in the loopback routing test, use TCP for the private Unix transport
    # so the actual bridge can run on hosts where Unix sockets are unavailable.
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bootstrap = f"""
import socket
original_socket = socket.socket
class FakeUnixSocket(original_socket):
    def __init__(self, family=socket.AF_INET, *args, **kwargs):
        self.is_unix = family == socket.AF_UNIX
        super().__init__(socket.AF_INET if self.is_unix else family, *args, **kwargs)
    def connect(self, address):
        return super().connect(('127.0.0.1', {server.server_port}) if self.is_unix else address)
socket.socket = FakeUnixSocket
exec({PROXY_BRIDGE!r})
"""
        code = f"""
import os, socket
from urllib.parse import urlsplit
target = urlsplit(os.environ['SEW_LITELLM_BASE_URL'])
with socket.create_connection((target.hostname, target.port), timeout=3) as client:
    client.sendall({payload!r})
    client.shutdown(socket.SHUT_WR)
    received = b''
    try:
        while chunk := client.recv(4096):
            received += chunk
    except ConnectionResetError:
        assert {not accepted!r}
assert received == {payload if accepted else b''!r}, received
"""
        base = "https://127.0.0.1:4000"
        try:
            result = _execute(
                [sys.executable, "-I", "-c", bootstrap, "fake-unix-transport",
                 sys.executable, "-I", "-c", code],
                tmp_path,
                {"SEW_GAP_LITELLM_BASE_URL": base, "SEW_LITELLM_BASE_URL": base},
                5,
            )
            assert result["exit_code"] == 0, result
            assert requests == ["127.0.0.1:4000"]
        finally:
            server.shutdown()
            thread.join()
