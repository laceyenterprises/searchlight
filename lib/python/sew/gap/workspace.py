"""GAP workspace isolation, offline inputs, and outcome-verifier patch capture.

These helpers never read hidden asset contents. A cache is verified and copied
before a child starts, so a provider or an unrelated task's wheel cannot change
what a cell installs.
"""

from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import venv
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from packaging.version import InvalidVersion, Version

from .. import harnesses
from ..backoff import bounded_exponential_delay
from ..catalog import module_root
from ..schema import SchemaError
from .catalog import load_gap_tasks

# Bound both content and metadata retained by the parent for untrusted output.
MAX_WORKSPACE_FILE_BYTES = 4 * 1024 * 1024
MAX_WORKSPACE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_WORKSPACE_ENTRIES = 10_000

# Only the driver supplies pip configuration in code cells. Unknown settings
# can add requirements, constraints or source inputs even with no-index enabled.
CELL_PIP_ENV_ALLOWLIST = frozenset({
    "PIP_NO_INDEX", "PIP_FIND_LINKS", "PIP_CONFIG_FILE",
    "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_REQUIRE_VIRTUALENV",
})

# Fixed across runs and independent of TMPDIR, state roots and child environments.
# Do not resolve the final component: a substituted symlink must be refused.
VERIFIER_ROOT = Path("/tmp").resolve() / f"sew-gap-verifier-{os.getuid()}"


class EgressCanaryRefused(SchemaError):
    """No GAP task may start without positive containment evidence.

    SchemaError is the harness boundary for refused evaluation inputs, including
    dynamic containment evidence; retaining it lets callers record policy denial
    through the same controlled refusal path as an invalid workspace.
    """


@contextmanager
def verifier_scratch():
    """Keep every verifier copy beneath the account-wide agent-denied root."""
    try:
        VERIFIER_ROOT.mkdir(mode=0o700, exist_ok=True)
        metadata = VERIFIER_ROOT.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise EgressCanaryRefused("GAP refused: invalid verifier scratch root")
        directory = tempfile.TemporaryDirectory(prefix="sew-gap-verify-", dir=VERIFIER_ROOT)
    except OSError as exc:
        raise EgressCanaryRefused("GAP refused: verifier scratch unavailable") from exc
    with directory as name:
        yield name


def workspace_task(config):
    root = Path(config.gap_module_root or module_root()).resolve()
    tasks = load_gap_tasks(root)
    task = tasks.get(config.task_id)
    if task is None or task["task_type"] != "code":
        raise SchemaError("workspace profile requires a GAP code catalog task")
    if config.prompt_text is not None and config.prompt_text != task["prompt"]:
        raise SchemaError("GAP workspace prompt must match the catalog")
    return root, task


def prepare_workspace(
    config, scratch: Path, root: Path, task: dict, *, wheel_roles=("old", "dependency")
):
    catalog = root / "catalogs" / "gap"
    fixture = catalog / task["fixture"]
    pristine = snapshot_tree(fixture)
    workspace = scratch / "workspace"
    workspace.mkdir()
    materialize_tree(pristine, workspace)
    wheels = scratch / "wheelhouse"
    wheels.mkdir()
    for pin in task["packages"]:
        if pin["role"] not in wheel_roles:
            continue
        name = Path(unquote(urlsplit(pin["url"]).path)).name
        if not name.endswith(".whl") or "/" in name or "\\" in name:
            raise SchemaError("invalid wheel cache filename")
        if config.wheelhouse is None:
            raise SchemaError("GAP packages require a verified wheelhouse cache")
        source = Path(config.wheelhouse) / name
        if source.is_symlink() or not source.is_file():
            raise SchemaError(f"missing wheelhouse wheel: {name}")
        target = wheels / name
        if target.exists():
            raise SchemaError(f"conflicting wheelhouse filename: {name}")
        shutil.copyfile(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != pin["sha256"]:
            raise SchemaError(f"wheelhouse hash mismatch: {name}")
        target.chmod(0o444)
    # The installed package source is an allowed floor strategy; only the
    # catalog and workbench internals are verifier-side knowledge.
    return (
        pristine,
        workspace,
        {
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheels),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        },
    )


def prepare_python_environment(scratch: Path, offline_env: dict, source_env: Mapping) -> dict:
    """Keep installs out of the host interpreter and out of the source diff."""
    environment = scratch / "venv"
    venv.EnvBuilder(with_pip=True, symlinks=False).create(environment)
    return {
        **offline_env,
        "VIRTUAL_ENV": str(environment),
        "PATH": str(environment / "bin") + os.pathsep + source_env.get("PATH", os.defpath),
        "PIP_REQUIRE_VIRTUALENV": "1",
    }


def _tree_paths(tree: Path):
    def refuse(exc):
        raise SchemaError("GAP workspace contains unreadable entries; diff refused") from exc

    try:
        for directory, directories, files in os.walk(tree, onerror=refuse, followlinks=False):
            for name in directories + files:
                yield Path(directory) / name
    except OSError as exc:
        refuse(exc)


def check_tree(tree: Path) -> list[Path]:
    paths = []
    total_bytes = 0
    try:
        for path in _tree_paths(tree):
            if len(paths) >= MAX_WORKSPACE_ENTRIES:
                raise SchemaError("GAP workspace entries exceed cap")
            paths.append(path)
            if path.name == ".git":
                raise SchemaError("GAP fixtures/workspaces must not contain .git metadata")
            if path.is_symlink():
                if not path.resolve().is_relative_to(tree.resolve()):
                    raise SchemaError("GAP workspace symlink escapes its root")
            elif path.is_file():
                size = path.stat().st_size
                if size > MAX_WORKSPACE_FILE_BYTES:
                    raise SchemaError("GAP workspace file size exceeds cap")
                total_bytes += size
                if total_bytes > MAX_WORKSPACE_TOTAL_BYTES:
                    raise SchemaError("GAP workspace total size exceeds cap")
            elif not path.is_dir():
                raise SchemaError("GAP workspace contains a special file")
    except OSError as exc:
        raise SchemaError("GAP workspace contains unreadable entries; diff refused") from exc
    return paths


def snapshot_tree(tree: Path) -> dict:
    """Freeze the baseline in parent memory, outside the shell's writable /tmp."""
    snapshot = {}
    total_bytes = 0
    try:
        for path in check_tree(tree):
            relative = path.relative_to(tree).as_posix()
            if path.is_symlink():
                link = os.readlink(path)
                if Path(link).is_absolute():
                    link = os.path.relpath(path.resolve(), path.parent.resolve())
                snapshot[relative] = ("symlink", link, 0)
            elif path.is_file():
                # The file may grow after preflight. Never perform an unbounded
                # read, and enforce the aggregate cap on the actual captured bytes.
                allowance = min(MAX_WORKSPACE_FILE_BYTES, MAX_WORKSPACE_TOTAL_BYTES - total_bytes)
                with path.open("rb") as source:
                    content = source.read(allowance + 1)
                if len(content) > allowance:
                    raise SchemaError("GAP workspace snapshot size exceeds cap")
                total_bytes += len(content)
                snapshot[relative] = ("file", content, path.stat().st_mode & 0o777)
            else:
                snapshot[relative] = ("directory", b"", path.stat().st_mode & 0o777)
    except OSError as exc:
        raise SchemaError("GAP workspace contains unreadable entries; diff refused") from exc
    return snapshot


def materialize_tree(snapshot: dict, tree: Path):
    for name, (kind, content, mode) in snapshot.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "symlink":
            path.symlink_to(content)
        elif kind == "directory":
            path.mkdir(exist_ok=True)
        else:
            path.write_bytes(content)
            path.chmod(mode)


