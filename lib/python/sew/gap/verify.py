"""Execute captured GAP patches in a fail-closed verifier sandbox.

Commands are trusted catalog pytest commands. Initial failures remain failures:
reruns diagnose flakes rather than laundering a failed cell into a success.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import venv
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing

from ..catalog import module_root
from ..schema import SchemaError
from .workspace import check_tree, materialize_tree, prepare_workspace, snapshot_tree, verifier_scratch


def _copy_verifier_tooling(site):
    """Copy pytest's installed distribution closure, not assumed import names."""
    from packaging.requirements import Requirement

    pending = ["pytest"]
    copied = set()
    while pending:
        name = pending.pop()
        distribution = importlib.metadata.distribution(name)
        identity = distribution.metadata["Name"].lower().replace("_", "-")
        if identity in copied:
            continue
        copied.add(identity)
        if not distribution.files:
            raise SchemaError(f"missing verifier tooling file manifest: {name}")
        for relative in distribution.files:
            # Entry-point scripts live outside purelib and are unnecessary for -m.
            if relative.is_absolute() or ".." in relative.parts:
                continue
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(distribution.locate_file(relative), target)
        for requirement in distribution.requires or []:
            dependency = Requirement(requirement)
            if dependency.marker is None or dependency.marker.evaluate({"extra": ""}):
                pending.append(dependency.name)


def _sandbox_command(argv, scratch, *, write_roots=None, read_roots=(), env=None, backend=None):
    """Seatbelt inherits into every child; absence is a refusal, never a fallback."""
    from .sandbox import select_backend

    backend = select_backend(env) if backend is None else backend
    if backend.name == "bubblewrap":
        return backend.command(argv, scratch, write_roots=write_roots, read_roots=read_roots)
    scratch = Path(scratch).resolve()
    writes = " ".join(
        f"(subpath {json.dumps(str(Path(p).resolve()))})"
        for p in (write_roots if write_roots is not None else [scratch])
    )
    runtime = Path(sys.base_prefix).resolve()
    read_roots = {
        runtime,
        Path("/System"),
        Path("/usr/lib"),
        Path("/usr/share"),
        *(Path(p).resolve() for p in read_roots),
    }
    # Homebrew Python extensions link to sibling keg libraries (e.g. OpenSSL).
    # Admit installed runtime files read-only, never Homebrew configuration or HOME.
    cellar = next((p for p in runtime.parents if p.name == "Cellar"), None)
    if cellar is not None:
        read_roots.add(cellar)
    reads = " ".join(f"(subpath {json.dumps(str(p))})" for p in sorted(read_roots))
    profile = f"""(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow file-read-metadata)
(allow file-read* (literal "/"))
(allow file-read* {reads} (subpath "/bin") (subpath "/usr/bin")
    (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random")
    (literal "/private/etc/apache2/mime.types"))
(allow file-read* (subpath {json.dumps(str(scratch))}))
(allow file-write* {writes} (literal "/dev/null"))
(allow sysctl-read (sysctl-name "hw.ncpu") (sysctl-name "hw.activecpu")
    (sysctl-name "hw.memsize") (sysctl-name "kern.osrelease")
    (sysctl-name "kern.osversion") (sysctl-name "kern.ostype")
    (sysctl-name "kern.hostname") (sysctl-name "kern.version") (sysctl-name "hw.machine"))
"""
    return backend.command(argv, scratch, profile=profile)


