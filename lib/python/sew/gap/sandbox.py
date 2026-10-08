"""Bench-owned egress backends. Unsupported or unqualified hosts fail closed."""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import uuid
import time

from ..backoff import bounded_exponential_delay
from dataclasses import dataclass
from pathlib import Path

from ..host import HostUnavailable, read_config
from .workspace import EgressCanaryRefused


@dataclass(frozen=True)
class SandboxBackend:
    name: str
    executable: str

    def command(
        self, argv, scratch, *, write_roots=None, read_roots=(), profile=None, deny_read_roots=()
    ):
        if self.name == "seatbelt":
            if profile is None:
                raise EgressCanaryRefused("GAP refused: missing Seatbelt profile")
            return [self.executable, "-p", profile, *argv]
        scratch = Path(scratch).resolve()
        prefix = [
            self.executable,
            "--unshare-net",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
        ]
        # A fresh root prevents access to host Unix sockets and hidden assets.
        roots = {Path(p).resolve() for p in read_roots}
        roots.update(
            Path(p) for p in ("/usr", "/bin", "/lib", "/lib64", "/etc") if Path(p).exists()
        )
        roots.add(Path(sys.base_prefix).resolve())
        roots.add(Path(sys.prefix).resolve())
        if Path("/") in roots:
            raise EgressCanaryRefused("GAP refused: sandbox cannot expose host root")
        # Mount parents first: a later /tmp tmpfs would hide proxy sockets,
        # harnesses and auth files explicitly bound below /tmp.
        prefix += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for root in sorted(roots):
            prefix += ["--ro-bind", str(root), str(root)]
        prefix += [
            "--ro-bind",
            str(scratch),
            str(scratch),
        ]
        for root in write_roots if write_roots is not None else [scratch]:
            root = Path(root).resolve()
            if not root.is_relative_to(scratch):
                raise EgressCanaryRefused("GAP refused: sandbox write root outside scratch")
            prefix += ["--bind", str(root), str(root)]
        wheels = scratch / "wheelhouse"
        if wheels.is_dir():
            prefix += ["--ro-bind", str(wheels), str(wheels)]
        # Mask last: system/runtime binds can otherwise expose the verifier cache.
        for root in deny_read_roots:
            root = Path(root).resolve()
            if scratch.is_relative_to(root) or root.is_relative_to(scratch):
                raise EgressCanaryRefused("GAP refused: verifier cache overlaps cell scratch")
            prefix += ["--tmpfs", str(root), "--remount-ro", str(root)]
        return [*prefix, "--", *argv]