def capture_diff(pristine: Path | dict, workspace: Path, target: Path, *, forbidden_values=()):
    """Binary-safe git patch, including added, deleted, ignored and mode changes.

    The index is private to the parent and created AFTER the job; the child
    cannot replace a baseline or hide its output via .gitignore/index edits.
    """
    final = snapshot_tree(workspace)
    for kind, content, _ in final.values():
        if kind == "file" and any(
            value.encode() in content for value in forbidden_values if value and len(value) > 8
        ):
            raise SchemaError("GAP workspace contains a credential; diff refused")
    baseline = snapshot_tree(pristine) if isinstance(pristine, Path) else pristine
    with tempfile.TemporaryDirectory(prefix="sew-gap-diff-") as scratch:
        repo = Path(scratch)
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": scratch,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }

        def git(*args):
            try:
                return subprocess.run(
                    ["git", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=repo,
                    env=env,
                    check=True,
                    capture_output=True,
                ).stdout
            except (subprocess.CalledProcessError, OSError) as exc:
                raise SchemaError("GAP workspace diff generation failed") from exc

        materialize_tree(baseline, repo)
        git("init", "-q")
        # Workspace attributes must not normalize/filter the bytes captured
        # for the verifier. info/attributes overrides repository attributes.
        (repo / ".git/info/attributes").write_text("* -text -filter -ident\n", encoding="utf-8")
        git("add", "-A", "-f", "--", ".")
        tree = git("write-tree").decode().strip()
        for path in repo.iterdir():
            if path.name != ".git":
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        materialize_tree(final, repo)
        git("add", "-A", "-f", "--", ".")
        patch = git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", tree, "--")
        # An incomplete patch cannot be used by the outcome oracle.
        if len(patch) > 190_000:
            raise SchemaError("GAP workspace diff exceeds artifact cap")
        from ..harness import (
            AUTHORIZATION_HEADER_VALUE_RE,
            BEARER_TOKEN_SHAPE_RE,
            COOKIE_HEADER_VALUE_RE,
        )

        try:
            text = patch.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaError("GAP diff is not UTF-8 evidence") from exc
        if any(
            pattern.search(text)
            for pattern in (
                AUTHORIZATION_HEADER_VALUE_RE,
                BEARER_TOKEN_SHAPE_RE,
                COOKIE_HEADER_VALUE_RE,
            )
        ):
            raise SchemaError("GAP diff contains secret-like material; capture refused")
        target.write_bytes(patch)


SHELL_TOOLS = {
    "bash",
    "shell",
    "shell_command",
    "exec_command",
    "command_execution",
    "unified_exec",
    # Hermes Agent's shell. Opencode and Pi name theirs "bash".
    "terminal",
}
FILE_TOOLS = {"read", "read_file", "edit", "write", "write_file", "patch"}
NETWORK_EXECUTABLES = {
    "curl",
    "wget",
    "http",
    "https",
    "httpie",
    "nslookup",
    "dig",
    "host",
    "ssh",
    "scp",
    "sftp",
    "ftp",
    "telnet",
    "nc",
    "netcat",
    "socat",
}
SCRIPT_NETWORK = re.compile(
    r"\b(?:requests|urllib|httpx|socket)\s*[.(]|"
    r"\b(?:fetch|XMLHttpRequest)\s*\(",
    re.I,
)


def shell_network_attempt(command: str) -> bool:
    """Recognize invoked commands, rather than searches/quotes about commands.

    This is a contamination audit, not a shell security parser. Arbitrary code
    can construct a URL dynamically; the harness sandbox remains the egress
    fence and the canary is mandatory even with a clean transcript.
    """
    segments = _shell_network_segments(command)
    return segments is None or bool(segments)


def _shell_segments(command: str) -> list[list[str]] | None:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None  # incomplete shell requests cannot be certified clean
    segments = [[]]
    for token in tokens:
        if token and all(char in ";&|()\n" for char in token):
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _shell_invocation(args):
    """Strip simple wrappers and retain pip environment override evidence."""
    pip_override = offline_override = False
    while args:
        if "=" in args[0] and not args[0].startswith("-"):
            name, _, value = args[0].partition("=")
            pip_override |= name.startswith("PIP_")
            offline_override |= name == "PIP_NO_INDEX" and value.casefold() in {
                "",
                "0",
                "false",
                "no",
                "off",
            }
            args = args[1:]
        elif args[0] == "env":
            args = args[1:]
            while args and args[0].startswith("-"):
                option = args[0]
                if option in {"-u", "--unset"}:
                    name = args[1] if len(args) > 1 else ""
                    pip_override |= name.startswith("PIP_")
                    offline_override |= name == "PIP_NO_INDEX"
                    args = args[2:]
                elif option.startswith("--unset="):
                    name = option.partition("=")[2]
                    pip_override |= name.startswith("PIP_")
                    offline_override |= name == "PIP_NO_INDEX"
                    args = args[1:]
                elif option in {"-i", "-", "--ignore-environment"}:
                    pip_override = offline_override = True
                    args = args[1:]
                elif option == "--":
                    args = args[1:]
                    break
                else:
                    break
        elif Path(args[0]).name in {"command", "exec", "sudo", "nohup", "time", "xargs"}:
            args = args[1:]
            if args[:1] == ["--"]:
                args = args[1:]
        else:
            break
    if args and args[0] in {"export", "readonly", "typeset"}:
        assignments = [arg for arg in args[1:] if "=" in arg]
        _, assigned_pip, assigned_offline = _shell_invocation(assignments)
        pip_override |= assigned_pip
        offline_override |= assigned_offline
    if args and args[0] == "unset":
        pip_override |= any(arg.startswith("PIP_") for arg in args[1:])
        offline_override |= "PIP_NO_INDEX" in args[1:]
    return args, pip_override, offline_override


def _pip_index_option(arg, *, include_isolated=True):
    # pip accepts unambiguous long-option abbreviations after shell quote removal.
    # Boolean short options can precede -i, with a separate or attached value.
    option = arg.partition("=")[0]
    return (
        option.startswith("--")
        and len(option) > 2
        and any(
            full.startswith(option)
            for full in (
                ("--isolated", "--index-url", "--extra-index-url")
                if include_isolated else ("--index-url", "--extra-index-url")
            )
        )
    ) or bool(re.match(r"^-[qvUI]*i", arg))


def _pip_unverified_source_option(arg):
    # Requirements/constraints can recursively add remote sources. Even a
    # local find-links HTML file can point to remote wheels. The transcript
    # contains no immutable copy of these inputs, so none can certify locality.
    option = arg.partition("=")[0]
    return (
        option.startswith("--")
        and len(option) > 2
        and any(
            full.startswith(option)
            for full in ("--requirement", "--constraint", "--build-constraint", "--find-links")
        )
    ) or bool(re.match(r"^-[qvUI]*[rcf]", arg))


def _pip_dynamic_arguments(args):
    # Transcript tokenization cannot prove expanded requirements/index inputs.
    for index, arg in enumerate(args):
        if "$" not in arg and "`" not in arg:
            continue
        # The bench owns TMPDIR. A download destination does not select a
        # package source, and existing offline downloads use this spelling.
        if (
            index > 0
            and args[index - 1] in {"-d", "--dest"}
            and re.fullmatch(r"\$(?:TMPDIR|\{TMPDIR\})(?:/[^$`\\]*)?", arg)
        ):
            continue
        return True
    return False


# The configuration-neutralized pip exemption is a strict allowlist, not a shell
# parser: shell syntax is open-ended, so anything unrecognized is not exempt.
PIP_EXEMPT_FLAGS = frozenset({"--no-deps", "--no-index", "-q", "--quiet"})
PIP_EXEMPT_PIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?==([0-9][A-Za-z0-9.+_-]*)")


def _shell_body(args):
    """Return executable shell text, never an ordinary command's literal arguments."""
    if not args:
        return None
    executable = Path(args[0]).name
    if executable == "eval":
        return " ".join(args[1:])
    if executable in {"bash", "sh", "zsh"}:
        for index, arg in enumerate(args[1:], start=1):
            if arg == "--":
                continue
            if not arg.startswith("-"):
                break
            if not arg.startswith("--") and "c" in arg[1:]:
                return args[index + 1] if index + 1 < len(args) else ""
    return None


# Bound both substitution scanning and recursive shell-body interpretation.
# Excessive nesting is uncertifiable, never an exception or a clean command.
SHELL_PARSE_MAX_DEPTH = 64


def _shell_substitutions(command, *, _depth=0):
    """Separate active command substitutions from literal quoted command text.

    Leave a dynamic argument marker in the outer command, so pip source checks
    still refuse to certify arguments produced by arbitrary shell execution.
    """
    def scan(index, end=None, nesting=_depth):
        if nesting >= SHELL_PARSE_MAX_DEPTH:
            return None, [], index
        text, bodies = [], []
        quote = None
        depth = 0
        word_start = True
        while index < len(command):
            char = command[index]
            if quote is None and end == "`" and char == "`":
                return "".join(text), bodies, index + 1
            if quote is None and word_start and char == "#":
                # Comments cannot execute substitutions or close the current
                # substitution. Resume at the next physical newline.
                newline = command.find("\n", index)
                index = newline if newline >= 0 else len(command)
                continue
            if char == "\\" and quote != "'":
                text.append(command[index:index + 2])
                if command[index:index + 2] != "\\\n":
                    word_start = False
                index += 2
                continue
            if quote != "'" and (command.startswith("$(", index) or char == "`"):
                closing = ")" if char == "$" else "`"
                body, nested, index = scan(
                    index + (2 if char == "$" else 1), closing, nesting + 1
                )
                if body is None:
                    return None, [], index
                bodies.extend([(body, nesting + 1), *nested])
                text.append("$GAP_SUBSTITUTION")
                word_start = False
                continue
            if quote is None and end == ")" and char == "(":
                depth += 1
            elif quote is None and char == end:
                if depth == 0:
                    return "".join(text), bodies, index + 1
                depth -= 1
            if char in {"'", '"'}:
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None
            text.append(char)
            word_start = quote is None and char in " \t\r\n;&|()<>"
            index += 1
        return (None if end else "".join(text)), bodies, index

    text, bodies, _ = scan(0)
    return text, bodies


def _pip_arguments(args):
    executable, tail = Path(args[0]).name.casefold(), args[1:]
    if re.fullmatch(r"pip[0-9.]*", executable):
        return tail
    if executable.startswith("python") and "-m" in tail:
        module_index = tail.index("-m")
        if tail[module_index + 1:module_index + 2] == ["pip"]:
            return tail[module_index + 2:]
    return None


def _is_version(value) -> bool:
    # A non-version such as "1.zip" can make pip read the argument as a local archive.
    try:
        Version(value)
    except InvalidVersion:
        return False
    return True


def _pinned_offline_pip_env(env) -> bool:
    wheelhouse = env.get("PIP_FIND_LINKS", "")
    return (
        env.get("PIP_NO_INDEX") == "1"
        and env.get("PIP_CONFIG_FILE") == "/dev/null"
        and not any(key.startswith("PIP_") and key not in CELL_PIP_ENV_ALLOWLIST for key in env)
        and "://" not in wheelhouse
    )


def pip_command_is_exempt(command, env) -> bool:
    """Exempt only a direct, pinned, local-only pip download/install request."""
    if not _pinned_offline_pip_env(env) or not isinstance(command, str):
        return False
    wheelhouse = env.get("PIP_FIND_LINKS")
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    # Canonical quoting proves every word is a literal: no wrapper, operator,
    # redirection, expansion, substitution or comment can be present.
    if not argv or shlex.join(argv) != command:
        return False
    if argv[0] in {"pip", "pip3"}:
        args = argv[1:]
    elif argv[0] in {"python", "python3"} and argv[1:3] == ["-m", "pip"]:
        args = argv[3:]
    else:
        return False
    if not args or args[0] not in {"download", "install"}:
        return False
    subcommand, args = args[0], args[1:]
    pins = index = 0
    while index < len(args):
        arg = args[index]
        value = args[index + 1] if index + 1 < len(args) else None
        if arg in PIP_EXEMPT_FLAGS:
            index += 1
        elif (
            arg in {"-d", "--dest"}
            and subcommand == "download"
            and value
            and not value.startswith("-")
            and "://" not in value
        ):
            index += 2
        elif arg == "--find-links" and wheelhouse and value == wheelhouse:
            index += 2
        elif (pin := PIP_EXEMPT_PIN.fullmatch(arg)) and _is_version(pin[1]):
            pins += 1
            index += 1
        else:
            return False
    return pins > 0


def _shell_network_segments(
    command: str, *, inherited_offline_override=False, _depth=0
) -> list[list[str]] | None:
    """Share quote-aware invocation parsing between detection and attribution."""
    if _depth >= SHELL_PARSE_MAX_DEPTH:
        return None
    command, substitutions = _shell_substitutions(command, _depth=_depth)
    if command is None:
        return None
    segments = _shell_segments(command)
    if segments is None:
        return None
    invocations = [_shell_invocation(args) for args in segments]
    offline_override = inherited_offline_override or any(override for _, _, override in invocations)
    network_segments = []
    for body, nesting in substitutions:
        nested = _shell_network_segments(
            body, inherited_offline_override=offline_override, _depth=nesting
        )
        if nested is None:
            return None
        network_segments.extend(nested)
    for args, _, _ in invocations:
        if not args:
            continue
        executable = Path(args[0]).name.casefold()
        tail = args[1:]
        if executable in NETWORK_EXECUTABLES:
            network_segments.append(args)
            continue
        if (body := _shell_body(args)) is not None:
            nested = _shell_network_segments(
                body, inherited_offline_override=offline_override, _depth=_depth + 1
            )
            if nested is None:
                return None
            network_segments.extend(nested)
            continue
        if (pip_args := _pip_arguments(args)) is not None:
            executable, tail = "pip", pip_args
        if executable in {"pip", "pip3", "uv"}:
            if _pip_dynamic_arguments(tail):
                network_segments.append(args)
                continue
            if "download" in tail:
                network_segments.append(args)
                continue
            if "config" in tail:
                config_override = False
                for index, arg in enumerate(tail):
                    if arg.endswith(".no-index") and (
                        "unset" in tail
                        or (
                            "set" in tail
                            and tail[index + 1 : index + 2]
                            and tail[index + 1].casefold() in {"", "0", "false", "no", "off"}
                        )
                    ):
                        network_segments.append(args)
                        config_override = True
                        break
                if config_override:
                    continue
            if "install" in tail or "sync" in tail:
                if (
                    any(
                        "://" in arg
                        or _pip_index_option(arg)
                        or _pip_unverified_source_option(arg)
                        for arg in tail
                    )
                    or offline_override
                    or (executable == "uv" and "--offline" not in tail)
                ):
                    network_segments.append(args)
                continue
        if executable == "git" and any(
            arg in {"clone", "fetch", "pull", "ls-remote"} for arg in tail
        ):
            network_segments.append(args)
        if executable in {"npm", "pnpm", "yarn"} and any(
            arg in {"view", "info", "install", "add", "search", "fetch"} for arg in tail
        ):
            network_segments.append(args)
        if executable.startswith(("python", "node", "ruby", "perl")) and SCRIPT_NETWORK.search(
            " ".join(tail)
        ):
            network_segments.append(args)
    return network_segments


def call_payloads(value, *, include_id=False):
    """Only tool requests; quoted commands in prose/results are not requests."""
    if isinstance(value, (list, tuple)):
        for entry in value:
            yield from call_payloads(entry, include_id=include_id)
    elif isinstance(value, Mapping):
        part = value.get("part")
        if value.get("type") == "tool_use" and isinstance(part, Mapping) and part.get("type") == "tool":
            # Opencode reports each finished tool as one part, input in its state.
            state = part.get("state")
            payload = state.get("input") if isinstance(state, Mapping) else None
            record = (str(part.get("tool")).casefold(), payload)
            yield (*record, part.get("callID")) if include_id else record
            return
        if value.get("type") in {
            "tool_use",
            "tool_call",
            "function_call",
            "command_execution",
            "mcp_tool_call",
            "web_search",
            "web_search_call",
            # Pi's assistant content block.
            "toolCall",
        }:
            name = value.get("name") or value.get("tool") or value.get("type")
            function = value.get("function")
            payload = value.get("input", value.get("arguments", value))
            if isinstance(function, Mapping):
                name = function.get("name", name)
                payload = function.get("arguments", payload)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {"command": payload}
            record = (str(name).casefold().removeprefix("functions."), payload)
            yield (*record, value.get("id", value.get("call_id"))) if include_id else record
        for key in ("harness_event", "message", "content", "item"):
            if key in value:
                yield from call_payloads(value[key], include_id=include_id)


def _blocks(value):
    """Every mapping in a transcript, including nested protocol events."""
    if isinstance(value, (list, tuple)):
        for entry in value:
            yield from _blocks(entry)
    elif isinstance(value, Mapping):
        yield value
        for key in ("harness_event", "message", "content", "item"):
            if key in value:
                yield from _blocks(value[key])


def _shell_calls(transcript):
    """``({call_id: command}, {call_id: tool name})`` for shell tool requests."""
    calls, names = {}, {}
    for name, payload, call_id in call_payloads(transcript, include_id=True):
        if name in SHELL_TOOLS and isinstance(payload, Mapping) and call_id is not None:
            calls[call_id] = payload.get("command", "")
            names[call_id] = name
    return calls, names


def _portable_shell_result(entry, calls, names):
    """``(call_id, command, output, failed)`` for an OSS harness's shell result.

    Each shape binds its output to the request id, so the hosted harnesses'
    pairing rules apply unchanged. Anything else returns None.
    """
    part = entry.get("part")
    if entry.get("type") == "tool_use" and isinstance(part, Mapping) and part.get("type") == "tool":
        # Opencode: the finished part carries input, output and exit status.
        state = part.get("state") if isinstance(part.get("state"), Mapping) else {}
        if state.get("status") not in {"completed", "error"}:
            return None
        metadata = state.get("metadata") if isinstance(state.get("metadata"), Mapping) else {}
        exit_code = metadata.get("exit")
        failed = state["status"] == "error" or (type(exit_code) is int and exit_code != 0)
        output = state.get("error" if state["status"] == "error" else "output", "")
        call_id = part.get("callID")
        return call_id, calls.get(call_id, ""), output, failed
    if entry.get("role") == "toolResult":
        # Pi: a tool result message names its call and flags failures.
        content = entry.get("content")
        output = "\n".join(
            block.get("text", "")
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, Mapping) and isinstance(block.get("text", ""), str)
        )
        failed = entry.get("isError") is True
        if failed:
            # Pi appends the exit status Codex reports separately; the command's
            # own last line is what the denial rule reads.
            output = re.sub(r"(?:\A|\n\n)Command exited with code -?[0-9]+\Z", "", output)
        call_id = entry.get("toolCallId")
        return call_id, calls.get(call_id, ""), output, failed
    if entry.get("type") == "tool_result" and names.get(entry.get("tool_use_id")) == "terminal":
        # Hermes: the terminal tool's result is a JSON object with an exit code.
        call_id = entry["tool_use_id"]
        try:
            result = json.loads(entry.get("content", ""))
        except (TypeError, ValueError):
            result = None
        if not isinstance(result, Mapping):
            return call_id, calls.get(call_id, ""), "", False
        exit_code = result.get("exit_code")
        return (
            call_id,
            calls.get(call_id, ""),
            result.get("output", ""),
            type(exit_code) is int and exit_code != 0,
        )
    return None