def _wait_without_reaping(process, timeout):
    """Retain the leader's PID until its group has received its last signal."""
    if hasattr(os, "waitid"):
        deadline = time.monotonic() + timeout
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(min(0.01, remaining))
    else:
        # macOS Python 3.11/3.12 do not expose waitid. NOTE_EXIT observes exit
        # without waitpid; registering an already-exited zombie returns ESRCH.
        with closing(select.kqueue()) as events:
            event = select.kevent(
                process.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            try:
                events.control([event], 0, 0)
            except ProcessLookupError:
                return
            if not events.control(None, 1, timeout):
                raise subprocess.TimeoutExpired(process.args, timeout)


def _execute(
    argv,
    cwd,
    env,
    timeout,
    *,
    sandbox_root=None,
    sandbox_backend=None,
    sandbox_write_roots=None,
    sandbox_read_roots=(),
    stdin=None,
):
    if sandbox_root is not None:
        argv = _sandbox_command(
            argv,
            sandbox_root,
            write_roots=sandbox_write_roots,
            read_roots=sandbox_read_roots,
            env=env,
            backend=sandbox_backend,
        )
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            _wait_without_reaping(process, timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Kill sleepers before reaping: the zombie leader reserves its PID
            # so this PGID cannot be recycled for an unrelated host process.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # macOS returns EPERM when only the zombie leader remains.
                pass
            process.wait()
        output.seek(0)
        return {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "output": output.read(16000).decode("utf-8", errors="replace"),
        }


def _tests(command, cwd, env, timeout, report, *, backend=None):
    argv = shlex.split(command)
    # Prevent shell operators and non-pytest commands from silently bypassing XML.
    if len(argv) < 3 or argv[:3] not in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]):
        raise SchemaError("GAP verifier commands must use python -m pytest")
    if any(arg.startswith("--junit") for arg in argv):
        raise SchemaError("GAP verifier owns JUnit output")
    record = _execute(
        [*argv, f"--junitxml={report}"],
        cwd,
        {**env, "HOME": str(report.parent), "TMPDIR": str(report.parent)},
        timeout,
        sandbox_root=cwd.parent,
        sandbox_backend=backend,
        sandbox_write_roots=[cwd, report.parent],
    )
    record.update(command=command, tests=[])
    try:
        for case in ET.parse(report).iter("testcase"):
            status = "pass"
            for tag in ("failure", "error", "skipped"):
                if case.find(tag) is not None:
                    status = tag
                    break
            record["tests"].append(
                {"id": case.get("classname", "") + "::" + case.get("name", ""), "status": status}
            )
    except (OSError, ET.ParseError):
        record["report_error"] = "missing_or_invalid_junit"
    record["passed"] = (
        record["exit_code"] == 0
        and not record["timed_out"]
        and bool(record["tests"])
        and all(t["status"] == "pass" for t in record["tests"])
    )
    return record


