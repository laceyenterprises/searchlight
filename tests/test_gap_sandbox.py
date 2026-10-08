"""Portable sandbox admission and real backend qualification."""

import errno
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from sew.gap import sandbox
from sew.gap.workspace import EgressCanaryRefused


@pytest.mark.parametrize(
    "platform,name,exe",
    [("darwin", "seatbelt", "/usr/bin/sandbox-exec"), ("linux", "bubblewrap", "/usr/bin/bwrap")],
)
@pytest.mark.parametrize("configured", ["auto", "explicit"])
def test_selection(tmp_path, monkeypatch, platform, name, exe, configured):
    config = tmp_path / "sew.yaml"
    config.write_text("sandbox: " + (name if configured == "explicit" else "auto"))
    monkeypatch.setattr(Path, "is_file", lambda p: True)
    backend = sandbox.select_backend({"SEW_CONFIG": str(config)}, platform=platform)
    assert (backend.name, backend.executable) == (name, exe)


@pytest.mark.parametrize(
    "platform,setting",
    [("win32", "auto"), ("linux", "seatbelt"), ("darwin", "bubblewrap"), ("linux", "none")],
)
def test_selection_refuses(tmp_path, platform, setting):
    config = tmp_path / "sew.yaml"
    config.write_text("sandbox: " + setting)
    with pytest.raises(EgressCanaryRefused):
        sandbox.select_backend({"SEW_CONFIG": str(config)}, platform=platform)


def test_missing_backend(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda p: False)
    with pytest.raises(EgressCanaryRefused, match="bubblewrap unavailable"):
        sandbox.select_backend({"PATH": ""}, platform="linux")


def evidence(backend):
    pip_output = (Path(__file__).parent / "fixtures/sandbox" / f"{backend}-pip.txt").read_text()
    number = errno.EPERM if backend == "seatbelt" else errno.ENETUNREACH
    return {
        "nonce": "n",
        "interfaces": ["lo"],
        "probes": {
            "curl": {
                "exit_code": 7,
                "error": "curl: (7) Failed to connect to 1.1.1.1 port 443: Couldn't connect to server",
            },
            "pip": {"exit_code": 1, "error": pip_output},
            "tcp": {"denied": True, "errno": number},
            "dns": {"denied": True, "errno": number},
        },
    }


@pytest.mark.parametrize("name", ["seatbelt", "bubblewrap"])
def test_parse_denials(name):
    record = sandbox.parse_backend_canary(
        json.dumps(evidence(name)), "n", sandbox.SandboxBackend(name, "exe")
    )
    assert record["admissible"] and record["backend"] == name


@pytest.mark.parametrize("name", ["seatbelt", "bubblewrap"])
@pytest.mark.parametrize(
    "bad",
    ["success", "timeout", "missing", "nonce", "tool-missing", "pip-quiet", "dns", "bool", "json"],
)
def test_parse_refuses_unproven_denials(name, bad):
    record = evidence(name)
    if bad == "success":
        record["probes"]["curl"]["exit_code"] = 0
    if bad == "timeout":
        record["probes"]["tcp"]["errno"] = errno.ETIMEDOUT
    if bad == "missing":
        del record["probes"]["pip"]
    if bad == "nonce":
        record["nonce"] = "other"
    if bad == "tool-missing":
        record["probes"]["pip"]["error"] = "No module named pip"
    if bad == "pip-quiet":
        record["probes"]["pip"]["error"] = (
            "Looking in indexes: https://1.1.1.1/simple\n"
            "ERROR: Could not find a version that satisfies the requirement pip (from versions: none)\n"
            "ERROR: No matching distribution found for pip\n"
        )
    if bad == "dns":
        record["probes"]["dns"]["denied"] = False
    if bad == "bool":
        record["probes"]["tcp"]["errno"] = True
    with pytest.raises(EgressCanaryRefused):
        sandbox.parse_backend_canary(
            "invalid" if bad == "json" else json.dumps(record),
            "n",
            sandbox.SandboxBackend(name, "exe"),
        )


def test_linux_routing_error_does_not_qualify_seatbelt():
    with pytest.raises(EgressCanaryRefused):
        sandbox.parse_backend_canary(
            json.dumps(evidence("bubblewrap")), "n", sandbox.SandboxBackend("seatbelt", "exe")
        )


def test_mount_policy(tmp_path):
    wheels = tmp_path / "wheelhouse"
    wheels.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")
    argv = backend.command(["python", "job"], tmp_path, write_roots=[workspace])
    assert "--unshare-net" in argv and "--share-net" not in argv
    assert argv[argv.index("--bind") : argv.index("--bind") + 3] == [
        "--bind",
        str(workspace),
        str(workspace),
    ]
    assert argv[argv.index(str(wheels)) - 1] == "--ro-bind"
    assert argv[-3:] == ["--", "python", "job"]
    assert "--proc" in argv and "--cap-drop" in argv
    with pytest.raises(EgressCanaryRefused, match="outside scratch"):
        backend.command(["job"], tmp_path, write_roots=[tmp_path.parent])
    with pytest.raises(EgressCanaryRefused, match="cannot expose host root"):
        backend.command(["job"], tmp_path, read_roots=["/"])