def _network_invocation_is_last(command, *, inherited_offline_override=False, _depth=0):
    """Reject trailing shell segments that could supply output or exit status."""
    if _depth >= SHELL_PARSE_MAX_DEPTH:
        return False
    command, substitutions = _shell_substitutions(command, _depth=_depth)
    if command is None or substitutions:
        return False  # substitution output/status can be replaced by the outer command
    segments = _shell_segments(command)
    if segments is None:
        return False
    segments = [segment for segment in segments if segment]
    if not segments:
        return False
    invocations = [_shell_invocation(segment) for segment in segments]
    args = invocations[-1][0]
    offline_override = inherited_offline_override or any(override for _, _, override in invocations)
    if not args:
        return False
    if (body := _shell_body(args)) is not None:
        return _network_invocation_is_last(
            body, inherited_offline_override=offline_override, _depth=_depth + 1
        )
    return bool(
        _shell_network_segments(
            shlex.join(args), inherited_offline_override=offline_override, _depth=_depth
        )
    )


def denied_network_commands(transcript):
    """Pair runtime results with requests; prose and unrelated denials do not qualify.

    Complex commands with multiple network invocations stay conservative: a
    denial of one invocation cannot certify the others.
    """
    from urllib.parse import urlsplit

    denied = set()
    entries = list(_blocks(transcript))
    calls, names = _shell_calls(transcript)
    for entry in entries:
        if (result := _portable_shell_result(entry, calls, names)) is not None:
            call_id, command, output, failed = result
        elif entry.get("type") == "tool_result":
            call_id = entry.get("tool_use_id")
            command = calls.get(call_id, "")
            content = entry.get("content", "")
            output = (
                content
                if isinstance(content, str)
                else "\n".join(
                    block.get("text", "")
                    for block in (content if isinstance(content, list) else [])
                    if isinstance(block, Mapping) and isinstance(block.get("text", ""), str)
                )
            )
            failed = entry.get("is_error") is True
        elif entry.get("type") == "command_execution" and entry.get("status") in {
            "completed",
            "failed",
        }:
            call_id = entry.get("id")
            command = entry.get("command", "")
            output = entry.get("aggregated_output", "")
            failed = entry.get("status") == "failed" or (
                type(entry.get("exit_code")) is int and entry["exit_code"] != 0
            )
        else:
            continue
        if not isinstance(command, str) or not isinstance(output, str):
            continue
        segments = _shell_network_segments(command)
        if segments is None or len(segments) != 1:
            continue
        targets = set()
        for url in re.findall(r"https?://[^\s\"']+", command):
            try:
                parsed = urlsplit(url)
                targets.add(
                    (parsed.hostname, str(parsed.port or (443 if parsed.scheme == "https" else 80)))
                )
            except ValueError:
                pass
        pip_args = _pip_arguments(segments[0])
        if pip_args is not None and "download" in pip_args and not any(
            _pip_index_option(arg, include_isolated=False) or "://" in arg
            for arg in pip_args
        ):
            targets.add(("pypi.org", "443"))
        # The harness block must be unique, separate, and trailing. Embedded
        # denial-shaped stdout cannot certify a compound command.
        sandbox_targets = set()
        block = re.search(
            r"(?:^|\n)<sandbox_violations>\s*([\s\S]*?)</sandbox_violations>\s*\Z", output
        )
        if (
            block
            and output.count("<sandbox_violations>") == 1
            and output.count("</sandbox_violations>") == 1
        ):
            lines = [line for line in block[1].splitlines() if line.strip()]
            matches = [
                re.fullmatch(
                    r"deny network-outbound ([a-zA-Z0-9.-]+):([0-9]+) \(host is on the deny list\)",
                    line,
                )
                for line in lines
            ]
            if matches and all(matches):
                sandbox_targets = {match.groups() for match in matches}
        final_line = output.rstrip().splitlines()[-1] if output.strip() else ""
        if _network_invocation_is_last(command) and (
            (targets and targets <= sandbox_targets)
            or (
                failed
                and len(targets) <= 1
                and re.search(
                    r"(?:connect|socket|network|urlopen)[^\n]*(?:Operation not permitted|Permission denied|EPERM|EACCES)",
                    final_line,
                    re.I,
                )
            )
        ):
            if call_id is not None:
                denied.add((call_id, command))
    return denied