def select_backend(env=None, *, platform=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    try:
        requested = read_config(env).get("sandbox", "auto")
    except HostUnavailable as exc:
        raise EgressCanaryRefused("GAP refused: invalid sandbox configuration") from exc
    if not isinstance(requested, str) or requested not in {"auto", "seatbelt", "bubblewrap"}:
        raise EgressCanaryRefused("GAP refused: sandbox must be auto, seatbelt or bubblewrap")
    selected = {"darwin": "seatbelt", "linux": "bubblewrap"}.get(platform)
    if selected is None or requested not in {"auto", selected}:
        raise EgressCanaryRefused(f"GAP refused: sandbox {requested} unsupported on {platform}")
    executable = "/usr/bin/sandbox-exec" if selected == "seatbelt" else "/usr/bin/bwrap"
    if not executable or not Path(executable).is_file():
        raise EgressCanaryRefused(f"GAP refused: {selected} unavailable")
    return SandboxBackend(selected, executable)


# Kept parent-owned: the evaluated model is never asked to disable a proxy.
# Explicit errors from direct TCP and DNS datagrams qualify the namespace;
# curl and isolated pip must also fail, with attributed network denial text.
# pip logs exhausted connection errors only at DEBUG (-vv), not with -v.
BACKEND_CANARY = r"""
import json, socket, subprocess, sys
probes = {}
for name, argv in (
    ('curl', ['curl', '--disable', '--noproxy', '*', '-k', '--max-time', '2', 'https://1.1.1.1/']),
    ('pip', [sys.executable, '-m', 'pip', '--isolated', 'download', '-vv', '--no-cache-dir', '--retries', '0', '--timeout', '2', '--index-url', 'https://1.1.1.1/simple', '--dest', '.', 'pip']),
):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=3)
        probes[name] = {'exit_code': p.returncode, 'error': p.stdout + p.stderr}
    except Exception as exc:
        probes[name] = {'exit_code': None, 'error': str(exc)}
for name, kind, port in [('tcp', socket.SOCK_STREAM, 443), ('dns', socket.SOCK_DGRAM, 53)]:
    try:
        with socket.socket(socket.AF_INET, kind) as s:
            s.settimeout(2)
            if name == 'tcp':
                s.connect(('1.1.1.1', port))
            else:
                s.sendto(b'\x00' * 12, ('1.1.1.1', port))
        probes[name] = {'denied': False, 'errno': None}
    except OSError as exc:
        probes[name] = {'denied': True, 'errno': exc.errno}
print(json.dumps({'nonce': sys.argv[1], 'probes': probes, 'interfaces': [name for _, name in socket.if_nameindex()]}))
"""


def parse_backend_canary(output, nonce, backend):
    try:
        record = json.loads(output)
        probes = record["probes"]
        if record["nonce"] != nonce or set(probes) != {"curl", "pip", "tcp", "dns"}:
            raise ValueError("incomplete canary")
        allowed = {errno.EPERM, errno.EACCES}
        denial = r"operation not permitted|permission denied"
        if backend.name == "bubblewrap":
            if record.get("interfaces") != ["lo"]:
                raise ValueError("fresh network namespace unproven")
            allowed.add(errno.ENETUNREACH)
            denial += r"|network is unreachable"
        for name in ("tcp", "dns"):
            p = probes[name]
            if p["denied"] is not True or type(p["errno"]) is not int or p["errno"] not in allowed:
                raise ValueError("direct denial unproven")
        for name in ("curl", "pip"):
            p = probes[name]
            pattern = denial + (
                r"|could(?:n't| not) connect to server|failed to connect" if name == "curl" else ""
            )
            if (
                type(p["exit_code"]) is not int
                or p["exit_code"] not in ({7} if name == "curl" else {1})
                or not re.search(pattern, p["error"], re.I)
            ):
                raise ValueError("tool denial unproven")
        return {**record, "backend": backend.name, "admissible": True}
    except (ValueError, KeyError, TypeError) as exc:
        raise EgressCanaryRefused(f"GAP refused: {backend.name} canary denial unproven") from exc


def qualify_backend(backend, scratch, env, *, profile=None, read_roots=()):
    nonce = uuid.uuid4().hex
    argv = backend.command(
        [sys.executable, "-I", "-c", BACKEND_CANARY, nonce],
        scratch,
        profile=profile,
        read_roots=read_roots,
    )
    # No provider keys are needed for this local qualification.
    probe_env = {"PATH": env.get("PATH", os.defpath), "HOME": str(scratch), "TMPDIR": str(scratch)}
    try:
        for attempt in range(3):
            try:
                result = subprocess.run(
                    argv, cwd=scratch, env=probe_env, capture_output=True, text=True, timeout=12
                )
                break
            except (OSError, subprocess.TimeoutExpired) as exc:
                transient = isinstance(exc, subprocess.TimeoutExpired) or exc.errno in {
                    errno.EAGAIN,
                    errno.EINTR,
                    errno.EIO,
                    errno.ETIMEDOUT,
                }
                if not transient or attempt == 2:
                    raise
                time.sleep(bounded_exponential_delay(attempt, base_seconds=0.5, max_seconds=1))
        if result.returncode:
            raise ValueError("canary process failed: " + result.stderr[:500])
        return parse_backend_canary(result.stdout, nonce, backend)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise EgressCanaryRefused(f"GAP refused: {backend.name} canary failed ({exc})") from exc


# Only a private Unix socket reaches the parent's frozen endpoint allowlist.
# The namespace has no host TCP/UDP connectivity. Each invocation starts its
# own loopback listener; exec keeps the harness as the tracked process leader.
PROXY_BRIDGE = r"""
import ipaddress, json, os, re, select, socket, sys, threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
path, *argv = sys.argv[1:]
def relay(client):
    try:
        with client, socket.socket(socket.AF_UNIX) as upstream:
            upstream.connect(path)
            if original is not None:
                # Translate only this invocation's rewritten authority. The parent
                # still enforces its frozen host/port and DNS answers.
                head = b''
                while b'\r\n\r\n' not in head:
                    chunk = client.recv(1)
                    if not chunk or len(head) >= 65536:
                        return
                    head += chunk
                line, rest = head.split(b'\r\n', 1)
                method, target, version = line.decode('latin-1').split(' ', 2)
                parsed = urlsplit('//' + target if method == 'CONNECT' else target)
                if parsed.hostname == original.hostname and parsed.port == server.getsockname()[1]:
                    target = original.netloc if method == 'CONNECT' else urlunsplit(
                        (original.scheme, original.netloc, parsed.path, parsed.query, parsed.fragment)
                    )
                    # The upstream receives its original authority, including port.
                    rest = re.sub(br'(?im)^Host:[^\r\n]*',
                                  b'Host: ' + original.netloc.encode('ascii'), rest)
                upstream.sendall((method + ' ' + target + ' ' + version + '\r\n').encode('latin-1') + rest)
            readers = [client, upstream]
            while readers:
                ready, _, _ = select.select(readers, [], [], 10)
                for source in ready:
                    target = upstream if source is client else client
                    data = source.recv(65536)
                    if data:
                        target.sendall(data)
                    else:
                        readers.remove(source)
                        target.shutdown(socket.SHUT_WR)
    except OSError:
        return
server = socket.socket()
server.bind(('127.0.0.1', 0)); server.listen()
url = 'http://127.0.0.1:' + str(server.getsockname()[1])
original = None
base = os.environ.pop('SEW_GAP_LITELLM_BASE_URL', '')
if base:
    target = urlsplit(base)
    try:
        loopback = target.hostname == 'localhost' or ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        loopback = False
    if loopback:
        original = target
        host = '[' + target.hostname + ']' if ':' in target.hostname else target.hostname
        authority = host + ':' + str(server.getsockname()[1])
        rewritten = urlunsplit((target.scheme, authority, target.path, target.query, target.fragment))
        for key in ('SEW_LITELLM_BASE_URL', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL'):
            if os.environ.get(key) == base:
                os.environ[key] = rewritten
        if os.environ.get('CODEX_HOME'):
            config = Path(os.environ['CODEX_HOME']) / 'config.toml'
            lines = config.read_text().splitlines(keepends=True)
            provider = False
            for index, line in enumerate(lines):
                if line.startswith('['):
                    provider = line.strip() == '[model_providers.searchlight_litellm]'
                if provider and line.startswith('base_url = '):
                    # Canary and cell launches each get a new bridge listener.
                    lines[index] = 'base_url = ' + json.dumps(rewritten + '/v1') + '\n'
            config.write_text(''.join(lines))
def serve():
    while True:
        client, _ = server.accept()
        threading.Thread(target=relay, args=(client,), daemon=True).start()
# A forked relay survives exec but remains in the harness process group.
pid = os.fork()
if pid == 0:
    sink = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    os.close(sink)
    # Threads do not survive fork; serve in this child instead.
    serve()
else:
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        os.environ[key] = os.environ[key.lower()] = url
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = '' if base else 'localhost,127.0.0.1,::1'
    os.execvpe(argv[0], argv, os.environ)
"""