def test_qualification_refuses_failed_process(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "namespace unavailable"),
    )
    with pytest.raises(EgressCanaryRefused, match="namespace unavailable"):
        sandbox.qualify_backend(sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"), tmp_path, {})


@pytest.mark.parametrize("name", ["seatbelt", "bubblewrap"])
def test_real_backend_canary(tmp_path, name, require_containment):
    expected = "darwin" if name == "seatbelt" else "linux"
    if sys.platform != expected:
        pytest.skip("backend requires " + expected)
    require_containment(name)
    backend = sandbox.select_backend()
    record = sandbox.qualify_backend(
        backend,
        tmp_path,
        os.environ,
        profile="(version 1)(allow default)(deny network-outbound)",
    )
    assert record["admissible"] and record["probes"]["dns"]["denied"]
    if name == "bubblewrap":
        wheels = tmp_path / "wheelhouse"
        wheels.mkdir()
        (wheels / "pin.whl").write_text("immutable")
        sentinel = tmp_path.parent / "host-secret"
        sentinel.write_text("private")
        code = (
            "from pathlib import Path; "
            f"assert not Path({str(sentinel)!r}).exists(); "
            'Path("allowed").write_text("ok"); '
            f'Path({str(wheels / "pin.whl")!r}).write_text("changed")'
        )
        result = subprocess.run(
            backend.command([sys.executable, "-c", code], tmp_path),
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and "Read-only file system" in result.stderr
        assert (tmp_path / "allowed").read_text() == "ok"
        assert (wheels / "pin.whl").read_text() == "immutable"


def test_doctor_reports_backend_verdict(tmp_path, monkeypatch):
    from sew.doctor import doctor

    monkeypatch.setattr(
        sandbox, "select_backend", lambda env: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")
    )
    monkeypatch.setattr(sandbox, "qualify_backend", lambda *a, **kw: {"admissible": True})
    output = doctor({"SEW_MODE": "standalone", "HOME": str(tmp_path), "PATH": ""})
    assert "bubblewrap — egress canary denied" in output

    def refuse(*a, **kw):
        raise EgressCanaryRefused("GAP refused: DNS denial unproven")

    monkeypatch.setattr(sandbox, "qualify_backend", refuse)
    assert "DNS denial unproven" in doctor(
        {"SEW_MODE": "standalone", "HOME": str(tmp_path), "PATH": ""}
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Unix bridge is the Linux namespace transport")
def test_unix_endpoint_proxy_and_bridge(tmp_path):
    with tempfile.TemporaryDirectory(
        dir=os.environ.get("TMPDIR", "/tmp"), prefix="swx-"
    ) as directory:
        _check_bridge(Path(directory), tmp_path)


def _check_bridge(directory, tmp_path):
    from sew.gap.endpoint_proxy import endpoint_proxy
    from sew.gap.verify import _execute

    path = directory / "proxy.sock"
    with endpoint_proxy({}, socket_path=path):
        code = "import urllib.request; urllib.request.urlopen('https://example.org', timeout=3)"
        result = _execute(
            [sys.executable, "-c", sandbox.PROXY_BRIDGE, str(path), sys.executable, "-c", code],
            tmp_path,
            {"PATH": os.defpath},
            5,
        )
        assert result["exit_code"] != 0
        assert "403 Permission denied: endpoint not allowlisted" in result["output"]


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
@pytest.mark.parametrize("userinfo", ["", "user:p%40ss@"])
@pytest.mark.parametrize("has_config", [False, True])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_litellm_bridge_preserves_local_http_and_config(tmp_path, host, userinfo, has_config, scheme):
    import base64
    import socket
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from sew.gap.endpoint_proxy import endpoint_proxy
    from sew.gap.verify import _execute

    cert = tmp_path / "cert.pem"
    if scheme == "https":
        import shutil

        openssl = shutil.which("openssl")
        if not openssl:
            pytest.skip("TLS stub requires openssl")
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-days", "1", "-subj", "/CN=localhost",
             "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
             "-keyout", str(tmp_path / "key.pem"), "-out", str(cert)],
            check=True, capture_output=True, timeout=5,
        )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/v1?probe=1"
            if scheme == "http":
                assert self.headers["Host"] == f"{host}:{self.server.server_port}"
            else:
                # TLS application bytes remain encrypted through both relays.
                from urllib.parse import urlsplit

                assert urlsplit("//" + self.headers["Host"]).hostname == host.strip("[]")
            expected = "Basic " + base64.b64encode(b"user:p@ss").decode() if userinfo else None
            assert self.headers.get("Authorization") == expected
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"model transport works")

        def log_message(self, *args):
            pass

    config = tmp_path / "config.toml"
    if has_config:
        config.write_text(
            '[model_providers.searchlight_litellm]\nbase_url = "http://stale.invalid/v1"\n'
            '[model_providers.other]\nbase_url = "http://other.invalid/v1"\n'
        )
    code = r"""
import os, socket, subprocess, threading, tomllib, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
assert os.environ['NO_PROXY'] == os.environ['no_proxy'] == 'localhost,127.0.0.1,::1'
base = os.environ['SEW_LITELLM_BASE_URL']
assert os.environ['ANTHROPIC_BASE_URL'] == os.environ['OPENAI_BASE_URL'] == base
assert 'SEW_GAP_LITELLM_BASE_URL' not in os.environ
target = urlsplit(base)
assert target.hostname in ('localhost', '127.0.0.1', '::1')
config = Path(os.environ['CODEX_HOME']) / 'config.toml'
if config.exists():
    providers = tomllib.loads(config.read_text())['model_providers']
    assert providers['searchlight_litellm']['base_url'] == base + '/v1'
    assert providers['other']['base_url'] == 'http://other.invalid/v1'
curl = ['curl', '--disable', '--fail', '--silent', '--show-error', '--max-time', '3']
if target.scheme == 'https':
    rejected = subprocess.run(curl + [base + '/v1?probe=1'], capture_output=True)
    assert rejected.returncode == 60, rejected.stderr
    curl += ['--cacert', os.environ['TEST_TLS_CERT']]
result = subprocess.run(curl + [base + '/v1?probe=1'], capture_output=True)
assert result.returncode == 0, result.stderr
assert result.stdout == b'model transport works'
for denied in ('http://provider.invalid/',
               urlunsplit(('http', 'provider.invalid:1', '/', '', ''))):
    try:
        urllib.request.urlopen(denied, timeout=3)
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    else:
        raise AssertionError('non-allowlisted endpoint reachable')
# CONNECT uses the same exact host/port translation, without URL credentials.
proxy = urlsplit(os.environ['HTTPS_PROXY'])
with socket.create_connection((proxy.hostname, proxy.port), timeout=3) as connection:
    authority = target.netloc.rsplit('@', 1)[-1]
    connection.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
    assert connection.recv(4096).startswith(b'HTTP/1.1 200 Connection Established')
class LocalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'local development works')
    def log_message(self, *args):
        pass
with ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler) as server:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for host in ('localhost', '127.0.0.1'):
            assert urllib.request.urlopen(f'http://{host}:{server.server_port}', timeout=3).read() == b'local development works'
    finally:
        server.shutdown()
        thread.join()
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        if scheme == "https":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, tmp_path / "key.pem")
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Freeze the test endpoint to this local stub, including IPv6 aliases.
            endpoints = {(host.strip("[]"), server.server_port): socket.getaddrinfo(
                "127.0.0.1", server.server_port, type=socket.SOCK_STREAM
            )}
            base = f"{scheme}://{userinfo}{host}:{server.server_port}/api"
            env = {key: base for key in (
                "SEW_GAP_LITELLM_BASE_URL", "SEW_LITELLM_BASE_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"
            )}
            env.update(PATH=os.defpath, CODEX_HOME=str(tmp_path))
            if scheme == "https":
                env["TEST_TLS_CERT"] = str(cert)
            # Keep Unix socket paths short on both macOS and Linux.
            with tempfile.TemporaryDirectory(prefix="swx-", dir="/tmp") as directory:
                path = Path(directory) / "proxy.sock"
                with endpoint_proxy(endpoints, socket_path=path):
                    # Qualification and cell launches must each refresh stale config.
                    for _ in range(2 if has_config else 1):
                        result = _execute(
                            [sys.executable, "-c", sandbox.PROXY_BRIDGE, str(path), sys.executable, "-c", code],
                            tmp_path, env, 10,
                        )
                        assert result["exit_code"] == 0, result
            assert config.exists() == has_config
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.skipif(sys.platform != "linux", reason="requires a real Linux namespace")
@pytest.mark.parametrize("oss_model", [False, True])
def test_real_namespace_endpoint_bridge(tmp_path, require_containment, oss_model):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from sew.gap.workspace import bench_network_boundary
    from sew.gap.verify import _execute

    require_containment("bubblewrap")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"model transport works")

        def log_message(self, *args):
            pass

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    workspace = scratch / "workspace"
    workspace.mkdir()
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}", "PATH": os.defpath}
            code = (
                "import urllib.request, socket; "
                f"assert urllib.request.urlopen('http://127.0.0.1:{server.server_port}', timeout=3).read() == b'model transport works'"
            )
            if oss_model:
                env["SEW_LITELLM_BASE_URL"] = env["OPENAI_BASE_URL"]
                code = (
                    "import os, urllib.request, urllib.error, socket; "
                    "assert os.environ['NO_PROXY'] == 'localhost,127.0.0.1,::1'; "
                    "assert urllib.request.urlopen(os.environ['SEW_LITELLM_BASE_URL'], timeout=3).read() == b'model transport works'; "
                    "\ntry: urllib.request.urlopen('http://provider.invalid/', timeout=3)"
                    "\nexcept urllib.error.HTTPError as exc: assert exc.code == 403"
                    "\nelse: raise AssertionError('provider reachable')"
                    f"\ntry: socket.create_connection(('127.0.0.1', {server.server_port}), timeout=1)"
                    "\nexcept OSError: pass"
                    "\nelse: raise AssertionError('host loopback reachable')"
                )
            else:
                # Preserve the frontier behavior while exercising its bridge.
                code = "import os; os.environ['NO_PROXY'] = os.environ['no_proxy'] = ''; " + code
            with bench_network_boundary(
                [sys.executable, "-c", code], env, cwd=workspace, harness_id="codex",
                harness_auth="litellm" if oss_model else "account"
            ) as (argv, evidence):
                assert evidence["admissible"]
                result = _execute(argv, workspace, env, 10)
                assert result["exit_code"] == 0, result
        finally:
            server.shutdown()
            thread.join()


def test_seatbelt_command_is_unchanged(tmp_path):
    backend = sandbox.SandboxBackend("seatbelt", "/usr/bin/sandbox-exec")
    assert backend.command(["job"], tmp_path, profile="original") == [
        "/usr/bin/sandbox-exec",
        "-p",
        "original",
        "job",
    ]
    with pytest.raises(EgressCanaryRefused, match="missing Seatbelt profile"):
        backend.command(["job"], tmp_path)


def test_verifier_routes_linux_to_backend(tmp_path, monkeypatch):
    import importlib

    verifier = importlib.import_module("sew.gap.verify")
    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"),
    )
    command = verifier._sandbox_command(["job"], tmp_path, write_roots=[])
    assert command[0] == "/bin/bwrap" and "--unshare-net" in command
    assert "--bind" not in command
    assert command[-2:] == ["--", "job"]


@pytest.mark.skipif(sys.platform != "linux", reason="requires real bubblewrap verification")
def test_real_linux_verifier_applies_and_grades_patch(
    tmp_path, monkeypatch, require_containment
):
    from sew.gap.verify import verify
    from sew.gap.workspace import capture_diff
    import shutil

    require_containment("bubblewrap")
    ambient_config = tmp_path / "ambient.yaml"
    ambient_config.write_text("sandbox: seatbelt\n")
    source_config = tmp_path / "source.yaml"
    source_config.write_text("sandbox: bubblewrap\n")
    monkeypatch.setenv("SEW_CONFIG", str(ambient_config))
    source_env = {"SEW_CONFIG": str(source_config), "PATH": os.defpath}
    root = tmp_path / "module"
    fixture = root / "catalogs/gap/fixture"
    hidden = root / "catalogs/gap/hidden"
    fixture.mkdir(parents=True)
    hidden.mkdir()
    (fixture / "app.py").write_text("def value(): return 1\n")
    (fixture / "test_visible.py").write_text(
        "from app import value\ndef test_visible(): assert value() > 0\n"
    )
    (hidden / "test_hidden.py").write_text(
        "from app import value\ndef test_hidden(): assert value() == 2\n"
    )
    task = {
        "task_type": "code",
        "fixture": "fixture",
        "hidden": "hidden",
        "packages": [],
        "verifier": {
            "timeout_seconds": 10,
            "visible_commands": ["python -m pytest test_visible.py"],
            "hidden_commands": ["python -m pytest hidden"],
        },
    }
    agent = tmp_path / "agent"
    shutil.copytree(fixture, agent)
    patch = tmp_path / "patch.diff"
    capture_diff(fixture, agent, patch)
    failed = verify(task, patch, root=root, environ=source_env)
    assert failed["outcome"] == "fail" and failed["escaped_defects"] == 1, failed
    (agent / "app.py").write_text("def value(): return 2\n")
    capture_diff(fixture, agent, patch)
    passed = verify(task, patch, root=root, environ=source_env)
    assert passed["outcome"] == "pass", json.dumps(passed)


def test_linux_boundary_exposes_native_binary_without_its_user_data(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from sew.gap import workspace, endpoint_proxy

    binary = tmp_path / "home/.local/bin/harness"
    binary.parent.mkdir(parents=True)
    binary.write_text("native executable")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    cwd = scratch / "workspace"
    cwd.mkdir()
    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"),
    )
    monkeypatch.setattr(sandbox, "qualify_backend", lambda *a, **kw: {"admissible": True})
    monkeypatch.setattr(
        workspace.shutil, "which", lambda name, **kw: None if name == "node" else str(binary)
    )
    monkeypatch.setattr(
        workspace.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("192.0.2.10", 443))]
    )
    monkeypatch.setattr(endpoint_proxy, "endpoint_proxy", lambda *a, **kw: nullcontext())
    with workspace.bench_network_boundary([str(binary)], {}, cwd=cwd, harness_id="codex") as (
        argv,
        evidence,
    ):
        assert evidence["admissible"]
        assert str(binary) in argv
        assert str(binary.parent.parent) not in argv
        assert str(binary.parent) not in argv


def test_tmpfs_precedes_all_tmp_binds(tmp_path):
    backend = sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")
    argv = backend.command(["job"], tmp_path, read_roots=["/tmp/proxy/endpoint.sock", "/tmp/cli"])
    tmpfs = argv.index("--tmpfs")
    for index, arg in enumerate(argv):
        if arg in {"--ro-bind", "--bind"} and Path(argv[index + 2]).is_relative_to("/tmp"):
            assert index > tmpfs


@pytest.mark.parametrize("name", ["_bench_network_boundary", "_bubblewrap_boundary"])
def test_boundary_selects_from_source_environment(tmp_path, monkeypatch, name):
    from sew.gap import workspace

    source = {"SEW_CONFIG": str(tmp_path / "sew.yaml")}

    def select(env):
        assert env is source
        raise EgressCanaryRefused("selected source environment")

    monkeypatch.setattr(sandbox, "select_backend", select)
    with pytest.raises(EgressCanaryRefused, match="selected source environment"):
        with getattr(workspace, name)(
            ["job"], {}, cwd=tmp_path, harness_id="codex", environ=source
        ):
            pytest.fail("should refuse")


@pytest.mark.parametrize(
    "auth,token", [(None, "token"), ("account", "token"), ("broker", None), ("broker", "")]
)
def test_linux_claude_refuses_refreshable_account_auth(tmp_path, monkeypatch, auth, token):
    from sew.gap import workspace

    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"),
    )
    monkeypatch.setattr(
        workspace,
        "_harness_endpoints",
        lambda *a: pytest.fail("auth refusal must precede network setup or any subprocess"),
    )
    account = tmp_path / "account"
    account.mkdir()
    login = account / ".credentials.json"
    login.write_text("host refresh token")
    scratch = tmp_path / "scratch"
    cwd = scratch / "workspace"
    cwd.mkdir(parents=True)
    env = {"HOME": str(account), "CLAUDE_CONFIG_DIR": str(account)}
    if token is not None:
        env["ANTHROPIC_AUTH_TOKEN"] = token
    receipt = scratch / "refusal.json"
    options = {} if auth is None else {"harness_auth": auth}
    with pytest.raises(EgressCanaryRefused, match="require broker OAuth"):
        with workspace.bench_network_boundary(
            ["claude"], env, cwd=cwd, harness_id="claude-code", refusal_path=receipt, **options
        ):
            pytest.fail("account auth must not spawn")
    assert login.read_text() == "host refresh token"
    assert not list(scratch.rglob(".credentials.json"))
    record = json.loads(receipt.read_text())
    assert not record["admissible"] and "require broker OAuth" in record["reason"]


def test_linux_claude_broker_uses_empty_private_config(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from sew.gap import workspace, endpoint_proxy

    monkeypatch.setattr(
        sandbox,
        "select_backend",
        lambda env=None: sandbox.SandboxBackend("bubblewrap", "/bin/bwrap"),
    )
    monkeypatch.setattr(sandbox, "qualify_backend", lambda *a, **kw: {"admissible": True})
    monkeypatch.setattr(workspace.shutil, "which", lambda name, **kw: sys.executable)
    monkeypatch.setattr(
        workspace.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("192.0.2.10", 443))]
    )
    monkeypatch.setattr(endpoint_proxy, "endpoint_proxy", lambda *a, **kw: nullcontext())
    account = tmp_path / "account"
    account.mkdir()
    for name in (".credentials.json", ".claude.json", "host-session"):
        (account / name).write_text("host private state")
    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)
    env = {
        "HOME": str(account),
        "CLAUDE_CONFIG_DIR": str(account),
        "ANTHROPIC_AUTH_TOKEN": "broker access token",
    }
    with workspace.bench_network_boundary(
        ["claude"], env, cwd=cwd, harness_id="claude-code", harness_auth="broker"
    ) as (argv, evidence):
        assert evidence["admissible"]
        assert str(account) not in argv
        config = Path(env["CLAUDE_CONFIG_DIR"])
        assert config.is_relative_to(cwd.parent)
        assert list(config.iterdir()) == []
        (config / ".credentials.json").write_text("sandbox state")
        assert env["ANTHROPIC_AUTH_TOKEN"] == "broker access token"
    assert (account / ".credentials.json").read_text() == "host private state"


def test_verifier_selects_from_command_environment(tmp_path, monkeypatch):
    import importlib

    verifier = importlib.import_module("sew.gap.verify")
    env = {"SEW_CONFIG": str(tmp_path / "sew.yaml")}

    def select(source):
        assert source is env
        return sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")

    monkeypatch.setattr(sandbox, "select_backend", select)
    argv = verifier._sandbox_command(["job"], tmp_path, env=env)
    assert argv[0] == "/bin/bwrap"


@pytest.mark.parametrize("error", [OSError("socket bind failed"), ValueError("invalid setup")])
def test_boundary_retains_setup_refusal_reason(tmp_path, monkeypatch, error):
    from contextlib import contextmanager
    from sew.gap import workspace

    @contextmanager
    def fail(*args, **kwargs):
        raise error
        yield

    monkeypatch.setattr(workspace, "_bench_network_boundary", fail)
    receipt = tmp_path / "refusal.json"
    with pytest.raises(type(error), match=str(error)):
        with workspace.bench_network_boundary(
            ["job"], {}, cwd=tmp_path, harness_id="codex", refusal_path=receipt
        ):
            pytest.fail("should refuse")
    record = json.loads(receipt.read_text())
    assert record["reason"] == str(error) and not record["admissible"]


def test_doctor_reports_scratch_failure(tmp_path, monkeypatch):
    import importlib

    diagnostics = importlib.import_module("sew.doctor")
    monkeypatch.setattr(
        sandbox, "select_backend", lambda env: sandbox.SandboxBackend("seatbelt", "exe")
    )

    def fail(*args, **kwargs):
        raise OSError("scratch unavailable")

    monkeypatch.setattr(diagnostics.tempfile, "TemporaryDirectory", fail)
    assert "inadmissible (scratch unavailable)" in diagnostics.doctor(
        {"SEW_MODE": "standalone", "HOME": str(tmp_path), "PATH": ""}
    )


@pytest.mark.skipif(sys.platform != "linux", reason="requires real bubblewrap")
@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_real_namespace_writable_auth_copy(tmp_path, harness, require_containment):
    from sew.gap.workspace import bench_network_boundary
    from sew.gap.verify import _execute

    require_containment("bubblewrap")
    account = tmp_path / "account"
    account.mkdir()
    config = account / "config"
    config.mkdir()
    credential = ".credentials.json" if harness == "claude-code" else "auth.json"
    (config / credential).write_text("login")
    (config / "host-session").write_text("private")
    (account / ".claude.json").write_text("account settings")
    scratch = tmp_path / "scratch"
    cwd = scratch / "workspace"
    cwd.mkdir(parents=True)
    key = "CLAUDE_CONFIG_DIR" if harness == "claude-code" else "CODEX_HOME"
    env = {
        "PATH": os.defpath,
        "HOME": str(account),
        key: str(config),
        "OPENAI_BASE_URL": "http://127.0.0.1:12345",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:12345",
    }
    if harness == "claude-code":
        env["ANTHROPIC_AUTH_TOKEN"] = "fixture broker access token"
    code = (
        "import os; from pathlib import Path; "
        f"p = Path(os.environ[{key!r}]); "
        f"assert not (p / {credential!r}).exists(); "
        "assert not (p / '.claude.json').exists(); "
        "assert not Path(os.environ['HOME'], '.claude.json').exists(); "
        "assert not (p / 'host-session').exists(); "
        "(p / 'session').write_text('writable'); "
        f"(p / {credential!r}).write_text('refreshed'); "
        "(p / '.claude.json').write_text('updated')"
        if harness == "claude-code"
        else "import os; from pathlib import Path; "
        "p = Path(os.environ['CODEX_HOME']); "
        "assert (p / 'auth.json').read_text() == 'login'; "
        "assert not (p / 'host-session').exists(); "
        "(p / 'session').write_text('writable'); "
        "(p / 'auth.json').write_text('refreshed')"
    )
    with bench_network_boundary(
        [sys.executable, "-c", code], env, cwd=cwd, harness_id=harness, harness_auth="broker"
    ) as (
        argv,
        evidence,
    ):
        assert evidence["admissible"]
        record = _execute(argv, cwd, env, 10)
        assert record["exit_code"] == 0, record
        assert Path(env[key]).is_relative_to(scratch)
    assert (config / credential).read_text() == "login"
    assert (account / ".claude.json").read_text() == "account settings"
    assert not (config / "session").exists()


def test_namespace_evidence_required():
    record = evidence("bubblewrap")
    record["interfaces"] = ["lo", "eth0"]
    with pytest.raises(EgressCanaryRefused):
        sandbox.parse_backend_canary(
            json.dumps(record), "n", sandbox.SandboxBackend("bubblewrap", "exe")
        )


def test_verifier_cache_mask_follows_runtime_binds(tmp_path):
    cache = tmp_path / "runtime/verifier-cache"
    cache.mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    backend = sandbox.SandboxBackend("bubblewrap", "bwrap")
    argv = backend.command(
        ["job"], scratch, read_roots=[cache.parent], deny_read_roots=[cache]
    )
    assert argv[-6:] == ["--tmpfs", str(cache), "--remount-ro", str(cache), "--", "job"]
    with pytest.raises(EgressCanaryRefused, match="overlaps cell scratch"):
        backend.command(["job"], scratch, deny_read_roots=[tmp_path])


@pytest.mark.parametrize("name", ["seatbelt", "bubblewrap"])
def test_real_verifier_cache_read_boundary(tmp_path, name, require_containment):
    from sew.gap import workspace

    if sys.platform != ("darwin" if name == "seatbelt" else "linux"):
        pytest.skip("requires " + name)
    require_containment(name)
    backend = sandbox.select_backend()
    cache = tmp_path / "runtime/verifier-cache"
    cache.mkdir(parents=True)
    wheel = cache / "stripe-16.0.0.whl"
    wheel.write_bytes(b"verifier-only source")
    scratch = tmp_path / "scratch"
    cwd = scratch / "workspace"
    cwd.mkdir(parents=True)
    (scratch / "wheelhouse").mkdir()
    old = scratch / "wheelhouse/stripe-15.0.0.whl"
    old.write_bytes(b"allowed old source")
    profile = workspace.cache_read_profile(cache, scratch)

    def wrap(command):
        return backend.command(
            command, scratch, profile=profile, read_roots=[cache.parent], deny_read_roots=[cache]
        )

    # Check Python non-shell reads through a descendant, plus find/unzip discovery.
    import zipfile

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("stripe/api.py", "print('verifier-only source')")
    evidence = workspace.qualify_cache_reads(
        wrap, cache, cwd=cwd, env=os.environ, backend=backend
    )
    assert evidence["direct_denied"] and evidence["discovery_denied"]
    if name == "seatbelt":
        write_evidence = workspace.qualify_wheelhouse_writes(
            wrap, cwd=cwd, env=os.environ, backend=backend
        )
        assert all(write_evidence["denied"].values())
        assert not list((scratch / "wheelhouse").glob(".sew-write-probe-*"))
    code = (
        "import subprocess, sys; from pathlib import Path; "
        f"assert Path({str(old)!r}).read_bytes() == b'allowed old source'; "
        f"p = subprocess.run([sys.executable, '-c', {('from pathlib import Path; Path(' + repr(str(wheel)) + ').read_bytes()')!r}], capture_output=True); "
        "assert p.returncode != 0"
    )
    result = subprocess.run(wrap([sys.executable, "-I", "-c", code]), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        wrap(["find", str(cache.parent), "-name", wheel.name, "-exec", "unzip", "-p", "{}", "*.py", ";"]),
        capture_output=True, text=True,
    )
    assert "verifier-only source" not in result.stdout
    if name == "seatbelt":
        for path in (cache, cache.parent):
            result = subprocess.run(
                wrap(["mv", str(path), str(tmp_path / "escaped")]), capture_output=True, text=True
            )
            assert result.returncode != 0
        assert wheel.exists()

        # A non-shell descendant cannot insert the HTML source that would let
        # a suppressed remote attempt hide behind a final local pip success.
        links = scratch / "wheelhouse/links.html"
        insertion = (
            "from pathlib import Path; "
            f"Path({str(links)!r}).write_text("
            "'<a href=\"https://example.org/sew_wheel_probe-2.0-py3-none-any.whl\">new</a>')"
        )
        result = subprocess.run(
            wrap([sys.executable, "-I", "-c",
                  "import subprocess, sys; "
                  f"sys.exit(subprocess.run([sys.executable, '-I', '-c', {insertion!r}]).returncode)"]),
            capture_output=True, text=True, timeout=12,
        )
        assert result.returncode != 0 and not links.exists()
        assert "PermissionError" in result.stderr
        with zipfile.ZipFile(scratch / "wheelhouse/sew_wheel_probe-1.0-py3-none-any.whl", "w") as archive:
            archive.writestr("sew_wheel_probe-1.0.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: sew-wheel-probe\nVersion: 1.0\n")
            archive.writestr("sew_wheel_probe-1.0.dist-info/WHEEL",
                             "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("sew_wheel_probe-1.0.dist-info/RECORD", "")
        command = (
            f"{sys.executable} -m pip download sew-wheel-probe==2.0 --no-deps --retries 0 --timeout 1"
            " >/dev/null 2>&1; "
            f"{sys.executable} -m pip download sew-wheel-probe==1.0 --no-deps --retries 0 --timeout 1"
            " 2>&1 | tail -1"
        )
        cell_env = {"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull,
                    "PIP_FIND_LINKS": str(scratch / "wheelhouse"),
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        result = subprocess.run(
            wrap(["/bin/sh", "-c", command]), cwd=cwd,
            env={**{k: v for k, v in os.environ.items() if not k.startswith("PIP_")}, **cell_env},
            capture_output=True, text=True, timeout=12,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "Successfully downloaded sew-wheel-probe"
        assert (cwd / "sew_wheel_probe-1.0-py3-none-any.whl").exists()
        assert not (cwd / "sew_wheel_probe-2.0-py3-none-any.whl").exists()


def test_writable_cell_wheelhouse_cannot_qualify(tmp_path):
    from sew.gap import workspace

    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)
    wheels = cwd.parent / "wheelhouse"
    wheels.mkdir()
    with pytest.raises(EgressCanaryRefused, match="wheelhouse immutability unproven"):
        workspace.qualify_wheelhouse_writes(
            lambda argv: argv, cwd=cwd, env=os.environ,
            backend=sandbox.SandboxBackend("seatbelt", "unused"),
        )
    assert wheels.is_dir() and cwd.is_dir()
    assert not list(wheels.iterdir())


@pytest.mark.parametrize("bad", ["json", "nonce", "missing", "integer", "exposed", "exit", None])
def test_wheelhouse_qualification_requires_all_write_denials(tmp_path, monkeypatch, bad):
    from sew.gap import workspace

    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)
    (cwd.parent / "wheelhouse").mkdir()

    def run(argv, **kwargs):
        assert Path(argv[-2]).read_bytes() == b"trusted-source-probe"
        denied = dict.fromkeys(
            ["create", "modify", "chmod", "link", "rename_wheelhouse", "rename_scratch"], True
        )
        record = {"nonce": argv[-1], "denied": denied}
        if bad == "nonce":
            record["nonce"] = "wrong"
        elif bad == "missing":
            del denied["create"]
        elif bad in {"integer", "exposed"}:
            denied["create"] = 1 if bad == "integer" else False
        return subprocess.CompletedProcess(
            argv, 1 if bad == "exit" else 0,
            "broken" if bad == "json" else json.dumps(record), "",
        )

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    options = dict(cwd=cwd, env={}, backend=sandbox.SandboxBackend("seatbelt", "unused"))
    if bad is None:
        assert workspace.qualify_wheelhouse_writes(lambda argv: argv, **options)["denied"]["create"]
    else:
        with pytest.raises(EgressCanaryRefused, match="wheelhouse immutability unproven"):
            workspace.qualify_wheelhouse_writes(lambda argv: argv, **options)
    assert not list((cwd.parent / "wheelhouse").iterdir())


@pytest.mark.parametrize("backend", ["seatbelt", "bubblewrap"])
def test_readable_cache_cannot_qualify(tmp_path, backend):
    from sew.gap import workspace

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stripe-16.0.0.whl").write_bytes(b"new source")
    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)
    with pytest.raises(EgressCanaryRefused, match="cache isolation unproven"):
        workspace.qualify_cache_reads(
            lambda argv: argv, cache, cwd=cwd, env=os.environ,
            backend=sandbox.SandboxBackend(backend, "unused"),
        )


def test_verifier_scratch_is_shared_across_run_environments(tmp_path, monkeypatch):
    from sew.gap import workspace

    root = workspace.VERIFIER_ROOT
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("SEW_STATE_ROOT", str(tmp_path / "state"))
    with workspace.verifier_scratch() as first, workspace.verifier_scratch() as second:
        assert Path(first).parent == Path(second).parent == root
        assert first != second
    assert not Path(first).exists() and not Path(second).exists()


def test_verifier_scratch_refuses_substituted_root(tmp_path, monkeypatch):
    from sew.gap import workspace

    root = tmp_path / "verifier"
    root.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(workspace, "VERIFIER_ROOT", root)
    with pytest.raises(EgressCanaryRefused, match="invalid verifier scratch root"):
        with workspace.verifier_scratch():
            pytest.fail("must refuse symlink")


def test_transcript_audit_includes_concurrent_verifier_root(tmp_path):
    from sew.gap import workspace

    path = workspace.VERIFIER_ROOT / "another-run/venv/mini.py"
    transcript = [{"type": "item.completed", "item": {
        "type": "command_execution", "command": f"cat {path}",
        "exit_code": 1, "aggregated_output": "Permission denied",
    }}]
    assert "workspace:forbidden-read" in workspace.audit_workspace_calls(transcript, cwd=tmp_path)


@pytest.mark.parametrize("scratch", ["inside", "ancestor"])
def test_verifier_root_overlap_refuses_cell(tmp_path, monkeypatch, scratch):
    from sew.gap import workspace

    root = tmp_path / "verifier"
    monkeypatch.setattr(workspace, "VERIFIER_ROOT", root)
    cell = root / "cell" if scratch == "inside" else tmp_path
    with pytest.raises(EgressCanaryRefused, match="overlaps cell scratch"):
        workspace.cache_read_profile(tmp_path.parent / "cache", cell)


@pytest.mark.parametrize("exposed", ["wheel", "installed_source", None])
def test_verifier_copy_qualification_checks_both_sources(tmp_path, monkeypatch, exposed):
    from sew.gap import workspace

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "new.whl").write_bytes(b"verifier-only wheel")
    root = tmp_path / "verifier"
    monkeypatch.setattr(workspace, "VERIFIER_ROOT", root)
    paths = []

    def run(argv, **kw):
        direct = argv[3] == workspace.CACHE_DIRECT_PROBE
        if direct:
            path = Path(argv[4])
            assert path.is_relative_to(root) and path.is_file()
            paths.append(path)
        else:
            assert Path(argv[4]) == root
        kind = "wheel" if argv[4 if direct else 5].endswith(".whl") else "installed_source"
        nonce = argv[-2] if direct else argv[-1]
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"nonce": nonce, "denied": kind != exposed}), ""
        )

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    options = dict(cwd=tmp_path / "cell/workspace", env={}, backend=sandbox.SandboxBackend("seatbelt", "unused"))
    if exposed:
        with pytest.raises(EgressCanaryRefused, match="isolation unproven"):
            workspace.qualify_verifier_reads(lambda argv: argv, cache, **options)
    else:
        evidence = workspace.qualify_verifier_reads(lambda argv: argv, cache, **options)
        assert evidence["wheel"]["discovery_denied"]
        assert evidence["installed_source"]["discovery_denied"]
        assert len(paths) == 2
    assert list(root.iterdir()) == []


def test_discovery_read_cannot_qualify_even_if_direct_read_denied(tmp_path):
    from sew.gap import workspace

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stripe-16.0.0.whl").write_bytes(b"new source")
    cwd = tmp_path / "scratch/workspace"
    cwd.mkdir(parents=True)

    def wrap(argv):
        if argv[3] == workspace.CACHE_DIRECT_PROBE:
            nonce = argv[-2]
            return [sys.executable, "-c", f"print({json.dumps({'nonce': nonce, 'denied': True})!r})"]
        assert str(cache) not in argv  # Discovery starts from its ancestor.
        return argv

    with pytest.raises(EgressCanaryRefused, match="cache isolation unproven"):
        workspace.qualify_cache_reads(
            wrap, cache, cwd=cwd, env=os.environ, backend=sandbox.SandboxBackend("seatbelt", "unused")
        )


@pytest.mark.parametrize("bad", ["missing-wheel", "json", "nonce", "integer", "missing", "exit"])
def test_cache_qualification_refuses_incomplete_evidence(tmp_path, monkeypatch, bad):
    from sew.gap import workspace

    cache = tmp_path / "cache"
    cache.mkdir()
    if bad != "missing-wheel":
        (cache / "pin.whl").write_bytes(b"source")

    def run(argv, **kw):
        record = {"nonce": argv[-2], "denied": True}
        if bad == "nonce":
            record["nonce"] = "wrong"
        elif bad == "integer":
            record["denied"] = 1
        elif bad == "missing":
            del record["denied"]
        output = "invalid" if bad == "json" else json.dumps(record)
        return subprocess.CompletedProcess(argv, 1 if bad == "exit" else 0, output, "")

    monkeypatch.setattr(workspace, "_run_probe_process", run)
    with pytest.raises(EgressCanaryRefused, match="cache isolation unproven"):
        workspace.qualify_cache_reads(
            lambda argv: argv, cache, cwd=tmp_path, env={},
            backend=sandbox.SandboxBackend("seatbelt", "unused"),
        )


def test_qualification_retries_transient_launch(tmp_path, monkeypatch):
    calls = []

    def launch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EAGAIN, "fork unavailable")
        record = evidence("bubblewrap")
        record["nonce"] = args[0][-1]
        return subprocess.CompletedProcess([], 0, json.dumps(record), "")

    monkeypatch.setattr(sandbox.subprocess, "run", launch)
    monkeypatch.setattr(sandbox.time, "sleep", lambda _: None)
    assert sandbox.qualify_backend(sandbox.SandboxBackend("bubblewrap", "exe"), tmp_path, {})[
        "admissible"
    ]
    assert len(calls) == 2


def test_verifier_qualification_refusal_is_not_patch_failure(tmp_path, monkeypatch):
    import sew.gap.verify as verifier

    def prepare(*args, wheel_roles):
        assert wheel_roles == ("old", "new", "dependency")
        return None, tmp_path, {}

    monkeypatch.setattr(verifier, "prepare_workspace", prepare)
    monkeypatch.setattr(
        sandbox, "select_backend", lambda env: sandbox.SandboxBackend("bubblewrap", "exe")
    )

    def refuse(*a, **kw):
        raise EgressCanaryRefused("qualification unavailable")

    monkeypatch.setattr(sandbox, "qualify_backend", refuse)
    result = verifier.verify({"verifier": {"timeout_seconds": 1}}, tmp_path / "patch")
    assert result["outcome"] == "not_applicable"
    assert result["errors"][0]["stage"] == "sandbox_unavailable"


def test_linux_claude_admits_litellm_without_account_state(tmp_path, monkeypatch):
    from sew.gap import workspace
    import socket

    backend = sandbox.SandboxBackend("bubblewrap", "/bin/bwrap")
    monkeypatch.setattr(sandbox, "select_backend", lambda env=None: backend)
    monkeypatch.setattr(sandbox, "qualify_backend", lambda *a, **kw: {"backend": "bubblewrap", "admissible": True})
    monkeypatch.setattr(workspace.shutil, "which", lambda *a, **kw: sys.executable)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])
    cwd = tmp_path / "cell" / "workspace"
    cwd.mkdir(parents=True)
    env = {"SEW_LITELLM_BASE_URL": "http://localhost:4000", "ANTHROPIC_AUTH_TOKEN": "fake-litellm-key", "PATH": os.defpath}
    with workspace._bubblewrap_boundary([sys.executable], env, cwd=cwd, harness_id="claude-code", harness_auth="litellm") as (argv, evidence):
        assert "--unshare-net" in argv and "--cap-drop" in argv
        assert sandbox.PROXY_BRIDGE in argv
        assert env["SEW_GAP_LITELLM_BASE_URL"] == "http://localhost:4000"
        assert evidence["endpoint_authorities"] == [{"host": "localhost", "port": 4000}]
        assert Path(env["CLAUDE_CONFIG_DIR"]).is_relative_to(cwd.parent)
        assert not list(Path(env["CLAUDE_CONFIG_DIR"]).iterdir())