def config_neutralized_pip_commands(transcript, env):
    """Certify local pip results only for the strict allowlist.

    See ``pip_command_is_exempt``; any other pip request remains a detected
    attempt unless the sandbox recorded an attributable denial.
    """
    if not _pinned_offline_pip_env(env):
        return set()
    calls, names = _shell_calls(transcript)
    neutralized = set()
    entries = list(_blocks(transcript))
    # Claude may background a compound command. Its task notification binds
    # the output file to the original request; a paired cat result supplies
    # the pip output without treating unrelated transcript prose as evidence.
    background_reads = {}
    for entry in entries:
        if entry.get("subtype") == "task_notification" and isinstance(
            entry.get("output_file"), str
        ):
            for read_id, read_command in calls.items():
                if read_command == "cat " + entry["output_file"]:
                    background_reads[read_id] = entry.get("tool_use_id")
    for entry in entries:
        if (result := _portable_shell_result(entry, calls, names)) is not None:
            call_id, command, output, _ = result
        elif entry.get("type") == "tool_result":
            call_id = entry.get("tool_use_id")
            call_id = background_reads.get(call_id, call_id)
            command = calls.get(call_id, "")
            content = entry.get("content", "")
            output = (
                content
                if isinstance(content, str)
                else "\n".join(
                    block.get("text", "") for block in content if isinstance(block, Mapping)
                )
                if isinstance(content, list)
                else ""
            )
        elif entry.get("type") == "command_execution" and entry.get("status") in {
            "completed",
            "failed",
        }:
            call_id, command = entry.get("id"), entry.get("command", "")
            output = entry.get("aggregated_output", "")
        else:
            continue
        if not isinstance(command, str) or not isinstance(output, str):
            continue
        if not pip_command_is_exempt(command, env):
            continue
        if re.search(r"https?://|Retrying|Connecting", output, re.I):
            continue
        if (
            "(from versions: none)" in output and "No matching distribution found" in output
        ) or re.search(r"^Successfully (?:downloaded|installed) .+", output, re.M):
            neutralized.add((call_id, command))
    return neutralized


def audit_workspace_calls(
    transcript,
    *,
    cwd: Path | None = None,
    forbidden_paths=(),
    denied_attempts=None,
    cell_env=None,
    neutralized_attempts=None,
    new_packages=(),
    new_package_attempts=None,
):
    forbidden = [Path(path).resolve() for path in forbidden_paths]
    # Always guard the canonical module too, including ad-hoc audit callers.
    forbidden.extend((module_root().resolve(), VERIFIER_ROOT))
    violations = []
    denied = denied_network_commands(transcript)
    neutralized = config_neutralized_pip_commands(transcript, cell_env or {}) - denied
    for name, payload, call_id in call_payloads(transcript, include_id=True):
        if not isinstance(payload, Mapping):
            continue
        command = payload.get("command", payload.get("cmd", ""))
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        shell = name in SHELL_TOOLS
        # Behavior evidence only: containment, not this heuristic, blocks access.
        if (
            shell
            and isinstance(command, str)
            and re.search(r"\b(?:install|download|curl|wget)\b", command, re.IGNORECASE)
        ):
            for pin in new_packages:
                package = re.escape(pin["name"]).replace(r"\-", "[-_.]")
                version = re.escape(pin["version"])
                if re.search(
                    rf"(?<![\w.])(?:{package}|{version})(?![\w.])", command, re.IGNORECASE
                ):
                    if new_package_attempts is not None:
                        new_package_attempts.add((call_id, command))
                    break
        if (
            shell
            and isinstance(command, str)
            and shell_network_attempt(command)
        ):
            if (call_id, command) in neutralized:
                if neutralized_attempts is not None:
                    neutralized_attempts.add((call_id, command))
            elif (call_id, command) in denied:
                if denied_attempts is not None:
                    denied_attempts.add((call_id, command))
            else:
                violations.append("workspace:shell-network")
        # File tools: Claude and Pi spell them read/edit/write, Hermes read_file,
        # write_file and patch; Opencode's take a filePath.
        if name in FILE_TOOLS or shell:
            values = [
                str(payload.get(key, ""))
                for key in ("file_path", "filePath", "path", "file", "command", "cmd")
            ]
            text = " ".join(values)
            if "catalogs/gap/hidden" in text or any(
                re.search(re.escape(str(path)) + r"(?:[/\s\"\']|$)", text) for path in forbidden
            ):
                violations.append("workspace:forbidden-read")
                continue
            try:
                tokens = shlex.split(text)
            except ValueError:
                tokens = text.split()
            for token in tokens:
                token = token.strip(";,()")
                if not token or token.startswith("-"):
                    continue
                candidate = Path(token).expanduser()
                if not candidate.is_absolute():
                    if cwd is None:
                        continue
                    candidate = cwd / candidate
                candidate = candidate.resolve()
                if any(candidate.is_relative_to(path) for path in forbidden):
                    violations.append("workspace:forbidden-read")
                    break
    return list(dict.fromkeys(violations))


# Run from a shell tool, never the unsandboxed harness parent. Probe output is
# accepted only from the matching shell call's result, never an agent message.
CANARY_PROGRAM = r"""
import json, subprocess, sys
harness, nonce = sys.argv[1:3]
curl_host = nonce + '.example.com' if harness == 'claude-code' else '1.1.1.1'
https_host = nonce + '.example.org' if harness == 'claude-code' else '1.1.1.1'
results = {}
def probe(name, argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        results[name] = {'exit_code': p.returncode, 'error': p.stdout + p.stderr}
    except Exception as exc:
        results[name] = {'exit_code': None, 'error': str(exc)}
probe('curl', ['curl', '--disable', '-k', '--max-time', '8', 'https://' + curl_host + '/'])
probe('pip', [sys.executable, '-m', 'pip', '--isolated', 'download', '--no-cache-dir',
              '--retries', '0', '--timeout', '8', '--index-url', 'https://pypi.org/simple',
              '--dest', '.', 'pip'])
probe('https', [sys.executable, '-c',
                'import urllib.request, sys; urllib.request.urlopen(sys.argv[1], timeout=8)',
                'https://' + https_host + '/'])
print('GAP_CANARY:' + json.dumps({'nonce': nonce, 'probes': results}, sort_keys=True))
"""
# OSS harnesses have no shell sandbox of their own; the bench's portable
# sandbox confines the whole tree. Every probe targets an address, never a
# name, and verbose output carries the attributable denial: the OS refusing a
# direct connect, or the bench endpoint proxy refusing a proxied one.
PORTABLE_CANARY_PROGRAM = r"""
import json, subprocess, sys
harness, nonce = sys.argv[1:3]
results = {}
def probe(name, argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        results[name] = {'exit_code': p.returncode, 'error': p.stdout + p.stderr}
    except Exception as exc:
        results[name] = {'exit_code': None, 'error': str(exc)}
probe('curl', ['curl', '-v', '--disable', '-k', '--max-time', '8', 'https://1.1.1.1/'])
probe('pip', [sys.executable, '-m', 'pip', '--isolated', 'download', '-vv', '--no-cache-dir',
              '--retries', '0', '--timeout', '8', '--index-url', 'https://1.1.1.1/simple',
              '--dest', '.', 'pip'])
probe('https', [sys.executable, '-c',
                'import urllib.request, sys; urllib.request.urlopen(sys.argv[1], timeout=8)',
                'https://1.1.1.1/'])
print('GAP_CANARY:' + json.dumps({'nonce': nonce, 'probes': results}, sort_keys=True))
"""
DENIAL = re.compile(
    r"operation not permitted|permission denied|sandbox.*denied|"
    r"network.*(?:blocked|denied)|(?:blocked|denied).*network",
    re.I,
)


def _canary_program(harness_id):
    spec = harnesses.find(harness_id)
    sandbox = spec.code_cell_sandbox if spec is not None else None
    if sandbox in {"srt", "codex-sandbox"}:
        return CANARY_PROGRAM
    if sandbox == "portable":
        return PORTABLE_CANARY_PROGRAM
    raise EgressCanaryRefused("GAP refused: unsupported canary harness")


def canary_command(harness_id):
    program = _canary_program(harness_id)
    nonce = uuid.uuid4().hex
    # The shell's PATH selects the cell venv; a host interpreter path may
    # not exist inside the harness sandbox.
    return nonce, shlex.join(["python3", "-c", program, harness_id, nonce])


def _same_command(observed, command):
    if observed == command:
        return True
    try:
        parts = shlex.split(observed or "")
    except (ValueError, TypeError):
        return False
    return (
        len(parts) == 3
        and Path(parts[0]).name in {"bash", "sh", "zsh"}
        and parts[1] in {"-c", "-lc"}
        and parts[2] == command
    )