def verify(
    task: dict,
    diff: Path,
    *,
    wheelhouse: Path | None = None,
    root: Path | None = None,
    install_roles: tuple[str, ...] | None = None,
    visible_only: bool = False,
    environ=None,
) -> dict:
    """Reconstruct a cell without accepting or reading its live workspace.

    The caller supplies the trusted catalog task and wheel cache. Ordinary
    grading installs new/dependency pins regardless of fixture requirements.
    Explicit install_roles selects author-validation legs; visible-only checks
    may use fixture requirements. pytest is part of the verifier's base tooling.
    """
    from .workspace import EgressCanaryRefused

    root = Path(root or module_root()).resolve()
    result = {
        "outcome": "fail",
        "escaped_defects": 0,
        "visible_regressions": 0,
        "visible": [],
        "hidden": [],
        "reruns": [],
        "flakes": [],
        "errors": [],
    }
    timeout = task["verifier"]["timeout_seconds"]
    try:
        with verifier_scratch() as name:
            scratch = Path(name).resolve()
            _, cwd, offline = prepare_workspace(
                SimpleNamespace(wheelhouse=wheelhouse),
                scratch,
                root,
                task,
                wheel_roles=("old", "new", "dependency"),
            )
            env = {
                "PATH": os.defpath,
                "TMPDIR": str(scratch),
                "HOME": name,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                **offline,
            }
            from .sandbox import qualify_backend, select_backend

            backend = select_backend(environ)
            if backend.name == "bubblewrap":
                qualify_backend(backend, scratch, env)
            if diff.stat().st_size > 190000:
                raise SchemaError("GAP diff exceeds artifact cap")
            if diff.stat().st_size:
                # /usr/bin/git is an xcrun shim on macOS. Resolve its installed
                # runtime before parsing the untrusted patch inside Seatbelt.
                if sys.platform == "darwin":
                    git = Path(
                        subprocess.check_output(
                            ["/usr/bin/xcrun", "--find", "git"], env=env, text=True, timeout=timeout
                        ).strip()
                    )
                else:
                    executable = shutil.which("git", path=env["PATH"])
                    if executable is None:
                        raise SchemaError("GAP verification requires git")
                    # /bin commonly aliases /usr/bin; its unresolved parent
                    # would expose the entire host root as Git's runtime.
                    git = Path(executable).resolve()
                with diff.open("rb") as patch:
                    applied = _execute(
                        [str(git), "apply", "--binary", "-"],
                        cwd,
                        env,
                        timeout,
                        sandbox_root=scratch,
                        sandbox_backend=backend,
                        sandbox_write_roots=[cwd],
                        sandbox_read_roots=[git.parent.parent],
                        stdin=patch,
                    )
                if applied["exit_code"] or applied["timed_out"]:
                    raise SchemaError(f"GAP patch application failed: {applied['output']}")
            check_tree(cwd)
            # Reserve the hidden destination: agent patches cannot replace the oracle.
            hidden = cwd / task["hidden"]
            if any(p.is_symlink() for p in [hidden, *hidden.parents]) or hidden.exists():
                raise SchemaError("GAP patch collides with hidden assets")
            venv.EnvBuilder(with_pip=True).create(scratch / "venv")
            # Copy only the verifier's pytest tooling, never expose the host's
            # dependency set to the job. Task dependencies come from pinned wheels.
            site = Path(
                subprocess.check_output(
                    [
                        str(scratch / "venv/bin/python"),
                        "-c",
                        "import sysconfig; print(sysconfig.get_path('purelib'))",
                    ],
                    env=env,
                    text=True,
                    timeout=timeout,
                ).strip()
            )
            _copy_verifier_tooling(site)
            env.update(
                PATH=str(scratch / "venv/bin") + os.pathsep + env["PATH"],
                VIRTUAL_ENV=str(scratch / "venv"),
                PIP_REQUIRE_VIRTUALENV="1",
                PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            )
            requirements = cwd / "requirements.txt"
            selected = []
            if requirements.exists():
                allowed = {f"{p['name']}=={p['version']}" for p in task["packages"]}
                selected = [
                    line.strip()
                    for line in requirements.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                if any(line not in allowed for line in selected):
                    raise SchemaError("requirements must select only catalog-pinned wheels")
            pins = [f"{p['name']}=={p['version']}" for p in task["packages"] if p["role"] != "old"]
            if install_roles is None and not visible_only:
                # Grading must exercise the target API even if a cell leaves the
                # pristine old-version requirements unchanged.
                install_roles = ("new", "dependency")
            if install_roles is not None:
                # Both grading and author validity select catalog roles, after
                # checking that requirements contain only permitted pins.
                selected = [
                    f"{p['name']}=={p['version']}"
                    for p in task["packages"]
                    if p["role"] in install_roles
                ]
                pins = selected
            if selected or (not requirements.exists() and pins):
                install = _execute(
                    [
                        str(scratch / "venv/bin/python"),
                        "-I",
                        "-m",
                        "pip",
                        "install",
                        "--ignore-installed",
                        "--no-index",
                        "--no-cache-dir",
                        "--only-binary=:all:",
                        "--find-links",
                        offline["PIP_FIND_LINKS"],
                        *(selected if requirements.exists() else pins),
                    ],
                    scratch,
                    env,
                    timeout,
                    sandbox_root=scratch,
                    sandbox_backend=backend,
                )
                if install["exit_code"] or install["timed_out"]:
                    result["errors"].append({"stage": "install", **install})
                    return result
            for lane in ("visible",) if visible_only else ("visible", "hidden"):
                if lane == "hidden":
                    # Visible code can write the workspace; expose the oracle only
                    # after its processes have exited, and reject planted assets.
                    if any(p.is_symlink() for p in [hidden, *hidden.parents]) or hidden.exists():
                        raise SchemaError("GAP visible tests collide with hidden assets")
                    hidden.mkdir(parents=True)
                    materialize_tree(snapshot_tree(root / "catalogs/gap" / task["hidden"]), hidden)
                for index, command in enumerate(task["verifier"][lane + "_commands"]):
                    reports = scratch / f"{lane}-{index}"
                    reports.mkdir()
                    record = _tests(
                        command, cwd, env, timeout, reports / "result.xml", backend=backend
                    )
                    result[lane].append(record)
                    counter = "visible_regressions" if lane == "visible" else "escaped_defects"
                    result[counter] += sum(
                        t["status"] in {"failure", "error"} for t in record["tests"]
                    )
                    if lane == "hidden" and not record["passed"]:
                        # pytest's cache records node ids (including parametrized
                        # cases) more faithfully than reconstructing them from XML.
                        retry_command = command
                        if any(t["status"] in {"failure", "error"} for t in record["tests"]):
                            retry_command += " --lf --lfnf=none"
                        reports = scratch / f"rerun-{index}"
                        reports.mkdir()
                        rerun = _tests(
                            retry_command,
                            cwd,
                            env,
                            timeout,
                            reports / "result.xml",
                            backend=backend,
                        )
                        rerun["initial_command_index"] = index
                        result["reruns"].append(rerun)
                        passed = {t["id"] for t in rerun["tests"] if t["status"] == "pass"}
                        result["flakes"].extend(
                            t["id"]
                            for t in record["tests"]
                            if t["status"] in {"failure", "error"} and t["id"] in passed
                        )
            if all(record["passed"] for lane in ("visible", "hidden") for record in result[lane]):
                result["outcome"] = "pass"
    except EgressCanaryRefused as exc:
        result["outcome"] = "not_applicable"
        result["errors"].append({"stage": "sandbox_unavailable", "message": str(exc)})
    except (
        OSError,
        ValueError,
        SchemaError,
        subprocess.SubprocessError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        result["errors"].append({"stage": "setup", "message": str(exc)})
    return result