def require_canary(events, nonce: str, command: str, *, harness_id: str):
    try:
        program = _canary_program(harness_id)
    except EgressCanaryRefused:
        program = None
    if program is None or shlex.split(command) != [
        "python3",
        "-c",
        program,
        harness_id,
        nonce,
    ]:
        raise EgressCanaryRefused("GAP refused: canary command harness mismatch")
    portable = program is PORTABLE_CANARY_PROGRAM

    def refused(value):
        if isinstance(value, Mapping):
            if (
                value.get("api_refusal_category")
                or value.get("type") == "refusal"
                or value.get("stop_reason") == "refusal"
            ):
                return True
            if "stopped by a safety classifier" in str(value.get("text", "")):
                return True
            return any(refused(v) for v in value.values())
        return isinstance(value, (list, tuple)) and any(refused(v) for v in value)

    if refused(events):
        raise EgressCanaryRefused("GAP refused: model refusal")
    # Match a completed Codex shell item, a Claude tool-use/tool-result pair,
    # or an OSS harness's shell call and its result.
    claude_tool_count = 0
    portable_calls = set()
    for captured in events:
        for name, payload, call_id in call_payloads(captured.get("event", captured), include_id=True):
            if name == "bash" and not portable:
                claude_tool_count += 1
            if portable:
                # OSS harnesses repeat one call across events; count distinct ids.
                portable_calls.add(call_id if call_id is not None else len(portable_calls))
            if (
                name not in SHELL_TOOLS
                or not isinstance(payload, Mapping)
                or not _same_command(payload.get("command", payload.get("cmd")), command)
            ):
                raise EgressCanaryRefused("GAP refused: canary used tools outside its exact probe")
    if claude_tool_count > 1:
        raise EgressCanaryRefused("GAP refused: canary used extra Claude tool calls")
    if len(portable_calls) > 1:
        raise EgressCanaryRefused("GAP refused: canary used extra shell tool calls")
    calls = {}
    outputs = []
    codex_items = set()
    for captured in events:
        event = captured.get("event", captured)
        item = event.get("item", {})
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "command_execution"
            and _same_command(item.get("command"), command)
        ):
            item_id = item.get("id")
            if item_id is not None and item_id in codex_items:
                continue
            codex_items.add(item_id if item_id is not None else ("anonymous", len(codex_items)))
            outputs.append(("codex", item.get("aggregated_output", "")))
        message = event.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, Mapping):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                calls[block.get("id")] = block.get("input", {}).get("command")
            elif block.get("type") == "tool_result" and _same_command(
                calls.get(block.get("tool_use_id")), command
            ):
                content = block.get("content", "")
                outputs.append(
                    (
                        "claude-code",
                        content
                        if isinstance(content, str)
                        else "\n".join(
                            entry.get("text", "") for entry in content if isinstance(entry, dict)
                        ),
                    )
                )
    if portable:
        transcript = [captured.get("event", captured) for captured in events]
        shell_calls, names = _shell_calls(transcript)
        results = set()
        for entry in _blocks(transcript):
            result = _portable_shell_result(entry, shell_calls, names)
            # Pi repeats a tool result in its start and end events.
            if result is not None and result[0] not in results and _same_command(result[1], command):
                results.add(result[0])
                outputs.append((harness_id, result[2] if isinstance(result[2], str) else ""))
    if len(codex_items) > 1:
        raise EgressCanaryRefused("GAP refused: canary used extra Codex tool calls")
    records = []
    for harness, output in outputs:
        if harness != harness_id:
            raise EgressCanaryRefused("GAP refused: canary event harness mismatch")
        denied_targets = set()
        if harness == "claude-code":
            # Only the matched tool result's separate sandbox block is evidence.
            blocks = re.findall(r"<sandbox_violations>\s*([\s\S]*?)</sandbox_violations>", output)
            for block in blocks:
                for line in block.splitlines():
                    if not line.strip():
                        continue
                    match = re.fullmatch(
                        r"deny network-outbound ([a-zA-Z0-9.-]+):([0-9]+) \(host is on the deny list\)",
                        line,
                    )
                    if not match:
                        raise EgressCanaryRefused(
                            "GAP refused: unrecognized sandbox_violations format"
                        )
                    denied_targets.add(match.groups())
        targets = {
            "curl": (nonce + ".example.com", "443"),
            "pip": ("pypi.org", "443"),
            "https": (nonce + ".example.org", "443"),
        }
        for line in output.splitlines():
            if not line.startswith("GAP_CANARY:"):
                continue
            try:
                record = json.loads(line[len("GAP_CANARY:") :])
                probes = record["probes"]
                if not isinstance(probes, dict):
                    raise EgressCanaryRefused("GAP refused: malformed probe map")
                if record["nonce"] != nonce or set(probes) != {"curl", "pip", "https"}:
                    raise EgressCanaryRefused("GAP refused: incomplete or mismatched canary")
                if not all(
                    type(p["exit_code"]) is int
                    and p["exit_code"] != 0
                    and isinstance(p["error"], str)
                    and (
                        targets[name] in denied_targets
                        if harness == "claude-code"
                        else DENIAL.search(p["error"])
                    )
                    for name, p in probes.items()
                ):
                    raise EgressCanaryRefused("GAP refused: egress reached or denial unproven")
                for name, probe in probes.items():
                    probe["attribution"] = (
                        "sandbox-violation" if harness == "claude-code" else "denial-text"
                    )
                records.append(record)
            except EgressCanaryRefused:
                raise
            except (ValueError, KeyError, TypeError):
                raise EgressCanaryRefused("GAP refused: malformed canary evidence")
    if len(records) == 1:
        return records[0]
    raise EgressCanaryRefused("GAP refused: curl, pip download and HTTPS must show sandbox denial")


DIRECT_PROBE = """
import errno, json, socket
try:
    with socket.create_connection(('1.1.1.1', 443), timeout=8):
        result = {'connected': True, 'errno': None}
except OSError as exc:
    result = {'connected': False, 'errno': exc.errno}
print(json.dumps(result))
"""


def require_direct_denial(result, *, runner):
    """Only an OS permission error qualifies; reachability is not evidence."""
    if (
        not isinstance(result, dict)
        or result.get("connected") is not False
        or (
            type(result.get("errno")) is not int
            or result["errno"] not in {errno.EPERM, errno.EACCES}
        )
    ):
        raise EgressCanaryRefused("GAP refused: direct egress denial unproven")
    return {**result, "attribution": f"harness-runner-permission:{runner}"}


def _run_probe_process(argv, *, cwd, env, timeout=12, process_group=False):
    """Retry only transient process infrastructure, including discovery calls."""
    for attempt in range(3):
        try:
            if not process_group:
                return subprocess.run(
                    argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
                )
            with subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            ) as process:
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Killing only srt leaves its shell and network probes alive.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    raise
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
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


def sandbox_probe_runner(argv, env, *, cwd, harness_id, scratch):
    """Build a separate runner probe; this cannot qualify the harness tree."""

    def option(name):
        try:
            return argv[argv.index(name) + 1]
        except (ValueError, IndexError) as exc:
            raise EgressCanaryRefused(f"GAP refused: missing cell sandbox setting {name}") from exc

    probe = [sys.executable, "-c", DIRECT_PROBE]
    if harness_id == "codex":
        mode = option("--sandbox")
        if mode not in {"workspace-write", "read-only"}:
            raise EgressCanaryRefused("GAP refused: cell sandbox mode permits direct egress")
        runner = argv[0]
        try:
            cell_config = tomllib.loads(
                (Path(env["CODEX_HOME"]) / "config.toml").read_text(encoding="utf-8")
            )
            if cell_config.get("sandbox_workspace_write", {}).get("network_access") is not False:
                raise ValueError("cell lacks explicit network deny")
            # prepare_arm_spawn also refuses these overrides. Keep the probe
            # fail-closed if another caller bypasses that admission path.
            if any(
                arg in {"-c", "--config", "-p", "--profile", "-P", "--permission-profile"}
                or arg.startswith(("-c", "--config=", "--profile=", "--permission-profile="))
                for arg in argv[1:]
            ):
                raise ValueError("cell overrides isolated sandbox configuration")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EgressCanaryRefused("GAP refused: invalid Codex cell sandbox policy") from exc
        policy = {"sandbox_mode": mode, "sandbox_workspace_write": {"network_access": False}}
        try:
            usage = _run_probe_process([runner, "sandbox", "--help"], cwd=cwd, env=env)
            help_text = usage.stdout + usage.stderr
            if usage.returncode:
                raise ValueError(help_text)
            if re.search(r"Usage:\s+codex sandbox \[OPTIONS\] \[COMMAND\]", help_text):
                sandbox_command = [runner, "sandbox"]
            elif re.search(r"^\s+macos\s", help_text, re.MULTILINE):
                sandbox_command = [runner, "sandbox", "macos"]
            else:
                raise ValueError("unsupported sandbox usage")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise EgressCanaryRefused(
                "GAP refused: codex sandbox runner usage unavailable or unsupported"
            ) from exc
        command = [
            *sandbox_command,
            "-c",
            f'sandbox_mode="{mode}"',
            "--",
            *probe,
        ]
        name = "codex " + " ".join(sandbox_command[1:])
        runner_id = "codex-sandbox"
    elif harness_id == "claude-code":
        try:
            sandbox = json.loads(Path(option("--settings")).read_text(encoding="utf-8"))["sandbox"]
            network = sandbox["network"]
            if (
                sandbox.get("enabled") is not True
                or sandbox.get("failIfUnavailable") is not True
                or network.get("strictAllowlist") is not True
                or network.get("allowLocalBinding") is not False
                or network.get("allowAllUnixSockets") is not False
                or sandbox.get("allowUnsandboxedCommands") is not False
                or sandbox.get("excludedCommands") != []
                or network.get("allowedDomains") != (
                    [urlsplit(env["SEW_LITELLM_BASE_URL"]).hostname]
                    if env.get("SEW_LITELLM_BASE_URL") else []
                )
                or network.get("deniedDomains") != ([] if env.get("SEW_LITELLM_BASE_URL") else ["*"])
            ):
                raise ValueError("cell lacks global network deny")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise EgressCanaryRefused("GAP refused: invalid Claude cell sandbox policy") from exc
        runner = shutil.which("srt", path=env.get("PATH", os.defpath))
        if not runner:
            raise EgressCanaryRefused(
                "GAP refused: claude-code sandbox runtime runner (srt) unavailable; install "
                "@anthropic-ai/sandbox-runtime@0.0.78 with npm install --prefix <dir> "
                "and add <dir>/node_modules/.bin to PATH"
            )
        policy = {
            "network": network,
            "filesystem": {"denyRead": [], "allowWrite": [str(cwd), str(scratch)], "denyWrite": []},
        }
        settings = scratch / "srt-settings.json"
        settings.write_text(json.dumps(policy), encoding="utf-8")
        command = [runner, "--settings", str(settings), "--", *probe]
        name = "@anthropic-ai/sandbox-runtime"
        runner_id = "srt"
    else:
        raise EgressCanaryRefused("GAP refused: unsupported bench harness")
    try:
        version = _run_probe_process([runner, "--version"], cwd=cwd, env=env)
        if version.returncode or not version.stdout.strip():
            raise ValueError(version.stderr or "empty runner version")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise EgressCanaryRefused(f"GAP refused: {name} runner version unavailable") from exc
    evidence = {
        "runner": name,
        "runner_id": runner_id,
        "runner_path": runner,
        "runner_version": version.stdout.strip(),
        "policy": policy,
        "policy_scope": "requested-runner-policy",
        "harness_tree_confined": False,
    }
    if harness_id == "claude-code":
        try:
            harness_version = _run_probe_process([argv[0], "--version"], cwd=cwd, env=env)
            if harness_version.returncode or not harness_version.stdout.strip():
                raise ValueError(harness_version.stderr or "empty harness version")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise EgressCanaryRefused("GAP refused: Claude Code version unavailable") from exc
        if (
            version.stdout.strip() != "0.0.78"
            or harness_version.stdout.strip() != "2.1.282 (Claude Code)"
        ):
            raise EgressCanaryRefused("GAP refused: Claude/srt version pin mismatch")
        evidence.update(
            attestation={
                "sandbox_enabled": True,
                "failIfUnavailable": True,
                "unsandboxed_retries": False,
                "excluded_commands": [],
                "network": network,
                "claude_code_pin": "2.1.282 (Claude Code)",
                "srt_pin": "0.0.78",
            },
            harness_version=harness_version.stdout.strip(),
            bundled_sandbox_runtime_version=None,
            runtime_relationship="standalone-srt-not-bundled-claude-runtime",
        )
    return command, evidence


def qualify_claude_code(
    argv, env, *, cwd, refusal_path=None, direct_evidence=None, command_prefix=()
):
    """Attest unwrapped Claude argv; qualify under the cell's filesystem prefix."""
    captured = {
        "harness_id": "claude-code",
        "qualification": "out-of-model-srt",
        "admissible": False,
        "direct_egress": direct_evidence or {"attribution": "unproven"},
        "attestation": (direct_evidence or {}).get("attestation", {}),
    }

    def retain():
        if refusal_path is not None:
            from ..live_harness import scrub_value, _scrub_canary_credentials, _elide_to_cap

            Path(refusal_path).write_text(
                json.dumps(_elide_to_cap(_scrub_canary_credentials(scrub_value(captured), env))),
                encoding="utf-8",
            )

    try:
        linux = (direct_evidence or {}).get("backend") == "bubblewrap"
        if linux and not env.get("SEW_LITELLM_BASE_URL"):
            raise EgressCanaryRefused(
                "GAP refused: Linux Claude code cells are unsupported: out-of-model "
                "qualification of the bubblewrap cell sandbox is unavailable; use Codex"
            )
        with tempfile.TemporaryDirectory(
            prefix="sew-claude-qualification-", dir=Path(cwd).parent if linux else None
        ) as directory:
            runner, evidence = sandbox_probe_runner(
                argv, env, cwd=cwd, harness_id="claude-code", scratch=Path(directory)
            )
            captured["attestation"] = evidence
            nonce, command = canary_command("claude-code")
            # Three sequential subprocesses allow 20s each. Leave startup headroom.
            # The pinned srt emits attributable filtering-proxy records on stderr.
            probe = [*runner[: runner.index("--")], "--debug", "--", *shlex.split(command)]
            completed = _run_probe_process(
                [*command_prefix, *probe], cwd=cwd, env=env, timeout=75, process_group=True
            )
            captured.update(stdout=completed.stdout, stderr=completed.stderr)
            if completed.returncode:
                raise ValueError("srt probe failed: " + completed.stderr)
            targets = re.findall(
                r"^\[SandboxDebug\] Connection blocked to ([a-zA-Z0-9.-]+):([0-9]+)$",
                completed.stderr,
                re.MULTILINE,
            )
            violations = "\n".join(
                f"deny network-outbound {host}:{port} (host is on the deny list)"
                for host, port in targets
            )
            # Reuse the strict nonce/exit/host parser with bench-owned records.
            events = [
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": "bench",
                                "input": {"command": command},
                            }
                        ]
                    }
                },
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "bench",
                                "content": completed.stdout
                                + "\n<sandbox_violations>\n"
                                + violations
                                + "\n</sandbox_violations>",
                            }
                        ]
                    }
                },
            ]
            record = require_canary(events, nonce, command, harness_id="claude-code")
            captured.update(admissible=True, probes=record["probes"], usage={})
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # EgressCanaryRefused is a ValueError via SchemaError; preserve its reason.
        detail = (
            f"qualification probe timed out after {exc.timeout}s (retries exhausted)"
            if isinstance(exc, subprocess.TimeoutExpired)
            else str(exc)
        )
        refusal = (
            exc
            if isinstance(exc, EgressCanaryRefused)
            else EgressCanaryRefused("GAP refused: out-of-model Claude probes failed: " + detail)
        )
        from ..live_harness import scrub_value, _scrub_canary_credentials

        captured["reason"] = scrub_value(_scrub_canary_credentials(str(refusal), env))[:500]
        retain()
        if refusal is exc:
            raise
        raise refusal from exc
    retain()
    return captured


def _portable(harness_id):
    spec = harnesses.find(harness_id)
    return spec is not None and spec.code_cell_sandbox == "portable"


def _harness_endpoints(env, harness_id, *, harness_auth="account"):
    if harness_id == "claude-code":
        hosts = {"api.anthropic.com"}
        keys = ("ANTHROPIC_BASE_URL",)
    elif harness_id == "codex":
        hosts = {"api.openai.com", "chatgpt.com"}
        keys = ("OPENAI_BASE_URL",)
    elif _portable(harness_id):
        # An OSS harness reaches its model only through LiteLLM.
        if harness_auth != "litellm":
            raise EgressCanaryRefused("GAP refused: OSS harness code cells require LiteLLM auth")
        hosts, keys = set(), ()
    else:
        raise EgressCanaryRefused("GAP refused: unsupported bench harness")
    authorities = {(host, 443) for host in hosts}
    if harness_auth == "litellm":
        if not env.get("SEW_LITELLM_BASE_URL"):
            raise EgressCanaryRefused("GAP refused: missing LiteLLM endpoint")
        keys = ("SEW_LITELLM_BASE_URL",)
    try:
        for key in keys:
            if env.get(key):
                target = urlsplit(env[key])
                if target.scheme not in {"http", "https"} or not target.hostname:
                    raise ValueError("invalid harness endpoint")
                port = target.port
                if port is None:
                    port = 443 if target.scheme == "https" else 80
                if not 1 <= port <= 65535:
                    raise ValueError("invalid harness endpoint port")
                authorities = {(target.hostname, port)}
        endpoints = {
            (host, port): socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            for host, port in authorities
        }
    except (OSError, ValueError) as exc:
        raise EgressCanaryRefused("GAP refused: invalid or unresolved harness endpoint") from exc
    addresses = {entry[4][0] for records in endpoints.values() for entry in records}
    # The qualification target must never become an endpoint exception.
    if "1.1.1.1" in addresses:
        raise EgressCanaryRefused("GAP refused: direct probe target allowlisted")
    if not addresses:
        raise EgressCanaryRefused("GAP refused: endpoint resolution failed")
    return endpoints, addresses, authorities


CACHE_DIRECT_PROBE = r"""
import errno, json, sys
from pathlib import Path
try:
    with Path(sys.argv[1]).open('rb') as wheel:
        wheel.read(1)
    denied = False
except OSError as exc:
    denied = exc.errno in json.loads(sys.argv[3])
print(json.dumps({'nonce': sys.argv[2], 'denied': denied}))
"""

# Only an ancestor and wheel basename are supplied, never the cache pathname.
# This covers tools that discover files before reading them, beyond transcript audit.
CACHE_DISCOVERY_PROBE = r"""
import errno, json, os, sys
from pathlib import Path
ancestor, name, scratch, nonce = sys.argv[1:]
scratch = Path(scratch)
count = 0
def onerror(exc):
    if exc.errno not in {errno.EACCES, errno.EPERM, errno.ENOENT}:
        raise exc
denied = True
for root, dirs, files in os.walk(ancestor, onerror=onerror):
    dirs[:] = [d for d in dirs if not (Path(root) / d).resolve().is_relative_to(scratch)]
    count += len(dirs) + len(files)
    if count > 10000:
        raise RuntimeError('cache discovery probe exceeded entry limit')
    if name in files:
        try:
            with (Path(root) / name).open('rb') as wheel:
                wheel.read(1)
            denied = False
            break
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EPERM, errno.ENOENT}:
                raise
print(json.dumps({'nonce': nonce, 'denied': denied}))
"""


def cache_read_profile(wheelhouse, scratch):
    scratch = Path(scratch).resolve()
    roots = {VERIFIER_ROOT}
    if wheelhouse is not None:
        roots.add(Path(wheelhouse).resolve())
    if any(
        root == Path("/") or scratch.is_relative_to(root) or root.is_relative_to(scratch)
        for root in roots
    ):
        raise EgressCanaryRefused("GAP refused: verifier cache overlaps cell scratch")
    # Keep the permitted find-links directory readable but immutable, including
    # through non-shell file tools. Renames must not escape pathname denial.
    wheels = scratch / "wheelhouse"
    parents = " ".join(
        '(literal ' + json.dumps(str(p)) + ')'
        for p in sorted({p for root in roots | {wheels} for p in root.parents})
    )
    denied = " ".join('(subpath ' + json.dumps(str(p)) + ')' for p in sorted(roots))
    return (
        '(version 1)(allow default)(deny file-read* file-write* '
        + denied + ')(deny file-write* (subpath ' + json.dumps(str(wheels))
        + '))(deny file-write-unlink ' + parents + ')'
    )


def portable_profile(profile, scratch, proxy, authorities):
    """Add whole-tree write and egress confinement to the files-only profile.

    A harness with no shell sandbox of its own runs entirely inside this one
    profile, because macOS refuses a nested Seatbelt profile. Writes stay in
    the cell scratch, and the only network peers are the bench endpoint proxy
    and a loopback LiteLLM endpoint: no DNS, no other loopback service.
    """
    scratch = Path(scratch).resolve()
    ports = {urlsplit(proxy).port}
    for host, port in authorities:
        try:
            if host == "localhost" or ipaddress.ip_address(host).is_loopback:
                ports.add(port)
        except ValueError:
            pass  # A remote LiteLLM is reached through the endpoint proxy.
    # Devices a shell needs, including pseudo-terminals for interactive tools.
    writable = " ".join(
        ["(subpath " + json.dumps(str(scratch)) + ")", '(subpath "/dev/fd")']
        + [
            '(literal "/dev/' + name + '")'
            for name in ("null", "zero", "tty", "random", "urandom", "dtracehelper", "ptmx")
        ]
        + ['(regex #"^/dev/ttys[0-9]+$")']
    )
    return (
        profile
        + "(deny file-write* (require-not (require-any " + writable + ")))"
        + "(deny network-outbound)"
        + "".join(
            f'(allow network-outbound (remote ip "localhost:{port}"))' for port in sorted(ports)
        )
    )


# Parent-chosen target outside the cell. A denied write, or one that lands in
# the namespace's private /tmp, leaves nothing on the host.
WRITE_ESCAPE_PROBE = r"""
import errno, json, sys
from pathlib import Path
target, nonce = Path(sys.argv[1]), sys.argv[2]
try:
    target.write_text(nonce)
    denied = False
except OSError as exc:
    if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT}:
        raise
    denied = True
print(json.dumps({'nonce': nonce, 'denied': denied}))
"""


def qualify_write_escape(wrap, *, cwd, env, backend):
    """Prove a write outside the cell scratch never reaches the host."""
    scratch = Path(cwd).resolve().parent
    nonce = uuid.uuid4().hex
    target = scratch.parent / ("sew-write-escape-" + nonce)
    try:
        completed = _run_probe_process(
            wrap([sys.executable, "-I", "-c", WRITE_ESCAPE_PROBE, str(target), nonce]),
            cwd=cwd, env=env, process_group=True,
        )
        if completed.returncode:
            raise ValueError("write probe failed: " + completed.stderr[:500])
        record = json.loads(completed.stdout)
        if (
            not isinstance(record, dict)
            or set(record) != {"nonce", "denied"}
            or record["nonce"] != nonce
            or type(record["denied"]) is not bool
            or target.exists()
        ):
            raise ValueError("write confinement unproven")
        return {
            "backend": backend.name,
            "denied_in_sandbox": record["denied"],
            "reached_host": False,
            "harness_tree_confined": True,
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise EgressCanaryRefused("GAP refused: cell write confinement unproven") from exc
    finally:
        target.unlink(missing_ok=True)


def _portable_cell_env(env, scratch):
    """Keep an OSS harness's home and temporary files inside the cell scratch."""
    for key, name in (("HOME", "harness-home"), ("TMPDIR", "harness-tmp")):
        current = env.get(key)
        if current and Path(current).resolve().is_relative_to(scratch):
            continue  # The arm already isolated it (Opencode's HOME).
        path = scratch / name
        path.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(path)


WHEELHOUSE_WRITE_PROBE = r"""
import errno, json, os, sys
from pathlib import Path
wheels, marker, nonce = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
# The copied inputs must remain readable. The marker is parent-created and
# writable, so mode bits cannot substitute for whole-tree sandbox enforcement.
assert marker.read_bytes() == b'trusted-source-probe'
created = wheels / ('links-' + nonce + '.html')
alias = wheels.parent / ('wheel-alias-' + nonce)
moved_wheels = wheels.with_name(wheels.name + '-' + nonce)
moved_scratch = wheels.parent.with_name(wheels.parent.name + '-' + nonce)
operations = {
    'create': lambda: created.write_bytes(b'<a href="https://example.org/new.whl">new</a>'),
    'modify': lambda: marker.write_bytes(b'untrusted'),
    'chmod': lambda: marker.chmod(0o777),
    'link': lambda: os.link(marker, alias),
    'rename_wheelhouse': lambda: os.rename(wheels, moved_wheels),
    'rename_scratch': lambda: os.rename(wheels.parent, moved_scratch),
}
cleanup = {
    'create': created.unlink,
    'link': alias.unlink,
    'rename_wheelhouse': lambda: os.rename(moved_wheels, wheels),
    'rename_scratch': lambda: os.rename(moved_scratch, wheels.parent),
}
denied = {}
for name, operation in operations.items():
    try:
        operation()
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
            raise
        denied[name] = True
    else:
        denied[name] = False
        # A denial during cleanup must never certify a successful mutation.
        if name in cleanup:
            cleanup[name]()
print(json.dumps({'nonce': nonce, 'denied': denied}))
"""


def qualify_wheelhouse_writes(wrap, *, cwd, env, backend):
    """Prove the permitted wheelhouse stays immutable for every descendant."""
    wheels = Path(cwd).resolve().parent / "wheelhouse"
    nonce = uuid.uuid4().hex
    marker = wheels / (".sew-write-probe-" + nonce)
    operations = {"create", "modify", "chmod", "link", "rename_wheelhouse", "rename_scratch"}
    try:
        with marker.open("xb") as output:
            output.write(b"trusted-source-probe")
        marker.chmod(0o600)
        completed = _run_probe_process(
            wrap([sys.executable, "-I", "-c", WHEELHOUSE_WRITE_PROBE, str(wheels), str(marker), nonce]),
            cwd=cwd, env=env, process_group=True,
        )
        if completed.returncode:
            raise ValueError("wheelhouse probe failed: " + completed.stderr[:500])
        record = json.loads(completed.stdout)
        if (
            not isinstance(record, dict)
            or set(record) != {"nonce", "denied"}
            or record["nonce"] != nonce
            or not isinstance(record["denied"], dict)
            or set(record["denied"]) != operations
            or any(value is not True for value in record["denied"].values())
        ):
            raise ValueError("wheelhouse write denial unproven")
        return {"backend": backend.name, "denied": record["denied"], "harness_tree_confined": True}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise EgressCanaryRefused("GAP refused: cell wheelhouse immutability unproven") from exc
    finally:
        marker.unlink(missing_ok=True)


def qualify_cache_reads(wrap, wheelhouse, *, cwd, env, backend):
    """Probe the exact whole-tree boundary, outside either evaluated model."""
    cache = Path(wheelhouse).resolve()
    try:
        wheel = next(p for p in sorted(cache.glob("*.whl")) if p.is_file())
        return _qualify_file_reads(wrap, wheel, cache.parent, cwd=cwd, env=env, backend=backend)
    except (OSError, StopIteration) as exc:
        raise EgressCanaryRefused("GAP refused: verifier cache isolation unproven") from exc


def _qualify_file_reads(wrap, wheel, ancestor, *, cwd, env, backend):
    try:
        # A missing/unreadable source must not masquerade as sandbox containment.
        with wheel.open("rb") as source:
            source.read(1)
        nonce = uuid.uuid4().hex
        allowed = [errno.EACCES, errno.EPERM]
        if backend.name == "bubblewrap":
            allowed.append(errno.ENOENT)
        probes = {
            "direct": [CACHE_DIRECT_PROBE, str(wheel), nonce, json.dumps(allowed)],
            "discovery": [
                CACHE_DISCOVERY_PROBE, str(ancestor), wheel.name, str(Path(cwd).resolve().parent),
                nonce,
            ],
        }
        for args in probes.values():
            completed = _run_probe_process(
                wrap([sys.executable, "-I", "-c", *args]),
                cwd=cwd, env=env, process_group=True,
            )
            if completed.returncode:
                raise ValueError("cache probe failed: " + completed.stderr[:500])
            record = json.loads(completed.stdout)
            if (
                not isinstance(record, dict)
                or set(record) != {"nonce", "denied"}
                or record["nonce"] != nonce
                or record["denied"] is not True
            ):
                raise ValueError("cache read denial unproven")
        return {
            "backend": backend.name,
            "direct_denied": True,
            "discovery_denied": True,
            "harness_tree_confined": True,
        }
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        raise EgressCanaryRefused("GAP refused: verifier cache isolation unproven") from exc


def qualify_verifier_reads(wrap, wheelhouse, *, cwd, env, backend):
    """Qualify copies in the shared root, including installed-source reads."""
    try:
        with verifier_scratch() as name:
            scratch = Path(name)
            wheels = scratch / "wheelhouse"
            wheels.mkdir()
            source = next(p for p in sorted(Path(wheelhouse).glob("*.whl")) if p.is_file())
            wheel = wheels / source.name
            shutil.copyfile(source, wheel)
            installed = scratch / "venv/lib/verifier-source-probe.py"
            installed.parent.mkdir(parents=True)
            installed.write_text("# verifier-only installed-source probe\n")
            return {
                "wheel": _qualify_file_reads(
                    wrap, wheel, VERIFIER_ROOT, cwd=cwd, env=env, backend=backend
                ),
                "installed_source": _qualify_file_reads(
                    wrap, installed, VERIFIER_ROOT, cwd=cwd, env=env, backend=backend
                ),
            }
    except (OSError, StopIteration) as exc:
        raise EgressCanaryRefused("GAP refused: verifier copy isolation unproven") from exc


@contextmanager
def bench_network_boundary(
    argv, env, *, cwd, harness_id, refusal_path=None, environ=None, harness_auth="account",
    wheelhouse=None,
):
    """Retain failed preflight evidence without replacing captured shell evidence."""
    evidence = {
        "harness_id": harness_id,
        "admissible": False,
        "direct_egress": {"attribution": "unproven"},
    }

    def retain():
        if refusal_path is not None:
            Path(refusal_path).write_text(json.dumps(evidence), encoding="utf-8")

    retain()
    with ExitStack() as boundaries:
        try:
            boundary = boundaries.enter_context(
                _bench_network_boundary(
                    argv,
                    env,
                    cwd=cwd,
                    harness_id=harness_id,
                    environ=environ,
                    harness_auth=harness_auth,
                    wheelhouse=wheelhouse,
                )
            )
        except Exception as exc:
            from ..live_harness import scrub_value, _scrub_canary_credentials

            evidence["reason"] = scrub_value(_scrub_canary_credentials(str(exc), env))[:500]
            retain()
            raise
        yield boundary


@contextmanager
def _bench_network_boundary(
    argv, env, *, cwd, harness_id, environ=None, harness_auth="account", wheelhouse=None
):
    """Qualify macOS runners separately; confine Linux invocations with bubblewrap.

    Retry transient probe subprocess failures, then fail closed on exhaustion.
    Unsupported hosts, resolution failures and unproven denials fail closed.
    No model is asked to disable a proxy or execute this probe.
    """
    from .sandbox import select_backend

    backend = select_backend(env if environ is None else environ)
    if backend.name == "bubblewrap":
        with _bubblewrap_boundary(
            argv, env, cwd=cwd, harness_id=harness_id, environ=environ, harness_auth=harness_auth,
            wheelhouse=wheelhouse,
        ) as boundary:
            yield boundary
        return
    endpoints, addresses, authorities = _harness_endpoints(env, harness_id, harness_auth=harness_auth)
    # File-only Seatbelt policy applies to the harness and all descendants,
    # including non-shell read tools. Shell sandbox compatibility is mandatory.
    # Even code controls without package pins must not read another run's verifier.
    profile = cache_read_profile(wheelhouse, Path(cwd).resolve().parent)
    prefix = backend.command([], Path(cwd).parent, profile=profile)
    cache_evidence = None
    if wheelhouse is not None:
        cache_evidence = qualify_cache_reads(
            lambda command: [*prefix, *command], wheelhouse, cwd=cwd, env=env, backend=backend
        )
        cache_evidence["verifier_copies"] = qualify_verifier_reads(
            lambda command: [*prefix, *command], wheelhouse, cwd=cwd, env=env, backend=backend
        )
        cache_evidence["cell_wheelhouse"] = qualify_wheelhouse_writes(
            lambda command: [*prefix, *command], cwd=cwd, env=env, backend=backend
        )
    # Resolve and filter proxy-aware destinations in an unsandboxed parent.
    from .endpoint_proxy import endpoint_proxy

    with (
        endpoint_proxy(endpoints) as proxy,
        tempfile.TemporaryDirectory(prefix="sew-probe-") as directory,
    ):
        if _portable(harness_id):
            # No harness runner exists: the bench profile is the runner, and
            # the direct probe runs under the exact profile of the harness tree.
            scratch = Path(cwd).resolve().parent
            _portable_cell_env(env, scratch)
            profile = portable_profile(profile, scratch, proxy, authorities)
            prefix = backend.command([], Path(cwd).parent, profile=profile)
            probe_argv = [sys.executable, "-c", DIRECT_PROBE]
            runner_evidence = {
                "runner": "sandbox-exec",
                "runner_id": "bench-seatbelt",
                "runner_path": backend.executable,
                "policy": {"profile": profile},
                "policy_scope": "harness-tree-profile",
                "harness_tree_confined": True,
                "write_escape": qualify_write_escape(
                    lambda command: [*prefix, *command], cwd=cwd, env=env, backend=backend
                ),
            }
        else:
            probe_argv, runner_evidence = sandbox_probe_runner(
                argv, env, cwd=cwd, harness_id=harness_id, scratch=Path(directory)
            )
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env[key] = env[key.lower()] = proxy
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
        try:
            completed = _run_probe_process([*prefix, *probe_argv], cwd=cwd, env=env)
            if completed.returncode != 0:
                output = completed.stdout + "\n" + completed.stderr
                if re.search(
                    r"sandbox_apply: Operation not permitted|"
                    r"sandbox[^\n]*(?:nested|inside (?:another|a) sandbox|already sandboxed)|"
                    r"sandbox (?:initialization|setup|startup) failed[^\n]*"
                    r"(?:Operation not permitted|EPERM)",
                    output,
                    re.IGNORECASE,
                ):
                    raise EgressCanaryRefused(
                        "GAP refused: sandbox runner cannot start inside a nested sandbox"
                    )
                raise ValueError(
                    f"probe process failed ({completed.returncode}): {completed.stderr}"
                )
            evidence = require_direct_denial(
                json.loads(completed.stdout), runner=runner_evidence["runner_id"]
            )
        except EgressCanaryRefused:
            raise
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise EgressCanaryRefused("GAP refused: bench probe failed") from exc
        evidence.update(runner_evidence)
        if cache_evidence is not None:
            evidence["wheel_cache_isolation"] = cache_evidence
        evidence["endpoint_addresses"] = sorted(addresses)
        evidence["endpoint_authorities"] = [
            {"host": host, "port": port} for host, port in sorted(authorities)
        ]
        yield [*prefix, *argv], evidence


def _script_runtime(binary):
    """Read roots a script launcher's interpreter needs (a Python console script).

    Native and Node launchers return nothing; their closure is bound above.
    """
    try:
        with Path(binary).open("rb") as handle:
            line = handle.readline(4096)
    except OSError:
        return []
    if not line.startswith(b"#!"):
        return []
    try:
        interpreter = Path(line[2:].split()[0].decode())
    except (IndexError, UnicodeDecodeError):
        return []
    if not interpreter.is_absolute() or interpreter.name == "env":
        return []
    environment = interpreter.parent.parent
    installations = [interpreter.resolve().parent.parent]
    try:
        config = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        config = None
    if config is not None:
        # A virtual environment also needs its base interpreter's stdlib.
        installations.append(environment.resolve())
        home = re.search(r"^home\s*=\s*(.+)$", config, re.MULTILINE)
        if home:
            installations.append(Path(home[1].strip()).resolve().parent)
    # Never a bare top-level directory such as /usr (bound already) or a tree
    # that contains the account's home directory.
    home = Path.home().resolve()
    return [
        root for root in installations
        if root.is_absolute() and len(root.parts) > 2 and not home.is_relative_to(root)
    ]


@contextmanager
def _bubblewrap_boundary(
    argv, env, *, cwd, harness_id, environ=None, harness_auth="account", wheelhouse=None
):
    """The endpoint proxy crosses namespaces only through one Unix socket."""
    from .endpoint_proxy import endpoint_proxy
    from .sandbox import PROXY_BRIDGE, qualify_backend, select_backend

    backend = select_backend(env if environ is None else environ)
    # An account refresh inside disposable scratch can invalidate the host's
    # refresh token. Broker tokens contain no refresh token; never copy a login.
    if harness_id == "claude-code" and (
        harness_auth not in {"broker", "litellm"} or not env.get("ANTHROPIC_AUTH_TOKEN")
    ):
        raise EgressCanaryRefused("GAP refused: Linux Claude code cells require broker OAuth auth or LiteLLM auth")
    endpoints, addresses, authorities = _harness_endpoints(env, harness_id, harness_auth=harness_auth)
    scratch = Path(cwd).resolve().parent
    # Installed harness binaries may live outside /usr (e.g. ~/.local).
    binary = shutil.which(argv[0], path=env.get("PATH", os.defpath))
    if binary is None:
        raise EgressCanaryRefused("GAP refused: harness executable unavailable")
    binary = str(Path(binary).resolve())
    argv = [binary, *argv[1:]]
    binary_path = Path(binary)
    # Native CLIs need their executable, not the surrounding user data tree.
    # Node launchers need their installed package closure (including optional
    # platform binaries alongside the package in node_modules).
    if binary_path.suffix in {".js", ".mjs", ".cjs"}:
        runtime = next(
            (p for p in binary_path.parents if p.name == "node_modules"), binary_path.parent
        )
        reads = [runtime]
    else:
        reads = [binary_path, *_script_runtime(binary_path)]
    if harness_auth == "litellm" and harnesses.get(harness_id).code_cell_sandbox == "srt":
        runtime = shutil.which("srt", path=env.get("PATH", os.defpath))
        if not runtime:
            raise EgressCanaryRefused("GAP refused: sandbox runtime runner (srt) unavailable")
        runtime = Path(runtime).resolve()
        reads.append(next((p for p in runtime.parents if p.name == "node_modules"), runtime))
    node = shutil.which("node", path=env.get("PATH", os.defpath))
    if node:
        node = Path(node).resolve()
        reads.append(node)
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", os.defpath)
    # Keep harness state private and writable without exposing host sessions.
    if harness_id == "claude-code":
        home = scratch / "harness-home"
        config = home / ".claude"
        config.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["HOME"] = str(home)
        env["CLAUDE_CONFIG_DIR"] = str(config)
    elif env.get("CODEX_HOME"):
        source = Path(env["CODEX_HOME"]).resolve()
        if not source.is_relative_to(scratch):
            home = scratch / "harness-codex-home"
            home.mkdir(mode=0o700, exist_ok=True)
            for name in ("auth.json", ".credentials.json", "config.toml"):
                if (source / name).is_file():
                    shutil.copyfile(source / name, home / name)
                    (home / name).chmod(0o600)
            env["CODEX_HOME"] = str(home)
    elif _portable(harness_id):
        _portable_cell_env(env, scratch)
    if harness_auth == "litellm":
        env["SEW_GAP_LITELLM_BASE_URL"] = env["SEW_LITELLM_BASE_URL"]
    else:
        env.pop("SEW_GAP_LITELLM_BASE_URL", None)
    evidence = qualify_backend(backend, scratch, env)
    with tempfile.TemporaryDirectory(prefix="sew-gap-proxy-", dir="/tmp") as name:
        socket_path = Path(name) / "endpoint.sock"
        with endpoint_proxy(endpoints, socket_path=socket_path):
            def wrap(command):
                return backend.command(
                    command, scratch, read_roots=[*reads, socket_path],
                    deny_read_roots=[wheelhouse] if wheelhouse is not None else (),
                )

            if wheelhouse is not None:
                cache_read_profile(wheelhouse, scratch)  # Same overlap refusal on both hosts.
                evidence["wheel_cache_isolation"] = qualify_cache_reads(
                    wrap, wheelhouse, cwd=cwd, env=env, backend=backend
                )
            if _portable(harness_id):
                # The namespace is the OSS harness's only sandbox.
                evidence.update(
                    harness_tree_confined=True,
                    write_escape=qualify_write_escape(wrap, cwd=cwd, env=env, backend=backend),
                )
            wrapped = wrap([sys.executable, "-I", "-c", PROXY_BRIDGE, str(socket_path), *argv])
            evidence.update(
                endpoint_addresses=sorted(addresses),
                endpoint_authorities=[
                    {"host": host, "port": port} for host, port in sorted(authorities)
                ],
            )
            yield wrapped, evidence
